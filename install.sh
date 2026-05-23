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
apt-get install -y python3 python3-venv python3-pip git i2c-tools

echo "==> Enabling I2C interface"
if command -v raspi-config >/dev/null 2>&1; then
  raspi-config nonint do_i2c 0 || true
fi
if ! grep -q '^dtparam=i2c_arm=on' /boot/firmware/config.txt 2>/dev/null; then
  if [[ -f /boot/firmware/config.txt ]]; then
    echo 'dtparam=i2c_arm=on' >> /boot/firmware/config.txt
  elif [[ -f /boot/config.txt ]]; then
    echo 'dtparam=i2c_arm=on' >> /boot/config.txt
  fi
fi

usermod -aG i2c "$RUN_USER" || true

echo "==> Installing app into $INSTALL_DIR"
rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
cp -a . "$INSTALL_DIR"
chown -R "$RUN_USER:$RUN_GROUP" "$INSTALL_DIR"

sudo -u "$RUN_USER" python3 -m venv "$INSTALL_DIR/.venv"
sudo -u "$RUN_USER" "$INSTALL_DIR/.venv/bin/python" -m pip install --upgrade pip
sudo -u "$RUN_USER" "$INSTALL_DIR/.venv/bin/pip" install flask flask-socketio adafruit-blinka adafruit-circuitpython-pn532

echo "==> Writing environment file"
cat > /etc/default/ink-cloner <<EOF
SECRET_KEY=change-me-in-production
CORS_ALLOWED_ORIGINS=*
PORT=$PORT
TAG_DETECTION_TIMEOUT_SECONDS=10
TAG_DETECTION_POLL_SECONDS=0.2
WRITE_BLOCK_RESPONSE_LENGTH=10
EOF
chmod 640 /etc/default/ink-cloner

echo "==> Installing systemd service"
cat > /etc/systemd/system/$SERVICE_NAME <<EOF
[Unit]
Description=Ink Cloner Flask/SocketIO Service
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
  echo "==> Local health check"
  curl -fsS "http://127.0.0.1:${PORT}/" >/dev/null && echo "Health check passed"
fi

echo "\nInstall complete."
echo "Open: http://<device-ip>:$PORT"
echo "View logs: sudo journalctl -u $SERVICE_NAME -f"
echo "If remote clients cannot connect, run: sudo ss -ltnp | grep :$PORT"
echo "If I2C was newly enabled, reboot once: sudo reboot"
