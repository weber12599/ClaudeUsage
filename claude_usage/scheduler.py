"""Background polling loop with per-account intervals.

Results are pushed onto `.results` (a queue.Queue of (account_id, Snapshot)); the
menubar drains it on the main thread. All config access is guarded by a lock so
`reload()` can be called from the UI thread.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Dict

from . import credentials
from .config import Config
from .poller import Snapshot, poll_account

# After a poll comes back `expired` we try one token refresh (a small `claude -p`
# call that mints a fresh access token from the stored refresh token). If that
# fails, wait this long before trying that account again so a dead refresh token
# doesn't spawn `claude` every poll cycle.
_REFRESH_RETRY_COOLDOWN = 900.0


class Scheduler:
    def __init__(self, cfg: Config):
        self._cfg = cfg
        self._lock = threading.Lock()
        self._next_due: Dict[str, float] = {}
        self._refresh_after: Dict[str, float] = {}  # account_id -> earliest next auto token-refresh
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.results: "queue.Queue[tuple[str, object]]" = queue.Queue()

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="claude-usage-poller", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    # -- control -----------------------------------------------------------
    def refresh_now(self) -> None:
        with self._lock:
            self._next_due.clear()
        self._wake.set()

    def reload(self, cfg: Config) -> None:
        with self._lock:
            self._cfg = cfg
            ids = {a.id for a in cfg.accounts}
            self._next_due = {k: v for k, v in self._next_due.items() if k in ids}
            self._refresh_after = {k: v for k, v in self._refresh_after.items() if k in ids}
            for account in cfg.accounts:            # new/re-enabled accounts poll asap
                self._next_due.setdefault(account.id, 0.0)
        self._wake.set()

    # -- loop ------------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop.is_set():
            sleep_for = self._tick()
            self._wake.wait(timeout=sleep_for)
            self._wake.clear()

    def _tick(self) -> float:
        now = time.time()
        with self._lock:
            cfg = self._cfg
            due = [a for a in cfg.accounts
                   if a.enabled and now >= self._next_due.get(a.id, 0.0)]

        for account in due:
            if self._stop.is_set():
                return 0.1
            try:
                snap = poll_account(account)
                if snap.state == "expired":
                    snap = self._try_token_refresh(account, snap)
            except Exception as exc:  # noqa: BLE001 - a bad account must not kill the loop
                snap = Snapshot(state="offline", fetched_at=time.time(),
                                account_id=account.id, detail=f"poll error: {exc}")
            if snap.state == "ok":
                self._refresh_after.pop(account.id, None)
            self.results.put((account.id, snap))
            interval = account.effective_interval(cfg.poll_interval_seconds)
            with self._lock:
                self._next_due[account.id] = time.time() + interval

        with self._lock:
            cfg = self._cfg
            upcoming = [self._next_due.get(a.id, 0.0)
                        for a in cfg.accounts if a.enabled]
        if not upcoming:
            return 30.0
        return max(1.0, min(60.0, min(upcoming) - time.time()))

    def _try_token_refresh(self, account, expired_snap: Snapshot) -> Snapshot:
        """The stored access token is expired — attempt one refresh from the
        refresh token, then re-poll. Rate-limited per account (see
        `_REFRESH_RETRY_COOLDOWN`) so a permanently dead refresh token doesn't
        spawn `claude` on every cycle. Called only from the poller thread.
        """
        now = time.time()
        if now < self._refresh_after.get(account.id, 0.0):
            return expired_snap
        self._refresh_after[account.id] = now + _REFRESH_RETRY_COOLDOWN

        try:
            res = credentials.refresh(account.config_dir)
        except Exception as exc:  # noqa: BLE001 - a refresh crash must not kill the loop
            res = {"ok": False, "message": str(exc)}

        if not res.get("ok"):
            expired_snap.detail = (
                f"auto token-refresh failed — {res.get('message', 'unknown error')}"
            )
            return expired_snap

        fresh = poll_account(account)
        if fresh.state == "ok":
            self._refresh_after.pop(account.id, None)
        elif fresh.state == "expired":
            fresh.detail = "still expired after auto token-refresh; sign in with `claude auth login`"
        return fresh
