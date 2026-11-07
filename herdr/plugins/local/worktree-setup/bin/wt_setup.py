#!/usr/bin/env python3
"""ワークツリーの初回セットアップと、space が閉じたあとの片付け。

  wt_setup.py event                 worktree.opened / worktree.created のフック
  wt_setup.py run [--path <dir>] [--force]   手で実行（--path 省略時はフォーカス中の space）
  wt_setup.py teardown              worktree.removed / workspace.closed のフック（gwt-tasks prune）

セットアップの中身（初回だけ。印は <git-dir>/herdr-setup.json。ワークツリーを消せば一緒に消える）:
  1. main のチェックアウトにある、git 管理外で無視されている設定ファイル（.env や *.local.json など）を
     同じ相対パスへコピーする。ワークツリー側に既にあるものは上書きしない
  2. ルートの lockfile に合わせて依存を入れる（pnpm / npm / bun / yarn / uv）。mise があれば mise exec 経由
  3. config.json の setup に書いたコマンドを順に実行する

設定は $HERDR_PLUGIN_CONFIG_DIR/config.json（無ければ既定値）。repos の下にリポジトリのディレクトリ名で上書きを書く:
  {"default": {"install": true},
   "repos": {"ai-president-next": {"install": false, "setup": [["npm", "run", "prepare"]]}}}

実行中はその space に $setup = "⚙ setup"、失敗したら "✗ setup" を出し、herdr の通知も出す。
"""
import argparse
import fnmatch
import json
import os
import shutil
import subprocess
import sys
import time

SOURCE = "plugin:worktree-setup"
HOME = os.path.expanduser("~")
HERDR = os.environ.get("HERDR_BIN_PATH") or shutil.which("herdr") or "/opt/homebrew/bin/herdr"
GIT = shutil.which("git") or "/usr/bin/git"
MISE = shutil.which("mise") or ("/opt/homebrew/bin/mise" if os.path.exists("/opt/homebrew/bin/mise") else None)
CONFIG_DIR = os.environ.get("HERDR_PLUGIN_CONFIG_DIR") or os.path.join(HOME, ".config/herdr/plugins/config/worktree-setup")
STATE_DIR = os.environ.get("HERDR_PLUGIN_STATE_DIR") or os.path.join(HOME, ".local/state/herdr/plugins/worktree-setup")
INSTALL_TIMEOUT = 20 * 60

DEFAULTS = {
    # basename に対する glob。コピーするのは「git 管理外で、.gitignore に当たっているファイル」だけ
    "copy": [".env", ".env.*", ".envrc", ".dev.vars", "*.local.toml", "*.local.json", "*.local.yaml", "*.local.yml"],
    "copy_exclude": ["*.example", "*.sample", "*.template", "*.dist"],
    "install": True,
    "setup": [],
}
SKIP_DIRS = {"node_modules", ".venv", "venv", ".git", ".pnpm-store", ".terraform", "dist", "build", ".next"}

# 上から順に見て、最初に見つかった lockfile で入れる
INSTALLERS = [
    ("pnpm-lock.yaml", ["pnpm", "install", "--frozen-lockfile"]),
    ("package-lock.json", ["npm", "ci"]),
    ("bun.lock", ["bun", "install", "--frozen-lockfile"]),
    ("bun.lockb", ["bun", "install", "--frozen-lockfile"]),
    ("yarn.lock", ["yarn", "install", "--frozen-lockfile"]),
    ("uv.lock", ["uv", "sync"]),
]


class Log:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.path = path
        self.f = open(path, "a")

    def __call__(self, msg):
        line = "[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
        self.f.write(line)
        self.f.flush()
        sys.stderr.write(line)


def git(path, *args):
    p = subprocess.run([GIT, "-C", path] + list(args), capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else None


def herdr(*args):
    subprocess.run([HERDR] + list(args), capture_output=True, text=True)


def set_token(ws_id, value, ttl_ms):
    if not ws_id:
        return
    if value:
        herdr("workspace", "report-metadata", ws_id, "--source", SOURCE, "--token", "setup=" + value,
              "--ttl-ms", str(ttl_ms))
    else:
        herdr("workspace", "report-metadata", ws_id, "--source", SOURCE, "--clear-token", "setup")


def load_config(repo_name):
    conf = dict(DEFAULTS)
    try:
        with open(os.path.join(CONFIG_DIR, "config.json")) as f:
            raw = json.load(f)
    except (OSError, ValueError):
        raw = {}
    conf.update(raw.get("default") or {})
    conf.update((raw.get("repos") or {}).get(repo_name) or {})
    return conf


def child_env():
    env = dict(os.environ)
    extra = [os.path.join(HOME, ".local/bin"), os.path.join(HOME, ".local/share/mise/shims"), "/opt/homebrew/bin"]
    env["PATH"] = ":".join(extra + [env.get("PATH", "/usr/bin:/bin")])
    # 依存のインストールに herdr のプラグイン文脈は要らない
    for k in list(env):
        if k.startswith("HERDR_PLUGIN_"):
            del env[k]
    return env


def run_cmd(argv, cwd, log, timeout=INSTALL_TIMEOUT, tools=()):
    """tools: mise exec に渡すツール指定（例 "node@26.13.0"）。mise は .nvmrc などを読まない設定なので明示する"""
    if MISE and argv[0] != MISE:
        argv = [MISE, "exec"] + list(tools) + ["--"] + argv
    log("$ " + " ".join(argv))
    try:
        p = subprocess.run(argv, cwd=cwd, env=child_env(), stdin=subprocess.DEVNULL,
                           stdout=log.f, stderr=subprocess.STDOUT, timeout=timeout)
    except subprocess.TimeoutExpired:
        log("時間切れ（%d分）" % (timeout // 60))
        return False
    except OSError as e:
        log("起動できません: %s" % e)
        return False
    if p.returncode != 0:
        log("終了コード %d" % p.returncode)
        return False
    return True


# ---------- セットアップ ----------

def copy_local_files(main, wt, conf, log):
    out = subprocess.run([GIT, "-C", main, "ls-files", "-z", "--others", "--ignored", "--exclude-standard",
                          "--directory", "--no-empty-directory"], capture_output=True)
    if out.returncode != 0:
        log("main の無視ファイルを列挙できません")
        return
    copied = 0
    for rel in out.stdout.decode("utf-8", "replace").split("\0"):
        if not rel or rel.endswith("/"):
            continue  # ディレクトリごと無視されているもの（node_modules など）は見ない
        parts = rel.split("/")
        if SKIP_DIRS.intersection(parts[:-1]):
            continue
        name = parts[-1]
        if not any(fnmatch.fnmatch(name, g) for g in conf["copy"]):
            continue
        if any(fnmatch.fnmatch(name, g) for g in conf["copy_exclude"]):
            continue
        src, dst = os.path.join(main, rel), os.path.join(wt, rel)
        if os.path.lexists(dst) or not os.path.isfile(src):
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
        log("コピー: %s" % rel)
    log("コピーしたファイル: %d" % copied)


def node_version(wt):
    """.node-version / .nvmrc のバージョン（"v26.13.0" → "26.13.0"）。lts/* などの別名は mise に任せず無視する"""
    for name in (".node-version", ".nvmrc"):
        try:
            with open(os.path.join(wt, name)) as f:
                v = f.read().strip().lstrip("v")
        except OSError:
            continue
        if v and v[0].isdigit():
            return v
    return None


def install_deps(wt, log):
    for lock, argv in INSTALLERS:
        if os.path.exists(os.path.join(wt, lock)):
            # 既に入っているなら触らない（npm ci は node_modules を消してから入れ直すので、使用中だと壊す）
            installed = os.path.join(wt, ".venv" if lock == "uv.lock" else "node_modules")
            if os.path.isdir(installed):
                log("%s が既にあるので依存のインストールは飛ばします" % os.path.basename(installed))
                return True
            tools = []
            node = node_version(wt) if lock != "uv.lock" else None
            if node:
                tools.append("node@" + node)
            return run_cmd(argv, wt, log, tools=tools)
    log("ルートに lockfile が無いので依存のインストールは飛ばします")
    return True


def setup(wt, ws_id, force):
    wt = os.path.realpath(wt)
    git_dir = git(wt, "rev-parse", "--absolute-git-dir")
    common = git(wt, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if not git_dir or not common:
        return 0
    main = os.path.realpath(os.path.dirname(common))
    if main == wt:
        return 0  # main のチェックアウト自体は対象外

    marker = os.path.join(git_dir, "herdr-setup.json")
    if force and os.path.exists(marker):
        os.remove(marker)
    try:
        # opened と created が続けて来ても1回だけ走るよう、排他的に作る
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        return 0

    branch = git(wt, "symbolic-ref", "--quiet", "--short", "HEAD") or os.path.basename(wt)
    log = Log(os.path.join(STATE_DIR, os.path.basename(wt) + ".log"))
    repo_name = os.path.basename(main)
    conf = load_config(repo_name)
    log("セットアップ開始: %s (%s) main=%s" % (wt, branch, main))
    set_token(ws_id, "⚙ setup", 30 * 60 * 1000)

    ok = True
    try:
        copy_local_files(main, wt, conf, log)
        if conf.get("install"):
            ok = install_deps(wt, log) and ok
        for argv in conf.get("setup") or []:
            if not run_cmd(list(argv), wt, log):
                ok = False
                break
    except Exception as e:  # 印だけ残って中途半端にならないよう、失敗として記録する
        log("例外: %r" % e)
        ok = False

    status = {"status": "done" if ok else "failed", "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "log": log.path}
    with os.fdopen(fd, "w") as f:
        json.dump(status, f, ensure_ascii=False)
    log("セットアップ%s" % ("完了" if ok else "失敗"))
    if ok:
        set_token(ws_id, None, 0)
    else:
        set_token(ws_id, "✗ setup", 2 * 3600 * 1000)
        herdr("notification", "show", "ワークツリーのセットアップ失敗: %s" % branch,
              "--body", log.path, "--sound", "request")
    return 0 if ok else 1


# ---------- 入口 ----------

def find_key(obj, key):
    if isinstance(obj, dict):
        if isinstance(obj.get(key), dict):
            return obj[key]
        for v in obj.values():
            r = find_key(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_key(v, key)
            if r is not None:
                return r
    return None


def find_key_value(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = find_key_value(v, key)
            if r is not None:
                return r
    return None


def cmd_event():
    try:
        ev = json.loads(os.environ.get("HERDR_PLUGIN_EVENT_JSON") or "{}")
    except ValueError:
        return 0
    wt = find_key(ev, "worktree") or {}
    ws = find_key(ev, "workspace") or {}
    if not wt.get("path") or not wt.get("is_linked_worktree") or wt.get("is_bare"):
        return 0
    if find_key_value(ev, "already_open") is True:
        return 0  # 既に開いていた space に戻っただけ
    return setup(wt["path"], ws.get("workspace_id") or wt.get("open_workspace_id"), force=False)


def focused_worktree():
    ws_id = os.environ.get("HERDR_WORKSPACE_ID")
    if not ws_id:
        return None, None
    p = subprocess.run([HERDR, "workspace", "get", ws_id], capture_output=True, text=True)
    try:
        ws = json.loads(p.stdout)["result"]["workspace"]
    except (ValueError, KeyError, TypeError):
        return None, ws_id
    return (ws.get("worktree") or {}).get("checkout_path"), ws_id


def cmd_run(args):
    path, ws_id = (args.path, None) if args.path else focused_worktree()
    if not path:
        sys.stderr.write("wt_setup: 対象のワークツリーが分かりません（--path を指定）\n")
        return 2
    if not ws_id:
        top = git(path, "rev-parse", "--show-toplevel")
        p = subprocess.run([HERDR, "workspace", "list"], capture_output=True, text=True)
        try:
            for ws in json.loads(p.stdout)["result"]["workspaces"]:
                if top and (ws.get("worktree") or {}).get("checkout_path") == top:
                    ws_id = ws["workspace_id"]
        except (ValueError, KeyError, TypeError):
            pass
    return setup(path, ws_id, force=args.force)


def cmd_teardown():
    gwt_tasks = shutil.which("gwt-tasks", path=child_env()["PATH"])
    if gwt_tasks:
        subprocess.run([gwt_tasks, "prune"], env=child_env(), capture_output=True, text=True)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("event")
    p = sub.add_parser("run")
    p.add_argument("--path")
    p.add_argument("--force", action="store_true", help="印を無視してやり直す")
    sub.add_parser("teardown")
    args = ap.parse_args()
    if args.cmd == "event":
        return cmd_event()
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "teardown":
        return cmd_teardown()
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
