# Claude Code の起動（claude-account でアカウントを選べるようにしたもの）
#   claude              … 既定のアカウント。シェルに CLAUDE_CONFIG_DIR があればそのアカウント
#   claude -A <id> …    … そのアカウントで起動（CLAUDE_ACCOUNT=<id> でも同じ）
# タイトルは書き換えない（Claude Code の OSC タイトルは herdr の状態検出の入力）。
# --dangerously-skip-permissions は好みで外す。
unalias claude 2>/dev/null
claude() {
    local acct="${CLAUDE_ACCOUNT:-}"
    case "${1:-}" in
        -A|--account) acct="$2"; shift 2 ;;
        --account=*)  acct="${1#*=}"; shift ;;
    esac
    if [[ -n "${acct}" ]]; then
        claude-account exec "${acct}" --dangerously-skip-permissions "$@"
    else
        command claude --dangerously-skip-permissions "$@"
    fi
}

# Herdr.app: herdr-core が落ちて（Herdr の再起動・クラッシュ・ログアウト）ターミナルがシェルに戻ったら、
# 落ちる前に動いていた claude の会話を --resume で戻す（herdr-revive。/exit で終えた会話は戻さない）
if [[ -o interactive && -n "${HC_TERMINAL_ID:-}" ]] && command -v herdr-revive > /dev/null 2>&1; then
    _herdr_revive_once() {
        add-zsh-hook -d precmd _herdr_revive_once
        unfunction _herdr_revive_once
        local line
        line="$(herdr-revive --line $$ 2> /dev/null)"
        [[ -n "${line}" ]] && { print -s -- "${line}"; eval "${line}"; }
    }
    autoload -Uz add-zsh-hook
    add-zsh-hook precmd _herdr_revive_once
fi
