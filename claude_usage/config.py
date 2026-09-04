"""Load/save the tool's JSON config (account list + global settings).

Config lives at ~/Library/Application Support/ClaudeUsage/config.json so the UI
can rewrite it. stdlib json only (tomllib is read-only), Python 3.9 compatible.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field, replace
from typing import Any, List, Optional

CONFIG_VERSION = 1

_DEFAULT_COLORS = ["#3b82f6", "#ef4444", "#22c55e", "#a855f7", "#f59e0b", "#14b8a6"]


def config_dir() -> str:
    return os.path.expanduser("~/Library/Application Support/ClaudeUsage")


def config_path() -> str:
    return os.path.join(config_dir(), "config.json")


@dataclass
class Account:
    id: str
    name: str
    short: str
    config_dir: str
    enabled: bool = True
    color: str = _DEFAULT_COLORS[0]
    poll_interval_seconds: Optional[int] = None  # None -> use the global interval

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "short": self.short,
            "config_dir": self.config_dir,
            "enabled": self.enabled,
            "color": self.color,
            "poll_interval_seconds": self.poll_interval_seconds,
        }

    @staticmethod
    def from_dict(d: dict) -> "Account":
        name = str(d.get("name") or d.get("id") or "Account")
        raw_id = str(d.get("id") or name).strip() or name
        interval = d.get("poll_interval_seconds")
        return Account(
            id=raw_id,
            name=name,
            short=str(d.get("short") or name[:1].upper() or "?"),
            config_dir=str(d.get("config_dir") or "~/.claude"),
            enabled=bool(d.get("enabled", True)),
            color=str(d.get("color") or _DEFAULT_COLORS[0]),
            poll_interval_seconds=int(interval) if interval else None,
        )

    def effective_interval(self, global_interval: int) -> int:
        return int(self.poll_interval_seconds or global_interval)


@dataclass
class Config:
    version: int = CONFIG_VERSION
    poll_interval_seconds: int = 300
    stale_after_seconds: int = 1800
    notify_thresholds: List[int] = field(default_factory=lambda: [80, 95])
    accounts: List[Account] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "poll_interval_seconds": self.poll_interval_seconds,
            "stale_after_seconds": self.stale_after_seconds,
            "notify_thresholds": list(self.notify_thresholds),
            "accounts": [a.to_dict() for a in self.accounts],
        }

    @staticmethod
    def from_dict(d: dict) -> "Config":
        accounts = [Account.from_dict(a) for a in d.get("accounts", []) if isinstance(a, dict)]
        accounts = _dedupe_ids(accounts)
        thresholds = d.get("notify_thresholds") or [80, 95]
        return Config(
            version=int(d.get("version") or CONFIG_VERSION),
            poll_interval_seconds=max(30, int(d.get("poll_interval_seconds") or 300)),
            stale_after_seconds=max(60, int(d.get("stale_after_seconds") or 1800)),
            notify_thresholds=sorted({int(t) for t in thresholds if 0 < int(t) <= 100}),
            accounts=accounts,
        )

    def account(self, account_id: str) -> Optional[Account]:
        return next((a for a in self.accounts if a.id == account_id), None)

    def enabled_accounts(self) -> List[Account]:
        return [a for a in self.accounts if a.enabled]


def _dedupe_ids(accounts: List[Account]) -> List[Account]:
    seen: set = set()
    out: List[Account] = []
    for a in accounts:
        aid = a.id
        n = 2
        while aid in seen:
            aid = f"{a.id}-{n}"
            n += 1
        seen.add(aid)
        out.append(a if aid == a.id else replace(a, id=aid))
    return out


def default_config() -> Config:
    return Config(
        accounts=[
            Account(id="personal", name="Personal", short="P",
                    config_dir="~/.claude", color=_DEFAULT_COLORS[0]),
            Account(id="work", name="Work", short="W",
                    config_dir="~/.claude-work", color=_DEFAULT_COLORS[1]),
        ]
    )


def suggest_color(existing: List[Account]) -> str:
    used = {a.color for a in existing}
    for c in _DEFAULT_COLORS:
        if c not in used:
            return c
    return _DEFAULT_COLORS[len(existing) % len(_DEFAULT_COLORS)]


def load(path: Optional[str] = None) -> Config:
    path = path or config_path()
    if not os.path.exists(path):
        cfg = default_config()
        save(cfg, path)
        return cfg
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        return Config.from_dict(raw)
    except Exception as exc:  # noqa: BLE001 - corrupt config must not crash the app
        bak = f"{path}.bak.{int(time.time())}"
        try:
            shutil.copy2(path, bak)
        except OSError:
            bak = "(backup failed)"
        print(f"[config] failed to parse {path}: {exc}; backed up to {bak}, using defaults")
        cfg = default_config()
        save(cfg, path)
        return cfg


def save(cfg: Config, path: Optional[str] = None) -> None:
    path = path or config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        try:
            shutil.copy2(path, f"{path}.bak")
        except OSError:
            pass
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg.to_dict(), fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)
