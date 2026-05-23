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

## One-command install (recommended)

For Raspberry Pi / Debian-based systems, use the installer script to set everything up automatically:

```bash
git clone https://github.com/SethMorrowSoftware/Ink-Cloner.git
cd Ink-Cloner
sudo bash install.sh
```

What it does:

- Installs OS dependencies (`python3-venv`, `pip`, `git`, `i2c-tools`)
- Enables I2C (via `raspi-config` when available + `dtparam=i2c_arm=on`)
- Adds your user to the `i2c` group
- Installs the app to `/opt/ink-cloner`
- Creates a virtualenv and installs Python dependencies
- Creates `/etc/default/ink-cloner` for runtime configuration
- Creates and enables a systemd service: `ink-cloner.service`

Useful commands after install:

```bash
sudo systemctl status ink-cloner
sudo systemctl restart ink-cloner
sudo journalctl -u ink-cloner -f
```

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

### `i2cdetect` says `/dev/i2c-*` does not exist

If you get errors like:

```bash
Error: Could not open file `/dev/i2c-1` ... No such file or directory
```

it means the Linux I2C device interface is not currently available.

Run these checks in order:

1. Confirm the kernel modules are loaded:

```bash
sudo modprobe i2c-dev
automod=$(lsmod | awk '/i2c_dev|i2c_bcm2708|i2c_bcm2835/{print $1}')
echo "$automod"
```

2. Ensure I2C is enabled in boot config:

```bash
grep -E '^dtparam=i2c_arm=on' /boot/config.txt /boot/firmware/config.txt 2>/dev/null
```

If not present, add this line to the active config file:

```text
dtparam=i2c_arm=on
```

Then reboot.

3. Re-enable via raspi-config (if on Raspberry Pi OS):

```bash
sudo raspi-config
# Interface Options -> I2C -> Enable
sudo reboot
```

4. Verify device nodes after reboot:

```bash
ls -l /dev/i2c*
```

You should see at least `/dev/i2c-1` on most Raspberry Pi models.

5. Install tools if needed and scan all available buses:

```bash
sudo apt-get install -y i2c-tools
sudo i2cdetect -l
```

Use the bus number shown by `i2cdetect -l` (not always `1` on non-Pi systems):

```bash
sudo i2cdetect -y <BUS_NUMBER>
```

6. If still missing, check hardware/platform support:

- On some images/kernels, I2C is disabled by default and requires device-tree overlay changes.
- In containers/VMs, `/dev/i2c-*` may not be passed through.
- On BeagleBone/Jetson/other SBCs, bus numbering and pinmux setup are different.

### SSH warning: `known_hosts` Permission denied

If you see:

```text
hostkeys_find_by_key_hostfile ... /home/<user>/.ssh/known_hosts: Permission denied
Failed to add the host to the list of known hosts
```

fix ownership/permissions on your **local client** machine:

```bash
mkdir -p ~/.ssh
sudo chown -R "$USER":"$USER" ~/.ssh
chmod 700 ~/.ssh
touch ~/.ssh/known_hosts
chmod 600 ~/.ssh/known_hosts
```

Then reconnect:

```bash
ssh tech@192.168.0.79
```

### `venv`/`pip` permission denied after using `sudo`

Your logs show this exact root cause:

- `sudo git clone ...` created a **root-owned repo**.
- `sudo python3 -m venv .venv` created a **root-owned virtualenv**.
- Running `pip` as normal user inside that venv then fails with permission denied.

#### Fastest clean fix

```bash
cd ~
sudo rm -rf ~/Ink-Cloner
git clone https://github.com/SethMorrowSoftware/Ink-Cloner.git
cd Ink-Cloner
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install flask flask-socketio adafruit-blinka adafruit-circuitpython-pn532
```

#### Alternative (repair existing folder)

```bash
cd ~
sudo chown -R tech:tech Ink-Cloner
cd Ink-Cloner
rm -rf .venv
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install flask flask-socketio adafruit-blinka adafruit-circuitpython-pn532
```

#### Important runtime rule

Do **not** run the app with `sudo python app.py` when using a user venv.
Run it as:

```bash
source .venv/bin/activate
python app.py
```

If I2C permissions block access later, add your user to the `i2c` group and re-login:

```bash
sudo usermod -aG i2c tech
# log out and back in
```

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
