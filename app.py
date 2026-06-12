#!/usr/bin/env python3
"""Flask + Socket.IO console for supervised PN5180 ISO 15693 ink-tag operations."""

import importlib
import importlib.util
import json
import os
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
    return importlib.import_module(name)


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
pn5180_tagomatic_module = _optional_module('pn5180_tagomatic')

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
NFC_READER_BACKEND = os.getenv('NFC_READER_BACKEND', 'pn5180pi').strip().lower()
PN5180_TAGOMATIC_SERIAL = os.getenv('PN5180_TAGOMATIC_SERIAL', '/dev/ttyACM0')
PN5180_NSS_PIN = env_int('PN5180_NSS_PIN', 8, minimum=0)
PN5180_BUSY_PIN = env_int('PN5180_BUSY_PIN', 24, minimum=0)
PN5180_RESET_PIN = env_int('PN5180_RESET_PIN', 23, minimum=0)
PN5180_SPI_CHANNEL = env_int('PN5180_SPI_CHANNEL', 0, minimum=0)
PN5180_SPI_SPEED_HZ = env_int('PN5180_SPI_SPEED_HZ', 1_000_000, minimum=100_000)
ENABLE_UID_BACKDOOR = os.getenv('ENABLE_UID_BACKDOOR', 'false').lower() in {'1', 'true', 'yes', 'on'}

reader: Optional['Iso15693Reader'] = None
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
    """Normalize common PN5180 library UID return shapes to an 8-byte value."""
    if value is None:
        return None
    if hasattr(value, 'uid'):
        return normalize_uid(getattr(value, 'uid'))
    if hasattr(value, 'nfc_id'):
        return normalize_uid(getattr(value, 'nfc_id'))
    if hasattr(value, 'NfcId'):
        return normalize_uid(getattr(value, 'NfcId'))
    if isinstance(value, str):
        cleaned = value.replace(':', '').replace('-', '').replace(' ', '')
        if len(cleaned) == 16:
            try:
                return bytes.fromhex(cleaned)
            except ValueError:
                return None
        return None
    if isinstance(value, int):
        if value <= 0:
            return None
        return value.to_bytes(8, 'big')
    if isinstance(value, (bytes, bytearray)):
        data = bytes(value)
    elif isinstance(value, (list, tuple)):
        if value and not isinstance(value[0], int):
            return normalize_uid(value[0])
        try:
            data = bytes(value)
        except (TypeError, ValueError):
            return None
    else:
        return None

    if len(data) == 8:
        return data
    if len(data) >= 10:
        # ISO 15693 inventory responses commonly include flags + DSFID + 8-byte UID.
        return data[-8:]
    return None


def parse_iso15693_uid(response: Optional[bytes]) -> Optional[bytes]:
    """Extract a UID from legacy PN532 InListPassiveTarget or raw ISO 15693 responses."""
    if not response:
        return None
    if len(response) >= 6 and response[0] == 1:
        uid_length = response[5]
        uid_start = 6
        uid_end = uid_start + uid_length
        if uid_length > 0 and len(response) >= uid_end:
            return bytes(response[uid_start:uid_end])
    return normalize_uid(response)


def call_first_available(target: Any, method_names: tuple[str, ...], *args, **kwargs) -> Any:
    for method_name in method_names:
        method = getattr(target, method_name, None)
        if callable(method):
            return method(*args, **kwargs)
    raise AttributeError(f'{target!r} does not expose any of: {", ".join(method_names)}')


class PN5180PiReader:
    """Adapter for direct Raspberry Pi PN5180 boards using the ``pn5180pi`` package."""

    label = 'PN5180 (SPI/GPIO via pn5180pi)'

    def __init__(self, module: Any):
        driver_class = self._find_driver_class(module)
        self.device = self._instantiate_driver(driver_class)
        self._configure_device()

    @staticmethod
    def _find_driver_class(module: Any) -> Any:
        for class_name in ('PN5180', 'Pn5180', 'PN5180Pi', 'Pn5180Pi'):
            driver_class = getattr(module, class_name, None)
            if driver_class is not None:
                return driver_class
        raise RuntimeError('pn5180pi is installed, but no PN5180 driver class was found')

    @staticmethod
    def _instantiate_driver(driver_class: Any) -> Any:
        attempts = (
            lambda: driver_class(PN5180_NSS_PIN, PN5180_BUSY_PIN, PN5180_RESET_PIN),
            lambda: driver_class(nss=PN5180_NSS_PIN, busy=PN5180_BUSY_PIN, reset=PN5180_RESET_PIN),
            lambda: driver_class(nss_pin=PN5180_NSS_PIN, busy_pin=PN5180_BUSY_PIN, reset_pin=PN5180_RESET_PIN),
            lambda: driver_class(PN5180_SPI_CHANNEL, PN5180_NSS_PIN, PN5180_BUSY_PIN, PN5180_RESET_PIN),
            lambda: driver_class(spi_channel=PN5180_SPI_CHANNEL, spi_speed_hz=PN5180_SPI_SPEED_HZ,
                                 nss_pin=PN5180_NSS_PIN, busy_pin=PN5180_BUSY_PIN,
                                 reset_pin=PN5180_RESET_PIN),
            lambda: driver_class(),
        )
        last_error: Optional[Exception] = None
        for attempt in attempts:
            try:
                return attempt()
            except TypeError as exc:
                last_error = exc
        raise RuntimeError(f'Unable to construct pn5180pi driver: {last_error}')

    def _configure_device(self) -> None:
        for method_names in (
            ('begin', 'init', 'initialize', 'reset'),
            ('setup_rf', 'setupRF', 'setup_iso15693', 'setupISO15693'),
            ('rf_on', 'rfOn', 'turn_rf_on', 'activate_rf_field'),
        ):
            try:
                call_first_available(self.device, method_names)
            except AttributeError:
                continue

    def poll_uid(self) -> Optional[bytes]:
        method_names = ('inventory', 'inventory_iso15693', 'get_inventory', 'getInventory', 'read_uid', 'poll_uid')
        last_error: Optional[Exception] = None
        for method_name in method_names:
            method = getattr(self.device, method_name, None)
            if not callable(method):
                continue
            uid_buffer = bytearray(8)
            for attempt in (lambda: method(), lambda: method(uid_buffer)):
                try:
                    result = attempt()
                    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], int):
                        return normalize_uid(result[1]) if result[0] else None
                    if isinstance(result, int) and result > 0 and uid_buffer != bytearray(8):
                        return normalize_uid(uid_buffer)
                    return normalize_uid(result)
                except TypeError as exc:
                    last_error = exc
                    continue
        if last_error:
            raise last_error
        raise AttributeError(f'{self.device!r} does not expose any of: {", ".join(method_names)}')

    def _transceive_iso15693(self, frame: bytes) -> bytes:
        response = call_first_available(
            self.device,
            ('transceive_iso15693', 'transceiveISO15693', 'iso15693_transceive', 'send_iso15693', 'sendData'),
            frame,
        )
        return bytes(response or b'')

    def write_block(self, uid: bytes, block_index: int, data: bytes) -> None:
        data = validate_block_data(data)
        last_error: Optional[Exception] = None
        for method_name in ('write_single_block', 'writeSingleBlock', 'write_block', 'writeBlock'):
            method = getattr(self.device, method_name, None)
            if not callable(method):
                continue
            attempts = (
                lambda: method(uid, block_index, data, len(data)),
                lambda: method(uid, block_index, data),
                lambda: method(block_index, data),
            )
            for attempt in attempts:
                try:
                    attempt()
                    return
                except TypeError as exc:
                    last_error = exc
                    continue
        if last_error:
            raise last_error

        # Fallback for libraries exposing only raw ISO 15693 transceive.
        response = self._transceive_iso15693(bytes([0x22, 0x21]) + uid[::-1] + bytes([block_index]) + data)
        validate_iso15693_response(response)

    def write_uid_backdoor(self, uid: bytes) -> None:
        method = getattr(self.device, 'write_uid_backdoor', None) or getattr(self.device, 'writeUIDBackdoor', None)
        if not callable(method):
            raise RuntimeError('Configured PN5180 driver does not expose UID backdoor writes')
        method(uid)


class TagomaticReader:
    """Adapter for PN5180-tagomatic USB/serial firmware."""

    label = 'PN5180-tagomatic (USB serial)'

    def __init__(self, module: Any):
        self.module = module
        self.reader = module.PN5180(PN5180_TAGOMATIC_SERIAL)
        enter = getattr(self.reader, '__enter__', None)
        if callable(enter):
            enter()

    def _start_iso15693_session(self):
        tx_protocol = getattr(getattr(self.module, 'TxProtocol', object), 'ISO_15693_26', 0x0D)
        rx_protocol = getattr(getattr(self.module, 'RxProtocol', object), 'ISO_15693_26', 0x8D)
        return self.reader.start_session(tx_protocol, rx_protocol)

    def poll_uid(self) -> Optional[bytes]:
        with self._start_iso15693_session() as session:
            card = call_first_available(
                session,
                ('connect_one_iso15693', 'connect_one_iso15693_card', 'connectOneIso15693', 'listen_iso15693'),
            )
            return normalize_uid(card)

    def write_block(self, uid: bytes, block_index: int, data: bytes) -> None:
        data = validate_block_data(data)
        with self._start_iso15693_session() as session:
            card = call_first_available(
                session,
                ('connect_one_iso15693', 'connect_one_iso15693_card', 'connectOneIso15693', 'listen_iso15693'),
            )
            card_uid = normalize_uid(card)
            if card_uid and card_uid != uid:
                raise RuntimeError(f'tag changed during write: expected {format_uid(uid)}, saw {format_uid(card_uid)}')
            call_first_available(card, ('write_single_block', 'writeSingleBlock', 'write_block', 'writeBlock'), block_index, data)

    def write_uid_backdoor(self, uid: bytes) -> None:
        raise RuntimeError('PN5180-tagomatic does not expose UID backdoor writes')


def validate_block_data(data: bytes) -> bytes:
    if len(data) != ISO15693_BLOCK_SIZE:
        raise ValueError(f'ISO 15693 block must be {ISO15693_BLOCK_SIZE} bytes, got {len(data)}')
    return data


def validate_iso15693_response(response: bytes) -> None:
    if response and response[0] & 0x01:
        error_code = response[1] if len(response) > 1 else 0
        raise RuntimeError(f'ISO 15693 tag returned error 0x{error_code:02X}')


def emit_action_complete(status: str) -> None:
    socketio.emit('action_complete', {'status': status})


def log_to_web(msg: str) -> None:
    socketio.emit('log_update', {'data': msg})


def update_ui_status() -> None:
    socketio.emit('hw_status_update', {'status': hardware_status})


def record_operation(name: str, status: str, **details: Any) -> None:
    operation_history.append({
        'timestamp': utc_now_iso(),
        'operation': name,
        'status': status,
        'details': details,
    })
    del operation_history[:-100]


def initialize_hardware() -> None:
    global reader, hardware_status
    try:
        if NFC_READER_BACKEND in {'pn5180pi', 'pn5180', 'spi'}:
            if pn5180pi_module is None:
                reader = None
                hardware_status = 'Unavailable: install pn5180pi and enable pigpiod/SPI'
                return
            reader = PN5180PiReader(pn5180pi_module)
        elif NFC_READER_BACKEND in {'tagomatic', 'pn5180-tagomatic', 'serial'}:
            if pn5180_tagomatic_module is None:
                reader = None
                hardware_status = 'Unavailable: install pn5180-tagomatic and flash its PN5180 firmware'
                return
            reader = TagomaticReader(pn5180_tagomatic_module)
        else:
            reader = None
            hardware_status = f'Unavailable: unknown NFC_READER_BACKEND={NFC_READER_BACKEND}'
            return
        hardware_status = f'Connected: {reader.label}'
    except Exception as exc:
        reader = None
        hardware_status = f'Error: {exc}'


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
        record_operation('reconnect_reader', 'success', backend=NFC_READER_BACKEND)
        emit_action_complete('success')
    else:
        log_to_web(f'❌ Reconnect failed: {hardware_status}')
        record_operation('reconnect_reader', 'fail', status=hardware_status, backend=NFC_READER_BACKEND)
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
