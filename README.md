# herdr-env

[Herdr](https://herdr.dev)（コーディングエージェント用ターミナルマルチプレクサ）を中心にした開発環境の設定一式。
ghq + gwq + herdr + Claude Code を組み合わせて、**「1ブランチ = 1 worktree = 1 Herdr workspace = 1 Claude エージェント」** で作業する構成になっている。

*A set of dotfiles, scripts and Herdr plugins for running one Claude Code agent per git worktree on [Herdr](https://herdr.dev). Documentation is in Japanese.*

## 動作環境

- **macOS**（launchd・Keychain・`pbcopy` を使う。Linux で動くのは一部のスクリプトとプラグインだけ）
- [Herdr](https://herdr.dev) 0.9 以降（一部のプラグインが `min_herdr_version = "0.9.0"`）
- [Claude Code](https://claude.com/claude-code)
- Homebrew で入れる CLI: herdr / ghq / gwq / fzf / jq / gh / bash（`Brewfile`）。worktree-setup は [mise](https://mise.jdx.dev) があれば使う
- `/usr/bin/python3`（プラグインと `gwt-tasks` / `claude-account`。標準ライブラリだけで動く）

## クイックスタート

```sh
brew bundle --file Brewfile
ghq get Marie673/herdr-env
cd "$(ghq list -p Marie673/herdr-env)"
./install.sh --dry-run            # 何を置くか確かめる
./install.sh                      # 置く（既にあって中身が違うファイルは飛ばす。--force で .bak を残して置き換え）
./install.sh --launchd            # 定期実行（wt-audit --auto / PR 状態の取り直し）も入れるとき
```

`install.sh` が最後に出す「残りの手順」（`.zshrc` への `source`、`~/.claude/settings.json` へのマージなど）は手で行う。各ファイルの置き場所と役割は下の「ファイル対応表」と「セットアップ手順」にある。

> **Claude Code にセットアップを任せる場合**: このリポジトリを渡して「README のセットアップ手順で入れて」と頼めばよい。ファイルの配置先は「ファイル対応表」に従い、既存ファイルがある場合は上書き前にユーザーへ確認すること。

### 置き場所を変えたいとき

既定のレイアウトは、ghq の root が `~/workspace`、gwq の worktree が `~/workspace/worktree/<host>/<owner>/<repo>=<branch>`。変えるときは次をそろえる。

- `git config --global ghq.root` と、`~/.gitconfig` の `includeIf "gitdir:<ghq root>/"`（本体保護フックはこの配下だけに効く）
- `~/.config/gwq/config.toml` の `worktree.basedir`
- `~/.claude/settings.json` の `permissions.additionalDirectories`（worktree の置き場所）

`zsh/gwq-herdr.zsh` は `ghq root` と `gwq config get worktree.basedir` を読むので書き換えは要らない。

## 全体像

- **ghq** が `~/workspace/github.com/<owner>/<repo>` に本体リポジトリ（canonical checkout）を置く。本体は常に `main`/`master` のまま。
- git の `reference-transaction` フックが本体でのブランチ切り替えを**物理的に禁止**する（`git/hooks/reference-transaction`）。
- ブランチ作業は **gwq** の worktree（`~/workspace/worktree/...=<branch>`）で行う。
- zsh 関数 **`gwt <branch>`** が入口: worktree を作り、Herdr の workspace として開く（`zsh/gwq-herdr.zsh`）。main で動いている agent のペインから呼ぶと、新しい agent は立てずに**そのペインごと新しい workspace へ移す**（GUI の「Move to...」と同じ。会話を引き継いだままシングル agent で続ける）。素のシェルから呼んだときは新しい workspace で Claude Code を起動する。
- Herdr のサイドバーは Claude Code の hooks（`claude/hooks/herdr-activity.sh`）から「今やっている操作」（実行中のコマンド、読んでいるファイル等）をリアルタイム表示する。
- **`herdr-wait`** が「外部待ち」（CodeRabbit のレビュー、CI 等）を idle と区別して表示する（`herdr/bin/herdr-wait.sh`）。
- **`gh-ci-wait`** が PR の CI 完了を確実に検知して終わり、待ち印を自分の寿命に結びつける（`herdr/bin/gh-ci-wait.sh`）。
- **`herdr-ask` / `herdr-await`** が agent 同士の質問・承認を人間ではなく依頼元 agent に回す。依頼先は `herdr-ask send` で依頼元に聞き、依頼元は `herdr-await` で相手が止まるのを待つ（待っている間は自分の PID つきで `herdr-wait` の印を立て、終われば外す）。Claude は AskUserQuestion を PreToolUse フックで deny して誘導し、承認待ちは Notification フックで依頼元に知らせる（`herdr/bin/herdr-ask.sh`、`herdr/bin/herdr-await.sh`）。作業はシングル agent で続ける方針なので、`claude/CLAUDE.snippet.md` からは委譲の指示を外してある。これらは人が `gwt --task` で依頼したときのためのもので、依頼先への使い方は `gwt` が依頼文に添えて渡す。
- **`gwt-tasks`** が「別 space の誰に何を任せたか」の台帳を持ち、herdr の状態と突き合わせて一覧にする（`herdr/bin/gwt-tasks.py`）。
- **`wt-audit`** が終わった作業のワークツリーを棚卸しして片付ける（`herdr/bin/wt-audit.sh`）。
- **`claude-account`** が Claude Code の複数アカウントを `CLAUDE_CONFIG_DIR` で使い分ける（`herdr/bin/claude-account.py`）。追加アカウントのディレクトリはログイン情報と `.claude.json` だけを持ち、設定・会話の記録は `~/.claude` を共有する。サイドバーにアカウントと上限の残りが出る。`prefix+shift+u` で、選んだアカウントで起動するか、今のペインの会話を同じ会話のまま別アカウントへ移せる（上限に達したとき用）。
- ローカルプラグイン3つがサイドバーの使い勝手を補完する:
  - **agent-view-space**: agents ペインを「現在の space のエージェントだけ」に絞る（`prefix+shift+a` でトグル）
  - **pane-title-sync**: ペイン名/タブ名を Claude の会話タイトルに自動同期し、サイドバー行と画面上のペインを対応づける
  - **pane-id-copy**: 今いるペインの `pane_id` をクリップボードへ入れる（`prefix+shift+c`）。他のペインのエージェントに「このペインを見て」と伝えるときの識別子

## Herdr の GUI（Herdr.app）で使う

Herdr には TUI（`herdr server`）とは別に、GUI の Herdr.app がある。GUI のペインは herdr-core（`hc`）のターミナルで、
TUI の `~/.config/herdr/config.toml`・ローカルプラグイン・`herdr` コマンドとは**つながっていない**。
ペインに `HERDR_ENV` / `HERDR_PANE_ID` も入らない（代わりに `HC_TERMINAL_ID` が入る）。そのため GUI 用に次を足している。

| 機能 | GUI での扱い |
|---|---|
| 今やっている操作の表示（`$act`） | 不要。herdr-core が Claude Code の状態と操作を自分で拾う |
| `herdr-wait` / `gh-ci-wait` の ⏳ 外部待ち、Stop フックの 💬 要返答 | 印を hc のラベル（`herdr-env.wait` / `.since` / `.pid` / `.human`）に載せ、mod `herdr-env-marks` がサイドバー下にカードで出す。印が無いときカードは消える |
| agents 一覧を選んでいる Space だけにする（agent-view-space） | mod `agents-env`。標準の agents を `herdr-app do mod.clone` で複製し、`herdr-app/agents-env.py` が絞り込みを足す。見出しのフォルダのボタンか `cmd+shift+a` で「この Space / 全部」を切り替える |
| `gwt`（agent ペインから） | `herdr-gui` が「このターンが終わったら、ワークツリーのスペースで同じ会話を `--resume` し、元のペインを閉じる」を予約し、Stop フック（`herdr-activity.sh`）が実行する。GUI はブランチをプロセスのディレクトリで判断するので、ペインを動かすだけにはしない。フォルダの信頼確認は TUI の gwt と同じく「Yes」に合わせたのを確かめてから通す |
| Space ごとの agent の状態 | mod `spaces-env`。標準の Spaces 一覧は畳んだときしか状態を出さず、全体は Agents 欄で見る作り。Agents を絞るとほかの Space が見えなくなるので、各行の右端に状態を出す（動作中 = スピナー、要対応 = !、返事待ち = 💬、完了 = 点、外部待ち = ⏳）。`herdr-app/spaces-env.py` が標準の spaces を複製して書き換える |
| pr-status（worktree の Space に PR の状態） | 同じ `pr_status.py` が、GUI の worktree の Space のターミナルに hc のラベル `herdr-env.pr` / `herdr-env.ci` / `herdr-env.rv` / `herdr-env.pr_at` を付け、mod `spaces-env` が名前の後ろに出す（`#13 競合 CI ✓ CR ✓`、`#959 merged`）。Space の場所は GUI がブランチを出すのと同じく最初のターミナルがいまいるディレクトリで決める。GUI はプラグインのイベントを送らないので、取り直すのは launchd の毎分と `gh-ci-wait` のとき。ラベルは30分で失効する |
| worktree-setup（初回の .env コピーと依存のインストール） | GUI はプラグインのイベントを送らないので、`herdr-gui` がワークツリーのスペースを開いたあと（素のシェルからの `gwt` も、agent ペインからの移動も）`wt_setup.py run --path` を切り離して呼ぶ。実行中・失敗はそのスペースのターミナルのラベル `herdr-env.setup` で `spaces-env` の行に `⚙ setup` / `✗ setup` と出し、失敗は macOS の通知でも知らせる |
| wt-audit（終わったワークツリーの片付け） | `herdr-app ls` の GUI のスペースも突き合わせる（id は `gui:<space id>`）。スペースのフォルダと、各ターミナルがいまいるディレクトリのワークツリーに、そのターミナルの agent の状態を結びつけ、working / blocked なら `--auto` で消さない。片付けるときは GUI のスペースも閉じる。通知は TUI が動いていなければ macOS の通知 |
| 棚（herdr-shelf） | GUI の agent も同じ条件（idle・表示していない・外部待ちでない・会話の記録が24時間更新されていない）でしまう。会話 ID は `claude-account` が付けるラベル `herdr-env.session`（無ければ起動引数の `--resume`）。`cmd+shift+z` でフォーカス中の agent をしまい、`cmd+shift+s` で棚から開き直す（GUI のターミナルから呼べば GUI に、元のスペースか同じフォルダのスペースへタブで開く）。1時間ごとの定期実行（`launchd/herdr-env.herdr-shelf.plist.in`）は TUI と GUI の両方を見る |
| 落ちた会話の再開（herdr-revive） | herdr-core が落ちる（Herdr の再起動・クラッシュ・ログアウト）と、ターミナルは同じアドレスのまま最後のフォルダのシェルに戻り、claude は二度と動かない。シェルの起動時（`zsh/claude-account.zsh` の precmd 1回）に `herdr-revive --line $$` が、そのターミナルのラベル `herdr-env.session` / `herdr-env.acct_id` から `claude -A <acct> --resume <id>` を打ち直す。/exit・Ctrl-C で終えた会話は SessionEnd hook がラベルを消すので戻さない。同じ会話がほかで動いているとき・記録が無いときも戻さず、同じシェル・同じ会話では一度しか試さない。すでにシェルに戻っている分は `herdr-revive` で一括（`--dry-run` で対象だけ） |
| Claude のアカウント（`$acct`、claude-accounts） | `claude-account hook`（SessionStart と statusLine）が GUI のターミナルにラベル `herdr-env.session` / `herdr-env.acct_id` / `herdr-env.acct` / `herdr-env.lim`（5h / 7d の残り）/ `herdr-env.lim_at` を付ける。`herdr-env-marks` のカードにアカウントごとの残りを出し（上限はアカウント単位なので一番新しい値。20% 以下は赤、50% 以下は黄。下部のステータスバーに利用量を出す mod `workspace-status-env` を入れているときは出さない）、`agents-env` はアカウントを2つ以上使っているとき行にその色の小さな丸（🟦 は青、🟪 は紫）を出す。`cmd+shift+u` でフォーカス中のペインの下にピッカーを開き、Enter で新しいタブに起動、ctrl-s でその会話を同じ会話のまま別アカウントで開き直す（`claude-account start / switch --terminal`） |
| issue-board（issue / PR 一覧） | 右パネル（Files / Changes のスタック）に mod `issues-env` の **Issues** を足す（下記）。タブバーの Issues ボタン（`herdr-app/tabs-env.py` が標準の tabs を複製して足す）か `cmd+shift+i` で出し入れする。TUI の fzf 版をターミナルで開くことはしない（GUI にはペイン幅を変えるコマンドが無く、一覧が画面の半分を取るため） |
| `gwt`（素のシェル / `--task`） | `herdr-app do space.new`（既にそのワークツリーのスペースがあれば `tab.new`）で開き、Claude Code を起動する。`--task` の依頼文は最初のプロンプトとして渡す（GUI の `task` 引数はシェル経由の起動に届かないため、ファイル経由で引数にする） |
| 利用量のバー（任意。TUI の usagebar の代わり） | `setup.sh --usage-bar` で入れる mod `workspace-status-env` が、下部のステータスバーに Claude（アカウントごと）/ Antigravity / Codex / Gemini CLI の残りを出す。クリックで詳細（枠ごとの残りとリセットまでの時間）、⊞ で右のペインに固定。値は `ai-usage-collect.py` が各ツールの手元の記録から集める（下記）。入れているときは `herdr-env-marks` のカードと `statusline.sh` の 5h / 7d は出さない |

入れ方:

```sh
herdr-app/setup.sh        # install.sh からも呼ばれる。Herdr を更新したら流し直す
```

利用量のバーも出すときは `herdr-app/setup.sh --usage-bar`（一度入れれば、以後は付けずに流しても入れ直す。やめるときは `--no-usage-bar`）。
`ai-usage-collect.py` が読むもの:

- Claude: `claude/statusline.sh` が残す statusLine の入力（`~/.local/state/herdr-env/statusline/<アカウント>.json` の `rate_limits`）と、
  Claude Code が `.claude.json` に持つ `cachedUsageUtilization`。アカウントは `~/.config/claude-accounts/accounts`（無ければ `~/.claude` だけ）。
  モデル別の週の枠まで新しい値で出したいときは `~/.config/herdr-env/usage.json` に `{"claude_usage_api": true}` を書く。
  Keychain にある Claude Code のトークンで `/usage` と同じ API（公開されていない）を5分に1回まで呼ぶので、既定では切ってある
- Codex: `~/.codex/sessions/` の記録の `rate_limits`
- Antigravity: statusLine のスクリプトから JSON を渡す（`printf '%s' "$input" | ~/.config/herdr/bin/ai-usage-collect.py --record-agy`）
- Gemini CLI: 今日の `~/.gemini/tmp/*/chats/` のトークン数。AfterAgent フックで `ai-usage-collect.py --record-gemini` を呼ぶと最終更新の時刻も出る

使っていないツールはバーに出ない。

`setup.sh` は標準の `main` レイアウトを複製して `herdr-env` レイアウトを作り（Spaces 欄を `spaces-env`、Agents 欄を `agents-env`、
タブバーを `tabs-env` に替え、右パネルに `issues-env` を足し、サイドバー下に `herdr-env-marks` を浮かべる）、それに切り替える。複製は Herdr の更新に追従しないので、
Herdr を更新したら `setup.sh` をもう一度流す。標準に戻すときは `ctrl+shift+l`（レイアウト切り替え）で `main` を選ぶ。

### Issues パネル（GUI）

今いる Space（フォーカスしているペインのフォルダ）のリポジトリの open な issue / PR を、右パネルに出す。Files と同じく Space を移れば中身も変わり、
ワークツリーの Space でも本体と同じ一覧になる（git の common dir でまとめる）。ほかのマシンの Space では出さない。TUI の `config.json` の `repos` は見ない。

- 一覧: `●`（ワークツリーあり）`#番号` タイトル、ラベル（GitHub の色）、作成者と更新。PR は TUI と同じ状態のバッジ（`draft` `競合` `CI✗2` `CR✓` `要修正` `未解決1` `承認`）。上の Issues / PRs で切り替え、入力欄で番号・タイトル・ラベル・作成者を絞り込む。↻ で取り直す
- 詳細（行を押す）: 本文とコメントを Markdown で出す。PR は checks（失敗・実行中が上、押すとそのページ）も出す。← で一覧に戻る
- 書き込み（GitHub にそのまま書く）: 本文とコメントのチェックボックスを押すと切り替える（書き込む直前の本文で切り替え、ほかで書き換わっていれば止める。書き換えられる人だけ押せる）。
  本文とコメントの下でリアクションを付け外しする（☺＋で8種類から選ぶ）。いちばん下の欄からコメントを書く（書きかけは issue / PR ごとに覚えておく）。
  PR では「承認」「変更を依頼」もできる（コメント欄の文がレビューの本文。変更の依頼は本文が要る。押し間違えないよう2回押しで送る。自分の PR には出さない）。
  レビュー（承認・変更の依頼・本文のあるコメント）はコメントと時刻順に並べ、状態を色で出す。
  失敗したら元に戻して通知で理由を出す。読み書きは GraphQL 1回ずつ（`gh api graphql`）
- 画像: GUI の Markdown 表示は画像を描かないので、本文を画像の所で切り、画像は落としてきて出す（元の大きさ、`<img width>` があればそれ。パネルより広ければ縮める。押すとブラウザで開く）。
  非公開リポジトリの添付（`github.com/user-attachments/assets/…`）は API の `body_html` にある署名つきの URL で取る。表の中の画像は、表では「🖼 説明」にして表のすぐ後ろに並べる。
  落とした画像は `~/.config/herdr-app/data/issues-env/img/` に置き、14日使わなければ消す
- issue の「着手」: haiku がブランチ名を提案し（TUI と同じ `suggest-branch.sh`）、入力欄で直して「着手」で `gwt <branch> --task` する。番号入りのワークツリーがあれば「Space へ移る」（Space が無ければ「Space を開く」）
- PR: そのブランチの Space へ移る。無ければ「Claude Code で開く / シェルで開く」（ローカルにブランチが無ければ origin から取る。フォークの PR は開かない）
- データは TUI と同じ `issues.sh` / `prs.sh` の `--json`（gh の結果は TUI の一覧と共有して30秒使い回す）。パネルが見えている間だけ1分ごとに取り直す
- `gwt` はシェル関数なので `zsh -lic` で呼ぶ。ターミナルの外から呼ぶので `HERDR_GUI=1` を付け、`herdr-gui open` がスペースを開く（会話を移す予約はしない）。
  `~/.zshrc` に gwt を直に書き写している場合は、GUI の分かれ目の条件に `|| "${HERDR_GUI:-}" == 1` を足す（足していないと「gwt がスペースを開きませんでした」と出る）

まだ GUI に移していないもの: `herdr-ask` / `herdr-await` / `gwt-tasks`（委譲。いまはシングル agent 運用なので未使用）、
TUI のローカルプラグインのうち pane-title-sync / pane-id-copy、agent-attention の通知の絞り込み、`$context`（コンテキストの使用量）。
GUI はタイトルの同期を自前で持ち、ペインの ID も TUI と形が違うので、pane-title-sync と pane-id-copy は要らない見込み。

## ファイル対応表

| リポジトリ内 | 配置先 |
|---|---|
| `herdr/config.toml` | `~/.config/herdr/config.toml`（テーマ名は空。好みのものを入れる） |
| `herdr/bin/new-workspace-picker.sh` | `~/.config/herdr/bin/new-workspace-picker.sh` |
| `herdr/bin/herdr-wait.sh` | `~/.config/herdr/bin/herdr-wait.sh` + `~/.local/bin/herdr-wait` へ symlink |
| `herdr/bin/herdr-gui.sh` | `~/.config/herdr/bin/herdr-gui.sh` + `~/.local/bin/herdr-gui` へ symlink（GUI 用） |
| `herdr/bin/ai-usage-collect.py` | `~/.config/herdr/bin/ai-usage-collect.py`（GUI の利用量のバーが呼ぶ） |
| `herdr-app/` | `herdr-app/setup.sh` が `~/.config/herdr-app/` に mod とレイアウトを作る（GUI 用、下の「Herdr の GUI で使う」） |
| `herdr/bin/gh-ci-wait.sh` | `~/.config/herdr/bin/gh-ci-wait.sh` + `~/.local/bin/gh-ci-wait` へ symlink |
| `herdr/bin/gwt-tasks.py` | `~/.config/herdr/bin/gwt-tasks.py` + `~/.local/bin/gwt-tasks` へ symlink |
| `herdr/bin/herdr-ask.sh` | `~/.config/herdr/bin/herdr-ask.sh` + `~/.local/bin/herdr-ask` へ symlink |
| `herdr/bin/herdr-await.sh` | `~/.config/herdr/bin/herdr-await.sh` + `~/.local/bin/herdr-await` へ symlink |
| `herdr/bin/wt-audit.sh` | `~/.config/herdr/bin/wt-audit.sh` + `~/.local/bin/wt-audit` へ symlink |
| `herdr/bin/herdr-shelf.py` | `~/.config/herdr/bin/herdr-shelf.py` + `~/.local/bin/herdr-shelf` へ symlink |
| `herdr/bin/herdr-revive.py` | `~/.config/herdr/bin/herdr-revive.py` + `~/.local/bin/herdr-revive` へ symlink |
| `herdr/plugins/local/agent-shelf/` | `~/.config/herdr/plugins/local/agent-shelf/`（TUI の `prefix+shift+z` で棚にしまう） |
| `launchd/herdr-env.herdr-shelf.plist.in` | `~/Library/LaunchAgents/local.herdr-env.herdr-shelf.plist`（1時間ごとに `herdr-shelf shelve --hours 24`） |
| `herdr/bin/claude-account.py` | `~/.config/herdr/bin/claude-account.py` + `~/.local/bin/claude-account` へ symlink |
| `claude-accounts/accounts.example` | `~/.config/claude-accounts/accounts`（自分のアカウントに書き換える） |
| `herdr/plugins/local/claude-accounts/` | `~/.config/herdr/plugins/local/claude-accounts/` |
| `zsh/claude-account.zsh` | 任意の場所に置き `.zshrc` から `source`（`claude` 関数。`claude -A <id>`） |
| `launchd/herdr-env.wt-audit.plist.in` | `~/Library/LaunchAgents/local.herdr-env.wt-audit.plist`（`install.sh --launchd` が `@HOME@` 等を埋めて置く） |
| `herdr/plugins/local/agent-view-space/` | `~/.config/herdr/plugins/local/agent-view-space/` |
| `herdr/plugins/local/pane-title-sync/` | `~/.config/herdr/plugins/local/pane-title-sync/` |
| `herdr/plugins/local/pane-id-copy/` | `~/.config/herdr/plugins/local/pane-id-copy/` |
| `herdr/plugins/local/pr-status/` | `~/.config/herdr/plugins/local/pr-status/` + `~/.local/bin/herdr-pr-status` へ symlink |
| `herdr/plugins/local/worktree-setup/` | `~/.config/herdr/plugins/local/worktree-setup/` |
| `herdr/plugins/local/issue-board/` | `~/.config/herdr/plugins/local/issue-board/` |
| `launchd/herdr-env.herdr-pr-status.plist.in` | `~/Library/LaunchAgents/local.herdr-env.herdr-pr-status.plist`（同上） |
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

`install.sh` がやるのは 1〜3・5〜7 のうちファイルを置く部分（と `ghq.root` / `includeIf` が未設定なら設定）。ここでは中身と、手でやる部分を説明する。

### 1. CLI のインストール

```sh
brew bundle --file Brewfile
```

（herdr / ghq / gwq / fzf / jq / gh / bash）

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
echo "source $(ghq list -p Marie673/herdr-env)/zsh/gwq-herdr.zsh" >> ~/.zshrc
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
install -m 755 herdr/bin/gh-ci-wait.sh ~/.config/herdr/bin/
ln -sf ~/.config/herdr/bin/gh-ci-wait.sh ~/.local/bin/gh-ci-wait
install -m 755 herdr/bin/wt-audit.sh ~/.config/herdr/bin/
ln -sf ~/.config/herdr/bin/wt-audit.sh ~/.local/bin/wt-audit
install -m 755 herdr/bin/gwt-tasks.py ~/.config/herdr/bin/
ln -sf ~/.config/herdr/bin/gwt-tasks.py ~/.local/bin/gwt-tasks
install -m 755 herdr/bin/herdr-ask.sh herdr/bin/herdr-await.sh ~/.config/herdr/bin/
ln -sf ~/.config/herdr/bin/herdr-ask.sh ~/.local/bin/herdr-ask
ln -sf ~/.config/herdr/bin/herdr-await.sh ~/.local/bin/herdr-await
```

`config.toml` の要点:

- prefix は `ctrl+a`
- サイドバーを広め（幅40）にして日本語の会話タイトルを1行目に表示。`$act` トークン（後述の Claude hooks が報告）で「今やっている操作」を2行目に出す
- `[ui.toast] delivery = "system"` でバックグラウンド workspace の状態変化を OS 通知に
- キーバインド: `prefix+shift+n`（workspace picker）, `prefix+t`（navigator）, `prefix+d`（reviewr）, `prefix+shift+a`（agents 絞り込みトグル）, `prefix+shift+c`（ペインIDコピー）, `prefix+shift+b`（terminal-browser）, `ctrl+shift+u` / `ctrl+shift+m`（usagebar）

### 5.4. 任せた作業の台帳（gwt-tasks）

`gwt` は worktree と workspace を作ってエージェントを起動するところまでしかやらない。起動した側には「誰に何を任せたか」が何も残らないので、切り出した作業を追いようがなかった。herdr が持っているのは agent の状態だけで、依頼の内容も依頼元も持っていない。

```sh
install -m 755 herdr/bin/gwt-tasks.py ~/.config/herdr/bin/
ln -sf ~/.config/herdr/bin/gwt-tasks.py ~/.local/bin/gwt-tasks
```

```sh
gwt feature/foo --task "issue #123 を実装。認証は既存の middleware を使う。PR まで。"
gwt feature/foo --task-file ./handover.md   # 長い引き継ぎはファイルで

gwt-tasks                    # 依頼の一覧
gwt-tasks --mine             # 自分のペインから出した依頼だけ
gwt-tasks show feature-foo   # 依頼の全文 + 委譲先の直近の返答
gwt-tasks done feature-foo   # 済んだら台帳から外す
gwt-tasks prune              # agent もワークツリーも無い依頼を掃除
```

```
agent                           状態         タスク                                   経過       space
ci-issue-1603-self-hosted-runne ⚠ 要承認     ランナー選択ワークフローを実装           22分       w1Q
tf-refactor                     ✅ 完了      terraform の共有 Cloud Run をリファクタ  1時間12分  w1R
feature-969-mt-image-upload     … 実行中     MT の画像アップロードを実装              4分        wT
```

設計:

- **台帳は状態を持たない。** 状態の情報源は常に herdr（`herdr agent list`）。台帳は依頼の内容・依頼元・開始時刻だけを持ち、一覧のたびに突き合わせる。二重に状態を持つと必ず食い違う。
- 1 依頼 1 ファイル（`~/.local/state/gwt-tasks/<agent>.json`）。並行して走る `gwt` が同じファイルを踏まない。
- agent 名はペインの占有者が入れ替わると herdr 側で消えるので、名前で引けなければ `pane_id` で引き直す。どちらでも見つからなければ「✗ 不在」。
- 外部待ちの印（`herdr-wait`）が立っているペインは、その印を優先して「⏳ 外部待ち」と出す。
- 並びは手当てが要るものが上（要承認 → 完了 → 実行中 → 外部待ち → 待機 → 不明 → 不在）。

**委譲先の返答は画面からではなくトランスクリプトから読む。** Claude Code は代替スクリーンで動くため、流れた行は herdr のホストスクロールバックに入らず `herdr agent read` では戻ってこない（`--lines` を増やしても無駄）。`herdr agent list` の `agent_session.value` が Claude Code のセッション ID なので、`~/.claude/projects/*/<session-id>.jsonl` を直接読んで最後の assistant メッセージを出している。セッション ID は台帳に書き戻すので、**agent が終了した後でも「最後に何と言い残したか」が読める**。

### 5.5. ワークツリーの棚卸し（wt-audit）

`gwt` で作業を始めるたびにワークツリーが増える一方なので、終わったものを片付けるコマンド。PR の状態・作業の残り・herdr の space を突き合わせて判定する。

```sh
install -m 755 herdr/bin/wt-audit.sh ~/.config/herdr/bin/
ln -sf ~/.config/herdr/bin/wt-audit.sh ~/.local/bin/wt-audit
```

```sh
wt-audit --list     # 一覧だけ（既定で ghq が知っている全リポジトリが対象）
wt-audit            # fzf で選んで、space を閉じてワークツリーを削除
wt-audit --auto --dry-run   # 自動で片付ける対象を確認
```

判定は次の通り:

| 判定 | 条件 |
|---|---|
| 片付け可 | PR が merged / closed、未コミット変更なし、未 push コミットなし |
| 要確認 | 未コミット変更・未 push コミットあり、または PR が無く30日放置 |
| 生存 | PR が open、または最近さわっている |

`--auto` は「片付け可」だけを消す。未コミット変更・未 push コミットがあるもの、エージェントが working / blocked の space、本体リポジトリには決して触らない。**消すのはワークツリーのディレクトリだけで、ブランチは残る**（`--delete-branch` を付けたときのみ消す）ので、`gwt <branch>` で作り直せる。

#### 自動で片付ける

`./install.sh --launchd` で `~/Library/LaunchAgents/local.herdr-env.wt-audit.plist` が入り、30分ごとに `wt-audit --auto` が走る（テンプレートは `launchd/herdr-env.wt-audit.plist.in`。手で置くなら `@HOME@` と `@BREW_PREFIX@` を書き換えて `launchctl bootstrap gui/$(id -u) <plist>`）。

片付けたときは herdr の通知が出る。ログは `~/.local/state/wt-audit/audit.log`。止めるときは `launchctl bootout gui/$(id -u)/local.herdr-env.wt-audit`。

実装上の注意:

- `gwq list -g` と `gwq remove -g` はベースディレクトリ全体を舐めるため1分以上かかる。`wt-audit` は必ず本体リポジトリの中から `gwq` を呼び、リポジトリの列挙は `ghq list -p` で行っている。
- `gwq list` は**ベースディレクトリを共有するリポジトリすべてで同じ一覧を返す**。全リポジトリを舐めると同じワークツリーがリポジトリ数だけ重複するので、`path` で一意化している（しないと 40 件が 1486 件になり、棚卸しに15分以上かかる）。全リポジトリ対象でも26秒程度で終わる。

### 6. Herdr プラグイン

ローカルプラグイン（このリポジトリに同梱）:

```sh
mkdir -p ~/.config/herdr/plugins/local
cp -R herdr/plugins/local/agent-view-space ~/.config/herdr/plugins/local/
cp -R herdr/plugins/local/pane-title-sync  ~/.config/herdr/plugins/local/
cp -R herdr/plugins/local/pane-id-copy     ~/.config/herdr/plugins/local/
cp -R herdr/plugins/local/pr-status        ~/.config/herdr/plugins/local/
cp -R herdr/plugins/local/worktree-setup   ~/.config/herdr/plugins/local/
cp -R herdr/plugins/local/issue-board      ~/.config/herdr/plugins/local/
herdr plugin link ~/.config/herdr/plugins/local/agent-view-space
herdr plugin link ~/.config/herdr/plugins/local/pane-title-sync
herdr plugin link ~/.config/herdr/plugins/local/pane-id-copy
herdr plugin link ~/.config/herdr/plugins/local/pr-status
herdr plugin link ~/.config/herdr/plugins/local/worktree-setup
herdr plugin link ~/.config/herdr/plugins/local/issue-board
ln -sf ~/.config/herdr/plugins/local/pr-status/bin/pr_status.py ~/.local/bin/herdr-pr-status
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

#### pr-status: space の行に PR の状態を出す

ワークツリーの space の3行目に、そのブランチの PR を出す。PR が無い space では行ごと消える。

```
⠿ feat/knowledge-status-legend
  feat/knowledge-status-legend ↑1
  #485 競合  CI ✓  CR ✓ 未解決1
```

| トークン | 値 |
|---|---|
| `$pr` | `#123` / `#123 draft` / `#123 競合` / `#123 merged` / `#123 closed` |
| `$ci` | `CI ✓` / `CI …` / `CI ✗2`（CodeRabbit を除いた checks） |
| `$rv` | `CR …` / `CR ✓`（CodeRabbit の check）と、`要修正` / `未解決N`（未解決のレビュースレッド）/ `承認` |

色は `config.toml` の `rules`（値に含まれる文字）で付けている。gh の呼び出しはリポジトリごとに GraphQL 1回で、open な PR をまとめて取る。open に無いブランチだけ `gh pr list --head` で1回引き、merged / closed / PR 無しを `~/.local/state/herdr-pr-status/state.json` に覚える。

取り直すタイミング:

- herdr の起動時（`[[startup]]`、metadata はサーバ再起動で消えるため）
- ワークツリーを開いたとき（`worktree.opened`）と、space にフォーカスしたとき（`workspace.focused`、60秒以内に取った結果があればそれを使う）
- その space の agent が1ターン終えたとき（`pane.agent_status_changed` で idle / done。push・PR 作成・指摘対応の直後。15秒以内の結果は使い回す）
- `gh-ci-wait` の checks の状態が変わったときと、終わったとき（`herdr-pr-status sync --path "$PWD" --fresh`）
- launchd で毎分（見ていない space のレビューや merge を拾う。GraphQL は1回3ポイントで、リポジトリごとに1回）

定期実行は `./install.sh --launchd` で入る（`local.herdr-env.herdr-pr-status`、テンプレートは `launchd/herdr-env.herdr-pr-status.plist.in`）。

トークンの TTL は30分なので、定期実行が止まれば古い表示は勝手に消える。

GUI（Herdr.app）にも同じ値を出す（`sync --all` と `sync --path` が GUI の Space も対象にする）。GUI 側の仕組みは「Herdr の GUI（Herdr.app）で使う」の表を参照。

#### worktree-setup: ワークツリーを開いたら .env を置いて依存を入れる

`gwt` は `herdr worktree open` を使うので、`worktree.opened`（herdr の create なら `worktree.created`）をフックして、初回だけ次をやる。

1. main のチェックアウトで **git 管理外かつ .gitignore に当たっている**設定ファイル（`.env` / `.env.*` / `.envrc` / `.dev.vars` / `*.local.{toml,json,yaml,yml}`）を同じ相対パスへコピー。`.claude/settings.local.json` もここで付いてくる。ワークツリー側に既にあるものは上書きしない
2. ルートの lockfile で依存を入れる（pnpm / npm / bun / yarn / uv の順、`mise exec --` 経由）。`node_modules` / `.venv` が既にあれば触らない。JS は `.node-version` / `.nvmrc` があればそのバージョンを `mise exec node@<版> --` で明示する（mise は既定ではこれらを読まないので、全体の既定の Node で走って `EBADENGINE` で落ちたことがある）
3. `config.json` の `setup` に書いたコマンドを実行

「初回」の印は `<git-dir>/herdr-setup.json`（`.git/worktrees/<name>/` の中）なので、ワークツリーを消せば一緒に消える。実行中はその space の1行目に `⚙ setup`、失敗すると `✗ setup` と herdr の通知が出る。ログは `~/.local/state/herdr/plugins/worktree-setup/<ディレクトリ名>.log`。

導入したときに既にあったワークツリーで走らないよう、先に印を付けておく:

```sh
for p in $(gwq list -g --json | jq -r '.[] | select(.is_main == false) | .path'); do
  gd=$(git -C "$p" rev-parse --absolute-git-dir) && [ ! -e "$gd/herdr-setup.json" ] \
    && echo '{"status":"preexisting"}' > "$gd/herdr-setup.json"
done
```

リポジトリごとの上書きは `$(herdr plugin config-dir worktree-setup)/config.json`:

```json
{"default": {"install": true},
 "repos": {"my-app": {"setup": [["npm", "run", "prepare"]]}}}
```

やり直すときは、その space でプラグインアクション「このワークツリーのセットアップをやり直す」（`herdr plugin action invoke worktree-setup.rerun`）。

space が閉じたとき・ワークツリーが消えたとき（`workspace.closed` / `worktree.removed`）は `gwt-tasks prune` を走らせて、agent もワークツリーも無くなった依頼を台帳から外す。

#### issue-board: main の space の右に issue / PR 一覧を出す

（TUI 用。GUI では右パネルの Issues を使う。上の「Issues パネル（GUI）」）

`config.json` に書いたリポジトリの **main の space** だけ、今のタブの右（幅34%）に GitHub の open な issue を fzf で出す。`^t` で open な PR の一覧（作成の新しい順）に切り替わる。ワークツリーの space には出さない。`workspace.focused` / `tab.focused` / `tab.created` で、そのタブに一覧が無ければ開く。

| キー | 動き |
|---|---|
| `↵` | 着手。タイトルから haiku がブランチ名 `<type>/<english-slug>-<番号>`（例 `feat/add-login-rate-limit-351`）を作り、確認画面で直してから `gwt --task` で切って、新しい space の Claude Code に issue を渡す。ブランチ名がそのまま space 名になるので、内容が分かる名前にしている。番号入りのワークツリーが既にあれば、その space へ移るだけ |
| `^t` | issue と PR を切り替える |
| `^o` | ブラウザで開く |
| `^r` | 取り直す（普段は1分ごとと、そのタブに来たときに自動。gh の結果は全一覧で共有して30秒使い回す） |
| `^p` | プレビュー（本文と最新コメント2件）の表示切り替え |

PR の一覧（`^t`）は、pr-status と同じ GraphQL 1回で取り、行に状態を並べる: `● #485 競合 CR✓ 未解決1 feat(knowledge): … @octocat`（`●` はそのブランチのワークツリーがある）。プレビューは checks（失敗・実行中が上）と本文で、一覧のキャッシュから出すので gh は呼ばない。`↵` はそのブランチの space へ移る。space が無ければ「Claude Code つきで開く / シェルだけで開く（見るだけ）」を選んで `gwt` する（ローカルにブランチが無ければ origin から取る。フォークの PR は開かない）。

モード（issue / PR）は一覧ペインごとに state のファイルで持ち、`^t` は `transform(mode.sh switch)` 1つで切り替える。`execute-silent(...)+reload(...)+transform-prompt(...)` のように reload の後ろに操作をつなぐと、読み込み中に押したときに後ろの操作（プロンプトの更新など）が落ちることがあった。

ワークツリーのブランチ名に番号が入っている issue には `●` が付く。ブランチ名を作る `claude -p` は `--setting-sources project` で一時ディレクトリから呼び、自分の hooks（herdr への状態報告など）を走らせない（`--bare` だとログイン情報も読まれず失敗する）。着手時は `HERDR_PANE_ID` を外して `gwt` を呼ぶので、依頼元はこの一覧ペインにならず、agent の質問は人間に来る。

- 出し入れ: `prefix+shift+i`（`issue-board.toggle`）。消した space では自動で開かなくなる
- 対象と幅: `$(herdr plugin config-dir issue-board)/config.json` に `{"repos": ["my-app"], "width": 0.34}`。`repos` はリポジトリのディレクトリ名で、`"*"` なら全リポジトリ。**既定は空なので、書くまでどこにも出ない**
- herdr のペイン情報にはプラグインの所有者が出ないので、開いたペインは `~/.local/state/herdr/plugins/issue-board/state.json` に tab_id → pane_id で覚えている
- `--cwd` を付けて `plugin pane open` すると、マニフェストの相対パス（`bin/board.sh`）が解決できずに即終了する。リポジトリは環境変数 `ISSUE_BOARD_REPO_ROOT` で渡している
- `pane resize --amount` はタブ幅に対する割合で、`0` を渡すと変えずにレイアウトだけ返る。これで今の幅を測ってから合わせている

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
herdr-wait set --pid 1234 "CI 待ち"             # PID 1234 が生きている間だけ有効な印
herdr-wait clear                                 # 外す
herdr-wait clear --if-pid 1234                   # PID 1234 で立てた印のときだけ外す
herdr-wait list                                  # 印が立っている全ペイン
```

印は `~/.local/state/herdr-wait/<pane_id>` に置かれ、6時間（`HERDR_WAIT_TTL`）で自動失効する。状態が working になるのに加えて、`herdr-activity.sh` が state_text を `⏳ 外部待ち`、`$act` を `⏳ <ラベル> <経過時間>` に差し替える。

印の付け外しは Claude 自身にやらせる。`claude/CLAUDE.snippet.md` の「外部待ちは herdr に印を立てる」がその指示。

**人間の返事待ちは外部待ちより優先する。** 印が残ったまま Claude が人間に「進めてよいですか」と聞いてターンを終えると、ステータスラインの `· ⏳ …` 行のせいでペインは working のままになり、人間の番であることがサイドバーから消える。手で立てた委譲待ちの印を、返事が届いた後に消し忘れたときに実際に起きた。そこで:

- `herdr-activity.sh stop` が最後の返答（Stop 入力の `last_assistant_message`、無ければトランスクリプトの末尾）の末尾3行を見て、人間への問いかけ（`？` で終わる、`〜ますか。`、「お返事」「教えてください」「承認をお願い」など）なら `herdr-wait human on` で旗を立てる。
- 旗が立っている間、`herdr-wait statusline` は何も出さない。ペインは idle / done に落ち、state_text と `$act` は `💬 要返答` になる。外部待ちの印は消さずに残す。
- 人間が入力すると（UserPromptSubmit）旗は降り、印が残っていれば working 表示に戻る。
- 旗は `~/.local/state/herdr-wait/human/<pane_id>`。判定は文面の推測なので、問いかけの形を外れた聞き方は取りこぼす。Claude には「人間に聞くときは返答の最後を問いかけで終える」よう CLAUDE.md で指示している。

```sh
herdr-wait human on|off|status   # 人間の返事待ちの旗（通常はフックが操作する）
herdr-wait refresh [--act <文言>] # 印と旗から state_text と $act を出し直す
```

#### CI 待ち（gh-ci-wait）

Claude に CI を待たせると、その場で `until [ "$(gh run list …)" = 0 ]; do sleep; done` を書いて裏で回しがちで、次の理由でペインが working のまま居座る。

- `gh` が一度でも失敗すると空文字になり、`= 0` が成り立たず永久に回る。上限時間も無い。
- push 直後は PR の head が古く、前のコミットの完了済みの結果を見て即終了する（逆に待ちたい結果を取りこぼす）。
- 待ち印を手で `set` / `clear` するので、ループが終わっても死んでも印が残り、ステータスラインの `· ⏳ …` 行で working 判定が続く。

`gh-ci-wait <PR番号>` はこれを塞ぐ。

- 待ちたい SHA（今のブランチが PR のブランチで push 済みならローカルの HEAD）が PR の head になるのを待ってから、`gh pr checks --json` の `bucket` を見る。checks が出始めてから60秒は待ち、pending 0 を2回続けて見て終わる（後から登録されるワークフローの取りこぼし対策）。
- `gh` の失敗は6回連続までは流す。全体に上限（既定60分）。head が進んだら（新しい push）exit 4 で終わる。
- 待ち印は `herdr-wait set --pid $$` で立て、`trap … EXIT` で `clear --if-pid $$` する。kill -9 でも、`herdr-wait` 側が PID の生存を見て印を無効にする。
- 終了コード: 0 成功 / 1 失敗・キャンセルあり / 2 時間切れ / 3 gh の失敗が続いた / 4 checks が出ない・head が合わない・head が進んだ。

Claude には Bash の `run_in_background: true` で起動させ、完了の task-notification で再開させる（`claude/CLAUDE.snippet.md` の「GitHub Actions / PR の CI を待つときは `gh-ci-wait`」）。

最後に Herdr スキルを Claude Code に入れる:

```sh
mkdir -p ~/.claude/skills/herdr
cp claude/skills/herdr/SKILL.md ~/.claude/skills/herdr/
```

（このスキルは `herdr --skill` の出力と同等のもの。最新版はそちらから取り直せる）

#### 複数アカウント（claude-account）

Claude Code のアカウント（Team・個人など）を herdr の上で使い分ける。考え方は [T3 Code](https://github.com/pingdotgg/t3code) の provider instance と同じ:

- **アカウント = `CLAUDE_CONFIG_DIR`**。`HOME` は変えない。macOS ではログイン情報が Keychain にあり、項目名が `CLAUDE_CONFIG_DIR` ごとに変わるので、ディレクトリを分けるだけでログインが分かれる。
- **既定のアカウントは `~/.claude` のまま `CLAUDE_CONFIG_DIR` を設定しない**。`CLAUDE_CONFIG_DIR=~/.claude` と明示すると Keychain の項目名が変わり、未ログイン扱いになる。
- **追加アカウントは shadow ディレクトリ**（Codex の shadow home と同じ）。自分で持つのは `.claude.json`（ログイン先・MCP・フォルダの信頼）と、上限の集計などアカウント固有のものだけ。`settings.json`・`CLAUDE.md`・hooks・skills・plugins・`projects`（会話の記録と auto memory）・history は `~/.claude` への symlink。会話の記録を共有するので、同じ会話を別のアカウントで `--resume` できる。
- 共有するもの・しないものは `claude-account.py` の `SHARED_DIRS` / `SHARED_FILES`。分からないものは共有しない。

```sh
install -m 755 herdr/bin/claude-account.py ~/.config/herdr/bin/
ln -sf ~/.config/herdr/bin/claude-account.py ~/.local/bin/claude-account
mkdir -p ~/.config/claude-accounts
cp claude-accounts/accounts.example ~/.config/claude-accounts/accounts   # 自分のアカウントに書き換える
claude-account init --all            # 追加アカウントのディレクトリと symlink を作る
claude-account login personal        # アカウントごとにブラウザでログイン（使う組織を選ぶ）
claude-account list                  # ログイン先の確認
cp -R herdr/plugins/local/claude-accounts ~/.config/herdr/plugins/local/
herdr plugin link ~/.config/herdr/plugins/local/claude-accounts
```

`.zshrc` から `zsh/claude-account.zsh` を `source` すると、`claude -A <id>` でアカウントを選んで起動できる。`settings-hooks.snippet.json` の SessionStart / SessionEnd と `statusline.sh` の末尾が、サイドバーの `$acct`（`🟪 Personal  5h 62%  7d 90%`）を出す。上限の残りは statusLine の入力（その claude プロセス自身の値）から取るので、アカウントを取り違えない。

| 操作 | やり方 |
|---|---|
| 選んだアカウントで新しいタブに起動 | `prefix+shift+u` → Enter（`claude-account start <id>`） |
| 今のペインの会話を別アカウントで続ける | `prefix+shift+u` → ctrl-s（`claude-account switch <id>`）。`/exit` して、そのペインのシェルに `CLAUDE_CONFIG_DIR` を入れ、同じ agent 名で `--resume` する |
| シェルから | `claude -A <id>`、`eval "$(claude-account env <id>)"` |

注意:

- usagebar（Agent Usage）に `[[claude.profiles]]` を足すと、上限ペインと通知がアカウントごとになる。ただし usagebar はセッションのファイルがどのプロファイルの `projects` にあるかでペインのアカウントを決めるため、`projects` を共有していると判定が当たらない。サイドバーでは usagebar の `$limit` の代わりに `$acct` を使う。
- 追加アカウントの `.claude.json` は、作るときに既定の `mcpServers` と信頼済みフォルダだけを写す。あとから既定に足した MCP サーバーは写らない。
- 棚（herdr-shelf）を使っているなら、開き直すときは `claude-account of-session` でしまったときのアカウントを引く。

### 8. Claude Code へのワークフロー指示

`claude/CLAUDE.snippet.md` の内容を `~/.claude/CLAUDE.md` に追記する。これで Claude が ghq/gwq/gwt の流儀（本体でブランチを切り替えない、作業は worktree から始める等）を守るようになる。

`gwt` でペインを移したあと、Claude Code の Bash の cwd がワークツリーに留まるよう、ワークツリーの置き場所を `~/.claude/settings.json` の作業ディレクトリに足しておく（足さないと、本体リポジトリで起動した agent は cd してもリポジトリへ戻される）:

```json
{ "permissions": { "additionalDirectories": ["~/workspace/worktree"] } }
```

## 日常の使い方

```sh
# 本体リポジトリで:
gwt feature/foo        # worktree 作成 → Herdr workspace。agent ペインからならそのペインを移す / シェルからなら Claude 起動
gwt feature/foo --task "issue #123 を実装。PR まで。"   # 新しい Claude に引き継ぎを渡して台帳に記録
gwt feature/foo --no-agent   # workspace を開くだけ（ペインも移さず Claude も起動しない）
gwt-tasks              # 別 space に任せた作業の一覧
gwt-tasks show <agent> # 依頼の全文と委譲先の直近の返答
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
- `~/.local/state/gwt-tasks/` — `gwt-tasks` の台帳（その環境で出した依頼そのもの）

## ライセンス

[MIT](LICENSE)
