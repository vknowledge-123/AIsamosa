#!/usr/bin/env bash
set -euo pipefail

APP_USER="${APP_USER:-aisamosa}"
APP_DIR="${APP_DIR:-/opt/aisamosa}"
REPO_URL="${REPO_URL:-https://github.com/vknowledge-123/AIsamosa.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y \
  git \
  nginx \
  python3 \
  python3-venv \
  python3-pip \
  build-essential \
  curl

if ! id -u "${APP_USER}" >/dev/null 2>&1; then
  useradd --system --create-home --shell /bin/bash "${APP_USER}"
fi

mkdir -p "${APP_DIR}"
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"

if [ ! -d "${APP_DIR}/.git" ]; then
  sudo -u "${APP_USER}" git clone --branch "${REPO_BRANCH}" "${REPO_URL}" "${APP_DIR}"
else
  sudo -u "${APP_USER}" bash -lc "cd '${APP_DIR}' && git fetch origin && git checkout '${REPO_BRANCH}' && git pull --ff-only origin '${REPO_BRANCH}'"
fi

sudo -u "${APP_USER}" bash -lc "
cd '${APP_DIR}'
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
"

mkdir -p /etc/aisamosa
cp -n "${APP_DIR}/deploy/gcp/aisamosa.env.example" /etc/aisamosa/aisamosa.env || true

cp "${APP_DIR}/deploy/gcp/aisamosa.service" /etc/systemd/system/aisamosa.service
cp "${APP_DIR}/deploy/gcp/nginx-aisamosa.conf" /etc/nginx/sites-available/aisamosa
ln -sf /etc/nginx/sites-available/aisamosa /etc/nginx/sites-enabled/aisamosa
rm -f /etc/nginx/sites-enabled/default

systemctl daemon-reload
systemctl enable aisamosa
systemctl restart aisamosa
nginx -t
systemctl restart nginx

echo "Bootstrap complete."
echo "Edit /etc/aisamosa/aisamosa.env and restart the service if needed:"
echo "sudo systemctl restart aisamosa"
