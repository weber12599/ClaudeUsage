"""Fast, network-free checks for the pure-logic modules.

These guard the two brittle pieces — the reverse-engineered Keychain
service-name derivation and the `anthropic-ratelimit-unified-*` header parsing —
plus config round-tripping and version sanity. GUI modules (menubar/window) are
not imported here.
"""

import re

import claude_usage
from claude_usage import credentials
from claude_usage.config import Config, default_config
from claude_usage.poller import Snapshot, _parse
from claude_usage.state import Registry, menubar_fragment, severity


def test_version_is_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", claude_usage.__version__)


def test_cli_version_flag_matches_package(capsys):
    import pytest

    from claude_usage.__main__ import main

    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"claude-usage {claude_usage.__version__}"


def test_keychain_service_shape_and_stability():
    svc = credentials.keychain_service("~/.claude")
    assert svc.startswith("Claude Code-credentials-")
    assert re.fullmatch(r"[0-9a-f]{8}", svc.rsplit("-", 1)[1])
    assert credentials.keychain_service("~/.claude") == svc  # deterministic


def test_only_default_dir_probes_the_legacy_entry():
    assert credentials.LEGACY_SERVICE in credentials._candidate_services("~/.claude")
    assert credentials.LEGACY_SERVICE not in credentials._candidate_services("~/.claude-work")


def test_header_parsing_healthy():
    now = 1_800_000_000.0
    snap = _parse(
        {
            "anthropic-ratelimit-unified-status": "allowed",
            "anthropic-ratelimit-unified-5h-utilization": "0.42",
            "anthropic-ratelimit-unified-5h-reset": str(int(now + 3600)),
            "anthropic-ratelimit-unified-7d-utilization": "0.1",
            "anthropic-ratelimit-unified-7d-reset": str(int(now + 86400)),
        },
        now,
    )
    assert snap.state == "ok"
    assert snap.five_hour.pct == 42.0
    assert snap.five_hour.reset == int(now + 3600)
    assert snap.seven_day.pct == 10.0


def test_header_parsing_blocked():
    now = 1_800_000_000.0
    snap = _parse(
        {
            "anthropic-ratelimit-unified-status": "rejected",
            "anthropic-ratelimit-unified-reset": str(int(now + 120)),
            "anthropic-ratelimit-unified-overage-disabled-reason": "org_spend_cap_reached",
        },
        now,
    )
    assert snap.state == "blocked"
    assert snap.unblock_at == int(now + 120)
    assert snap.blocked_reason == "org_spend_cap_reached"


def test_config_roundtrip():
    cfg = default_config()
    again = Config.from_dict(cfg.to_dict())
    assert [a.id for a in again.accounts] == [a.id for a in cfg.accounts]
    assert again.poll_interval_seconds == cfg.poll_interval_seconds


def test_dashboard_payload_carries_version():
    payload = Registry(default_config()).dashboard_payload()
    assert payload["version"] == claude_usage.__version__


def test_severity_and_menubar_fragment_for_expired():
    cfg = default_config()
    reg = Registry(cfg)
    st = reg.states[cfg.accounts[0].id]
    st.snapshot = Snapshot(state="expired", fetched_at=0.0, account_id=st.account.id)
    assert severity(st, cfg) == "expired"
    assert "⚠" in menubar_fragment(st, cfg)
