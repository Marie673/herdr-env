#!/usr/bin/python3
"""Claude Code の複数アカウントを herdr 上で使い分ける。

アカウントは CLAUDE_CONFIG_DIR で分ける（T3 Code の provider instance と同じ考え方）。
HOME は変えない。macOS では Claude Code のログイン情報は Keychain にあり、項目名が
CLAUDE_CONFIG_DIR ごとに変わるので、ディレクトリを分けるだけでログインが分かれる。

既定のアカウントは ~/.claude のまま CLAUDE_CONFIG_DIR を設定しない。追加のアカウントの
ディレクトリは「ログイン情報と .claude.json だけ自分のもの、残りは ~/.claude への
symlink」にする（Codex の shadow home と同じ）。会話の記録も共有するので、上限に
達したら同じ会話のまま別アカウントで続けられる（switch）。

  claude-account [list] [--mark <id>]       一覧（既定・ログイン状態。* は今のアカウントか --mark）
  claude-account init [<id>|--all]          追加アカウントのディレクトリを作る／symlink を直す
  claude-account login <id> [args…]         そのアカウントで `claude auth login`
  claude-account status <id>                そのアカウントで `claude auth status`
  claude-account env <id>                   シェルに eval する export / unset を出す
  claude-account exec <id> [claude の引数…]  そのアカウントで claude を起動する
  claude-account dir <id>                   CLAUDE_CONFIG_DIR に入れる値（既定は空）
  claude-account current                    この環境のアカウント id
  claude-account start <id> [--pane P] [--cwd D] [-- claude の引数…]
                                            herdr: 新しいタブでそのアカウントの claude を起動
  claude-account switch <id> [--pane P] [--force]
                                            herdr: ペインの会話を、同じ会話のまま別アカウントで開き直す
  claude-account of-session <session_id>    そのセッションを最後に動かしたアカウント id
  claude-account hook <session-start|session-end|statusline>   Claude Code の hooks / statusLine から呼ぶ
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
CONF = os.environ.get("CLAUDE_ACCOUNTS_FILE") or os.path.join(HOME, ".config/claude-accounts/accounts")
STATE = os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.join(HOME, ".local/state"), "claude-accounts")
os.environ["PATH"] = "%s/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:%s" % (
    HOME, os.environ.get("PATH", ""))

# 追加アカウントのディレクトリで ~/.claude と共有するもの。
# ここに無いもの（.claude.json、policy-limits、remote-settings、daemon、sessions、
# herdr-usagebar の集計など）はアカウントごとに持つ。分からないものは共有しない。
SHARED_DIRS = [
    "projects", "file-history", "todos", "plans", "shell-snapshots", "session-env",
    "paste-cache", "tasks", "teams", "hooks", "skills", "plugins", "agents", "commands",
    "output-styles",
]
SHARED_FILES = ["settings.json", "CLAUDE.md", "statusline.sh", "history.jsonl", "keybindings.json"]
# .claude.json のうち、追加アカウントを作るときに既定から写すキー（ログイン情報は写さない）
COPY_KEYS = ["mcpServers", "theme", "editorMode"]
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


def die(msg, code=1):
    print("claude-account: %s" % msg, file=sys.stderr)
    sys.exit(code)


# ---- アカウントの定義 -----------------------------------------------------------


class Account(object):
    def __init__(self, id, config_dir, badge, label, default):
        self.id, self.config_dir, self.badge, self.label, self.default = id, config_dir, badge, label, default

    @property
    def path(self):
        return os.path.realpath(os.path.expanduser(self.config_dir))

    @property
    def claude_json(self):
        # 既定（CLAUDE_CONFIG_DIR 無し）は ~/.claude.json、それ以外は <dir>/.claude.json
        return os.path.join(HOME, ".claude.json") if self.default else os.path.join(self.path, ".claude.json")

    @property
    def display(self):
        return ("%s %s" % (self.badge, self.label)).strip()

    def env(self):
        """子プロセスに渡す環境。既定は CLAUDE_CONFIG_DIR を外す。"""
        env = dict(os.environ)
        env.pop("CLAUDE_CONFIG_DIR", None)
        if not self.default:
            env["CLAUDE_CONFIG_DIR"] = self.path
        return env

    def oauth(self):
        try:
            with open(self.claude_json, encoding="utf-8") as f:
                return json.load(f).get("oauthAccount") or {}
        except (OSError, ValueError):
            return {}


def accounts():
    if not os.path.isfile(CONF):
        die("アカウントの定義がありません: %s" % CONF)
    out = []
    with open(CONF, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 3)
            if len(parts) < 2 or not ID_RE.match(parts[0]):
                die("%s:%d が読めません: %s" % (CONF, n, line))
            badge = parts[2] if len(parts) > 2 else ""
            label = parts[3] if len(parts) > 3 else parts[0]
            out.append(Account(parts[0], parts[1], badge, label, default=not out))
    if not out:
        die("アカウントが1つも定義されていません: %s" % CONF)
    if out[0].path != os.path.realpath(os.path.join(HOME, ".claude")):
        die("最初の行（既定）は ~/.claude にしてください")
    return out


def find(acct_id):
    for a in accounts():
        if a.id == acct_id:
            return a
    die("アカウント '%s' がありません（%s）" % (acct_id, ", ".join(a.id for a in accounts())))


def from_config_dir(config_dir):
    """CLAUDE_CONFIG_DIR の値からアカウントを引く。未設定は既定。"""
    accts = accounts()
    if not config_dir:
        return accts[0]
    real = os.path.realpath(os.path.expanduser(config_dir))
    for a in accts:
        if a.path == real:
            return a
    return None


# ---- 状態（どのセッションをどのアカウントで動かしたか） --------------------------------


def remember_session(session_id, acct):
    if not re.fullmatch(r"[0-9A-Za-z-]{8,64}", session_id or ""):
        return
    d = os.path.join(STATE, "sessions")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, session_id), "w") as f:
        f.write(acct.id + "\n")


def session_account(session_id):
    try:
        with open(os.path.join(STATE, "sessions", session_id)) as f:
            return f.read().strip() or None
    except (OSError, TypeError):
        return None


# ---- init ---------------------------------------------------------------------


def link(src, dst):
    """dst を src への symlink にする。既に実体があれば触らずに知らせる。"""
    if os.path.islink(dst):
        if os.path.realpath(dst) == os.path.realpath(src):
            return "ok"
        os.remove(dst)
    elif os.path.exists(dst):
        return "conflict"
    os.symlink(src, dst)
    return "linked"


def init_account(a, quiet=False):
    if a.default:
        return
    base = accounts()[0].path
    os.makedirs(a.path, exist_ok=True)
    problems = []
    for name in SHARED_DIRS:
        src = os.path.join(base, name)
        os.makedirs(src, exist_ok=True)  # 既定側に無ければ作ってから共有する
        if link(src, os.path.join(a.path, name)) == "conflict":
            problems.append(name)
    for name in SHARED_FILES:
        src = os.path.join(base, name)
        if not os.path.exists(src):
            if name != "history.jsonl":
                continue
            open(src, "a").close()
        if link(src, os.path.join(a.path, name)) == "conflict":
            problems.append(name)
    seed_claude_json(a)
    if problems and not quiet:
        print("  %s: 実体があるので共有にしていません: %s" % (a.id, ", ".join(problems)), file=sys.stderr)


def seed_claude_json(a):
    """新しいアカウントの .claude.json に、既定の MCP サーバーとフォルダの信頼を写す。"""
    if os.path.exists(a.claude_json):
        return
    try:
        with open(accounts()[0].claude_json, encoding="utf-8") as f:
            base = json.load(f)
    except (OSError, ValueError):
        base = {}
    seed = {k: base[k] for k in COPY_KEYS if k in base}
    trusted = {p: {"hasTrustDialogAccepted": True}
               for p, v in (base.get("projects") or {}).items()
               if isinstance(v, dict) and v.get("hasTrustDialogAccepted")}
    if trusted:
        seed["projects"] = trusted
    with open(a.claude_json, "w", encoding="utf-8") as f:
        json.dump(seed, f, ensure_ascii=False, indent=2)
    os.chmod(a.claude_json, 0o600)


# ---- herdr --------------------------------------------------------------------


def herdr(args, check=True):
    out = subprocess.run(["herdr"] + args, capture_output=True, text=True)
    if check and out.returncode != 0:
        die("herdr %s: %s" % (" ".join(args[:3]), (out.stderr or out.stdout).strip()))
    try:
        return json.loads(out.stdout) if out.stdout.strip() else {}
    except ValueError:
        return {}


def agent_in_pane(pane):
    for a in herdr(["agent", "list"]).get("result", {}).get("agents", []):
        if a.get("pane_id") == pane and a.get("agent"):
            return a
    return None


def taken_names():
    return {a.get("name") for a in herdr(["agent", "list"]).get("result", {}).get("agents", []) if a.get("name")}


def new_name(prefix, taken=None):
    taken = taken_names() if taken is None else taken
    prefix = re.sub(r"[^a-z0-9_-]", "-", prefix.lower())[:28]
    n = 1
    while "%s-%d" % (prefix, n) in taken:
        n += 1
    return "%s-%d" % (prefix, n)


def start_agent(name, pane, args):
    out = subprocess.run(["herdr", "agent", "start", name, "--kind", "claude", "--pane", pane, "--"] + args,
                         capture_output=True, text=True)
    text = out.stdout + out.stderr
    if out.returncode != 0 and "agent_not_ready" not in text:
        die("起動に失敗しました: %s" % text.strip())
    return "agent_not_ready" not in text


def notify(title, body=""):
    subprocess.run(["herdr", "notification", "show", title, "--body", body, "--sound", "none"],
                   capture_output=True)


def pad(text, cols):
    import unicodedata
    w = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)
    return text + " " * max(1, cols - w)


# ---- コマンド --------------------------------------------------------------------


def cmd_list(argv):
    opts, _ = parse_opts(argv, {"mark": True})
    here = find(opts["mark"]) if opts.get("mark") else from_config_dir(os.environ.get("CLAUDE_CONFIG_DIR"))
    for a in accounts():
        o = a.oauth()
        who = "%s / %s" % (o.get("emailAddress", "?"), o.get("organizationName", "?")) if o else "未ログイン"
        plan = (o.get("organizationType") or o.get("billingType") or "") if o else ""
        mark = "*" if here and here.id == a.id else " "
        print("%s %s %s %s %s %s" % (mark, pad(a.id, 10), pad(a.display, 14),
                                     pad(a.config_dir + (" (既定)" if a.default else ""), 24),
                                     who, ("[%s]" % plan) if plan else ""))


def cmd_init(argv):
    targets = accounts()[1:] if (not argv or argv[0] == "--all") else [find(argv[0])]
    for a in targets:
        init_account(a)
        print("%s: %s" % (a.id, a.config_dir))


def cmd_login(argv):
    if not argv:
        die("使い方: claude-account login <id>")
    a = find(argv[0])
    init_account(a)
    print("%s（%s）にログインします。ブラウザで、このアカウントで使うメールと組織を選んでください。" % (a.display, a.config_dir))
    os.execvpe("claude", ["claude", "auth", "login"] + argv[1:], a.env())


def cmd_status(argv):
    a = find(argv[0]) if argv else from_config_dir(os.environ.get("CLAUDE_CONFIG_DIR"))
    os.execvpe("claude", ["claude", "auth", "status"] + argv[1:], a.env())


def cmd_env(argv):
    if not argv:
        die("使い方: claude-account env <id>")
    a = find(argv[0])
    if a.default:
        print("unset CLAUDE_CONFIG_DIR")
    else:
        init_account(a, quiet=True)
        print("export CLAUDE_CONFIG_DIR=%s" % shell_quote(a.path))


def shell_quote(s):
    return "'" + s.replace("'", "'\\''") + "'"


def cmd_exec(argv):
    if not argv:
        die("使い方: claude-account exec <id> [claude の引数…]")
    a = find(argv[0])
    init_account(a, quiet=True)
    os.execvpe("claude", ["claude"] + argv[1:], a.env())


def cmd_dir(argv):
    """CLAUDE_CONFIG_DIR に入れる値。既定のアカウントは空行（設定しない）。"""
    if not argv:
        die("使い方: claude-account dir <id>")
    a = find(argv[0])
    if not a.default:
        init_account(a, quiet=True)
    print("" if a.default else a.path)


def cmd_current(argv):
    a = from_config_dir(os.environ.get("CLAUDE_CONFIG_DIR"))
    if not a:
        die("CLAUDE_CONFIG_DIR=%s はどのアカウントでもありません" % os.environ.get("CLAUDE_CONFIG_DIR"))
    print(a.id)


def cmd_of_session(argv):
    acct = session_account(argv[0]) if argv else None
    if acct:
        print(acct)
    else:
        sys.exit(1)


def parse_opts(argv, names):
    opts, rest, i = {}, [], 0
    while i < len(argv):
        if argv[i] == "--":
            rest += argv[i + 1:]
            break
        key = argv[i][2:] if argv[i].startswith("--") else None
        if key in names:
            if names[key]:
                opts[key] = argv[i + 1]
                i += 1
            else:
                opts[key] = True
        else:
            rest.append(argv[i])
        i += 1
    return opts, rest


def origin_pane(opts):
    pane = opts.get("pane") or os.environ.get("CLAUDE_ACCOUNT_ORIGIN_PANE") or os.environ.get("HERDR_PANE_ID")
    if not pane:
        die("対象のペインが分かりません（--pane で指定してください）")
    return pane


def cmd_start(argv):
    """新しいタブで、指定のアカウントの claude を起動する。"""
    opts, rest = parse_opts(argv, {"pane": True, "cwd": True, "name": True, "no-focus": False})
    if not rest:
        die("使い方: claude-account start <id> [--pane P] [--cwd D] [-- claude の引数…]")
    a = find(rest[0])
    init_account(a, quiet=True)
    pane = origin_pane(opts)
    info = herdr(["pane", "get", pane]).get("result", {}).get("pane", {})
    cwd = opts.get("cwd") or info.get("foreground_cwd") or info.get("cwd") or HOME
    args = ["tab", "create", "--workspace", info.get("workspace_id", ""), "--cwd", cwd,
            "--label", a.label]
    if not a.default:
        args += ["--env", "CLAUDE_CONFIG_DIR=%s" % a.path]
    args += [] if opts.get("no-focus") else ["--focus"]
    res = herdr(args)
    new_pane = res["result"]["root_pane"]["pane_id"]
    name = opts.get("name") or new_name("c-" + a.id)
    ready = start_agent(name, new_pane, rest[1:])
    print("%s で起動しました（agent %s、pane %s）%s" % (a.display, name, new_pane,
                                                     "" if ready else "。入力待ちです。画面を確認してください"))


def cmd_switch(argv):
    """ペインの claude を終了し、同じ会話を別アカウントで --resume する。"""
    opts, rest = parse_opts(argv, {"pane": True, "force": False, "detached": False})
    if not rest:
        die("使い方: claude-account switch <id> [--pane P]")
    a = find(rest[0])
    pane = origin_pane(opts)
    ag = agent_in_pane(pane)
    if not ag or ag.get("agent") != "claude":
        die("ペイン %s で claude が動いていません" % pane)
    sid = (ag.get("agent_session") or {}).get("value")
    if not sid:
        die("ペイン %s のセッション ID が分かりません" % pane)
    if session_account(sid) == a.id:
        print("もう %s で動いています" % a.display)
        return
    if ag.get("agent_status") == "working" and not opts.get("force"):
        die("作業中です。止まってから切り替えてください（--force で強制）")
    init_account(a, quiet=True)

    # 自分自身のペインを切り替えるときは、claude と一緒に終了しないよう切り離して続ける
    if pane == os.environ.get("HERDR_PANE_ID") and not opts.get("detached"):
        log = open(os.path.join(STATE, "switch.log"), "a") if os.path.isdir(STATE) else subprocess.DEVNULL
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "switch", a.id, "--pane", pane, "--detached"]
                         + (["--force"] if opts.get("force") else []),
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        print("%s に切り替えます。このセッションはいったん終了し、同じ会話のまま開き直します" % a.display)
        return

    name = ag.get("name")
    herdr(["pane", "send-keys", pane, "escape"], check=False)
    herdr(["pane", "send-text", pane, "/exit"])
    herdr(["pane", "send-keys", pane, "enter"])
    for _ in range(60):
        time.sleep(0.5)
        if not agent_in_pane(pane):
            break
    else:
        die("claude が終了しませんでした（ペイン %s）" % pane)

    # 以後このペインのシェルはそのアカウントになる
    line = "unset CLAUDE_CONFIG_DIR" if a.default else "export CLAUDE_CONFIG_DIR=%s" % shell_quote(a.path)
    herdr(["pane", "send-text", pane, " " + line])  # 先頭の空白で履歴に残さない
    herdr(["pane", "send-keys", pane, "enter"])
    time.sleep(0.3)
    taken = taken_names()
    if not name or name in taken:
        name = new_name("c-" + a.id, taken)
    start_agent(name, pane, ["--resume", sid])
    remember_session(sid, a)
    msg = "%s に切り替えました（同じ会話を再開）" % a.display
    print(msg)
    notify(msg, ag.get("terminal_title_stripped") or sid)


def badge_text(a, data):
    """サイドバーの $acct。アカウント名と、statusLine が渡す上限の残り（5h / 7d）。

    上限は usagebar の $limit を使わない。会話の記録を共有しているので、usagebar の
    「セッションのファイルがどのアカウントの projects にあるか」での判定が当たらない。
    statusLine の入力はその claude プロセス自身（＝そのアカウント）の値なので確実。
    """
    text = a.display if a else "? %s" % os.environ.get("CLAUDE_CONFIG_DIR")
    limits = (data or {}).get("rate_limits") or {}
    for key, short in (("five_hour", "5h"), ("seven_day", "7d")):
        used = (limits.get(key) or {}).get("used_percentage")
        if isinstance(used, (int, float)):
            text += "  %s %d%%" % (short, max(0, min(100, round(100 - used))))
    return text


def report_badge(pane, a, data):
    subprocess.run(["herdr", "pane", "report-metadata", pane, "--source", "custom:account",
                    "--applies-to-source", "herdr:claude", "--token", "acct=%s" % badge_text(a, data)],
                   capture_output=True)


def cmd_hook(argv):
    """SessionStart / SessionEnd / statusline。どのアカウントで動いているかをサイドバーとセッション台帳に残す。

    statusline は ~/.claude/statusline.sh から入力の JSON を渡して呼ぶ（ターンごと・60 秒ごと）。
    """
    try:
        data = json.load(sys.stdin)
    except ValueError:
        data = {}
    event = argv[0] if argv else ""
    pane = os.environ.get("HERDR_PANE_ID")
    a = from_config_dir(os.environ.get("CLAUDE_CONFIG_DIR"))
    if event in ("session-start", "statusline"):
        if a:
            remember_session(data.get("session_id"), a)
        if pane and len(accounts()) > 1:
            report_badge(pane, a, data if event == "statusline" else None)
    elif event == "session-end" and pane:
        subprocess.run(["herdr", "pane", "report-metadata", pane, "--source", "custom:account",
                        "--applies-to-source", "herdr:claude", "--clear-token", "acct"], capture_output=True)


COMMANDS = {
    "list": cmd_list, "init": cmd_init, "login": cmd_login, "status": cmd_status, "env": cmd_env,
    "exec": cmd_exec, "dir": cmd_dir, "current": cmd_current, "start": cmd_start, "switch": cmd_switch,
    "of-session": cmd_of_session, "hook": cmd_hook,
}


def main(argv):
    if argv and argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd = argv[0] if argv else "list"
    if cmd not in COMMANDS:
        die("知らないコマンドです: %s（claude-account help）" % cmd)
    os.makedirs(STATE, exist_ok=True)
    COMMANDS[cmd](argv[1:])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
