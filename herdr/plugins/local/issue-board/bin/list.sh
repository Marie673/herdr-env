#!/bin/bash
# 今のモードの一覧を出す（issues.sh / prs.sh）。引数はそのまま渡す（--fresh）
dir="$(cd "$(dirname "$0")" && pwd)"
if [ "$("$dir/mode.sh" get)" = pr ]; then exec "$dir/prs.sh" "$@"; else exec "$dir/issues.sh" "$@"; fi
