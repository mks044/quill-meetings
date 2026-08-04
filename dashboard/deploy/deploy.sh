#!/bin/bash
# Deploy/update the dashboard on the machine hosting it.
#   bash dashboard/deploy/deploy.sh
# Idempotent: pulls (if this is a git checkout), syncs deps, writes the systemd
# unit with real absolute paths, restarts, and health-checks.
set -euo pipefail

DASH_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DASH_DIR"

if git -C "$DASH_DIR" rev-parse --show-toplevel >/dev/null 2>&1; then
  ROOT="$(git -C "$DASH_DIR" rev-parse --show-toplevel)"
  git -C "$ROOT" pull --ff-only || echo "(pull skipped)"
fi

[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt

# Single source of truth: .env values win, shell env overrides, then defaults.
env_get() { [ -f .env ] && sed -n "s/^$1=//p" .env | tail -1; }
PORT="${QUILL_PORT:-$(env_get QUILL_PORT)}"; PORT="${PORT:-7435}"
HOST_BIND="${QUILL_HOST:-$(env_get QUILL_HOST)}"; HOST_BIND="${HOST_BIND:-127.0.0.1}"
DATA_DIR="${QUILL_DATA:-$(env_get QUILL_DATA)}"; DATA_DIR="${DATA_DIR:-$HOME/quill-data}"

umask 077
mkdir -p "$DATA_DIR/sessions" "$HOME/.config/systemd/user"
[ -f .env ] && chmod 600 .env
chmod -R go-rwx "$DATA_DIR" 2>/dev/null || true

# Unit is generated, not copied: paths must be absolute and correct wherever
# this repo happens to live.
sed -e "s|__DASH_DIR__|$DASH_DIR|g" \
    -e "s|__PORT__|$PORT|g" \
    -e "s|__BIND__|$HOST_BIND|g" \
    -e "s|__HOME__|$HOME|g" \
    "$DASH_DIR/deploy/quill-dash.service.tmpl" > "$HOME/.config/systemd/user/quill-dash.service"

systemctl --user daemon-reload
systemctl --user enable --now quill-dash >/dev/null 2>&1 || true
systemctl --user restart quill-dash
sleep 2

if curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null; then
  echo "deploy OK @ $(git -C "$DASH_DIR" rev-parse --short HEAD 2>/dev/null || echo 'no-git')"
else
  echo "HEALTH CHECK FAILED"
  systemctl --user status quill-dash --no-pager | tail -15
  exit 1
fi
