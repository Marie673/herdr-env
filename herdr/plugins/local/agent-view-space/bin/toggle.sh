#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
. ./lib.sh

# agent.view には getter がないので、適用状態を自前で持つ
state_dir="${HERDR_PLUGIN_STATE_DIR:-$HOME/.config/herdr/plugins/local/agent-view-space}"
mkdir -p "$state_dir"
state="$state_dir/scope"

if [ "$(cat "$state" 2>/dev/null || echo current)" = "current" ]; then
  bash ./clear.sh && printf 'all' > "$state"
else
  bash ./apply.sh && printf 'current' > "$state"
fi
