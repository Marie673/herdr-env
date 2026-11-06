#!/usr/bin/env bash
# 選んだ issue に着手する。ブランチを gwt で切り、新しい space の Claude Code に issue を渡す。
# ブランチ名（= space 名）は、タイトルから haiku に <type>/<english-slug>-<番号> を作らせ、確認画面で直せる。
# 番号入りのワークツリーが既にあれば、その space へ移るだけ。
# read -e -i を使うので bash 4 以上（board.sh が PATH の先頭に /opt/homebrew/bin を置いている）
set -u
n="${1:-}"
case "$n" in ''|*[!0-9]*) exit 0 ;; esac
root="${ISSUE_BOARD_REPO_ROOT:?}"
cd "$root" || exit 1

pause() { printf '\n%s' "${1:-Enter で一覧に戻ります}"; read -r _; }

info=$(gh issue view "$n" --json title,labels 2>/dev/null)
title=$(printf '%s' "$info" | jq -r '.title // ""')
labels=$(printf '%s' "$info" | jq -r '[.labels[]?.name] | join(", ")')

# タイトルからブランチ名を作る。hooks やリポジトリの設定を読ませないよう、ユーザー設定を外して中立な場所から呼ぶ
suggest_branch() {
  local claude out
  claude=$(PATH="$HOME/.local/bin:$PATH" command -v claude) || return 1
  out=$(cd "${TMPDIR:-/tmp}" && printf '%s\n' "GitHub issue のタイトルから git のブランチ名を1つだけ作って、その1行だけを出力して。説明や記号は付けない。
形式: <type>/<english-kebab-slug>
- type は feat / fix / docs / refactor / chore / test のどれか（不具合やセキュリティの修正は fix、方針決めや調査のまとめは docs）
- slug は何をする変更か一目で分かる英語2〜5語。全体で40文字以内。小文字・数字・ハイフンのみ
タイトル: $title
ラベル: $labels" | timeout 30 "$claude" -p --setting-sources project --model haiku --no-session-persistence 2>/dev/null) || return 1
  out=$(printf '%s' "$out" | tr -d '`\r' | grep -E '^(feat|fix|docs|refactor|chore|test)/[a-z0-9-]+$' | head -1)
  [ -n "$out" ] || return 1
  printf '%s-%s' "${out%-}" "$n"
}
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

printf 'ブランチ名を考えています…'
suggested=$(suggest_branch) || suggested="issue-$n"
printf '\r\033[K新しい space の Claude Code にこの issue を渡します。ブランチ名（= space 名）を確認してください。\n'
printf '\033[90mEnter で決定 / 空にして Enter でやめる\033[0m\n'
while :; do
  read -r -e -i "$suggested" -p 'ブランチ: ' branch || exit 0
  [ -n "$branch" ] || exit 0
  if ! git check-ref-format --branch "$branch" >/dev/null 2>&1; then
    printf 'ブランチ名に使えない文字があります\n'; suggested="$branch"; continue
  fi
  if git show-ref --verify --quiet "refs/heads/$branch"; then
    printf '既に同じ名前のブランチがあります（そのまま使うなら gwt が既存のブランチで開きます）。続けますか [y/N] '
    read -r a
    case "$a" in y*|Y*) ;; *) suggested="$branch"; continue ;; esac
  fi
  break
done

task="GitHub issue #$n「$title」に着手してください。
- まず \`gh issue view $n --comments\` で本文と議論を読む
- 方針の分かれ目があれば、実装の前に確認する
- PR の本文には \`Closes #$n\` を入れる"

# 依頼元をこの一覧ペインにしないよう HERDR_PANE_ID を外す（質問は人間に来る）
env -u HERDR_PANE_ID zsh -ic 'gwt "$1" --task "$2"' zsh "$branch" "$task" || pause "失敗しました。Enter で一覧に戻ります"
