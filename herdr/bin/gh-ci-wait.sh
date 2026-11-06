#!/usr/bin/env bash
# PR の CI（GitHub の checks）が全部終わるまで待ち、結果を1回だけ出して終わる。
#
# Claude Code がその場で書く `until [ "$(gh run list …)" = 0 ]; do sleep; done` は、
# gh が一度失敗すると空文字になって永久に回り、push 直後は前のコミットの結果で
# 即終了し、待ち印（herdr-wait）も消し忘れる。その3つをここで塞ぐ。
#
# 使い方:
#   gh-ci-wait [<PR番号>] [-R owner/repo] [--sha <SHA>] [--timeout 60m] [--label "..."]
#
#   <PR番号>    省略時は今のブランチの PR
#   --sha       この SHA の checks を待つ。省略時は、今のブランチが PR のブランチで
#               push 済みならローカルの HEAD、それ以外は PR の head
#   --timeout   全体の上限（秒、または 30m / 2h）。既定 60m
#   --interval  問い合わせ間隔（秒）。既定 20
#   --label     herdr サイドバーに出す文言。既定 "PR #<番号> CI 待ち"
#   --no-mark   herdr-wait の印を立てない
#
# 待っている間は herdr-wait の印を、このプロセスの PID つきで立てる。
# 正常終了でも異常終了でも印は消え、kill -9 されても PID が消えた時点で無効になる。
#
# 終了コード: 0 全部成功（skip 含む） / 1 失敗・キャンセルあり / 2 時間切れ
#             3 gh の失敗が続いた / 4 checks が出ない・head が合わない・head が進んだ

set -u

PR=""
REPO=""
SHA=""
TIMEOUT=3600
INTERVAL=20
LABEL=""
MARK=1
GRACE=300      # SHA が PR に載る / checks が1件でも出るまでの猶予
SETTLE=60      # checks が出てから、全部終わったと言ってよいまでの最短時間
MAX_ERRORS=6   # gh の連続失敗をここまでは一時的なものとして流す

usage() {
  sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'
}

to_seconds() {
  case "$1" in
    *h) echo $(( ${1%h} * 3600 )) ;;
    *m) echo $(( ${1%m} * 60 )) ;;
    *s) echo "${1%s}" ;;
    *)  echo "$1" ;;
  esac
}

while [ $# -gt 0 ]; do
  case "$1" in
    -R|--repo)  REPO="${2:-}"; shift 2 ;;
    --sha)      SHA="${2:-}"; shift 2 ;;
    --timeout)  TIMEOUT="$(to_seconds "${2:-}")"; shift 2 ;;
    --interval) INTERVAL="$(to_seconds "${2:-}")"; shift 2 ;;
    --label)    LABEL="${2:-}"; shift 2 ;;
    --no-mark)  MARK=0; shift ;;
    -h|--help)  usage; exit 0 ;;
    -*)         echo "gh-ci-wait: 不明なオプション: $1" >&2; exit 64 ;;
    *)          PR="$1"; shift ;;
  esac
done

for v in "$TIMEOUT" "$INTERVAL"; do
  case "$v" in ''|*[!0-9]*) echo "gh-ci-wait: 時間の指定が読めません: $v" >&2; exit 64 ;; esac
done

gh_repo() {
  if [ -n "$REPO" ]; then gh "$@" -R "$REPO"; else gh "$@"; fi
}

log() {
  printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"
}

if [ -z "$PR" ]; then
  PR="$(gh_repo pr view --json number -q .number 2>/dev/null)" || true
  if [ -z "$PR" ]; then
    echo "gh-ci-wait: 今のブランチに PR が見つかりません。PR 番号を指定してください" >&2
    exit 64
  fi
fi

info="$(gh_repo pr view "$PR" --json headRefName,headRefOid,url 2>&1)" || {
  echo "gh-ci-wait: PR #$PR を取得できません: $info" >&2
  exit 3
}
head_ref="$(printf '%s' "$info" | jq -r .headRefName)"
pr_url="$(printf '%s' "$info" | jq -r .url)"

if [ -z "$SHA" ]; then
  SHA="$(printf '%s' "$info" | jq -r .headRefOid)"
  # 手元で今 push したばかりなら、PR の head がまだ古いことがある。手元の HEAD を待つ
  if [ -z "$REPO" ] && [ "$(git rev-parse --abbrev-ref HEAD 2>/dev/null)" = "$head_ref" ]; then
    local_head="$(git rev-parse HEAD 2>/dev/null)"
    if [ -n "$local_head" ] && [ "$local_head" = "$(git rev-parse '@{u}' 2>/dev/null)" ]; then
      SHA="$local_head"
    fi
  fi
fi

[ -n "$LABEL" ] || LABEL="PR #$PR CI 待ち"

# サイドバーの PR 状態（pr-status プラグインの $ci / $rv）を、この待ちの進み具合に合わせて取り直す。
# 別リポジトリ（-R）を待っているときは、手元の space とは関係ないので触らない。
refresh_pr_status() {
  [ -z "$REPO" ] && command -v herdr-pr-status >/dev/null 2>&1 || return 0
  (herdr-pr-status sync --path "$PWD" --fresh >/dev/null 2>&1 &)
}

on_exit() {
  refresh_pr_status
  if [ "$MARK" = 1 ] && command -v herdr-wait >/dev/null 2>&1; then
    herdr-wait clear --if-pid $$ >/dev/null 2>&1 || true
  fi
}

if [ "$MARK" = 1 ] && command -v herdr-wait >/dev/null 2>&1; then
  herdr-wait set --pid $$ "$LABEL" >/dev/null 2>&1 || true
fi
trap on_exit EXIT
trap 'exit 130' INT TERM HUP

log "PR #$PR ($pr_url) の ${SHA:0:9} を待ちます（上限 $((TIMEOUT / 60))分）"

started=$(date +%s)
sha_seen_at=""
checks_seen_at=""
errors=0
prev_summary=""
prev_pending=""
checks_json="[]"

summarize() {
  printf '%s' "$1" | jq -r '
    group_by(.bucket) | map("\(.[0].bucket) \(length)") | join(" / ")'
}

report() {
  local json="$1"
  echo
  echo "== PR #$PR ${SHA:0:9}: $(summarize "$json")"
  printf '%s' "$json" | jq -r '
    map(select(.bucket == "fail" or .bucket == "cancel" or .bucket == "pending"))
    | sort_by(.bucket)
    | .[] | "  \(.bucket)\t\(.workflow) / \(.name)\t\(.link)"'
}

while :; do
  now=$(date +%s)
  if [ $((now - started)) -ge "$TIMEOUT" ]; then
    report "$checks_json"
    log "時間切れ（$((TIMEOUT / 60))分）。まだ終わっていない checks があります"
    exit 2
  fi

  # 1. PR の head が待ちたい SHA になっているか
  head="$(gh_repo pr view "$PR" --json headRefOid -q .headRefOid 2>/dev/null)"
  if [ -z "$head" ]; then
    errors=$((errors + 1))
    if [ "$errors" -ge "$MAX_ERRORS" ]; then
      log "gh の失敗が ${errors} 回続いたのでやめます"
      exit 3
    fi
    sleep "$INTERVAL"; continue
  fi
  if [ "$head" != "$SHA" ]; then
    if [ -n "$sha_seen_at" ]; then
      log "PR の head が ${head:0:9} に進みました（新しい push）。待ち直してください"
      exit 4
    fi
    if [ $((now - started)) -ge "$GRACE" ]; then
      log "PR の head が ${SHA:0:9} になりません（今は ${head:0:9}）。push 済みか確認してください"
      exit 4
    fi
    sleep "$INTERVAL"; continue
  fi
  [ -n "$sha_seen_at" ] || sha_seen_at=$now

  # 2. checks の状態
  out="$(gh_repo pr checks "$PR" --json name,workflow,state,bucket,link 2>&1)"
  if printf '%s' "$out" | jq -e 'type == "array"' >/dev/null 2>&1; then
    checks_json="$out"
    errors=0
  elif printf '%s' "$out" | grep -q 'no checks reported'; then
    checks_json="[]"
    errors=0
  else
    errors=$((errors + 1))
    if [ "$errors" -ge "$MAX_ERRORS" ]; then
      log "gh の失敗が ${errors} 回続いたのでやめます: $out"
      exit 3
    fi
    sleep "$INTERVAL"; continue
  fi

  total=$(printf '%s' "$checks_json" | jq length)
  if [ "$total" = 0 ]; then
    if [ $((now - sha_seen_at)) -ge "$GRACE" ]; then
      log "checks が1件も出ません（$((GRACE / 60))分）。ワークフローが走らない変更か、Draft の可能性があります"
      exit 4
    fi
    sleep "$INTERVAL"; continue
  fi
  [ -n "$checks_seen_at" ] || checks_seen_at=$now

  pending=$(printf '%s' "$checks_json" | jq '[.[] | select(.bucket == "pending")] | length')
  summary="$(summarize "$checks_json")"
  if [ "$summary" != "$prev_summary" ]; then
    log "$summary"
    prev_summary="$summary"
    refresh_pr_status
  fi

  # 後から登録されるワークフローを取りこぼさないよう、出始めてから SETTLE 秒は待ち、
  # 2回続けて pending 0 を見てから終わる
  if [ "$pending" = 0 ] && [ "$prev_pending" = 0 ] && [ $((now - checks_seen_at)) -ge "$SETTLE" ]; then
    report "$checks_json"
    failed=$(printf '%s' "$checks_json" | jq '[.[] | select(.bucket == "fail" or .bucket == "cancel")] | length')
    if [ "$failed" = 0 ]; then
      log "全部終わりました: 成功"
      exit 0
    fi
    log "全部終わりました: 失敗 ${failed} 件"
    exit 1
  fi
  prev_pending="$pending"
  sleep "$INTERVAL"
done
