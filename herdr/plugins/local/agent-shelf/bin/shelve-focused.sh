#!/bin/bash
# フォーカス中のペインの agent を herdr-shelf で棚にしまう
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:$PATH"
pane=$(printf '%s' "${HERDR_PLUGIN_CONTEXT_JSON:-}" | jq -r '.focused_pane_id // empty')
[ -n "$pane" ] || exit 0
if out=$(herdr-shelf shelve --pane "$pane" 2>&1); then
  herdr notification show "棚にしまいました" --body "$out（prefix+shift+s で開き直せます）" --sound none >/dev/null 2>&1
else
  herdr notification show "棚にしまえませんでした" --body "$out" --sound none >/dev/null 2>&1
fi
