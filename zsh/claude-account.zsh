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

# Herdr.app: herdr-core が落ちて（Herdr の再起動・クラッシュ・ログアウト）ターミナルがシェルに戻ったら、
# 落ちる前に動いていた claude の会話を --resume で戻す（herdr-revive。/exit で終えた会話は戻さない）
if [[ -o interactive && -n "${HC_TERMINAL_ID:-}" ]] && command -v herdr-revive > /dev/null 2>&1; then
    # 打つ行は最初の precmd で決め、動かすのはプロンプトが出た後（zle の line-init）にする。
    # precmd の時点では p10k の instant prompt が標準出力をまだファイルへ流しているので、そこで
    # claude を動かすと端末につながっていないと見て print モードで起動し、
    # 「No deferred tool marker found in the resumed session」で即終了する（Herdr.app の再起動後に
    # 多くの会話が戻らなかった原因）。終わるときに SessionEnd がラベルを消すので、後から戻せもしない
    typeset -g _herdr_revive_line=""
    _herdr_revive_once() {
        add-zsh-hook -d precmd _herdr_revive_once
        unfunction _herdr_revive_once
        _herdr_revive_line="$(herdr-revive --line $$ 2> /dev/null)"
        [[ -n "${_herdr_revive_line}" ]] && add-zle-hook-widget line-init _herdr_revive_run
    }
    _herdr_revive_run() {
        add-zle-hook-widget -d line-init _herdr_revive_run
        BUFFER="${_herdr_revive_line}"
        unset _herdr_revive_line
        zle .accept-line
    }
    autoload -Uz add-zsh-hook add-zle-hook-widget
    add-zsh-hook precmd _herdr_revive_once
fi
