# 多帳號 Claude 用量選單列工具 — 設計筆記

> 做法:讀各帳號的 Keychain OAuth token,對 `POST /v1/messages` 打一次 1-token
> 呼叫,解析 response 的 `anthropic-ratelimit-unified-*` header。
>
> **§1 的內容已用專案根目錄的 `verify.sh` 實測過(2026-09-04);程式碼實作見
> `claude_usage/`。本文件保留作為設計與 API 逆向工程的參考。**

## 選型結論

| 項目 | 決定 |
|---|---|
| 用量來源 | `POST /v1/messages` 1-token 呼叫 → 解析 `anthropic-ratelimit-unified-*` response header |
| 外殼 | Python + [rumps](https://github.com/jaredks/rumps) menubar app |
| Token 過期(401) | 背景緒**自動試一次** `credentials.refresh()`(跑一個小小的 `claude -p` 讓 Claude Code 用 refresh token 換新的 access token 寫回 Keychain),成功就馬上重打一輪。失敗會退避 `_REFRESH_RETRY_COOLDOWN`(15 分鐘)再試,期間該帳號顯示上次快取值 + `⚠ 需重新登入`。手動的「Refresh token」按鈕仍在。原始設計是「只讀不寫」,見 §9 |
| 輪詢間隔 | 預設 5 分鐘,可設定 |

---

## 1. 已驗證的事實(verify.sh 輸出)

### 1a. 憑證位置與讀法

macOS 上憑證存在 **Keychain**(無 `.credentials.json` 檔案)。每個 `CLAUDE_CONFIG_DIR` 對應一個 generic-password entry:

```
service = "Claude Code-credentials-" + sha256(<config dir 絕對路徑>) 前 8 個 hex 字元
account = $USER
```

實測:
- `sha256("/Users/carbon/.claude")[:8]` = `c5f84148` → `Claude Code-credentials-c5f84148`(personal)
- `sha256("/Users/carbon/.claude-work")[:8]` = `5fc49545` → `Claude Code-credentials-5fc49545`(work)

取值:`security find-generic-password -s "<service>" -w` → 一段 JSON:

```json
{
  "claudeAiOauth": {
    "accessToken": "sk-ant-oat01-…",          // 108 chars
    "refreshToken": "sk-ant-ort01-…",
    "expiresAt": 1788536863161,                // 毫秒 epoch
    "scopes": ["user:inference", "user:profile", "user:sessions:claude_code", ...],
    "subscriptionType": "max"
  }
}
```

備援:Keychain 另有一個**無 hash 尾碼**的 `Claude Code-credentials`(舊格式),實測仍與 personal 同步更新 → 可當 personal 的 fallback。Keychain 裡還有數十個 `Claude Code-credentials-<其他hash>` 是歷史殘留,**忽略**,只用上面公式算出來的那一個。

第一次讀會跳 macOS 授權視窗,按「一律允許」。

### 1b. API 呼叫

```
POST https://api.anthropic.com/v1/messages
Headers:
  authorization: Bearer <accessToken>
  anthropic-version: 2023-06-01
  anthropic-beta: oauth-2025-04-20
  content-type: application/json
Body:
  {
    "model": "claude-haiku-4-5-20251001",
    "max_tokens": 1,
    "system": "You are Claude Code, Anthropic's official CLI for Claude.",
    "messages": [{"role": "user", "content": "."}]
  }
```

- `system` 那句 spoof prompt **必要**(OAuth token 只授權給 Claude Code 用途);省略大概率被擋
- personal 帳號實測回 `HTTP/2 200`(健康狀態)
- work 帳號實測(重新登入後 token 新的)回 `HTTP/2 429` —— 該 org 撞到每月支出上限,`anthropic-ratelimit-unified-overage-disabled-reason: org_spend_cap_reached`,鎖到 `reset` = 2026-10-01
- token 過期時回 `HTTP/2 401`,**不帶任何 ratelimit header**

### 1c. Response header

**⚠ header 集合會依帳號狀態變動,每個欄位都要當 optional。**

#### 健康狀態(personal 實測,`status: allowed`)

| header | 範例值 | 意義 / 格式 |
|---|---|---|
| `anthropic-ratelimit-unified-5h-utilization` | `0.32` | 5 小時 session 用量,**float 0..1** |
| `anthropic-ratelimit-unified-5h-reset` | `1788523200` | **unix epoch 秒** |
| `anthropic-ratelimit-unified-5h-status` | `allowed` | |
| `anthropic-ratelimit-unified-7d-utilization` | `0.42` | 7 天用量,float 0..1 |
| `anthropic-ratelimit-unified-7d-reset` | `1788602400` | unix 秒 |
| `anthropic-ratelimit-unified-7d-status` | `allowed` | |
| `anthropic-ratelimit-unified-status` | `allowed` | 整體狀態 |
| `anthropic-ratelimit-unified-reset` | `1788523200` | 綁定視窗的 reset(此例 = 5h) |
| `anthropic-ratelimit-unified-representative-claim` | `five_hour` | 目前綁定(較緊)的視窗 |
| `anthropic-ratelimit-unified-overage-utilization` | `0.0` | Max 加購額度桶 |
| `anthropic-ratelimit-unified-overage-status` | `allowed` | |
| `anthropic-ratelimit-unified-overage-reset` | `1790812800` | unix 秒 |
| `anthropic-ratelimit-unified-fallback-percentage` | `0.5` | 用途不明,忽略 |
| `anthropic-organization-id` | `da2a…` | 區分帳號 |
| `request-id` | `req_…` | debug |

#### 封鎖狀態(work 實測,`HTTP 429` / `status: rejected`)

**`5h-*` 與 `7d-*` 的 utilization / reset 完全不出現**,只有:

| header | 範例值 | 意義 |
|---|---|---|
| `anthropic-ratelimit-unified-status` | `rejected` | 被擋 |
| `anthropic-ratelimit-unified-representative-claim` | `overage` | 綁定在 overage 桶 |
| `anthropic-ratelimit-unified-reset` | `1790812800` | 解封時間(unix 秒) |
| `anthropic-ratelimit-unified-overage-status` | `rejected` | |
| `anthropic-ratelimit-unified-overage-utilization` | `1.0` | |
| `anthropic-ratelimit-unified-overage-surpassed-threshold` | `1.0` | |
| `anthropic-ratelimit-unified-overage-disabled-reason` | `org_spend_cap_reached` | 封鎖原因(顯示給使用者) |
| `anthropic-ratelimit-unified-upgrade-paths` | `overage` | |
| `retry-after` | `2303362` | 秒;此例 ≈ 26 天 |
| `x-should-retry` | `true` | |

**解析規則**:`utilization * 100` = 百分比;`reset` 直接 `datetime.fromtimestamp(int(v))`;缺欄位一律 `None`。
- `status == "rejected"` 且無 `5h-utilization` → 該視窗顯示「🔴 封鎖」,原因取 `overage-disabled-reason`,解封時間取 `-reset` / `retry-after`
- `representative-claim` 標出目前是哪個視窗在卡

---

## 2. 架構

```
claude_usage/
├── app.py         # rumps:選單列標題、下拉、timer + 背景緒、門檻通知
├── poller.py      # 對一個帳號:讀 token → 打 API → 解析 header → UsageSnapshot
├── credentials.py # sha256 公式算 service → security find-generic-password → accessToken/expiresAt
├── cache.py       # ~/.cache/claude-usage/<account>.json:上次成功結果落地
├── config.py      # ~/.config/claude-usage/config.toml:帳號清單、間隔、門檻
└── models.py      # UsageSnapshot / WindowUsage dataclass
```

執行流(每 `poll_interval` 一輪,跑在 `threading.Thread`):
每帳號 → `credentials.load()` → 若 `now >= expiresAt` 直接 `state="expired"`(不打 API)→ 否則 `poller.fetch()` → 200 就解析 + 寫 cache;401/其他就沿用 cache + 標記 → 回主緒重繪。

---

## 3. `credentials.py`

```python
import hashlib, json, subprocess, os

def keychain_service(config_dir: str) -> str:
    abspath = os.path.realpath(os.path.expanduser(config_dir))
    return "Claude Code-credentials-" + hashlib.sha256(abspath.encode()).hexdigest()[:8]

def load(config_dir: str):
    for svc in (keychain_service(config_dir), "Claude Code-credentials"):  # 第二個只對 default dir 有意義
        try:
            raw = subprocess.run(
                ["security", "find-generic-password", "-s", svc, "-w"],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
            if not raw:
                continue
            oauth = json.loads(raw)["claudeAiOauth"]
            return {
                "access_token": oauth["accessToken"],
                "expires_at": oauth.get("expiresAt", 0) / 1000,  # → 秒
                "service": svc,
            }
        except Exception:
            continue
    return None
```

- 只有 `config_dir` 是預設 `~/.claude` 時才試無尾碼那個備援(其他帳號試了也是撈到 personal 的,要擋掉)

---

## 4. `poller.py`

```python
import json, time, urllib.request

API = "https://api.anthropic.com/v1/messages"
BODY = json.dumps({
    "model": "claude-haiku-4-5-20251001",
    "max_tokens": 1,
    "system": "You are Claude Code, Anthropic's official CLI for Claude.",
    "messages": [{"role": "user", "content": "."}],
}).encode()

WINDOWS = {"five_hour": "5h", "seven_day": "7d"}

def fetch(access_token: str) -> dict:
    req = urllib.request.Request(API, data=BODY, method="POST", headers={
        "authorization": f"Bearer {access_token}",
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "oauth-2025-04-20",
        "content-type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            h = {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return {"state": "expired"}
        h = {k.lower(): v for k, v in (e.headers or {}).items()}  # 429 仍可能帶 header
        if not any(k.startswith("anthropic-ratelimit-unified") for k in h):
            return {"state": f"http_{e.code}"}
    except Exception:
        return {"state": "offline"}

    def win(prefix):
        u = h.get(f"anthropic-ratelimit-unified-{prefix}-utilization")
        rs = h.get(f"anthropic-ratelimit-unified-{prefix}-reset")
        return {
            "pct": round(float(u) * 100, 1) if u is not None else None,
            "reset": int(rs) if rs else None,
            "status": h.get(f"anthropic-ratelimit-unified-{prefix}-status"),
        }

    overall = h.get("anthropic-ratelimit-unified-status")        # allowed / rejected
    return {
        "state": "blocked" if overall == "rejected" else "ok",
        "fetched_at": time.time(),
        "overall_status": overall,
        "five_hour": win("5h"),      # rejected 時整包會是 None
        "seven_day": win("7d"),
        "representative": h.get("anthropic-ratelimit-unified-representative-claim"),  # 5h/7d/overage
        "overage_pct": _pct(h.get("anthropic-ratelimit-unified-overage-utilization")),
        "blocked_reason": h.get("anthropic-ratelimit-unified-overage-disabled-reason"),  # e.g. org_spend_cap_reached
        "unblock_at": int(h["anthropic-ratelimit-unified-reset"]) if h.get("anthropic-ratelimit-unified-reset") else None,
    }

def _pct(v):
    return round(float(v) * 100, 1) if v is not None else None
```

**實測到的兩種回應形狀**:

| 帳號 | HTTP | `unified-status` | 帶了什麼 |
|---|---|---|---|
| personal | 200 | `allowed` | `5h-*`、`7d-*`、`overage-*` 全套 |
| work | 429 | `rejected` | **沒有 `5h-*` / `7d-*`**;只有 `status`、`representative-claim: overage`、`overage-*`、`overage-disabled-reason: org_spend_cap_reached`、`retry-after`、`reset` |

→ 每個欄位都當 optional;`5h/7d` 的整組可能不存在。

錯誤處理彙整:

| 狀況 | `state` | app 行為 |
|---|---|---|
| 200 `allowed` | `ok` | 寫 cache、正常顯示 5h / 7d % |
| 429 `rejected`(overage / spend cap) | `blocked` | 顯示 `🔴 封鎖`,tooltip 帶 `blocked_reason`,下拉顯示 `unblock_at` 倒數;仍寫 cache |
| token 已過期(打之前 `now >= expires_at` 就判定) | `expired` | 背景緒先自動試一次 token refresh;成功就重打一輪,失敗才顯示 cache 舊值 + `⚠`(15 分鐘內不再重試) |
| 401 | `expired` | 同上 |
| 連線失敗 / timeout | `offline` | 顯示 cache 舊值 + `~` |
| 其他 HTTP | `http_<code>` | 顯示 cache 舊值 + `~`,下拉顯示 code |

---

## 5. `config.py`

`~/.config/claude-usage/config.toml`(缺就用內建預設):

```toml
poll_interval_seconds = 300
stale_after_seconds = 1800          # 超過就在數字旁加 "~"
notify_thresholds = [80, 95]        # 跨越門檻發一次系統通知

[[accounts]]
name = "Personal"
short = "P"
config_dir = "~/.claude"

[[accounts]]
name = "Work"
short = "W"
config_dir = "~/.claude-work"
```

---

## 6. `app.py`(rumps）

### 選單列標題

```
P 42% · W 30%
```

- 每帳號取 `max(5h%, 7d%)`
- 前綴 emoji:`<80%` 無 · `80–95%` 🟠 · `≥95%` 🔴 · `blocked`(rejected)🔴 · `offline/http error` 加 `~` · `expired` 加 `⚠`
- `state == "blocked"` 沒有 5h/7d 數字 → 顯示 `W 封鎖`
- cache 超過 `stale_after` → 數字加 `~`
- 沒 cache 又抓不到 → `—`

### 下拉

```
Personal — 5h  32%   ↻ 3h52m        (下拉每行可點 → 什麼都不做,純顯示)
           7d  42%   ↻ 25h
           updated 14:32
Work     — 🔴 封鎖:org_spend_cap_reached
           解封 2026-10-01（約 26 天）
           updated 16:10
────────
Refresh now            ← 立即觸發一輪(每個健康帳號花 ~1 token)
Open config
Quit
```

### timer / 執行緒

- `@rumps.timer(poll_interval)` → 丟 `threading.Thread` 跑 `poller.fetch`,結果用 `rumps.Timer` 或 `PyObjC performSelectorOnMainThread` 回主緒更新 UI
- 啟動先立刻跑一輪

### 門檻通知

- app 記憶體存「上次已通知的門檻」;`這次 ≥ 門檻 且 上次 < 門檻` 才 `rumps.notification()`
- 用量回落到門檻下 → 清旗標
- 5h 與 7d 各自獨立判斷

---

## 7. 打包 / 開機啟動

- MVP:`python3 -m claude_usage`(venv 或 pipx)
- 正式:`py2app` 打成 `.app`,`Info.plist` 設 `LSUIElement=1`(不進 Dock);丟「登入項目」自動啟動
- 依賴:`rumps`(唯一外部依賴;其餘用標準庫 `urllib` / `tomllib`(3.11+)/ `subprocess`)

---

## 8. 里程碑

1. ~~確認憑證位置、header 名稱與格式(含 200 與 429/rejected 兩種形狀)~~ ✅ 已由 `verify.sh` 完成
2. `credentials.py` + `poller.py`:CLI 跑通「讀 token → 打 API → 印 `UsageSnapshot`」(personal = ok、work = blocked)
3. 加 `config.py` 雙帳號迴圈;涵蓋 `ok` / `blocked` / `expired` / `offline` 分支
4. `cache.py`:落地上次成功值 + offline/expired fallback
5. `app.py`:rumps 標題 + 下拉 + 「Refresh now」+ timer/背景緒
6. 門檻通知(跨越才發、可回落、5h/7d 獨立)
7. `py2app` 打包 + 登入項目
8. (選用)`overage` 顯示、`representative-claim` 高亮綁定視窗、per-account 錯誤獨立呈現

---

## 9. 風險與注意

- OAuth token 儲存格式 / Keychain service 命名 / header 名稱都非官方穩定介面,可能隨版本變 → 解析層容錯(缺欄位 `None`)、`credentials.py` 保留 fallback
- **header 集合會依帳號狀態變** —— 健康帳號有 `5h-*`/`7d-*`,rejected 帳號只有 `overage-*`。不能假設任何欄位一定存在
- **work 這種不常開的帳號,token 會過期**;現在背景緒會自動試一次 refresh(跑一個 `claude -p`),refresh token 本身還有效就能救回來。refresh token 也失效時才會卡在舊值,等使用者跑 `claude auth login`
- work 帳號目前實測是 `org_spend_cap_reached`(Console org 每月支出上限),鎖到 2026-10-01;這也代表 work 的 Claude Code 現在應該也在被擋
- spoof system prompt 是必要條件;哪天 Anthropic 收緊 OAuth token 用途檢查,這條路可能整個失效
- 純本機、5 分鐘一次、每次約 1 token(見 `menubar-plan` 討論),成本與觸發異常存取判定的風險都可忽略;不要把間隔調到秒級
- 第一次跑會有 Keychain 授權視窗,選「一律允許」
