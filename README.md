# Claude Usage — menubar tool

A macOS menubar tool that watches Claude usage across **multiple accounts**, one
per `CLAUDE_CONFIG_DIR`. The menubar shows each account's usage; left-click drops
down a dashboard with a table and an editor for adding/removing accounts.

```
 P 43%  ·  W ⛔          ← menubar title
```

## How it works

For each enabled account the tool:

1. derives the Keychain service name `Claude Code-credentials-<sha256(abs dir)[:8]>`
   and reads the OAuth access token from it;
2. makes one 1-token `POST /v1/messages` call (Haiku, with the Claude Code system
   prompt the OAuth token requires);
3. parses the `anthropic-ratelimit-unified-*` response headers — 5h / 7d
   utilisation and reset times, or the `rejected` / overage state when blocked.

There is **no token refresh**: an account whose stored token has expired shows
`⚠` until you run `claude` on it yourself. Cost is ~1 token per healthy account
per poll (default every 5 min).

See [`docs/design.md`](docs/design.md) for the reverse-engineered API details and
[`verify.sh`](verify.sh) for a standalone end-to-end check.

## Requirements

- macOS, run from a real login GUI session (not headless / ssh).
- A modern Python — the system 3.9 can't build pyobjc. Homebrew's works and has
  prebuilt wheels:

  ```sh
  brew install python@3.13
  /opt/homebrew/bin/python3.13 -m venv .venv
  .venv/bin/pip install rumps "pyobjc-framework-WebKit>=10.0"
  ```

## Run (dev)

```sh
.venv/bin/python -m claude_usage            # the menubar app
.venv/bin/python -m claude_usage --once     # poll each account once, print JSON, exit
```

First run pops a Keychain prompt per account — choose **Always Allow**.

## Build a standalone .app

```sh
./build.sh                                  # -> dist/ClaudeUsage.app  (~36 MB)
```

Then install it and set it to start at login:

```sh
./install.sh          # copies to /Applications, registers a hidden login item, launches it
```

(approve the one-time "control System Events" prompt, then the per-account
Keychain prompts). Equivalent manual steps: `mv dist/ClaudeUsage.app
/Applications/`, then System Settings → General → Login Items → **+** →
`ClaudeUsage.app`.

To auto-restart on crash instead, use a LaunchAgent:

```sh
mkdir -p ~/Library/LaunchAgents
cat > ~/Library/LaunchAgents/local.claude-usage.menubar.plist <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>local.claude-usage.menubar</string>
  <key>ProgramArguments</key>
  <array><string>/Applications/ClaudeUsage.app/Contents/MacOS/ClaudeUsage</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict></plist>
PLIST
launchctl load ~/Library/LaunchAgents/local.claude-usage.menubar.plist
```

`LSUIElement` is set — no Dock icon, no app menu; the menubar item is the whole
app. The bundle is ad-hoc signed; if Gatekeeper blocks it, right-click → Open
once (or `xattr -dr com.apple.quarantine /Applications/ClaudeUsage.app`).

## UI

| gesture | result |
|---|---|
| **left-click** menubar item | dashboard popover (status table + account editor + settings) |
| **right-click** / control-click | small menu: Refresh now / Open config file / Quit |
| click outside the popover | closes it |

In the dashboard's **Accounts** section: add / edit / remove accounts, toggle
enabled, pick a colour, set a per-account poll interval, and choose the config
dir (with a live "credentials found / not found" check).

## Config

`~/Library/Application Support/ClaudeUsage/config.json`, rewritten by the UI:

```json
{
  "poll_interval_seconds": 300,
  "stale_after_seconds": 1800,
  "notify_thresholds": [80, 95],
  "accounts": [
    {"id": "personal", "name": "Personal", "short": "P",
     "config_dir": "~/.claude", "enabled": true,
     "color": "#3b82f6", "poll_interval_seconds": null}
  ]
}
```

A crossing of a `notify_thresholds` value (per account, per 5h/7d window) fires
one system notification; a blocked account fires one too.

## Project layout

```
claude_usage/
  __main__.py     entry point ( --once | menubar app )
  config.py       config.json load/save, Account model, defaults
  credentials.py  Keychain read + sha256 service-name derivation
  poller.py       the API call + header parsing -> Snapshot
  scheduler.py    background poll loop, per-account intervals
  state.py        view model: menubar title, dashboard payload, severity
  menubar.py      rumps app: title, notifications, status-item click handling
  window.py       NSPopover + WKWebView dashboard, JSON bridge, NSOpenPanel
  ui/dashboard.html   status table + account editor (no framework)
app_main.py       top-level launcher for py2app
setup.py          py2app config (LSUIElement, bundles ui/)
build.sh          one-shot standalone build
verify.sh         standalone shell check of the whole credential->API->headers path
docs/design.md    design notes + reverse-engineered API reference
```

## Notes / limits

- Keychain service-name derivation and the `anthropic-ratelimit-unified-*` header
  names are reverse-engineered; a Claude Code update could change them. The
  default `~/.claude` also has the legacy un-suffixed `Claude Code-credentials`
  entry as a fallback, and the parser treats every header as optional.
- The OAuth call depends on sending the "You are Claude Code…" system prompt; if
  Anthropic tightens OAuth-token scoping this route breaks.
- Header sets vary by state: a healthy account returns `5h-*` / `7d-*`; a
  `rejected` one returns only `overage-*` + a reason (e.g. `org_spend_cap_reached`).
- Keep the poll interval at minutes, not seconds.
