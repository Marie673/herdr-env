#!/bin/bash
# フォーカス中のペインを覚えてからピッカーを開く（ピッカー自身のペインと区別するため）
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:$PATH"
pane=$(printf '%s' "${HERDR_PLUGIN_CONTEXT_JSON:-}" | jq -r '.focused_pane_id // .pane_id // empty' 2>/dev/null)
[ -n "$pane" ] || pane="${HERDR_PANE_ID:-}"
exec herdr plugin pane open --plugin claude-accounts --entrypoint picker --placement overlay \
  --env "CLAUDE_ACCOUNT_ORIGIN_PANE=$pane" --focus >/dev/null
