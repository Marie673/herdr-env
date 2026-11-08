#!/bin/sh
# 別 space に任された agent が、人間ではなく「依頼元の agent」に質問・報告するための道具。
# 依頼元は gwt-tasks の台帳（requested_by_pane）から引く。依頼元が agent でなければ何もせず、
# 呼び出し側が従来どおり人間に聞けるよう exit 1 を返す。
#
# 使い方:
#   herdr-ask caller            依頼元 agent の pane ID を出す（無ければ exit 1）
#   herdr-ask send <本文>        依頼元へ質問・報告を送る（返答は依頼元が herdr agent prompt で返す）
#   herdr-ask hook-question     Claude の PreToolUse(AskUserQuestion) フック。依頼元がいれば deny する
#   herdr-ask hook-blocked      Claude の Notification フック。承認待ち・質問への回答待ちで止まったら依頼元へ知らせる
#
# HERDR_ASK_DRYRUN=1 なら送信せず、送る内容を標準出力に出す。

[ "${HERDR_ENV:-}" = "1" ] || exit 1
[ -n "${HERDR_PANE_ID:-}" ] || exit 1
command -v herdr >/dev/null 2>&1 || exit 1
command -v jq >/dev/null 2>&1 || exit 1

# 台帳から「自分を任せた pane」と自分の名前を引く。agent として生きている依頼元だけを採用する。
lookup() {
  row="$(gwt-tasks list --json 2>/dev/null | jq -c --arg p "$HERDR_PANE_ID" '
    [.[] | select(.pane_id == $p and (.requested_by_pane // "") != "")][0] // empty')" || return 1
  [ -n "$row" ] || return 1
  caller="$(printf '%s' "$row" | jq -r '.requested_by_pane')"
  me="$(printf '%s' "$row" | jq -r '.live_name // .agent')"
  [ -n "$caller" ] && [ "$caller" != "$HERDR_PANE_ID" ] || return 1
  herdr agent get "$caller" >/dev/null 2>&1 || return 1
}

send() {
  msg="$1"
  if [ "${HERDR_ASK_DRYRUN:-}" = "1" ]; then
    printf '→ %s\n%s\n' "$caller" "$msg"
    return 0
  fi
  herdr agent prompt "$caller" "$msg" >/dev/null 2>&1
}

footer() {
  printf '\n返答は `herdr agent prompt %s "<回答>"` で返してください。選択肢の質問は、依頼の趣旨に沿って決められるならあなたが決めて構いません。依頼にも趣旨にも書かれていない判断（破壊的な操作、費用、公開など）だけ、あなたから人間に確認してください。' "$me"
}

action="${1:-}"
[ $# -gt 0 ] && shift

case "$action" in
  caller)
    lookup || exit 1
    printf '%s\n' "$caller"
    ;;

  send)
    lookup || { echo "herdr-ask: 依頼元の agent がいません。人間に直接聞いてよいです。" >&2; exit 1; }
    body="$*"
    [ -n "$body" ] || { echo "herdr-ask: 本文が空です" >&2; exit 2; }
    send "$(printf '[依頼先 %s (%s) から]\n%s%s' "$me" "$HERDR_PANE_ID" "$body" "$(footer)")" \
      || { echo "herdr-ask: 依頼元 ${caller} に届きませんでした（承認待ち等）。人間に聞いてよいです。" >&2; exit 3; }
    echo "herdr-ask: 依頼元 ${caller} に送りました。返答は新しい入力として届くので、このターンはここで終えてください。"
    ;;

  hook-question)
    cat >/dev/null 2>&1
    lookup || exit 0
    jq -nc --arg c "$caller" '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "deny",
      permissionDecisionReason: ("この作業は agent (" + $c + ") から任されたものなので、人間には質問しません。`herdr-ask send \"<質問と選択肢、推奨案>\"` で依頼元に聞き、このターンを終えてください。返答は新しい入力として届きます。依頼元に届かなかったときだけ人間に聞いてください。")}}'
    ;;

  hook-blocked)
    input="$(cat 2>/dev/null)"
    # 承認待ち・質問への回答待ちだけ知らせる。idle_prompt（ターンを終えて60秒入力が無い）は
    # 止まっているのではなく終わっただけで、終わったことは本人が herdr-ask send で報告する。
    # 種別が無い古い Claude は従来どおり知らせる。
    kind="$(printf '%s' "$input" | jq -r '.notification_type // empty' 2>/dev/null)"
    case "$kind" in
      ""|permission_prompt|elicitation_dialog) ;;
      *) exit 0 ;;
    esac
    lookup || exit 0
    msg="$(printf '%s' "$input" | jq -r '.message // empty' 2>/dev/null)"
    [ -n "$msg" ] || exit 0
    send "$(printf '[依頼先 %s (%s) が止まっています]\n%s\n\n確認: `herdr agent read %s --source detection --lines 40`\n応答: `herdr agent send-keys %s <key>`（選択肢なら番号や enter）。\n依頼の範囲内の許可なら応答して構いません。CLAUDE.md の承認方針で人間の承認が要る操作（削除、強制 push、クラウドの変更など）は、人間に確認してから応答してください。' "$me" "$HERDR_PANE_ID" "$msg" "$me" "$me")" || true
    ;;

  *)
    echo "usage: herdr-ask caller|send <本文>|hook-question|hook-blocked" >&2
    exit 2
    ;;
esac
exit 0
