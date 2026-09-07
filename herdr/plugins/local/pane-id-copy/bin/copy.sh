#!/usr/bin/env bash
set -euo pipefail

# 呼び出し元ペインの pane_id をクリップボードへ入れる。
# 他のペイン（エージェント）が `herdr pane ...` の対象として貼り付けて使うための ID。
#
# 対象ペインの解決順:
#   1. HERDR_PANE_ID          … プラグイン起動時に注入される呼び出し元ペイン
#   2. HERDR_PLUGIN_CONTEXT_JSON の pane フィールド
#   3. アクティブワークスペースでフォーカス中のペイン（保険）

with_hint=0
[ "${1:-}" = "--with-hint" ] && with_hint=1

clip() {
  if command -v pbcopy >/dev/null 2>&1; then
    pbcopy
  elif command -v wl-copy >/dev/null 2>&1; then
    wl-copy
  elif command -v xclip >/dev/null 2>&1; then
    xclip -selection clipboard
  else
    cat >/dev/null
    return 1
  fi
}

notify() {
  herdr notification show "$1" --body "$2" --sound none >/dev/null 2>&1 || true
}

pane="${HERDR_PANE_ID:-}"

if [ -z "$pane" ] && [ -n "${HERDR_PLUGIN_CONTEXT_JSON:-}" ]; then
  pane=$(printf '%s' "$HERDR_PLUGIN_CONTEXT_JSON" | jq -r '
    [ .pane_id?, .focused_pane_id?, .pane?.pane_id?, .pane?.id?, .focused_pane?.pane_id? ]
    | map(select(type == "string" and . != "")) | first // empty
  ' 2>/dev/null || true)
fi

if [ -z "$pane" ]; then
  ws="${HERDR_WORKSPACE_ID:-${HERDR_ACTIVE_WORKSPACE_ID:-}}"
  pane=$(herdr pane list 2>/dev/null | jq -r --arg ws "$ws" '
    [ .result.panes[]?
      | select(.focused == true)
      | select($ws == "" or .workspace_id == $ws)
      | .pane_id ] | first // empty
  ' 2>/dev/null || true)
fi

if [ -z "$pane" ]; then
  notify "ペインIDをコピーできません" "対象ペインを特定できませんでした"
  exit 1
fi

payload="$pane"
if [ "$with_hint" = 1 ]; then
  agent=""
  agent=$(herdr pane get "$pane" 2>/dev/null | jq -r '.result.pane.agent // empty' 2>/dev/null || true)
  if [ -n "${agent}" ]; then
    payload="pane ${pane} (agent: ${agent}) — herdr pane read ${pane} / herdr agent prompt ${pane} \"...\""
  else
    payload="pane ${pane} — herdr pane read ${pane} / herdr pane run ${pane} \"...\""
  fi
fi

if printf '%s' "$payload" | clip; then
  notify "ペインIDをコピーしました" "$payload"
else
  notify "クリップボードが使えません" "$payload"
  exit 1
fi
