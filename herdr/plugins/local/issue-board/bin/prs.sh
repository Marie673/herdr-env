#!/bin/bash
# open な PR の一覧を fzf の行にして出す。1行 = "<番号>\t<表示>"
#   prs.sh [--fresh] [--json] [--cached]
#     --fresh   キャッシュ（リポジトリごと・全一覧で共有、30秒）を使わず gh を引き直す
#     --json    キャッシュの JSON をそのまま出す（pr-preview.sh / pr-open.sh / GUI のパネル issues-env 用）
#     --cached  古くても gh を呼ばない（pr-preview.sh / pr-open.sh は一覧と同じ中身を見たいので付ける）
# 並びは作成の新しい順（番号の大きい順）
# 表示: ●（ワークツリーあり） #番号 状態（draft 競合 CI CR 要修正/未解決/承認） ラベル（GitHub の色） タイトル @作成者
set -u
root="${ISSUE_BOARD_REPO_ROOT:-$PWD}"
cache_dir="$HOME/.local/state/herdr/plugins/issue-board"
mkdir -p "$cache_dir"
cache="$cache_dir/prs-$(printf '%s' "$root" | shasum | cut -c1-12).json"

read -r -d '' QUERY <<'GQL'
query($owner: String!, $repo: String!) {
  repository(owner: $owner, name: $repo) {
    pullRequests(states: OPEN, first: 100, orderBy: {field: CREATED_AT, direction: DESC}) {
      nodes {
        number title body url headRefName isDraft isCrossRepository reviewDecision mergeable updatedAt
        author { login }
        labels(first: 20) { nodes { name color } }
        commits(last: 1) { nodes { commit { statusCheckRollup { contexts(first: 100) { nodes {
          __typename
          ... on CheckRun { name status conclusion detailsUrl }
          ... on StatusContext { context state targetUrl }
        } } } } } }
        reviewThreads(first: 100) { nodes { isResolved } }
      }
    }
  }
}
GQL

fresh=0 json=0 cached=0
for a in "$@"; do
  case "$a" in --fresh) fresh=1 ;; --json) json=1 ;; --cached) cached=1 ;; esac
done

age=999999
[ -f "$cache" ] && age=$(( $(date +%s) - $(stat -f %m "$cache") ))
if [ "$fresh" = 1 ] || { [ "$age" -gt 30 ] && [ "$cached" = 0 ]; }; then
  if out=$(cd "$root" && gh api graphql -F owner='{owner}' -F repo='{repo}' -f query="$QUERY" 2>"$cache.err"); then
    printf '%s' "$out" | jq '.data.repository.pullRequests.nodes' > "$cache.tmp" && mv "$cache.tmp" "$cache"
  else
    err=$(head -c 500 "$cache.err")
  fi
  rm -f "$cache.err"
fi
if [ "$json" = 1 ]; then
  if [ -f "$cache" ]; then cat "$cache"
  elif [ "$cached" = 1 ]; then echo '[]'
  else printf '%s\n' "${err:-PR を取得できません（gh auth status を確認）}" >&2; exit 1
  fi
  exit 0
fi
if [ ! -f "$cache" ]; then
  printf -- '-\t(PR を取得できません。gh auth status を確認)\n'
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
  def cls:
    if .__typename == "CheckRun" then
      if .status != "COMPLETED" then "pending"
      elif (.conclusion == "SUCCESS" or .conclusion == "NEUTRAL") then "ok"
      elif .conclusion == "SKIPPED" then "skip"
      else "fail" end
    else
      if (.state == "PENDING" or .state == "EXPECTED") then "pending"
      elif .state == "SUCCESS" then "ok" else "fail" end
    end;
  def c(code; s): "\u001b[" + code + "m" + s + "\u001b[0m";
  ($br | split("\n") | map(select(. != ""))) as $b
  | .[]
  | ((.commits.nodes[0].commit.statusCheckRollup.contexts.nodes) // []) as $ctx
  | ([$ctx[] | select(((.name // .context) | ascii_downcase | contains("coderabbit")) | not) | cls]) as $ci
  | ([$ctx[] | select((.name // .context) | ascii_downcase | contains("coderabbit")) | cls]) as $cr
  | ([.reviewThreads.nodes[] | select(.isResolved | not)] | length) as $unres
  | [
      (if .isDraft then c("90"; "draft") else empty end),
      (if .mergeable == "CONFLICTING" then c("1;31"; "競合") else empty end),
      (if ($ci | index("fail")) then c("1;31"; "CI✗\($ci | map(select(. == "fail")) | length)")
       elif ($ci | index("pending")) then c("33"; "CI…")
       elif ($ci | index("ok")) then c("32"; "CI✓") else empty end),
      (if ($cr | index("pending")) then c("33"; "CR…") elif ($cr | index("ok")) then c("32"; "CR✓") else empty end),
      (if .reviewDecision == "CHANGES_REQUESTED" then c("1;31"; "要修正")
       elif $unres > 0 then c("38;5;215"; "未解決\($unres)")
       elif .reviewDecision == "APPROVED" then c("32"; "承認") else empty end)
    ] as $badges
  | "\(.number)\t"
    + (if (.headRefName as $h | $b | index($h)) then c("33"; "●") + " " else "  " end)
    + c("36"; "#\(.number)") + " "
    + (if ($badges | length) > 0 then ($badges | join(" ")) + " " else "" end)
    + chips(.labels.nodes // [])
    + .title
    + c("90"; "  @\(.author.login // "?")")
' "$cache"
