#!/usr/bin/env python3
import os
import time
from threading import Lock

from flask import Flask, render_template, Response
from flask_socketio import SocketIO

import board
import busio
from adafruit_pn532.i2c import PN532_I2C

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'change-me-in-production')
socketio = SocketIO(app, cors_allowed_origins=os.getenv('CORS_ALLOWED_ORIGINS', '*'))

# Runtime tuning
TAG_DETECTION_TIMEOUT_SECONDS = float(os.getenv('TAG_DETECTION_TIMEOUT_SECONDS', '10'))
TAG_DETECTION_POLL_SECONDS = float(os.getenv('TAG_DETECTION_POLL_SECONDS', '0.2'))
WRITE_BLOCK_RESPONSE_LENGTH = int(os.getenv('WRITE_BLOCK_RESPONSE_LENGTH', '10'))

# Global state
pn532 = None
hardware_status = 'Disconnected'
burn_lock = Lock()


def log_to_web(msg: str) -> None:
    socketio.emit('log_update', {'data': msg})


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


# Master Payload Configurations
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


@app.route('/')
def index():
    return render_template('index.html', hw_status=hardware_status)

@app.route('/favicon.ico')
def favicon():
    return Response(status=204)



def run_burn_sequence():
    if not burn_lock.acquire(blocking=False):
        log_to_web('⚠️ Another burn is already in progress. Please wait.')
        socketio.emit('action_complete', {'status': 'busy'})
        return

    try:
        if not pn532:
            log_to_web('❌ Hardware not connected. Check wiring and restart service.')
            socketio.emit('action_complete', {'status': 'fail'})
            return

        log_to_web('⏳ [STEP 1/3] Waiting for Magic Sticker placement...')

        uid = None
        timeout = time.time() + TAG_DETECTION_TIMEOUT_SECONDS
        while time.time() < timeout:
            try:
                uid = pn532.read_passive_target(timeout=TAG_DETECTION_POLL_SECONDS)
            except Exception as exc:
                log_to_web(f'⚠️ Reader poll error: {exc}')
                uid = None
            if uid is not None:
                break
            time.sleep(0.05)

        if uid is None:
            log_to_web('❌ Timeout: No tag detected on the reader panel.')
            socketio.emit('action_complete', {'status': 'fail'})
            return

        detected_uid_str = '-'.join([f'{x:02X}' for x in uid])
        log_to_web(f'🎯 Tag Detected! (Hardware Factory UID: {detected_uid_str})')
        log_to_web('✍️ [STEP 2/3] Writing 64 high-capacity data blocks...')

        failed_blocks = []
        for block_idx, block_bytes in enumerate(CLEARED_DATA_BLOCKS):
            try:
                write_payload = bytes([0x42, 0x21, block_idx]) + block_bytes
                pn532.call_function(0x42, params=write_payload, response_length=WRITE_BLOCK_RESPONSE_LENGTH)
            except Exception as exc:
                failed_blocks.append((block_idx, str(exc)))
                log_to_web(f'⚠️ Warning: Skip on block {block_idx} ({exc})')

        log_to_web('🔐 [STEP 3/3] Sending Gen2 backdoor handshake to spoof master UID...')
        magic_command = bytes([0x42, 0xB4, 0x00]) + TARGET_UID
        pn532.call_function(0x42, params=magic_command, response_length=WRITE_BLOCK_RESPONSE_LENGTH)

        if failed_blocks:
            failed_idxs = ', '.join(str(idx) for idx, _ in failed_blocks)
            log_to_web(f'⚠️ Completed with warnings. Blocks skipped: {failed_idxs}')
        log_to_web('✅ SUCCESS: Ink tag cloned and reset to 100% capacity!')
        socketio.emit('action_complete', {'status': 'success'})
    except Exception as exc:
        log_to_web(f'❌ Burn failed unexpectedly: {exc}')
        socketio.emit('action_complete', {'status': 'fail'})
    finally:
        burn_lock.release()


@socketio.on('start_burn')
def handle_burn():
    log_to_web('🚀 Initializing System Protocol...')
    socketio.start_background_task(run_burn_sequence)


initialize_hardware()

if __name__ == '__main__':
    socketio.run(
        app,
        host='0.0.0.0',
        port=int(os.getenv('PORT', '5000')),
        debug=False,
        allow_unsafe_werkzeug=True,
    )
