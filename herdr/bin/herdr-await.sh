#!/bin/sh
# 任せた agent が止まる（idle / done / blocked）まで待ち、どれで止まったかを知らせる。
# Bash の run_in_background で起動すると、止まったときに完了通知が届く。
#
# 使い方: herdr-await <agent> [--timeout 2h]
# blocked のときは画面の下端も出す。選択肢や承認待ちなら、こちらで答えるか、人間に回すかを決める。
#
# 待っている間は herdr-wait の印を、このプロセスの PID つきで立てる（gh-ci-wait と同じ）。
# 相手が止まって待ちが終われば印も消えるので、依頼元が clear を忘れて working に居座ることがない。
# 同じペインで複数の agent を待っているときは、最後の1つが終わるまで印を残す。

agent="${1:-}"
[ -n "$agent" ] || { echo "usage: herdr-await <agent> [--timeout MS]" >&2; exit 2; }
shift
timeout_ms=7200000
[ "${1:-}" = "--timeout" ] && timeout_ms="${2:-$timeout_ms}"

HW="$HOME/.config/herdr/bin/herdr-wait.sh"
REG=""
if [ -x "$HW" ] && [ -n "${HERDR_PANE_ID:-}" ]; then
  REG="${XDG_STATE_HOME:-$HOME/.local/state}/herdr-wait/await/$(printf '%s' "$HERDR_PANE_ID" | tr ':/' '__')"
  mkdir -p "$REG" && printf '%s' "$agent" > "$REG/$$"
fi

# このペインで herdr-await が待っている agent の一覧（生きているものだけ）
live_waits() {
  for f in "$REG"/*; do
    [ -f "$f" ] || continue
    if kill -0 "$(basename "$f")" 2>/dev/null; then
      printf '%s\t%s\n' "$(basename "$f")" "$(cat "$f")"
    else
      rm -f "$f"
    fi
  done
}

MARK="${XDG_STATE_HOME:-$HOME/.local/state}/herdr-wait/$(printf '%s' "${HERDR_PANE_ID:-}" | tr ':/' '__')"

# 印を「生きている待ちのうち1つの PID」に付け替える。誰もいなければ外す。
remark() {
  _waits="$(live_waits)"
  if [ -z "$_waits" ]; then
    "$HW" clear --if-pid "$1" >/dev/null 2>&1
    return 0
  fi
  _pid="$(printf '%s\n' "$_waits" | head -n 1 | cut -f1)"
  _names="$(printf '%s\n' "$_waits" | cut -f2 | awk 'NR > 1 { printf "・" } { printf "%s", $0 }')"
  "$HW" set --pid "$_pid" "$_names の作業待ち" >/dev/null 2>&1
}

finish() {
  rm -f "$REG/$$"
  _owner="$(sed -n '2s/^pid //p' "$MARK" 2>/dev/null)"
  if [ -f "$MARK" ]; then
    # 手で立てた印や、gh-ci-wait など herdr-await 以外の印に置き換わっていたら触らない
    [ -z "$_owner" ] && return 0
    [ "$_owner" != "$$" ] && ! [ -f "$REG/$_owner" ] && return 0
  fi
  remark "${_owner:-$$}"
}

if [ -n "$REG" ]; then
  # gh-ci-wait など、生きている別プロセスの印はそのまま残す（ペインは待ち中のまま）
  _owner="$(sed -n '2s/^pid //p' "$MARK" 2>/dev/null)"
  if [ -n "$_owner" ] && ! [ -f "$REG/$_owner" ] && kill -0 "$_owner" 2>/dev/null; then
    :
  else
    remark "$$"
  fi
  trap 'finish' EXIT
  trap 'exit 143' TERM INT HUP
fi

out="$(herdr agent wait "$agent" --timeout "$timeout_ms" 2>&1)"
rc=$?
status="$(herdr agent get "$agent" 2>/dev/null | jq -r '.result.agent.agent_status // "unknown"')"

case "$rc:$status" in
  0:blocked)
    echo "herdr-await: $agent は blocked（承認待ちか選択肢）です。"
    herdr agent read "$agent" --source detection --lines 40 2>&1
    echo "→ 依頼の範囲内なら herdr agent send-keys $agent <key> で答える。人間の承認が要る操作は人間に確認する。"
    ;;
  0:*)
    echo "herdr-await: $agent は $status で止まりました。完了、または依頼元への質問待ちです。"
    echo "→ gwt-tasks show $agent で直近の返答を読む。"
    ;;
  *)
    echo "herdr-await: $agent の待機が終わりませんでした (rc=$rc, $status): $out"
    ;;
esac
exit 0
