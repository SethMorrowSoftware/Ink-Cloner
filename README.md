# Photobooth Ink Cloner

Professional Flask + Socket.IO PN532 console with **ink cloning as the primary workflow** and a focused operator UI for authorized ISO 15693 tag management.

## Core focus
- Guided **Ink Clone Burn** workflow with step-by-step console logs and completion status.
- UID scan for quick ISO 15693 tag detection.
- Reader reconnect controls and live hardware status.
- Operation history/audit export at `/history.json`.
- Health check endpoint at `/healthz`.
- Safer default behavior: UID backdoor writes are disabled unless explicitly enabled with `ENABLE_UID_BACKDOOR=true`.

## Quick start
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python app.py
```
Open: `http://localhost:5000`

The web UI can still start on a development machine without PN532 libraries installed; hardware actions will report that the reader is unavailable until the PN532 stack is installed and connected.

## Configuration
- `SECRET_KEY` (default `change-me-in-production`)
- `CORS_ALLOWED_ORIGINS` (default `*`)
- `PORT` (default `5000`)
- `TAG_DETECTION_TIMEOUT_SECONDS` (default `15`, minimum `1`)
- `TAG_DETECTION_POLL_SECONDS` (default `0.2`, minimum `0.05`)
- `WRITE_BLOCK_RESPONSE_LENGTH` (default `10`, minimum `1`)
- `ENABLE_UID_BACKDOOR` (default `false`; set to `true` only when you are authorized to write UID backdoor registers)

## Testing
```bash
python -m unittest discover -s tests
python -m py_compile app.py
```

## Safety
Use only on tags/systems you own or are authorized to manage. Keep UID backdoor writes disabled unless the target tag and workflow explicitly require them and you are authorized to perform that operation.
