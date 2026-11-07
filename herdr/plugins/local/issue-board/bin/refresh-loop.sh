#!/bin/bash
# fzf（--listen）へ定期的に reload を送る。fzf が終われば curl が失敗して抜ける。
# gh を呼ぶかどうかは list.sh 側の共有キャッシュ（30秒）が決めるので、一覧がいくつあっても問い合わせは増えない。
# fzf のポートを state に書いておき、タブに来たとき ensure.py がすぐ reload を送れるようにする。
# 自分のペインが消えていたら（閉じたのにプロセスが残った場合）board.sh ごと止める
dir="$(cd "$(dirname "$0")" && pwd)"
herdr="${HERDR_BIN_PATH:-herdr}"
state="${HERDR_PLUGIN_STATE_DIR:-$HOME/.local/state/herdr/plugins/issue-board}"
portfile="$state/port-${BOARD_ID:-default}"
portfile="${portfile//:/_}"
echo "${FZF_PORT:?}" > "$portfile"
trap 'rm -f "$portfile"' EXIT
while sleep "${1:-60}"; do
  if [ -n "${HERDR_PANE_ID:-}" ] && "$herdr" pane get "$HERDR_PANE_ID" 2>&1 | grep -q pane_not_found; then
    kill 0
  fi
  curl -fs -XPOST "localhost:$FZF_PORT" -d "reload($dir/list.sh)" >/dev/null || exit 0
done
