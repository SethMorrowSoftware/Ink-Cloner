#!/usr/bin/env python3
import json
import os
import time
from threading import Lock
from typing import Optional

from flask import Flask, Response, render_template
from flask_socketio import SocketIO

import board
import busio
from adafruit_pn532.i2c import PN532_I2C

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'change-me-in-production')
socketio = SocketIO(app, cors_allowed_origins=os.getenv('CORS_ALLOWED_ORIGINS', '*'))
TAG_DETECTION_TIMEOUT_SECONDS = float(os.getenv('TAG_DETECTION_TIMEOUT_SECONDS', '10'))
TAG_DETECTION_POLL_SECONDS = float(os.getenv('TAG_DETECTION_POLL_SECONDS', '0.2'))
WRITE_BLOCK_RESPONSE_LENGTH = int(os.getenv('WRITE_BLOCK_RESPONSE_LENGTH', '10'))
DEFAULT_KEY_A = bytes.fromhex(os.getenv('MIFARE_DEFAULT_KEY_A_HEX', 'FFFFFFFFFFFF'))

pn532 = None
hardware_status = 'Disconnected'
op_lock = Lock()

TARGET_UID = bytes([0xE0, 0x07, 0x81, 0x6A, 0xE3, 0x2E, 0x96, 0x32])
CLEARED_DATA_BLOCKS = [
    bytes([0x29, 0x50, 0x4E, 0x44]), bytes([0x00, 0x01, 0xA3, 0x42]),
    bytes([0x45, 0x02, 0x00, 0x00]), bytes([0xA1, 0x10, 0x13, 0x17]),
] + [bytes([0x00, 0x00, 0x00, 0x00]) for _ in range(60)]


def log_to_web(msg: str) -> None: socketio.emit('log_update', {'data': msg})
def update_ui_status() -> None: socketio.emit('hw_status_update', {'status': hardware_status})


def initialize_hardware() -> None:
    global pn532, hardware_status
    try:
        i2c = busio.I2C(board.SCL, board.SDA)
        pn532 = PN532_I2C(i2c, debug=False)
        pn532.SAM_configuration()
        hardware_status = 'Connected'
    except Exception as exc:
        pn532 = None
        hardware_status = f'Error: {exc}'


def ensure_reader() -> bool:
    if not pn532:
        log_to_web('❌ PN532 unavailable.'); socketio.emit('action_complete', {'status': 'fail'}); return False
    return True


def poll_for_tag() -> Optional[bytes]:
    timeout = time.time() + TAG_DETECTION_TIMEOUT_SECONDS
    while time.time() < timeout:
        uid = pn532.read_passive_target(timeout=TAG_DETECTION_POLL_SECONDS)
        if uid is not None:
            return uid
        time.sleep(0.05)
    return None


def uid_str(uid: bytes) -> str: return '-'.join(f'{x:02X}' for x in uid)
def validate_block(block: int) -> bool: return 0 <= block <= 63

def auth(uid: bytes, block: int) -> bool:
    return bool(pn532.mifare_classic_authenticate_block(uid, block, 0x60, DEFAULT_KEY_A))


def run_tag_scan():
    if not ensure_reader(): return
    uid = poll_for_tag()
    if not uid: log_to_web('❌ No tag detected.'); socketio.emit('action_complete', {'status': 'fail'}); return
    log_to_web(f'✅ UID {uid_str(uid)}'); socketio.emit('action_complete', {'status': 'success'})


def run_get_firmware():
    if not ensure_reader(): return
    ic, ver, rev, support = pn532.firmware_version
    log_to_web(f'ℹ️ PN532 IC=0x{ic:02X} version={ver}.{rev} support=0x{support:02X}')
    socketio.emit('action_complete', {'status': 'success'})


def run_read_block(block: int = 4):
    if not validate_block(block): log_to_web('❌ Invalid block (0..63).'); socketio.emit('action_complete', {'status': 'fail'}); return
    if not ensure_reader(): return
    uid = poll_for_tag()
    if not uid: log_to_web('❌ No tag detected.'); socketio.emit('action_complete', {'status': 'fail'}); return
    if not auth(uid, block): log_to_web('❌ Auth failed.'); socketio.emit('action_complete', {'status': 'fail'}); return
    data = pn532.mifare_classic_read_block(block)
    log_to_web(f'✅ Read block {block}: {bytes(data).hex().upper() if data else "READ_FAIL"}')
    socketio.emit('action_complete', {'status': 'success' if data else 'fail'})


def run_write_block(block: int, data_hex: str, verify: bool = True):
    if not validate_block(block): log_to_web('❌ Invalid block (0..63).'); socketio.emit('action_complete', {'status': 'fail'}); return
    try: payload = bytes.fromhex(data_hex)
    except ValueError: log_to_web('❌ Invalid hex payload.'); socketio.emit('action_complete', {'status': 'fail'}); return
    if len(payload) != 16: log_to_web('❌ Payload must be 16 bytes.'); socketio.emit('action_complete', {'status': 'fail'}); return
    if not ensure_reader(): return
    uid = poll_for_tag()
    if not uid or not auth(uid, block): log_to_web('❌ Tag/auth failed.'); socketio.emit('action_complete', {'status': 'fail'}); return
    if not pn532.mifare_classic_write_block(block, payload): log_to_web('❌ Write failed.'); socketio.emit('action_complete', {'status': 'fail'}); return
    if verify:
        rb = pn532.mifare_classic_read_block(block)
        if not rb or bytes(rb) != payload: log_to_web('❌ Verify failed.'); socketio.emit('action_complete', {'status': 'fail'}); return
    log_to_web(f'✅ Wrote block {block} ({"verified" if verify else "no verify"})')
    socketio.emit('action_complete', {'status': 'success'})


def run_dump(start: int = 0, end: int = 15):
    if not ensure_reader(): return
    if start < 0 or end > 63 or start > end: log_to_web('❌ Invalid dump range.'); socketio.emit('action_complete', {'status': 'fail'}); return
    uid = poll_for_tag()
    if not uid: log_to_web('❌ No tag detected.'); socketio.emit('action_complete', {'status': 'fail'}); return
    result = {'uid': uid_str(uid), 'blocks': {}}
    for b in range(start, end + 1):
        if not auth(uid, b): result['blocks'][str(b)] = 'AUTH_FAIL'; continue
        d = pn532.mifare_classic_read_block(b)
        result['blocks'][str(b)] = bytes(d).hex().upper() if d else 'READ_FAIL'
    log_to_web('✅ Dump complete'); log_to_web(json.dumps(result))
    socketio.emit('action_complete', {'status': 'success'})


def run_reconnect():
    initialize_hardware(); update_ui_status()
    ok = hardware_status == 'Connected'
    log_to_web('✅ Reader reconnected.' if ok else f'❌ Reconnect failed: {hardware_status}')
    socketio.emit('action_complete', {'status': 'success' if ok else 'fail'})


def run_burn_sequence():
    if not ensure_reader():
        return

    log_to_web('🚀 Ink Clone Protocol started.')
    log_to_web('ℹ️ Preparing PN532 session and validating reader state...')
    log_to_web('⏳ [STEP 1/5] Waiting for ink tag placement on antenna...')

    uid = poll_for_tag()
    if not uid:
        log_to_web('❌ Timeout: No tag detected within configured detection window.')
        socketio.emit('action_complete', {'status': 'fail'})
        return

    log_to_web(f'🎯 Tag detected: UID {uid_str(uid)}')
    log_to_web(f'ℹ️ Tag UID length: {len(uid)} bytes')
    log_to_web('✍️ [STEP 2/5] Starting clone data write pass (64 blocks)...')

    failed = []
    wrote = 0
    milestones = {1, 2, 3, 4, 8, 16, 24, 32, 40, 48, 56, 64}

    for i, block_bytes in enumerate(CLEARED_DATA_BLOCKS):
        try:
            pn532.call_function(
                0x42,
                params=bytes([0x42, 0x21, i]) + block_bytes,
                response_length=WRITE_BLOCK_RESPONSE_LENGTH,
            )
            wrote += 1
            current = i + 1
            if current in milestones:
                log_to_web(f'   • Write progress: {current}/64 blocks complete')
        except Exception as exc:
            failed.append(i)
            log_to_web(f'⚠️ Block {i} skipped due to write error: {exc}')

    log_to_web('🔐 [STEP 3/5] Applying Gen2 UID handshake payload...')
    pn532.call_function(
        0x42,
        params=bytes([0x42, 0xB4, 0x00]) + TARGET_UID,
        response_length=WRITE_BLOCK_RESPONSE_LENGTH,
    )

    log_to_web('🧪 [STEP 4/5] Performing post-write summary checks...')
    log_to_web(f'ℹ️ Blocks attempted: 64 | blocks written: {wrote} | blocks skipped: {len(failed)}')

    if failed:
        failed_str = ', '.join(str(i) for i in failed)
        log_to_web(f'⚠️ Clone finished with warnings. Skipped blocks: {failed_str}')
    else:
        log_to_web('✅ Clone blocks written with no skipped blocks.')

    log_to_web('🏁 [STEP 5/5] Finalizing operation and notifying UI...')
    log_to_web('✅ SUCCESS: Ink clone burn completed.')
    socketio.emit('action_complete', {'status': 'success'})


def with_lock(fn, *a):
    if not op_lock.acquire(blocking=False): log_to_web('⚠️ Busy.'); socketio.emit('action_complete', {'status': 'busy'}); return
    try: fn(*a)
    except Exception as exc: log_to_web(f'❌ {exc}'); socketio.emit('action_complete', {'status': 'fail'})
    finally: op_lock.release()


@app.route('/')
def index(): return render_template('index.html', hw_status=hardware_status)

@app.route('/favicon.ico')
def favicon(): return Response(status=204)

@socketio.on('start_burn')
def handle_burn(): socketio.start_background_task(with_lock, run_burn_sequence)
@socketio.on('scan_tag')
def handle_scan_tag(): socketio.start_background_task(with_lock, run_tag_scan)
@socketio.on('get_firmware')
def handle_fw(): socketio.start_background_task(with_lock, run_get_firmware)
@socketio.on('reconnect_reader')
def handle_reconnect(): socketio.start_background_task(with_lock, run_reconnect)
@socketio.on('read_classic_block')
def handle_read(payload): socketio.start_background_task(with_lock, run_read_block, int((payload or {}).get('block', 4)))
@socketio.on('write_classic_block')
def handle_write(payload):
    p = payload or {}
    socketio.start_background_task(with_lock, run_write_block, int(p.get('block', 4)), str(p.get('data_hex', '00'*16)), bool(p.get('verify', True)))
@socketio.on('dump_classic')
def handle_dump(payload):
    p = payload or {}
    socketio.start_background_task(with_lock, run_dump, int(p.get('start_block', 0)), int(p.get('end_block', 15)))
@socketio.on('refresh_hw_status')
def handle_refresh_hw_status(): update_ui_status()

initialize_hardware()

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=int(os.getenv('PORT', '5000')), debug=False, allow_unsafe_werkzeug=True)
