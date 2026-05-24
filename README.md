# Photobooth Ink Cloner

Professional Flask + Socket.IO PN532 console with **ink cloning as the primary workflow** and advanced NFC operations in secondary tabs.

## Core focus
- Verbose **Ink Clone Burn** workflow (step-by-step logs, progress, summary).

## Additional PN532 features
- UID Scan
- Tag Profiling (family hint)
- Firmware check
- Reader reconnect
- MIFARE Classic read/write/dump
- Safe mode write protections + optional expert trailer writes
- In-memory backup/restore workflow (dump + restore)
- Operation history/audit export (JSON)
- NTAG (Type 2) page read/write (with verify)

## Quick start
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install flask flask-socketio adafruit-blinka adafruit-circuitpython-pn532
python app.py
```
Open: `http://localhost:5000`

## Configuration
- `SECRET_KEY`
- `CORS_ALLOWED_ORIGINS`
- `PORT` (default `5000`)
- `TAG_DETECTION_TIMEOUT_SECONDS` (default `10`)
- `TAG_DETECTION_POLL_SECONDS` (default `0.2`)
- `WRITE_BLOCK_RESPONSE_LENGTH` (default `10`)
- `MIFARE_DEFAULT_KEY_A_HEX` (default `FFFFFFFFFFFF`)
- `SAFE_MODE` (default `true`)

## Safety
Use only on tags/systems you own or are authorized to manage.
