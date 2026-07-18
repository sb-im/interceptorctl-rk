#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
exec python3 daemon.py \
  --serial "${INTERCEPTORCTL_SERIAL:-/dev/mcu}" \
  --socket "${INTERCEPTORCTL_SOCKET:-/tmp/interceptorctl.sock}"
