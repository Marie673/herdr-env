# ~/.claude/CLAUDE.md に追記するスニペット

Claude Code にこのワークフローを守らせるための指示。グローバルの `~/.claude/CLAUDE.md`（またはプロジェクトの `CLAUDE.md`）に貼る。

---

## 外部待ちは herdr に印を立てる

`HERDR_ENV=1` のとき、herdr のサイドバーはエージェントの状態を idle / done としか表示できない。「用が終わって手空き」と「外部の返事を待っているだけ」が同じ見た目になるので、待ちに入ったら明示的に印を立てる（`herdr/bin/herdr-wait.sh` を `~/.local/bin/herdr-wait` から呼べるようにしておく）。

- CodeRabbit のレビュー、デプロイなど、**こちらでは進められない外部の返事を待つ状態に入ったら** `herdr-wait set "<何を待っているか>"` を実行する（例: `herdr-wait set "CodeRabbit レビュー待ち #123"`）。応答を終える直前に実行する。
- 待ちが解けたら（レビューが届いた / CI が緑になった / 待つのをやめた）**必ず** `herdr-wait clear` を実行する。
- PR の CI は `gh-ci-wait`、任せた agent の返答は `herdr-await` が、自分の PID つきで印を立てて終了時に外す。この2つの待ちには `herdr-wait set` を打たない。
- 印が立っている間、サイドバーの状態表示は「⏳ 外部待ち」、活動行は「⏳ <ラベル> <経過時間>」になる。
- 印は6時間で自動失効するので、消し忘れが居座ることはない。
- **人間に聞くときは印を立てない。** 人間の返事待ちは外部待ちではない。返答の最後を問いかけ（「〜してよいですか。」「教えてください。」）で終えれば、Stop フックがそれを見て状態を「💬 要返答」に切り替える。外部待ちの印が残っていても、こちらが優先され、working には見えない。
- `HERDR_ENV=1` でない環境では何もしなくてよい。

## GitHub Actions / PR の CI を待つときは `gh-ci-wait`

- PR の CI 完了を待つときは、`until … gh run list …; do sleep; done` や `gh run watch` を自分で組まず、**`gh-ci-wait <PR番号>` を Bash の `run_in_background: true` で起動**する。起動したら応答を終え、完了の task-notification を待つ。
  - 自作ループは、`gh` が一度失敗すると空文字になって永久に回る、push 直後に前のコミットの結果で即終了する、待ち印を消し忘れる、の3つで「working のまま」になる。
- `gh-ci-wait` は待っている間、自分の PID つきで `herdr-wait` の印を立て、終了時（成功・失敗・中断・kill）に印が消える。**CI 待ちのために `herdr-wait set` を別に打たない。**
- 終了コード: 0 全部成功 / 1 失敗・キャンセルあり / 2 時間切れ（既定60分、`--timeout 2h` で変更） / 3 gh の失敗が続いた / 4 checks が出ない・head が合わない・新しい push で head が進んだ。最後に失敗した checks の一覧とリンクが出る。
- push 直後でも、今のブランチが PR のブランチで push 済みならローカルの HEAD の checks を待つ。別リポジトリは `-R owner/repo`、特定コミットは `--sha`。
- push し直したら、前の `gh-ci-wait` の完了を待たず新しく起動し直してよい（古い方は head が進んだことを検知して exit 4 で終わる）。

## Repository management with ghq

- Use `ghq root` to find the managed repository root and `ghq list -p` to list absolute repository paths.
- Clone over SSH with `ghq get -p <owner>/<repo>`; use `ghq get <URL>` when an explicit URL or non-SSH transport is required.
- Use `ghq get -u <owner>/<repo>` to fetch updates for an already managed repository.
- Locate repositories with `ghq list <query>`; add `-p` when a filesystem path is needed.
- Do not manually clone a repository into an arbitrary directory when it should be managed by ghq.

## Worktree management with gwq

- Use `gwq list` inside a repository and `gwq list -g` to inspect all managed worktrees.
- Create a worktree for an existing branch with `gwq add <branch>` and create a new branch plus worktree with `gwq add -b <branch>`.
- Resolve a worktree path with `gwq get <pattern>`; execute without changing directories with `gwq exec <pattern> -- <command>`.
- Enter a worktree with `gwq cd <pattern>` when an interactive shell is appropriate.
- Before removal, run `gwq remove --dry-run <pattern>`. Treat `gwq remove`, `gwq remove -f`, `gwq remove -b`, and `--force-delete-branch` as destructive operations that need user approval.

## Branch work must start from a gwq worktree

- Repositories under `~/workspace/github.com/` are the canonical checkouts and stay on `main` / `master`. A `reference-transaction` hook (`~/.config/git/hooks/reference-transaction`) aborts `git switch` / `git checkout` there, so never plan work that depends on switching branches in the canonical checkout.
- To start branch work, run `gwt <branch>` from inside the canonical checkout. It creates the gwq worktree, opens it as a Herdr workspace, and starts Claude Code in that workspace's root pane.
- Under Herdr (`HERDR_ENV=1`), do not continue branch work in the current workspace. Move the work to the workspace that `gwt` opened for the worktree.
- When you are the one handing work off, pass the handover with `gwt <branch> --task "<引き継ぎ文>"` (or `--task-file <path>` when it is long). **You write the handover text**, not the user: fold in what you already found, the assumptions you are making, and what counts as done. Being given only a branch name does not excuse dropping the context.
- `--task` sends that text to the new agent as its first prompt and records the request in the `gwt-tasks` ledger. It does not block; the launching side keeps working.
- When running the steps yourself instead of `gwt`, keep the same order:
  1. `gwq add -b <branch>` (or `gwq add <branch>` for an existing branch)
  2. `herdr worktree open --cwd <repo-root> --path "$(gwq list --json | jq -r --arg b <branch> '.[] | select(.branch == $b and .is_main == false) | .path')" --label <branch> --no-focus`
  3. `herdr agent start <name> --kind claude --pane <root-pane-id>`
- `GIT_ALLOW_BRANCH_SWITCH=1` bypasses the hook. Use it only when the user explicitly asks to switch branches in the canonical checkout.

## 別 space に任せた作業は `gwt-tasks` で追う

herdr は agent の状態（実行中 / 待機 / 要承認 / 完了）しか持たない。「何を任せたのか」「誰が任せたのか」は持っていないので、`gwt` で切り出しただけでは起動側から作業を追えない。台帳はそこを埋めるためにある。

- 一覧: `gwt-tasks`（状態・タスク・経過・space）。`--mine` で自分のペインから出した依頼だけに絞る。`--json` は機械可読。
- 詳細: `gwt-tasks show <agent>`。依頼の全文と、**委譲先の直近の返答**が出る。返答は Claude Code のトランスクリプトから読むので、agent が終了した後でも読める。`--replies N` で遡る数を増やす。
- 片付け: 依頼が済んだら `gwt-tasks done <agent>`。`gwt-tasks prune` は agent もワークツリーも無い依頼だけを外す（ワークツリーが残っているものは作業途中とみなして残す）。
- 状態の情報源は常に herdr。台帳は状態を持たないので、食い違ったら herdr が正しい。
- 依頼の待ちの印は `herdr-await` が自分の PID つきで立て、相手が止まると外す（複数待っていれば最後の1つが終わるまで残す）。**委譲の待ちのために `herdr-wait set` を別に打たない。** 手で立てた印は、返事が届いても誰も外さず working のまま居座る。

## agent 同士の質問・承認は、人間ではなく依頼元に回す

別の agent に作業を任せる（`gwt --task` / `herdr agent prompt`）と、任された側が選択肢や承認で止まる。この質問は人間ではなく、**任せた側の agent** が受ける。台帳（`gwt-tasks`）の「依頼元」が宛先。

### 任された側（依頼先）
- 選択肢・仕様の判断・確認が要るときは、AskUserQuestion を使わず `herdr-ask send "<質問、選択肢、推奨案>"` で依頼元に聞く。送ったらそのターンを終える。返答は新しい入力として届く。
- 区切りごとと完了時も `herdr-ask send` で依頼元に一行で報告する。進捗は `herdr agent prompt --wait` の無言ブロックでは伝わらない。
- `herdr-ask` が失敗した（依頼元がいない、承認待ちで届かない）ときだけ人間に聞く。依頼元が人間なら従来どおり人間に聞く。
- Claude では、依頼元がいる pane で AskUserQuestion を呼ぶと PreToolUse フックが deny して `herdr-ask` に誘導する。承認待ちで止まると Notification フックが依頼元に知らせる。Codex にはこのフックが無いので、この規約が頼り。

### 任せた側（依頼元）
- 依頼を出したら `herdr-await <agent>` を Bash の `run_in_background: true` で起動し、応答を終える。相手が止まる（idle / done / blocked）と完了通知が届く。blocked なら画面の下端も出る。
- `[依頼先 … から]` で始まる入力は、人間ではなく依頼先 agent からの質問。依頼の趣旨で決められるものは自分で決め、`herdr agent prompt <agent> "<回答>"` で返す。blocked の選択肢は `herdr agent read <agent> --source detection` で読み、`herdr agent send-keys` で答える。
- herdr スキルの「blocked はユーザーに聞く」は、**自分が任せた agent** には当てはまらない。ただし次は自分で承認せず、人間に確認する: このファイルの approval policy で人間の承認が要る操作（作業ディレクトリ外の削除、force push、`gcloud`/`aws` の変更系）、費用が発生する操作、依頼にも趣旨にも書かれていない判断。
- 人間の承認を待つときは `herdr-wait set` を使わない。返答の最後を問いかけで終えれば「💬 要返答」と出る（上の「外部待ちは herdr に印を立てる」参照）。
