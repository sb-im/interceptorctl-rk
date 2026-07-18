#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/home/orangepi/interceptorctl}"
SERVICE_NAME="interceptorctl.service"
SERVICE_SRC="${APP_DIR}/systemd/${SERVICE_NAME}"
SERVICE_DST="/etc/systemd/system/${SERVICE_NAME}"
OLD_SERVICES=(sbmcu.service sbdockctl3.service sbdockctl300.service)

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Please run as root, for example:"
  echo "  sudo ${APP_DIR}/tools/install_service.sh"
  exit 1
fi

if [[ ! -f "${SERVICE_SRC}" ]]; then
  echo "service file not found: ${SERVICE_SRC}" >&2
  exit 1
fi

install -m 0644 "${SERVICE_SRC}" "${SERVICE_DST}"

systemctl daemon-reload

for service in "${OLD_SERVICES[@]}"; do
  if systemctl list-unit-files "${service}" >/dev/null 2>&1; then
    systemctl disable --now "${service}" >/dev/null 2>&1 || true
  fi
done

if command -v tmux >/dev/null 2>&1; then
  if id orangepi >/dev/null 2>&1; then
    sudo -u orangepi tmux kill-session -t interceptorctl >/dev/null 2>&1 || true
  fi
  tmux kill-session -t interceptorctl >/dev/null 2>&1 || true
fi

rm -f /tmp/interceptorctl.sock

systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"
systemctl --no-pager --full status "${SERVICE_NAME}"
