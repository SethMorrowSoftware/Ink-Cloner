# Photobooth Ink Cloner

Professional Flask + Socket.IO **PN5180** console with **ink cloning as the primary workflow** and a focused operator UI for authorized ISO 15693 / NFC-V sticker management.

## Core focus
- Guided **Ink Clone Burn** workflow with step-by-step console logs and completion status.
- PN5180-first NFC-V UID scan and ISO 15693 block writes.
- **PN5180 Self-Test** that reads the chip's firmware/product/EEPROM identity so you can tell a wiring/SPI problem apart from an RF/sticker problem.
- Single-slot ISO 15693 inventory for reliable detection of one sticker on the antenna, with a 16-slot anticollision fallback.
- Reader reconnect controls and live hardware status.
- Operation history/audit export at `/history.json`.
- Health check endpoint at `/healthz` (includes the latest self-test identity).
- Safer default behavior: UID backdoor writes are disabled unless explicitly enabled with `ENABLE_UID_BACKDOOR=true`.

## PN5180 setup for Raspberry Pi Zero / Zero W / Zero 2 W on Bookworm Lite

The app defaults to `PN5180_BACKEND=direct-spi` so the Pi talks to a **PN5180 SPI module directly** through `pigpio`. This avoids accidentally selecting unrelated PN532/I2C-style Python stacks, which is a common reason the app starts but never sees ISO 15693 / NFC-V stickers.

Use this when the PN5180 module is wired directly to the Pi SPI0 bus plus NSS, BUSY, and RESET GPIO lines.

```bash
sudo apt update
sudo apt install -y pigpio python3-pigpio python3-venv
sudo raspi-config nonint do_spi 0
sudo systemctl enable --now pigpiod
export PN5180_NSS_PIN=8
export PN5180_BUSY_PIN=24
export PN5180_RESET_PIN=23
export PN5180_BACKEND=direct-spi
python app.py
```

### Wiring diagram using regular Raspberry Pi physical pin numbers

The environment variables still use **BCM GPIO numbers** because `pigpio` addresses pins that way. The wiring table below gives the normal Raspberry Pi header pin numbers first, which are the pin numbers printed in most Pi Zero wiring diagrams.

| PN5180 module pin | Raspberry Pi physical header pin | BCM GPIO / Pi function | App setting |
| --- | ---: | --- | --- |
| 5V / VCC | Pin 2 or 4 | 5V power for RF field | — |
| 3.3V / VCC_IO | Pin 1 or 17 | 3.3V logic power | — |
| GND | Pin 6, 9, 14, 20, 25, 30, 34, or 39 | Ground | — |
| NSS / SSEL / CS | Pin 24 | GPIO 8 / SPI0 CE0 | `PN5180_NSS_PIN=8` |
| BUSY | Pin 18 | GPIO 24 | `PN5180_BUSY_PIN=24` |
| RST / RESET | Pin 16 | GPIO 23 | `PN5180_RESET_PIN=23` |
| MOSI | Pin 19 | GPIO 10 / SPI0 MOSI | fixed SPI0 |
| MISO | Pin 21 | GPIO 9 / SPI0 MISO | fixed SPI0 |
| SCK / CLK | Pin 23 | GPIO 11 / SPI0 SCLK | fixed SPI0 |

> Important: many PN5180 boards require both 3.3V and 5V connected. SPI can appear alive with only 3.3V, but the antenna/RF field will not reliably power NFC-V stickers without 5V.

> Pi Zero note: the 40-pin header pinout is the same across Raspberry Pi Zero models. Bookworm Lite works with this project when SPI is enabled, `pigpiod` is running, and the service user is in the `spi` and `gpio` groups. Reboot once after enabling SPI or changing groups.

### Sticker detection checklist

If the web UI says the PN5180 is connected but scans time out:

1. **Press Self-Test first.** The badge shows "Online" as soon as `pigpiod` opens the SPI bus, *before* the chip has answered anything, so it is not proof the PN5180 is wired correctly. Self-Test reads the chip's firmware/product/EEPROM identity:
   - If Self-Test reports a real firmware version, SPI comms work and the problem is on the **RF side** (continue with steps 2–3).
   - If Self-Test reports an empty identity, SPI comms are **not** working: recheck NSS/BUSY/RESET/MOSI/MISO/SCK wiring, that SPI is enabled, `pigpiod` is running, and that 3.3V logic power is present.
2. Confirm the sticker is an **ISO 15693 / NFC-V** sticker. MIFARE/ISO 14443 stickers are a different protocol and will not respond to this workflow.
3. Put only one sticker on the antenna and hold it flat in the center of the PN5180 coil.
4. Confirm 5V is connected to the PN5180 RF power pin and 3.3V is connected to the logic/IO pin when your module exposes both. SPI/Self-Test can pass on 3.3V alone, but the RF field needs 5V to power the sticker.
5. Confirm SPI devices exist after reboot: `ls -l /dev/spidev0.*`.
6. Force the known-good path in `/etc/default/ink-cloner`: `PN5180_BACKEND=direct-spi`.


## Troubleshooting

### `pip install` fails on `pn5180pi` (e.g. piwheels TLS/SSL errors)

`pn5180pi` is **optional** and is only used by the non-default `PN5180_BACKEND=pn5180pi`
backend. The default `direct-spi` backend talks to the PN5180 through `pigpio` and never
imports `pn5180pi`, so a failure fetching it must not block the app. The core
`requirements.txt` no longer lists it, and `install.sh` / `repair_pi_hardware.sh` install
it best-effort and continue on failure.

If you installed before this change (or pinned the old `requirements.txt`) and the install
aborts on `pn5180pi`, install just the core dependencies:

```bash
/opt/ink-cloner/.venv/bin/pip install -r requirements.txt
sudo systemctl restart ink-cloner.service
```

To use the optional library backend later, install it separately once your network/mirror
is healthy:

```bash
/opt/ink-cloner/.venv/bin/pip install -r requirements-pn5180pi.txt
```

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

## Raspberry Pi hardware repair helper

If the service still reports missing PN5180/SPI dependencies after installation, run the repair script from the repo checkout on the Pi:

```bash
cd ~/Ink-Cloner
git pull
sudo bash scripts/repair_pi_hardware.sh
```

The script installs SPI/GPIO system packages, refreshes `/opt/ink-cloner`, installs the Python requirements into the service virtual environment, verifies `pigpio` imports, prints SPI/GPIO diagnostics, restarts the service, and shows recent logs.

## Quick start
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python app.py
```
Open: `http://localhost:5000`

The web UI can still start on a development machine without PN5180 libraries installed; hardware actions will report that the reader is unavailable until either a compatible `pn5180pi` stack or the direct SPI dependencies (`pigpio` and `pigpiod`) are installed and the reader is connected.

## Configuration
- `SECRET_KEY` (default `change-me-in-production`)
- `CORS_ALLOWED_ORIGINS` (default `*`)
- `PORT` (default `5000`)
- `PN5180_BACKEND` (default `direct-spi`; use `auto` or `pn5180pi` only if you intentionally want the optional library wrapper)
- `PN5180_NSS_PIN` (default `8`, BCM numbering for direct PN5180 boards)
- `PN5180_BUSY_PIN` (default `24`, BCM numbering for direct PN5180 boards)
  - The pyPN5180 Raspberry Pi examples use GPIO 25 as BUSY; set `PN5180_BUSY_PIN=25` if you followed that wiring.
- `PN5180_RESET_PIN` (default `23`, BCM numbering for direct PN5180 boards)
  - Some pn5180pi examples wire RESET to GPIO 25; set `PN5180_RESET_PIN=25` in `/etc/default/ink-cloner` if your board is wired that way.
- `PN5180_RESPONSE_TIMEOUT_SECONDS` (default `0.25`, minimum `0.01`, direct SPI response wait time)
- `PN5180_BUSY_TIMEOUT_SECONDS` (default `1.0`, minimum `0.05`) — how long to wait for the PN5180 BUSY line before giving up. Prevents a stuck/floating BUSY line (unpowered or mis-wired board) from hanging startup or a scan.
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
