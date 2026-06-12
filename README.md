# Photobooth Ink Cloner

Professional Flask + Socket.IO **PN5180** console with **ink cloning as the primary workflow** and a focused operator UI for authorized ISO 15693 / NFC-V sticker management.

## Core focus
- Guided **Ink Clone Burn** workflow with step-by-step console logs and completion status.
- PN5180-first NFC-V UID scan and ISO 15693 block writes.
- Reader reconnect controls and live hardware status.
- Operation history/audit export at `/history.json`.
- Health check endpoint at `/healthz`.
- Safer default behavior: UID backdoor writes are disabled unless explicitly enabled with `ENABLE_UID_BACKDOOR=true`.

## Supported PN5180 setups

The app now uses a small reader adapter layer so it can talk to PN5180 hardware instead of the old PN532 path.

### Direct Raspberry Pi PN5180 board (default)

Use this when the PN5180 module is wired directly to the Pi SPI bus plus NSS, BUSY, and RESET GPIO lines.

```bash
sudo systemctl enable --now pigpiod
export NFC_READER_BACKEND=pn5180pi
export PN5180_NSS_PIN=8
export PN5180_BUSY_PIN=24
export PN5180_RESET_PIN=23
export PN5180_SPI_CHANNEL=0
python app.py
```

Default wiring assumptions use Raspberry Pi BCM GPIO numbers:

| PN5180 pin | Raspberry Pi connection |
| --- | --- |
| 5V | 5V power for RF field |
| 3.3V | 3.3V logic power |
| GND | GND |
| NSS/SSEL | GPIO 8 / CE0 |
| BUSY | GPIO 24 |
| RST | GPIO 23 |
| MOSI | GPIO 10 / SPI0 MOSI |
| MISO | GPIO 9 / SPI0 MISO |
| SCK | GPIO 11 / SPI0 SCLK |

> Important: many PN5180 boards require both 3.3V and 5V connected. SPI can appear alive with only 3.3V, but the antenna/RF field will not reliably power NFC-V stickers without 5V.

### PN5180-tagomatic USB/serial firmware

Use this when the PN5180 is connected through the PN5180-tagomatic Pico firmware.

```bash
export NFC_READER_BACKEND=pn5180-tagomatic
export PN5180_TAGOMATIC_SERIAL=/dev/ttyACM0
python app.py
```

## Quick start
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python app.py
```
Open: `http://localhost:5000`

The web UI can still start on a development machine without PN5180 libraries installed; hardware actions will report that the reader is unavailable until the selected PN5180 stack is installed and connected.

## Configuration
- `SECRET_KEY` (default `change-me-in-production`)
- `CORS_ALLOWED_ORIGINS` (default `*`)
- `PORT` (default `5000`)
- `NFC_READER_BACKEND` (default `pn5180pi`; supported: `pn5180pi`, `pn5180-tagomatic`)
- `PN5180_NSS_PIN` (default `8`, BCM numbering for direct PN5180 boards)
- `PN5180_BUSY_PIN` (default `24`, BCM numbering for direct PN5180 boards)
- `PN5180_RESET_PIN` (default `23`, BCM numbering for direct PN5180 boards)
- `PN5180_SPI_CHANNEL` (default `0`)
- `PN5180_SPI_SPEED_HZ` (default `1000000`)
- `PN5180_TAGOMATIC_SERIAL` (default `/dev/ttyACM0`)
- `TAG_DETECTION_TIMEOUT_SECONDS` (default `15`, minimum `1`)
- `TAG_DETECTION_POLL_SECONDS` (default `0.2`, minimum `0.05`)
- `ISO15693_BLOCK_SIZE` (default `4`, minimum `1`)
- `ENABLE_UID_BACKDOOR` (default `false`; set to `true` only when you are authorized to write UID backdoor registers and your PN5180 driver supports it)

## Testing
```bash
python -m unittest discover -s tests
python -m py_compile app.py
```

## Safety
Use only on stickers/systems you own or are authorized to manage. Keep UID backdoor writes disabled unless the target tag and workflow explicitly require them and you are authorized to perform that operation.
