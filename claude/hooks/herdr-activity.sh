#!/bin/sh
# 自作フック（herdr 管理外）。今やっている操作を herdr サイドバーの $act トークンとして報告する。
# Herdr の GUI（Herdr.app）では、人間の返事待ちの旗と gwt の移動予約だけを扱う（下の GUI 分岐）。
# 公式の herdr-agent-state.sh は herdr の再インストールで上書きされるため、こちらに分離している。
#
# 使い方: herdr-activity.sh <tool|prompt|stop|notify|clear>
# 失敗しても Claude Code の動作を止めないよう、常に exit 0 する。

action="${1:-}"
input="$(cat 2>/dev/null)"

# TUI（herdr server のペイン）か、GUI（Herdr.app。ペインは herdr-core のターミナル）か。
# GUI はエージェントの状態も「今やっている操作」も herdr-core が自分で拾うので、ここでは
# 人間の返事待ちの旗と、gwt の「ターンが終わったらワークツリーへ移す」予約だけを扱う。
GUI=0
if [ "${HERDR_ENV:-}" = "1" ] && [ -n "${HERDR_PANE_ID:-}" ]; then
  command -v herdr >/dev/null 2>&1 || exit 0
elif [ -n "${HC_TERMINAL_ID:-}" ] && command -v hc >/dev/null 2>&1; then
  GUI=1
else
  exit 0
fi
command -v jq >/dev/null 2>&1 || exit 0

# サブエージェントの発火は無視する。メインの表示を奪って行が乱高下するのを防ぐ。
if printf '%s' "$input" | jq -e 'has("agent_id") and (.agent_id != null)' >/dev/null 2>&1; then
  exit 0
fi

HW="$HOME/.config/herdr/bin/herdr-wait.sh"
[ -x "$HW" ] || exit 0

# 最後の返答が人間への問いかけで終わっているか。Stop の入力の last_assistant_message を見て、
# 無ければトランスクリプトの末尾から拾う。末尾3行だけを見る（途中の引用で誤判定しないため）。
ends_with_question() {
  _msg="$(printf '%s' "$input" | jq -r '.last_assistant_message // empty' 2>/dev/null)"
  if [ -z "$_msg" ]; then
    _tp="$(printf '%s' "$input" | jq -r '.transcript_path // empty' 2>/dev/null)"
    [ -n "$_tp" ] && [ -f "$_tp" ] || return 1
    _msg="$(tail -n 400 "$_tp" | jq -rs '
      [.[] | select(.type == "assistant") | .message.content[]? | select(.type == "text") | .text]
      | last // empty' 2>/dev/null)"
  fi
  [ -n "$_msg" ] || return 1
  printf '%s' "$_msg" | jq -Rse '
    [split("\n")[] | gsub("^\\s+|\\s+$"; "") | select(length > 0)] | .[-3:] as $tail
    | ($tail | map(gsub("[*_`）)」』\\]]+$"; "")) ) as $t
    | ($t | any(test("[?？]$")))
      or ($t | any(test("(ますか|ですか|でしょうか|ませんか|ましょうか)[。．!！]?$")))
      or ($tail | join("\n") | test(
           "お返事|ご返答|ご判断|ご指示|教えてください|選んでください|決めてください|"
         + "(承認|確認|許可|指示)を?(ください|お願い|いただ)|"
         + "let me know|should I |shall I |would you like|do you want|which (one|option)"; "i"))
  ' >/dev/null 2>&1
}

if [ "$GUI" = 1 ]; then
  case "$action" in
    prompt) "$HW" human off >/dev/null 2>&1 ;;
    stop)
      if ends_with_question; then "$HW" human on >/dev/null 2>&1; else "$HW" human off >/dev/null 2>&1; fi
      # gwt が予約した「このペインの会話をワークツリーのスペースへ移す」を、ターンの終わりに実行する
      HG="$HOME/.config/herdr/bin/herdr-gui.sh"
      [ -x "$HG" ] && printf '%s' "$input" | "$HG" move-on-stop >/dev/null 2>&1
      ;;
    clear) "$HW" human off >/dev/null 2>&1 ;;
  esac
  exit 0
fi

wait_label="$("$HW" label 2>/dev/null)"

case "$action" in
  prompt)
    # 人間が答えた。返事待ちの旗を降ろす
    "$HW" human off >/dev/null 2>&1
    act="考え中…"
    ;;
  stop)
    if ends_with_question; then
      # 外部待ちの印が残っていても、人間の番であることを優先して見せる
      "$HW" human on >/dev/null 2>&1
      act="💬 要返答"
    else
      "$HW" human off >/dev/null 2>&1
      act="${wait_label:-待機中}"
    fi
    ;;
  notify)
    if "$HW" human status >/dev/null 2>&1; then act="💬 要返答"; else act="要確認"; fi
    ;;
  clear)
    "$HW" human off >/dev/null 2>&1
    herdr pane report-metadata "$HERDR_PANE_ID" \
      --source custom:activity --applies-to-source herdr:claude \
      --clear-token act --clear-state-labels >/dev/null 2>&1
    exit 0
    ;;
  tool)
    # サイドバー幅に収まるよう短く整形する。
    act="$(printf '%s' "$input" | jq -r '
      def clip($n): if (. | length) > $n then (.[0:$n] + "…") else . end;
      def base: sub(".*/"; "");
      .tool_name as $t | (.tool_input // {}) as $i |
      if   $t == "Bash"  then "$ " + (($i.command // "") | gsub("\\s+"; " ") | clip(36))
      elif $t == "Read"  then "読 " + (($i.file_path // $i.notebook_path // "") | base | clip(30))
      elif ($t == "Edit" or $t == "Write" or $t == "NotebookEdit")
                         then "編集 " + (($i.file_path // $i.notebook_path // "") | base | clip(28))
      elif ($t == "Grep" or $t == "Glob")
                         then "検索 " + (($i.pattern // "") | clip(28))
      elif $t == "Agent" then "委譲 " + (($i.description // "") | clip(28))
      elif $t == "Skill" then "skill " + (($i.skill // "") | clip(26))
      elif $t == "Task"  then "task " + (($i.description // $i.subject // "") | clip(26))
      elif $t == "WebFetch"  then "web " + (($i.url // "") | sub("^https?://"; "") | clip(28))
      elif $t == "WebSearch" then "web検索 " + (($i.query // "") | clip(26))
      elif ($t | startswith("mcp__"))
                         then ($t | sub("^mcp__"; "") | gsub("__"; " ") | clip(32))
      else ($t | clip(32)) end
    ' 2>/dev/null)"
    # 待ち中は、ツール実行の表示にも ⏳ を付けて「まだ用事が終わっていない」と分かるようにする。
    [ -n "$wait_label" ] && [ -n "$act" ] && act="⏳ $act"
    ;;
  *) exit 0 ;;
esac

[ -n "$act" ] || exit 0

# state_text（done / idle / working の表示文字）は herdr-wait が印と旗から決める。
# herdr のテーマには状態別の色トークンが無く、idle と done を色で分けられないので、
# 表示文字そのものを差し替えて見分けられるようにしている。
"$HW" refresh --act "$act" >/dev/null 2>&1

exit 0
