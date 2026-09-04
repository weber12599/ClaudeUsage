"""Read a Claude Code account's OAuth access token from the macOS Keychain.

Verified 2026-09-04 (see verify.sh): Claude Code stores credentials as a generic
password whose service name is

    "Claude Code-credentials-" + sha256(<abs config dir>).hexdigest()[:8]

The value is JSON: {"claudeAiOauth": {"accessToken", "refreshToken",
"expiresAt" (ms), "scopes", "subscriptionType"}}. The default ~/.claude account
also keeps a legacy un-suffixed "Claude Code-credentials" entry in sync.
"""

from __future__ import annotations

import hashlib
import json
import os
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
