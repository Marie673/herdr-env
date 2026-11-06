#!/bin/bash
# fzf（--listen）へ定期的に reload を送る。fzf が終われば curl が失敗して抜ける。
# 自分のペインが消えていたら（閉じたのにプロセスが残った場合）board.sh ごと止める
dir="$(cd "$(dirname "$0")" && pwd)"
herdr="${HERDR_BIN_PATH:-herdr}"
while sleep "${1:-180}"; do
  if [ -n "${HERDR_PANE_ID:-}" ] && "$herdr" pane get "$HERDR_PANE_ID" 2>&1 | grep -q pane_not_found; then
    kill 0
  fi
  curl -fs -XPOST "localhost:${FZF_PORT:?}" -d "reload($dir/issues.sh --fresh)" >/dev/null || exit 0
done
