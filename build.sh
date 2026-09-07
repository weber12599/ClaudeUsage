#!/bin/bash
# Build the standalone ClaudeUsage.app bundle.
set -euo pipefail
cd "$(dirname "$0")"

# PY defaults to the project venv; CI sets PY=python to use the runner's env.
PY="${PY:-.venv/bin/python}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "python not found: $PY" >&2
  echo "create the venv first:" >&2
  echo "  /opt/homebrew/bin/python3.13 -m venv .venv" >&2
  echo "  .venv/bin/pip install -r requirements-dev.txt" >&2
  exit 1
fi

"$PY" -m pip install -q --upgrade "rumps>=0.4.0" "pyobjc-framework-WebKit>=10.0" "py2app>=0.28"
rm -rf build dist
"$PY" setup.py py2app
echo
echo "built: dist/ClaudeUsage.app"
echo "install: mv dist/ClaudeUsage.app /Applications/ && open /Applications/ClaudeUsage.app"
