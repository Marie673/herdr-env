#!/bin/bash
# Herdr の GUI（Herdr.app）で gwt を動かすための補助。
#
# GUI のペインは herdr-core（hc）のターミナルで、HERDR_ENV / HERDR_PANE_ID は入らず、
# TUI の `herdr worktree open` / `herdr pane move` も使えない。代わりに herdr-app と hc を使う。
#
# 使い方:
#   herdr-gui active                    … GUI のターミナルの中なら終了コード0
#   herdr-gui open <branch> <worktree> [--no-agent] [--no-focus] [--task <文>] [--title <名前>]
#                                       … ワークツリーをスペースとして開く（gwt が呼ぶ）。
#                                         --title はスペースの名前（そこで何をしているか）
#                                         GUI の mod（issues-env）のようにターミナルの外から呼ぶときは
#                                         HERDR_GUI=1 を付ける（会話を移す予約はせず、常にスペースを開く）
#   herdr-gui move-on-stop              … Stop フックの入力を標準入力で受け、移動の予約があれば実行する
#                                         （HERDR_GUI_ASKED=1 なら移った先で続きを促さない）
#   herdr-gui cancel                    … このターミナルの移動の予約を取り消す
#
# agent（Claude Code）のペインから gwt を呼んだときは、新しい agent を立てずに会話ごと移す。
# GUI はペインを別スペースへ動かせても、プロセスの作業ディレクトリは変えられない。GUI は
# ブランチをプロセスのディレクトリで判断するので、動かしただけでは main のスペースに見えてしまう。
# そこで「ターンが終わったら、ワークツリーのスペースで同じ会話を --resume し、元のペインを閉じる」
# を予約し、Stop フック（herdr-activity.sh）がそれを実行する。ターンの途中で閉じると、
# 最後のツール結果がトランスクリプトに残らないため。
# TUI ではペインがターンの途中で移り、agent はそのまま作業を続ける。GUI でも止まらないよう、
# 再開するときに「移ったので続きを進めて」を最初の入力として渡す。ただし人間に聞いて終わった
# ターン（Stop フックが HERDR_GUI_ASKED=1 を渡す）では渡さず、返事を待つ。
#
# スペースの名前は「そこで何をしているか」にする。名前を付けないとフォルダ名（<repo>=<branch>）に
# なるが、ブランチ名はサイドバーに灰色で別に出るので冗長になる。--title があればそれを、無ければ
# 会話を移すときに限り Claude Code のセッションのタイトル（ターミナルのタイトル）を使う。
# 既にあるスペースは、名前がフォルダ名のまま（人が付け直していない）ときだけ付け直す。
set -u

STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/herdr-env/gui-move"
WT_SETUP="${HERDR_WT_SETUP:-$HOME/.config/herdr/plugins/local/worktree-setup/bin/wt_setup.py}"

die() { echo "herdr-gui: $*" >&2; exit 1; }

active() {
  [ -n "${HC_TERMINAL_ID:-}" ] && [ "${HERDR_ENV:-}" != 1 ] && command -v hc >/dev/null 2>&1
}

# GUI のターミナルの中か、GUI の mod から HERDR_GUI=1 で呼ばれたか（open だけが受け付ける）
gui() {
  active || { [ "${HERDR_GUI:-}" = 1 ] && command -v herdr-app >/dev/null 2>&1; }
}

# ワークツリーのフォルダを開いているスペースの id（無ければ空）
space_for() {
  herdr-app ls 2>/dev/null | jq -r --arg f "$1" '[.spaces[] | select(.folder == $f and (.machine == null))][0].id // empty'
}

# ターミナルのタイトルをスペースの名前に使える形にする。Claude Code が頭に付ける記号（✳ や
# スピナー）を外し、セッションのタイトルが付く前の既定の名前は捨てる（空を返す）
clean_title() {
  local t
  t="$(printf '%s' "$1" | perl -CSD -pe 's/^[^\p{L}\p{N}]+//; s/\s+$//')"
  case "$t" in
    ''|'Claude Code'|claude|zsh|-zsh|bash) return 0 ;;
  esac
  [[ "$t" =~ ^[0-9.]+$ ]] && return 0
  printf '%s' "$t"
}

# 既にあるスペースに名前を付ける。名前がフォルダ名のまま（既定）のときだけで、人が付けた名前は残す
name_space() {
  local id="$1" folder="$2" title="$3" cur
  [ -n "$id" ] && [ -n "$title" ] || return 0
  cur="$(herdr-app ls 2>/dev/null | jq -r --arg id "$id" '.spaces[] | select(.id == $id) | .name')"
  [ "$cur" = "$(basename "$folder")" ] || return 0
  herdr-app do space.rename "$(jq -n --arg id "$id" --arg n "$title" '{id: $id, name: $n}')" >/dev/null 2>&1 || true
}

# スペースを開く（あれば新しいタブ、無ければ新しいスペース）。args は space.new / tab.new の引数。
# 開いたスペースの id を OPENED_SPACE に、結果の JSON を OPENED_OUT に、tab / space を OPENED_HOW に入れる
open_in_space() {
  local folder="$1" title="$2" args="$3" space out
  space="$(space_for "$folder")"
  if [ -n "$space" ]; then
    out="$(herdr-app do tab.new "$(jq --arg s "$space" '. + {space: $s}' <<<"$args")")" || return 1
    name_space "$space" "$folder" "$title"
    OPENED_HOW="tab"
  else
    [ -n "$title" ] && args="$(jq --arg n "$title" '. + {name: $n}' <<<"$args")"
    out="$(herdr-app do space.new "$args")" || return 1
    space="$(jq -r '.space // empty' <<<"$out")"
    OPENED_HOW="space"
  fi
  OPENED_SPACE="$space"
  OPENED_OUT="$out"
}

# このターミナルで動いているのが agent（Claude Code）か
self_is_agent() {
  [ -n "${HC_TERMINAL_ID:-}" ] || return 1
  hc get "$HC_TERMINAL_ID" 2>/dev/null | jq -e '
    (.status.app // "") == "claude-code"
    or ((.foreground.argv[0] // "") | split("/") | last) == "claude"' >/dev/null 2>&1
}

# 引数を zsh -c に渡せる1行にする
quote_words() {
  local out="" w
  for w in "$@"; do out+="$(printf '%q' "$w") "; done
  printf '%s' "${out% }"
}

# 新しく起動した Claude Code の「このフォルダを信頼するか」を通す（切り離して動かす）。
# gwq のワークツリーは ghq 管理下の自分のリポジトリなので、TUI の gwt と同じく自動で通す。
# 既定のカーソルは「No, exit」にあることがあり、Enter だけ送ると Claude が終了する。
# 「Yes, I trust this folder」にカーソルが合ったのを確かめてから確定する。
trust_later() {
  local t="$1"
  [ -n "$t" ] || return 0
  nohup /bin/bash -c '
    t="$1"; screen=""
    for _ in $(seq 1 60); do
      screen="$(hc read "$t" --lines 40 2>/dev/null)" || exit 0
      case "$screen" in *"I trust this folder"*) break ;; esac
      # 信頼済みのフォルダなら確認は出ない。会話が表示されたら終わり
      case "$screen" in *"? for shortcuts"*|*"bypass permissions"*) exit 0 ;; esac
      sleep 0.5
    done
    case "$screen" in *"I trust this folder"*) ;; *) exit 0 ;; esac
    for _ in 1 2 3 4; do
      case "$screen" in *"❯ Yes, I trust this folder"*) hc keys "$t" enter >/dev/null; exit 0 ;; esac
      hc keys "$t" down >/dev/null; sleep 0.3
      screen="$(hc read "$t" --lines 40 2>/dev/null)"
    done' _ "$t" >/dev/null 2>&1 &
}

# ワークツリーの初回セットアップ（.env などのコピーと依存のインストール）。TUI では worktree-setup
# プラグインが worktree.opened で走らせるが、GUI はイベントを送らないので、スペースを開いたあとここで呼ぶ。
# 2回目以降は wt_setup.py が印（<git-dir>/herdr-setup.json）を見て何もしない
setup_later() {
  [ -f "$WT_SETUP" ] || return 0
  nohup /usr/bin/python3 "$WT_SETUP" run --path "$1" >/dev/null 2>&1 &
}

cmd_open() {
  local branch="" worktree="" start_agent=1 focus=true task="" title=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --no-agent) start_agent=0; shift ;;
      --no-focus) focus=false; shift ;;
      --task) task="${2:-}"; shift 2 ;;
      --title) title="${2:-}"; shift 2 ;;
      -*) die "不明なオプション: $1" ;;
      *)
        if [ -z "$branch" ]; then branch="$1"; elif [ -z "$worktree" ]; then worktree="$1"; else die "引数が多すぎます: $1"; fi
        shift ;;
    esac
  done
  [ -n "$branch" ] && [ -n "$worktree" ] || die "usage: herdr-gui open <branch> <worktree> [--no-agent] [--no-focus] [--task <文>]"
  gui || die "Herdr の GUI のターミナルではありません"

  # agent のペインから呼ばれたら、会話ごと移す予約をして終わる
  if [ "$start_agent" = 1 ] && [ -z "$task" ] && self_is_agent; then
    mkdir -p "$STATE_DIR"
    jq -n --arg wt "$worktree" --arg br "$branch" --argjson focus "$focus" --arg title "$title" \
      '{worktree: $wt, branch: $br, focus: $focus, title: $title}' > "$STATE_DIR/$HC_TERMINAL_ID"
    echo "gwt: ${branch} -> ${worktree}"
    echo "gwt: このターンが終わると、この会話をワークツリーのスペースで続けます（このペインは閉じます）。"
    echo "gwt: 取り消すときは herdr-gui cancel"
    return 0
  fi

  local args
  args="$(jq -n --arg folder "$worktree" --argjson focus "$focus" '{folder: $folder, focus: $focus}')"
  if [ "$start_agent" = 1 ]; then
    # ログインシェル経由で起動する（PATH と、claude のラッパー関数があればそれを通すため）。
    # 依頼文は claude の最初のプロンプトとして引数で渡す（GUI の task 引数はシェル経由の起動には届かない）
    # 日本語や改行をシェルの引用に通すと化けるので、ファイルに書いて起動時に読む
    local c="claude"
    if [ -n "$task" ]; then
      local tf
      mkdir -p "$STATE_DIR/tasks"
      tf="$(mktemp "$STATE_DIR/tasks/task.XXXXXX")" || return 1
      printf '%s' "$task" > "$tf"
      c="claude \"\$(<$(printf '%q' "$tf"))\"; rm -f $(printf '%q' "$tf")"
    fi
    args="$(jq --arg c "$c; exec /bin/zsh -l" '. + {argv: ["/bin/zsh", "-lic", $c]}' <<<"$args")"
  fi
  open_in_space "$worktree" "$title" "$args" || return 1
  if [ "$OPENED_HOW" = tab ]; then
    echo "gwt: ${branch} -> ${worktree}（スペース ${OPENED_SPACE} に新しいタブを開きました）"
  else
    echo "gwt: ${branch} -> ${worktree}（スペース ${OPENED_SPACE} を開きました）"
  fi
  [ "$start_agent" = 1 ] && trust_later "$(jq -r '.terminal // empty' <<<"$OPENED_OUT")"
  setup_later "$worktree"
  return 0
}

cmd_move_on_stop() {
  local input f wt focus title sid info pid argv_q cmd args
  input="$(cat)"
  active || return 0
  f="$STATE_DIR/$HC_TERMINAL_ID"
  [ -f "$f" ] || return 0
  # サブエージェントの Stop では動かない
  printf '%s' "$input" | jq -e 'has("agent_id") and (.agent_id != null)' >/dev/null 2>&1 && return 0

  wt="$(jq -r '.worktree' "$f")"
  focus="$(jq -r '.focus' "$f")"
  title="$(jq -r '.title // empty' "$f")"
  rm -f "$f"
  [ -d "$wt" ] || { echo "herdr-gui: ワークツリーがありません: $wt" >&2; return 1; }
  sid="$(printf '%s' "$input" | jq -r '.session_id // empty')"
  [ -n "$sid" ] || return 1

  info="$(hc get "$HC_TERMINAL_ID" 2>/dev/null)" || return 1
  pid="$(jq -r '.foreground.pid // empty' <<<"$info")"
  [ -n "$title" ] || title="$(clean_title "$(jq -r '.title // empty' <<<"$info")")"
  # 今の起動引数を引き継ぎ、会話の指定（--resume / --continue 等）だけ差し替える
  local -a argv=()
  while IFS= read -r a; do argv+=("$a"); done < <(jq -r '
    (.foreground.argv // ["claude"]) as $a
    | [range(1; $a | length)] as $idx
    | reduce $idx[] as $i ({out: [], skip: false};
        if .skip then .skip = false
        elif ($a[$i] | test("^(-r|--resume|--session-id|--fork-session)$")) then .skip = ($a[$i] != "--fork-session")
        elif ($a[$i] | test("^(-c|--continue)$|^--resume=|^--session-id=")) then .
        elif ($a[$i] | startswith("--dangerously")) and (.out | index([$a[$i]])) then .
        else .out += [$a[$i]] end)
    | .out[]' <<<"$info")
  argv_q="$(quote_words claude ${argv[@]+"${argv[@]}"} --resume "$sid")"
  # 続きを促す最初の入力。日本語をシェルの引用に通すと化けるので、ファイルに書いて起動時に読む
  if [ "${HERDR_GUI_ASKED:-0}" != 1 ]; then
    local tf
    mkdir -p "$STATE_DIR/tasks"
    if tf="$(mktemp "$STATE_DIR/tasks/resume.XXXXXX")"; then
      printf '%s' "（自動）gwt の予約どおり、この会話をワークツリー ${wt} のスペースに移しました。作業ディレクトリはワークツリーです。移る前に予定していた続きを、そのまま進めてください。" > "$tf"
      argv_q+=" \"\$(<$(printf '%q' "$tf"))\"; rm -f $(printf '%q' "$tf")"
    fi
  fi
  [ -n "${CLAUDE_CONFIG_DIR:-}" ] && argv_q="CLAUDE_CONFIG_DIR=$(printf '%q' "$CLAUDE_CONFIG_DIR") $argv_q"
  # 元の claude が終わるのを待ってから再開する（同じ会話を2つのプロセスで開かない）
  cmd=""
  [ -n "$pid" ] && cmd="while kill -0 $pid 2>/dev/null; do sleep 0.2; done; "
  cmd+="$argv_q; exec /bin/zsh -l"

  args="$(jq -n --arg folder "$wt" --argjson focus "$focus" --arg c "$cmd" \
    '{folder: $folder, focus: $focus, argv: ["/bin/zsh", "-lic", $c]}')"
  open_in_space "$wt" "$title" "$args" || return 1
  trust_later "$(jq -r '.terminal // empty' <<<"$OPENED_OUT")"
  setup_later "$wt"
  # 元のペインを閉じる。フックが返ってから閉じるよう、切り離して少し待つ
  nohup /bin/sh -c "sleep 1; hc kill $(printf '%q' "$HC_TERMINAL_ID")" >/dev/null 2>&1 &
}

cmd_cancel() {
  active || die "Herdr の GUI のターミナルではありません"
  rm -f "$STATE_DIR/$HC_TERMINAL_ID" && echo "gwt: 移動の予約を取り消しました"
}

case "${1:-}" in
  active) active ;;
  open) shift; cmd_open "$@" ;;
  move-on-stop) cmd_move_on_stop ;;
  cancel) cmd_cancel ;;
  ''|-h|--help|help) awk 'NR > 1 && !/^#/ { exit } NR > 1 { sub(/^# ?/, ""); print }' "$0" ;;
  *) die "不明なサブコマンド: $1" ;;
esac
