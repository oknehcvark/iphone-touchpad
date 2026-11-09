#!/bin/bash
# Builds the menu bar icon (next to the battery) and starts it at login.
# It shows if a phone is connected, copies the PIN and address, and opens the QR page.
# Needs the Xcode command line tools (swiftc).  Undo with ./uninstall-menubar.sh
set -euo pipefail

LABEL="com.iphone-touchpad.menubar"
DIR="$(cd "$(dirname "$0")" && pwd)"
APP="$HOME/Applications/Touchpad.app"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PORT="${TOUCHPAD_PORT:-8899}"

if [ "$(uname)" != "Darwin" ]; then
  echo "This only works on macOS." >&2
  exit 1
fi
if ! command -v swiftc >/dev/null 2>&1; then
  echo "swiftc was not found. Install the Xcode command line tools:  xcode-select --install" >&2
  exit 1
fi

BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT
mkdir -p "$BUILD/Touchpad.app/Contents/MacOS" "$BUILD/Touchpad.app/Contents/Resources"

echo "Building..."
swiftc -O "$DIR/menubar/Touchpad.swift" -o "$BUILD/Touchpad.app/Contents/MacOS/Touchpad"

# App icon (.icns) from the rounded icon
ICONSET="$BUILD/AppIcon.iconset"
mkdir "$ICONSET"
SRC="$DIR/assets/icon-rounded-512.png"
for spec in "16 icon_16x16" "32 icon_16x16@2x" "32 icon_32x32" "64 icon_32x32@2x" \
            "128 icon_128x128" "256 icon_128x128@2x" "256 icon_256x256" "512 icon_256x256@2x" "512 icon_512x512"; do
  set -- $spec
  sips -z "$1" "$1" "$SRC" --out "$ICONSET/$2.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$BUILD/Touchpad.app/Contents/Resources/AppIcon.icns"

cat > "$BUILD/Touchpad.app/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleIdentifier</key><string>$LABEL</string>
    <key>CFBundleName</key><string>Touchpad</string>
    <key>CFBundleDisplayName</key><string>Touchpad</string>
    <key>CFBundleExecutable</key><string>Touchpad</string>
    <key>CFBundleIconFile</key><string>AppIcon</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>CFBundleVersion</key><string>1</string>
    <key>LSMinimumSystemVersion</key><string>11.0</string>
    <key>LSUIElement</key><true/>
</dict>
</plist>
EOF
codesign --force --sign - "$BUILD/Touchpad.app" >/dev/null 2>&1

launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
mkdir -p "$HOME/Applications" "$HOME/Library/LaunchAgents"
rm -rf "$APP"
cp -R "$BUILD/Touchpad.app" "$APP"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$APP/Contents/MacOS/Touchpad</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>TOUCHPAD_PORT</key>
        <string>$PORT</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>ProcessType</key>
    <string>Interactive</string>
</dict>
</plist>
EOF
plutil -lint "$PLIST" >/dev/null
launchctl bootstrap "gui/$(id -u)" "$PLIST"

echo
echo "Done. The icon is in the menu bar, next to the battery, and starts at every login."
echo "  App:  $APP"
echo "If you do not see it, the menu bar may be full: close a few other icons."
