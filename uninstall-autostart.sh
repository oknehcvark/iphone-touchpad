#!/bin/bash
# Removes the autostart installed by ./install-autostart.sh
set -u
LABEL="com.iphone-touchpad"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
rm -f "$PLIST"
echo "Autostart removed. The server will no longer start at login."
echo "You can still run it by hand:  python3 server.py"
