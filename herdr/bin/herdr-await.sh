#!/bin/sh
# 任せた agent が止まる（idle / done / blocked）まで待ち、どれで止まったかを知らせる。
# Bash の run_in_background で起動すると、止まったときに完了通知が届く。
#
# 使い方: herdr-await <agent> [--timeout 2h]
# blocked のときは画面の下端も出す。選択肢や承認待ちなら、こちらで答えるか、人間に回すかを決める。

agent="${1:-}"
[ -n "$agent" ] || { echo "usage: herdr-await <agent> [--timeout MS]" >&2; exit 2; }
shift
timeout_ms=7200000
[ "${1:-}" = "--timeout" ] && timeout_ms="${2:-$timeout_ms}"

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
