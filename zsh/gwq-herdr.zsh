# ghq / gwq / herdr ワークフロー用の zsh 関数群
# .zshrc から `source ~/path/to/gwq-herdr.zsh` で読み込む。
# 依存: ghq, gwq, herdr, fzf, jq

# ------ ターミナルタイトル用の短縮パス ------
# workspace 配下のパスから短縮タイトルを生成
#   ~/workspace/worktree/github.com/owner/repo=branch → owner/repo=branch
#   ~/workspace/github.com/owner/repo                → owner/repo
#   その他 workspace 配下                             → workspace/以降
#   workspace 外                                     → ベースネーム
_workspace_shortpath() {
    local p="$1"
    case "$p" in
        */workspace/worktree/github.com/*) echo "${p#*/workspace/worktree/github.com/}" ;;
        */workspace/github.com/*)          echo "${p#*/workspace/github.com/}" ;;
        */workspace/*)                     echo "${p#*/workspace/}" ;;
        *)                                 echo "${p##*/}" ;;
    esac
}

# 注意: claude をタイトル書き換えでラップしない。Claude Code が設定する OSC タイトル
# （作業中はスピナー、待機中は ✳）は herdr のエージェント状態検出の入力なので、
# ラッパーで塗り替えると working が idle に誤検出される。タブの識別は
# herdr の agents パネル（workspace · tab）に任せる。

# ------ ペイン間 cd 同期（Ghostty 等の素のターミナル用） ------
_GHOSTTY_SYNC_FILE="/tmp/.ghostty-sync-default"

# sync-group: 同期グループを設定（同じグループ名のペインが連動する）
function sync-group() {
    if [[ -z "$1" ]]; then
        echo "Current group: ${GHOSTTY_SYNC_GROUP:-default}"
        return 0
    fi
    export GHOSTTY_SYNC_GROUP="$1"
    _GHOSTTY_SYNC_FILE="/tmp/.ghostty-sync-${1}"
    echo "Sync group set to: $1"
}

if [[ -n "${GHOSTTY_SYNC_GROUP}" ]]; then
    _GHOSTTY_SYNC_FILE="/tmp/.ghostty-sync-${GHOSTTY_SYNC_GROUP}"
fi

# 他ペインで実行して同じグループの移動先に追従
function follow() {
    local target
    target=$(cat "${_GHOSTTY_SYNC_FILE}" 2>/dev/null)
    [[ -z "${target}" || ! -d "${target}" ]] && echo "No sync target" && return 1
    cd "${target}" || return 1
}

# ------ ghq: リポジトリ選択 ------
function ghq-path() {
    local root
    root=$(ghq root)
    ghq list | fzf --preview "ls -la ${root}/{}" | while read -r line; do
        [[ -n "${line}" ]] && echo "${root}/${line}"
    done
}

# dev: リポジトリ（+ worktree/ブランチ）を fzf で選んで cd
#   Enter            : メインリポジトリへ移動
#   Ctrl-B/Alt-Enter : そのリポジトリの worktree/ブランチを選んで移動
function dev() {
    local root out key sel repo_path moveto
    root=$(ghq root)

    out=$(ghq list | grep '^github\.com/' | grep -v '=' \
        | fzf --expect=ctrl-b,alt-enter \
              --prompt="repo> " \
              --header="enter: main / ctrl-b(alt-enter): ブランチ選択" \
              --preview "ls -la ${root}/{}")
    [[ -z "${out}" ]] && return 0
    key=$(printf '%s\n' "${out}" | head -1)
    sel=$(printf '%s\n' "${out}" | sed -n 2p)
    [[ -z "${sel}" ]] && return 0
    repo_path="${root}/${sel}"

    if [[ "${key}" == "ctrl-b" || "${key}" == "alt-enter" ]]; then
        moveto=$(cd "${repo_path}" && gwq list --json 2>/dev/null \
            | jq -r '.[] | "\(if .is_main then "● " else "  " end)\(.branch)\t\(.path)"' \
            | fzf --with-nth=1 --prompt="branch> " \
                  --preview 'git -C {2} log --oneline --graph -20' \
            | cut -f2)
        [[ -z "${moveto}" ]] && return 0
    else
        moveto="${repo_path}"
    fi

    cd "${moveto}" || return 1
    echo "${moveto}" > "${_GHOSTTY_SYNC_FILE}"

    local repo_name
    repo_name=$(_workspace_shortpath "$moveto")
    print -Pn "\e]0;${repo_name}\a"
}

# gsw: git switch を fzf で。gwq worktree があれば切り替えずにそこへ cd する
function gsw() {
    local branch
    branch=$(
        git branch -a --format='%(refname:short)' \
        | sed 's|^origin/||' \
        | sort -u \
        | grep -vE '^(HEAD|origin)$' \
        | fzf --prompt="git switch> " \
              --preview "git log --oneline --graph -20 {}"
    )
    [[ -z "$branch" ]] && return 0

    local wt_path
    wt_path=$(gwq list --json 2>/dev/null | jq -r --arg b "$branch" '.[] | select(.branch == $b) | .path')

    if [[ -n "$wt_path" && -d "$wt_path" ]]; then
        echo "Worktree found: $wt_path"
        cd "$wt_path" || return 1
        echo "$wt_path" > "${_GHOSTTY_SYNC_FILE}"
        local repo_name
        repo_name=$(_workspace_shortpath "$wt_path")
        print -Pn "\e]0;${repo_name}\a"
    else
        git switch "$branch"
    fi
}

# wt: 現在のリポジトリの worktree を fzf で選んで cd
function wt() {
    local worktree
    worktree=$(gwq list --json | jq -r '.[] | select(.branch != "HEAD") | "\(.branch)\t\(.path)"' | fzf --with-nth=1 --preview 'git -C {2} log --oneline -10' | cut -f2)
    [[ -z "${worktree}" ]] && return 0
    cd "${worktree}" || return 1
    echo "${worktree}" > "${_GHOSTTY_SYNC_FILE}"

    local repo_name
    repo_name=$(_workspace_shortpath "$worktree")
    print -Pn "\e]0;${repo_name}\a"
}

function wta() {
    wt
}

# gwq のワークツリー一覧から、指定ブランチのパスを返す（本体リポジトリは除く）
function _gwt_path() {
    gwq list --json 2>/dev/null | jq -r --arg b "$1" '.[] | select(.branch == $b and .is_main == false) | .path' | head -1
}

# gwt: gwq + herdr の作業開始コマンド。
#   gwt <branch> [--no-agent] [--no-focus]
# ワークツリーを用意し、herdr の workspace として開いて Claude Code を起動する。
# 本体リポジトリ(main/master)はブランチ切り替えを禁止しているので、作業は必ずこの入口から始める。
# herdr の外で実行した場合は、ワークツリーへ cd するだけ。
function gwt() {
    local branch="" start_agent=1 focus=1 arg
    for arg in "$@"; do
        case "$arg" in
            --no-agent) start_agent=0 ;;
            --no-focus) focus=0 ;;
            -*) echo "gwt: 不明なオプション: ${arg}" >&2; return 2 ;;
            *)
                if [[ -z "${branch}" ]]; then
                    branch="$arg"
                else
                    echo "gwt: 引数が多すぎます: ${arg}" >&2
                    return 2
                fi
                ;;
        esac
    done
    if [[ -z "${branch}" ]]; then
        echo "usage: gwt <branch> [--no-agent] [--no-focus]" >&2
        return 2
    fi

    local repo_root
    repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || {
        echo "gwt: git リポジトリの中で実行してください" >&2
        return 1
    }

    local worktree
    worktree=$(_gwt_path "${branch}")
    if [[ -z "${worktree}" ]]; then
        if git show-ref --verify --quiet "refs/heads/${branch}"; then
            gwq add "${branch}" || return 1
        else
            gwq add -b "${branch}" || return 1
        fi
        worktree=$(_gwt_path "${branch}")
    fi
    if [[ -z "${worktree}" ]]; then
        echo "gwt: ワークツリーのパスを特定できませんでした: ${branch}" >&2
        return 1
    fi

    if [[ "${HERDR_ENV:-}" != 1 ]]; then
        cd "${worktree}" || return 1
        return 0
    fi

    local opened pane workspace already
    opened=$(herdr worktree open --cwd "${repo_root}" --path "${worktree}" --label "${branch}" --no-focus) || return 1
    pane=$(print -r -- "${opened}" | jq -r '.result.root_pane.pane_id')
    workspace=$(print -r -- "${opened}" | jq -r '.result.workspace.workspace_id')
    already=$(print -r -- "${opened}" | jq -r '.result.already_open')

    # herdr の agent 名の規則 [a-z][a-z0-9_-]{0,31} に合わせる
    local name="${branch:l}"
    name="${name//[^a-z0-9_-]/-}"
    [[ "${name}" == [a-z]* ]] || name="w-${name}"
    name="${name[1,32]}"

    if (( start_agent )); then
        if [[ "${already}" == "true" ]] && herdr agent get "${name}" > /dev/null 2>&1; then
            echo "gwt: 既存の agent '${name}' を再利用します"
        else
            local start_out
            if ! start_out=$(herdr agent start "${name}" --kind claude --pane "${pane}" 2>&1); then
                if [[ "${start_out}" != *agent_not_ready* ]]; then
                    print -r -- "${start_out}" >&2
                    return 1
                fi
                # gwq のワークツリーは ghq 管理下の自分のリポジトリなので、
                # Claude Code の初回フォルダ信頼プロンプトだけは自動で通す。
                if herdr agent read "${name}" --source detection --lines 40 2>/dev/null | grep -q "I trust this folder"; then
                    herdr agent send-keys "${name}" enter > /dev/null 2>&1
                    herdr agent wait "${name}" --until idle --timeout 30000 > /dev/null 2>&1
                else
                    echo "gwt: agent '${name}' が入力待ちです。workspace ${workspace} を開いて確認してください。" >&2
                fi
            fi
        fi
    fi
    (( focus )) && herdr workspace focus "${workspace}" > /dev/null

    local summary="gwt: ${branch} -> ${worktree} (workspace ${workspace}"
    (( start_agent )) && summary+=", agent ${name}"
    summary+=")"
    echo "${summary}"
}
