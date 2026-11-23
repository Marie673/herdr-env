#!/usr/bin/env bash
# issue のタイトルからブランチ名 <type>/<english-slug>-<番号> を haiku に作らせて1行で出す。作れなければ終了コード1
#   suggest-branch.sh <番号> <タイトル> [ラベル（カンマ区切り）]
# start.sh（TUI）と GUI のパネル issues-env が使う。
# hooks やリポジトリの設定を読ませないよう、ユーザー設定を外して中立な場所から呼ぶ
# （--bare だとログイン情報も読まれず失敗する）
set -u
n="${1:-}" title="${2:-}" labels="${3:-}"
case "$n" in ''|*[!0-9]*) exit 2 ;; esac
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
# 30秒で打ち切る（timeout が無ければ打ち切らない。GUI のパネルは exec の timeout でも止める）
limit=(); command -v timeout >/dev/null 2>&1 && limit=(timeout 30)
out=$(cd "${TMPDIR:-/tmp}" && printf '%s\n' "GitHub issue のタイトルから git のブランチ名を1つだけ作って、その1行だけを出力して。説明や記号は付けない。
形式: <type>/<english-kebab-slug>
- type は feat / fix / docs / refactor / chore / test のどれか（不具合やセキュリティの修正は fix、方針決めや調査のまとめは docs）
- slug は何をする変更か一目で分かる英語2〜5語。全体で40文字以内。小文字・数字・ハイフンのみ
タイトル: $title
ラベル: $labels" | ${limit[@]+"${limit[@]}"} claude -p --setting-sources project --model haiku --no-session-persistence 2>/dev/null) || exit 1
out=$(printf '%s' "$out" | tr -d '`\r' | grep -E '^(feat|fix|docs|refactor|chore|test)/[a-z0-9-]+$' | head -1)
[ -n "$out" ] || exit 1
printf '%s-%s\n' "${out%-}" "$n"
