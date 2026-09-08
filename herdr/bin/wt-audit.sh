#!/usr/bin/env bash
# gwq worktree の棚卸し。PR の状態・作業の残り・herdr space の有無を突き合わせて、
# 終わった作業のワークツリーを片付ける。
#
#   wt-audit                 一覧を出して fzf で選び、選んだものを片付ける
#   wt-audit --list          一覧を出すだけ
#   wt-audit --auto          対話なしで「片付け可」だけ片付ける（launchd 用）
#   wt-audit --auto --dry-run  何を消すつもりかだけ出す
#   wt-audit --json          機械可読な一覧
#
# オプション:
#   --repo <path>       対象リポジトリ（既定: カレント）
#   --all-repos         ghq が知っている全リポジトリを対象にする（カレントが repo 外なら既定）
#   --delete-branch     ワークツリーに加えてローカルブランチも消す
#   --stale-days <N>    PR が無いワークツリーを「放置」とみなす日数（既定 30）
#   --yes               対話の確認を飛ばす
#
# 判定:
#   片付け可  PR が merged/closed、未コミット変更なし、未 push コミットなし
#   要確認    未コミット変更・未 push コミットあり、または PR が無く N 日放置
#   生存      PR が open、または最近さわっている
#
# 安全のため、以下は --auto では絶対に消さない:
#   - 未コミット変更 / 未 push コミットがあるもの
#   - herdr の space でエージェントが working / blocked のもの
#   - 本体リポジトリ（is_main）

set -uo pipefail

STALE_DAYS=30
MODE="interactive"
DRY_RUN=0
DELETE_BRANCH=0
ASSUME_YES=0
REPO_ARG=""
ALL_REPOS=0

while [ $# -gt 0 ]; do
  case "$1" in
    --list) MODE="list"; shift ;;
    --auto) MODE="auto"; shift ;;
    --json) MODE="json"; shift ;;
    --dry-run|-d) DRY_RUN=1; shift ;;
    --delete-branch) DELETE_BRANCH=1; shift ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    --stale-days) STALE_DAYS="${2:-30}"; shift 2 ;;
    --repo) REPO_ARG="${2:-}"; shift 2 ;;
    --all-repos) ALL_REPOS=1; shift ;;
    -h|--help) sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "wt-audit: 不明なオプション: $1" >&2; exit 2 ;;
  esac
done

for c in gwq jq git; do
  command -v "$c" >/dev/null 2>&1 || { echo "wt-audit: $c が必要です" >&2; exit 1; }
done

now=$(date +%s)

# ---- 1. worktree 一覧 -------------------------------------------------------
# gwq list -g はディスク上の全ワークツリーを見る。--repo が効いていればそこだけ。
if [ -n "$REPO_ARG" ]; then
  worktrees_json=$(cd "$REPO_ARG" && gwq list --json 2>/dev/null)
elif [ "$ALL_REPOS" = 1 ]; then
  # gwq list -g はベースディレクトリ全体を舐めて 1 分以上かかる。
  # ghq が知っているリポジトリごとに引けば 1 件 0.1 秒で済む。
  worktrees_json=$(ghq list -p 2>/dev/null | while IFS= read -r r; do
      (cd "$r" 2>/dev/null && gwq list --json 2>/dev/null)
    done | jq -s 'map(select(. != null)) | add // []')
elif git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  worktrees_json=$(gwq list --json 2>/dev/null)
else
  ALL_REPOS=1
  worktrees_json=$(ghq list -p 2>/dev/null | while IFS= read -r r; do
      (cd "$r" 2>/dev/null && gwq list --json 2>/dev/null)
    done | jq -s 'map(select(. != null)) | add // []')
fi
[ -n "$worktrees_json" ] || { echo "wt-audit: ワークツリーが取れませんでした" >&2; exit 1; }

# 本体リポジトリは棚卸し対象外
worktrees=$(printf '%s' "$worktrees_json" | jq -c '[.[] | select(.is_main != true) | {branch, path}] | sort_by(.branch) | .[]')
[ -n "$worktrees" ] || { echo "対象のワークツリーはありません。"; exit 0; }

# ---- 2. herdr の space（あれば） -------------------------------------------
spaces_json='[]'
if [ "${HERDR_ENV:-}" = "1" ] || command -v herdr >/dev/null 2>&1; then
  spaces_json=$(herdr workspace list 2>/dev/null \
    | jq -c '[.result.workspaces[]? | select(.worktree.is_linked_worktree == true)
              | {id: .workspace_id, path: .worktree.checkout_path, status: .agent_status}]' 2>/dev/null)
  [ -n "$spaces_json" ] || spaces_json='[]'
fi

# ---- 3. PR 一覧（リポジトリごとに1回だけ引く） ------------------------------
# macOS 標準の bash 3.2 には連想配列が無いので、一時ファイルに貯めて引く。
PR_CACHE=$(mktemp -t wt-audit-pr)
trap 'rm -f "$PR_CACHE"' EXIT
prs_loaded=""
load_prs() {
  root="$1"
  case " $prs_loaded " in *" $root "*) return 0 ;; esac
  prs_loaded="$prs_loaded $root"
  command -v gh >/dev/null 2>&1 || return 0
  # 同じブランチに複数 PR がある場合は OPEN を優先し、無ければ番号が大きい方を残す
  (cd "$root" && gh pr list --state all --limit 1000 --json number,headRefName,state 2>/dev/null) \
    | jq -r '
        group_by(.headRefName)
        | map((map(select(.state == "OPEN")) | first) // (sort_by(.number) | last))
        | .[] | [.headRefName, (.number|tostring), .state] | @tsv
      ' >> "$PR_CACHE" 2>/dev/null
}

pr_lookup() {  # $1=branch  -> "num<TAB>state" または空
  awk -F'\t' -v b="$1" '$1 == b { print $2 "\t" $3; exit }' "$PR_CACHE" 2>/dev/null
}

# ---- 4. 1件ずつ調べる -------------------------------------------------------
rows=""   # verdict \t branch \t pr \t last \t flags \t space_id \t path
while IFS= read -r wt; do
  branch=$(printf '%s' "$wt" | jq -r '.branch')
  path=$(printf '%s' "$wt" | jq -r '.path')
  [ -d "$path" ] || continue

  root=$(git -C "$path" rev-parse --path-format=absolute --git-common-dir 2>/dev/null | sed 's|/\.git$||')
  [ -n "$root" ] && load_prs "$root"

  pr_hit=$(pr_lookup "$branch")
  pr_num=$(printf '%s' "$pr_hit" | cut -f1)
  pr_state=$(printf '%s' "$pr_hit" | cut -f2)

  dirty=0
  [ -n "$(git -C "$path" status --porcelain 2>/dev/null)" ] && dirty=1

  # 未 push（upstream 無しはコミットがあれば未 push 扱い）
  unpushed=0
  if git -C "$path" rev-parse --abbrev-ref '@{u}' >/dev/null 2>&1; then
    [ -n "$(git -C "$path" log --oneline '@{u}..HEAD' 2>/dev/null)" ] && unpushed=1
  else
    base=$(git -C "$path" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null)
    [ -z "$base" ] && base="origin/main"
    [ -n "$(git -C "$path" log --oneline "$base..HEAD" 2>/dev/null)" ] && unpushed=1
  fi

  last_epoch=$(git -C "$path" log -1 --format=%ct 2>/dev/null || echo 0)
  age_days=$(( (now - last_epoch) / 86400 ))

  space_id=$(printf '%s' "$spaces_json" | jq -r --arg p "$path" '.[] | select(.path == $p) | .id' | head -1)
  space_status=$(printf '%s' "$spaces_json" | jq -r --arg p "$path" '.[] | select(.path == $p) | .status' | head -1)

  # 判定
  if [ "$dirty" = 1 ] || [ "$unpushed" = 1 ]; then
    verdict="要確認"
  elif [ "$pr_state" = "MERGED" ] || [ "$pr_state" = "CLOSED" ]; then
    verdict="片付け可"
  elif [ "$pr_state" = "OPEN" ]; then
    verdict="生存"
  elif [ "$age_days" -ge "$STALE_DAYS" ]; then
    verdict="要確認"
  else
    verdict="生存"
  fi

  # エージェントが動いている space は触らない
  case "$space_status" in
    working|blocked) [ "$verdict" = "片付け可" ] && verdict="要確認" ;;
  esac

  flags=""
  [ "$dirty" = 1 ] && flags="${flags}dirty "
  [ "$unpushed" = 1 ] && flags="${flags}unpushed "
  [ -n "$space_status" ] && flags="${flags}space:${space_status} "
  [ -n "$flags" ] || flags="-"

  pr_disp="-"
  [ -n "$pr_num" ] && pr_disp="#${pr_num} $(printf '%s' "$pr_state" | tr 'A-Z' 'a-z')"

  rows="${rows}${verdict}\t${branch}\t${pr_disp}\t${age_days}日前\t${flags% }\t${space_id:--}\t${path}\n"
done <<< "$worktrees"

[ -n "$rows" ] || { echo "対象のワークツリーはありません。"; exit 0; }

print_table() {
  printf '%b' "$rows" \
    | sort -t$'\t' -k1,1 \
    | awk -F'\t' 'BEGIN{printf "%-8s %-46s %-14s %-9s %s\n","判定","ブランチ","PR","最終","備考"}
                  {printf "%-8s %-46s %-14s %-9s %s\n",$1,$2,$3,$4,$5}'
}

case "$MODE" in
  json)
    printf '%b' "$rows" | awk -F'\t' 'NF>=7 {printf "{\"verdict\":\"%s\",\"branch\":\"%s\",\"pr\":\"%s\",\"last\":\"%s\",\"flags\":\"%s\",\"space\":\"%s\",\"path\":\"%s\"}\n",$1,$2,$3,$4,$5,$6,$7}' | jq -s .
    exit 0
    ;;
  list)
    print_table
    exit 0
    ;;
esac

# ---- 5. 片付け --------------------------------------------------------------
cleanup_one() {
  local branch="$1" path="$2" space_id="$3"
  # gwq はリポジトリの外から呼ぶ（-g）と全ベースディレクトリを舐めて非常に遅いので、
  # 必ず本体リポジトリの中から呼ぶ。
  local root
  root=$(git -C "$path" rev-parse --path-format=absolute --git-common-dir 2>/dev/null | sed 's|/\.git$||')
  [ -n "$root" ] && [ -d "$root" ] || { echo "  失敗: ${branch}（リポジトリを特定できません）" >&2; return 1; }

  if [ "$DRY_RUN" = 1 ]; then
    echo "  [dry-run] space=${space_id} worktree=${path}"
    (cd "$root" && gwq remove --dry-run "$path" 2>&1 | sed 's/^/    /')
    return 0
  fi
  if [ -n "$space_id" ] && [ "$space_id" != "-" ]; then
    herdr workspace close "$space_id" >/dev/null 2>&1 \
      && echo "  space ${space_id} を閉じました"
  fi
  local rm_args=""
  [ "$DELETE_BRANCH" = 1 ] && rm_args="-b"
  if (cd "$root" && gwq remove $rm_args "$path" >/dev/null 2>&1); then
    echo "  削除: ${branch}"
    return 0
  fi
  echo "  失敗: ${branch}（gwq remove が拒否しました）" >&2
  return 1
}

if [ "$MODE" = "auto" ]; then
  targets=$(printf '%b' "$rows" | awk -F'\t' '$1=="片付け可"')
  [ -n "$targets" ] || { [ "$DRY_RUN" = 1 ] && echo "片付ける対象はありません。"; exit 0; }
  count=0
  names=""
  while IFS=$'\t' read -r verdict branch pr last flags space path; do
    [ -n "$branch" ] || continue
    echo "${branch}  (${pr})"
    cleanup_one "$branch" "$path" "$space" && { count=$((count + 1)); names="${names}${branch} "; }
  done <<< "$targets"
  if [ "$count" -gt 0 ] && [ "$DRY_RUN" = 0 ] && command -v herdr >/dev/null 2>&1; then
    herdr notification show "ワークツリーを${count}件片付けました" \
      --body "${names}" --sound none >/dev/null 2>&1 || true
  fi
  exit 0
fi

# 対話モード
command -v fzf >/dev/null 2>&1 || { print_table; echo; echo "fzf が無いので一覧のみ表示しました。"; exit 0; }

selected=$(printf '%b' "$rows" | sort -t$'\t' -k1,1 \
  | fzf --multi --with-nth=1,2,3,4,5 --delimiter='\t' \
        --header=$'tab で複数選択 / enter で片付け\n判定  ブランチ  PR  最終  備考' \
        --preview 'git -C {7} log --oneline -15 2>/dev/null; echo; git -C {7} status --short 2>/dev/null')
[ -n "$selected" ] || { echo "何も選ばれませんでした。"; exit 0; }

echo "以下を片付けます:"
printf '%s\n' "$selected" | awk -F'\t' '{printf "  %-8s %-46s %s\n",$1,$2,$3}'
echo

if [ "$ASSUME_YES" = 0 ] && [ "$DRY_RUN" = 0 ]; then
  printf 'space を閉じてワークツリーを削除します。よろしいですか？ [y/N] '
  read -r ans
  case "$ans" in y|Y|yes) ;; *) echo "中止しました。"; exit 0 ;; esac
fi

while IFS=$'\t' read -r verdict branch pr last flags space path; do
  [ -n "$branch" ] || continue
  echo "${branch}"
  cleanup_one "$branch" "$path" "$space"
done <<< "$selected"
