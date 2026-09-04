"""In-memory view model: last snapshot per account + menubar/dashboard formatting."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .config import Account, Config
from .poller import Snapshot

# severity buckets, worst last
SEV_ORDER = ["nodata", "ok", "warn", "offline", "expired", "nocreds", "crit", "blocked"]


@dataclass
class AccountState:
    account: Account
    snapshot: Optional[Snapshot] = None          # last result (success or failure)
    last_ok: Optional[Snapshot] = None           # last state == "ok"
    notified: Dict[str, int] = field(default_factory=dict)  # "5h"/"7d" -> highest threshold notified

    def display_snapshot(self) -> Optional[Snapshot]:
        if self.snapshot and self.snapshot.state in ("ok", "blocked"):
            return self.snapshot
        return self.last_ok or self.snapshot


def display_pct(snap: Optional[Snapshot]) -> Optional[float]:
    if snap is None:
        return None
    vals = [w.pct for w in (snap.five_hour, snap.seven_day) if w and w.pct is not None]
    return max(vals) if vals else None


def is_stale(snap: Optional[Snapshot], cfg: Config) -> bool:
    if not snap or not snap.fetched_at:
        return False
    return (time.time() - snap.fetched_at) > cfg.stale_after_seconds


def severity(state: AccountState, cfg: Config) -> str:
    snap = state.snapshot
    if snap is None:
        return "nodata"
    if snap.state == "blocked":
        return "blocked"
    if snap.state == "no_credentials":
        return "nocreds"
    if snap.state == "expired":
        return "expired"
    if snap.state in ("offline",) or snap.state.startswith("http_"):
        return "offline"
    pct = display_pct(state.display_snapshot())
    if pct is None:
        return "nodata"
    if pct >= 95:
        return "crit"
    if pct >= 80:
        return "warn"
    return "ok"


_PREFIX = {"ok": "", "warn": "\U0001F7E0 ", "crit": "\U0001F534 "}  # green/orange/red dots


def menubar_fragment(state: AccountState, cfg: Config) -> str:
    short = state.account.short or state.account.name[:1].upper()
    sev = severity(state, cfg)
    if sev == "blocked":
        return f"{short} ⛔"          # no-entry
    if sev == "expired":
        return f"{short} ⚠"          # warning sign
    if sev == "nocreds":
        return f"{short} —"          # em dash
    if sev in ("offline", "nodata"):
        pct = display_pct(state.display_snapshot())
        return f"{short} ~{pct:.0f}%" if pct is not None else f"{short} ~"
    pct = display_pct(state.display_snapshot())
    if pct is None:
        return f"{short} —"
    stale = "~" if is_stale(state.snapshot, cfg) else ""
    return f"{_PREFIX.get(sev, '')}{short} {stale}{pct:.0f}%"


class Registry:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.states: Dict[str, AccountState] = {}
        self.reconcile(cfg)

    def reconcile(self, cfg: Config) -> None:
        self.cfg = cfg
        ids = {a.id for a in cfg.accounts}
        for account in cfg.accounts:
            st = self.states.get(account.id)
            if st is None:
                self.states[account.id] = AccountState(account=account)
            else:
                st.account = account
        for gone in [k for k in self.states if k not in ids]:
            del self.states[gone]

    def update(self, account_id: str, snap: Snapshot) -> None:
        st = self.states.get(account_id)
        if st is None:
            return
        st.snapshot = snap
        if snap.state == "ok":
            st.last_ok = snap

    def title(self) -> str:
        frags = [
            menubar_fragment(self.states[a.id], self.cfg)
            for a in self.cfg.accounts if a.enabled and a.id in self.states
        ]
        return "  ·  ".join(frags) if frags else "Claude —"

    def dashboard_payload(self) -> dict:
        return {
            "config": {
                "poll_interval_seconds": self.cfg.poll_interval_seconds,
                "stale_after_seconds": self.cfg.stale_after_seconds,
                "notify_thresholds": self.cfg.notify_thresholds,
            },
            "accounts": [self._account_payload(a) for a in self.cfg.accounts],
            "generated_at": time.time(),
        }

    def _account_payload(self, account: Account) -> dict:
        st = self.states.get(account.id)
        snap = st.snapshot if st else None
        disp = st.display_snapshot() if st else None
        return {
            **account.to_dict(),
            "severity": severity(st, self.cfg) if st else "nodata",
            "stale": is_stale(snap, self.cfg),
            "display_pct": display_pct(disp),
            "snapshot": snap.to_dict() if snap else None,
        }


def crossed_thresholds(prev: Optional[int], pct: Optional[float], thresholds: List[int]) -> Optional[int]:
    """Highest threshold newly crossed upward, else None."""
    if pct is None:
        return None
    prev = prev or 0
    newly = [t for t in thresholds if t > prev and pct >= t]
    return max(newly) if newly else None
