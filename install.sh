#!/bin/bash
# herdr-env のファイルを所定の場所に置く。
#
#   ./install.sh              置く（既にあって中身が違うファイルは飛ばして警告する）
#   ./install.sh --dry-run    何をするかだけ出す
#   ./install.sh --force      中身が違うファイルも置き換える（元は <file>.bak.<日時> に残す）
#   ./install.sh --launchd    定期実行（wt-audit --auto / herdr-pr-status sync / herdr-shelf shelve）も入れる（macOS）
#
# Herdr の GUI（Herdr.app）が入っていれば、herdr-app/setup.sh で GUI 側の表示も入れる。
#
# 自動でやらないこと（最後に手順を出す）:
#   ~/.zshrc への source、~/.claude/settings.json へのマージ、~/.claude/CLAUDE.md への追記、
#   herdr integration install claude、GitHub のプラグイン、claude-account のアカウント設定
set -eu

SRC="$(cd "$(dirname "$0")" && pwd)"
DRY=0
FORCE=0
LAUNCHD=0
STAMP="$(date +%Y%m%d%H%M%S)"
SKIPPED=0

usage() { sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    -n|--dry-run) DRY=1 ;;
    -f|--force) FORCE=1 ;;
    --launchd) LAUNCHD=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "不明なオプション: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

say()  { printf '%s\n' "$*"; }
run()  { if [ "$DRY" = 1 ]; then say "  (dry-run) $*"; else "$@"; fi; }
warn() { printf '  ! %s\n' "$*" >&2; }

backup() {
  run mv "$1" "$1.bak.$STAMP"
  say "  退避: $1 -> $1.bak.$STAMP"
}

# put <src> <dest> [mode]: ファイルを1つ置く
put() {
  src="$SRC/$1"; dest="$2"; mode="${3:-644}"
  if [ -e "$dest" ] || [ -L "$dest" ]; then
    if [ ! -L "$dest" ] && cmp -s "$src" "$dest"; then
      [ "$mode" = 755 ] && [ ! -x "$dest" ] && run chmod 755 "$dest"
      say "  = $dest"
      return
    fi
    if [ "$FORCE" != 1 ]; then
      warn "$dest は中身が違うので飛ばしました（--force で置き換え）"
      SKIPPED=$((SKIPPED + 1))
      return
    fi
    backup "$dest"
  fi
  run mkdir -p "$(dirname "$dest")"
  run install -m "$mode" "$src" "$dest"
  say "  + $dest"
}

# link <target> <link>: symlink を張る
link() {
  target="$1"; lnk="$2"
  if [ -L "$lnk" ] && [ "$(readlink "$lnk")" = "$target" ]; then
    say "  = $lnk"
    return
  fi
  if [ -e "$lnk" ] || [ -L "$lnk" ]; then
    if [ "$FORCE" != 1 ]; then
      warn "$lnk は別のものを指しているので飛ばしました（--force で置き換え）"
      SKIPPED=$((SKIPPED + 1))
      return
    fi
    backup "$lnk"
  fi
  run mkdir -p "$(dirname "$lnk")"
  run ln -s "$target" "$lnk"
  say "  + $lnk -> $target"
}

# put_dir <src-dir> <dest-dir>: プラグインのディレクトリを置く。実行時に作られる状態ファイルは比べない・消さない
put_dir() {
  src="$SRC/$1"; dest="$2"
  if [ -d "$dest" ]; then
    if diff -rq -x scope -x state.json -x '*.log' "$src" "$dest" >/dev/null 2>&1; then
      say "  = $dest"
      return
    fi
    if [ "$FORCE" != 1 ]; then
      warn "$dest は中身が違うので飛ばしました（--force で上書き。状態ファイルは残す）"
      SKIPPED=$((SKIPPED + 1))
      return
    fi
    run cp -R "$dest" "$dest.bak.$STAMP"
    say "  退避: $dest -> $dest.bak.$STAMP"
  fi
  run mkdir -p "$dest"
  run cp -R "$src/." "$dest/"
  say "  + $dest"
}

HERDR_CONF="$HOME/.config/herdr"
BIN="$HOME/.local/bin"

say "== herdr: 設定とコマンド"
put herdr/config.toml "$HERDR_CONF/config.toml"
for f in herdr/bin/*; do
  put "$f" "$HERDR_CONF/bin/$(basename "$f")" 755
done
for pair in \
  herdr-wait:herdr-wait.sh gh-ci-wait:gh-ci-wait.sh gwt-tasks:gwt-tasks.py \
  herdr-ask:herdr-ask.sh herdr-await:herdr-await.sh wt-audit:wt-audit.sh \
  claude-account:claude-account.py herdr-gui:herdr-gui.sh herdr-shelf:herdr-shelf.py; do
  link "$HERDR_CONF/bin/${pair#*:}" "$BIN/${pair%%:*}"
done

say "== herdr: ローカルプラグイン"
for d in "$SRC"/herdr/plugins/local/*/; do
  name="$(basename "$d")"
  put_dir "herdr/plugins/local/$name" "$HERDR_CONF/plugins/local/$name"
done
link "$HERDR_CONF/plugins/local/pr-status/bin/pr_status.py" "$BIN/herdr-pr-status"
if command -v herdr >/dev/null 2>&1; then
  linked="$(herdr plugin list 2>/dev/null || true)"
  for d in "$SRC"/herdr/plugins/local/*/; do
    name="$(basename "$d")"
    case "$linked" in
      *"- $name ("*) say "  = plugin $name" ;;
      *)
        if [ "$DRY" = 1 ]; then say "  (dry-run) herdr plugin link $HERDR_CONF/plugins/local/$name"
        else herdr plugin link "$HERDR_CONF/plugins/local/$name" >/dev/null && say "  + plugin link $name"; fi ;;
    esac
  done
else
  warn "herdr が見つからないので herdr plugin link を飛ばしました"
fi

say "== Herdr GUI（Herdr.app）"
if command -v herdr-app >/dev/null 2>&1 && [ -d "${HERDR_APP_HOME:-$HOME/.config/herdr-app}" ]; then
  if [ "$DRY" = 1 ]; then say "  (dry-run) herdr-app/setup.sh"
  else "$SRC/herdr-app/setup.sh" | sed 's/^/  /'; fi
else
  say "  - herdr-app が無いので飛ばしました（入れたあと herdr-app/setup.sh を流す）"
fi

say "== git: 本体リポジトリを main に固定するフック"
put git/hooks/reference-transaction "$HOME/.config/git/hooks/reference-transaction" 755
put git/workspace.gitconfig "$HOME/.config/git/workspace.gitconfig"
ghq_root="$(git config --global --get ghq.root || true)"
if [ -z "$ghq_root" ]; then
  ghq_root="~/workspace"
  run git config --global ghq.root "$ghq_root"
  say "  + git config --global ghq.root $ghq_root"
else
  say "  = ghq.root = $ghq_root"
fi
inc_key="includeIf.gitdir:${ghq_root%/}/.path"
if [ -z "$(git config --global --get "$inc_key" || true)" ]; then
  run git config --global "$inc_key" "~/.config/git/workspace.gitconfig"
  say "  + git config --global $inc_key ~/.config/git/workspace.gitconfig"
else
  say "  = $inc_key"
fi

say "== gwq"
put gwq/config.toml "$HOME/.config/gwq/config.toml"

say "== Claude Code"
put claude/hooks/herdr-activity.sh "$HOME/.claude/hooks/herdr-activity.sh" 755
put claude/statusline.sh "$HOME/.claude/statusline.sh" 755
put claude/skills/herdr/SKILL.md "$HOME/.claude/skills/herdr/SKILL.md"

if [ "$LAUNCHD" = 1 ]; then
  say "== launchd（定期実行）"
  if [ "$(uname)" != Darwin ]; then
    warn "macOS ではないので飛ばしました"
  else
    brew_prefix="$(brew --prefix 2>/dev/null || echo /opt/homebrew)"
    agents="$HOME/Library/LaunchAgents"
    run mkdir -p "$agents" "$HOME/.local/state/wt-audit" "$HOME/.local/state/herdr-pr-status" "$HOME/.local/state/herdr-shelf"
    for tpl in "$SRC"/launchd/*.plist.in; do
      name="local.$(basename "$tpl" .in)"
      label="${name%.plist}"
      dest="$agents/$name"
      tmp="$(mktemp)"
      LC_ALL=C sed -e "s|@HOME@|$HOME|g" -e "s|@BREW_PREFIX@|$brew_prefix|g" "$tpl" >"$tmp"
      if [ -e "$dest" ] && cmp -s "$tmp" "$dest"; then
        say "  = $dest"
      else
        [ -e "$dest" ] && backup "$dest"
        run install -m 644 "$tmp" "$dest"
        run launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
        run launchctl bootstrap "gui/$(id -u)" "$dest"
        say "  + $dest（$label を有効化）"
      fi
      rm -f "$tmp"
    done
  fi
fi

say ""
if [ "$SKIPPED" -gt 0 ]; then
  say "中身が違うため $SKIPPED 件を飛ばしました。差分を見て、置き換えてよければ --force を付けて再実行してください。"
  say ""
fi
cat <<EOF
残りは手で行ってください（README の「セットアップ手順」に詳細）:

  1. ~/.zshrc に追加:
       source "$SRC/zsh/gwq-herdr.zsh"
       source "$SRC/zsh/claude-account.zsh"   # 複数アカウントを使うとき
  2. Herdr 公式の Claude 連携:  herdr integration install claude
  3. $SRC/claude/settings-hooks.snippet.json の hooks を ~/.claude/settings.json にマージし、statusLine.command を ~/.claude/statusline.sh にして、
     permissions.additionalDirectories に gwq の worktree.basedir（既定 ~/workspace/worktree）を足す
  4. $SRC/claude/CLAUDE.snippet.md を ~/.claude/CLAUDE.md に追記
  5. （任意）GitHub のプラグイン: README の「6. Herdr プラグイン」
  6. （任意）issue-board を出すリポジトリを \$(herdr plugin config-dir issue-board)/config.json に書く
  7. （任意）複数アカウント: claude-accounts/accounts.example を ~/.config/claude-accounts/accounts にコピーして編集
EOF
