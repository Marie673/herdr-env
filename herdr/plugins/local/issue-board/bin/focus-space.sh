#!/bin/bash
# ワークツリーのパスを開いている space へ移る。開いていなければ終了コード1。
# TUI（herdr server）と GUI（Herdr.app、HC_TERMINAL_ID がある）の両方に対応する。
path="${1:?}"
if [ -n "${HC_TERMINAL_ID:-}" ] && [ "${HERDR_ENV:-}" != 1 ] && command -v herdr-app >/dev/null 2>&1; then
  sp=$(herdr-app ls 2>/dev/null | jq -r --arg p "$path" '[.spaces[] | select(.folder == $p and .machine == null)][0].id // empty')
  [ -n "$sp" ] || exit 1
  herdr-app do space.select "$(jq -n --arg id "$sp" '{id: $id}')" >/dev/null
  exit 0
fi
ws=$(herdr workspace list | jq -r --arg p "$path" '.result.workspaces[] | select(.worktree.checkout_path == $p) | .workspace_id' | head -1)
[ -n "$ws" ] || exit 1
herdr workspace focus "$ws" >/dev/null
