"""py2app build:  python setup.py py2app        (standalone .app)
                   python setup.py py2app -A     (fast alias build for dev)

Produces dist/ClaudeUsage.app — a menubar-only agent (LSUIElement, no Dock icon).

Any other invocation (pip install, build, editable install) falls through to a
bare setup() so the metadata comes from pyproject.toml.
"""

import pathlib
import re
import sys

from setuptools import setup

_INIT = pathlib.Path(__file__).parent / "claude_usage" / "__init__.py"
VERSION = re.search(r'__version__\s*=\s*"([^"]+)"', _INIT.read_text(encoding="utf-8")).group(1)

APP = ["app_main.py"]
DATA_FILES = [("claude_usage/ui", ["claude_usage/ui/dashboard.html"])]
OPTIONS = {
    "argv_emulation": False,
    "packages": ["claude_usage", "rumps"],
    "includes": ["objc", "AppKit", "WebKit", "Foundation"],
    "plist": {
        "CFBundleName": "ClaudeUsage",
        "CFBundleDisplayName": "Claude Usage",
        "CFBundleIdentifier": "local.claude-usage.menubar",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
    },
}

if "py2app" in sys.argv:
    setup(
        app=APP,
        name="ClaudeUsage",
        version=VERSION,
        data_files=DATA_FILES,
        options={"py2app": OPTIONS},
    )
else:
    setup()  # metadata + version come from pyproject.toml
