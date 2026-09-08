#!/bin/sh
# 外部待ち（CodeRabbit のレビュー、CI、デプロイ等）を herdr サイドバーに出すための印。
#
# herdr は Claude Code のペインを idle / done としか言えないので、「もう用は無い」と
# 「外部の返事を待っているだけ」が同じ見た目になる。この印を立てておくと、
# herdr-activity.sh が state_text と $act を待ち用の表示に差し替える。
#
# 使い方:
#   herdr-wait set "CodeRabbit レビュー待ち"   … 印を立てる（--pane で他ペインも指定可）
#   herdr-wait clear                           … 印を外す
#   herdr-wait status                          … このペインの印を出す（無ければ終了コード1）
#   herdr-wait label                           … サイドバー用の1行（経過時間つき）。無ければ空
#   herdr-wait list                            … 印が立っている全ペイン
#
# 印は TTL（既定6時間）を過ぎると自動で無効になる。消し忘れても居座らない。

set -u

STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/herdr-wait"
TTL_SECONDS="${HERDR_WAIT_TTL:-21600}"

usage() {
  sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
}

resolve_pane() {
  if [ -n "${OPT_PANE:-}" ]; then
    printf '%s' "$OPT_PANE"
    return 0
  fi
  if [ -n "${HERDR_PANE_ID:-}" ]; then
    printf '%s' "$HERDR_PANE_ID"
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

# 印が有効なら "開始epoch<TAB>ラベル" を出す。期限切れなら消して何も出さない。
read_marker() {
  _f="$(marker_path "$1")"
  [ -f "$_f" ] || return 1
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

# herdr に印の有無を反映させる。herdr-activity.sh と同じ source を使うので
# 片方が書けばもう片方の表示も置き換わる。
push_state_labels() {
  _pane="$1"
  _waiting="$2"
  command -v herdr >/dev/null 2>&1 || return 0
  if [ "$_waiting" = 1 ]; then
    herdr pane report-metadata "$_pane" \
      --source custom:activity --applies-to-source herdr:claude \
      --token act="$3" \
      --state-label done="⏳ 外部待ち" \
      --state-label idle="⏳ 外部待ち" \
      --state-label working="… 実行中" \
      --state-label blocked="⚠ 要承認" \
      --state-label unknown="? 不明" >/dev/null 2>&1
  else
    herdr pane report-metadata "$_pane" \
      --source custom:activity --applies-to-source herdr:claude \
      --token act="待機中" \
      --state-label done="✅ 完了 未確認" \
      --state-label idle="待機" \
      --state-label working="… 実行中" \
      --state-label blocked="⚠ 要承認" \
      --state-label unknown="? 不明" >/dev/null 2>&1
  fi
}

cmd="${1:-}"
[ $# -gt 0 ] && shift

OPT_PANE=""
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
    printf '%s\t%s\n' "$(date +%s)" "$label" > "$(marker_path "$pane")"
    push_state_labels "$pane" 1 "⏳ $label"
    echo "waiting: $pane — $label"
    ;;

  clear)
    pane="$(resolve_pane)" || { echo "herdr-wait: ペインを特定できません（--pane を指定してください）" >&2; exit 1; }
    rm -f "$(marker_path "$pane")"
    push_state_labels "$pane" 0 ""
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

  ''|-h|--help|help)
    usage
    ;;

  *)
    echo "herdr-wait: 不明なサブコマンド: $cmd" >&2
    usage >&2
    exit 1
    ;;
esac
