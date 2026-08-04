#!/bin/bash
# Installs the recorder on this Mac: dependencies, build, app bundle.
# Idempotent — safe to re-run. Requires macOS 15+.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> checking macOS version"
ver=$(sw_vers -productVersion | cut -d. -f1)
[ "$ver" -ge 15 ] || { echo "needs macOS 15+ (found $ver)"; exit 1; }

echo "==> checking build prerequisites"
xcode-select -p >/dev/null 2>&1 || {
  echo "Xcode Command Line Tools missing. Run:  xcode-select --install"
  echo "(a GUI installer opens; re-run this script when it finishes)"; exit 1; }
command -v swift >/dev/null || { echo "swift not found — install Xcode or the Command Line Tools"; exit 1; }
SWIFTV=$(swift --version 2>/dev/null | grep -oE 'Swift version [0-9]+' | grep -oE '[0-9]+$' || echo 0)
[ "${SWIFTV:-0}" -ge 6 ] || { echo "need Swift 6+ (found ${SWIFTV:-unknown}) — update Xcode/CLT"; exit 1; }

echo "==> installing dependencies (whisper-cpp, ffmpeg)"
command -v brew >/dev/null || {
  echo "Homebrew missing. Install it, then re-run:"
  echo '  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
  exit 1; }
brew list whisper-cpp >/dev/null 2>&1 || brew install whisper-cpp
brew list ffmpeg      >/dev/null 2>&1 || brew install ffmpeg

echo "==> building the recorder (Swift, ~2 min first time)"
cd recorder && swift build -c release

echo "==> installing ~/Applications/quill.app"
bash docs/make-app.sh

cat <<'EOM'

DONE — recorder installed.

Steps only you can do (macOS requires real clicks):
  1. Launch it:            open ~/Applications/quill.app
  2. Start a short recording from the feather icon in the menu bar, say a word,
     stop it. macOS shows the Microphone and System Audio prompts on this FIRST
     recording — approve both. (The app only appears in the Privacy panes once
     it has asked, which is why recording once matters.)
  3. If you clicked "Don't Allow" on either: System Settings -> Privacy &
     Security -> Microphone / Screen & System Audio Recording -> enable "quill",
     then quit and relaunch the app.

Verify with:  ~/Applications/quill.app/Contents/MacOS/quill doctor
If the menu bar is full and the icon is hidden, use the fallback button:
     printf '#!/bin/zsh\nkill -USR1 $(pgrep -x quill)\n' > ~/Desktop/Record.command
     chmod +x ~/Desktop/Record.command

Next: install the dashboard on your server (install/server.sh), then run
install/link.sh here to connect them.
EOM
