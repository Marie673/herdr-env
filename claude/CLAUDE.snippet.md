# ~/.claude/CLAUDE.md に追記するスニペット

Claude Code にこのワークフローを守らせるための指示。グローバルの `~/.claude/CLAUDE.md`（またはプロジェクトの `CLAUDE.md`）に貼る。

---

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
- When running the steps yourself instead of `gwt`, keep the same order:
  1. `gwq add -b <branch>` (or `gwq add <branch>` for an existing branch)
  2. `herdr worktree open --cwd <repo-root> --path "$(gwq list --json | jq -r --arg b <branch> '.[] | select(.branch == $b and .is_main == false) | .path')" --label <branch> --no-focus`
  3. `herdr agent start <name> --kind claude --pane <root-pane-id>`
- `GIT_ALLOW_BRANCH_SWITCH=1` bypasses the hook. Use it only when the user explicitly asks to switch branches in the canonical checkout.
