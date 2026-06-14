# Photobooth Ink Cloner

Professional Flask + Socket.IO **PN5180** console with **ink cloning as the primary workflow** and a focused operator UI for authorized ISO 15693 / NFC-V sticker management.

## Core focus
- Guided **Ink Clone Burn** workflow with step-by-step console logs and completion status.
- PN5180-first NFC-V UID scan and ISO 15693 block writes.
- Reader reconnect controls and live hardware status.
- Operation history/audit export at `/history.json`.
- Health check endpoint at `/healthz`.
- Safer default behavior: UID backdoor writes are disabled unless explicitly enabled with `ENABLE_UID_BACKDOOR=true`.

## PN5180 setup

The app uses the `pn5180pi` library directly through `pn5180pi.Pn5180` and sends raw ISO 15693 / NFC-V frames for inventory and write-single-block operations.

Use this when the PN5180 module is wired directly to the Pi SPI bus plus NSS, BUSY, and RESET GPIO lines.

```bash
sudo systemctl enable --now pigpiod
export PN5180_NSS_PIN=8
export PN5180_BUSY_PIN=24
export PN5180_RESET_PIN=23
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


## Troubleshooting

### `No I2C device at address: 0x24`

This application is configured for a direct PN5180 module on the Raspberry Pi SPI bus. A `No I2C device at address: 0x24` startup error means the active Python NFC stack is trying to initialize an I2C peripheral instead of the expected PN5180 SPI path.

Check the following on the Raspberry Pi:

```bash
source /opt/ink-cloner/.venv/bin/activate
python -c "import pn5180pi; print(pn5180pi.__file__)"
sudo systemctl status pigpiod --no-pager
sudo raspi-config nonint get_spi
```

Then confirm the PN5180 is wired to SPI0 (MOSI GPIO 10, MISO GPIO 9, SCK GPIO 11, CE0/NSS GPIO 8 by default), both 3.3V logic and 5V RF power are connected, and `/etc/default/ink-cloner` uses the correct `PN5180_NSS_PIN`, `PN5180_BUSY_PIN`, and `PN5180_RESET_PIN` values for your board.

## Quick start
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python app.py
```
Open: `http://localhost:5000`

The web UI can still start on a development machine without PN5180 libraries installed; hardware actions will report that the reader is unavailable until the pn5180pi stack is installed and connected.

## Configuration
- `SECRET_KEY` (default `change-me-in-production`)
- `CORS_ALLOWED_ORIGINS` (default `*`)
- `PORT` (default `5000`)
- `PN5180_NSS_PIN` (default `8`, BCM numbering for direct PN5180 boards)
- `PN5180_BUSY_PIN` (default `24`, BCM numbering for direct PN5180 boards)
- `PN5180_RESET_PIN` (default `23`, BCM numbering for direct PN5180 boards)
- `TAG_DETECTION_TIMEOUT_SECONDS` (default `15`, minimum `1`)
- `TAG_DETECTION_POLL_SECONDS` (default `0.2`, minimum `0.05`)
- `ISO15693_BLOCK_SIZE` (default `4`, minimum `1`)
- `ENABLE_UID_BACKDOOR` (default `false`; set to `true` only when you are authorized to send the PN5180 magic UID backdoor command to compatible ISO 15693 media)

## Testing
```bash
python -m unittest discover -s tests
python -m py_compile app.py
```

## Safety
Use only on stickers/systems you own or are authorized to manage. Keep UID backdoor writes disabled unless the target tag and workflow explicitly require them and you are authorized to perform that operation.
