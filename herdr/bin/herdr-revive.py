#!/usr/bin/env python3
"""herdr-revive: Herdr.app（GUI）で消えた claude / agy の会話を、同じターミナルで開き直す。

herdr-core が落ちる（Herdr の再起動・クラッシュ・再起動・ログアウト・電池切れ）と、ターミナルは同じ
アドレスのまま最後のフォルダのシェルに戻るが、元のコマンドは二度と動かない。会話 ID はターミナルの
ラベルとして残るので、それを見て打ち直す。

  - claude: claude-account の hooks が付けるラベル herdr-env.session（会話 ID）と
    herdr-env.acct_id（アカウント）から `claude -A <acct> --resume <id>`
  - agy（Antigravity CLI）: statusline（ai-usage-collect.py --record-agy）が付けるラベル
    herdr-env.agy_conversation から `agy --conversation <id>`

  herdr-revive              GUI の全ターミナルを見て、戻すべきものに打ち込む（hc send）
  herdr-revive --dry-run    戻す対象を出すだけ
  herdr-revive --line <pid> 今のターミナル用。シェルの起動直後に zsh から $$ を渡して呼ばれ、打つ行を出力する

戻す条件:
  - Herdr のスペースに属する（ラベル herdr.space）
  - 会話 ID のラベルがある。claude を /exit や Ctrl-C で終えたときは SessionEnd hook が、agy を
    終えたときは zsh の precmd がラベルを消すので対象にならない（落ちたときだけ残る）
  - ターミナルの最上位のシェルが前面にいる（何も動いていない）
  - 同じ会話がほかのターミナルで動いていない
  - 会話の記録（claude: ~/.claude*/projects/*/<id>.jsonl、agy: ~/.gemini/antigravity-cli/conversations/<id>.db）がある
  - 同じシェル（アドレス・PID）・同じ会話では一度しか試さない（失敗してもループしない）
"""
import fcntl
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
LOCK = os.path.join(STATE, "tried.lock")
AGY_CONVERSATIONS = os.path.expanduser("~/.gemini/antigravity-cli/conversations")


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


def claude_transcript(sid):
    homes = [os.path.expanduser("~/.claude")] + glob.glob(os.path.expanduser("~/.claude-*"))
    return any(glob.glob(os.path.join(h, "projects", "*", sid + ".jsonl")) for h in homes)


def agy_transcript(cid):
    return os.path.exists(os.path.join(AGY_CONVERSATIONS, cid + ".db"))


def sessions(labels):
    """ターミナルのラベルから、戻せる会話を (種類, ID, 打つ行, 記録があるか) で返す"""
    out = []
    sid = labels.get("herdr-env.session")
    if sid:
        acct = labels.get("herdr-env.acct_id") or ""
        if acct == "antigravity":  # agy の statusline が付ける値。claude のアカウントではない
            acct = ""
        parts = ["claude"] + (["-A", acct] if acct else []) + ["--resume", sid]
        out.append(("claude", sid, " ".join(shlex.quote(p) for p in parts), claude_transcript))
    cid = labels.get("herdr-env.agy_conversation")
    if cid:
        out.append(("agy", cid, "agy --conversation " + shlex.quote(cid), agy_transcript))
    return out


class Tried:
    """試した (アドレス:PID:会話) の記録。シェルが一斉に起動すると --line が同時に動くので、
    読み書きはロックの中で行う（ロック無しだと書き込みが互いに消し合う）"""

    def __enter__(self):
        os.makedirs(STATE, exist_ok=True)
        self.fd = os.open(LOCK, os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        try:
            with open(TRIED) as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = {}
        cutoff = time.time() - 7 * 86400
        self.d = {k: v for k, v in d.items() if v > cutoff}
        return self

    def __contains__(self, key):
        return key in self.d

    def add(self, key):
        self.d[key] = time.time()
        tmp = TRIED + ".tmp.%d" % os.getpid()
        with open(tmp, "w") as f:
            json.dump(self.d, f)
        os.replace(tmp, TRIED)

    def __exit__(self, *exc):
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)


def candidates(infos, tried):
    running = set()
    for i in infos:
        if not is_idle_shell(i):
            running.update(s[1] for s in sessions(i.get("labels") or {}))
    seen = set()
    for i in infos:
        labels = i.get("labels") or {}
        if not labels.get("herdr.space") or not is_idle_shell(i):
            continue
        for kind, sid, line, has_transcript in sessions(labels):
            key = "%s:%s:%s" % (i["address"], i.get("pid"), sid)
            if sid in running or sid in seen or key in tried or not has_transcript(sid):
                continue
            seen.add(sid)
            yield i, key, line
            break  # 1つのターミナルで戻すのは1つだけ


def cmd_line(shell_pid):
    """zsh の起動時: そのシェル（PID）がターミナルの最上位で、戻すべき会話があれば行を出す"""
    term = os.environ.get("HC_TERMINAL_ID")
    if not term or not shell_pid.isdigit():
        return 0
    info = hc(["get", term, "--json"])
    if not info or info.get("pid") != int(shell_pid):
        return 0
    if not sessions(info.get("labels") or {}):
        return 0
    others = [i for i in terminals() if i.get("address") != term]
    with Tried() as tried:
        for i, key, line in candidates([info] + others, tried):
            if i.get("address") != term:
                continue
            tried.add(key)
            print(line)
            break
    return 0


def cmd_all(dry):
    n = 0
    with Tried() as tried:
        for i, key, line in candidates(terminals(), tried):
            where = (i.get("labels") or {}).get("where") or i.get("cwd")
            print("%s  %s  %s" % (i["address"], where, line))
            n += 1
            if dry:
                continue
            tried.add(key)
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
