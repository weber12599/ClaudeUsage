#!/bin/bash
# Build the standalone ClaudeUsage.app bundle.
set -euo pipefail
cd "$(dirname "$0")"

PY=.venv/bin/python
if [ ! -x "$PY" ]; then
  echo "no .venv — create it first:" >&2
  echo "  /opt/homebrew/bin/python3.13 -m venv .venv" >&2
  echo "  .venv/bin/pip install rumps 'pyobjc-framework-WebKit>=10.0' 'py2app>=0.28'" >&2
  exit 1
fi

"$PY" -m pip install -q --upgrade "rumps>=0.4.0" "pyobjc-framework-WebKit>=10.0" "py2app>=0.28"
rm -rf build dist
"$PY" setup.py py2app
echo
echo "built: dist/ClaudeUsage.app"
echo "install: mv dist/ClaudeUsage.app /Applications/ && open /Applications/ClaudeUsage.app"
