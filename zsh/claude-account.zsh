# Claude Code の起動（claude-account でアカウントを選べるようにしたもの）
#   claude              … 既定のアカウント（claude-account default）。シェルに CLAUDE_CONFIG_DIR があればそのアカウント
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
    # 指定が無ければ既定のアカウント（accounts の @default。上限に達していれば次に使えるもの）
    if [[ -z "${acct}" && -z "${CLAUDE_CONFIG_DIR:-}" ]] && command -v claude-account > /dev/null 2>&1; then
        acct="$(claude-account default 2> /dev/null)"
    fi
    if [[ -n "${acct}" ]]; then
        claude-account exec "${acct}" --dangerously-skip-permissions "$@"
    else
        command claude --dangerously-skip-permissions "$@"
    fi
}
