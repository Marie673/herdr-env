#!/bin/bash
dir="$(cd "$(dirname "$0")" && pwd)"
case "${1:-}" in ''|*[!0-9]*) exit 0 ;; esac
kind=issue; [ "$("$dir/mode.sh" get)" = pr ] && kind=pr
gh "$kind" view "$1" --web >/dev/null 2>&1
