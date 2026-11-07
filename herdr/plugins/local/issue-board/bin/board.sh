#!/bin/bash
# issue / PR 一覧ペイン（issue-board プラグインの board）。^t で issue と PR を切り替える。
# fzf を抜けても開き直すので、ペインは閉じない。閉じるときはプラグインアクション issue-board.toggle
set -u
dir="$(cd "$(dirname "$0")" && pwd)"
export ISSUE_BOARD_REPO_ROOT="${ISSUE_BOARD_REPO_ROOT:-$PWD}"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"
export BOARD_ID="${HERDR_PANE_ID:-$$}"   # モード（issue / pr）をペインごとに持つためのキー
export FZF_DEFAULT_COMMAND="$dir/list.sh"
cd "$ISSUE_BOARD_REPO_ROOT" || exit 1
printf '\033]0;Issues\007'
"$dir/mode.sh" reset
# ペインが閉じたら（SIGHUP）fzf と更新ループも一緒に止める
trap 'trap - EXIT HUP TERM; "$dir/mode.sh" reset; kill 0 2>/dev/null' EXIT HUP TERM

while :; do
  fzf --ansi --listen --layout reverse --info inline-right \
    --delimiter '\t' --with-nth '2..' --id-nth 1 --track \
    --prompt "$("$dir/mode.sh" prompt)" \
    --header "$("$dir/mode.sh" header)" \
    --preview "$dir/preview.sh {1}" \
    --preview-window 'down,55%,wrap,border-top' \
    --bind "start:execute-silent($dir/refresh-loop.sh 60 >/dev/null 2>&1 &)" \
    --bind "ctrl-t:transform($dir/mode.sh switch)" \
    --bind "ctrl-r:reload($dir/list.sh --fresh)" \
    --bind "ctrl-o:execute-silent($dir/open.sh {1})" \
    --bind "ctrl-p:toggle-preview" \
    --bind "enter:execute($dir/act.sh {1})+reload($dir/list.sh --fresh)" \
    --bind "esc:clear-query"
  sleep 0.3
done
