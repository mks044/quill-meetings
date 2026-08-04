#!/bin/bash
# Installs the dashboard on THIS machine (the always-on Linux server).
# Run it on the server, not the Mac. Idempotent.
set -euo pipefail
cd "$(dirname "$0")/../dashboard"

umask 077

echo "==> checking prerequisites"
missing=()
for c in python3 git rsync curl openssl; do command -v "$c" >/dev/null || missing+=("$c"); done
python3 -c 'import venv' 2>/dev/null || missing+=("python3-venv")
if [ ${#missing[@]} -gt 0 ]; then
  echo "missing: ${missing[*]}"
  echo "on Debian/Ubuntu:  sudo apt update && sudo apt install -y python3 python3-venv git rsync curl openssl"
  exit 1
fi
PYVER=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' \
  || { echo "need Python 3.10+ (found $PYVER)"; exit 1; }
systemctl --user show-environment >/dev/null 2>&1 \
  || { echo "no systemd user session (needed for the service). On a VPS: loginctl enable-linger $USER, then reconnect."; exit 1; }

echo "==> checking the Codex CLI (powers the AI notes)"
CODEX="$(command -v codex || true)"
if [ -z "$CODEX" ]; then
  cat <<'EOM'
codex CLI not found. Install and log in FIRST, then re-run this script:
    npm i -g @openai/codex        (needs Node; apt install -y nodejs npm)
    codex login --device-auth     (prints a URL + code to open on any device)
Verify with: codex login status
EOM
  exit 1
fi
codex login status >/dev/null 2>&1 || { echo "codex is installed but not logged in — run: codex login --device-auth"; exit 1; }
echo "    codex OK at $CODEX"

echo "==> writing .env (managed keys only; anything else you set is preserved)"
[ -f .env ] || cp .env.example .env
CODEX_BIN="$CODEX" QUILL_PASSWORD="${QUILL_PASSWORD:-}" python3 - <<'PY'
import os, pathlib, re, secrets
p = pathlib.Path(".env")
lines = p.read_text().splitlines() if p.exists() else []
def get(k):
    for l in lines:
        if l.startswith(k + "="): return l.split("=", 1)[1]
    return ""
managed = {"CODEX_BIN": os.environ["CODEX_BIN"]}
pw = os.environ.get("QUILL_PASSWORD", "")
if pw:
    managed["QUILL_PASSWORD"] = pw
    managed["QUILL_SECRET"] = get("QUILL_SECRET") or secrets.token_hex(24)
out, seen = [], set()
for l in lines:
    k = l.split("=", 1)[0] if "=" in l and not l.startswith("#") else None
    if k in managed:
        out.append(f"{k}={managed[k]}"); seen.add(k)
    else:
        out.append(l)
for k, v in managed.items():
    if k not in seen: out.append(f"{k}={v}")
p.write_text("\n".join(out) + "\n")
p.chmod(0o600)
print("    password gate: " + ("ENABLED" if pw else "off (private-network install)"))
PY

echo "==> enabling linger (service survives logout/reboot)"
loginctl enable-linger "$USER"
loginctl show-user "$USER" -p Linger --value | grep -qx yes \
  || { echo "linger did not enable — the dashboard would die on logout. Check: loginctl show-user $USER"; exit 1; }

echo "==> installing service"
bash deploy/deploy.sh

cat <<'EOM'

DONE — dashboard running, bound to 127.0.0.1:7435 (not exposed to any network).

Reach it one of these ways:
  · SSH tunnel (nothing to configure):
      ssh -N -L 7435:localhost:7435 user@this-server     # then http://localhost:7435
  · VPN/LAN: set QUILL_HOST to the VPN interface IP in dashboard/.env, re-run
    deploy, and firewall the port to that interface.
  · Internet: put TLS in front of it (Caddy example in SETUP.md), keep the bind
    on 127.0.0.1, and make sure QUILL_PASSWORD is set in dashboard/.env.

Next: on the Mac, run install/link.sh user@this-server
EOM
