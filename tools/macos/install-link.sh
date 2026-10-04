#!/bin/bash
# macOS: open the terminal from a browser bookmark.
#   ./tools/macos/install-link.sh              install ~/Applications/Investment Monitor.app
#   ./tools/macos/install-link.sh --uninstall  remove it
# Then bookmark  monitor://open  (starts the terminal if it is not running, then opens it) and, if you like,
# monitor://stop. The browser asks once before opening the app. The app runs tools/macos/monitor-link.sh
# of THIS folder: install again if you move the folder.
set -eu
[ "$(uname)" = Darwin ] || { echo "The monitor:// link is for macOS."; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
APP="$HOME/Applications/Investment Monitor.app"
LSREGISTER=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
PLIST=/usr/libexec/PlistBuddy

if [ "${1:-}" = --uninstall ]; then
  if [ -d "$APP" ]; then "$LSREGISTER" -u "$APP" 2>/dev/null || true; rm -rf "$APP"; fi
  echo "Removed $APP — monitor:// links do nothing now."
  exit 0
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
# This folder's script, written into the applet as an AppleScript string (\ and " escaped)
script="$HERE/monitor-link.sh"
script="${script//\\/\\\\}"
script="${script//\"/\\\"}"
src="$(cat "$HERE/link.applescript")"
printf '%s\n' "${src//__SCRIPT__/$script}" > "$tmp/link.applescript"

mkdir -p "$HOME/Applications"
rm -rf "$APP"
osacompile -o "$APP" "$tmp/link.applescript"
info="$APP/Contents/Info.plist"
"$PLIST" -c "Delete :CFBundleIdentifier" "$info" 2>/dev/null || true
"$PLIST" -c "Add :CFBundleIdentifier string local.investment-monitor.link" \
         -c "Add :LSUIElement bool true" \
         -c "Add :CFBundleURLTypes array" \
         -c "Add :CFBundleURLTypes:0 dict" \
         -c "Add :CFBundleURLTypes:0:CFBundleURLName string Investment Monitor" \
         -c "Add :CFBundleURLTypes:0:CFBundleURLSchemes array" \
         -c "Add :CFBundleURLTypes:0:CFBundleURLSchemes:0 string monitor" "$info"
codesign --force --sign - "$APP" 2>/dev/null           # the Info.plist changed: sign it again (ad hoc)
"$LSREGISTER" -f "$APP"
chmod +x "$HERE/monitor-link.sh"
echo "Installed $APP"
echo "Bookmark  monitor://open  (and monitor://stop to stop the terminal)."
