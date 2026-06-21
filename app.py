#!/usr/bin/env python3
"""Flask + Socket.IO console for supervised PN5180 ISO 15693 ink-tag operations."""

import importlib
import importlib.util
import json
import os
import pkgutil
import time
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Optional, Protocol


def _optional_module(name: str) -> Any:
    """Return an optional module when it is installed, otherwise ``None``."""
    module_path = []
    for part in name.split('.'):
        module_path.append(part)
        if importlib.util.find_spec('.'.join(module_path)) is None:
            return None
    try:
        return importlib.import_module(name)
    except Exception:
        return None


flask_module = _optional_module('flask')
flask_socketio_module = _optional_module('flask_socketio')
HAS_WEB_DEPS = flask_module is not None and flask_socketio_module is not None

if HAS_WEB_DEPS:
    Flask = flask_module.Flask
    Response = flask_module.Response
    jsonify = flask_module.jsonify
    render_template = flask_module.render_template
    SocketIO = flask_socketio_module.SocketIO
else:
    class _MissingWebDependencyApp:
        config: dict[str, str] = {}

        def route(self, *_args, **_kwargs):
            def decorator(fn):
                return fn
            return decorator

    class _MissingWebDependencySocketIO:
        def __init__(self, *_args, **_kwargs):
            pass

        def emit(self, *_args, **_kwargs):
            pass

        def on(self, *_args, **_kwargs):
            def decorator(fn):
                return fn
            return decorator

        def start_background_task(self, fn, *args):
            return fn(*args)

        def run(self, *_args, **_kwargs):
            raise SystemExit('Missing Flask dependencies. Run: pip install -r requirements.txt')

    def Flask(_name):
        return _MissingWebDependencyApp()

    def Response(*args, **kwargs):
        return {'args': args, 'kwargs': kwargs}

    def jsonify(data):
        return data

    def render_template(*_args, **_kwargs):
        return 'Missing Flask dependencies. Run: pip install -r requirements.txt'

    SocketIO = _MissingWebDependencySocketIO

pn5180pi_module = _optional_module('pn5180pi')
pigpio_module = _optional_module('pigpio')


def resolve_pn5180_class(module: Any) -> Any:
    """Find the PN5180 driver class across known pn5180pi export styles."""
    if module is None:
        return None
    for class_name in ('Pn5180', 'PN5180'):
        driver_class = getattr(module, class_name, None)
        if driver_class is not None:
            return driver_class
    module_paths = getattr(module, '__path__', None)
    if module_paths is None:
        return None
    for submodule in pkgutil.iter_modules(module_paths, f'{module.__name__}.'):
        try:
            imported_submodule = importlib.import_module(submodule.name)
        except Exception:
            continue
        for class_name in ('Pn5180', 'PN5180'):
            driver_class = getattr(imported_submodule, class_name, None)
            if driver_class is not None:
                return driver_class
    return None


PN5180_CLASS = resolve_pn5180_class(pn5180pi_module)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'change-me-in-production')
socketio = SocketIO(app, cors_allowed_origins=os.getenv('CORS_ALLOWED_ORIGINS', '*'))


def env_float(name: str, default: float, *, minimum: float) -> float:
    """Read a bounded float from the environment."""
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = float(raw_value)
    except ValueError:
        return default
    return max(value, minimum)


def env_int(name: str, default: int, *, minimum: int) -> int:
    """Read a bounded integer from the environment."""
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError:
        return default
    return max(value, minimum)


TAG_DETECTION_TIMEOUT_SECONDS = env_float('TAG_DETECTION_TIMEOUT_SECONDS', 15.0, minimum=1.0)
TAG_DETECTION_POLL_SECONDS = env_float('TAG_DETECTION_POLL_SECONDS', 0.2, minimum=0.05)
ISO15693_BLOCK_SIZE = env_int('ISO15693_BLOCK_SIZE', 4, minimum=1)
PN5180_RESPONSE_TIMEOUT_SECONDS = env_float('PN5180_RESPONSE_TIMEOUT_SECONDS', 0.25, minimum=0.01)
PN5180_BUSY_TIMEOUT_SECONDS = env_float('PN5180_BUSY_TIMEOUT_SECONDS', 1.0, minimum=0.05)
PN5180_NSS_PIN = env_int('PN5180_NSS_PIN', 8, minimum=0)
PN5180_BUSY_PIN = env_int('PN5180_BUSY_PIN', 24, minimum=0)
PN5180_RESET_PIN = env_int('PN5180_RESET_PIN', 23, minimum=0)
ENABLE_UID_BACKDOOR = os.getenv('ENABLE_UID_BACKDOOR', 'false').lower() in {'1', 'true', 'yes', 'on'}
PN5180_BACKEND = os.getenv('PN5180_BACKEND', 'direct-spi').lower()
NFC_READER_BACKEND = PN5180_BACKEND

ISO15693_FLAG_DATA_RATE_HIGH = 0x02
ISO15693_FLAG_INVENTORY = 0x04
ISO15693_FLAG_ADDRESS = 0x20
ISO15693_CMD_INVENTORY = 0x01
ISO15693_CMD_READ_SINGLE_BLOCK = 0x20
ISO15693_CMD_WRITE_SINGLE_BLOCK = 0x21
ISO15693_CMD_LOCK_BLOCK = 0x22
ISO15693_CMD_GET_SYSTEM_INFO = 0x2B
ISO15693_CMD_GET_MULTIPLE_BLOCK_SECURITY = 0x2C
# PN532Killer / MTools "Gen2 UID Changeable" ISO 15693 magic UID-set sequence:
# two unaddressed custom frames carry the new UID in wire order (LSB first).
# 0x40 sets the first four wire bytes, 0x41 the last four. Reference: MTools/
# PN532Killer raw commands and Proxmark `hf 15 csetuid --v2`.
ISO15693_MAGIC_SET_UID_HIGH = bytes([0x02, 0xE0, 0x09, 0x40])
ISO15693_MAGIC_SET_UID_LOW = bytes([0x02, 0xE0, 0x09, 0x41])
# In an inventory request bit 6 is the Nb_slots flag: set it to run a single
# slot, which is the most reliable way to detect one sticker on the antenna.
ISO15693_FLAG_NB_SLOTS_ONE = 0x20

# PN5180 identity EEPROM addresses (READ_EEPROM opcode 0x07). Reading these back
# is the standard way to confirm the chip is actually answering on the SPI bus.
PN5180_EEPROM_DIE_ID = 0x00
PN5180_EEPROM_PRODUCT_VERSION = 0x10
PN5180_EEPROM_FIRMWARE_VERSION = 0x12
PN5180_EEPROM_EEPROM_VERSION = 0x14

reader: Optional['PN5180Iso15693Reader'] = None
hardware_status = 'Disconnected'
op_lock = Lock()
operation_history: list[dict[str, Any]] = []

TARGET_UID = bytes([0xE0, 0x07, 0x81, 0x6A, 0xE3, 0x2E, 0x96, 0x32])
# DNP media stores the prints-remaining counter in block 2, 16-bit little-endian
# (observed decrement 0x0083=131 -> 0x0082=130 after one print). The block is
# unlocked so the printer can update it; that also lets us write it for testing.
PRINT_COUNTER_BLOCK = 2
# Blocks the genuine master has write-locked. A clone with these unlocked is
# rejected as invalid media, so a faithful clone must lock exactly this set.
# (Blocks 0, 2 and 50-62 stay unlocked: the printer updates the counter and
# signature there.)
TARGET_LOCKED_BLOCKS = [1] + list(range(3, 50)) + [63]
# Captured snapshot of the genuine master at count 130 (block 2 = 0x82 little-endian)
# with its matching per-print signature (blocks 50-55). Burning this produces a
# faithful clone of the master's current state to test whether the booth accepts it.
CLEARED_DATA_BLOCKS = [
    bytes([0x29, 0x50, 0x4E, 0x44]), bytes([0x00, 0x01, 0xA3, 0x42]),
    bytes([0x82, 0x00, 0x00, 0x00]), bytes([0xA1, 0x10, 0x13, 0x17]),
    bytes([0xF2, 0x10, 0xC0, 0x00]), bytes([0x9E, 0x01, 0x51, 0x02]),
    bytes([0x58, 0x02, 0x00, 0x00]), bytes([0x00, 0x00, 0x00, 0x00]),
    bytes([0x00, 0x00, 0x00, 0x00]), bytes([0x00, 0x00, 0x00, 0x00]),
    bytes([0x00, 0x00, 0x00, 0x00]), bytes([0x00, 0x00, 0x00, 0x00]),
    bytes([0x00, 0x00, 0x00, 0x00]), bytes([0x00, 0x00, 0x00, 0x00]),
    bytes([0x00, 0x00, 0x00, 0x00]), bytes([0x25, 0x67, 0x45, 0x9D]),
    bytes([0x96, 0x10, 0x5F, 0xD6]), bytes([0x18, 0xA9, 0x6A, 0x74]),
    bytes([0x26, 0x67, 0x2D, 0x21]), bytes([0xC9, 0xA8, 0x72, 0x5E]),
    bytes([0xFE, 0x30, 0x75, 0x26]), bytes([0xFE, 0x10, 0x24, 0x9F]),
    bytes([0x93, 0x43, 0x08, 0xE5]), bytes([0xA3, 0x60, 0x8E, 0xF3]),
    bytes([0x4B, 0x1F, 0xDA, 0x33]), bytes([0x2E, 0x66, 0xE3, 0x84]),
    bytes([0xFA, 0x5A, 0x3B, 0x9E]), bytes([0x8A, 0x8C, 0xBE, 0xC2]),
    bytes([0x94, 0xE8, 0x61, 0xD0]), bytes([0x09, 0x2F, 0xD4, 0x3F]),
    bytes([0xAB, 0x71, 0xF7, 0x65]), bytes([0xD9, 0x98, 0xD7, 0x01]),
    bytes([0x6E, 0xD9, 0xAD, 0x00]), bytes([0x3E, 0x34, 0xD8, 0xAA]),
    bytes([0x9B, 0x26, 0xB2, 0x8E]), bytes([0xA6, 0xC6, 0x52, 0x93]),
    bytes([0x9A, 0x4D, 0x32, 0x34]), bytes([0x4E, 0xC1, 0xD0, 0x4C]),
    bytes([0x15, 0xA8, 0xA2, 0x7F]), bytes([0x0A, 0x56, 0x14, 0xEE]),
    bytes([0x5C, 0x24, 0xB5, 0x04]), bytes([0x59, 0xC3, 0x33, 0x47]),
    bytes([0xF6, 0x8D, 0x46, 0x84]), bytes([0x99, 0x4B, 0xB9, 0x94]),
    bytes([0x74, 0xBA, 0x88, 0xD4]), bytes([0xBD, 0x82, 0xC7, 0x9D]),
    bytes([0x9F, 0x4C, 0x4A, 0xA8]), bytes([0x6E, 0x6A, 0x36, 0xA6]),
    bytes([0x24, 0x70, 0x43, 0x72]), bytes([0xF5, 0x50, 0x7D, 0xD9]),
    bytes([0xEE, 0x47, 0xDF, 0x16]), bytes([0x65, 0x0F, 0x90, 0x5F]),
    bytes([0x6C, 0x26, 0xA3, 0x8E]), bytes([0x93, 0x8B, 0xC3, 0xEB]),
    bytes([0xC8, 0xB7, 0x5A, 0x4C]), bytes([0x59, 0x61, 0x3C, 0x5A]),
    bytes([0x60, 0x63, 0x1A, 0x53]), bytes([0x9F, 0xF1, 0xFD, 0x34]),
    bytes([0x1D, 0xD5, 0x77, 0x68]), bytes([0xB2, 0xDA, 0xAA, 0x0D]),
    bytes([0x83, 0x7C, 0x7C, 0xC6]), bytes([0xBF, 0xE3, 0x7B, 0xD3]),
    bytes([0x00, 0x00, 0x00, 0x00]), bytes([0xA2, 0x05, 0x01, 0x76])
]


class Iso15693Reader(Protocol):
    """Minimal reader contract used by the Flask workflow."""

    label: str

    def poll_uid(self) -> Optional[bytes]:
        """Return one ISO 15693 UID when a tag is present."""

    def read_block(self, uid: bytes, block_index: int) -> bytes:
        """Read one ISO 15693 memory block."""

    def read_system_info(self, uid: bytes) -> dict[str, Any]:
        """Read the tag's Get System Information (DSFID/AFI/memory/IC reference)."""

    def read_block_security(self, uid: bytes, first_block: int, block_count: int) -> list[bool]:
        """Return per-block locked flags via Get Multiple Block Security Status."""

    def lock_block(self, uid: bytes, block_index: int) -> None:
        """Permanently write-lock one ISO 15693 block."""

    def write_block(self, uid: bytes, block_index: int, data: bytes) -> None:
        """Write one ISO 15693 memory block."""

    def write_uid_backdoor(self, uid: bytes) -> None:
        """Write a vendor-specific UID backdoor register when supported."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def format_uid(uid: bytes) -> str:
    return '-'.join(f'{byte:02X}' for byte in uid)


def format_pn5180_version(value: Optional[bytes]) -> str:
    """Format a 2-byte PN5180 version EEPROM value as ``major.minor``."""
    if not value or len(value) < 2:
        return 'unknown'
    return f'{value[1]}.{value[0]}'


def pn5180_identity_responsive(*values: Optional[bytes]) -> bool:
    """Return True when an identity read looks like real silicon, not all 0x00/0xFF.

    A PN5180 that is wired and powered returns real version/die bytes. A bus that
    is mis-wired, unpowered, or held in reset reads back as all zeros or all ones,
    so those patterns mean "no chip answered" rather than a genuine identity.
    """
    for value in values:
        if not value:
            continue
        if all(byte == 0x00 for byte in value):
            continue
        if all(byte == 0xFF for byte in value):
            continue
        return True
    return False


def normalize_uid(value: Any) -> Optional[bytes]:
    """Normalize an ISO 15693 UID into display order (MSB first, normally starting E0)."""
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.replace(':', '').replace('-', '').replace(' ', '')
        if len(cleaned) != 16:
            return None
        try:
            return bytes.fromhex(cleaned)
        except ValueError:
            return None
    if isinstance(value, int):
        if value <= 0:
            return None
        return value.to_bytes(8, 'big')
    if isinstance(value, (bytes, bytearray)):
        data = bytes(value)
    elif isinstance(value, (list, tuple)):
        try:
            data = bytes(value)
        except (TypeError, ValueError):
            return None
    else:
        return None
    return data if len(data) == 8 else None


def parse_iso15693_inventory_response(response: Optional[bytes]) -> Optional[bytes]:
    """Extract a UID from a raw ISO 15693 inventory response.

    A successful inventory response is flags + DSFID + 8-byte UID. ISO 15693
    transmits UID bytes least-significant byte first, so this returns display
    order by reversing those 8 bytes.
    """
    if not response or len(response) < 10:
        return None
    validate_iso15693_response(response)
    return bytes(response[2:10][::-1])


def parse_iso15693_uid(response: Optional[bytes]) -> Optional[bytes]:
    """Extract a UID from raw ISO 15693 inventory or legacy PN532 responses."""
    if not response:
        return None
    if len(response) >= 6 and response[0] == 1:
        uid_length = response[5]
        uid_start = 6
        uid_end = uid_start + uid_length
        if uid_length > 0 and len(response) >= uid_end:
            return bytes(response[uid_start:uid_end])
    if len(response) >= 10:
        return parse_iso15693_inventory_response(response)
    return normalize_uid(response)


def validate_block_data(data: bytes) -> bytes:
    if len(data) != ISO15693_BLOCK_SIZE:
        raise ValueError(f'ISO 15693 block must be {ISO15693_BLOCK_SIZE} bytes, got {len(data)}')
    return data


def parse_iso15693_block_response(response: bytes, block_index: int) -> bytes:
    """Extract block data from a READ_SINGLE_BLOCK response (flags byte + data)."""
    validate_iso15693_response(response)
    data = bytes(response[1:1 + ISO15693_BLOCK_SIZE])
    if len(data) != ISO15693_BLOCK_SIZE:
        raise RuntimeError(
            f'ISO 15693 block {block_index} read returned {len(data)} of '
            f'{ISO15693_BLOCK_SIZE} bytes'
        )
    return data


def parse_iso15693_system_info(response: bytes) -> dict[str, Any]:
    """Parse a Get System Information response into the tag's identity fields.

    Layout: flags + info_flags + UID(8) then, gated by info_flags bits,
    DSFID(0x01), AFI(0x02), memory size(0x04 -> blocks-1, blocksize-1) and
    IC reference(0x08).
    """
    validate_iso15693_response(response)
    if len(response) < 10:
        raise RuntimeError(f'Get System Information response too short: {response.hex()}')
    info_flags = response[1]
    info: dict[str, Any] = {
        'uid': format_uid(bytes(response[2:10][::-1])),
        'info_flags': f'0x{info_flags:02X}',
    }
    index = 10
    if info_flags & 0x01 and index < len(response):
        info['dsfid'] = f'0x{response[index]:02X}'
        index += 1
    if info_flags & 0x02 and index < len(response):
        info['afi'] = f'0x{response[index]:02X}'
        index += 1
    if info_flags & 0x04 and index + 1 < len(response):
        info['block_count'] = response[index] + 1
        info['block_size'] = (response[index + 1] & 0x1F) + 1
        index += 2
    if info_flags & 0x08 and index < len(response):
        info['ic_reference'] = f'0x{response[index]:02X}'
        index += 1
    return info


def parse_iso15693_block_security(response: bytes, block_count: int) -> list[bool]:
    """Parse Get Multiple Block Security Status into a per-block locked flag list."""
    validate_iso15693_response(response)
    statuses = response[1:1 + block_count]
    if len(statuses) < block_count:
        raise RuntimeError(
            f'block security response returned {len(statuses)} of {block_count} statuses'
        )
    return [bool(status & 0x01) for status in statuses]


def validate_uid(uid: bytes) -> bytes:
    if len(uid) != 8:
        raise ValueError(f'ISO 15693 UID must be 8 bytes, got {len(uid)}')
    return uid


def first_callable(target: Any, *names: str) -> Any:
    for name in names:
        candidate = getattr(target, name, None)
        if callable(candidate):
            return candidate
    return None


def validate_iso15693_response(response: bytes) -> None:
    if response and response[0] & 0x01:
        error_code = response[1] if len(response) > 1 else 0
        raise RuntimeError(f'ISO 15693 tag returned error 0x{error_code:02X}')


class DirectSpiPN5180Iso15693Reader:
    """Direct PN5180 ISO 15693 reader using pigpio hardware SPI/GPIO."""

    label = 'PN5180 (direct SPI ISO 15693)'

    def __init__(self) -> None:
        if pigpio_module is None:
            raise RuntimeError('Install pigpio and start pigpiod for direct PN5180 SPI access')
        self._pi = pigpio_module.pi()
        if not getattr(self._pi, 'connected', True):
            raise RuntimeError('pigpiod is not running or is unreachable for direct PN5180 SPI access')
        self._spi_channel = 0 if PN5180_NSS_PIN == 8 else 1
        self._spi = self._pi.spi_open(
            self._spi_channel,
            env_int('PN5180_SPI_HZ', 50000, minimum=1000),
            env_int('PN5180_SPI_FLAGS', 0, minimum=0),
        )
        self._pi.set_mode(PN5180_NSS_PIN, pigpio_module.OUTPUT)
        self._pi.set_mode(PN5180_BUSY_PIN, pigpio_module.INPUT)
        self._pi.set_mode(PN5180_RESET_PIN, pigpio_module.OUTPUT)
        self._deselect()
        self._hardware_reset()
        self._bytes_in_card_buffer = 0
        self._last_inventory_response = b''
        self.self_test: dict[str, Any] = {}

    def _hardware_reset(self) -> None:
        self._pi.write(PN5180_RESET_PIN, 0)
        time.sleep(0.02)
        self._pi.write(PN5180_RESET_PIN, 1)
        time.sleep(0.02)

    def _wait_ready(self) -> None:
        # Bounded so a stuck/floating BUSY line (unpowered or mis-wired PN5180)
        # can never hang startup or a scan in an infinite loop; it surfaces as a
        # clear error instead.
        deadline = time.monotonic() + PN5180_BUSY_TIMEOUT_SECONDS
        while self._pi.read(PN5180_BUSY_PIN):
            if time.monotonic() > deadline:
                raise RuntimeError(
                    f'PN5180 BUSY (GPIO {PN5180_BUSY_PIN}) stuck high for '
                    f'{PN5180_BUSY_TIMEOUT_SECONDS:g}s; check 3.3V/5V power and BUSY/SPI wiring'
                )
            time.sleep(0.0001)

    def _select(self) -> None:
        self._pi.write(PN5180_NSS_PIN, 0)

    def _deselect(self) -> None:
        self._pi.write(PN5180_NSS_PIN, 1)

    def _spi_xfer(self, frame: list[int]) -> list[int]:
        count, data = self._pi.spi_xfer(self._spi, bytes(frame))
        if count < 0:
            raise RuntimeError(f'pigpio SPI transfer failed with status {count}')
        return list(data[:count])

    def _send(self, frame: list[int]) -> None:
        self._wait_ready()
        self._select()
        try:
            self._spi_xfer(frame)
        finally:
            self._deselect()
        self._wait_ready()

    def _read_after_command(self, command: list[int], length: int) -> list[int]:
        # PN5180 framing: the command and its response are two separate NSS frames.
        # Send the command, raise NSS to end the frame, then wait for BUSY to drop
        # (response ready) with NSS high, and finally clock the response in a new
        # frame. Holding NSS low across the BUSY wait keeps the chip from completing
        # the command, so BUSY never clears and the read hangs.
        self._wait_ready()
        self._select()
        try:
            self._spi_xfer(command)
        finally:
            self._deselect()
        self._wait_ready()
        self._select()
        try:
            return self._spi_xfer([0x00] * length)
        finally:
            self._deselect()

    def _read_register(self, register: int) -> list[int]:
        return self._read_after_command([0x04, register], 4)

    def _read_data(self, length: int) -> list[int]:
        # PN5180 READ_DATA has a required dummy parameter byte after opcode 0x0A.
        # Without the 0x00 byte, the chip does not clock out the RF receive buffer.
        return self._read_after_command([0x0A, 0x00], length)

    def _read_eeprom(self, address: int, length: int) -> list[int]:
        # READ_EEPROM: opcode 0x07 + start address + length, then clock out `length` bytes.
        return self._read_after_command([0x07, address & 0xFF, length & 0xFF], length)

    def read_self_test(self) -> dict[str, Any]:
        """Read PN5180 identity EEPROM so operators can confirm SPI comms really work.

        This uses the same SPI read path as every other command, so an all-zero or
        all-0xFF result is a strong signal that the wiring/power/SPI bus is the
        problem rather than the RF field or the sticker.
        """
        product = bytes(self._read_eeprom(PN5180_EEPROM_PRODUCT_VERSION, 2))
        firmware = bytes(self._read_eeprom(PN5180_EEPROM_FIRMWARE_VERSION, 2))
        eeprom = bytes(self._read_eeprom(PN5180_EEPROM_EEPROM_VERSION, 2))
        die_id = bytes(self._read_eeprom(PN5180_EEPROM_DIE_ID, 16))
        info: dict[str, Any] = {
            'product_version': format_pn5180_version(product),
            'firmware_version': format_pn5180_version(firmware),
            'eeprom_version': format_pn5180_version(eeprom),
            'die_id': ''.join(f'{byte:02X}' for byte in die_id),
            'responsive': pn5180_identity_responsive(product, firmware, eeprom),
        }
        self.self_test = info
        return info

    def last_scan_summary(self) -> str:
        """Describe the most recent RF response to help diagnose scan timeouts."""
        if self._last_inventory_response:
            return 'last RF bytes: ' + '-'.join(f'{byte:02X}' for byte in self._last_inventory_response)
        return 'no RF bytes received from any sticker (RF field, antenna, or sticker-type issue)'

    @staticmethod
    def _rx_status_byte_count(rx_status: list[int]) -> int:
        """Return RX byte count from RX_STATUS bits 0-8."""
        if not rx_status:
            return 0
        low_byte = rx_status[0]
        high_bit = (rx_status[1] & 0x01) if len(rx_status) > 1 else 0
        return low_byte | (high_bit << 8)

    def _card_has_responded(self) -> bool:
        # Wait for RX_IRQ (end of RF reception) before trusting the byte count.
        # Reading RX_STATUS the instant any byte appears can grab the buffer
        # mid-frame and return a truncated UID/response.
        deadline = time.monotonic() + PN5180_RESPONSE_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            irq_status = self._read_register(0x02)  # IRQ_STATUS
            if irq_status and (irq_status[0] & 0x01):  # RX_IRQ: reception complete
                rx_status = self._read_register(0x13)  # RX_STATUS (count now final)
                self._bytes_in_card_buffer = self._rx_status_byte_count(rx_status)
                if self._bytes_in_card_buffer > 0:
                    return True
            time.sleep(0.002)
        return False

    def _recover_if_busy_stuck(self) -> None:
        """Hardware-reset the PN5180 if BUSY is still asserted from a prior op.

        Some transceives (notably ISO 15693 block writes) can leave the state
        machine non-idle with BUSY high, which would hang the next operation. A
        brief grace period covers a normal late release; if BUSY is genuinely
        stuck, a reset returns the chip to a clean idle state.
        """
        deadline = time.monotonic() + 0.05
        while self._pi.read(PN5180_BUSY_PIN):
            if time.monotonic() > deadline:
                self._hardware_reset()
                return
            time.sleep(0.001)

    def _prepare_iso15693(self) -> None:
        self._recover_if_busy_stuck()
        self._send([0x11, 0x0D, 0x8D])  # LOAD_RF_CONFIG: ISO 15693
        self._send([0x16, 0x00])  # RF_ON
        self._send([0x00, 0x03, 0xFF, 0xFF, 0x0F, 0x00])  # WRITE_REGISTER IRQ_CLEAR
        self._send([0x02, 0x00, 0xF8, 0xFF, 0xFF, 0xFF])  # SYSTEM_CONFIG idle mask
        self._send([0x01, 0x00, 0x03, 0x00, 0x00, 0x00])  # SYSTEM_CONFIG transceive

    def exchange(self, frame: bytes) -> bytes:
        self._prepare_iso15693()
        self._send([0x09, 0x00] + list(frame))  # SEND_DATA, complete bytes
        response = b''
        if self._card_has_responded():
            response = bytes(self._read_data(self._bytes_in_card_buffer))
        # Return the command state machine to IDLE before dropping RF so a write
        # transceive does not leave the chip non-idle with BUSY asserted.
        self._send([0x02, 0x00, 0xF8, 0xFF, 0xFF, 0xFF])  # SYSTEM_CONFIG -> IDLE
        self._send([0x17, 0x00])  # RF_OFF
        return response

    def _send_inventory_eof(self) -> None:
        self._send([0x02, 0x18, 0x3F, 0xFB, 0xFF, 0xFF])
        self._send([0x02, 0x00, 0xF8, 0xFF, 0xFF, 0xFF])
        self._send([0x01, 0x00, 0x03, 0x00, 0x00, 0x00])
        self._send([0x00, 0x03, 0xFF, 0xFF, 0x0F, 0x00])
        self._send([0x09, 0x00])

    def _inventory_round(self, flags: int, slots: int) -> Optional[bytes]:
        """Run one ISO 15693 inventory of `slots` slots and return the first UID."""
        self._prepare_iso15693()
        self._send([
            0x09,
            0x00,
            # Inventory must be unaddressed: addressed inventory frames require a
            # UID that we do not know yet, so stickers will not answer them.
            flags,
            ISO15693_CMD_INVENTORY,
            0x00,
        ])
        for slot_index in range(slots):
            if self._card_has_responded():
                response = bytes(self._read_data(self._bytes_in_card_buffer))
                self._last_inventory_response = response
                uid = parse_iso15693_inventory_response(response)
                if uid:
                    return uid
            if slot_index < slots - 1:
                self._send_inventory_eof()
        return None

    def poll_uid(self) -> Optional[bytes]:
        try:
            # A single sticker on the antenna is detected most reliably with a
            # one-slot inventory: it removes any dependence on slot/EOF timing,
            # whereas a lone sticker rarely lands in slot 0 of a 16-slot sweep.
            single_slot_flags = (
                ISO15693_FLAG_DATA_RATE_HIGH
                | ISO15693_FLAG_INVENTORY
                | ISO15693_FLAG_NB_SLOTS_ONE
            )
            try:
                uid = self._inventory_round(single_slot_flags, 1)
                if uid:
                    return uid
            except RuntimeError:
                pass  # Fall through to the anticollision sweep on a tag error.
            # Fall back to the full 16-slot anticollision sweep (multi-tag fields).
            multi_slot_flags = ISO15693_FLAG_DATA_RATE_HIGH | ISO15693_FLAG_INVENTORY
            return self._inventory_round(multi_slot_flags, 16)
        finally:
            self._send([0x17, 0x00])  # RF_OFF

    def read_block(self, uid: bytes, block_index: int) -> bytes:
        uid = validate_uid(uid)
        if not 0 <= block_index <= 0xFF:
            raise ValueError(f'ISO 15693 block index out of range: {block_index}')
        frame = bytes([
            ISO15693_FLAG_DATA_RATE_HIGH | ISO15693_FLAG_ADDRESS,
            ISO15693_CMD_READ_SINGLE_BLOCK,
        ]) + uid[::-1] + bytes([block_index])
        return parse_iso15693_block_response(self.exchange(frame), block_index)

    def read_system_info(self, uid: bytes) -> dict[str, Any]:
        uid = validate_uid(uid)
        frame = bytes([
            ISO15693_FLAG_DATA_RATE_HIGH | ISO15693_FLAG_ADDRESS,
            ISO15693_CMD_GET_SYSTEM_INFO,
        ]) + uid[::-1]
        return parse_iso15693_system_info(self.exchange(frame))

    def read_block_security(self, uid: bytes, first_block: int, block_count: int) -> list[bool]:
        uid = validate_uid(uid)
        frame = bytes([
            ISO15693_FLAG_DATA_RATE_HIGH | ISO15693_FLAG_ADDRESS,
            ISO15693_CMD_GET_MULTIPLE_BLOCK_SECURITY,
        ]) + uid[::-1] + bytes([first_block & 0xFF, (block_count - 1) & 0xFF])
        return parse_iso15693_block_security(self.exchange(frame), block_count)

    def lock_block(self, uid: bytes, block_index: int) -> None:
        uid = validate_uid(uid)
        if not 0 <= block_index <= 0xFF:
            raise ValueError(f'ISO 15693 block index out of range: {block_index}')
        frame = bytes([
            ISO15693_FLAG_DATA_RATE_HIGH | ISO15693_FLAG_ADDRESS,
            ISO15693_CMD_LOCK_BLOCK,
        ]) + uid[::-1] + bytes([block_index])
        validate_iso15693_response(self.exchange(frame))

    def write_block(self, uid: bytes, block_index: int, data: bytes) -> None:
        uid = validate_uid(uid)
        data = validate_block_data(data)
        if not 0 <= block_index <= 0xFF:
            raise ValueError(f'ISO 15693 block index out of range: {block_index}')
        frame = bytes([
            ISO15693_FLAG_DATA_RATE_HIGH | ISO15693_FLAG_ADDRESS,
            ISO15693_CMD_WRITE_SINGLE_BLOCK,
        ]) + uid[::-1] + bytes([block_index]) + data
        validate_iso15693_response(self.exchange(frame))

    def write_uid_backdoor(self, uid: bytes) -> None:
        uid = validate_uid(uid)
        # The Gen2 magic command stores the UID in wire order (LSB first).
        wire = uid[::-1]
        validate_iso15693_response(self.exchange(ISO15693_MAGIC_SET_UID_HIGH + wire[0:4]))
        validate_iso15693_response(self.exchange(ISO15693_MAGIC_SET_UID_LOW + wire[4:8]))


class PN5180Iso15693Reader:
    """Direct PN5180 ISO 15693 reader using a pn5180pi raw send/receive driver."""

    label = 'PN5180 (pn5180pi raw ISO 15693)'

    def __init__(self) -> None:
        if PN5180_CLASS is None:
            raise RuntimeError('Install pn5180pi and confirm it exports a Pn5180 or PN5180 driver class')
        self.device = PN5180_CLASS(PN5180_NSS_PIN, PN5180_BUSY_PIN, PN5180_RESET_PIN)
        self._inventory_iso15693 = first_callable(
            self.device,
            'inventory_iso15693',
            'inventoryIso15693',
            'inventory_iso_15693',
            'inventory',
        )
        self._write_block_iso15693 = first_callable(
            self.device,
            'write_single_block_iso15693',
            'writeSingleBlockIso15693',
            'write_block_iso15693',
            'writeBlockIso15693',
        )
        self._prepare_iso15693 = first_callable(
            self.device,
            'prepare_iso15693',
            'setup_iso15693',
            'configure_iso15693',
            'begin_iso15693',
            'load_rf_config_iso15693',
            'rf_on_iso15693',
            'rf_on',
            'enable_rf',
            'enable_rf_field',
            'turn_rf_on',
        )
        self._send_data = getattr(self.device, 'send_data', None) or getattr(self.device, 'sendData', None)
        self._receive_data = getattr(self.device, 'receive_data', None) or getattr(self.device, 'receiveData', None)
        if not callable(self._inventory_iso15693) and (not callable(self._send_data) or not callable(self._receive_data)):
            raise RuntimeError('pn5180pi driver must expose inventory_iso15693() or send_data(frame) and receive_data()')

    def exchange(self, frame: bytes) -> bytes:
        if callable(self._prepare_iso15693):
            self._prepare_iso15693()
        self._send_data(bytes(frame))
        response = self._receive_data()
        return bytes(response or b'')

    def poll_uid(self) -> Optional[bytes]:
        if callable(self._inventory_iso15693):
            if callable(self._prepare_iso15693):
                self._prepare_iso15693()
            response = self._inventory_iso15693()
            if isinstance(response, (list, tuple)) and response:
                response = response[0]
            return parse_iso15693_uid(response)
        frame = bytes([
            # Inventory must be unaddressed; use addressed mode only after UID discovery.
            ISO15693_FLAG_DATA_RATE_HIGH | ISO15693_FLAG_INVENTORY,
            ISO15693_CMD_INVENTORY,
            0x00,  # mask length
        ])
        return parse_iso15693_inventory_response(self.exchange(frame))

    def read_block(self, uid: bytes, block_index: int) -> bytes:
        uid = validate_uid(uid)
        if not 0 <= block_index <= 0xFF:
            raise ValueError(f'ISO 15693 block index out of range: {block_index}')
        frame = bytes([
            ISO15693_FLAG_DATA_RATE_HIGH | ISO15693_FLAG_ADDRESS,
            ISO15693_CMD_READ_SINGLE_BLOCK,
        ]) + uid[::-1] + bytes([block_index])
        return parse_iso15693_block_response(self.exchange(frame), block_index)

    def read_system_info(self, uid: bytes) -> dict[str, Any]:
        uid = validate_uid(uid)
        frame = bytes([
            ISO15693_FLAG_DATA_RATE_HIGH | ISO15693_FLAG_ADDRESS,
            ISO15693_CMD_GET_SYSTEM_INFO,
        ]) + uid[::-1]
        return parse_iso15693_system_info(self.exchange(frame))

    def read_block_security(self, uid: bytes, first_block: int, block_count: int) -> list[bool]:
        uid = validate_uid(uid)
        frame = bytes([
            ISO15693_FLAG_DATA_RATE_HIGH | ISO15693_FLAG_ADDRESS,
            ISO15693_CMD_GET_MULTIPLE_BLOCK_SECURITY,
        ]) + uid[::-1] + bytes([first_block & 0xFF, (block_count - 1) & 0xFF])
        return parse_iso15693_block_security(self.exchange(frame), block_count)

    def lock_block(self, uid: bytes, block_index: int) -> None:
        uid = validate_uid(uid)
        if not 0 <= block_index <= 0xFF:
            raise ValueError(f'ISO 15693 block index out of range: {block_index}')
        frame = bytes([
            ISO15693_FLAG_DATA_RATE_HIGH | ISO15693_FLAG_ADDRESS,
            ISO15693_CMD_LOCK_BLOCK,
        ]) + uid[::-1] + bytes([block_index])
        validate_iso15693_response(self.exchange(frame))

    def write_block(self, uid: bytes, block_index: int, data: bytes) -> None:
        uid = validate_uid(uid)
        data = validate_block_data(data)
        if not 0 <= block_index <= 0xFF:
            raise ValueError(f'ISO 15693 block index out of range: {block_index}')
        if callable(self._write_block_iso15693):
            self._write_block_iso15693(uid, block_index, data)
            return
        frame = bytes([
            ISO15693_FLAG_DATA_RATE_HIGH | ISO15693_FLAG_ADDRESS,
            ISO15693_CMD_WRITE_SINGLE_BLOCK,
        ]) + uid[::-1] + bytes([block_index]) + data
        validate_iso15693_response(self.exchange(frame))

    def write_uid_backdoor(self, uid: bytes) -> None:
        uid = validate_uid(uid)
        # The Gen2 magic command stores the UID in wire order (LSB first).
        wire = uid[::-1]
        validate_iso15693_response(self.exchange(ISO15693_MAGIC_SET_UID_HIGH + wire[0:4]))
        validate_iso15693_response(self.exchange(ISO15693_MAGIC_SET_UID_LOW + wire[4:8]))


def emit_action_complete(status: str) -> None:
    socketio.emit('action_complete', {'status': status})


def log_to_web(msg: str) -> None:
    socketio.emit('log_update', {'data': msg})


def update_ui_status() -> None:
    socketio.emit('hw_status_update', {'status': hardware_status})


def record_operation(name: str, operation_status: str, **details: Any) -> None:
    operation_history.append({
        'timestamp': utc_now_iso(),
        'operation': name,
        'status': operation_status,
        'details': details,
    })
    del operation_history[:-100]


def reset_pn5180_hardware() -> None:
    """Pulse the PN5180 reset pin through pigpiod without requiring /dev/mem/root."""
    if pigpio_module is None:
        return
    pi = pigpio_module.pi()
    if not getattr(pi, 'connected', True):
        return
    try:
        pi.set_mode(PN5180_RESET_PIN, pigpio_module.OUTPUT)
        pi.write(PN5180_RESET_PIN, 1)
        time.sleep(0.01)
        pi.write(PN5180_RESET_PIN, 0)
        time.sleep(0.1)
        pi.write(PN5180_RESET_PIN, 1)
        time.sleep(0.1)
    finally:
        stop = getattr(pi, 'stop', None)
        if callable(stop):
            stop()


def describe_hardware_error(exc: Exception) -> str:
    """Return an operator-friendly PN5180 initialization error."""
    message = str(exc)
    if 'No I2C device at address' in message:
        return (
            f'{message}. This app uses a direct PN5180 SPI reader, not an I2C reader at 0x24; '
            'confirm the installed pn5180pi package is selected, SPI is enabled, pigpiod is running, '
            'and the PN5180 NSS/BUSY/RESET/MOSI/MISO/SCK pins match the README wiring.'
        )
    if 'Install pigpio' in message or 'Install pn5180pi' in message:
        return (
            f'{message}. Install dependencies with install.sh or run '
            'pip install -r requirements.txt in the application virtual environment.'
        )
    return message


def describe_reader_self_test(active_reader: Any) -> str:
    """Best-effort PN5180 identity read so 'Connected' means the chip really answered."""
    read_self_test = getattr(active_reader, 'read_self_test', None)
    if not callable(read_self_test):
        return ''
    try:
        info = read_self_test()
    except Exception as exc:
        return f'identity read failed: {exc}'
    if info.get('responsive'):
        return f"PN5180 firmware {info.get('firmware_version', '?')}"
    return 'PN5180 identity read empty — check SPI wiring, pigpiod, and 3.3V logic power'


def initialize_hardware() -> None:
    global reader, hardware_status
    try:
        reset_pn5180_hardware()
        if PN5180_BACKEND == 'direct-spi':
            reader = DirectSpiPN5180Iso15693Reader()
        elif PN5180_BACKEND == 'pn5180pi':
            reader = PN5180Iso15693Reader()
        elif PN5180_CLASS is not None:
            # Auto mode preserves compatibility, but production Pi installs default to direct-spi
            # to avoid accidentally selecting PN532/I2C-style helper libraries.
            reader = PN5180Iso15693Reader()
        else:
            reader = DirectSpiPN5180Iso15693Reader()
        hardware_status = f'Connected: {reader.label}'
        detail = describe_reader_self_test(reader)
        if detail:
            hardware_status = f'Connected: {reader.label} — {detail}'
    except Exception as exc:
        reader = None
        hardware_status = f'Error: {describe_hardware_error(exc)}'


def ensure_reader() -> bool:
    if not reader:
        log_to_web(f'❌ PN5180 reader offline ({hardware_status}).')
        emit_action_complete('fail')
        return False
    return True


def poll_for_iso15693_tag() -> Optional[bytes]:
    timeout = time.monotonic() + TAG_DETECTION_TIMEOUT_SECONDS
    while time.monotonic() < timeout:
        try:
            if reader:
                uid = reader.poll_uid()
                if uid:
                    return uid
        except Exception as exc:
            log_to_web(f'⚠️ Reader poll error: {exc}')
        time.sleep(TAG_DETECTION_POLL_SECONDS)
    return None


def log_scan_diagnostics() -> None:
    """Explain a scan timeout: distinguish SPI/comms problems from RF/sticker ones."""
    summary = getattr(reader, 'last_scan_summary', None)
    if callable(summary):
        log_to_web(f'   • {summary()}')
    info = getattr(reader, 'self_test', None)
    if info and info.get('responsive'):
        log_to_web(
            f"   • PN5180 firmware {info.get('firmware_version', '?')} answers on SPI, so this points to the "
            'RF side: confirm 5V RF power and hold one ISO 15693 / NFC-V sticker flat on the coil.'
        )
    elif info:
        log_to_web('   • PN5180 identity read was empty earlier — run Self-Test; SPI comms may be the real problem.')
    else:
        log_to_web('   • Tip: press Self-Test to confirm the PN5180 is actually responding on SPI.')


def run_tag_info() -> None:
    if not ensure_reader():
        record_operation('tag_info', 'fail', reason='reader_offline')
        return
    read_system_info = getattr(reader, 'read_system_info', None)
    log_to_web('🪪 Reading full tag profile (UID + Get System Information)...')
    uid = poll_for_iso15693_tag()
    if not uid:
        log_to_web('❌ No sticker detected.')
        log_scan_diagnostics()
        record_operation('tag_info', 'fail', reason='timeout')
        emit_action_complete('fail')
        return
    log_to_web(f'🎯 UID: {format_uid(uid)}')
    if not callable(read_system_info):
        log_to_web('ℹ️ Tag info requires a backend with Get System Information support.')
        record_operation('tag_info', 'skipped', backend=NFC_READER_BACKEND)
        emit_action_complete('fail')
        return
    try:
        info = reader.read_system_info(uid)
    except Exception as exc:
        log_to_web(f'⚠️ Get System Information failed: {exc}')
        record_operation('tag_info', 'fail', error=str(exc))
        emit_action_complete('fail')
        return
    log_to_web(f"   • DSFID: {info.get('dsfid', 'n/a')}")
    log_to_web(f"   • AFI: {info.get('afi', 'n/a')}")
    log_to_web(f"   • Memory: {info.get('block_count', 'n/a')} blocks x {info.get('block_size', 'n/a')} bytes")
    log_to_web(f"   • IC reference: {info.get('ic_reference', 'n/a')}")
    log_to_web(f"   • Info flags: {info.get('info_flags', 'n/a')}")

    locked_blocks: list[int] = []
    read_block_security = getattr(reader, 'read_block_security', None)
    if callable(read_block_security):
        try:
            statuses = reader.read_block_security(uid, 0, len(CLEARED_DATA_BLOCKS))
            locked_blocks = [index for index, locked in enumerate(statuses) if locked]
            if locked_blocks:
                log_to_web(f'   • Locked blocks ({len(locked_blocks)}): {locked_blocks}')
            else:
                log_to_web('   • Locked blocks: none (all blocks writable)')
        except Exception as exc:
            log_to_web(f'   • Block lock status unavailable: {exc}')

    log_to_web('   • Run this on the genuine master and the clone and compare the fields.')
    record_operation('tag_info', 'success', locked_blocks=locked_blocks, **info)
    emit_action_complete('success')


def run_set_counter(value: int) -> None:
    if not ensure_reader():
        record_operation('set_counter', 'fail', reason='reader_offline')
        return
    write_block = getattr(reader, 'write_block', None)
    if not callable(write_block):
        log_to_web('ℹ️ Set Counter requires a backend with block-write support.')
        record_operation('set_counter', 'skipped', backend=NFC_READER_BACKEND)
        emit_action_complete('fail')
        return
    value = max(0, min(int(value), 0xFFFF))
    block = bytes([value & 0xFF, (value >> 8) & 0xFF, 0x00, 0x00])
    log_to_web(f'🔢 Setting prints-remaining counter (block {PRINT_COUNTER_BLOCK}) to {value} ({block.hex()})...')
    uid = poll_for_iso15693_tag()
    if not uid:
        log_to_web('❌ No sticker detected.')
        log_scan_diagnostics()
        record_operation('set_counter', 'fail', reason='timeout')
        emit_action_complete('fail')
        return
    try:
        reader.write_block(uid, PRINT_COUNTER_BLOCK, block)
    except Exception as exc:
        log_to_web(f'❌ Counter write failed: {exc}')
        record_operation('set_counter', 'fail', error=str(exc))
        emit_action_complete('fail')
        return
    readback = None
    read_block = getattr(reader, 'read_block', None)
    if callable(read_block):
        try:
            readback = bytes(reader.read_block(uid, PRINT_COUNTER_BLOCK))
            log_to_web(f'   • Block {PRINT_COUNTER_BLOCK} now reads: {readback.hex()}')
        except Exception as exc:
            log_to_web(f'   • Readback failed: {exc}')
    ok = readback is not None and readback[:2] == block[:2]
    if ok:
        log_to_web(f'✅ Counter set to {value}. Now try a print in the booth and watch the count.')
    else:
        log_to_web('⚠️ Counter readback did not match — the write may not have taken.')
    record_operation('set_counter', 'success' if ok else 'fail', value=value, block=block.hex())
    emit_action_complete('success' if ok else 'fail')


def run_lock_to_master() -> None:
    if not ensure_reader():
        record_operation('lock_to_master', 'fail', reason='reader_offline')
        return
    lock_block = getattr(reader, 'lock_block', None)
    read_block = getattr(reader, 'read_block', None)
    if not callable(lock_block) or not callable(read_block):
        log_to_web('ℹ️ Lock requires a backend with block lock/read support.')
        record_operation('lock_to_master', 'skipped', backend=NFC_READER_BACKEND)
        emit_action_complete('fail')
        return

    log_to_web('🔒 Lock to Master: PERMANENTLY locking blocks to match the genuine tag.')
    uid = poll_for_iso15693_tag()
    if not uid:
        log_to_web('❌ No sticker detected.')
        log_scan_diagnostics()
        record_operation('lock_to_master', 'fail', reason='timeout')
        emit_action_complete('fail')
        return

    # Safety: only lock a verified-correct clone, never the master or a bad copy.
    if uid != TARGET_UID:
        log_to_web(f'❌ Refusing to lock: UID {format_uid(uid)} is not the master {format_uid(TARGET_UID)}. Burn a clone first.')
        record_operation('lock_to_master', 'fail', reason='uid_mismatch')
        emit_action_complete('fail')
        return
    mismatched = []
    for block_index, expected in enumerate(CLEARED_DATA_BLOCKS):
        try:
            if bytes(reader.read_block(uid, block_index)) != bytes(expected):
                mismatched.append(block_index)
        except Exception:
            mismatched.append(block_index)
    if mismatched:
        log_to_web(f'❌ Refusing to lock: {len(mismatched)} block(s) do not match the master. Re-burn first.')
        record_operation('lock_to_master', 'fail', reason='data_mismatch', mismatched_blocks=mismatched)
        emit_action_complete('fail')
        return

    log_to_web(f'🔒 Locking {len(TARGET_LOCKED_BLOCKS)} blocks (1, 3-49, 63) — this cannot be undone...')
    failed = []
    for block_index in TARGET_LOCKED_BLOCKS:
        try:
            reader.lock_block(uid, block_index)
        except Exception as exc:
            failed.append(block_index)
            log_to_web(f'   ⚠️ Block {block_index:02d} lock failed: {exc}')

    confirmed = None
    read_block_security = getattr(reader, 'read_block_security', None)
    if callable(read_block_security):
        try:
            statuses = reader.read_block_security(uid, 0, len(CLEARED_DATA_BLOCKS))
            now_locked = [index for index, locked in enumerate(statuses) if locked]
            confirmed = now_locked == TARGET_LOCKED_BLOCKS
            log_to_web(f'   • Now locked ({len(now_locked)}): {now_locked}')
        except Exception as exc:
            log_to_web(f'   • Could not read back lock status: {exc}')

    if failed or confirmed is False:
        log_to_web('❌ Lock incomplete — the tag may not support permanent locking (or not match the master). Try the booth anyway and report.')
        status = 'fail'
    else:
        log_to_web('✅ Locked to match the master. Try it in the booth now.')
        status = 'success'
    record_operation('lock_to_master', status, failed_blocks=failed, confirmed=confirmed)
    emit_action_complete(status)


def run_dump_tag() -> None:
    if not ensure_reader():
        record_operation('dump', 'fail', reason='reader_offline')
        return
    read_block = getattr(reader, 'read_block', None)
    if not callable(read_block):
        log_to_web('ℹ️ Dump requires a backend with block-read support.')
        record_operation('dump', 'skipped', backend=NFC_READER_BACKEND)
        emit_action_complete('fail')
        return

    total_blocks = len(CLEARED_DATA_BLOCKS)
    log_to_web(f'🗂️ Dumping all {total_blocks} blocks + lock status...')
    uid = poll_for_iso15693_tag()
    if not uid:
        log_to_web('❌ No sticker detected.')
        log_scan_diagnostics()
        record_operation('dump', 'fail', reason='timeout')
        emit_action_complete('fail')
        return

    log_to_web(f'🎯 UID: {format_uid(uid)}')
    blocks: list[bytes] = []
    read_errors: list[int] = []
    for block_index in range(total_blocks):
        try:
            blocks.append(bytes(reader.read_block(uid, block_index)))
        except Exception as exc:
            blocks.append(b'')
            read_errors.append(block_index)
            log_to_web(f'   ⚠️ Block {block_index:02d} read failed: {exc}')
    for row in range(0, total_blocks, 16):
        cells = [
            (blocks[i].hex() if blocks[i] else '??' * ISO15693_BLOCK_SIZE)
            for i in range(row, min(row + 16, total_blocks))
        ]
        log_to_web(f'   {row:02d}: ' + ' '.join(cells))

    locked_blocks: list[int] = []
    read_block_security = getattr(reader, 'read_block_security', None)
    if callable(read_block_security):
        try:
            statuses = reader.read_block_security(uid, 0, total_blocks)
            locked_blocks = [index for index, locked in enumerate(statuses) if locked]
            log_to_web(f'   • Locked blocks ({len(locked_blocks)}): {locked_blocks or "none"}')
        except Exception as exc:
            log_to_web(f'   • Lock status unavailable: {exc}')

    status = 'fail' if read_errors else 'success'
    record_operation(
        'dump',
        status,
        uid=format_uid(uid),
        data=''.join(block.hex() for block in blocks),
        locked_blocks=locked_blocks,
        read_errors=read_errors,
    )
    log_to_web('   • Full hex is also in /history.json. Dump before AND after one print to see what the booth changes.')
    emit_action_complete(status)


def run_verify() -> None:
    if not ensure_reader():
        record_operation('verify', 'fail', reason='reader_offline')
        return
    read_block = getattr(reader, 'read_block', None)
    if not callable(read_block):
        log_to_web('ℹ️ Verify requires a reader backend with block-read support.')
        record_operation('verify', 'skipped', backend=NFC_READER_BACKEND)
        emit_action_complete('fail')
        return

    total_blocks = len(CLEARED_DATA_BLOCKS)
    log_to_web('🔍 Verify: reading the tag back and comparing to the master...')
    uid = poll_for_iso15693_tag()
    if not uid:
        log_to_web('❌ No sticker detected to verify.')
        log_scan_diagnostics()
        record_operation('verify', 'fail', reason='timeout')
        emit_action_complete('fail')
        return

    log_to_web(f'🎯 Tag UID: {format_uid(uid)}')
    uid_matches = uid == TARGET_UID
    if uid_matches:
        log_to_web(f'   ✅ UID matches master {format_uid(TARGET_UID)}.')
    else:
        log_to_web(
            f'   ⚠️ UID is {format_uid(uid)}, master is {format_uid(TARGET_UID)} — '
            'UID not cloned (only matters if your booth checks the UID).'
        )

    mismatched_blocks: list[int] = []
    read_errors: list[int] = []
    for block_index, expected in enumerate(CLEARED_DATA_BLOCKS):
        try:
            actual = bytes(reader.read_block(uid, block_index))
        except Exception as exc:
            read_errors.append(block_index)
            log_to_web(f'   ⚠️ Block {block_index:02d} read failed: {exc}')
            continue
        if actual != bytes(expected):
            mismatched_blocks.append(block_index)
            log_to_web(f'   ❌ Block {block_index:02d} differs: read {actual.hex()} expected {bytes(expected).hex()}')
        if (block_index + 1) % 16 == 0 or block_index + 1 == total_blocks:
            log_to_web(f'   • Verified {block_index + 1}/{total_blocks} blocks...')

    data_matches = not mismatched_blocks and not read_errors
    if data_matches:
        log_to_web(f'   ✅ All {total_blocks} data blocks match the master.')
    else:
        log_to_web(
            f'   ❌ {len(mismatched_blocks)} block(s) differ, {len(read_errors)} read error(s) — '
            're-run the burn.'
        )

    if data_matches and uid_matches:
        log_to_web('✅ Verify passed: exact clone (UID + all data blocks match).')
        status = 'success'
    elif data_matches:
        log_to_web('⚠️ Verify: data is a faithful copy, but the UID was not cloned.')
        status = 'fail'
    else:
        log_to_web('❌ Verify failed: the data on the tag does not match the master.')
        status = 'fail'

    record_operation(
        'verify',
        status,
        uid=format_uid(uid),
        uid_matches=uid_matches,
        mismatched_blocks=mismatched_blocks,
        read_errors=read_errors,
    )
    emit_action_complete(status)


def run_self_test() -> None:
    if not ensure_reader():
        record_operation('self_test', 'fail', reason='reader_offline')
        return
    read_self_test = getattr(reader, 'read_self_test', None)
    if not callable(read_self_test):
        log_to_web('ℹ️ Self-test is only available on the direct-SPI PN5180 backend.')
        record_operation('self_test', 'skipped', backend=NFC_READER_BACKEND)
        emit_action_complete('success')
        return
    try:
        info = read_self_test()
    except Exception as exc:
        log_to_web(f'❌ PN5180 self-test could not read the chip: {exc}')
        record_operation('self_test', 'fail', error=str(exc))
        emit_action_complete('fail')
        return
    log_to_web('🔎 PN5180 SPI self-test:')
    log_to_web(f"   • Firmware version: {info.get('firmware_version', 'unknown')}")
    log_to_web(f"   • Product version:  {info.get('product_version', 'unknown')}")
    log_to_web(f"   • EEPROM version:   {info.get('eeprom_version', 'unknown')}")
    log_to_web(f"   • DIE ID:           {info.get('die_id', 'unknown')}")
    if info.get('responsive'):
        log_to_web(
            '✅ PN5180 is responding on SPI. If scans still time out, the issue is the RF side: '
            'confirm 5V RF power and use an ISO 15693 / NFC-V sticker held flat on the coil.'
        )
        status = 'success'
    else:
        log_to_web(
            '❌ PN5180 returned an empty identity. SPI comms are not working — recheck '
            'NSS/BUSY/RESET/MOSI/MISO/SCK wiring, that SPI is enabled, pigpiod is running, '
            'and that 3.3V logic power is present.'
        )
        status = 'fail'
    record_operation('self_test', status, **info)
    emit_action_complete(status)


def run_tag_scan() -> None:
    if not ensure_reader():
        record_operation('scan_tag', 'fail', reason='reader_offline')
        return
    log_to_web(f'⏳ Waiting up to {TAG_DETECTION_TIMEOUT_SECONDS:g}s for an ISO 15693 / NFC-V sticker...')
    uid = poll_for_iso15693_tag()
    if not uid:
        log_to_web('❌ No ISO 15693 / NFC-V sticker detected.')
        log_scan_diagnostics()
        record_operation('scan_tag', 'fail', reason='timeout')
        emit_action_complete('fail')
        return
    log_to_web(f'✅ Detected NFC-V UID: {format_uid(uid)}')
    record_operation('scan_tag', 'success', uid=format_uid(uid))
    emit_action_complete('success')


def run_reconnect() -> None:
    initialize_hardware()
    update_ui_status()
    if reader:
        log_to_web(f'✅ Reconnected to {reader.label}.')
        info = getattr(reader, 'self_test', None)
        if info:
            if info.get('responsive'):
                log_to_web(f"   • PN5180 self-test OK (firmware {info.get('firmware_version', '?')}).")
            else:
                log_to_web('   • ⚠️ PN5180 did not return an identity — SPI comms look wrong; run Self-Test.')
        record_operation('reconnect_reader', 'success')
        emit_action_complete('success')
    else:
        log_to_web(f'❌ Reconnect failed: {hardware_status}')
        record_operation('reconnect_reader', 'fail', hardware_status=hardware_status, backend=NFC_READER_BACKEND)
        emit_action_complete('fail')


def write_data_blocks(uid: bytes) -> tuple[int, list[int]]:
    written = 0
    failed_blocks = []
    total_blocks = len(CLEARED_DATA_BLOCKS)
    for block_index, block_bytes in enumerate(CLEARED_DATA_BLOCKS):
        try:
            if not reader:
                raise RuntimeError('reader offline')
            reader.write_block(uid, block_index, block_bytes)
            written += 1
        except Exception as exc:
            failed_blocks.append(block_index)
            log_to_web(f'   ⚠️ Block {block_index:02d} write failed: {exc}')
        if (block_index + 1) % 16 == 0 or block_index + 1 == total_blocks:
            log_to_web(f'   • Written {written}/{total_blocks} blocks...')
    return written, failed_blocks


def run_burn_sequence() -> None:
    if not ensure_reader():
        record_operation('burn', 'fail', reason='reader_offline')
        return

    total_blocks = len(CLEARED_DATA_BLOCKS)
    log_to_web('🚀 PN5180 NFC-V ink clone protocol started.')
    log_to_web('⏳ [STEP 1/4] Place one authorized writable ISO 15693 / NFC-V sticker on the PN5180 antenna...')
    uid = poll_for_iso15693_tag()
    if not uid:
        log_to_web('❌ Timeout: no NFC-V sticker found.')
        log_scan_diagnostics()
        record_operation('burn', 'fail', reason='timeout')
        emit_action_complete('fail')
        return

    log_to_web(f'🎯 [STEP 2/4] NFC-V sticker detected: {format_uid(uid)}')
    log_to_web(f'🧱 [STEP 3/4] Writing {total_blocks} ISO 15693 blocks ({ISO15693_BLOCK_SIZE} bytes each)...')
    written, failed_blocks = write_data_blocks(uid)

    uid_backdoor_status = 'disabled'
    log_to_web('🔐 [STEP 4/4] UID backdoor write policy check...')
    if ENABLE_UID_BACKDOOR:
        try:
            if not reader:
                raise RuntimeError('reader offline')
            reader.write_uid_backdoor(TARGET_UID)
            new_uid = None
            for _ in range(3):
                new_uid = reader.poll_uid()
                if new_uid:
                    break
            if new_uid == TARGET_UID:
                uid_backdoor_status = 'success'
                log_to_web(f'   • ✅ Master UID set and verified: {format_uid(TARGET_UID)}')
            else:
                uid_backdoor_status = 'fail'
                current = format_uid(new_uid) if new_uid else 'unreadable'
                log_to_web(
                    f'   ⚠️ UID did not change: tag still reports {current} '
                    f'(expected {format_uid(TARGET_UID)}). The UID-set commands target '
                    'PN532Killer / MTools Gen2 UID-changeable tags — confirm your blank is that type.'
                )
        except Exception as exc:
            uid_backdoor_status = 'fail'
            log_to_web(f'   ⚠️ UID backdoor write failed: {exc}')
    else:
        log_to_web('   • Skipped UID backdoor write; set ENABLE_UID_BACKDOOR=true only for authorized magic UID media.')

    if failed_blocks or uid_backdoor_status == 'fail':
        status = 'fail'
        log_to_web(f'❌ Burn incomplete: {written}/{total_blocks} blocks written.')
    else:
        status = 'success'
        log_to_web(f'✅ Burn complete: {written}/{total_blocks} blocks written.')

    record_operation(
        'burn',
        status,
        source_uid=format_uid(uid),
        blocks_written=written,
        failed_blocks=failed_blocks,
        uid_backdoor=uid_backdoor_status,
        backend=NFC_READER_BACKEND,
    )
    emit_action_complete(status)


def with_lock(fn, *args) -> None:
    if not op_lock.acquire(blocking=False):
        log_to_web('⚠️ System busy: another operation is already running.')
        emit_action_complete('busy')
        return
    try:
        fn(*args)
    except Exception as exc:
        log_to_web(f'❌ Unexpected error: {exc}')
        record_operation(getattr(fn, '__name__', 'unknown'), 'fail', error=str(exc))
        emit_action_complete('fail')
    finally:
        op_lock.release()


@app.route('/')
def index():
    return render_template('index.html', hw_status=hardware_status, backend=NFC_READER_BACKEND)


@app.route('/favicon.ico')
def favicon():
    return Response(status=204)


@app.route('/healthz')
def healthz():
    return jsonify({
        'ok': True,
        'hardware_status': hardware_status,
        'backend': NFC_READER_BACKEND,
        'self_test': getattr(reader, 'self_test', None) if reader else None,
    })


@app.route('/history.json')
def history_json():
    return Response(
        json.dumps(operation_history, indent=2),
        mimetype='application/json',
    )


@socketio.on('start_burn')
def handle_burn():
    socketio.start_background_task(with_lock, run_burn_sequence)


@socketio.on('scan_tag')
def handle_scan_tag():
    socketio.start_background_task(with_lock, run_tag_scan)


@socketio.on('self_test')
def handle_self_test():
    socketio.start_background_task(with_lock, run_self_test)


@socketio.on('verify_clone')
def handle_verify():
    socketio.start_background_task(with_lock, run_verify)


@socketio.on('tag_info')
def handle_tag_info():
    socketio.start_background_task(with_lock, run_tag_info)


@socketio.on('dump_tag')
def handle_dump_tag():
    socketio.start_background_task(with_lock, run_dump_tag)


@socketio.on('set_counter')
def handle_set_counter(payload=None):
    try:
        value = int((payload or {}).get('value', 0))
    except (TypeError, ValueError):
        value = 0
    socketio.start_background_task(with_lock, run_set_counter, value)


@socketio.on('lock_to_master')
def handle_lock_to_master():
    socketio.start_background_task(with_lock, run_lock_to_master)


@socketio.on('reconnect_reader')
def handle_reconnect():
    socketio.start_background_task(with_lock, run_reconnect)


@socketio.on('refresh_hw_status')
def handle_refresh_hw_status():
    update_ui_status()


initialize_hardware()
if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=int(os.getenv('PORT', '5000')), debug=False, allow_unsafe_werkzeug=True)
