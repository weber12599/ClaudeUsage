#!/bin/bash
# Install ClaudeUsage.app into /Applications and register it as a hidden
# login item so it starts automatically when you log in.
#
#   ./install.sh                       # uses dist/ClaudeUsage.app (run ./build.sh first)
#   ./install.sh /path/to/ClaudeUsage.app
#
# The first run triggers a one-time "control System Events" permission prompt.
set -euo pipefail
cd "$(dirname "$0")"

SRC="${1:-dist/ClaudeUsage.app}"
DEST="/Applications/ClaudeUsage.app"

if [ ! -d "$SRC" ]; then
  echo "not found: $SRC  — run ./build.sh first" >&2
  exit 1
fi

if [ "$(cd "$SRC" && pwd)" != "$DEST" ]; then
  rm -rf "$DEST"
  cp -R "$SRC" "$DEST"
  echo "copied -> $DEST"
fi

osascript <<EOF
tell application "System Events"
  if login item "ClaudeUsage" exists then delete login item "ClaudeUsage"
  make login item at end with properties {path:"$DEST", hidden:true}
end tell
EOF
echo "registered as login item (hidden)"

open "$DEST"
echo "launched. Look for the usage text in your menubar."
echo "to remove: System Settings > General > Login Items, or"
echo "  osascript -e 'tell application \"System Events\" to delete login item \"ClaudeUsage\"'"
