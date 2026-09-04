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

from .config import Config
from .poller import poll_account


class Scheduler:
    def __init__(self, cfg: Config):
        self._cfg = cfg
        self._lock = threading.Lock()
        self._next_due: Dict[str, float] = {}
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
            except Exception as exc:  # noqa: BLE001 - a bad account must not kill the loop
                from .poller import Snapshot
                snap = Snapshot(state="offline", fetched_at=time.time(),
                                account_id=account.id, detail=f"poll error: {exc}")
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
