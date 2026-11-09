#!/bin/bash
# Removes the menu bar icon installed by ./install-menubar.sh (the server keeps working).
set -u
LABEL="com.iphone-touchpad.menubar"

launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
rm -rf "$HOME/Applications/Touchpad.app"
echo "Menu bar icon removed. The touchpad server is not affected."
