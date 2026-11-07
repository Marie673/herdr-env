#!/usr/bin/env python3
"""ワークツリーの space に、そのブランチの PR 状態をトークンで出す。

  pr_status.py sync --all [--fresh]       全ワークツリーの space を付け直す（startup / launchd）
  pr_status.py sync --workspace <id>      1つの space だけ
  pr_status.py sync --path <dir> [--fresh]  そのディレクトリを含むワークツリーの space（gh-ci-wait から）
  pr_status.py event                      プラグインのイベントフック（worktree.opened / workspace.focused /
                                          pane.agent_status_changed で idle・done になったとき）

出すトークン（workspace.report_metadata、source = plugin:pr-status）:
  $pr  "#123" / "#123 draft" / "#123 競合" / "#123 merged" / "#123 closed"
  $ci  "CI ✓" / "CI …" / "CI ✗2"          CodeRabbit を除いた checks
  $rv  "CR …" / "CR ✓" と "要修正" / "未解決3" / "承認" を並べたもの
PR が無いブランチはトークンを消す。

gh の呼び出しはリポジトリごとに GraphQL 1回（open な PR をまとめて取る）。open に無いブランチだけ
`gh pr list --head` で1回引き、merged / closed / PR 無しを state に覚えておく。
"""
import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time

SOURCE = "plugin:pr-status"
TOKENS = ("pr", "ci", "rv")
TTL_MS = 30 * 60 * 1000          # 定期実行が止まったら30分で消える
# 前回の取得からこの秒数以内なら gh を呼ばずにキャッシュを使う（呼び出し元ごと）
MAX_AGE_SYNC = 30                # sync（launchd は毎分）
MAX_AGE_FOCUS = 60               # space にフォーカスしたとき
MAX_AGE_AGENT = 15               # agent が止まったとき（push や PR 作成の直後）
NONE_RECHECK_SEC = 10 * 60       # PR 無しのブランチを引き直す間隔
TERMINAL_RECHECK_SEC = 6 * 3600  # merged / closed を引き直す間隔（同じブランチで PR を作り直したとき用）
CODERABBIT = "coderabbit"

STATE_DIR = os.path.expanduser("~/.local/state/herdr-pr-status")
HERDR = os.environ.get("HERDR_BIN_PATH") or shutil.which("herdr") or "/opt/homebrew/bin/herdr"
GH = shutil.which("gh") or "/opt/homebrew/bin/gh"
GIT = shutil.which("git") or "/usr/bin/git"

QUERY = """
query($owner: String!, $repo: String!) {
  repository(owner: $owner, name: $repo) {
    pullRequests(states: OPEN, first: 100, orderBy: {field: UPDATED_AT, direction: DESC}) {
      nodes {
        number headRefName isDraft reviewDecision mergeable
        commits(last: 1) { nodes { commit { statusCheckRollup { contexts(first: 100) { nodes {
          __typename
          ... on CheckRun { name status conclusion }
          ... on StatusContext { context state }
        } } } } } }
        reviewThreads(first: 100) { nodes { isResolved } }
      }
    }
  }
}
"""


def log(msg):
    sys.stderr.write("[pr-status] %s\n" % msg)


def run(cmd, cwd=None, timeout=60):
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        log("%s: %s" % (" ".join(cmd[:4]), e))
        return None
    if p.returncode != 0:
        log("%s: %s" % (" ".join(cmd[:4]), (p.stderr or p.stdout).strip()[:300]))
        return None
    return p.stdout


def herdr_json(*args):
    out = run([HERDR] + list(args))
    if not out:
        return None
    try:
        return json.loads(out).get("result")
    except ValueError:
        return None


# ---------- state ----------

def state_path(name):
    os.makedirs(STATE_DIR, exist_ok=True)
    return os.path.join(STATE_DIR, name)


def load_state():
    try:
        with open(state_path("state.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(st):
    tmp = state_path("state.json.tmp")
    with open(tmp, "w") as f:
        json.dump(st, f, ensure_ascii=False)
    os.replace(tmp, state_path("state.json"))


# ---------- 対象の space ----------

def worktree_workspaces():
    """linked worktree の space を [(workspace_id, checkout_path, repo_root)] で返す"""
    res = herdr_json("workspace", "list")
    if not res:
        return []
    out = []
    for ws in res.get("workspaces", []):
        wt = ws.get("worktree") or {}
        if wt.get("is_linked_worktree") and wt.get("checkout_path"):
            out.append((ws["workspace_id"], wt["checkout_path"], wt.get("repo_root") or wt["checkout_path"]))
    return out


def current_branch(path):
    out = run([GIT, "-C", path, "symbolic-ref", "--quiet", "--short", "HEAD"], timeout=10)
    return out.strip() if out else None


# ---------- GitHub ----------

def fetch_open_prs(repo_root, st, max_age):
    cache = st.setdefault("open", {}).get(repo_root)
    if cache and time.time() - cache["at"] < max_age:
        return cache["prs"]
    out = run([GH, "api", "graphql", "-F", "owner={owner}", "-F", "repo={repo}", "-f", "query=" + QUERY],
              cwd=repo_root, timeout=60)
    if out is None:
        return cache["prs"] if cache else None
    try:
        nodes = json.loads(out)["data"]["repository"]["pullRequests"]["nodes"]
    except (ValueError, KeyError, TypeError):
        return cache["prs"] if cache else None
    prs = {}
    for n in nodes:
        commits = (n.get("commits") or {}).get("nodes") or []
        rollup = (commits[0]["commit"].get("statusCheckRollup") if commits else None) or {}
        contexts = ((rollup.get("contexts") or {}).get("nodes")) or []
        threads = ((n.get("reviewThreads") or {}).get("nodes")) or []
        prs.setdefault(n["headRefName"], {
            "number": n["number"],
            "state": "OPEN",
            "draft": n.get("isDraft", False),
            "decision": n.get("reviewDecision") or "",
            "mergeable": n.get("mergeable") or "",
            "contexts": contexts,
            "unresolved": sum(1 for t in threads if not t.get("isResolved")),
        })
    st["open"][repo_root] = {"at": time.time(), "prs": prs}
    return prs


def lookup_closed(repo_root, branch, st):
    """open に無いブランチ: merged / closed / PR 無しを覚えておき、たまに引き直す"""
    key = repo_root + "\t" + branch
    known = st.setdefault("closed", {}).get(key)
    if known:
        age = time.time() - known["at"]
        limit = NONE_RECHECK_SEC if known.get("state") is None else TERMINAL_RECHECK_SEC
        if age < limit:
            return known if known.get("state") else None
    out = run([GH, "pr", "list", "--head", branch, "--state", "all", "--limit", "1",
               "--json", "number,state"], cwd=repo_root, timeout=30)
    if out is None:
        return known if known and known.get("state") else None
    try:
        items = json.loads(out)
    except ValueError:
        return None
    rec = {"at": time.time(), "state": None, "number": None}
    if items:
        rec.update(state=items[0]["state"], number=items[0]["number"])
    st["closed"][key] = rec
    # open に戻った PR（reopen）は次の open 取得で拾うので、ここでは OPEN を覚えない
    return rec if rec["state"] in ("MERGED", "CLOSED") else None


# ---------- 表示 ----------

def classify(ctx):
    if ctx.get("__typename") == "CheckRun":
        if ctx.get("status") != "COMPLETED":
            return "pending"
        c = ctx.get("conclusion") or ""
        if c in ("SUCCESS", "NEUTRAL"):
            return "ok"
        if c == "SKIPPED":
            return "skip"
        return "fail"
    s = ctx.get("state") or ""
    if s in ("PENDING", "EXPECTED"):
        return "pending"
    if s == "SUCCESS":
        return "ok"
    return "fail"


def ctx_name(ctx):
    return (ctx.get("name") or ctx.get("context") or "").lower()


def render(pr):
    if pr is None:
        return {}
    n = "#%d" % pr["number"]
    if pr["state"] == "MERGED":
        return {"pr": n + " merged"}
    if pr["state"] == "CLOSED":
        return {"pr": n + " closed"}

    tokens = {}
    label = n
    if pr.get("draft"):
        label += " draft"
    if pr.get("mergeable") == "CONFLICTING":
        label += " 競合"
    tokens["pr"] = label

    ci = [classify(c) for c in pr["contexts"] if CODERABBIT not in ctx_name(c)]
    fail = ci.count("fail")
    if fail:
        tokens["ci"] = "CI ✗%d" % fail
    elif "pending" in ci:
        tokens["ci"] = "CI …"
    elif "ok" in ci:
        tokens["ci"] = "CI ✓"

    rv = []
    cr = [classify(c) for c in pr["contexts"] if CODERABBIT in ctx_name(c)]
    if "pending" in cr:
        rv.append("CR …")
    elif "ok" in cr:
        rv.append("CR ✓")
    if pr.get("decision") == "CHANGES_REQUESTED":
        rv.append("要修正")
    elif pr.get("unresolved"):
        rv.append("未解決%d" % pr["unresolved"])
    elif pr.get("decision") == "APPROVED":
        rv.append("承認")
    if rv:
        tokens["rv"] = " ".join(rv)
    return tokens


def report(workspace_id, tokens):
    cmd = [HERDR, "workspace", "report-metadata", workspace_id, "--source", SOURCE, "--ttl-ms", str(TTL_MS)]
    for k in TOKENS:
        if tokens.get(k):
            cmd += ["--token", "%s=%s" % (k, tokens[k])]
        else:
            cmd += ["--clear-token", k]
    run(cmd, timeout=10)


# ---------- 本体 ----------

def sync(targets, max_age):
    """targets: [(workspace_id, checkout_path, repo_root)]"""
    if not targets:
        return
    lock = open(state_path("lock"), "w")
    fcntl.flock(lock, fcntl.LOCK_EX)
    try:
        st = load_state()
        by_repo = {}
        for t in targets:
            by_repo.setdefault(t[2], []).append(t)
        for repo_root, items in by_repo.items():
            prs = fetch_open_prs(repo_root, st, max_age)
            if prs is None:
                continue  # gh が使えないときは前の表示を残す（TTL で消える）
            for ws_id, path, _ in items:
                branch = current_branch(path)
                pr = None
                if branch:
                    pr = prs.get(branch) or lookup_closed(repo_root, branch, st)
                report(ws_id, render(pr))
        save_state(st)
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def find_by_path(path):
    top = run([GIT, "-C", path, "rev-parse", "--show-toplevel"], timeout=10)
    if not top:
        return []
    top = os.path.realpath(top.strip())
    return [t for t in worktree_workspaces() if os.path.realpath(t[1]) == top]


def find_key(obj, key):
    if isinstance(obj, dict):
        if key in obj and isinstance(obj[key], dict):
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


def cmd_event():
    try:
        ev = json.loads(os.environ.get("HERDR_PLUGIN_EVENT_JSON") or "{}")
    except ValueError:
        ev = {}
    kind = os.environ.get("HERDR_PLUGIN_EVENT", "")
    if kind == "pane.agent_status_changed":
        # agent が1ターン終えたときだけ（push・PR 作成・指摘対応はたいていターンの中で終わる）
        if find_value(ev, "agent_status") not in ("idle", "done"):
            return
        max_age = MAX_AGE_AGENT
    elif kind.startswith("worktree"):
        max_age = 0  # 開いた直後は引き直す
    else:
        max_age = MAX_AGE_FOCUS
    ws_id = find_value(ev, "workspace_id") or os.environ.get("HERDR_WORKSPACE_ID")
    if not ws_id:
        return
    targets = [t for t in worktree_workspaces() if t[0] == ws_id]
    sync(targets, max_age)


def find_value(obj, key):
    if isinstance(obj, dict):
        if isinstance(obj.get(key), str):
            return obj[key]
        for v in obj.values():
            r = find_value(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_value(v, key)
            if r is not None:
                return r
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("sync")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true")
    g.add_argument("--workspace")
    g.add_argument("--path")
    p.add_argument("--fresh", action="store_true", help="キャッシュを使わず gh を引き直す")
    p.add_argument("--max-age", type=int, default=MAX_AGE_SYNC, help="この秒数以内に取った結果があれば使う")
    sub.add_parser("event")
    args = ap.parse_args()

    if args.cmd == "event":
        cmd_event()
    elif args.cmd == "sync":
        if args.all:
            targets = worktree_workspaces()
        elif args.workspace:
            targets = [t for t in worktree_workspaces() if t[0] == args.workspace]
        else:
            targets = find_by_path(args.path)
        sync(targets, 0 if args.fresh else args.max_age)
    else:
        ap.print_help()
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
