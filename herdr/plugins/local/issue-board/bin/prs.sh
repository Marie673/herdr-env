#!/bin/bash
# open な PR の一覧を fzf の行にして出す。1行 = "<番号>\t<表示>"
#   prs.sh [--fresh]   キャッシュ（リポジトリごと・全一覧で共有、30秒）を使わず gh を引き直す
# 並びは作成の新しい順（番号の大きい順）
#   prs.sh --json      キャッシュの JSON をそのまま出す（pr-preview.sh / pr-open.sh 用）
# 表示: ●（ワークツリーあり） #番号 状態（draft 競合 CI CR 要修正/未解決/承認） タイトル @作成者
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

age=999999
[ -f "$cache" ] && age=$(( $(date +%s) - $(stat -f %m "$cache") ))
if [ "${1:-}" = --fresh ] || { [ "$age" -gt 30 ] && [ "${1:-}" != --json ]; }; then
  if out=$(cd "$root" && gh api graphql -F owner='{owner}' -F repo='{repo}' -f query="$QUERY" 2>/dev/null); then
    printf '%s' "$out" | jq '.data.repository.pullRequests.nodes' > "$cache.tmp" && mv "$cache.tmp" "$cache"
  fi
fi
if [ "${1:-}" = --json ]; then
  cat "$cache" 2>/dev/null || echo '[]'
  exit 0
fi
if [ ! -f "$cache" ]; then
  printf -- '-\t(PR を取得できません。gh auth status を確認)\n'
  exit 0
fi

branches=$(git -C "$root" worktree list --porcelain 2>/dev/null | sed -n 's#^branch refs/heads/##p')
jq -r --arg br "$branches" '
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
    + .title
    + c("90"; "  @\(.author.login // "?")")
' "$cache"
