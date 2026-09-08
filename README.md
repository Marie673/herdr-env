# herdr-env

[Herdr](https://herdr.dev)（コーディングエージェント用ターミナルマルチプレクサ）を中心にした開発環境の設定一式。
ghq + gwq + herdr + Claude Code を組み合わせて、**「1ブランチ = 1 worktree = 1 Herdr workspace = 1 Claude エージェント」** で作業する構成になっている。

> **Claude Code へ**: このリポジトリを渡されたら、下の「セットアップ手順」を上から順に実行すれば環境を再現できる。ファイルの配置先は「ファイル対応表」に従うこと。既存ファイルがある場合は上書き前にユーザーへ確認する。

## 全体像

- **ghq** が `~/workspace/github.com/<owner>/<repo>` に本体リポジトリ（canonical checkout）を置く。本体は常に `main`/`master` のまま。
- git の `reference-transaction` フックが本体でのブランチ切り替えを**物理的に禁止**する（`git/hooks/reference-transaction`）。
- ブランチ作業は **gwq** の worktree（`~/workspace/worktree/...=<branch>`）で行う。
- zsh 関数 **`gwt <branch>`** が入口: worktree を作り、Herdr の workspace として開き、そのペインで Claude Code を起動するところまで一発でやる（`zsh/gwq-herdr.zsh`）。
- Herdr のサイドバーは Claude Code の hooks（`claude/hooks/herdr-activity.sh`）から「今やっている操作」（実行中のコマンド、読んでいるファイル等）をリアルタイム表示する。
- **`herdr-wait`** が「外部待ち」（CodeRabbit のレビュー、CI 等）を idle と区別して表示する（`herdr/bin/herdr-wait.sh`）。
- ローカルプラグイン3つがサイドバーの使い勝手を補完する:
  - **agent-view-space**: agents ペインを「現在の space のエージェントだけ」に絞る（`prefix+shift+a` でトグル）
  - **pane-title-sync**: ペイン名/タブ名を Claude の会話タイトルに自動同期し、サイドバー行と画面上のペインを対応づける
  - **pane-id-copy**: 今いるペインの `pane_id` をクリップボードへ入れる（`prefix+shift+c`）。他のペインのエージェントに「このペインを見て」と伝えるときの識別子

## ファイル対応表

| リポジトリ内 | 配置先 |
|---|---|
| `herdr/config.toml` | `~/.config/herdr/config.toml`（テーマ名は空。好みのものを入れる） |
| `herdr/bin/new-workspace-picker.sh` | `~/.config/herdr/bin/new-workspace-picker.sh` |
| `herdr/bin/herdr-wait.sh` | `~/.config/herdr/bin/herdr-wait.sh` + `~/.local/bin/herdr-wait` へ symlink |
| `herdr/plugins/local/agent-view-space/` | `~/.config/herdr/plugins/local/agent-view-space/` |
| `herdr/plugins/local/pane-title-sync/` | `~/.config/herdr/plugins/local/pane-title-sync/` |
| `herdr/plugins/local/pane-id-copy/` | `~/.config/herdr/plugins/local/pane-id-copy/` |
| `zsh/gwq-herdr.zsh` | 任意の場所に置き `.zshrc` から `source` |
| `git/hooks/reference-transaction` | `~/.config/git/hooks/reference-transaction`（要 `chmod +x`） |
| `git/workspace.gitconfig` | `~/.config/git/workspace.gitconfig` |
| `gwq/config.toml` | `~/.config/gwq/config.toml` |
| `claude/hooks/herdr-activity.sh` | `~/.claude/hooks/herdr-activity.sh`（要 `chmod +x`） |
| `claude/statusline.sh` | `~/.claude/statusline.sh`（`settings.json` の `statusLine.command` から呼ぶ） |
| `claude/settings-hooks.snippet.json` | `~/.claude/settings.json` の `hooks` にマージ |
| `claude/skills/herdr/SKILL.md` | `~/.claude/skills/herdr/SKILL.md` |
| `claude/CLAUDE.snippet.md` | `~/.claude/CLAUDE.md` に追記 |

## セットアップ手順

### 1. CLI のインストール

```sh
brew bundle --file Brewfile
```

（herdr / ghq / gwq / fzf / jq / gh）

### 2. git: ghq root と本体保護フック

`~/.gitconfig` に:

```ini
[ghq]
	root = ~/workspace
[includeIf "gitdir:~/workspace/"]
	path = ~/.config/git/workspace.gitconfig
```

そして:

```sh
mkdir -p ~/.config/git/hooks
cp git/workspace.gitconfig ~/.config/git/
install -m 755 git/hooks/reference-transaction ~/.config/git/hooks/
```

`workspace.gitconfig` は `core.hooksPath` を `~/.config/git/hooks` に向ける。**`~/workspace` 配下だけ**にフックが効くので、それ以外のリポジトリには影響しない。

### 3. gwq

```sh
mkdir -p ~/.config/gwq
cp gwq/config.toml ~/.config/gwq/
```

worktree は `~/workspace/worktree/github.com/<owner>/<repo>=<branch>` に作られる（`=` 区切りのテンプレート）。

### 4. zsh 関数

```sh
# 例: リポジトリを ghq 管理下に置いたまま source する
echo 'source ~/workspace/github.com/Marie673/herdr-env/zsh/gwq-herdr.zsh' >> ~/.zshrc
```

入るもの: `gwt`（作業開始の入口）, `dev`（fzf でリポジトリ/worktree へ cd）, `wt`/`wta`, `gsw`, `ghq-path`, ペイン間 cd 同期（`sync-group`/`follow`）。

**重要**: `claude` コマンドをタイトル書き換えでラップしないこと。Claude Code の OSC タイトル（スピナー/✳）は Herdr のエージェント状態検出の入力で、塗り替えると working/idle を誤検出する。

### 5. Herdr 本体設定

```sh
mkdir -p ~/.config/herdr/bin
cp herdr/config.toml ~/.config/herdr/
install -m 755 herdr/bin/new-workspace-picker.sh ~/.config/herdr/bin/
install -m 755 herdr/bin/herdr-wait.sh ~/.config/herdr/bin/
ln -sf ~/.config/herdr/bin/herdr-wait.sh ~/.local/bin/herdr-wait
```

`config.toml` の要点:

- prefix は `ctrl+a`
- サイドバーを広め（幅40）にして日本語の会話タイトルを1行目に表示。`$act` トークン（後述の Claude hooks が報告）で「今やっている操作」を2行目に出す
- `[ui.toast] delivery = "system"` でバックグラウンド workspace の状態変化を OS 通知に
- キーバインド: `prefix+shift+n`（workspace picker）, `prefix+t`（navigator）, `prefix+d`（reviewr）, `prefix+shift+a`（agents 絞り込みトグル）, `prefix+shift+c`（ペインIDコピー）, `prefix+shift+b`（terminal-browser）, `ctrl+shift+u` / `ctrl+shift+m`（usagebar）

### 6. Herdr プラグイン

ローカルプラグイン（このリポジトリに同梱）:

```sh
mkdir -p ~/.config/herdr/plugins/local
cp -R herdr/plugins/local/agent-view-space ~/.config/herdr/plugins/local/
cp -R herdr/plugins/local/pane-title-sync  ~/.config/herdr/plugins/local/
cp -R herdr/plugins/local/pane-id-copy     ~/.config/herdr/plugins/local/
herdr plugin link ~/.config/herdr/plugins/local/agent-view-space
herdr plugin link ~/.config/herdr/plugins/local/pane-title-sync
herdr plugin link ~/.config/herdr/plugins/local/pane-id-copy
```

GitHub プラグイン（同梱しない。`herdr plugin install` で取得）:

```sh
herdr plugin install thanhdat77/herdr-navigator   # fuzzy navigator（Rust製、cargo build が走る）
herdr plugin install persiyanov/herdr-reviewr     # エージェントの diff をチャット横でレビュー
herdr plugin install senna-lang/herdr-agent-usage # サイドバーにコンテキスト/レート上限メーター
herdr plugin install zenbu-labs/terminal-browser  # ターミナル内ブラウザ
```

登録状況は `herdr plugin list` で確認。

#### agent-view-space の背景（重要な知見）

Herdr 0.8.2 では config の `agent_panel_scope` は**効かない**（パーサに残っているだけの死にキー）。agents ペインの絞り込みは socket API `agent.view.set` で行うが、これは**ランタイム状態でサーバ再起動で消える**。そこでこのプラグインが `[[startup]]` フックで毎回張り直し、`prefix+shift+a` でトグルできるようにしている。API は `~/.config/herdr/herdr.sock` に JSON を 1 行投げる方式（`bin/lib.sh` 参照）。

#### pane-id-copy の背景

「このペインを他のペイン（エージェント）から指したい」ときの識別子は `pane_id`（例 `w9:pB`）。Herdr 0.8.2 の socket API にはクリップボード書き込みがなく、右クリックのペインメニュー（Rename / Split / Close 等）も固定で拡張できない。そこでプラグインアクションとして実装し、`HERDR_PANE_ID`→context JSON の `focused_pane_id`→フォーカス中ペインの順で対象を解決して `pbcopy` に流している。`copy-target` アクションは `herdr pane read <id>` 等のコマンド例付きでコピーする。

#### pane-title-sync の背景

タイトル変更そのもののイベントは存在しないため、`pane.focused` / `pane.agent_status_changed` / `pane.created` の3フックで代用している。手動で付けた名前は `state.json`（ランタイム生成、リポジトリには含めない）で保護され、上書きされない。

### 7. Herdr × Claude Code 連携

まず Herdr 公式の Claude インテグレーションを入れる（`~/.claude/hooks/herdr-agent-state.sh` と SessionStart フックが自動生成される。これは Herdr が上書き管理するので**このリポジトリには含めない**）:

```sh
herdr integration install claude
```

次に自作フックを入れる。公式スクリプトとは別ファイルにしてあるのは、Herdr の再インストールで上書きされないようにするため:

```sh
install -m 755 claude/hooks/herdr-activity.sh ~/.claude/hooks/
```

そして `claude/settings-hooks.snippet.json` の `hooks` を `~/.claude/settings.json` にマージする。これで Herdr サイドバーの `$act` トークンに「`$ git diff`」「読 foo.py」「編集 bar.ts」のような操作内容がリアルタイムで出る。状態ラベル（`✅ 完了 未確認` など）も同フックが差し替える。

#### 外部待ちの可視化（herdr-wait）

Herdr は Claude Code のペイン状態を画面の見た目から判定する。レビュー待ちで応答が終わると `idle` になり、「用が終わって手空き」と区別がつかない。

`pane.report-agent` による状態の上書きは**効かない**。Claude Code は公式の session-only エージェント扱いで、custom source からの lifecycle 報告は `{"result":{"type":"ok"}}` を返しつつ黙って捨てられる（herdr 0.8.2 / 上流 Discussion #3625）。

そこで**検出マニフェストが working と読む行を、自分のステータスラインに出す**方式にしている。`agent-detection/remote/claude.toml` の `live_turn_working`（priority 970）は

```
line_regex = ['^\s*[*·✢✶✻✽]\s+\S.*…(?:\s+\(\d+[smh](?:\s|·)|\s*$)']
region = "bottom_non_empty_lines(12)"
```

にマッチする行を working と判定する。`herdr-wait statusline` はこの形の1行（`· ⏳ CodeRabbit レビュー待ち 12分…`）を出し、`claude/statusline.sh` が待ち中だけそれを描画する。結果、待っている間はペインが idle（`live_prompt_box`, priority 950）に落ちず working のままになる。承認ダイアログ系（priority 980）は上位なので、待ち中でも `⚠ 要承認` が優先される。

マニフェストを上書き（`~/.config/herdr/agent-detection/claude.toml`）する手もあるが、ローカル上書きは remote 版を丸ごと shadow するため claude の検出ルールが古いまま固定される。ステータスライン方式ならマニフェストは公式のまま追従できる。

```sh
herdr-wait set "CodeRabbit レビュー待ち #123"   # 印を立てる
herdr-wait clear                                 # 外す
herdr-wait list                                  # 印が立っている全ペイン
```

印は `~/.local/state/herdr-wait/<pane_id>` に置かれ、6時間（`HERDR_WAIT_TTL`）で自動失効する。状態が working になるのに加えて、`herdr-activity.sh` が state_text を `⏳ 外部待ち`、`$act` を `⏳ <ラベル> <経過時間>` に差し替える。

印の付け外しは Claude 自身にやらせる。`claude/CLAUDE.snippet.md` の「外部待ちは herdr に印を立てる」がその指示。

最後に Herdr スキルを Claude Code に入れる:

```sh
mkdir -p ~/.claude/skills/herdr
cp claude/skills/herdr/SKILL.md ~/.claude/skills/herdr/
```

（このスキルは `herdr --skill` の出力と同等のもの。最新版はそちらから取り直せる）

### 8. Claude Code へのワークフロー指示

`claude/CLAUDE.snippet.md` の内容を `~/.claude/CLAUDE.md` に追記する。これで Claude が ghq/gwq/gwt の流儀（本体でブランチを切り替えない、作業は worktree から始める等）を守るようになる。

## 日常の使い方

```sh
# 本体リポジトリで:
gwt feature/foo        # worktree 作成 → Herdr workspace → Claude 起動 まで一発
gwt feature/foo --no-agent   # Claude を起動しない
dev                    # fzf でリポジトリ/worktree に移動
wt                     # 現在のリポジトリの worktree に移動
prefix+shift+n         # Herdr 内で workspace picker
prefix+shift+a         # agents ペインの「現在の space だけ / 全部」トグル
```

本体で `git switch` すると:

```
✗ このディレクトリは 'main' 専用です ('feature/foo' への切り替えを中止しました)
```

と怒られるので、迷わず worktree に誘導される。どうしても必要なら `GIT_ALLOW_BRANCH_SWITCH=1` を付ける。

## 含めていないもの

- `~/.config/herdr/plugins.json`, `session.json`, `*.sock`, `*.log` — Herdr が生成するランタイム状態
- `herdr-agent-state.sh` — Herdr のインテグレーション導入時に自動生成される（上書き管理される）
- `plugins/github/` 配下 — `herdr plugin install` で再取得する
- `agent-view-space/scope`, `pane-title-sync/state.json` — プラグインのランタイム状態
