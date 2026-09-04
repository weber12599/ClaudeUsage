"""py2app build:  python setup.py py2app        (standalone .app)
                   python setup.py py2app -A     (fast alias build for dev)

Produces dist/ClaudeUsage.app — a menubar-only agent (LSUIElement, no Dock icon).
"""

from setuptools import setup

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
        "CFBundleVersion": "0.1.0",
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
    },
}

setup(
    app=APP,
    name="ClaudeUsage",
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
)
