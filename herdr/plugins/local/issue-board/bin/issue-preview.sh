#!/bin/bash
# issue のプレビュー（タイトル・ラベル・作成者・本文・最新コメント2件）
n="${1:-}"
case "$n" in ''|*[!0-9]*) exit 0 ;; esac
gh issue view "$n" --json title,body,labels,author,createdAt,comments 2>/dev/null | jq -r '
  "\u001b[1;36m#'"$n"' \(.title)\u001b[0m",
  "\u001b[90m\(.author.login)  \(.createdAt[0:10])  コメント \(.comments | length)"
    + (if (.labels | length) > 0 then "  [" + ([.labels[].name] | join(", ")) + "]" else "" end) + "\u001b[0m",
  "",
  (.body | if . == "" then "(本文なし)" else . end),
  (if (.comments | length) > 0 then
     "", "\u001b[1m── 最新のコメント ──\u001b[0m",
     (.comments[-2:][] | "", "\u001b[33m\(.author.login)\u001b[0m \u001b[90m\(.createdAt[0:10])\u001b[0m", .body)
   else empty end)
'
