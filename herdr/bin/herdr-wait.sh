#!/bin/sh
# 外部待ち（CodeRabbit のレビュー、CI、デプロイ等）を herdr サイドバーに出すための印。
#
# herdr は Claude Code のペインを idle / done としか言えないので、「もう用は無い」と
# 「外部の返事を待っているだけ」が同じ見た目になる。この印を立てておくと、
# herdr-activity.sh が state_text と $act を待ち用の表示に差し替える。
#
# 使い方:
#   herdr-wait set "CodeRabbit レビュー待ち"   … 印を立てる（--pane で他ペインも指定可）
#   herdr-wait set --pid <PID> "CI 待ち"       … PID のプロセスが生きている間だけ有効な印
#   herdr-wait clear                           … 印を外す
#   herdr-wait clear --if-pid <PID>            … その PID で立てた印のときだけ外す
#   herdr-wait status                          … このペインの印を出す（無ければ終了コード1）
#   herdr-wait label                           … サイドバー用の1行（経過時間つき）。無ければ空
#   herdr-wait list                            … 印が立っている全ペイン
#   herdr-wait human on|off|status             … 「人間の返事待ち」の旗（Stop / UserPromptSubmit フックが操作する）
#   herdr-wait refresh [--act <文言>]          … 印と旗から state_text と $act を herdr に出し直す
#
# 印は TTL（既定6時間）を過ぎると自動で無効になる。消し忘れても居座らない。
# --pid 付きの印は、そのプロセスが終わった時点でも無効になる（監視ループが死んでも残らない）。
#
# 人間の返事待ちの旗は、外部待ちの印より優先する。印が残っていても、人間に問いかけて
# ターンを終えたペインは working に保たず「💬 要返答」と出す（印は消さずに残す）。

set -u

STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/herdr-wait"
TTL_SECONDS="${HERDR_WAIT_TTL:-21600}"

usage() {
  sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'
}

resolve_pane() {
  if [ -n "${OPT_PANE:-}" ]; then
    printf '%s' "$OPT_PANE"
    return 0
  fi
  if [ -n "${HERDR_PANE_ID:-}" ]; then
    # gwt がペインを別 workspace へ移すと pane_id が変わるが、プロセスの HERDR_PANE_ID は
    # 古いまま残る。herdr は古い ID も今のペインに解決するので、今の ID に直して印を合わせる
    # （agent-attention は今の ID で印を探す）。
    _cur="$(herdr pane get "$HERDR_PANE_ID" 2>/dev/null | jq -r '.result.pane.pane_id // empty' 2>/dev/null)"
    printf '%s' "${_cur:-$HERDR_PANE_ID}"
    return 0
  fi
  if [ -n "${HERDR_ACTIVE_PANE_ID:-}" ]; then
    printf '%s' "$HERDR_ACTIVE_PANE_ID"
    return 0
  fi
  return 1
}

marker_path() {
  # pane_id の ':' はファイル名に使えるが、念のため '_' に寄せる
  printf '%s/%s' "$STATE_DIR" "$(printf '%s' "$1" | tr ':/' '__')"
}

# 経過時間を「12分」「2時間5分」の形にする
elapsed_text() {
  _sec="$1"
  _min=$((_sec / 60))
  if [ "$_min" -lt 60 ]; then
    printf '%d分' "$_min"
  else
    printf '%d時間%d分' $((_min / 60)) $((_min % 60))
  fi
}

# 印に結びついた PID（2行目の "pid <N>"）。無ければ空。
marker_pid() {
  sed -n '2s/^pid //p' "$1" 2>/dev/null
}

# 印が有効なら "開始epoch<TAB>ラベル" を出す。期限切れなら消して何も出さない。
read_marker() {
  _f="$(marker_path "$1")"
  [ -f "$_f" ] || return 1
  _pid="$(marker_pid "$_f")"
  if [ -n "$_pid" ] && ! kill -0 "$_pid" 2>/dev/null; then
    # 待っていたプロセスがもういない。待ちは終わっている
    rm -f "$_f"
    return 1
  fi
  _line="$(head -n 1 "$_f" 2>/dev/null)" || return 1
  _started="${_line%%	*}"
  _label="${_line#*	}"
  case "$_started" in
    ''|*[!0-9]*) rm -f "$_f"; return 1 ;;
  esac
  _now="$(date +%s)"
  if [ $((_now - _started)) -ge "$TTL_SECONDS" ]; then
    rm -f "$_f"
    return 1
  fi
  printf '%s\t%s' "$_started" "$_label"
}

human_path() {
  printf '%s/human/%s' "$STATE_DIR" "$(printf '%s' "$1" | tr ':/' '__')"
}

is_human_turn() {
  [ -f "$(human_path "$1")" ]
}

# herdr に表示を反映させる。herdr-activity.sh と同じ source を使うので
# 片方が書けばもう片方の表示も置き換わる。
# 優先順位: 人間の返事待ち > 外部待ちの印 > 通常。$2 を渡せば $act はそれを使う。
push_labels() {
  _pane="$1"
  _act="${2:-}"
  command -v herdr >/dev/null 2>&1 || return 0
  if is_human_turn "$_pane"; then
    [ -n "$_act" ] || _act="💬 要返答"
    _done="💬 要返答"; _idle="💬 要返答"; _working="… 実行中"
  elif _m="$(read_marker "$_pane")"; then
    [ -n "$_act" ] || _act="⏳ ${_m#*	}"
    _done="⏳ 外部待ち"; _idle="⏳ 外部待ち"; _working="⏳ 外部待ち"
  else
    [ -n "$_act" ] || _act="待機中"
    _done="✅ 完了 未確認"; _idle="待機"; _working="… 実行中"
  fi
  herdr pane report-metadata "$_pane" \
    --source custom:activity --applies-to-source herdr:claude \
    --token act="$_act" \
    --state-label done="$_done" \
    --state-label idle="$_idle" \
    --state-label working="$_working" \
    --state-label blocked="⚠ 要承認" \
    --state-label unknown="? 不明" >/dev/null 2>&1
}

cmd="${1:-}"
[ $# -gt 0 ] && shift

OPT_PANE=""
OPT_PID=""
OPT_IF_PID=""
OPT_ACT=""
args=""
while [ $# -gt 0 ]; do
  case "$1" in
    --pane)
      OPT_PANE="${2:-}"
      shift 2 || break
      ;;
    --pane=*)
      OPT_PANE="${1#--pane=}"
      shift
      ;;
    --pid)
      OPT_PID="${2:-}"
      shift 2 || break
      ;;
    --if-pid)
      OPT_IF_PID="${2:-}"
      shift 2 || break
      ;;
    --act)
      OPT_ACT="${2:-}"
      shift 2 || break
      ;;
    *)
      if [ -z "$args" ]; then args="$1"; else args="$args $1"; fi
      shift
      ;;
  esac
done

case "$cmd" in
  set)
    pane="$(resolve_pane)" || { echo "herdr-wait: ペインを特定できません（--pane を指定してください）" >&2; exit 1; }
    label="${args:-外部待ち}"
    mkdir -p "$STATE_DIR"
    case "$OPT_PID" in
      *[!0-9]*) echo "herdr-wait: --pid は数値で指定してください: $OPT_PID" >&2; exit 1 ;;
    esac
    {
      printf '%s\t%s\n' "$(date +%s)" "$label"
      [ -n "$OPT_PID" ] && printf 'pid %s\n' "$OPT_PID"
    } > "$(marker_path "$pane")"
    push_labels "$pane"
    echo "waiting: $pane — $label"
    ;;

  clear)
    pane="$(resolve_pane)" || { echo "herdr-wait: ペインを特定できません（--pane を指定してください）" >&2; exit 1; }
    f="$(marker_path "$pane")"
    if [ -n "$OPT_IF_PID" ] && [ -f "$f" ] && [ "$(marker_pid "$f")" != "$OPT_IF_PID" ]; then
      # 別の待ち（手で立てた CodeRabbit 待ち等）に置き換わっている。消さない
      echo "kept: ${pane}（別の印）"
      exit 0
    fi
    rm -f "$f"
    push_labels "$pane"
    echo "cleared: $pane"
    ;;

  status)
    pane="$(resolve_pane)" || exit 1
    m="$(read_marker "$pane")" || exit 1
    started="${m%%	*}"
    label="${m#*	}"
    echo "$pane	$label	$(elapsed_text $(( $(date +%s) - started )))"
    ;;

  label)
    pane="$(resolve_pane)" || exit 0
    m="$(read_marker "$pane")" || exit 0
    started="${m%%	*}"
    label="${m#*	}"
    printf '⏳ %s %s' "$label" "$(elapsed_text $(( $(date +%s) - started )))"
    ;;

  statusline)
    # Claude Code のステータスラインに出す1行。
    # herdr の claude 検出マニフェストの live_turn_working ルール
    #   line_regex = '^\s*[*·✢✶✻✽]\s+\S.*…(\s+\(\d+[smh][\s·]|\s*$)'   priority 970
    # に合わせてあるので、この行が画面にある間ペインの状態は working になり、
    # idle（live_prompt_box, priority 950）に落ちない。
    # 先頭の `· ` と末尾の `…` は検出条件そのもの。崩すと働かなくなる。
    # 人間に問いかけてターンを終えたペインでは出さない。出すと「人間の番」が working に隠れる。
    pane="$(resolve_pane)" || exit 0
    is_human_turn "$pane" && exit 0
    m="$(read_marker "$pane")" || exit 0
    started="${m%%	*}"
    label="${m#*	}"
    printf '· ⏳ %s %s…' "$label" "$(elapsed_text $(( $(date +%s) - started )))"
    ;;

  list)
    [ -d "$STATE_DIR" ] || exit 0
    for f in "$STATE_DIR"/*; do
      [ -f "$f" ] || continue
      pane="$(basename "$f" | sed 's/_/:/')"
      m="$(read_marker "$pane")" || continue
      started="${m%%	*}"
      label="${m#*	}"
      echo "$pane	$label	$(elapsed_text $(( $(date +%s) - started )))"
    done
    ;;

  human)
    pane="$(resolve_pane)" || exit 0
    hf="$(human_path "$pane")"
    case "$args" in
      on)
        mkdir -p "$(dirname "$hf")"
        date +%s > "$hf"
        push_labels "$pane" "${OPT_ACT:-}"
        ;;
      off)
        [ -f "$hf" ] || exit 0
        rm -f "$hf"
        push_labels "$pane" "${OPT_ACT:-}"
        ;;
      status)
        [ -f "$hf" ]
        ;;
      *)
        echo "herdr-wait: human は on / off / status のどれかです" >&2
        exit 1
        ;;
    esac
    ;;

  refresh)
    pane="$(resolve_pane)" || exit 0
    push_labels "$pane" "${OPT_ACT:-}"
    ;;

  ''|-h|--help|help)
    usage
    ;;

  *)
    echo "herdr-wait: 不明なサブコマンド: $cmd" >&2
    usage >&2
    exit 1
    ;;
esac
