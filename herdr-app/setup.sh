#!/bin/bash
# Herdr の GUI（Herdr.app）に herdr-env の表示を入れる。何度流してもよい（作り直す）。
#
#   - mods/herdr-env-marks : 外部待ち ⏳ / 要返答 💬 のカード（herdr-wait と Claude hooks のラベルを読む）
#   - mods/agents-env      : 選んでいる Space の agent だけを出す agents 一覧（agents-env.py が標準の agents から作る）
#   - mods/spaces-env      : 各行の右端にその Space の agent の状態を出す Spaces 一覧（spaces-env.py が標準の spaces から作る）
#   - layouts/herdr-env    : 標準の main を複製し、Spaces / Agents 欄を差し替え、サイドバー下にカードを浮かべたもの
#   - mods/issues-env      : 右パネル（Files / Changes のスタック）の Issues。今の Space のリポジトリの issue / PR 一覧と詳細、
#                            着手（gwt --task）。データは TUI の issue-board と同じスクリプト（install.sh が置く）
#   - mods/tabs-env        : タブバーに Issues のボタンを足す複製（tabs-env.py が標準の tabs から作る）
#   - mods/workspace-status-env : （--usage-bar のときだけ）下部のステータスバーに Claude / Antigravity / Codex / Gemini の
#                            利用量を出す。データは ~/.config/herdr/bin/ai-usage-collect.py（install.sh が置く）。
#                            一度入れたら、以後 --usage-bar なしで流しても入れ直す（やめるときは --no-usage-bar）
#   - keys.json            : cmd+shift+a で agents の範囲（この Space / 全部）、cmd+shift+i で右パネルの Issues を出し入れする。
#                            cmd+shift+u で Claude のアカウントを選ぶ（起動 / この会話を別アカウントで続ける）、
#                            cmd+shift+z でこの agent を棚にしまう、cmd+shift+s で棚から開き直す、
#                            cmd+shift+m でこの agent（Claude Code / Codex / Antigravity CLI）のモデル選択を開く
#                            （そのキーが空いているときだけ足す）
#
# 標準の main / agents は Herdr の更新で変わるが、複製は追従しない。Herdr を更新したらもう一度流す。
#
#   herdr-app/setup.sh               入れて herdr-env レイアウトに切り替える
#   herdr-app/setup.sh --no-switch   入れるだけ
#   herdr-app/setup.sh --usage-bar   下部のステータスバーに利用量を出す（--no-usage-bar でやめる）
set -eu

SRC="$(cd "$(dirname "$0")" && pwd)"
HOME_DIR="${HERDR_APP_HOME:-$HOME/.config/herdr-app}"
SWITCH=1
USAGE=""   # 空なら、前に入れていればそのまま入れ直す
for a in "$@"; do
  case "$a" in
    --no-switch) SWITCH=0 ;;
    --usage-bar) USAGE=1 ;;
    --no-usage-bar) USAGE=0 ;;
    *) echo "不明なオプション: $a" >&2; exit 2 ;;
  esac
done
if [ -z "$USAGE" ]; then
  if [ -f "$HOME_DIR/mods/workspace-status-env/mod.json" ]; then USAGE=1; else USAGE=0; fi
fi

command -v herdr-app >/dev/null 2>&1 || { echo "herdr-app が見つかりません（Herdr.app を入れて一度起動してください）" >&2; exit 1; }
[ -d "$HOME_DIR" ] || { echo "$HOME_DIR がありません（Herdr.app を一度起動してください）" >&2; exit 1; }

# 1. 印のカード
mkdir -p "$HOME_DIR/mods/herdr-env-marks"
cp "$SRC/mods/herdr-env-marks/"* "$HOME_DIR/mods/herdr-env-marks/"
herdr-app check "$HOME_DIR/mods/herdr-env-marks"

# 1b. 右パネルの Issues と、タブバーのボタン
#     （前の版の issue-board-env … main の Space で fzf の一覧を右に開いていたもの … は消す）
rm -rf "$HOME_DIR/mods/issue-board-env"
mkdir -p "$HOME_DIR/mods/issues-env"
cp "$SRC/mods/issues-env/"* "$HOME_DIR/mods/issues-env/"
herdr-app check "$HOME_DIR/mods/issues-env"
python3 "$SRC/tabs-env.py" --home "$HOME_DIR"
herdr-app check "$HOME_DIR/mods/tabs-env"

# 2. Space で絞れる agents
python3 "$SRC/agents-env.py" --home "$HOME_DIR"
herdr-app check "$HOME_DIR/mods/agents-env"

# 2b. 行ごとに agent の状態を出す Spaces（Agents を絞っても、ほかの Space が動いているか分かるように）
python3 "$SRC/spaces-env.py" --home "$HOME_DIR"
herdr-app check "$HOME_DIR/mods/spaces-env"

# 2c. 下部のステータスバーに利用量を出す（入れるときだけ）
if [ "$USAGE" = 1 ]; then
  mkdir -p "$HOME_DIR/mods/workspace-status-env"
  cp "$SRC/mods/workspace-status-env/"* "$HOME_DIR/mods/workspace-status-env/"
  herdr-app check "$HOME_DIR/mods/workspace-status-env"
else
  rm -rf "$HOME_DIR/mods/workspace-status-env"
fi

# 3. レイアウト（main から作り直す）
L="$HOME_DIR/layouts/herdr-env.json"
[ -f "$L" ] && mv "$L" "$L.bak.$(date +%Y%m%d%H%M%S)"
herdr-app do layout.clone '{"name":"main","as":"herdr-env"}' >/dev/null
jq '
  def fix: if type == "object" then
      (if .id == "agents" and .content.mod == "agents" then .content.mod = "agents-env" else . end)
      | (if .id == "spaces" and .content.mod == "spaces" then .content.mod = "spaces-env" else . end)
      | (if $usage and .content.mod? == "workspace-status" then .content.mod = "workspace-status-env" else . end)
      | (if .id == "tabs" and .content.mod? == "tabs" then .content.mod = "tabs-env" else . end)
      | (if .id == "panel-body" then .children = ((.children // []) | map(select(.id != "issues"))
           + [{id: "issues", type: "block", content: {mod: "issues-env"}}]) else . end)
      | (if .id == "sidebar-lists" then .floating = ((.floating // []) | map(select(.id != "herdr-env-marks" and .id != "issue-board-env"))
           + [{id: "herdr-env-marks", content: {mod: "herdr-env-marks"}, edge: "bottom", align: "center"}]) else . end)
      | (if has("children") then .children |= map(fix) else . end)
    else . end;
  fix' --argjson usage "$([ "$USAGE" = 1 ] && echo true || echo false)" "$L" > "$L.tmp" && mv "$L.tmp" "$L"
jq -e '[.. | objects | select(.content.mod? == "agents-env")] | length == 1' "$L" >/dev/null \
  || echo "  ! main の Agents 欄が見つからず、agents-env に差し替えられませんでした" >&2
jq -e '[.. | objects | select(.content.mod? == "spaces-env")] | length == 1' "$L" >/dev/null \
  || echo "  ! main の Spaces 欄が見つからず、spaces-env に差し替えられませんでした" >&2
jq -e '[.. | objects | select(.id? == "herdr-env-marks")] | length == 1' "$L" >/dev/null \
  || echo "  ! main に sidebar-lists が無く、印のカードを置けませんでした" >&2
jq -e '[.. | objects | select(.content.mod? == "issues-env")] | length == 1' "$L" >/dev/null \
  || echo "  ! main に右パネル（panel-body）が無く、Issues を置けませんでした" >&2
jq -e '[.. | objects | select(.content.mod? == "tabs-env")] | length == 1' "$L" >/dev/null \
  || echo "  ! main のタブバーが見つからず、tabs-env に差し替えられませんでした（Issues のボタンが出ません）" >&2
if [ "$USAGE" = 1 ]; then
  jq -e '[.. | objects | select(.content.mod? == "workspace-status-env")] | length == 1' "$L" >/dev/null \
    || echo "  ! main に下部のステータスバー（workspace-status）が無く、利用量のバーに差し替えられませんでした" >&2
fi

# 4. キー（使っていなければ）
K="$HOME_DIR/keys.json"
add_key() {
  [ -f "$K" ] || return 0
  jq -e --arg k "$1" '.global[$k]' "$K" >/dev/null 2>&1 && return 0
  jq --arg k "$1" --arg c "$2" '.global[$k] = $c' "$K" > "$K.tmp" && mv "$K.tmp" "$K"
}
add_key cmd-shift-a agents-env.scope
# 前の版の割り当て（issue-board-env.toggle）は、右パネルの Issues に付け替える
if [ -f "$K" ] && [ "$(jq -r '.global["cmd-shift-i"] // empty' "$K")" = issue-board-env.toggle ]; then
  jq '.global["cmd-shift-i"] = "tabs-env.issues"' "$K" > "$K.tmp" && mv "$K.tmp" "$K"
fi
add_key cmd-shift-i tabs-env.issues
add_key cmd-shift-u herdr-env-marks.accounts
add_key cmd-shift-z herdr-env-marks.shelve
add_key cmd-shift-s herdr-env-marks.restore
add_key cmd-shift-m herdr-env-marks.model

[ "$SWITCH" = 1 ] && herdr-app do layout.switch '{"name":"herdr-env"}' >/dev/null
herdr-app status | jq -c '{layout, errors}'
