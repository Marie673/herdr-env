#!/bin/bash
# 一覧のモード（issue / pr）。一覧ペインごとに state のファイルで持つ
#   mode.sh get | toggle | reset | prompt | header
#   mode.sh switch   切り替えて、fzf の transform に渡す操作の列を出す
f="${HERDR_PLUGIN_STATE_DIR:-$HOME/.local/state/herdr/plugins/issue-board}/mode-${BOARD_ID:-default}"
f="${f//:/_}"
mode=$(cat "$f" 2>/dev/null || echo issue)
case "${1:-get}" in
  get)    echo "$mode" ;;
  reset)  rm -f "$f" ;;
  switch)
    "$0" toggle
    printf 'change-prompt(%s)+change-header(%s)+clear-query+reload(%s)+first' \
      "$("$0" prompt)" "$("$0" header)" "$(dirname "$0")/list.sh" ;;
  toggle) [ "$mode" = issue ] && echo pr > "$f" || echo issue > "$f" ;;
  prompt) [ "$mode" = pr ] && echo 'pr> ' || echo 'issue> ' ;;
  header)
    if [ "$mode" = pr ]; then
      echo '↵ space へ  ^o ブラウザ  ^r 更新  ^p 本文  ^t issue へ'
    else
      echo '↵ 着手  ^o ブラウザ  ^r 更新  ^p 本文  ^t PR へ'
    fi ;;
esac
