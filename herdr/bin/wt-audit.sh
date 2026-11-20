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
#   --repo <path>       このリポジトリだけを対象にする（既定: ghq が知っている全リポジトリ）
#   --delete-branch     ワークツリーに加えてローカルブランチも消す
#   --stale-days <N>    PR が無いワークツリーを「放置」とみなす日数（既定 30）
#   --yes               対話の確認を飛ばす
#
# 判定:
#   片付け可  PR が merged/closed、未コミット変更なし、未 push コミットなし、
#             作り直せないignored ファイル（data/ や .env など）なし
#   放置      PR が無く、変更も独自のコミットも作り直せない ignored ファイルも無く、herdr の space も無く、
#             作ってから --orphan-days 日（既定 3）経ったもの。対話モードの候補に出すだけで、--auto では消さない
#   要確認    未コミット変更・未 push コミットあり、または PR が無く N 日放置
#   生存      PR が open、または最近さわっている
#   保護      ~/.config/wt-audit/keep に書いたブランチ（メンバー・常駐役など）。消さない
#
# 「未 push」は、どのリモートのブランチからも辿れないコミットがあること。
# マージ後にリモートのブランチが消えても、中身が main に入っていれば未 push にならない。
#
# 消したワークツリーは、すぐには捨てず ~/.local/state/wt-audit/quarantine/<日時>/ へ移し、
# 7 日（WT_AUDIT_QUARANTINE_DAYS）経ったら捨てる。間違えて消しても、その間は取り出せる。
# （2026-11-01、改修途中の版が data/ を抱えたワークツリーを消した事故への対策）
#
# 安全のため、以下は --auto では絶対に消さない:
#   - 未コミット変更 / 未 push コミットがあるもの
#   - herdr の space でエージェントが working / blocked のもの
#   - 本体リポジトリ（is_main）

set -uo pipefail

STALE_DAYS=30
ORPHAN_DAYS=3
KEEP_FILE="${WT_AUDIT_KEEP:-$HOME/.config/wt-audit/keep}"
QUARANTINE_ROOT="${XDG_STATE_HOME:-$HOME/.local/state}/wt-audit/quarantine"
QUARANTINE_DAYS="${WT_AUDIT_QUARANTINE_DAYS:-7}"
# 消してよい ignored ファイル（作り直せるもの）。パスの最後の要素で判定する
REGENERABLE_RE='(^|/)(\.DS_Store|__pycache__/|\.pytest_cache/|\.ruff_cache/|\.mypy_cache/|\.venv/|venv/|node_modules/|target/|\.next/|\.turbo/|dist/|build/|coverage/|\.gradle/|\.terraform/|[^/]+\.pyc|\.claude/worktrees/)$'
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
    --orphan-days) ORPHAN_DAYS="${2:-3}"; shift 2 ;;
    --repo) REPO_ARG="${2:-}"; shift 2 ;;
    --all-repos) ALL_REPOS=1; shift ;;  # 既定なので何もしない（後方互換）
    -h|--help) sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "wt-audit: 不明なオプション: $1" >&2; exit 2 ;;
  esac
done

for c in gwq jq git; do
  command -v "$c" >/dev/null 2>&1 || { echo "wt-audit: $c が必要です" >&2; exit 1; }
done

now=$(date +%s)

# ---- 1. worktree 一覧 -------------------------------------------------------
# 既定は「ghq が知っている全リポジトリ」。棚卸しは全体像が要るし、
# カレントリポジトリに worktree が無いと空振りして分かりにくいため。
# --repo で 1 リポジトリに絞れる。
if [ -n "$REPO_ARG" ]; then
  worktrees_json=$(cd "$REPO_ARG" && gwq list --json 2>/dev/null)
else
  # gwq list -g はベースディレクトリ全体を舐めて 1 分以上かかる。
  # ghq が知っているリポジトリごとに引けば 1 件 0.1 秒で済む。
  worktrees_json=$(ghq list -p 2>/dev/null | while IFS= read -r r; do
      (cd "$r" 2>/dev/null && gwq list --json 2>/dev/null)
    done | jq -s 'map(select(. != null)) | add // []')
fi
[ -n "$worktrees_json" ] || { echo "wt-audit: ワークツリーが取れませんでした" >&2; exit 1; }

# 本体リポジトリは棚卸し対象外。
# gwq list は同じベースディレクトリを共有するリポジトリすべてで同じ一覧を返すため、
# 全リポジトリを舐めると同じワークツリーが repo 数だけ重複する。path で一意化する。
worktrees=$(printf '%s' "$worktrees_json" | jq -c '[.[] | select(.is_main != true) | {branch, path}] | unique_by(.path) | sort_by(.branch) | .[]')
[ -n "$worktrees" ] || { echo "対象のワークツリーはありません。"; exit 0; }

# ---- 2. herdr の space（あれば） -------------------------------------------
spaces_json='[]'
if [ "${HERDR_ENV:-}" = "1" ] || command -v herdr >/dev/null 2>&1; then
  spaces_json=$(herdr workspace list 2>/dev/null \
    | jq -c '[.result.workspaces[]? | select(.worktree.is_linked_worktree == true)
              | {id: .workspace_id, path: .worktree.checkout_path, status: .agent_status}]' 2>/dev/null)
  [ -n "$spaces_json" ] || spaces_json='[]'
fi

# Herdr の GUI（Herdr.app）のスペースも見る。id は "gui:<space id>"。GUI はブランチを
# プロセスのディレクトリで判断するので、スペースのフォルダに加えて、各ターミナルがいま
# いるディレクトリのワークツリーにも、そのターミナルの agent の状態を結びつける
if command -v herdr-app >/dev/null 2>&1 && command -v hc >/dev/null 2>&1; then
  gui_json=$(herdr-app ls 2>/dev/null | /usr/bin/python3 -c '
import json, os, subprocess, sys
try:
    spaces = json.load(sys.stdin).get("spaces", [])
except ValueError:
    spaces = []
def top(d):
    if not d or not os.path.isdir(d):
        return None
    p = subprocess.run(["git", "-C", d, "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return os.path.realpath(p.stdout.strip()) if p.returncode == 0 else None
out = []
for sp in spaces:
    if sp.get("machine"):
        continue
    sid = "gui:" + sp["id"]
    folder = top(sp.get("folder"))
    for tab in sp.get("tabs", []):
        for x in tab.get("terminals", []):
            try:
                info = json.loads(subprocess.run(["hc", "get", x["terminal"], "--json"], capture_output=True, text=True).stdout)
            except ValueError:
                continue
            st = x.get("status") or (info.get("status") or {}).get("state") or ""
            for path in {folder, top((info.get("foreground") or {}).get("cwd"))}:
                if path:
                    out.append({"id": sid, "path": path, "status": st})
    if folder:
        out.append({"id": sid, "path": folder, "status": ""})
print(json.dumps(out))
' 2>/dev/null)
  [ -n "$gui_json" ] || gui_json='[]'
  # 同じワークツリーを TUI と GUI の両方が開いていることもある。id はまとめ、状態は一番急ぐものにする
  spaces_json=$(jq -c -n --argjson a "$spaces_json" --argjson b "$gui_json" '
    def rank: {"blocked": 4, "working": 3, "done": 2, "idle": 1}[.] // 0;
    ($a + $b) | group_by(.path) | map({
      id: (map(.id) | unique | join(",")),
      path: .[0].path,
      status: (map(.status // "") | max_by(rank))
    })')
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

  # .gitignore されたファイルは git worktree remove が黙って消す。再生成できるもの
  # （依存・キャッシュ・ビルド成果物）以外が残っていれば、データや結果の可能性があるので消さない。
  ignored=0
  if git -C "$path" status --porcelain --ignored 2>/dev/null | sed -n 's/^!! //p' \
      | grep -vE "$REGENERABLE_RE" | grep -q .; then
    ignored=1
  fi

  # 未 push = どのリモートのブランチからも辿れないコミットがある。
  # 以前は @{u}..HEAD を見ていたので、PR のマージ後に main を取り込んだだけのブランチや、
  # リモートのブランチが消えたブランチまで「未 push」になり、片付けが止まっていた。
  unpushed=0
  [ -n "$(git -C "$path" rev-list -n 1 HEAD --not --remotes 2>/dev/null)" ] && unpushed=1

  # ワークツリーを作ってからの日数（.git ファイルの更新時刻 ≒ gwq add の時刻）
  created_epoch=$(stat -f %m "$path/.git" 2>/dev/null || echo "$now")
  created_days=$(( (now - created_epoch) / 86400 ))

  keep=0
  if [ -f "$KEEP_FILE" ] && grep -qxF -- "$branch" "$KEEP_FILE" 2>/dev/null; then
    keep=1
  fi

  last_epoch=$(git -C "$path" log -1 --format=%ct 2>/dev/null || echo 0)
  age_days=$(( (now - last_epoch) / 86400 ))

  space_id=$(printf '%s' "$spaces_json" | jq -r --arg p "$path" '.[] | select(.path == $p) | .id' | head -1)
  space_status=$(printf '%s' "$spaces_json" | jq -r --arg p "$path" '.[] | select(.path == $p) | .status' | head -1)

  # 判定
  if [ "$keep" = 1 ]; then
    verdict="保護"
  elif [ "$pr_state" = "OPEN" ]; then
    verdict="生存"
  elif [ "$dirty" = 1 ] || [ "$unpushed" = 1 ]; then
    verdict="要確認"
  elif [ "$pr_state" = "MERGED" ] || [ "$pr_state" = "CLOSED" ]; then
    # 作り直せない ignored ファイル（data/・.env など）があれば、人が見てから消す
    if [ "$ignored" = 1 ]; then verdict="要確認"; else verdict="片付け可"; fi
  elif [ "$ignored" = 0 ] && [ -z "$space_id" ] && [ "$created_days" -ge "$ORPHAN_DAYS" ]; then
    # PR が無いものは、何の作業だったかを機械では判断できない。--auto では消さず、候補に出すだけ
    verdict="放置"
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
  [ "$ignored" = 1 ] && flags="${flags}ignored "
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
    printf '%b' "$rows" | jq -R -s '[split("\n")[] | select(length > 0) | split("\t")
      | select(length >= 7)
      | {verdict: .[0], branch: .[1], pr: .[2], last: .[3], flags: .[4], space: .[5], path: .[6]}]'
    exit 0
    ;;
  list)
    print_table
    exit 0
    ;;
esac

# ---- 5. 片付け --------------------------------------------------------------
# 通知: TUI の herdr が動いていればその通知に、だめなら macOS の通知に出す（GUI には外から出す口が無い）
notify_all() {
  if command -v herdr >/dev/null 2>&1 && herdr notification show "$1" --body "$2" --sound none >/dev/null 2>&1; then
    return 0
  fi
  osascript -e "display notification $(jq -Rn --arg s "$2" '$s') with title $(jq -Rn --arg s "$1" '$s')" >/dev/null 2>&1 || true
}

cleanup_one() {
  local branch="$1" path="$2" space_id="$3"
  # gwq はリポジトリの外から呼ぶ（-g）と全ベースディレクトリを舐めて非常に遅いので、
  # 必ず本体リポジトリの中から呼ぶ。
  local root
  root=$(git -C "$path" rev-parse --path-format=absolute --git-common-dir 2>/dev/null | sed 's|/\.git$||')
  [ -n "$root" ] && [ -d "$root" ] || { echo "  失敗: ${branch}（リポジトリを特定できません）" >&2; return 1; }

  if [ "$DRY_RUN" = 1 ]; then
    echo "  [dry-run] space=${space_id} worktree=${path}"
    return 0
  fi
  if [ -n "$space_id" ] && [ "$space_id" != "-" ]; then
    local sid
    for sid in ${space_id//,/ }; do
      case "$sid" in
        gui:*)
          herdr-app do space.close "$(jq -cn --arg id "${sid#gui:}" '{id: $id, confirmed: true}')" >/dev/null 2>&1 \
            && echo "  space ${sid} を閉じました（GUI）" ;;
        *)
          herdr workspace close "$sid" >/dev/null 2>&1 \
            && echo "  space ${sid} を閉じました" ;;
      esac
    done
  fi
  # gwq remove はパスもパターンとして解釈するので、`…-407` が `…-407b` にも当たって
  # 「複数に一致」で拒否されていた。git に完全一致のパスで頼む（--force なし。
  # 未コミット変更があれば git が拒否する）。
  # 未コミット変更があれば git と同じく拒否する（判定の後に変わった場合の保険）
  if [ -n "$(git -C "$path" status --porcelain 2>/dev/null)" ]; then
    echo "  失敗: ${branch}（未コミットの変更があります）" >&2
    FAILED_NAMES="${FAILED_NAMES}${branch} "
    return 1
  fi
  # すぐには捨てず、隔離フォルダへ移してから git の管理情報を片付ける
  local qdir err
  qdir="${QUARANTINE_ROOT}/$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$qdir"
  if err=$(mv "$path" "$qdir/" 2>&1) && err=$(git -C "$root" worktree prune 2>&1); then
    printf '%s\t%s\t%s\n' "$branch" "$path" "$root" >> "$qdir/MANIFEST.tsv"
    echo "  削除: ${branch}（${qdir}/$(basename "$path") に 7 日置きます）"
    if [ "$DELETE_BRANCH" = 1 ]; then
      git -C "$root" branch -d "$branch" >/dev/null 2>&1 && echo "  ブランチも削除: ${branch}"
    fi
    return 0
  fi
  echo "  失敗: ${branch}（$(printf '%s' "$err" | head -1)）" >&2
  FAILED_NAMES="${FAILED_NAMES}${branch} "
  return 1
}

if [ "$MODE" = "auto" ] && [ "$DRY_RUN" = 0 ] && [ -d "$QUARANTINE_ROOT" ]; then
  # 期限の過ぎた隔離を捨てる
  find "$QUARANTINE_ROOT" -mindepth 1 -maxdepth 1 -type d -mtime "+${QUARANTINE_DAYS}" -exec rm -rf {} + 2>/dev/null
fi

if [ "$MODE" = "auto" ]; then
  targets=$(printf '%b' "$rows" | awk -F'\t' '$1=="片付け可"')
  [ -n "$targets" ] || { [ "$DRY_RUN" = 1 ] && echo "片付ける対象はありません。"; exit 0; }
  count=0
  names=""
  FAILED_NAMES=""
  while IFS=$'\t' read -r verdict branch pr last flags space path; do
    [ -n "$branch" ] || continue
    echo "${branch}  (${pr})"
    cleanup_one "$branch" "$path" "$space" && { count=$((count + 1)); names="${names}${branch} "; }
  done <<< "$targets"
  if [ "$DRY_RUN" = 0 ]; then
    # ワークツリーが消えた依頼を gwt-tasks の台帳からも外す
    command -v gwt-tasks >/dev/null 2>&1 && gwt-tasks prune 2>/dev/null | sed 's/^/  /'
    if [ "$count" -gt 0 ]; then
      notify_all "ワークツリーを${count}件片付けました" "${names}"
    fi
    # 片付けられなかったものは、前回と顔ぶれが変わったときだけ知らせる（30 分ごとに同じ通知を出さない）
    fail_state="${XDG_STATE_HOME:-$HOME/.local/state}/wt-audit/last-failed"
    if [ "$FAILED_NAMES" != "$(cat "$fail_state" 2>/dev/null)" ]; then
      printf '%s' "$FAILED_NAMES" > "$fail_state"
      if [ -n "$FAILED_NAMES" ]; then
        notify_all "ワークツリーを片付けられませんでした" "${FAILED_NAMES}（wt-audit --list で確認）"
      fi
    fi
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
