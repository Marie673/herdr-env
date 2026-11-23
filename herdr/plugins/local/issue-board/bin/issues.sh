#!/bin/bash
# issue 一覧を fzf の行にして出す。1行 = "<番号>\t<表示>"
#   issues.sh [--fresh] [--json] [--cached]
#     --fresh   キャッシュ（リポジトリごと・全一覧で共有、30秒）を使わず gh を引き直す
#     --json    キャッシュの JSON をそのまま出す（GUI のパネル issues-env 用）。取れていなければ終了コード1
#     --cached  古くても gh を呼ばない
# ワークツリーのブランチ名に番号が入っている issue には ● を付ける（着手済みの目印）
# ラベルは GitHub の色のチップにしてタイトルの前に出す（幅が狭くても切れないように）
set -u
root="${ISSUE_BOARD_REPO_ROOT:-$PWD}"
cache_dir="$HOME/.local/state/herdr/plugins/issue-board"
mkdir -p "$cache_dir"
cache="$cache_dir/issues-$(printf '%s' "$root" | shasum | cut -c1-12).json"

fresh=0 json=0 cached=0
for a in "$@"; do
  case "$a" in --fresh) fresh=1 ;; --json) json=1 ;; --cached) cached=1 ;; esac
done

age=999999
[ -f "$cache" ] && age=$(( $(date +%s) - $(stat -f %m "$cache") ))
if [ "$fresh" = 1 ] || { [ "$age" -gt 30 ] && [ "$cached" = 0 ]; }; then
  if out=$(cd "$root" && gh issue list --state open --limit 200 \
             --json number,title,labels,author,updatedAt 2>"$cache.err"); then
    printf '%s' "$out" > "$cache.tmp" && mv "$cache.tmp" "$cache"
  else
    err=$(head -c 500 "$cache.err")
  fi
  rm -f "$cache.err"
fi
if [ "$json" = 1 ]; then
  [ -f "$cache" ] || { printf '%s\n' "${err:-issue を取得できません（gh auth status を確認）}" >&2; exit 1; }
  cat "$cache"
  exit 0
fi
if [ ! -f "$cache" ]; then
  printf -- '-\t(issue を取得できません。gh auth status を確認)\n'
  exit 0
fi

branches=$(git -C "$root" worktree list --porcelain 2>/dev/null | sed -n 's#^branch refs/heads/##p')
jq -r --arg br "$branches" '
  def chip: (.color // "888888") as $h
    | ([$h | ascii_downcase | explode[] | if . >= 97 then . - 87 else . - 48 end]) as $d
    | [($d[0] * 16 + $d[1]), ($d[2] * 16 + $d[3]), ($d[4] * 16 + $d[5])] as $rgb
    | (if ($rgb[0] * 299 + $rgb[1] * 587 + $rgb[2] * 114) / 1000 > 140 then "30" else "97" end) as $fg
    | "\u001b[48;2;\($rgb[0]);\($rgb[1]);\($rgb[2]);\($fg)m \(.name) \u001b[0m";
  def chips($ls): if ($ls | length) > 0 then ($ls | map(chip) | join(" ")) + " " else "" end;
  ($br | split("\n") | map(select(. != ""))) as $b
  | .[]
  | (.number | tostring) as $n
  | ([$b[] | select(test("(^|[^0-9])" + $n + "($|[^0-9])"))] | length > 0) as $wip
  | "\($n)\t"
    + (if $wip then "\u001b[33m●\u001b[0m " else "  " end)
    + "\u001b[36m#\($n)\u001b[0m " + chips(.labels // []) + .title
' "$cache"
