# Photobooth Ink Cloner

A Flask + Socket.IO web utility for writing predefined data blocks to NFC tags using an **Adafruit PN532** reader over **I2C**.

> ⚠️ **Important legal and ethical notice**
>
> This project can alter NFC tags at a low level. Only use it with tags and systems you own or are explicitly authorized to manage. Misuse may violate laws, service terms, or device warranties.

---

## Table of Contents

- [What this project does](#what-this-project-does)
- [System requirements](#system-requirements)
- [Hardware needed](#hardware-needed)
- [PN532 wiring (I2C)](#pn532-wiring-i2c)
- [Software installation](#software-installation)
- [Configuration](#configuration)
- [Run the app](#run-the-app)
- [How to use the web UI](#how-to-use-the-web-ui)
- [Troubleshooting](#troubleshooting)
- [Security and deployment notes](#security-and-deployment-notes)
- [Project structure](#project-structure)

---

## What this project does

When you click **Burn New Roll Tag** in the web UI, the app:

1. Waits for a tag to be presented to the PN532 reader.
2. Writes a fixed list of data blocks (`CLEARED_DATA_BLOCKS`) to the detected tag.
3. Attempts a final UID-related command payload using `TARGET_UID`.
4. Streams live progress logs to the browser via Socket.IO.

The backend is in `app.py` and the UI template is `templates/index.html`.

---

## System requirements

- Python **3.9+** (3.10+ recommended)
- Linux environment with I2C support (Raspberry Pi OS recommended)
- Network access to open the web UI from a browser
- PN532 hardware connected to your host (see wiring below)

Python packages used by this project:

- `flask`
- `flask-socketio`
- `adafruit-blinka`
- `adafruit-circuitpython-pn532`

---

## Hardware needed

- 1x PN532 NFC module (Adafruit or compatible)
- 4 jumper wires (female-female or female-male depending on board)
- Host board/computer with I2C pins (for example Raspberry Pi)

---

## PN532 wiring (I2C)

### 1) Put PN532 into I2C mode

Most PN532 breakouts have mode-select DIP switches or solder jumpers (often labeled `SEL0/SEL1` or similar).

- Set the board to **I2C mode** according to your PN532 board documentation.
- If you're using an Adafruit PN532 breakout, follow Adafruit's I2C mode selection guidance for that exact board revision.

### 2) Connect power and I2C lines

Typical Raspberry Pi ↔ PN532 I2C mapping:

- **Pi 3V3 (pin 1)** → **PN532 VIN** (or 3.3V input, depending on board)
- **Pi GND (pin 6)** → **PN532 GND**
- **Pi SDA (GPIO2 / pin 3)** → **PN532 SDA**
- **Pi SCL (GPIO3 / pin 5)** → **PN532 SCL**

> Notes:
>
> - Some PN532 boards accept 5V on VIN and level-shift internally; others require 3.3V logic only. Verify your board's documentation before powering.
> - Keep wires short and connections firm to reduce I2C noise.

### 3) Enable I2C on the host

On Raspberry Pi:

```bash
sudo raspi-config
```

Then:

- `Interface Options` → `I2C` → `Enable`
- Reboot:

```bash
sudo reboot
```

### 4) Verify the PN532 is visible on I2C bus

Install I2C tools:

```bash
sudo apt-get update
sudo apt-get install -y i2c-tools
```

Scan bus (typically bus `1` on Raspberry Pi):

```bash
i2cdetect -y 1
```

You should see the PN532 address (commonly `0x24` for PN532 in I2C mode).

If you do not see it, re-check:

- Mode switch (must be I2C)
- SDA/SCL swapped or loose
- Power/GND wiring
- I2C enabled in OS

---

## Software installation

From the project directory:

### 1) Create a virtual environment

```bash
python3 -m venv .venv
```

### 2) Activate it

```bash
source .venv/bin/activate
```

### 3) Install dependencies

```bash
pip install --upgrade pip
pip install flask flask-socketio adafruit-blinka adafruit-circuitpython-pn532
```

(Optional) Validate syntax:

```bash
python -m py_compile app.py
```

---

## Configuration

The app supports environment-variable configuration:

- `SECRET_KEY` (default: `change-me-in-production`)
- `CORS_ALLOWED_ORIGINS` (default: `*`)
- `PORT` (default: `5000`)
- `TAG_DETECTION_TIMEOUT_SECONDS` (default: `10`)
- `TAG_DETECTION_POLL_SECONDS` (default: `0.2`)
- `WRITE_BLOCK_RESPONSE_LENGTH` (default: `10`)

Example:

```bash
export SECRET_KEY='replace-with-long-random-string'
export CORS_ALLOWED_ORIGINS='http://localhost:5000'
export PORT=5000
export TAG_DETECTION_TIMEOUT_SECONDS=12
export TAG_DETECTION_POLL_SECONDS=0.2
export WRITE_BLOCK_RESPONSE_LENGTH=10
```

---

## Run the app

From the project root:

```bash
source .venv/bin/activate
python app.py
```

Expected startup behavior:

- Flask-SocketIO server starts on `0.0.0.0:$PORT`
- Hardware initialization runs at startup
- UI shows PN532 status badge

Open in browser:

- Local machine: `http://localhost:5000`
- Remote/lan host: `http://<HOST_IP>:5000`

---

## How to use the web UI

1. Confirm **Hardware Status** shows PN532 active.
2. Place the compatible NFC tag on/near the PN532 antenna.
3. Click **Burn New Roll Tag**.
4. Watch live logs in the console panel.
5. Wait for `SUCCESS` or failure/warning messages.

Concurrency behavior:

- If a burn is already running, additional clicks return a `busy` state and the UI shows **Another Burn Running** briefly.

---

## Troubleshooting

### UI shows hardware error

- Check wiring and I2C mode selection.
- Confirm I2C is enabled in OS.
- Confirm the process has permission to access I2C.
- Run `i2cdetect -y 1` and verify PN532 appears.

### Timeout: no tag detected

- Reposition the tag on the antenna.
- Try a slower/more stable presentation (hold still).
- Increase detection timeout:

```bash
export TAG_DETECTION_TIMEOUT_SECONDS=20
```

### Intermittent reader poll or write warnings

- Improve wire quality/length.
- Ensure stable power supply.
- Reduce electrical noise near the reader.
- Verify you are using a tag type compatible with your workflow.

### Browser cannot connect

- Confirm firewall rules allow the chosen `PORT`.
- Confirm you are visiting the correct host IP.
- If behind proxy, ensure WebSocket support is enabled.

---

## Security and deployment notes

- Do **not** use default `SECRET_KEY` in production.
- Restrict `CORS_ALLOWED_ORIGINS` to trusted origins.
- Place this service on a trusted network segment.
- Add authentication if exposed beyond a private LAN.
- Log and audit usage if operating in shared environments.

---

## Project structure

```text
Ink-Cloner/
├── app.py
├── templates/
│   └── index.html
└── README.md
```

---

## Maintainer tips

- Core burn payload values are currently hard-coded in `app.py` (`TARGET_UID`, `CLEARED_DATA_BLOCKS`).
- If you need profile-based behavior, consider externalizing payloads into JSON/YAML configuration with validation.
- For long-term reliability, add automated tests by mocking PN532 interactions.
