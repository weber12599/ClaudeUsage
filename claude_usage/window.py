"""Dashboard as a menubar popover (an NSPopover hosting a WKWebView).

Shares rumps' NSApplication; every method here runs on the main thread (called
from the status-item action or rumps.Timer). The JS<->Python bridge speaks JSON
strings so we never touch NSDictionary conversion:

    JS  -> window.webkit.messageHandlers.bridge.postMessage(JSON.stringify(msg))
    Py  -> webview.evaluateJavaScript("window.__render(<json>)" / "window.__bridgeReply(<json>)")
"""

from __future__ import annotations

import json
import os
from typing import Callable, Dict, Optional

import objc
from AppKit import (
    NSApp,
    NSOpenPanel,
    NSPopover,
    NSPopoverBehaviorTransient,
    NSRectEdgeMinY,
    NSViewController,
)
from Foundation import NSMakeRect, NSMakeSize, NSObject
from WebKit import WKWebView, WKWebViewConfiguration

POPOVER_SIZE = (580, 480)


def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _load_html() -> str:
    # dev: next to this file; py2app bundle: same, package tree is copied whole
    candidates = [os.path.join(os.path.dirname(__file__), "ui", "dashboard.html")]
    res = os.environ.get("RESOURCEPATH")  # set inside a py2app bundle
    if res:
        candidates.append(os.path.join(res, "claude_usage", "ui", "dashboard.html"))
    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            continue
    return "<h1 style='font:14px -apple-system;padding:20px'>dashboard.html not found</h1>"


class DashboardController(NSObject):
    def initWithHandlers_(self, handlers: Dict[str, Callable]):
        self = objc.super(DashboardController, self).init()
        if self is None:
            return None
        self.handlers = handlers
        self._loaded = False
        self._pending: Optional[dict] = None
        self._build()
        return self

    # -- construction --------------------------------------------------
    @objc.python_method
    def _build(self):
        cfg = WKWebViewConfiguration.alloc().init()
        cfg.userContentController().addScriptMessageHandler_name_(self, "bridge")

        w, h = POPOVER_SIZE
        self.webview = WKWebView.alloc().initWithFrame_configuration_(
            NSMakeRect(0, 0, w, h), cfg
        )
        self.webview.setNavigationDelegate_(self)

        vc = NSViewController.alloc().init()
        vc.setView_(self.webview)

        self.popover = NSPopover.alloc().init()
        self.popover.setContentViewController_(vc)
        self.popover.setContentSize_(NSMakeSize(w, h))
        self.popover.setBehavior_(NSPopoverBehaviorTransient)
        self.popover.setAnimates_(True)

        self.webview.loadHTMLString_baseURL_(_load_html(), None)

    # -- public (Python, main thread) -------------------------------
    @objc.python_method
    def toggle(self, sender, payload: Optional[dict]):
        if self.popover.isShown():
            self.popover.performClose_(sender)
            return
        if payload is not None:
            self._render_or_queue(payload)
        self.popover.showRelativeToRect_ofView_preferredEdge_(
            sender.bounds(), sender, NSRectEdgeMinY
        )
        NSApp.activateIgnoringOtherApps_(True)

    @objc.python_method
    def close(self):
        if self.popover.isShown():
            self.popover.performClose_(None)

    @objc.python_method
    def is_visible(self) -> bool:
        return bool(self.popover.isShown())

    @objc.python_method
    def render(self, payload: dict):
        self._render_or_queue(payload)

    # -- internals -------------------------------------------------
    @objc.python_method
    def _render_or_queue(self, payload: dict):
        if not self._loaded:
            self._pending = payload
            return
        self._eval(f"window.__render({_dumps(payload)})")

    @objc.python_method
    def _eval(self, js: str):
        self.webview.evaluateJavaScript_completionHandler_(js, None)

    @objc.python_method
    def _reply(self, req_id, result):
        if req_id is None:
            return
        self._eval(f"window.__bridgeReply({_dumps({'reqId': req_id, 'result': result})})")

    @objc.python_method
    def _pick_dir(self) -> Optional[str]:
        panel = NSOpenPanel.openPanel()
        panel.setCanChooseFiles_(False)
        panel.setCanChooseDirectories_(True)
        panel.setAllowsMultipleSelection_(False)
        panel.setPrompt_("Choose")
        if panel.runModal() != 1:  # NSModalResponseOK
            return None
        return str(panel.URLs()[0].path())

    # -- WKNavigationDelegate ------------------------------------
    def webView_didFinishNavigation_(self, webview, navigation):
        self._loaded = True
        if self._pending is not None:
            self._eval(f"window.__render({_dumps(self._pending)})")
            self._pending = None

    # -- WKScriptMessageHandler --------------------------------
    def userContentController_didReceiveScriptMessage_(self, controller, message):
        try:
            msg = json.loads(str(message.body()))
        except (ValueError, TypeError):
            return
        action = msg.get("action")
        req_id = msg.get("reqId")

        if action == "pickDir":
            self._reply(req_id, {"path": self._pick_dir()})
            return
        if action == "quit":
            NSApp.terminate_(None)
            return

        handler = self.handlers.get(action)
        if handler is None:
            self._reply(req_id, None)
            return
        try:
            result = handler(msg)
        except Exception as exc:  # noqa: BLE001 - surface to UI, don't crash
            self._reply(req_id, {"error": str(exc)})
            return
        self._reply(req_id, result)
