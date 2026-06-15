#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/ink-cloner}"
SERVICE_NAME="${SERVICE_NAME:-ink-cloner.service}"
RUN_USER="${SUDO_USER:-${USER}}"
RUN_GROUP="$(id -gn "$RUN_USER")"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

info() { printf '\n==> %s\n' "$*"; }
warn() { printf '\nWARNING: %s\n' "$*" >&2; }
fail() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }
run_as_user() { sudo -u "$RUN_USER" "$@"; }

if [[ $EUID -ne 0 ]]; then
  exec sudo APP_DIR="$APP_DIR" SERVICE_NAME="$SERVICE_NAME" bash "$0" "$@"
fi

info "Ink Cloner Raspberry Pi PN5180 repair/diagnostic script"
printf 'Repo source: %s\n' "$REPO_DIR"
printf 'App target:  %s\n' "$APP_DIR"
printf 'Run user:    %s\n' "$RUN_USER"

info "Installing Raspberry Pi system packages for SPI/GPIO Python access"
apt-get update
apt-get install -y \
  build-essential \
  curl \
  git \
  pigpio \
  python3 \
  python3-dev \
  python3-pigpio \
  python3-pip \
  python3-rpi.gpio \
  python3-venv \
  rsync

info "Enabling SPI"
if command -v raspi-config >/dev/null 2>&1; then
  raspi-config nonint do_spi 0 || true
fi
for boot_config in /boot/firmware/config.txt /boot/config.txt; do
  if [[ -f "$boot_config" ]] && ! grep -q '^dtparam=spi=on' "$boot_config"; then
    echo 'dtparam=spi=on' >> "$boot_config"
  fi
done

info "Ensuring user has spi/gpio groups"
usermod -aG spi,gpio "$RUN_USER" || true
systemctl enable --now pigpiod || true

info "Deploying current checkout to $APP_DIR"
mkdir -p "$APP_DIR"
rsync -a --delete \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude '__pycache__' \
  "$REPO_DIR/" "$APP_DIR/"
chown -R "$RUN_USER:$RUN_GROUP" "$APP_DIR"

info "Creating/updating virtual environment and Python packages"
if [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then
  run_as_user python3 -m venv "$APP_DIR/.venv"
fi
run_as_user "$APP_DIR/.venv/bin/python" -m pip install --upgrade pip
run_as_user "$APP_DIR/.venv/bin/pip" install --upgrade -r "$APP_DIR/requirements.txt"
run_as_user "$APP_DIR/.venv/bin/pip" install --upgrade pigpio RPi.GPIO

if [[ ! -f /etc/default/ink-cloner ]]; then
  info "Creating default /etc/default/ink-cloner"
  cat > /etc/default/ink-cloner <<'ENVEOF'
SECRET_KEY=change-me-in-production
CORS_ALLOWED_ORIGINS=*
PORT=5000
TAG_DETECTION_TIMEOUT_SECONDS=10
TAG_DETECTION_POLL_SECONDS=0.2
PN5180_NSS_PIN=8
PN5180_BUSY_PIN=24
PN5180_RESET_PIN=23
PN5180_BACKEND=auto
PN5180_RESPONSE_TIMEOUT_SECONDS=0.25
ISO15693_BLOCK_SIZE=4
ENABLE_UID_BACKDOOR=false
ENVEOF
  chmod 640 /etc/default/ink-cloner
fi


info "Ensuring service backend defaults to auto"
if grep -q '^PN5180_BACKEND=' /etc/default/ink-cloner; then
  sed -i 's/^PN5180_BACKEND=.*/PN5180_BACKEND=auto/' /etc/default/ink-cloner
else
  echo 'PN5180_BACKEND=auto' >> /etc/default/ink-cloner
fi

if [[ ! -f /etc/systemd/system/$SERVICE_NAME ]]; then
  info "Creating $SERVICE_NAME"
  cat > "/etc/systemd/system/$SERVICE_NAME" <<SERVICEEOF
[Unit]
Description=Ink Cloner PN5180 Flask/SocketIO Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
Group=$RUN_GROUP
WorkingDirectory=$APP_DIR
EnvironmentFile=/etc/default/ink-cloner
ExecStart=$APP_DIR/.venv/bin/python $APP_DIR/app.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
SERVICEEOF
fi

info "Running hardware/dependency diagnostics"
printf 'SPI devices:\n'
if compgen -G '/dev/spidev*' >/dev/null; then
  ls -l /dev/spidev*
else
  warn 'No /dev/spidev* devices found. Reboot may be required after enabling SPI.'
fi

if command -v raspi-config >/dev/null 2>&1; then
  spi_state="$(raspi-config nonint get_spi || true)"
  printf 'raspi-config get_spi: %s (0 means enabled)\n' "$spi_state"
fi

printf 'Groups for %s: ' "$RUN_USER"
id -nG "$RUN_USER" || true

printf '\n/etc/default/ink-cloner:\n'
cat /etc/default/ink-cloner

if command -v pinctrl >/dev/null 2>&1; then
  printf '\nPin state snapshot:\n'
  for pin in 8 23 25 10 9 11 24; do
    pinctrl get "$pin" || true
  done
elif command -v raspi-gpio >/dev/null 2>&1; then
  printf '\nPin state snapshot:\n'
  for pin in 8 23 25 10 9 11 24; do
    raspi-gpio get "$pin" || true
  done
fi

info "Verifying Python imports in $APP_DIR/.venv"
run_as_user "$APP_DIR/.venv/bin/python" - <<'PY'
import importlib.util
for name in ("pigpio", "RPi.GPIO", "pn5180pi"):
    spec = importlib.util.find_spec(name)
    print(f"{name}: {spec.origin if spec else 'not installed'}")
import pigpio
import RPi.GPIO as GPIO
print("direct pigpio SPI imports OK")
PY

info "Restarting service and checking health"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"
sleep 2
systemctl --no-pager --full status "$SERVICE_NAME" || true
if command -v curl >/dev/null 2>&1; then
  curl -fsS http://127.0.0.1:5000/healthz || warn 'Health check failed; recent logs are printed below.'
fi

info "Recent service logs"
journalctl -u "$SERVICE_NAME" -n 80 --no-pager -l || true

info "Done"
echo "If you were just added to spi/gpio groups or SPI was newly enabled, reboot once: sudo reboot"
echo "Then open the UI and press Reconnect."
