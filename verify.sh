#!/bin/bash
# verify.sh — 驗證「讀 Keychain token → 打 1-token API → 解析 ratelimit header」整條可行
# 用法: ./verify.sh          (跑兩個帳號)
#       ./verify.sh personal (只跑一個)

set -uo pipefail

ACCOUNTS=(
  "personal:$HOME/.claude"
  "work:$HOME/.claude-work"
)
MODEL="claude-haiku-4-5-20251001"
SPOOF_SYSTEM="You are Claude Code, Anthropic's official CLI for Claude."

want="${1:-}"

for entry in "${ACCOUNTS[@]}"; do
  name="${entry%%:*}"
  dir="${entry#*:}"
  [ -n "$want" ] && [ "$want" != "$name" ] && continue

  rp="$(cd "$dir" 2>/dev/null && pwd -P || echo "$dir")"
  hash="$(printf '%s' "$rp" | shasum -a 256 | cut -c1-8)"
  svc="Claude Code-credentials-$hash"

  echo "=================================================================="
  echo "[$name]  dir=$rp"
  echo "         keychain service = $svc"

  cred="$(security find-generic-password -s "$svc" -w 2>/dev/null || true)"
  src="$svc"
  if [ -z "$cred" ]; then
    cred="$(security find-generic-password -s 'Claude Code-credentials' -w 2>/dev/null || true)"
    src="Claude Code-credentials (legacy fallback)"
  fi
  if [ -z "$cred" ]; then
    echo "  !! 讀不到憑證"; echo; continue
  fi
  echo "         credential source = $src"

  read -r token expires scopes <<<"$(printf '%s' "$cred" | /usr/bin/python3 -c '
import sys, json
d = json.load(sys.stdin)["claudeAiOauth"]
print(d.get("accessToken",""), d.get("expiresAt",""), ",".join(d.get("scopes",[])))
' 2>/dev/null)"

  if [ -z "${token:-}" ]; then echo "  !! 解析 accessToken 失敗"; echo; continue; fi
  echo "         token   = ${token:0:20}… (${#token} chars)"
  echo "         scopes  = $scopes"
  if [ -n "${expires:-}" ]; then
    now_ms=$(( $(date +%s) * 1000 ))
    left=$(( (expires - now_ms) / 60000 ))
    echo "         expires = $expires  (約 ${left} 分鐘後)"
  fi

  echo "  ---- POST /v1/messages 回應 header ----"
  curl -sS -D - -o /dev/null https://api.anthropic.com/v1/messages \
    -H "authorization: Bearer $token" \
    -H "anthropic-version: 2023-06-01" \
    -H "anthropic-beta: oauth-2025-04-20" \
    -H "content-type: application/json" \
    -d "{\"model\":\"$MODEL\",\"max_tokens\":1,\"system\":\"$SPOOF_SYSTEM\",\"messages\":[{\"role\":\"user\",\"content\":\".\"}]}" \
    2>/dev/null \
  | grep -iE '^(HTTP/|anthropic-ratelimit|anthropic-organization|retry-after|x-should-retry|request-id)' \
  || echo "  (沒有符合的 header — 印完整回應偵錯:)"
  echo
done
