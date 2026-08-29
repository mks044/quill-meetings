#!/bin/bash
# Connects the Mac recorder to the dashboard server. Run on the MAC.
# Usage: bash install/link.sh user@your-server
set -euo pipefail
cd "$(dirname "$0")/.."
REMOTE="${1:-}"
[ -n "$REMOTE" ] || { echo "usage: bash install/link.sh user@your-server"; exit 1; }

echo "==> testing passwordless SSH to $REMOTE"
ssh -o BatchMode=yes -o ConnectTimeout=8 "$REMOTE" true 2>/dev/null || {
  cat <<EOM
Passwordless SSH to $REMOTE is not set up — the recorder can't upload without it.
Fix it (asks for the server password once), then re-run this script:
    ls ~/.ssh/id_ed25519.pub >/dev/null 2>&1 || ssh-keygen -t ed25519
    ssh-copy-id $REMOTE
EOM
  exit 1; }

# Read the server's real configuration — never guess paths or ports.
REMOTE_HOME=$(ssh "$REMOTE" 'echo $HOME')
REMOTE_ENV="$REMOTE_HOME/quill-meetings/dashboard/.env"
REMOTE_DATA=$(ssh "$REMOTE" "sed -n 's/^QUILL_DATA=//p' '$REMOTE_ENV' 2>/dev/null | tail -1")
REMOTE_DATA="${REMOTE_DATA:-$REMOTE_HOME/quill-data}"
REMOTE_PORT=$(ssh "$REMOTE" "sed -n 's/^QUILL_PORT=//p' '$REMOTE_ENV' 2>/dev/null | tail -1")
REMOTE_PORT="${REMOTE_PORT:-7435}"
REMOTE_DIR="$REMOTE_DATA/sessions"
ssh "$REMOTE" "mkdir -p '$REMOTE_DIR'"

echo "==> writing sync config"
mkdir -p ~/.config/quill-dash ~/.local/bin ~/.config/quill
cat > ~/.config/quill-dash/sync.conf <<EOF
REMOTE="$REMOTE"
REMOTE_DIR="$REMOTE_DIR"
DASH_URL="http://127.0.0.1:$REMOTE_PORT"
EOF

cp dashboard/mac/quill-sync ~/.local/bin/quill-sync
chmod +x ~/.local/bin/quill-sync

echo "==> installing periodic catch-up sync"
python3 install/sync_agent.py --program "$HOME/.local/bin/quill-sync"

echo "==> hooking it to the recorder"
python3 - <<'PY'
import json, os, pathlib
p = pathlib.Path.home() / ".config/quill/config.json"
cfg = json.loads(p.read_text()) if p.exists() else {}
cfg["on_stop"] = str(pathlib.Path.home() / ".local/bin/quill-sync")
p.write_text(json.dumps(cfg, indent=2))
print("   on_stop ->", cfg["on_stop"])
PY

echo "==> verifying the dashboard answers on the server"
ssh "$REMOTE" "curl -fsS --max-time 10 http://127.0.0.1:$REMOTE_PORT/api/health" >/dev/null \
  && echo "   dashboard OK" || echo "   WARNING: dashboard not answering on the server yet"

cat <<'EOM'

DONE — linked.

Test the whole chain now:
  1. Click the feather (or ~/Desktop/Record.command), say a few words, stop.
  2. Open the dashboard. The meeting appears within seconds; AI notes fill in
     a minute or two later.

Anything stuck? Run ~/.local/bin/quill-sync by hand — it re-uploads everything
not yet synced — and read ~/.local/state/quill-sync.log
EOM
