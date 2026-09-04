"""Entry point.

  python -m claude_usage --once     poll every enabled account once, print JSON, exit
  python -m claude_usage            run the menubar app (needs rumps + a recent python3)
"""

from __future__ import annotations

import argparse
import json
import sys

from . import config
from .poller import poll_account


def _run_once(cfg: config.Config) -> int:
    out = {}
    for account in cfg.accounts:
        if not account.enabled:
            out[account.id] = {"state": "disabled"}
            continue
        out[account.id] = poll_account(account).to_dict()
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


def _run_app(cfg: config.Config) -> int:
    try:
        from .menubar import run as run_app
    except ImportError as exc:
        print(
            "The menubar app needs 'rumps' (and a Homebrew python3, not the "
            f"system 3.9).\n  pip install rumps\nImport error: {exc}",
            file=sys.stderr,
        )
        return 1
    return run_app(cfg)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="claude-usage")
    parser.add_argument("--once", action="store_true",
                        help="poll each enabled account once, print JSON, exit")
    parser.add_argument("--config", metavar="PATH", default=None,
                        help="override config.json location")
    args = parser.parse_args(argv)

    cfg = config.load(args.config)
    return _run_once(cfg) if args.once else _run_app(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
