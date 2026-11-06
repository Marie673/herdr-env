#!/bin/bash
# PR のプレビュー（一覧のキャッシュから。gh は呼ばない）: 見出し・ブランチ・checks・本文
dir="$(cd "$(dirname "$0")" && pwd)"
n="${1:-}"
case "$n" in ''|*[!0-9]*) exit 0 ;; esac
"$dir/prs.sh" --json | jq -r --argjson n "$n" '
  def cls:
    if .__typename == "CheckRun" then
      if .status != "COMPLETED" then "pending"
      elif (.conclusion == "SUCCESS" or .conclusion == "NEUTRAL") then "ok"
      elif .conclusion == "SKIPPED" then "skip" else "fail" end
    else
      if (.state == "PENDING" or .state == "EXPECTED") then "pending"
      elif .state == "SUCCESS" then "ok" else "fail" end
    end;
  def mark: {"fail": "\u001b[31m✗\u001b[0m", "pending": "\u001b[33m…\u001b[0m", "ok": "\u001b[32m✓\u001b[0m", "skip": "\u001b[90m-\u001b[0m"}[.];
  .[] | select(.number == $n)
  | ((.commits.nodes[0].commit.statusCheckRollup.contexts.nodes) // []) as $ctx
  | "\u001b[1;36m#\(.number) \(.title)\u001b[0m",
    "\u001b[90m@\(.author.login // "?")  \(.headRefName)  更新 \(.updatedAt[0:10])"
      + (if .isDraft then "  draft" else "" end)
      + (if .mergeable == "CONFLICTING" then "  \u001b[31m競合\u001b[90m" else "" end)
      + (if .reviewDecision != null and .reviewDecision != "" then "  \(.reviewDecision)" else "" end)
      + "  未解決スレッド \([.reviewThreads.nodes[] | select(.isResolved | not)] | length)\u001b[0m",
    "",
    (if ($ctx | length) > 0 then
       "\u001b[1mchecks\u001b[0m",
       ($ctx | map(. + {c: cls}) | sort_by({"fail":0,"pending":1,"ok":2,"skip":3}[.c])[]
         | "  \(.c | mark) \(.name // .context)"),
       ""
     else empty end),
    ((.body // "") | if . == "" then "(本文なし)" else . end)
'
