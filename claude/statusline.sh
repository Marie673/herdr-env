#!/bin/bash

input=$(cat)

format_bar() {
  local remaining="$1"
  local filled i bar
  filled=$((remaining / 10))
  [ "$remaining" -gt 0 ] && [ "$filled" -eq 0 ] && filled=1
  bar=''
  i=0
  while [ "$i" -lt 10 ]; do
    if [ "$i" -lt "$filled" ]; then
      bar="${bar}━"
    else
      bar="${bar}─"
    fi
    i=$((i + 1))
  done
  printf '%s' "$bar"
}

format_window() {
  local label="$1"
  local used="$2"
  local resets_at="$3"

  [ -z "$used" ] && return

  local remaining reset_text color reset dim bold bar
  remaining=$(awk -v used="$used" 'BEGIN { printf "%.0f", 100 - used }')
  reset_text=""
  if [ -n "$resets_at" ] && [ "$resets_at" != "null" ]; then
    if [ "$label" = "5h" ]; then
      reset_text=$(date -r "$resets_at" '+%H:%M' 2>/dev/null)
    else
      reset_text=$(date -r "$resets_at" '+%a %H:%M' 2>/dev/null)
    fi
  fi

  color='\033[32m'
  [ "$remaining" -le 50 ] && color='\033[33m'
  [ "$remaining" -le 20 ] && color='\033[31m'
  reset='\033[0m'
  dim='\033[2m'
  bold='\033[1m'

  bar=$(format_bar "$remaining")

  if [ -n "$reset_text" ]; then
    printf '%b%s%b %b%s%b %b%s%%%b %b↻ %s%b' \
      "$dim" "$label" "$reset" \
      "$color" "$bar" "$reset" \
      "$bold" "$remaining" "$reset" \
      "$dim" "$reset_text" "$reset"
  else
    printf '%b%s%b %b%s%b %b%s%%%b' \
      "$dim" "$label" "$reset" \
      "$color" "$bar" "$reset" \
      "$bold" "$remaining" "$reset"
  fi
}

format_context() {
  local used_pct="$1"
  local tokens_used="$2"
  local window_size="$3"

  [ -z "$used_pct" ] && return

  local remaining color reset dim bold bar label
  remaining=$(awk -v used="$used_pct" 'BEGIN { r = 100 - used; if (r < 0) r = 0; if (r > 100) r = 100; printf "%.0f", r }')

  color='\033[32m'
  [ "$remaining" -le 50 ] && color='\033[33m'
  [ "$remaining" -le 20 ] && color='\033[31m'
  reset='\033[0m'
  dim='\033[2m'
  bold='\033[1m'

  bar=$(format_bar "$remaining")

  if [ -n "$tokens_used" ] && [ -n "$window_size" ] && [ "$window_size" -gt 0 ] 2>/dev/null; then
    label=$(awk -v u="$tokens_used" -v w="$window_size" 'BEGIN {
      uk = u / 1000; wk = w / 1000;
      if (uk >= 100) { printf "%.0fk/%.0fk", uk, wk } else { printf "%.1fk/%.0fk", uk, wk }
    }')
    printf '%bctx%b %b%s%b %b%s%%%b %b%s%b' \
      "$dim" "$reset" \
      "$color" "$bar" "$reset" \
      "$bold" "$remaining" "$reset" \
      "$dim" "$label" "$reset"
  else
    printf '%bctx%b %b%s%b %b%s%%%b' \
      "$dim" "$reset" \
      "$color" "$bar" "$reset" \
      "$bold" "$remaining" "$reset"
  fi
}

ctx_used_pct=$(printf '%s' "$input" | jq -r '.context_window.used_percentage // empty')
ctx_window_size=$(printf '%s' "$input" | jq -r '.context_window.context_window_size // empty')
ctx_input_tokens=$(printf '%s' "$input" | jq -r '.context_window.total_input_tokens // empty')
ctx_output_tokens=$(printf '%s' "$input" | jq -r '.context_window.total_output_tokens // empty')

ctx_tokens_used=""
if [ -n "$ctx_input_tokens" ] && [ -n "$ctx_output_tokens" ]; then
  ctx_tokens_used=$((ctx_input_tokens + ctx_output_tokens))
fi

# Fallback: derive usage from the transcript when the pre-calculated field is unavailable
# (e.g. before the first assistant response of the session).
if [ -z "$ctx_used_pct" ]; then
  transcript_path=$(printf '%s' "$input" | jq -r '.transcript_path // empty')
  [ -z "$ctx_window_size" ] && ctx_window_size=200000
  if [ -n "$transcript_path" ] && [ -f "$transcript_path" ]; then
    last_usage=$(jq -c 'select(.type == "assistant" and .message.usage != null) | .message.usage' "$transcript_path" 2>/dev/null | tail -n 1)
    if [ -n "$last_usage" ]; then
      ctx_tokens_used=$(printf '%s' "$last_usage" | jq -r '(.input_tokens // 0) + (.cache_read_input_tokens // 0) + (.cache_creation_input_tokens // 0) + (.output_tokens // 0)')
      ctx_used_pct=$(awk -v u="$ctx_tokens_used" -v w="$ctx_window_size" 'BEGIN { if (w > 0) printf "%.2f", (u / w) * 100 }')
    fi
  fi
fi

model_name=$(printf '%s' "$input" | jq -r '.model.display_name // .model.id // empty')
effort_level=$(printf '%s' "$input" | jq -r '.effort.level // empty')

five_used=$(printf '%s' "$input" | jq -r '.rate_limits.five_hour.used_percentage // empty')
five_reset=$(printf '%s' "$input" | jq -r '.rate_limits.five_hour.resets_at // empty')
week_used=$(printf '%s' "$input" | jq -r '.rate_limits.seven_day.used_percentage // empty')
week_reset=$(printf '%s' "$input" | jq -r '.rate_limits.seven_day.resets_at // empty')

parts=()
if [ -n "$model_name" ]; then
  model_part=$(printf '%b%s%b' '\033[1;36m' "$model_name" '\033[0m')
  [ -n "$effort_level" ] && model_part="$model_part $(printf '%b%s%b' '\033[2m' "$effort_level" '\033[0m')"
  parts+=("$model_part")
fi
ctx=$(format_context "$ctx_used_pct" "$ctx_tokens_used" "$ctx_window_size")
five=$(format_window '5h' "$five_used" "$five_reset")
week=$(format_window '7d' "$week_used" "$week_reset")
[ -n "$ctx" ] && parts+=("$ctx")
[ -n "$five" ] && parts+=("$five")
[ -n "$week" ] && parts+=("$week")

if [ "${#parts[@]}" -gt 0 ]; then
  printf '%s' "${parts[0]}"
  i=1
  while [ "$i" -lt "${#parts[@]}" ]; do
    printf '  %b│%b  %s' '\033[2m' '\033[0m' "${parts[$i]}"
    i=$((i + 1))
  done
  printf '\n'
else
  printf 'usage --\n'
fi

# 外部待ち（CodeRabbit のレビュー等）の行。
# herdr の claude 検出マニフェストは `· ○○…` の形の行を "working" と読むので、
# この1行を出しておくと待っている間ペインが idle に落ちない（herdr-wait 参照）。
if [ -x "$HOME/.config/herdr/bin/herdr-wait.sh" ]; then
  wait_line=$("$HOME/.config/herdr/bin/herdr-wait.sh" statusline 2>/dev/null)
  if [ -n "$wait_line" ]; then
    printf '%s\n' "$wait_line"
  fi
fi

# 終了コードが 0 以外だと Claude Code はステータスラインを空表示にするため、
# 直前の判定結果に関係なく 0 で終える。
exit 0
