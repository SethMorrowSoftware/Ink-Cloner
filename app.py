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
gpio_module = _optional_module('RPi.GPIO')


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
PN5180_NSS_PIN = env_int('PN5180_NSS_PIN', 8, minimum=0)
PN5180_BUSY_PIN = env_int('PN5180_BUSY_PIN', 24, minimum=0)
PN5180_RESET_PIN = env_int('PN5180_RESET_PIN', 23, minimum=0)
ENABLE_UID_BACKDOOR = os.getenv('ENABLE_UID_BACKDOOR', 'false').lower() in {'1', 'true', 'yes', 'on'}
PN5180_BACKEND = os.getenv('PN5180_BACKEND', 'auto').lower()
NFC_READER_BACKEND = PN5180_BACKEND

ISO15693_FLAG_DATA_RATE_HIGH = 0x02
ISO15693_FLAG_INVENTORY = 0x04
ISO15693_FLAG_ADDRESS = 0x20
ISO15693_CMD_INVENTORY = 0x01
ISO15693_CMD_WRITE_SINGLE_BLOCK = 0x21
ISO15693_CMD_WRITE_UID_BACKDOOR = 0xB4

reader: Optional['PN5180Iso15693Reader'] = None
hardware_status = 'Disconnected'
op_lock = Lock()
operation_history: list[dict[str, Any]] = []

TARGET_UID = bytes([0xE0, 0x07, 0x81, 0x6A, 0xE3, 0x2E, 0x96, 0x32])
CLEARED_DATA_BLOCKS = [
    bytes([0x29, 0x50, 0x4E, 0x44]), bytes([0x00, 0x01, 0xA3, 0x42]),
    bytes([0x45, 0x02, 0x00, 0x00]), bytes([0xA1, 0x10, 0x13, 0x17]),
    bytes([0xF2, 0x10, 0xC0, 0x00]), bytes([0x9E, 0x01, 0x51, 0x02]),
    bytes([0x58, 0x02, 0x00, 0x00]), bytes([0x00, 0x00, 0x00, 0x00]),
    bytes([0x01, 0xA3, 0x42, 0xFF]), bytes([0x00, 0x00, 0x00, 0x00]),
    bytes([0x00, 0x00, 0x00, 0x00]), bytes([0x00, 0x00, 0x00, 0x00]),
    bytes([0x00, 0x00, 0x00, 0x00]), bytes([0x00, 0x00, 0x00, 0x00]),
    bytes([0x00, 0x00, 0x00, 0x00]), bytes([0x00, 0x00, 0x00, 0x00]),
    bytes([0x25, 0x67, 0x45, 0x9D]), bytes([0x96, 0x10, 0x5F, 0xD6]),
    bytes([0x18, 0xA9, 0x6A, 0x74]), bytes([0x26, 0x67, 0x2D, 0x21]),
    bytes([0xC9, 0xA8, 0x72, 0x5E]), bytes([0xFE, 0x30, 0x75, 0x26]),
    bytes([0xFE, 0x10, 0x24, 0x9F]), bytes([0x93, 0x43, 0x08, 0xE5]),
    bytes([0xA3, 0x60, 0x8E, 0xF3]), bytes([0x4B, 0x1F, 0x2E, 0x66]),
    bytes([0xE3, 0x84, 0x08, 0xC9]), bytes([0xC9, 0xA6, 0x47, 0x39]),
    bytes([0x38, 0x02, 0x88, 0xBF]), bytes([0x5B, 0xBE, 0x48, 0xCB]),
    bytes([0x89, 0x53, 0xBC, 0x26]), bytes([0x4F, 0x07, 0x02, 0x6B]),
    bytes([0x98, 0xFB, 0xF7, 0xAD]), bytes([0x6F, 0xD1, 0x38, 0xB0]),
    bytes([0x34, 0xA6, 0x29, 0x83]), bytes([0x81, 0x21, 0x13, 0x81]),
    bytes([0xA7, 0x8A, 0x02, 0xEC]), bytes([0xA2, 0x25, 0xA5, 0x16]),
    bytes([0x3F, 0x0A, 0x56, 0x6A]), bytes([0x0D, 0x43, 0x14, 0xF7]),
    bytes([0xAF, 0x8E, 0x59, 0x8A]), bytes([0x0C, 0x35, 0x3D, 0x93]),
    bytes([0x37, 0x3E, 0x34, 0x5E]), bytes([0x5D, 0xBD, 0x59, 0x3B]),
    bytes([0xA0, 0x7B, 0x70, 0x79]), bytes([0x4E, 0xC6, 0x14, 0xF7]),
    bytes([0xC3, 0x9F, 0x1A, 0x5A]), bytes([0xE2, 0x56, 0xAF, 0xDA]),
    bytes([0x33, 0x1F, 0xEB, 0x02]), bytes([0xFF, 0x00, 0xAE, 0x76]),
    bytes([0x60, 0x43, 0xAB, 0x79]), bytes([0x07, 0xF3, 0xE4, 0x3E]),
    bytes([0x83, 0x9B, 0xDF, 0x4D]), bytes([0xA5, 0x17, 0x5D, 0x2A]),
    bytes([0x11, 0xEC, 0x9A, 0x9F]), bytes([0x8A, 0xE6, 0xEE, 0x60]),
    bytes([0x63, 0x1A, 0x53, 0x9F]), bytes([0xF1, 0xFD, 0x34, 0x1D]),
    bytes([0xD5, 0x77, 0x68, 0xB2]), bytes([0xDA, 0xAA, 0x0D, 0x83]),
    bytes([0x7C, 0x7C, 0xC6, 0xBF]), bytes([0xE3, 0x7B, 0xD3, 0x00]),
    bytes([0x00, 0x00, 0x00, 0x00]), bytes([0x00, 0x00, 0x00, 0x00])
]


class Iso15693Reader(Protocol):
    """Minimal reader contract used by the Flask workflow."""

    label: str

    def poll_uid(self) -> Optional[bytes]:
        """Return one ISO 15693 UID when a tag is present."""

    def write_block(self, uid: bytes, block_index: int, data: bytes) -> None:
        """Write one ISO 15693 memory block."""

    def write_uid_backdoor(self, uid: bytes) -> None:
        """Write a vendor-specific UID backdoor register when supported."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def format_uid(uid: bytes) -> str:
    return '-'.join(f'{byte:02X}' for byte in uid)


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

    def _hardware_reset(self) -> None:
        self._pi.write(PN5180_RESET_PIN, 0)
        time.sleep(0.02)
        self._pi.write(PN5180_RESET_PIN, 1)
        time.sleep(0.02)

    def _wait_ready(self) -> None:
        while self._pi.read(PN5180_BUSY_PIN):
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
        self._wait_ready()
        self._select()
        try:
            self._spi_xfer(command)
            self._wait_ready()
            return self._spi_xfer([0x00] * length)
        finally:
            self._deselect()

    def _read_register(self, register: int) -> list[int]:
        return self._read_after_command([0x04, register], 4)

    def _read_data(self, length: int) -> list[int]:
        return self._read_after_command([0x0A], length)

    def _card_has_responded(self) -> bool:
        deadline = time.monotonic() + PN5180_RESPONSE_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            rx_status = self._read_register(0x13)  # RX_STATUS
            self._read_register(0x02)  # IRQ_STATUS
            self._bytes_in_card_buffer = rx_status[0] if rx_status else 0
            if self._bytes_in_card_buffer > 0:
                return True
            time.sleep(0.005)
        return False

    def _prepare_iso15693(self) -> None:
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
        self._send([0x17, 0x00])  # RF_OFF
        return response

    def _send_inventory_eof(self) -> None:
        self._send([0x02, 0x18, 0x3F, 0xFB, 0xFF, 0xFF])
        self._send([0x02, 0x00, 0xF8, 0xFF, 0xFF, 0xFF])
        self._send([0x01, 0x00, 0x03, 0x00, 0x00, 0x00])
        self._send([0x00, 0x03, 0xFF, 0xFF, 0x0F, 0x00])
        self._send([0x09, 0x00])

    def poll_uid(self) -> Optional[bytes]:
        self._prepare_iso15693()
        self._send([
            0x09,
            0x00,
            ISO15693_FLAG_DATA_RATE_HIGH | ISO15693_FLAG_INVENTORY | ISO15693_FLAG_ADDRESS,
            ISO15693_CMD_INVENTORY,
            0x00,
        ])
        try:
            for slot_index in range(16):
                if self._card_has_responded():
                    response = bytes(self._read_data(self._bytes_in_card_buffer))
                    uid = parse_iso15693_inventory_response(response)
                    if uid:
                        return uid
                if slot_index < 15:
                    self._send_inventory_eof()
        finally:
            self._send([0x17, 0x00])  # RF_OFF
        return None

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
        frame = bytes([ISO15693_FLAG_DATA_RATE_HIGH, ISO15693_CMD_WRITE_UID_BACKDOOR, 0x00]) + uid
        validate_iso15693_response(self.exchange(frame))


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
            ISO15693_FLAG_DATA_RATE_HIGH | ISO15693_FLAG_INVENTORY,
            ISO15693_CMD_INVENTORY,
            0x00,  # mask length
        ])
        return parse_iso15693_inventory_response(self.exchange(frame))

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
        frame = bytes([ISO15693_FLAG_DATA_RATE_HIGH, ISO15693_CMD_WRITE_UID_BACKDOOR, 0x00]) + uid
        validate_iso15693_response(self.exchange(frame))


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
    """Pulse the PN5180 reset pin before constructing a backend driver."""
    if gpio_module is None:
        return
    gpio_module.setmode(gpio_module.BCM)
    gpio_module.setup(PN5180_RESET_PIN, gpio_module.OUT, initial=gpio_module.HIGH)
    gpio_module.output(PN5180_RESET_PIN, gpio_module.LOW)
    time.sleep(0.1)
    gpio_module.output(PN5180_RESET_PIN, gpio_module.HIGH)
    time.sleep(0.1)


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


def initialize_hardware() -> None:
    global reader, hardware_status
    try:
        reset_pn5180_hardware()
        if PN5180_BACKEND == 'direct-spi':
            reader = DirectSpiPN5180Iso15693Reader()
        elif PN5180_CLASS is not None:
            reader = PN5180Iso15693Reader()
        else:
            reader = DirectSpiPN5180Iso15693Reader()
        hardware_status = f'Connected: {reader.label}'
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


def run_tag_scan() -> None:
    if not ensure_reader():
        record_operation('scan_tag', 'fail', reason='reader_offline')
        return
    log_to_web(f'⏳ Waiting up to {TAG_DETECTION_TIMEOUT_SECONDS:g}s for an ISO 15693 / NFC-V sticker...')
    uid = poll_for_iso15693_tag()
    if not uid:
        log_to_web('❌ No ISO 15693 / NFC-V sticker detected.')
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
            uid_backdoor_status = 'success'
            log_to_web(f'   • Master UID set to: {format_uid(TARGET_UID)}')
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
    return jsonify({'ok': True, 'hardware_status': hardware_status, 'backend': NFC_READER_BACKEND})


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


@socketio.on('reconnect_reader')
def handle_reconnect():
    socketio.start_background_task(with_lock, run_reconnect)


@socketio.on('refresh_hw_status')
def handle_refresh_hw_status():
    update_ui_status()


initialize_hardware()
if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=int(os.getenv('PORT', '5000')), debug=False, allow_unsafe_werkzeug=True)
