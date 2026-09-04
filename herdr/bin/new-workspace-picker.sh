#!/bin/zsh

set -u

herdr_bin="${HERDR_BIN_PATH:-/opt/homebrew/bin/herdr}"
root=$(ghq root) || exit 1

out=$(
  ghq list \
    | awk '/^github\.com\// && !/=/' \
    | fzf \
        --expect=ctrl-b,alt-enter \
        --prompt='repo> ' \
        --header='enter: main / ctrl-b(alt-enter): ブランチ選択' \
        --preview "ls -la ${root}/{}"
)

[[ -z "$out" ]] && exit 0

key=$(printf '%s\n' "$out" | sed -n '1p')
selection=$(printf '%s\n' "$out" | sed -n '2p')
[[ -z "$selection" ]] && exit 0

repo_path="${root}/${selection}"
workspace_path="$repo_path"
workspace_label="${repo_path:t}"

if [[ "$key" == "ctrl-b" || "$key" == "alt-enter" ]]; then
  worktree=$(
    cd "$repo_path" && \
      gwq list --json 2>/dev/null \
        | jq -r '.[] | select(.branch != "HEAD") | "\(.branch)\t\(.path)"' \
        | fzf \
            --with-nth=1 \
            --prompt='branch> ' \
            --preview 'git -C {2} log --oneline --graph -20'
  )
  [[ -z "$worktree" ]] && exit 0

  workspace_label=$(printf '%s\n' "$worktree" | cut -f1)
  workspace_path=$(printf '%s\n' "$worktree" | cut -f2)
fi

exec "$herdr_bin" workspace create \
  --cwd "$workspace_path" \
  --label "$workspace_label" \
  --focus
