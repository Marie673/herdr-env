#!/usr/bin/env python3
"""herdr-revive: Herdr.app（GUI）で消えた claude の会話を、同じターミナルで --resume し直す。

herdr-core が落ちる（Herdr の再起動・クラッシュ・再起動・ログアウト）と、ターミナルは同じアドレスのまま
最後のフォルダのシェルに戻るが、元のコマンド（claude）は二度と動かない。claude-account の hooks が
付けたラベル herdr-env.session（会話 ID）と herdr-env.acct_id（アカウント）はラベルとして残るので、
それを見て `claude -A <acct> --resume <id>` を打ち直す。

  herdr-revive              GUI の全ターミナルを見て、戻すべきものに打ち込む（hc send）
  herdr-revive --dry-run    戻す対象を出すだけ
  herdr-revive --line <pid> 今のターミナル用。シェルの起動直後に zsh から $$ を渡して呼ばれ、打つ行を出力する

戻す条件:
  - Herdr のスペースに属する（ラベル herdr.space）
  - ラベル herdr-env.session がある。/exit や Ctrl-C で正常に終わった会話は SessionEnd hook が
    ラベルを消すので対象にならない（落ちたときだけ残る）
  - ターミナルの最上位のシェルが前面にいる（何も動いていない）
  - 同じ会話がほかのターミナルで動いていない
  - 会話の記録（~/.claude/projects/*/<id>.jsonl）がある
  - 同じシェル（アドレス・PID）・同じ会話では一度しか試さない（失敗してもループしない）
"""
import glob
import json
import os
import shlex
import subprocess
import sys
import time

SHELLS = {"zsh", "bash", "sh", "fish", "-zsh", "-bash"}
STATE = os.path.expanduser("~/.local/state/herdr-revive")
TRIED = os.path.join(STATE, "tried.json")


def hc(args):
    env = dict(os.environ)
    env.pop("HC_TERMINAL_ID", None)  # 付いていると hc send が <hc from=…> の札を付ける
    r = subprocess.run(["hc"] + args, capture_output=True, text=True, env=env)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout) if r.stdout.strip() else {}
    except ValueError:
        return {}


def terminals():
    ls = hc(["ls", "--json"]) or {}
    out = []
    for t in ls.get("terminals", []):
        info = hc(["get", t["address"], "--json"])
        if info and info.get("state") == "live":
            out.append(info)
    return out


def is_idle_shell(info):
    fg = info.get("foreground") or {}
    name = os.path.basename((fg.get("argv") or [fg.get("name") or ""])[0])
    return name in SHELLS and fg.get("pid") == info.get("pid")


def has_transcript(sid):
    homes = [os.path.expanduser("~/.claude")] + glob.glob(os.path.expanduser("~/.claude-*"))
    return any(glob.glob(os.path.join(h, "projects", "*", sid + ".jsonl")) for h in homes)


def load_tried():
    try:
        with open(TRIED) as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    cutoff = time.time() - 7 * 86400
    return {k: v for k, v in d.items() if v > cutoff}


def save_tried(d):
    os.makedirs(STATE, exist_ok=True)
    tmp = TRIED + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f)
    os.replace(tmp, TRIED)


def command_line(labels):
    sid = labels["herdr-env.session"]
    acct = labels.get("herdr-env.acct_id") or ""
    parts = ["claude"] + (["-A", acct] if acct else []) + ["--resume", sid]
    return " ".join(shlex.quote(p) for p in parts)


def candidates(infos, tried):
    running = set()
    for i in infos:
        sid = (i.get("labels") or {}).get("herdr-env.session")
        if sid and not is_idle_shell(i):
            running.add(sid)
    seen = set()
    for i in infos:
        labels = i.get("labels") or {}
        sid = labels.get("herdr-env.session")
        if not sid or not labels.get("herdr.space") or not is_idle_shell(i):
            continue
        key = "%s:%s:%s" % (i["address"], i.get("pid"), sid)
        if sid in running or sid in seen or key in tried or not has_transcript(sid):
            continue
        seen.add(sid)
        yield i, key


def cmd_line(shell_pid):
    """zsh の起動時: そのシェル（PID）がターミナルの最上位で、戻すべき会話があれば行を出す"""
    term = os.environ.get("HC_TERMINAL_ID")
    if not term or not shell_pid.isdigit():
        return 0
    info = hc(["get", term, "--json"])
    if not info or info.get("pid") != int(shell_pid):
        return 0
    others = [i for i in terminals() if i.get("address") != term] if (info.get("labels") or {}).get("herdr-env.session") else []
    tried = load_tried()
    for i, key in candidates([info] + others, tried):
        if i.get("address") != term:
            continue
        tried[key] = time.time()
        save_tried(tried)
        print(command_line(i["labels"]))
    return 0


def cmd_all(dry):
    tried = load_tried()
    n = 0
    for i, key in candidates(terminals(), tried):
        line = command_line(i["labels"])
        where = (i.get("labels") or {}).get("where") or i.get("cwd")
        print("%s  %s  %s" % (i["address"], where, line))
        n += 1
        if dry:
            continue
        tried[key] = time.time()
        save_tried(tried)
        hc(["send", i["address"], line])
    if not n:
        print("戻す会話はありません")
    return 0


def main(argv):
    if "-h" in argv or "--help" in argv:
        print(__doc__)
        return 0
    if "--line" in argv:
        rest = argv[argv.index("--line") + 1:]
        return cmd_line(rest[0] if rest else "")
    return cmd_all("--dry-run" in argv or "-n" in argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
