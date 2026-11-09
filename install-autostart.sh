#!/bin/bash
# Starts the touchpad server automatically when you log in to your Mac,
# and restarts it if it ever stops. Undo with ./uninstall-autostart.sh
#
#   ./install-autostart.sh           install and start now
#   ./install-autostart.sh --print   only print the LaunchAgent plist
set -euo pipefail

LABEL="com.iphone-touchpad"
DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/iphone-touchpad.log"
PORT="${TOUCHPAD_PORT:-8899}"

if [ "$(uname)" != "Darwin" ]; then
  echo "This only works on macOS." >&2
  exit 1
fi

# macOS blocks background programs from reading Downloads, Desktop and Documents.
case "$DIR" in
  "$HOME/Downloads"* | "$HOME/Desktop"* | "$HOME/Documents"*)
    echo "This folder is protected by macOS, a background server could not read it:" >&2
    echo "  $DIR" >&2
    echo "Move it first, for example:  mv \"$DIR\" \"$HOME/iphone-touchpad\"" >&2
    echo "then run ./install-autostart.sh from the new place." >&2
    exit 1
    ;;
esac

PY="${PYTHON:-$(command -v python3 || true)}"
if [ -z "$PY" ]; then
  echo "python3 was not found. Install the Xcode command line tools:  xcode-select --install" >&2
  exit 1
fi
# The real executable is what macOS asks you to allow in Accessibility.
PY="$("$PY" -c 'import os, sys; print(os.path.realpath(sys.executable))')"
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
  echo "Python 3.9 or newer is required (found: $("$PY" --version 2>&1))." >&2
  exit 1
fi

plist() {
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PY</string>
        <string>$DIR/server.py</string>
        <string>--strict</string>
        <string>--prompt</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$DIR</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONUNBUFFERED</key>
        <string>1</string>
        <key>TOUCHPAD_PORT</key>
        <string>$PORT</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>10</integer>
    <key>StandardOutPath</key>
    <string>$LOG</string>
    <key>StandardErrorPath</key>
    <string>$LOG</string>
</dict>
</plist>
EOF
}

if [ "${1:-}" = "--print" ]; then
  plist
  exit 0
fi

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1 \
   && ! launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
  echo "Note: port $PORT is already in use (another copy of the server?)." >&2
  echo "Stop it first, otherwise the autostart copy cannot take the port." >&2
fi

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
plist > "$PLIST"
plutil -lint "$PLIST" >/dev/null

launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

for _ in 1 2 3 4 5 6 7 8 9 10; do
  [ -s "$DIR/.pin" ] && break
  sleep 0.5
done

echo
echo "Installed. The server now starts when you log in."
echo "  Python:  $PY"
echo "  Log:     $LOG"
[ -s "$DIR/.pin" ] && echo "  PIN:     $(cat "$DIR/.pin")"
echo
echo "One last step: macOS must allow this Python to control the cursor."
echo "  System Settings > Privacy & Security > Accessibility > switch on \"Python\""
echo "  (if it is not in the list, press + and pick: $PY)"
echo "Then restart the server so the permission applies:"
echo "  launchctl kickstart -k gui/$(id -u)/$LABEL"
open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility" >/dev/null 2>&1 || true
