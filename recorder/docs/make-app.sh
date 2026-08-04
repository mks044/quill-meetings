#!/bin/bash
# Build quill and install it as ~/Applications/quill.app — the shape macOS needs
# to attribute mic + system-audio permissions and to register as a login item.
set -euo pipefail
cd "$(dirname "$0")/.."
swift build -c release
APP="$HOME/Applications/quill.app"
mkdir -p "$APP/Contents/MacOS"
cp .build/release/quill "$APP/Contents/MacOS/quill"
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>com.digimata.quill</string>
<key>CFBundleName</key><string>quill</string>
<key>CFBundleExecutable</key><string>quill</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>1.0</string>
<key>LSUIElement</key><true/>
<key>NSMicrophoneUsageDescription</key><string>quill records your microphone during meetings so you can transcribe them later. Audio never leaves this Mac.</string>
<key>NSAudioCaptureUsageDescription</key><string>quill records system audio (the other side of your meetings) so you can transcribe them later. Audio never leaves this Mac.</string>
</dict></plist>
PLIST
echo "installed → $APP"
echo "launch it once from Finder/Spotlight so macOS draws the menu-bar item."
