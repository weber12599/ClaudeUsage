"""py2app entry point — a top-level script (not a package __main__) so the
bundle can run it directly. Delegates to the real CLI in claude_usage.__main__.
"""

from claude_usage.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
