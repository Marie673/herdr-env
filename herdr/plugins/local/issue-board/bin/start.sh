#!/bin/bash
# 選んだ issue に着手する。ブランチ issue-<番号> を gwt で切り、新しい space の Claude Code に issue を渡す。
# 番号入りのワークツリーが既にあれば、その space へ移るだけ。
set -u
n="${1:-}"
case "$n" in ''|*[!0-9]*) exit 0 ;; esac
root="${ISSUE_BOARD_REPO_ROOT:?}"
cd "$root" || exit 1

pause() { printf '\n%s' "${1:-Enter で一覧に戻ります}"; read -r _; }

title=$(gh issue view "$n" --json title -q .title 2>/dev/null)
clear
printf '\033[36m#%s\033[0m %s\n\n' "$n" "$title"

existing=$(git worktree list --porcelain | sed -n 's#^branch refs/heads/##p' \
  | grep -E "(^|[^0-9])$n($|[^0-9])" | head -1)
if [ -n "$existing" ]; then
  path=$(git worktree list --porcelain | awk -v b="branch refs/heads/$existing" '/^worktree /{p=substr($0,10)} $0==b{print p}')
  ws=$(herdr workspace list | jq -r --arg p "$path" '.result.workspaces[] | select(.worktree.checkout_path == $p) | .workspace_id' | head -1)
  printf '既にワークツリーがあります: %s\n' "$existing"
  if [ -n "$ws" ]; then
    printf 'space %s へ移ります\n' "$ws"
    herdr workspace focus "$ws" >/dev/null
    exit 0
  fi
  printf 'space が開いていません。開きますか [y/N] '
  read -r a
  case "$a" in y*|Y*) env -u HERDR_PANE_ID zsh -ic 'gwt "$1"' zsh "$existing" || pause ;; esac
  exit 0
fi

branch="issue-$n"
printf 'ブランチ %s を作り、新しい space の Claude Code にこの issue を渡します。\n着手しますか [y/N] ' "$branch"
read -r a
case "$a" in y*|Y*) ;; *) exit 0 ;; esac

task="GitHub issue #$n「$title」に着手してください。
- まず \`gh issue view $n --comments\` で本文と議論を読む
- 方針の分かれ目があれば、実装の前に確認する
- PR の本文には \`Closes #$n\` を入れる"

# 依頼元をこの一覧ペインにしないよう HERDR_PANE_ID を外す（質問は人間に来る）
env -u HERDR_PANE_ID zsh -ic 'gwt "$1" --task "$2"' zsh "$branch" "$task" || pause "失敗しました。Enter で一覧に戻ります"
