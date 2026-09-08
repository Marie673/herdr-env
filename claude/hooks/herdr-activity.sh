#!/bin/sh
# 自作フック（herdr 管理外）。今やっている操作を herdr サイドバーの $act トークンとして報告する。
# 公式の herdr-agent-state.sh は herdr の再インストールで上書きされるため、こちらに分離している。
#
# 使い方: herdr-activity.sh <tool|prompt|stop|notify|clear>
# 失敗しても Claude Code の動作を止めないよう、常に exit 0 する。

action="${1:-}"
input="$(cat 2>/dev/null)"

[ "${HERDR_ENV:-}" = "1" ] || exit 0
[ -n "${HERDR_PANE_ID:-}" ] || exit 0
command -v herdr >/dev/null 2>&1 || exit 0
command -v jq >/dev/null 2>&1 || exit 0

# サブエージェントの発火は無視する。メインの表示を奪って行が乱高下するのを防ぐ。
if printf '%s' "$input" | jq -e 'has("agent_id") and (.agent_id != null)' >/dev/null 2>&1; then
  exit 0
fi

# 外部待ち（CodeRabbit のレビュー、CI 等）の印。立っていれば idle / done の見え方を差し替える。
# 印は herdr-wait が張り、TTL 切れなら空が返る。
wait_label=""
if [ -x "$HOME/.config/herdr/bin/herdr-wait.sh" ]; then
  wait_label="$("$HOME/.config/herdr/bin/herdr-wait.sh" label 2>/dev/null)"
fi

case "$action" in
  prompt) act="考え中…" ;;
  stop)   act="${wait_label:-待機中}" ;;
  notify) act="要確認" ;;
  clear)
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
    ;;
  *) exit 0 ;;
esac

[ -n "$act" ] || exit 0

# 待ち中は、ツール実行の表示にも ⏳ を付けて「まだ用事が終わっていない」と分かるようにする。
if [ -n "$wait_label" ] && [ "$action" = "tool" ]; then
  act="⏳ $act"
fi

# 待ち中の idle / done は「もう用は無い」ではなく「外部の返事待ち」。文言を差し替える。
if [ -n "$wait_label" ]; then
  label_done="⏳ 外部待ち"
  label_idle="⏳ 外部待ち"
else
  label_done="✅ 完了 未確認"
  label_idle="待機"
fi

# herdr のテーマには状態別の色トークンが無く、idle と done を色で分けられない。
# 代わりに状態の表示文字そのものを差し替えて見分けられるようにする。
herdr pane report-metadata "$HERDR_PANE_ID" \
  --source custom:activity --applies-to-source herdr:claude \
  --token act="$act" \
  --state-label done="$label_done" \
  --state-label idle="$label_idle" \
  --state-label working="… 実行中" \
  --state-label blocked="⚠ 要承認" \
  --state-label unknown="? 不明" >/dev/null 2>&1

exit 0
