#!/bin/bash
# herdr-web-browser（agent 用の Chromium）に Google Chrome のログイン（cookie）を流し込み直す。
# launchd から定期的に呼ぶ（launchd/herdr-env.herdr-browser-logins.plist.in）。
#
# 何を取り込むかは ~/.config/herdr/browser-logins に書く。ファイルが無ければ何もしない（opt-in）。
#   all              Chrome のすべてのサイト
#   github.com       サイトを1行に1つ（# から後はコメント）
#   profile=Profile 1  読む Chrome のプロファイル（省略時は最後に使ったもの）
#
# - ブラウザが動いていないときは何もしない（取り込みのためにブラウザを起動しない）。
# - Chrome から消えた cookie（ログアウト）は消さない。上書きと追加だけ。
# - 取り込んだサイトでは、このブラウザを使うすべての agent があなたとして振る舞える。
set -u

CONF="${HERDR_BROWSER_LOGINS:-$HOME/.config/herdr/browser-logins}"
BIN="$(command -v herdr-web-browser || true)"

log() { printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S')" "$*"; }

[ -f "$CONF" ] || exit 0
[ -n "$BIN" ] || { log "herdr-web-browser が見つからない"; exit 0; }

all=0
profile=()
sites=()
while IFS= read -r line || [ -n "$line" ]; do
  line="${line%%#*}"
  line="$(printf '%s' "$line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  case "$line" in
    "") ;;
    all) all=1 ;;
    profile=*) profile=(--profile "${line#profile=}") ;;
    *) sites+=("$line") ;;
  esac
done <"$CONF"

if [ "$all" = 0 ] && [ "${#sites[@]}" = 0 ]; then
  exit 0
fi

# 動いていなければ飛ばす（import-logins は止まっているブラウザを起動してしまう）。
if ! "$BIN" --json status 2>/dev/null | grep -q '"running":true'; then
  exit 0
fi

if [ "$all" = 1 ]; then
  args=(--all)
else
  args=("${sites[@]}")
fi

if out="$("$BIN" import-logins "${profile[@]+"${profile[@]}"}" "${args[@]}" 2>&1)"; then
  log "$out"
else
  log "失敗: $out"
  exit 1
fi
