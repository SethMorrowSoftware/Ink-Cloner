# Photobooth Ink Cloner

Professional Flask + Socket.IO operations console for PN532-based NFC workflows.

## What’s included

- **Ink cloning workflow preserved** (existing burn sequence remains core capability).
- **PN532 hardware diagnostics** (firmware/version query).
- **Universal UID scan** for quick tag detection.
- **MIFARE Classic block operations**:
  - Authenticate block with Key A
  - Read 16-byte block
  - Write 16-byte block
- **Safer concurrency model**: one NFC operation at a time with explicit busy responses.
- **Live operator logs** in browser.

## Safety & legal

Use only on tags/systems you own or are authorized to manage. Writing NFC memory or changing identifiers can violate law, contracts, or warranties.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install flask flask-socketio adafruit-blinka adafruit-circuitpython-pn532
python app.py
```

Open: `http://localhost:5000`

## Config

Environment variables:

- `SECRET_KEY`
- `CORS_ALLOWED_ORIGINS`
- `PORT` (default `5000`)
- `TAG_DETECTION_TIMEOUT_SECONDS` (default `10`)
- `TAG_DETECTION_POLL_SECONDS` (default `0.2`)
- `WRITE_BLOCK_RESPONSE_LENGTH` (default `10`)
- `MIFARE_DEFAULT_KEY_A_HEX` (default `FFFFFFFFFFFF`)

## New Operations

- **Get Firmware**: fetch PN532 firmware info to verify reader state.
- **Read Block**: enter Classic block index and read data.
- **Write Block**: write 16-byte hex payload to Classic block.

> Tip: avoid trailer blocks (`3, 7, 11, ...`) unless intentionally changing keys/access bits.

## Next polish opportunities

- Add sector-dump and full-card backup/restore workflows.
- Add configurable keys per sector.
- Add NTAG page read/write support if your tag mix includes Type 2 tags.
- Add role-based auth and full audit history for production operators.
