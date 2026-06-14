#!/usr/bin/env bash
set -euo pipefail

APP_NAME="ink-cloner"
INSTALL_DIR="/opt/ink-cloner"
SERVICE_NAME="ink-cloner.service"
RUN_USER="${SUDO_USER:-$USER}"
RUN_GROUP="$(id -gn "$RUN_USER")"
PORT="${PORT:-5000}"

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root: sudo bash install.sh"
  exit 1
fi

echo "==> Installing system dependencies"
apt-get update
apt-get install -y python3 python3-venv python3-pip python3-dev git pigpio python3-pigpio python3-spidev python3-rpi.gpio build-essential rsync

echo "==> Enabling SPI interface"
if command -v raspi-config >/dev/null 2>&1; then
  raspi-config nonint do_spi 0 || true
fi
if ! grep -q '^dtparam=spi=on' /boot/firmware/config.txt 2>/dev/null; then
  if [[ -f /boot/firmware/config.txt ]]; then
    echo 'dtparam=spi=on' >> /boot/firmware/config.txt
  elif [[ -f /boot/config.txt ]]; then
    echo 'dtparam=spi=on' >> /boot/config.txt
  fi
fi

usermod -aG spi,gpio "$RUN_USER" || true
systemctl enable --now pigpiod || true

echo "==> Installing app into $INSTALL_DIR"
rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
cp -a . "$INSTALL_DIR"
chown -R "$RUN_USER:$RUN_GROUP" "$INSTALL_DIR"

sudo -u "$RUN_USER" python3 -m venv "$INSTALL_DIR/.venv"
sudo -u "$RUN_USER" "$INSTALL_DIR/.venv/bin/python" -m pip install --upgrade pip
sudo -u "$RUN_USER" "$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

echo "==> Writing environment file"
cat > /etc/default/ink-cloner <<EOF
SECRET_KEY=change-me-in-production
CORS_ALLOWED_ORIGINS=*
PORT=$PORT
TAG_DETECTION_TIMEOUT_SECONDS=10
TAG_DETECTION_POLL_SECONDS=0.2
PN5180_NSS_PIN=8
PN5180_BUSY_PIN=24
PN5180_RESET_PIN=23
PN5180_BACKEND=auto
PN5180_RESPONSE_TIMEOUT_SECONDS=0.25
ISO15693_BLOCK_SIZE=4
ENABLE_UID_BACKDOOR=false
EOF
chmod 640 /etc/default/ink-cloner

echo "==> Installing systemd service"
cat > /etc/systemd/system/$SERVICE_NAME <<EOF
[Unit]
Description=Ink Cloner PN5180 Flask/SocketIO Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
Group=$RUN_GROUP
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=/etc/default/ink-cloner
ExecStart=$INSTALL_DIR/.venv/bin/python $INSTALL_DIR/app.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

echo "==> Service status"
systemctl --no-pager --full status "$SERVICE_NAME" || true

if ! systemctl is-active --quiet "$SERVICE_NAME"; then
  echo "==> Service did not start cleanly. Recent logs:"
  journalctl -u "$SERVICE_NAME" -n 80 --no-pager || true
  exit 1
fi

if command -v curl >/dev/null 2>&1; then
  echo "==> Local health check (with startup retries)"
  ok=0
  for attempt in $(seq 1 20); do
    if ! systemctl is-active --quiet "$SERVICE_NAME"; then
      echo "Service became inactive during health check"
      break
    fi
    if curl -fsS "http://127.0.0.1:${PORT}/healthz" >/dev/null; then
      echo "Health check passed"
      ok=1
      break
    fi
    sleep 1
  done
  if [[ "$ok" -ne 1 ]]; then
    echo "==> Health check failed after retries. Recent logs:"
    journalctl -u "$SERVICE_NAME" -n 120 --no-pager -l || true
    exit 1
  fi
fi

echo "\nInstall complete."
echo "Open: http://<device-ip>:$PORT"
echo "View logs: sudo journalctl -u $SERVICE_NAME -f"
echo "If remote clients cannot connect, run: sudo ss -ltnp | grep :$PORT"
echo "If SPI was newly enabled, reboot once: sudo reboot"
