#!/bin/bash
# Herdr の GUI（Herdr.app）に herdr-env の表示を入れる。何度流してもよい（作り直す）。
#
#   - mods/herdr-env-marks : 外部待ち ⏳ / 要返答 💬 のカード（herdr-wait と Claude hooks のラベルを読む）
#   - mods/agents-env      : 選んでいる Space の agent だけを出す agents 一覧（agents-env.py が標準の agents から作る）
#   - mods/spaces-env      : 各行の右端にその Space の agent の状態を出す Spaces 一覧（spaces-env.py が標準の spaces から作る）
#   - layouts/herdr-env    : 標準の main を複製し、Spaces / Agents 欄を差し替え、サイドバー下にカードを浮かべたもの
#   - mods/issue-board-env : issue-board（TUI のプラグイン）の GUI 版の入口。対象リポジトリの main の Space で一覧を右に開く
#   - keys.json            : cmd+shift+a で agents の範囲（この Space / 全部）、cmd+shift+i で issue 一覧の表示を切り替える
#                            （そのキーが空いているときだけ足す）
#
# 標準の main / agents は Herdr の更新で変わるが、複製は追従しない。Herdr を更新したらもう一度流す。
#
#   herdr-app/setup.sh            入れて herdr-env レイアウトに切り替える
#   herdr-app/setup.sh --no-switch 入れるだけ
set -eu

SRC="$(cd "$(dirname "$0")" && pwd)"
HOME_DIR="${HERDR_APP_HOME:-$HOME/.config/herdr-app}"
SWITCH=1
[ "${1:-}" = "--no-switch" ] && SWITCH=0

command -v herdr-app >/dev/null 2>&1 || { echo "herdr-app が見つかりません（Herdr.app を入れて一度起動してください）" >&2; exit 1; }
[ -d "$HOME_DIR" ] || { echo "$HOME_DIR がありません（Herdr.app を一度起動してください）" >&2; exit 1; }

# 1. 印のカード
mkdir -p "$HOME_DIR/mods/herdr-env-marks"
cp "$SRC/mods/herdr-env-marks/"* "$HOME_DIR/mods/herdr-env-marks/"
herdr-app check "$HOME_DIR/mods/herdr-env-marks"

# 1b. issue-board の GUI 版の入口（対象リポジトリの main の Space で一覧を右に開く）
mkdir -p "$HOME_DIR/mods/issue-board-env"
cp "$SRC/mods/issue-board-env/"* "$HOME_DIR/mods/issue-board-env/"
herdr-app check "$HOME_DIR/mods/issue-board-env"

# 2. Space で絞れる agents
python3 "$SRC/agents-env.py" --home "$HOME_DIR"
herdr-app check "$HOME_DIR/mods/agents-env"

# 2b. 行ごとに agent の状態を出す Spaces（Agents を絞っても、ほかの Space が動いているか分かるように）
python3 "$SRC/spaces-env.py" --home "$HOME_DIR"
herdr-app check "$HOME_DIR/mods/spaces-env"

# 3. レイアウト（main から作り直す）
L="$HOME_DIR/layouts/herdr-env.json"
[ -f "$L" ] && mv "$L" "$L.bak.$(date +%Y%m%d%H%M%S)"
herdr-app do layout.clone '{"name":"main","as":"herdr-env"}' >/dev/null
jq '
  def fix: if type == "object" then
      (if .id == "agents" and .content.mod == "agents" then .content.mod = "agents-env" else . end)
      | (if .id == "spaces" and .content.mod == "spaces" then .content.mod = "spaces-env" else . end)
      | (if .id == "sidebar-lists" then .floating = ((.floating // []) | map(select(.id != "herdr-env-marks" and .id != "issue-board-env"))
           + [{id: "herdr-env-marks", content: {mod: "herdr-env-marks"}, edge: "bottom", align: "center"},
              {id: "issue-board-env", content: {mod: "issue-board-env"}, edge: "top", align: "end"}]) else . end)
      | (if has("children") then .children |= map(fix) else . end)
    else . end;
  fix' "$L" > "$L.tmp" && mv "$L.tmp" "$L"
jq -e '[.. | objects | select(.content.mod? == "agents-env")] | length == 1' "$L" >/dev/null \
  || echo "  ! main の Agents 欄が見つからず、agents-env に差し替えられませんでした" >&2
jq -e '[.. | objects | select(.content.mod? == "spaces-env")] | length == 1' "$L" >/dev/null \
  || echo "  ! main の Spaces 欄が見つからず、spaces-env に差し替えられませんでした" >&2
jq -e '[.. | objects | select(.id? == "herdr-env-marks")] | length == 1' "$L" >/dev/null \
  || echo "  ! main に sidebar-lists が無く、印のカードを置けませんでした" >&2

# 4. キー（使っていなければ）
K="$HOME_DIR/keys.json"
add_key() {
  [ -f "$K" ] || return 0
  jq -e --arg k "$1" '.global[$k]' "$K" >/dev/null 2>&1 && return 0
  jq --arg k "$1" --arg c "$2" '.global[$k] = $c' "$K" > "$K.tmp" && mv "$K.tmp" "$K"
}
add_key cmd-shift-a agents-env.scope
add_key cmd-shift-i issue-board-env.toggle

[ "$SWITCH" = 1 ] && herdr-app do layout.switch '{"name":"herdr-env"}' >/dev/null
herdr-app status | jq -c '{layout, errors}'
