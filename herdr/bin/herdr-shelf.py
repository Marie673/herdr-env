#!/usr/bin/env python3
"""使い終わった agent のセッションを「棚」にしまい、あとで同じ会話のまま開き直す。

相談だけのセッションが 1 つの space に 10 体以上溜まり、サイドバーが埋まる。
長く触っていない agent は、session ID と題名を棚に記録してペインを閉じる。
会話は Claude Code / Codex の記録に残っているので、棚から `--resume` で戻せる。

  herdr-shelf candidates [--hours 24]      しまう候補を出す（何もしない）
  herdr-shelf shelve [--hours 24] [--dry-run]  候補をまとめてしまう（launchd から 1 時間ごと）
  herdr-shelf shelve --pane <pane_id>      このペインを今すぐしまう（キー prefix+shift+z）
  herdr-shelf shelve --terminal <t>        GUI（Herdr.app）のターミナルを今すぐしまう（キー cmd+shift+z）
  herdr-shelf list                         棚の一覧
  herdr-shelf restore [<番号|session ID>]  棚から開き直す（引数なしは fzf で選ぶ。キー prefix+shift+s）
  herdr-shelf forget <番号|session ID>     棚から外す（会話の記録は消さない）

しまう候補（まとめてしまうとき）:
  - claude / codex で、状態が idle（見終わった完了）。done（未確認の完了）・working・blocked・unknown は除く
  - focus されていない、外部待ちの印（herdr-wait）が無い
  - gwt-tasks の依頼で任された agent ではない／任せた先が生きている agent ではない
  - 会話の記録の最終更新から --hours 時間（既定 24）以上たっている
しまい方: space に他のペインがあればペインを閉じる。space の最後のペインなら agent だけ終了してシェルを残す。

Herdr の GUI（Herdr.app）の agent も同じ条件で対象にする（TUI と GUI のどちらか片方だけ動いていてもよい）。
GUI の会話 ID は claude-account が付ける hc のラベル herdr-env.session（無ければ起動引数の --resume）から取る。
restore は、GUI のターミナルから呼べば GUI に（キー cmd+shift+s）、TUI のペインから呼べば TUI に開く。
"""
import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import time
import unicodedata

HOME = os.path.expanduser("~")
STATE = os.environ.get("XDG_STATE_HOME") or os.path.join(HOME, ".local/state")
SHELF_DIR = os.path.join(STATE, "herdr-shelf")
LEDGER_DIR = os.path.join(STATE, "gwt-tasks")
HERDR_WAIT = os.path.join(HOME, ".config/herdr/bin/herdr-wait.sh")
os.environ["PATH"] = "/opt/homebrew/bin:%s/.local/bin:/usr/local/bin:/usr/bin:/bin:%s" % (
    HOME, os.environ.get("PATH", ""))


def width(text):
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def clip(text, cols):
    if width(text) <= cols:
        return text
    out, used = "", 0
    for c in text:
        w = 2 if unicodedata.east_asian_width(c) in "WF" else 1
        if used + w > cols - 1:
            break
        out += c
        used += w
    return out + "…"


def pad(text, cols):
    return text + " " * max(0, cols - width(text))


def ago(seconds):
    h = int(seconds // 3600)
    return "%d日%d時間" % divmod(h, 24) if h >= 24 else "%d時間" % h


def herdr(args, check=True):
    if not shutil.which("herdr"):
        if check:
            raise RuntimeError("herdr がありません")
        return {}
    out = subprocess.run(["herdr"] + args, capture_output=True, text=True)
    if check and out.returncode != 0:
        raise RuntimeError("herdr %s: %s" % (" ".join(args), (out.stderr or out.stdout).strip()))
    try:
        return json.loads(out.stdout) if out.stdout.strip() else {}
    except ValueError:
        return {}


# ---- 材料 -------------------------------------------------------------------


def agents():
    """TUI と GUI の agent。TUI のサーバが止まっていれば GUI だけ"""
    tui = [a for a in herdr(["agent", "list"], check=False).get("result", {}).get("agents", []) if a.get("agent")]
    return tui + gui_agents()


def workspaces():
    ws = {w["workspace_id"]: w for w in herdr(["workspace", "list"], check=False).get("result", {}).get("workspaces", [])}
    for sp in gui_ls().get("spaces", []):
        ws["gui:" + sp["id"]] = {"workspace_id": "gui:" + sp["id"], "label": sp.get("name") or "", "folder": sp.get("folder")}
    return ws


# ---- GUI（Herdr.app。ペインは herdr-core の hc ターミナル） ----------------------------
# GUI の agent は TUI と同じ形の dict にそろえる。pane_id は "gui:<terminal>"、workspace_id は "gui:<space id>"


def gui_active():
    return bool(shutil.which("herdr-app") and shutil.which("hc"))


def gui_json(cmd):
    out = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return json.loads(out.stdout) if out.stdout.strip() else {}
    except ValueError:
        return {}


def gui_ls():
    return gui_json(["herdr-app", "ls"]) if gui_active() else {}


def gui_do(cmd, args):
    out = subprocess.run(["herdr-app", "do", cmd, json.dumps(args, ensure_ascii=False)], capture_output=True, text=True)
    try:
        res = json.loads(out.stdout) if out.stdout.strip() else {}
    except ValueError:
        res = {}
    if out.returncode != 0 or "error" in res:
        raise RuntimeError("herdr-app do %s: %s" % (cmd, res.get("error") or (out.stderr or out.stdout).strip()))
    return res


def gui_agents():
    ls = gui_ls()
    if not ls:
        return []
    focused = (gui_json(["herdr-app", "status"]).get("focused_pane") or {})
    focused_pane = focused.get("id") if isinstance(focused, dict) else focused
    now = time.time()
    out = []
    for sp in ls.get("spaces", []):
        n_terms = sum(len(tab.get("terminals", [])) for tab in sp.get("tabs", []))
        for tab in sp.get("tabs", []):
            for x in tab.get("terminals", []):
                if x.get("app") not in ("claude-code", "codex"):
                    continue
                info = gui_json(["hc", "get", x["terminal"], "--json"])
                l = info.get("labels") or {}
                fg = info.get("foreground") or {}
                argv = fg.get("argv") or []
                sid = l.get("herdr-env.session")
                if not sid and "--resume" in argv[:-1]:
                    sid = argv[argv.index("--resume") + 1]
                try:
                    waiting = bool(l.get("herdr-env.wait")) and now - int(l.get("herdr-env.since") or 0) < 6 * 3600
                except ValueError:
                    waiting = False
                out.append({
                    "agent": "claude" if x["app"] == "claude-code" else "codex",
                    "agent_session": {"value": sid} if sid else None,
                    "agent_status": x.get("status") or "unknown",
                    "focused": bool(focused_pane) and x.get("pane") == focused_pane,
                    "pane_id": "gui:" + x["terminal"],
                    "gui_pane": x.get("pane"),
                    "gui_last": n_terms <= 1,
                    "gui_waiting": waiting,
                    "workspace_id": "gui:" + sp["id"],
                    "name": "",
                    "terminal_title_stripped": info.get("title") or "",
                    "foreground_cwd": fg.get("cwd") or info.get("cwd"),
                    "tokens": {},
                })
    return out


def gui_space_for(cwd):
    """cwd をフォルダにしている GUI のスペース id（無ければ None）"""
    real = os.path.realpath(cwd or "")
    for sp in gui_ls().get("spaces", []):
        if sp.get("folder") and os.path.realpath(sp["folder"]) == real and not sp.get("machine"):
            return sp["id"]
    return None


def notify(title, body):
    out = subprocess.run(["herdr", "notification", "show", title, "--body", body, "--sound", "none"],
                         capture_output=True) if shutil.which("herdr") else None
    if out is None or out.returncode != 0:
        script = "display notification %s with title %s" % (json.dumps(body), json.dumps(title))
        subprocess.run(["osascript", "-e", script], capture_output=True)


def transcript(agent, session_id):
    if not session_id:
        return None
    if agent == "claude":
        hits = glob.glob(os.path.join(HOME, ".claude/projects/*/%s.jsonl" % session_id))
    elif agent == "codex":
        hits = glob.glob(os.path.join(HOME, ".codex/sessions/*/*/*/*%s.jsonl" % session_id))
    else:
        hits = []
    return max(hits, key=os.path.getmtime) if hits else None


def ledger_links():
    """(任された agent の名前と pane, 依頼元の pane)"""
    delegatees, requesters = set(), set()
    for path in glob.glob(os.path.join(LEDGER_DIR, "*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                e = json.load(f)
        except (OSError, ValueError):
            continue
        delegatees.add(e.get("agent") or "")
        delegatees.add(e.get("pane_id") or "")
        if e.get("requested_by_pane"):
            requesters.add((e["requested_by_pane"], e.get("agent") or ""))
    return delegatees, requesters


def wait_marked():
    out = subprocess.run(["sh", HERDR_WAIT, "list"], capture_output=True, text=True)
    return {line.split("\t")[0] for line in out.stdout.splitlines() if line.strip()}


def candidates(hours):
    live = agents()
    names = {a.get("name") for a in live if a.get("name")}
    delegatees, requesters = ledger_links()
    marked = wait_marked()
    now = time.time()
    out = []
    for a in live:
        why = None
        sid = (a.get("agent_session") or {}).get("value")
        if a["agent"] not in ("claude", "codex"):
            why = "対象外の agent"
        elif a.get("agent_status") != "idle":
            why = "状態 %s" % a.get("agent_status")
        elif a.get("focused"):
            why = "表示中"
        elif a["pane_id"] in marked or a.get("gui_waiting"):
            why = "外部待ち"
        elif (a.get("name") and a["name"] in delegatees) or a["pane_id"] in delegatees:
            why = "任された作業"
        elif any(p == a["pane_id"] and child in names for p, child in requesters):
            why = "任せた先が生きている"
        path = transcript(a["agent"], sid)
        idle_for = now - os.path.getmtime(path) if path else 0
        if why is None and not path:
            why = "会話の記録が見つからない"
        if why is None and idle_for < hours * 3600:
            why = "最終更新 %s前" % ago(idle_for)
        out.append((a, sid, idle_for, why))
    return out


# ---- 棚 ---------------------------------------------------------------------


def shelf_entries():
    entries = []
    for path in glob.glob(os.path.join(SHELF_DIR, "*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                e = json.load(f)
            e["_path"] = path
            entries.append(e)
        except (OSError, ValueError):
            continue
    entries.sort(key=lambda e: e.get("shelved_at", 0), reverse=True)
    return entries


def title_of(a):
    t = (a.get("terminal_title_stripped") or "").strip()
    if not t or t.lower() == os.path.basename(HOME).lower():
        t = (a.get("tokens") or {}).get("title") or ""
    return t or "%s %s" % (a["agent"], os.path.basename(a.get("foreground_cwd") or a.get("cwd") or ""))


def shelve_one(a, sid, ws_all, dry_run=False):
    ws = ws_all.get(a["workspace_id"], {})
    entry = {
        "session_id": sid,
        "agent": a["agent"],
        "name": a.get("name") or "",
        "title": title_of(a),
        "cwd": a.get("foreground_cwd") or a.get("cwd") or HOME,
        "workspace_id": a["workspace_id"],
        "workspace_label": ws.get("label") or "",
        "ui": "gui" if a["pane_id"].startswith("gui:") else "tui",
        "shelved_at": int(time.time()),
    }
    if dry_run:
        print("[dry-run] %s  %s" % (a["pane_id"], entry["title"]))
        return True
    os.makedirs(SHELF_DIR, exist_ok=True)
    path = os.path.join(SHELF_DIR, "%d-%s.json" % (entry["shelved_at"], sid))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entry, f, ensure_ascii=False, indent=2)
        f.write("\n")
    if a["pane_id"].startswith("gui:"):
        term = a["pane_id"][4:]
        if not a.get("gui_last") and a.get("gui_pane"):
            gui_do("pane.close", {"pane": a["gui_pane"]})
            how = "ペインを閉じました"
        else:
            # HC_TERMINAL_ID を外す（付いていると <hc> の札付きのメッセージになり、/exit が効かない）
            env = {k: v for k, v in os.environ.items() if k != "HC_TERMINAL_ID"}
            subprocess.run(["hc", "send", term, "/exit" if a["agent"] == "claude" else "/quit"], capture_output=True, env=env)
            how = "agent を終了しました（シェルは残しています）"
        print("しまいました: %s（%s / %s）" % (entry["title"], entry["workspace_label"] or a["workspace_id"], how))
        return True
    panes = herdr(["pane", "list", "--workspace", a["workspace_id"]], check=False).get("result", {}).get("panes", [])
    if len(panes) > 1:
        herdr(["pane", "close", a["pane_id"]])
        how = "ペインを閉じました"
    else:
        # space の最後のペインは閉じると space ごと消えるので、agent だけ終える
        herdr(["pane", "send-text", a["pane_id"], "/exit" if a["agent"] == "claude" else "/quit"])
        herdr(["pane", "send-keys", a["pane_id"], "enter"])
        how = "agent を終了しました（シェルは残しています）"
    print("しまいました: %s（%s / %s）" % (entry["title"], entry["workspace_label"] or a["workspace_id"], how))
    return True


def cmd_candidates(args):
    rows = candidates(args.hours)
    for a, sid, idle_for, why in sorted(rows, key=lambda r: (r[3] is not None, -r[2])):
        mark = "○ しまう" if why is None else "  残す  "
        print("%s %-9s %s %s %s" % (mark, a["pane_id"], pad(clip(title_of(a), 34), 34),
                                    pad(ago(idle_for), 8), why or ""))
    return 0


def cmd_shelve(args):
    ws_all = workspaces()
    if args.pane or args.terminal:
        live = {a["pane_id"]: a for a in agents()}
        key = args.pane or "gui:" + args.terminal
        a = live.get(key)
        if not a:
            print("herdr-shelf: %s に agent が居ません" % key, file=sys.stderr)
            return 1
        sid = (a.get("agent_session") or {}).get("value")
        if not sid or a["agent"] not in ("claude", "codex"):
            print("herdr-shelf: この agent は session ID が分からないのでしまえません", file=sys.stderr)
            return 1
        shelve_one(a, sid, ws_all, args.dry_run)
        return 0
    n = 0
    for a, sid, idle_for, why in candidates(args.hours):
        if why is None:
            try:
                shelve_one(a, sid, ws_all, args.dry_run)
                n += 1
            except RuntimeError as exc:
                print("失敗: %s: %s" % (a["pane_id"], exc), file=sys.stderr)
    if n and not args.dry_run:
        notify("%d 件のセッションを棚にしまいました" % n, "prefix+shift+s（GUI は cmd+shift+s）で開き直せます")
    if n == 0:
        print("しまう候補はありません。")
    return 0


def cmd_list(args):
    entries = shelf_entries()
    if not entries:
        print("棚は空です。")
        return 0
    now = time.time()
    for i, e in enumerate(entries, 1):
        print("%3d  %-6s %s %s %s前" % (
            i, e.get("agent", ""), pad(clip(e.get("title", ""), 40), 40),
            pad(clip(e.get("workspace_label") or os.path.basename(e.get("cwd", "")), 22), 22),
            ago(now - e.get("shelved_at", now))))
    return 0


def pick(entries, key):
    if key is None:
        if not shutil.which("fzf"):
            cmd_list(None)
            return None
        lines = ["%d\t%s\t%s\t%s" % (i, e.get("agent"), e.get("title"), e.get("workspace_label") or e.get("cwd"))
                 for i, e in enumerate(entries, 1)]
        out = subprocess.run(["fzf", "--delimiter=\t", "--with-nth=2,3,4", "--header=棚から開き直すセッション"],
                             input="\n".join(lines), capture_output=True, text=True)
        if out.returncode != 0 or not out.stdout.strip():
            return None
        key = out.stdout.split("\t", 1)[0]
    if key.isdigit() and 1 <= int(key) <= len(entries):
        return entries[int(key) - 1]
    for e in entries:
        if e.get("session_id", "").startswith(key):
            return e
    print("herdr-shelf: 見つかりません: %s" % key, file=sys.stderr)
    return None


def unique_name(base, taken):
    name = "".join(c if c.isalnum() or c in "-_" else "-" for c in (base or "").lower())
    if not name or not name[0].isalpha():
        name = "r-" + name
    name = name[:28]
    cand, i = name, 2
    while cand in taken:
        cand, i = "%s-%d" % (name[:26], i), i + 1
    return cand


def cmd_restore(args):
    entries = shelf_entries()
    if not entries:
        print("棚は空です。")
        return 0
    e = pick(entries, args.key)
    if not e:
        return 1
    ws_all = workspaces()
    cwd = e.get("cwd") if os.path.isdir(e.get("cwd") or "") else HOME
    label = clip(e.get("title", ""), 24)
    # GUI のターミナルから呼ばれたら GUI に、TUI のペインからなら TUI に。どちらでもなければしまった側
    if os.environ.get("HC_TERMINAL_ID") and os.environ.get("HERDR_ENV") != "1":
        to_gui = True
    elif os.environ.get("HERDR_ENV") == "1":
        to_gui = False
    else:
        to_gui = e.get("ui") == "gui" and gui_active()
    if to_gui:
        return restore_gui(e, cwd, label)
    # しまったときと同じ Claude のアカウントで開き直す（claude-account。会話の記録は共有なので
    # 別アカウントでも --resume できるが、上限やログイン先を勝手に変えない）
    env = []
    if e["agent"] == "claude" and shutil.which("claude-account"):
        acct = subprocess.run(["claude-account", "of-session", e["session_id"]], capture_output=True, text=True)
        if acct.returncode == 0 and acct.stdout.strip():
            d = subprocess.run(["claude-account", "dir", acct.stdout.strip()], capture_output=True, text=True)
            if d.returncode == 0 and d.stdout.strip():
                env = ["--env", "CLAUDE_CONFIG_DIR=%s" % d.stdout.strip()]
    if e.get("workspace_id") in ws_all:
        res = herdr(["tab", "create", "--workspace", e["workspace_id"], "--cwd", cwd, "--label", label, "--focus"] + env)
    else:
        res = herdr(["workspace", "create", "--cwd", cwd, "--label",
                     e.get("workspace_label") or os.path.basename(cwd), "--focus"] + env)
    pane = res["result"]["root_pane"]["pane_id"]
    taken = {a.get("name") for a in agents() if a.get("name")}
    name = unique_name(e.get("name") or "r-" + e["session_id"][:8], taken)
    native = ["--resume", e["session_id"]] if e["agent"] == "claude" else ["resume", e["session_id"]]
    out = subprocess.run(["herdr", "agent", "start", name, "--kind", e["agent"], "--pane", pane, "--"] + native,
                         capture_output=True, text=True)
    if out.returncode != 0 and "agent_not_ready" not in out.stdout + out.stderr:
        print("herdr-shelf: 起動に失敗しました: %s" % (out.stderr or out.stdout).strip(), file=sys.stderr)
        return 1
    os.remove(e["_path"])
    print("開き直しました: %s（agent %s）" % (e.get("title"), name))
    return 0


def restore_gui(e, cwd, label):
    acct = ""
    if e["agent"] == "claude" and shutil.which("claude-account"):
        r = subprocess.run(["claude-account", "of-session", e["session_id"]], capture_output=True, text=True)
        acct = r.stdout.strip() if r.returncode == 0 else ""
    q = lambda v: "'" + v.replace("'", "'\\''") + "'"
    if e["agent"] == "claude":
        line = "claude %s--resume %s" % ("-A %s " % q(acct) if acct else "", q(e["session_id"]))
    else:
        line = "codex resume %s" % q(e["session_id"])
    req = {"name": label, "folder": cwd, "argv": ["/bin/zsh", "-lic", "cd %s && %s; exec /bin/zsh -l" % (q(cwd), line)]}
    wid = e.get("workspace_id") or ""
    space = wid[4:] if wid.startswith("gui:") and wid in workspaces() else gui_space_for(cwd)
    if space:
        req["space"] = space
        gui_do("tab.new", req)
    else:
        req["name"] = e.get("workspace_label") or os.path.basename(cwd)
        req["tab"] = label
        gui_do("space.new", req)
    os.remove(e["_path"])
    print("開き直しました: %s" % e.get("title"))
    return 0


def cmd_forget(args):
    e = pick(shelf_entries(), args.key)
    if not e:
        return 1
    os.remove(e["_path"])
    print("棚から外しました: %s" % e.get("title"))
    return 0


def main(argv):
    p = argparse.ArgumentParser(prog="herdr-shelf", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd")
    c = sub.add_parser("candidates")
    c.add_argument("--hours", type=float, default=24)
    c.set_defaults(func=cmd_candidates)
    s = sub.add_parser("shelve")
    s.add_argument("--hours", type=float, default=24)
    s.add_argument("--pane")
    s.add_argument("--terminal")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_shelve)
    ls = sub.add_parser("list")
    ls.set_defaults(func=cmd_list)
    r = sub.add_parser("restore")
    r.add_argument("key", nargs="?")
    r.set_defaults(func=cmd_restore)
    f = sub.add_parser("forget")
    f.add_argument("key")
    f.set_defaults(func=cmd_forget)
    args = p.parse_args(argv[1:])
    if not getattr(args, "func", None):
        p.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
