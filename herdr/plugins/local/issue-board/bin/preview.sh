#!/bin/bash
dir="$(cd "$(dirname "$0")" && pwd)"
if [ "$("$dir/mode.sh" get)" = pr ]; then exec "$dir/pr-preview.sh" "$@"; else exec "$dir/issue-preview.sh" "$@"; fi
