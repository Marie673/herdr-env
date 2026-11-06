#!/bin/bash
# issue 一覧ペイン（issue-board プラグインの board）。fzf を抜けても開き直すので、ペインは閉じない。
# 閉じるときはプラグインアクション issue-board.toggle（その space では自動で開かなくなる）
set -u
dir="$(cd "$(dirname "$0")" && pwd)"
export ISSUE_BOARD_REPO_ROOT="${ISSUE_BOARD_REPO_ROOT:-$PWD}"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"
export FZF_DEFAULT_COMMAND="$dir/issues.sh"
cd "$ISSUE_BOARD_REPO_ROOT" || exit 1
printf '\033]0;Issues\007'
# ペインが閉じたら（SIGHUP）fzf と更新ループも一緒に止める
trap 'trap - EXIT HUP TERM; kill 0 2>/dev/null' EXIT HUP TERM

while :; do
  fzf --ansi --listen --layout reverse --info inline-right \
    --delimiter '\t' --with-nth '2..' --id-nth 1 --track \
    --prompt 'issue> ' \
    --header '↵ 着手  ^o ブラウザ  ^r 更新  ^p 本文' \
    --preview "$dir/preview.sh {1}" \
    --preview-window 'down,55%,wrap,border-top' \
    --bind "start:execute-silent($dir/refresh-loop.sh 180 >/dev/null 2>&1 &)" \
    --bind "ctrl-r:reload($dir/issues.sh --fresh)" \
    --bind "ctrl-o:execute-silent(gh issue view {1} --web >/dev/null 2>&1)" \
    --bind "ctrl-p:toggle-preview" \
    --bind "enter:execute($dir/start.sh {1})+reload($dir/issues.sh --fresh)" \
    --bind "esc:clear-query"
  sleep 0.3
done
