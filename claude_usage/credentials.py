"""Read a Claude Code account's OAuth access token from the macOS Keychain.

Verified 2026-09-04 (see verify.sh): Claude Code stores credentials as a generic
password whose service name is

    "Claude Code-credentials-" + sha256(<abs config dir>).hexdigest()[:8]

The value is JSON: {"claudeAiOauth": {"accessToken", "refreshToken",
"expiresAt" (ms), "scopes", "subscriptionType"}}. The default ~/.claude account
also keeps a legacy un-suffixed "Claude Code-credentials" entry in sync.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import List, Optional

LEGACY_SERVICE = "Claude Code-credentials"


@dataclass
class Cred:
    access_token: str
    expires_at_s: float           # epoch seconds (0 if unknown)
    subscription: Optional[str]   # e.g. "max", "pro"
    service: str                  # keychain service it came from
    scopes: List[str]

    @property
    def expired(self) -> bool:
        return self.expires_at_s > 0 and time.time() >= self.expires_at_s

    @property
    def seconds_left(self) -> Optional[int]:
        if self.expires_at_s <= 0:
            return None
        return int(self.expires_at_s - time.time())


def _abs_config_dir(config_dir: str) -> str:
    return os.path.realpath(os.path.expanduser(config_dir))


def keychain_service(config_dir: str) -> str:
    digest = hashlib.sha256(_abs_config_dir(config_dir).encode()).hexdigest()
    return f"{LEGACY_SERVICE}-{digest[:8]}"


def _candidate_services(config_dir: str) -> List[str]:
    services = [keychain_service(config_dir)]
    # The legacy un-suffixed entry only ever belongs to the default ~/.claude.
    if _abs_config_dir(config_dir) == _abs_config_dir("~/.claude"):
        services.append(LEGACY_SERVICE)
    return services


def _read_keychain(service: str) -> Optional[str]:
    try:
        proc = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-w"],
            capture_output=True, text=True, timeout=8,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    out = proc.stdout.strip()
    return out or None


def load(config_dir: str) -> Optional[Cred]:
    for service in _candidate_services(config_dir):
        raw = _read_keychain(service)
        if not raw:
            continue
        try:
            oauth = json.loads(raw)["claudeAiOauth"]
            token = oauth["accessToken"]
        except (ValueError, KeyError, TypeError):
            continue
        expires_ms = oauth.get("expiresAt") or 0
        return Cred(
            access_token=token,
            expires_at_s=float(expires_ms) / 1000.0 if expires_ms else 0.0,
            subscription=oauth.get("subscriptionType"),
            service=service,
            scopes=list(oauth.get("scopes") or []),
        )
    return None


def _find_claude_cli() -> Optional[str]:
    """Locate the `claude` binary. A menubar app launched as a login item has a
    bare PATH, so fall back to the usual install spots and then a login shell.
    """
    found = shutil.which("claude")
    if found:
        return found
    candidates = [
        os.path.expanduser("~/.claude/local/claude"),
        "/opt/homebrew/bin/claude",
        "/usr/local/bin/claude",
        os.path.expanduser("~/.bun/bin/claude"),
        os.path.expanduser("~/.local/bin/claude"),
    ]
    candidates += sorted(
        glob.glob(os.path.expanduser("~/.nvm/versions/node/*/bin/claude")), reverse=True
    )
    for path in candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    try:
        proc = subprocess.run(
            [os.environ.get("SHELL", "/bin/zsh"), "-lc", "command -v claude"],
            capture_output=True, text=True, timeout=8,
        )
        lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
        if lines and os.path.isfile(lines[-1]):
            return lines[-1]
    except (OSError, subprocess.SubprocessError):
        pass
    return None


# A one-token print-mode query is the lightest `claude` invocation that makes
# Claude Code notice the stale access token, mint a new one from the refresh
# token, and write it back to the Keychain. `claude auth status` does NOT
# (verified 2026-09-07: it reports loggedIn from the refresh token alone and
# never rewrites credentials).
_REFRESH_MODEL = "claude-haiku-4-5-20251001"


def refresh(config_dir: str, timeout: float = 60.0) -> dict:
    """Best-effort token refresh: run a tiny `claude -p` query pointed at this
    account's config dir so Claude Code mints a fresh OAuth access token from
    the stored refresh token and writes it back to the Keychain.

    Returns {"ok": bool, "message": str, "expires_at": Optional[float]}.
    """
    cli = _find_claude_cli()
    if cli is None:
        return {"ok": False, "message": "could not find the `claude` CLI on this machine"}

    before = load(config_dir)
    env = dict(os.environ)
    env["CLAUDE_CONFIG_DIR"] = _abs_config_dir(config_dir)
    # a login-item app has a bare PATH; make sure the CLI's own dir (where a
    # bundled `node` usually sits, for non-native installs) is reachable
    cli_dir = os.path.dirname(cli)
    env["PATH"] = cli_dir + os.pathsep + env.get("PATH", "")
    try:
        proc = subprocess.run(
            [cli, "-p", ".", "--model", _REFRESH_MODEL],
            capture_output=True, text=True, timeout=timeout, env=env,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "message": f"`claude` timed out after {int(timeout)}s"}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "message": f"could not run `claude`: {exc}"}

    cred = load(config_dir)
    if cred is not None and not cred.expired:
        changed = before is None or before.access_token != cred.access_token
        return {
            "ok": True,
            "message": "access token refreshed" if changed else "token already valid",
            "expires_at": cred.expires_at_s or None,
        }

    tail = ""
    for stream in (proc.stderr, proc.stdout):
        lines = [ln.strip() for ln in (stream or "").splitlines() if ln.strip()]
        if lines:
            tail = lines[-1]
            break
    tail = tail or f"exit {proc.returncode}"
    return {
        "ok": False,
        "message": f"still expired after running `claude` — sign in with "
                   f"`claude auth login` on this account ({tail})",
        "expires_at": cred.expires_at_s if cred else None,
    }


def probe(config_dir: str) -> dict:
    """Lightweight check for the UI when adding/editing an account."""
    cred = load(config_dir)
    if cred is None:
        return {
            "found": False,
            "service": keychain_service(config_dir),
            "subscription": None,
            "expires_at": None,
            "expired": None,
            "dir_exists": os.path.isdir(os.path.expanduser(config_dir)),
        }
    return {
        "found": True,
        "service": cred.service,
        "subscription": cred.subscription,
        "expires_at": cred.expires_at_s or None,
        "expired": cred.expired,
        "dir_exists": os.path.isdir(os.path.expanduser(config_dir)),
    }
