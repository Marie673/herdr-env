#!/usr/bin/env python3
"""gwt で別 space に切り出したエージェントへの「依頼」の台帳。

gwt は worktree と herdr の space を作ってエージェントを起動するが、起動した側には
「誰に何を任せたか」が何も残らない。herdr のサイドバーは状態を出せても、その agent が
どの依頼で立ち上がったのかは持っていないので、起動側から追跡しようがない。
このスクリプトが台帳を持ち、herdr の現在の状態と突き合わせて一覧にする。

台帳は 1 依頼 1 ファイル。状態は持たない（herdr が唯一の情報源）。

  gwt-tasks                      依頼の一覧（既定）
  gwt-tasks --mine               このペインから出した依頼だけ
  gwt-tasks --json               機械可読な一覧
  gwt-tasks show <agent>         1 件の詳細と、その agent の直近の出力
  gwt-tasks add --agent N ...    台帳に登録する（gwt が呼ぶ）
  gwt-tasks done <agent>...      台帳から外す（依頼が済んだ）
  gwt-tasks prune [--dry-run]    agent もワークツリーも無い依頼を外す

状態は herdr の agent_status をそのまま読む。台帳にあるのに agent が居なければ「不在」。
外部待ちの印（herdr-wait）が立っているペインは、その印を優先して出す。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import unicodedata

STATE_DIR = os.path.join(
    os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"),
    "gwt-tasks",
)
HERDR_WAIT = os.path.expanduser("~/.config/herdr/bin/herdr-wait.sh")

# herdr-activity.sh がサイドバーに出している文言に揃える。同じ状態が別の言葉で
# 出ると、サイドバーとこの一覧のどちらを信じるのか分からなくなる。
STATE_TEXT = {
    "working": "… 実行中",
    "blocked": "⚠ 要承認",
    "done": "✅ 完了",
    "idle": "待機",
    "unknown": "? 不明",
}
ABSENT_TEXT = "✗ 不在"
WAITING_TEXT = "⏳ 外部待ち"

# 一覧の並び。手当てが要るものを上に出す。
STATE_ORDER = {
    "⚠ 要承認": 0,
    "✅ 完了": 1,
    "… 実行中": 2,
    "⏳ 外部待ち": 3,
    "待機": 4,
    "? 不明": 5,
    "✗ 不在": 6,
}


# ---- 表示の幅計算（全角を 2 として数える） --------------------------------


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


def elapsed_text(seconds):
    seconds = max(0, int(seconds))
    minutes = seconds // 60
    if minutes < 60:
        return "%d分" % minutes
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return "%d時間%d分" % (hours, minutes)
    days, hours = divmod(hours, 24)
    return "%d日%d時間" % (days, hours)


# ---- 台帳 -------------------------------------------------------------------


def entry_path(agent):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", agent)
    return os.path.join(STATE_DIR, safe + ".json")


def load_entries():
    entries = []
    try:
        names = sorted(os.listdir(STATE_DIR))
    except OSError:
        return entries
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(STATE_DIR, name), encoding="utf-8") as f:
                entry = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(entry, dict) and entry.get("agent"):
            entries.append(entry)
    return entries


def save_entry(entry):
    os.makedirs(STATE_DIR, exist_ok=True)
    path = entry_path(entry["agent"])
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entry, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


# ---- herdr の現況 -----------------------------------------------------------


def herdr_json(args):
    try:
        out = subprocess.run(
            ["herdr"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout.decode("utf-8", "replace"))
    except ValueError:
        return None


def herdr_text(args):
    """read 系は JSON ではなく素のテキストを返す。"""
    try:
        out = subprocess.run(
            ["herdr"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if out.returncode != 0:
        return ""
    return out.stdout.decode("utf-8", "replace")


def live_agents():
    """herdr が今認識している agent を、名前引きとペイン引きの両方で返す。"""
    res = herdr_json(["agent", "list"]) or {}
    agents = (res.get("result") or {}).get("agents") or []
    by_name, by_pane = {}, {}
    for a in agents:
        if a.get("name"):
            by_name[a["name"]] = a
        if a.get("pane_id"):
            by_pane[a["pane_id"]] = a
    return by_name, by_pane


def wait_markers():
    """外部待ちの印が立っているペイン -> ラベル。"""
    markers = {}
    if not os.access(HERDR_WAIT, os.X_OK):
        return markers
    try:
        out = subprocess.run(
            [HERDR_WAIT, "list"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return markers
    for line in out.stdout.decode("utf-8", "replace").splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            markers[parts[0]] = parts[1]
    return markers


def last_replies(session_id, count):
    """委譲先の直近の返答を Claude Code のトランスクリプトから読む。

    ペインを読む手も herdr にはあるが、Claude Code は代替スクリーンで動くので
    流れた行は戻らない。起動側が知りたいのは「何と答えたか」なので、
    セッションのトランスクリプトを正面から読む。
    """
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", session_id or ""):
        return []
    base = os.path.expanduser("~/.claude/projects")
    path = None
    try:
        for project in os.listdir(base):
            candidate = os.path.join(base, project, session_id + ".jsonl")
            if os.path.isfile(candidate):
                path = candidate
                break
    except OSError:
        return []
    if path is None:
        return []

    texts = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or '"assistant"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("type") != "assistant" or rec.get("isSidechain"):
                    continue
                for block in (rec.get("message") or {}).get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = (block.get("text") or "").strip()
                        if text:
                            texts.append(text)
    except OSError:
        return []
    return texts[-count:]


def resolve(entry, by_name, by_pane, markers):
    """台帳の 1 件に、herdr が見ている現在の状態を貼り付ける。"""
    agent = by_name.get(entry["agent"])
    if agent is None and entry.get("pane_id"):
        # agent 名はペインの占有者が入れ替わると消える。ペインにまだ agent が
        # 居るなら、それが同じ依頼の続きだとみなす。
        agent = by_pane.get(entry["pane_id"])

    pane_id = (agent or {}).get("pane_id") or entry.get("pane_id") or ""
    if agent is None:
        state = ABSENT_TEXT
    elif pane_id in markers:
        state = WAITING_TEXT
    else:
        state = STATE_TEXT.get(agent.get("agent_status") or "", "? 不明")

    # Claude Code のセッション ID。トランスクリプトから応答を読むのに使う。
    session = (agent or {}).get("agent_session") or {}
    session_id = session.get("value") if session.get("agent") == "claude" else ""

    return {
        "agent": entry["agent"],
        "live_name": (agent or {}).get("name") or "",
        "session_id": session_id or "",
        "state": state,
        "agent_status": (agent or {}).get("agent_status") or "absent",
        "wait_label": markers.get(pane_id, ""),
        "act": ((agent or {}).get("tokens") or {}).get("act", ""),
        "context": ((agent or {}).get("tokens") or {}).get("context", ""),
        "title": (agent or {}).get("terminal_title_stripped") or "",
        "task": entry.get("task") or "",
        "branch": entry.get("branch") or "",
        "worktree": entry.get("worktree") or "",
        "workspace_id": (agent or {}).get("workspace_id") or entry.get("workspace_id") or "",
        "pane_id": pane_id,
        "requested_by_pane": entry.get("requested_by_pane") or "",
        "stored_session_id": entry.get("session_id") or "",
        "created_at": entry.get("created_at") or 0,
        "elapsed": int(time.time()) - int(entry.get("created_at") or time.time()),
    }


def collect(mine_only=False):
    by_name, by_pane = live_agents()
    markers = wait_markers()
    entries = load_entries()
    rows = [resolve(e, by_name, by_pane, markers) for e in entries]

    # セッション ID を台帳へ書き戻す。agent が居なくなった後でも
    # 「最後に何と言い残したか」をトランスクリプトから読めるようにするため。
    for entry, row in zip(entries, rows):
        if row["session_id"] and row["session_id"] != row["stored_session_id"]:
            entry["session_id"] = row["session_id"]
            try:
                save_entry(entry)
            except OSError:
                pass
        elif not row["session_id"] and row["stored_session_id"]:
            row["session_id"] = row["stored_session_id"]

    if mine_only:
        me = os.environ.get("HERDR_PANE_ID", "")
        rows = [r for r in rows if r["requested_by_pane"] == me]
    rows.sort(key=lambda r: (STATE_ORDER.get(r["state"], 9), -r["elapsed"]))
    return rows


# ---- サブコマンド -----------------------------------------------------------


def cmd_add(args):
    task = args.task or ""
    if args.task_file:
        with open(args.task_file, encoding="utf-8") as f:
            task = f.read().strip()

    path = entry_path(args.agent)
    now = int(time.time())
    created = now
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                old = json.load(f)
            # 同じ依頼の続きなら開始時刻を引き継ぐ。別の依頼を渡したなら数え直す。
            if (old.get("task") or "") == task or not task:
                created = int(old.get("created_at") or now)
                task = task or (old.get("task") or "")
        except (OSError, ValueError):
            pass

    save_entry(
        {
            "agent": args.agent,
            "branch": args.branch or "",
            "worktree": args.worktree or "",
            "workspace_id": args.workspace or "",
            "pane_id": args.pane or "",
            "task": task,
            "requested_by_pane": args.requested_by or os.environ.get("HERDR_PANE_ID", ""),
            "created_at": created,
            "updated_at": now,
        }
    )
    print("gwt-tasks: 登録 %s" % args.agent)
    return 0


def cmd_list(args):
    rows = collect(mine_only=args.mine)
    if args.json:
        json.dump(rows, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    if not rows:
        print("依頼はありません（gwt <branch> --task \"<指示>\" で登録されます）")
        return 0

    w_agent = max(16, min(32, max(width(r["agent"]) for r in rows)))
    # 状態の語彙は固定なので、一番長いものに合わせて桁を決める。
    w_state = max(width(t) for t in list(STATE_TEXT.values()) + [ABSENT_TEXT, WAITING_TEXT]) + 1
    w_task = 40
    w_elapsed = 10

    print(
        "%s %s %s %s %s"
        % (
            pad("agent", w_agent),
            pad("状態", w_state),
            pad("タスク", w_task),
            pad("経過", w_elapsed),
            "space",
        )
    )
    for r in rows:
        task = r["task"] or (r["title"] and "（%s）" % r["title"]) or "-"
        print(
            "%s %s %s %s %s"
            % (
                pad(clip(r["agent"], w_agent), w_agent),
                pad(r["state"], w_state),
                pad(clip(task, w_task), w_task),
                pad(elapsed_text(r["elapsed"]), w_elapsed),
                r["workspace_id"] or "-",
            )
        )
    return 0


def cmd_show(args):
    rows = [r for r in collect() if r["agent"] == args.agent]
    if not rows:
        sys.stderr.write("gwt-tasks: 台帳にありません: %s\n" % args.agent)
        return 1
    r = rows[0]

    print("agent      %s" % r["agent"])
    print("状態       %s" % r["state"])
    if r["wait_label"]:
        print("待ち       %s" % r["wait_label"])
    if r["act"]:
        print("いま       %s" % r["act"])
    print("経過       %s" % elapsed_text(r["elapsed"]))
    print("ブランチ   %s" % (r["branch"] or "-"))
    print("space/pane %s / %s" % (r["workspace_id"] or "-", r["pane_id"] or "-"))
    if r["context"]:
        print("context    %s" % r["context"])
    print("ワークツリー %s" % (r["worktree"] or "-"))
    print("依頼元     %s" % (r["requested_by_pane"] or "-"))
    print("\n--- 依頼 ---")
    print(r["task"] or "(なし)")

    print("\n--- 直近の返答 ---")
    replies = last_replies(r["session_id"], args.replies)
    if replies:
        print("\n\n".join(replies))
        return 0

    if r["agent_status"] == "absent":
        print("agent が居ないため読めません。")
        return 0

    # トランスクリプトが辿れないとき（claude 以外、まだ応答が無い等）はペインを読む。
    target = r["live_name"] or r["pane_id"]
    content = herdr_text(
        ["agent", "read", target, "--source", "recent-unwrapped", "--lines", str(args.lines)]
    )
    if content.strip():
        print("\n".join(line.rstrip() for line in content.rstrip().splitlines()))
    else:
        print("(まだ応答がありません)")
    return 0


def cmd_done(args):
    rc = 0
    for agent in args.agents:
        path = entry_path(agent)
        if os.path.exists(path):
            os.remove(path)
            print("gwt-tasks: 外しました %s" % agent)
        else:
            sys.stderr.write("gwt-tasks: 台帳にありません: %s\n" % agent)
            rc = 1
    return rc


def cmd_prune(args):
    removed = 0
    for r in collect():
        if r["agent_status"] != "absent":
            continue
        # ワークツリーが残っているなら、まだ作業の途中。台帳から外さない。
        if r["worktree"] and os.path.isdir(r["worktree"]):
            continue
        if args.dry_run:
            print("gwt-tasks: 外す予定 %s (%s)" % (r["agent"], r["state"]))
        else:
            os.remove(entry_path(r["agent"]))
            print("gwt-tasks: 外しました %s" % r["agent"])
        removed += 1
    if removed == 0:
        print("外すものはありません。")
    return 0


def main(argv):
    parser = argparse.ArgumentParser(prog="gwt-tasks", add_help=True)
    sub = parser.add_subparsers(dest="cmd")

    p_list = sub.add_parser("list", help="依頼の一覧")
    p_list.add_argument("--mine", action="store_true", help="このペインから出した依頼だけ")
    p_list.add_argument("--json", action="store_true")
    p_list.set_defaults(func=cmd_list)

    p_add = sub.add_parser("add", help="台帳に登録する")
    p_add.add_argument("--agent", required=True)
    p_add.add_argument("--task", default="")
    p_add.add_argument("--task-file", dest="task_file", default="")
    p_add.add_argument("--branch", default="")
    p_add.add_argument("--worktree", default="")
    p_add.add_argument("--workspace", default="")
    p_add.add_argument("--pane", default="")
    p_add.add_argument("--requested-by", dest="requested_by", default="")
    p_add.set_defaults(func=cmd_add)

    p_show = sub.add_parser("show", help="1 件の詳細と直近の出力")
    p_show.add_argument("agent")
    p_show.add_argument("--replies", type=int, default=1, help="読む直近の返答の数")
    p_show.add_argument("--lines", type=int, default=40, help="トランスクリプトが無いとき読むペインの行数")
    p_show.set_defaults(func=cmd_show)

    p_done = sub.add_parser("done", help="台帳から外す")
    p_done.add_argument("agents", nargs="+")
    p_done.set_defaults(func=cmd_done)

    p_rm = sub.add_parser("rm", help="done の別名")
    p_rm.add_argument("agents", nargs="+")
    p_rm.set_defaults(func=cmd_done)

    p_prune = sub.add_parser("prune", help="agent もワークツリーも無い依頼を外す")
    p_prune.add_argument("--dry-run", action="store_true")
    p_prune.set_defaults(func=cmd_prune)

    # サブコマンド無しは list。`gwt-tasks --mine` も通す。
    if not argv or argv[0].startswith("-") and argv[0] not in ("-h", "--help"):
        argv = ["list"] + argv

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        return cmd_list(argparse.Namespace(mine=False, json=False))
    return args.func(args)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.exit(130)
