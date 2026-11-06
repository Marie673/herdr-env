#!/bin/bash
# Enter: issue なら着手（start.sh）、PR ならその space へ（pr-open.sh）
dir="$(cd "$(dirname "$0")" && pwd)"
if [ "$("$dir/mode.sh" get)" = pr ]; then exec "$dir/pr-open.sh" "$@"; else exec "$dir/start.sh" "$@"; fi
