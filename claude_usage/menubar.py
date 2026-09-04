"""Menubar app: live title, threshold notifications, click-to-open dashboard popover.

rumps drives the status item, timer and notifications. We take over the status
button's action so a left click toggles the dashboard popover (no drop-down
menu); a right / control click shows a tiny context menu.

Needs `rumps` + `pyobjc-framework-WebKit`. Run from a real login GUI session.
"""

from __future__ import annotations

import subprocess
import queue as _queue
from typing import Callable, Dict

import objc
import rumps
from AppKit import (
    NSApp,
    NSEventMaskLeftMouseUp,
    NSEventMaskRightMouseUp,
    NSEventModifierFlagControl,
    NSEventTypeRightMouseUp,
    NSMenu,
    NSMenuItem,
)
from Foundation import NSObject

from . import config as config_mod
from .config import Account, Config
from .scheduler import Scheduler
from .state import Registry


def _notify(title: str, subtitle: str, message: str) -> None:
    try:
        rumps.notification(title, subtitle, message)
    except Exception:  # noqa: BLE001 - notifications are best effort
        pass


class _StatusClickProxy(NSObject):
    """ObjC target for the status-item button + context menu."""

    def initWithApp_(self, app):
        self = objc.super(_StatusClickProxy, self).init()
        if self is None:
            return None
        self._app = app
        return self

    def statusItemClicked_(self, sender):
        event = NSApp.currentEvent()
        right = (event is not None
                 and (event.type() == NSEventTypeRightMouseUp
                      or bool(event.modifierFlags() & NSEventModifierFlagControl)))
        if right:
            self._app._show_context_menu(sender)
        else:
            self._app._toggle_dashboard(sender)

    def refresh_(self, _):
        self._app.scheduler.refresh_now()

    def openConfig_(self, _):
        self._app._open_config()

    def quit_(self, _):
        NSApp.terminate_(None)


class ClaudeUsageApp(rumps.App):
    def __init__(self, cfg: Config, scheduler: Scheduler, registry: Registry):
        super().__init__("Claude —", quit_button=None)
        self.cfg = cfg
        self.scheduler = scheduler
        self.registry = registry
        self._dash = None                      # DashboardController (lazy)
        self._proxy = _StatusClickProxy.alloc().initWithApp_(self)

        self._timer = rumps.Timer(self._drain, 1)
        self._timer.start()
        self._wire_timer = rumps.Timer(self._wire_status_item, 0.3)
        self._wire_timer.start()
        self.title = self.registry.title()

    # -- take over the status item once rumps has created it -----------
    def _wire_status_item(self, timer):
        timer.stop()
        try:
            item = self._nsapp.nsstatusitem
            item.setMenu_(None)                 # no left-click drop-down
            button = item.button()
            button.setTarget_(self._proxy)
            button.setAction_("statusItemClicked:")
            button.sendActionOn_(NSEventMaskLeftMouseUp | NSEventMaskRightMouseUp)
        except Exception as exc:  # noqa: BLE001 - fall back to a plain menu
            print(f"[menubar] could not attach popover handler: {exc}; using menu")
            self.menu = [
                rumps.MenuItem("Dashboard…", callback=lambda _: self._toggle_dashboard(None)),
                rumps.MenuItem("Refresh now", callback=lambda _: self.scheduler.refresh_now()),
                None,
                rumps.MenuItem("Open config file", callback=lambda _: self._open_config()),
                rumps.MenuItem("Quit", callback=lambda _: NSApp.terminate_(None)),
            ]

    # -- actions -------------------------------------------------------
    @objc.python_method
    def _open_config(self):
        subprocess.run(["open", "-R", config_mod.config_path()], check=False)

    @objc.python_method
    def _dashboard(self):
        if self._dash is None:
            from .window import DashboardController
            self._dash = DashboardController.alloc().initWithHandlers_(self._bridge_handlers())
        return self._dash

    @objc.python_method
    def _toggle_dashboard(self, sender):
        dash = self._dashboard()
        if sender is None:                      # from fallback menu: anchor on status button
            sender = self._nsapp.nsstatusitem.button()
        dash.toggle(sender, self.registry.dashboard_payload())
        self.scheduler.refresh_now()

    @objc.python_method
    def _show_context_menu(self, sender):
        menu = NSMenu.alloc().init()
        for title, action in (("Refresh now", "refresh:"),
                              ("Open config file", "openConfig:"),
                              (None, None),
                              ("Quit", "quit:")):
            if title is None:
                menu.addItem_(NSMenuItem.separatorItem())
                continue
            mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, "")
            mi.setTarget_(self._proxy)
            menu.addItem_(mi)
        self._nsapp.nsstatusitem.popUpStatusItemMenu_(menu)

    # -- main-loop pump ---------------------------------------------
    def _drain(self, _):
        changed = False
        while True:
            try:
                account_id, snap = self.scheduler.results.get_nowait()
            except _queue.Empty:
                break
            self.registry.update(account_id, snap)
            self._maybe_notify(account_id)
            changed = True
        if changed:
            self.title = self.registry.title()
            if self._dash is not None and self._dash.is_visible():
                self._dash.render(self.registry.dashboard_payload())

    @objc.python_method
    def _maybe_notify(self, account_id: str):
        st = self.registry.states.get(account_id)
        if not st or not st.snapshot:
            return
        if st.snapshot.state == "blocked":
            if st.notified.get("blocked") != 1:
                st.notified["blocked"] = 1
                _notify("Claude usage", st.account.name,
                        f"Blocked: {st.snapshot.blocked_reason or 'rate limit reached'}")
            return
        st.notified.pop("blocked", None)
        for label, win in (("5h", st.snapshot.five_hour), ("7d", st.snapshot.seven_day)):
            pct = win.pct if win else None
            if pct is None:
                continue
            prev = st.notified.get(label, 0)
            applicable = max([t for t in self.cfg.notify_thresholds if pct >= t], default=0)
            if applicable > prev:
                _notify("Claude usage", st.account.name,
                        f"{label} usage at {pct:.0f}% (≥ {applicable}%)")
            st.notified[label] = applicable

    # -- bridge handlers for the dashboard -------------------------
    @objc.python_method
    def _bridge_handlers(self) -> Dict[str, Callable]:
        return {
            "ready": lambda _p: self.registry.dashboard_payload(),
            "refreshNow": lambda _p: (self.scheduler.refresh_now(),
                                      self.registry.dashboard_payload())[1],
            "addAccount": self._h_add_account,
            "updateAccount": self._h_update_account,
            "removeAccount": self._h_remove_account,
            "saveGlobal": self._h_save_global,
            "probeDir": self._h_probe_dir,
            "openConfigFile": lambda _p: (self._open_config(), None)[1],
        }

    @objc.python_method
    def _persist(self) -> dict:
        config_mod.save(self.cfg)
        self.scheduler.reload(self.cfg)
        self.registry.reconcile(self.cfg)
        self.title = self.registry.title()
        return self.registry.dashboard_payload()

    @objc.python_method
    def _h_add_account(self, payload: dict) -> dict:
        base = _slug(payload.get("name") or payload.get("config_dir") or "account")
        acc_id, n = base, 2
        while self.cfg.account(acc_id):
            acc_id, n = f"{base}-{n}", n + 1
        acc = Account.from_dict({**payload, "id": acc_id})
        if not acc.color:
            acc.color = config_mod.suggest_color(self.cfg.accounts)
        self.cfg.accounts.append(acc)
        return self._persist()

    @objc.python_method
    def _h_update_account(self, payload: dict) -> dict:
        acc = self.cfg.account(payload.get("id", ""))
        if not acc:
            return self.registry.dashboard_payload()
        for f in ("name", "short", "config_dir", "color"):
            if payload.get(f) is not None:
                setattr(acc, f, str(payload[f]))
        if "enabled" in payload:
            acc.enabled = bool(payload["enabled"])
        if "poll_interval_seconds" in payload:
            v = payload["poll_interval_seconds"]
            acc.poll_interval_seconds = int(v) if v else None
        return self._persist()

    @objc.python_method
    def _h_remove_account(self, payload: dict) -> dict:
        self.cfg.accounts = [a for a in self.cfg.accounts if a.id != payload.get("id")]
        return self._persist()

    @objc.python_method
    def _h_save_global(self, payload: dict) -> dict:
        if payload.get("poll_interval_seconds"):
            self.cfg.poll_interval_seconds = max(30, int(payload["poll_interval_seconds"]))
        if payload.get("stale_after_seconds"):
            self.cfg.stale_after_seconds = max(60, int(payload["stale_after_seconds"]))
        if payload.get("notify_thresholds") is not None:
            self.cfg.notify_thresholds = sorted(
                {int(t) for t in payload["notify_thresholds"] if 0 < int(t) <= 100}
            )
        return self._persist()

    @objc.python_method
    def _h_probe_dir(self, payload: dict) -> dict:
        from . import credentials
        return credentials.probe(str(payload.get("config_dir") or ""))


def _slug(text: str) -> str:
    out = "".join(c if c.isalnum() else "-" for c in text.strip().lower())
    return "-".join(p for p in out.split("-") if p) or "account"


def run(cfg: Config) -> int:
    scheduler = Scheduler(cfg)
    registry = Registry(cfg)
    scheduler.start()
    print(f"claude-usage: menubar app started, watching {len(cfg.accounts)} account(s). "
          "Left-click the menubar item for the dashboard; right-click for Quit.")
    ClaudeUsageApp(cfg, scheduler, registry).run()
    return 0
