#!/usr/bin/env bash
# PR のブランチの space へ移る。space が無ければ、確認してから gwt で開く（ローカルにブランチが無ければ origin から取る）
set -u
dir="$(cd "$(dirname "$0")" && pwd)"
n="${1:-}"
case "$n" in ''|*[!0-9]*) exit 0 ;; esac
root="${ISSUE_BOARD_REPO_ROOT:?}"
cd "$root" || exit 1
pause() { printf '\n%s' "${1:-Enter で一覧に戻ります}"; read -r _; }

pr=$("$dir/prs.sh" --json | jq -c --argjson n "$n" '.[] | select(.number == $n)')
[ -n "$pr" ] || { pause "PR #$n が一覧に見つかりません。^r で取り直してください"; exit 0; }
branch=$(printf '%s' "$pr" | jq -r .headRefName)
title=$(printf '%s' "$pr" | jq -r .title)

path=$(git worktree list --porcelain | awk -v b="branch refs/heads/$branch" '/^worktree /{p=substr($0,10)} $0==b{print p}')
if [ -n "$path" ]; then
  ws=$(herdr workspace list | jq -r --arg p "$path" '.result.workspaces[] | select(.worktree.checkout_path == $p) | .workspace_id' | head -1)
  if [ -n "$ws" ]; then
    herdr workspace focus "$ws" >/dev/null
    exit 0
  fi
fi

clear
printf '\033[36m#%s\033[0m %s\n\nブランチ %s の space はまだ開いていません。\n' "$n" "$title" "$branch"
if [ "$(printf '%s' "$pr" | jq -r .isCrossRepository)" = true ]; then
  pause "フォークからの PR なので開けません（gh pr checkout $n で取ってください）。Enter で戻ります"
  exit 0
fi
printf '  [a] Claude Code つきで開く   [s] シェルだけで開く（見るだけ）   [Enter] やめる\n> '
read -r a
case "$a" in
  a|A) opt=() ;;
  s|S) opt=(--no-agent) ;;
  *) exit 0 ;;
esac
if ! git show-ref --verify --quiet "refs/heads/$branch"; then
  git fetch -q origin "$branch" && git branch -q --track "$branch" "origin/$branch" \
    || { pause "origin/$branch を取れませんでした"; exit 0; }
fi
# 依頼元をこの一覧ペインにしないよう HERDR_PANE_ID を外す
env -u HERDR_PANE_ID zsh -ic 'gwt "$@"' zsh "$branch" "${opt[@]}" || pause "失敗しました。Enter で一覧に戻ります"
