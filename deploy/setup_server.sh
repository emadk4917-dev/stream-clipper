#!/usr/bin/env bash
# One-time server setup (Ubuntu 24.04). Run as root:  bash deploy/setup_server.sh
set -euo pipefail
cd "$(dirname "$0")/.."
APP_DIR="$(pwd)"
apt-get update
apt-get install -y ffmpeg python3-venv python3-pip git fonts-inter
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -r requirements.txt
[ -f .env ] || cp .env.example .env
for svc in realtime live bot trends; do
  sed "s#__APP_DIR__#${APP_DIR}#g" "deploy/clipper-${svc}.service" > "/etc/systemd/system/clipper-${svc}.service"
done
systemctl daemon-reload
echo
echo "✅ Installed. Now let's add your keys:"
bash "${APP_DIR}/deploy/configure.sh"
