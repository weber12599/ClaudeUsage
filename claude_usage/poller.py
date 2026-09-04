"""Query one account's usage via a 1-token POST /v1/messages call.

Verified 2026-09-04 (see verify.sh). Two response shapes were observed:

  healthy  -> HTTP 200, anthropic-ratelimit-unified-status: allowed
              + -5h-utilization / -5h-reset / -7d-utilization / -7d-reset + overage-*
  blocked  -> HTTP 429, anthropic-ratelimit-unified-status: rejected
              NO 5h/7d headers; only -status, -representative-claim: overage,
              -overage-*, -overage-disabled-reason, retry-after, -reset

Every header is treated as optional.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict
from typing import Optional

from . import credentials
from .config import Account

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-haiku-4-5-20251001"
SYSTEM = "You are Claude Code, Anthropic's official CLI for Claude."
_H = "anthropic-ratelimit-unified"

_BODY = json.dumps({
    "model": MODEL,
    "max_tokens": 1,
    "system": SYSTEM,
    "messages": [{"role": "user", "content": "."}],
}).encode()


@dataclass
class Window:
    pct: Optional[float]
    reset: Optional[int]
    status: Optional[str]


@dataclass
class Snapshot:
    state: str                      # ok | blocked | expired | no_credentials | offline | http_<code>
    fetched_at: float
    account_id: Optional[str] = None
    subscription: Optional[str] = None
    overall_status: Optional[str] = None
    five_hour: Optional[Window] = None
    seven_day: Optional[Window] = None
    representative: Optional[str] = None
    overage_pct: Optional[float] = None
    blocked_reason: Optional[str] = None
    unblock_at: Optional[int] = None
    detail: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["fetched_at_iso"] = (
            time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self.fetched_at))
            if self.fetched_at else None
        )
        return d


def _pct(v: Optional[str]) -> Optional[float]:
    if v is None:
        return None
    try:
        return round(float(v) * 100, 1)
    except (TypeError, ValueError):
        return None


def _int(v: Optional[str]) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _window(h: dict, prefix: str) -> Optional[Window]:
    u = h.get(f"{_H}-{prefix}-utilization")
    r = h.get(f"{_H}-{prefix}-reset")
    s = h.get(f"{_H}-{prefix}-status")
    if u is None and r is None and s is None:
        return None
    return Window(pct=_pct(u), reset=_int(r), status=s)


def _parse(headers: dict, now: float) -> Snapshot:
    h = {k.lower(): v for k, v in headers.items()}
    overall = h.get(f"{_H}-status")
    blocked = overall == "rejected"
    unblock = None
    if blocked:
        unblock = _int(h.get(f"{_H}-reset"))
        if unblock is None and h.get("retry-after"):
            ra = _int(h.get("retry-after"))
            unblock = int(now + ra) if ra else None
    return Snapshot(
        state="blocked" if blocked else "ok",
        fetched_at=now,
        overall_status=overall,
        five_hour=_window(h, "5h"),
        seven_day=_window(h, "7d"),
        representative=h.get(f"{_H}-representative-claim"),
        overage_pct=_pct(h.get(f"{_H}-overage-utilization")),
        blocked_reason=h.get(f"{_H}-overage-disabled-reason"),
        unblock_at=unblock,
    )


def fetch(access_token: str) -> Snapshot:
    now = time.time()
    req = urllib.request.Request(API_URL, data=_BODY, method="POST", headers={
        "authorization": f"Bearer {access_token}",
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "oauth-2025-04-20",
        "content-type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return _parse(dict(resp.headers.items()), now)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return Snapshot(state="expired", fetched_at=now, detail="401 unauthorized")
        headers = dict(exc.headers.items()) if exc.headers else {}
        if any(k.lower().startswith(_H) for k in headers):
            snap = _parse(headers, now)
            snap.detail = f"http {exc.code}"
            return snap
        return Snapshot(state=f"http_{exc.code}", fetched_at=now, detail=f"http {exc.code}")
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return Snapshot(state="offline", fetched_at=now, detail=str(exc))


def poll_account(account: Account) -> Snapshot:
    now = time.time()
    cred = credentials.load(account.config_dir)
    if cred is None:
        return Snapshot(state="no_credentials", fetched_at=now, account_id=account.id,
                        detail=f"no keychain entry for {account.config_dir}")
    if cred.expired:
        return Snapshot(state="expired", fetched_at=now, account_id=account.id,
                        subscription=cred.subscription,
                        detail="stored token already expired; run `claude` on this account")
    snap = fetch(cred.access_token)
    snap.account_id = account.id
    snap.subscription = snap.subscription or cred.subscription
    return snap
