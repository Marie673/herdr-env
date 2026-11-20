#!/bin/bash
# アカウントを選ぶ。Enter: 新しいタブで起動 / ctrl-s: 元のペインの会話をそのアカウントで開き直す
# 未ログインのアカウントを選ぶと、その場でログインする
# TUI: 元のペインは CLAUDE_ACCOUNT_ORIGIN_PANE（open.sh が渡す）
# GUI（Herdr.app）: 元のターミナルは CLAUDE_ACCOUNT_ORIGIN_TERMINAL（herdr-env-marks の accounts が渡す）
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
origin="${CLAUDE_ACCOUNT_ORIGIN_PANE:-}"
origin_term="${CLAUDE_ACCOUNT_ORIGIN_TERMINAL:-}"
current=""
sid=""
if [ -n "$origin" ]; then
  sid=$(herdr agent list 2>/dev/null | jq -r --arg p "$origin" \
    '.result.agents[] | select(.pane_id == $p and .agent == "claude") | .agent_session.value // empty' | head -1)
elif [ -n "$origin_term" ]; then
  sid=$(hc get "$origin_term" --json 2>/dev/null | jq -r '
    .labels["herdr-env.session"]
    // ((.foreground.argv // []) as $a | ($a | index("--resume")) as $i | if $i then $a[$i + 1] else empty end)
    // empty')
fi
[ -n "$sid" ] && current=$(claude-account of-session "$sid" 2>/dev/null)
header="enter: 新しいタブで起動 / ctrl-s: このペインの会話をこのアカウントで続ける / esc: やめる"
[ -n "$current" ] && header="$header"$'\n'"このペインは今 ${current}"
out=$(claude-account list ${current:+--mark "$current"} | fzf --ansi --expect=ctrl-s --no-sort --layout=reverse \
  --prompt='account> ' --header="$header")
key=$(printf '%s\n' "$out" | sed -n 1p)
line=$(printf '%s\n' "$out" | sed -n 2p)
[ -n "$line" ] || exit 0
id=$(printf '%s\n' "$line" | awk '{ sub(/^\*/, ""); print $1 }')
if printf '%s' "$line" | grep -q '未ログイン'; then
  claude-account login "$id" || { read -r -p "ログインに失敗しました。Enter で閉じます" _; exit 1; }
fi
if [ -n "$origin_term" ]; then
  target=(--terminal "$origin_term")
else
  target=(--pane "$origin")
fi
if [ "$key" = "ctrl-s" ]; then
  out=$(claude-account switch "$id" "${target[@]}" 2>&1)
else
  out=$(claude-account start "$id" "${target[@]}" 2>&1)
fi
rc=$?
if [ $rc -ne 0 ]; then
  printf '%s\n' "$out"
  read -r -p "Enter で閉じます" _
fi
[ -z "$origin_term" ] && herdr notification show "Claude アカウント" --body "$out" --sound none >/dev/null 2>&1
exit $rc
