# herdr の API ソケットへ 1 リクエスト投げる共通処理
herdr_sock() {
  local s="${HERDR_SOCKET_PATH:-}"
  if [ -z "$s" ]; then
    s="${HERDR_CONFIG_PATH:+$(dirname "$HERDR_CONFIG_PATH")/herdr.sock}"
  fi
  [ -S "$s" ] || s="$HOME/.config/herdr/herdr.sock"
  printf '%s' "$s"
}

herdr_api() {
  printf '%s\n' "$1" | nc -U -w 2 "$(herdr_sock)"
}

herdr_state_write() {
  local dir="${HERDR_PLUGIN_STATE_DIR:-$HOME/.config/herdr/plugins/local/agent-view-space}"
  mkdir -p "$dir" && printf '%s' "$1" > "$dir/scope"
}
