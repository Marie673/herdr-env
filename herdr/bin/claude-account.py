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
  claude-account default [--configured]    `claude` とだけ打ったときのアカウント id。accounts の `@default <id>`
                                            が上限に達していれば、次に使えるもの（--configured は設定どおり）
  claude-account init [<id>|--all]          追加アカウントのディレクトリを作る／symlink を直す
  claude-account login <id> [args…]         そのアカウントで `claude auth login`
  claude-account status <id>                そのアカウントで `claude auth status`
  claude-account env <id>                   シェルに eval する export / unset を出す
  claude-account exec <id> [claude の引数…]  そのアカウントで claude を起動する
  claude-account dir <id>                   CLAUDE_CONFIG_DIR に入れる値（既定は空）
  claude-account current                    この環境のアカウント id
  claude-account start <id> [--pane P | --terminal T] [--cwd D] [-- claude の引数…]
                                            herdr: 新しいタブでそのアカウントの claude を起動
  claude-account switch <id> [--pane P | --terminal T] [--force] [--prompt 文]
                                            herdr: ペインの会話を、同じ会話のまま別アカウントで開き直す
                                            （--prompt は開き直した会話に最初に送る文）
                                            （--terminal は Herdr の GUI のターミナル。GUI の中では既定で自分）
  claude-account of-session <session_id>    そのセッションを最後に動かしたアカウント id
  claude-account hook <session-start|session-end|statusline|stop-failure>
                                            Claude Code の hooks / statusLine から呼ぶ

既定（`@default`）と ~/.claude は別物: ~/.claude は CLAUDE_CONFIG_DIR 無しでログイン情報を持つ
アカウント（accounts の最初の行）、`@default` は `claude` とだけ打ったときに使うアカウント。

上限で止まったとき（StopFailure の rate_limit）は、同じ会話のまま次に使えるアカウントへ
自動で切り替え、続きを頼む（hook stop-failure）。上限に達したアカウントはリセットまで使わない。
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
PREFERRED = None  # accounts の `@default <id>`


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
    global PREFERRED
    out = []
    with open(CONF, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("@default"):
                PREFERRED = (line.split() + [""])[1]
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


def preferred():
    """`claude` とだけ打ったときのアカウント（accounts の `@default`。無ければ最初の行）"""
    accts = accounts()
    for a in accts:
        if a.id == PREFERRED:
            return a
    return accts[0]


# ---- 上限（使えるアカウントの選び方） ---------------------------------------------------

EXHAUSTED = os.path.join(STATE, "exhausted.json")


def load_exhausted():
    try:
        with open(EXHAUSTED) as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    now = time.time()
    return {k: v for k, v in d.items() if isinstance(v, (int, float)) and v > now}


def mark_exhausted(a, until):
    d = load_exhausted()
    if d.get(a.id, 0) >= until:
        return
    d[a.id] = until
    os.makedirs(STATE, exist_ok=True)
    tmp = EXHAUSTED + ".%d" % os.getpid()
    with open(tmp, "w") as f:
        json.dump(d, f)
    os.replace(tmp, EXHAUSTED)


def usage_windows():
    """ai-usage-collect.py（利用量バー）が集めるアカウントごとの残り。{id: [window…]}。取れなければ {}"""
    try:
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "ai-usage-collect.py")
        spec = importlib.util.spec_from_file_location("ai_usage_collect", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return {x["id"]: x.get("windows") or [] for x in mod.collect_claude(time.time()).get("accounts", [])}
    except Exception:
        return {}


def blocked_until(a, windows=None):
    """そのアカウントが上限で使えない間の終わり（使えるなら 0）"""
    until = load_exhausted().get(a.id, 0)
    now = time.time()
    for w in (windows or {}).get(a.id, []):
        rs = w.get("resets_at") or 0
        if isinstance(w.get("remaining"), (int, float)) and w["remaining"] <= 0 and rs > now:
            until = max(until, rs)
    return until


def available(exclude=()):
    """使えるアカウントを、既定・accounts の順に。ログインしていないもの、上限に達しているものは除く"""
    windows = usage_windows()
    pref = preferred()
    order = [pref] + [a for a in accounts() if a.id != pref.id]
    return [a for a in order if a.id not in exclude and a.oauth() and not blocked_until(a, windows)]


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


def ensure_trust(a, cwd):
    """切り替え先のアカウントで、そのフォルダの信頼確認が出ないようにする（ほかのアカウントで信頼済みなら写す）。
    信頼確認で止まると、自動で切り替えたあと続きを頼めない"""
    if not cwd:
        return
    cwd = os.path.realpath(cwd)

    def trusted(acct):
        try:
            with open(acct.claude_json, encoding="utf-8") as f:
                projects = json.load(f).get("projects") or {}
        except (OSError, ValueError):
            return False
        return any(os.path.realpath(p) == cwd and isinstance(v, dict) and v.get("hasTrustDialogAccepted")
                   for p, v in projects.items())

    if trusted(a) or not any(trusted(b) for b in accounts() if b.id != a.id):
        return
    try:
        with open(a.claude_json, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return
    d.setdefault("projects", {}).setdefault(cwd, {})["hasTrustDialogAccepted"] = True
    tmp = a.claude_json + ".claude-account.%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, a.claude_json)


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
    if gui_mode():
        gui_notify(title, body)
        return
    subprocess.run(["herdr", "notification", "show", title, "--body", body, "--sound", "none"],
                   capture_output=True)


# ---- Herdr の GUI（Herdr.app。ペインは herdr-core の hc ターミナル） -----------------------
# GUI のペインには HERDR_PANE_ID が無く、HC_TERMINAL_ID が入る。herdr の agent / pane コマンドは
# GUI とつながっていないので、hc と herdr-app を使う。


def gui_mode():
    return bool(os.environ.get("HC_TERMINAL_ID") or os.environ.get("CLAUDE_ACCOUNT_ORIGIN_TERMINAL")) \
        and not os.environ.get("HERDR_PANE_ID") and shutil.which("hc") is not None


def hc(args, check=True):
    # HC_TERMINAL_ID を外す。付いていると hc は送り主の agent からのメッセージとして <hc> の札を付けて
    # 送るので、/exit がコマンドとして効かない（人がキーで打ったのと同じにする）
    env = {k: v for k, v in os.environ.items() if k != "HC_TERMINAL_ID"}
    out = subprocess.run(["hc"] + args, capture_output=True, text=True, env=env)
    if check and out.returncode != 0:
        die("hc %s: %s" % (" ".join(args[:2]), (out.stderr or out.stdout).strip()))
    try:
        return json.loads(out.stdout) if out.stdout.strip() else {}
    except ValueError:
        return {}


def herdr_app(args, check=True):
    out = subprocess.run(["herdr-app"] + args, capture_output=True, text=True)
    try:
        res = json.loads(out.stdout) if out.stdout.strip() else {}
    except ValueError:
        res = {}
    if check and (out.returncode != 0 or "error" in res):
        die("herdr-app %s: %s" % (" ".join(args[:2]), res.get("error") or (out.stderr or out.stdout).strip()))
    return res


def gui_notify(title, body=""):
    """GUI には外から通知を出すコマンドが無いので、macOS の通知に出す"""
    script = "display notification %s with title %s" % (json.dumps(body[:200]), json.dumps(title))
    subprocess.run(["osascript", "-e", script], capture_output=True)


def gui_terminal_session(term):
    """GUI のターミナルで動いている claude の session ID。hooks が付けるラベルか、起動引数の --resume から"""
    info = hc(["get", term, "--json"], check=False)
    sid = (info.get("labels") or {}).get("herdr-env.session")
    argv = (info.get("foreground") or {}).get("argv") or []
    if not sid and "--resume" in argv[:-1]:
        sid = argv[argv.index("--resume") + 1]
    return info, sid


def gui_is_claude(info):
    fg = (info.get("foreground") or {}).get("argv") or [""]
    return info.get("agent") == "claude-code" or (info.get("status") or {}).get("app") == "claude-code" \
        or os.path.basename(fg[0]) == "claude"


def gui_space_of(term):
    for sp in herdr_app(["ls"]).get("spaces", []):
        for tab in sp.get("tabs", []):
            for x in tab.get("terminals", []):
                if x.get("terminal") == term:
                    return sp
    return None


def gui_label(term, **kv):
    if term:
        subprocess.run(["hc", "label", term] + ["herdr-env.%s=%s" % (k, v) for k, v in kv.items()],
                       capture_output=True)


def claude_line(a, args):
    """ログインシェルで打つ claude の起動行（claude のラッパー関数を通すため -A で渡す）"""
    return " ".join(["claude", "-A", a.id] + [shell_quote(x) for x in args])


def pad(text, cols):
    import unicodedata
    w = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)
    return text + " " * max(1, cols - w)


# ---- コマンド --------------------------------------------------------------------


def cmd_list(argv):
    opts, _ = parse_opts(argv, {"mark": True})
    here = find(opts["mark"]) if opts.get("mark") else from_config_dir(os.environ.get("CLAUDE_CONFIG_DIR"))
    pref = preferred()
    blocked = load_exhausted()
    for a in accounts():
        o = a.oauth()
        who = "%s / %s" % (o.get("emailAddress", "?"), o.get("organizationName", "?")) if o else "未ログイン"
        plan = (o.get("organizationType") or o.get("billingType") or "") if o else ""
        mark = "*" if here and here.id == a.id else " "
        print("%s %s %s %s %s %s" % (mark, pad(a.id, 10), pad(a.display, 14),
                                     pad(a.config_dir + (" (既定)" if a.id == pref.id else ""), 24),
                                     who, ("[%s]" % plan) if plan else "")
              + ("  上限（%s まで）" % time.strftime("%m/%d %H:%M", time.localtime(blocked[a.id]))
                 if a.id in blocked else ""))


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


def cmd_default(argv):
    if "--configured" in argv:
        print(preferred().id)
        return
    usable = available()
    print((usable[0] if usable else preferred()).id)


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


def origin_terminal(opts):
    t = opts.get("terminal") or os.environ.get("CLAUDE_ACCOUNT_ORIGIN_TERMINAL") or os.environ.get("HC_TERMINAL_ID")
    if not t:
        die("対象のターミナルが分かりません（--terminal で指定してください）")
    return t


def origin_pane(opts):
    pane = opts.get("pane") or os.environ.get("CLAUDE_ACCOUNT_ORIGIN_PANE") or os.environ.get("HERDR_PANE_ID")
    if not pane:
        die("対象のペインが分かりません（--pane で指定してください）")
    return pane


def cmd_start(argv):
    """新しいタブで、指定のアカウントの claude を起動する。"""
    opts, rest = parse_opts(argv, {"pane": True, "terminal": True, "cwd": True, "name": True, "no-focus": False})
    if not rest:
        die("使い方: claude-account start <id> [--pane P | --terminal T] [--cwd D] [-- claude の引数…]")
    a = find(rest[0])
    init_account(a, quiet=True)
    if opts.get("terminal") or (not opts.get("pane") and gui_mode()):
        return gui_start(a, opts, rest[1:])
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


def gui_start(a, opts, args):
    term = origin_terminal(opts)
    info = hc(["get", term, "--json"], check=False)
    sp = gui_space_of(term)
    cwd = opts.get("cwd") or (info.get("foreground") or {}).get("cwd") or info.get("cwd") or HOME
    req = {"name": a.label, "folder": cwd, "focus": not opts.get("no-focus"),
           "argv": ["/bin/zsh", "-lic", "%s; exec /bin/zsh -l" % claude_line(a, args)]}
    if sp:
        req["space"] = sp["id"]
    res = herdr_app(["do", "tab.new", json.dumps(req, ensure_ascii=False)])
    print("%s で起動しました（ターミナル %s）" % (a.display, res.get("terminal", "?")))


def gui_switch(a, opts):
    """GUI のターミナルの claude を終え、同じ会話を別アカウントで開き直す"""
    term = origin_terminal(opts)
    info, sid = gui_terminal_session(term)
    if not gui_is_claude(info):
        die("ターミナル %s で claude が動いていません" % term)
    if not sid:
        die("ターミナル %s のセッション ID が分かりません" % term)
    if session_account(sid) == a.id:
        print("もう %s で動いています" % a.display)
        return
    if info.get("status") == "working" and not opts.get("force"):
        die("作業中です。止まってから切り替えてください（--force で強制）")
    init_account(a, quiet=True)
    if term == os.environ.get("HC_TERMINAL_ID") and not opts.get("detached"):
        log = open(os.path.join(STATE, "switch.log"), "a") if os.path.isdir(STATE) else subprocess.DEVNULL
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "switch", a.id, "--terminal", term, "--detached"]
                         + (["--force"] if opts.get("force") else [])
                         + (["--prompt", opts["prompt"]] if opts.get("prompt") else []),
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        print("%s に切り替えます。このセッションはいったん終了し、同じ会話のまま開き直します" % a.display)
        return
    cwd = (info.get("foreground") or {}).get("cwd") or info.get("cwd") or HOME
    hc(["keys", term, "esc"], check=False)
    hc(["send", term, "/exit"], check=False)
    alive = True
    for _ in range(60):
        time.sleep(0.5)
        cur = hc(["get", term, "--json"], check=False)
        if cur.get("state") != "live":
            alive = False
            break
        if not gui_is_claude(cur):
            break
    else:
        die("claude が終了しませんでした（ターミナル %s）" % term)
    ensure_trust(a, cwd)
    line = claude_line(a, ["--resume", sid] + ([opts["prompt"]] if opts.get("prompt") else []))
    if alive:
        # ターミナルにシェルが残った: そこで続ける（先頭の空白で履歴に残さない）
        time.sleep(0.3)
        hc(["send", term, " cd %s && %s" % (shell_quote(cwd), line)])
    else:
        sp = gui_space_of(term)
        req = {"folder": cwd, "argv": ["/bin/zsh", "-lic", "%s; exec /bin/zsh -l" % line]}
        if sp:
            req["space"] = sp["id"]
        herdr_app(["do", "tab.new", json.dumps(req, ensure_ascii=False)])
    remember_session(sid, a)
    msg = "%s に切り替えました（同じ会話を再開）" % a.display
    print(msg)
    notify(msg, sid)


def cmd_switch(argv):
    """ペインの claude を終了し、同じ会話を別アカウントで --resume する。"""
    opts, rest = parse_opts(argv, {"pane": True, "terminal": True, "force": False, "detached": False,
                                   "prompt": True})
    if not rest:
        die("使い方: claude-account switch <id> [--pane P | --terminal T]")
    a = find(rest[0])
    if opts.get("terminal") or (not opts.get("pane") and gui_mode()):
        return gui_switch(a, opts)
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
                         + (["--force"] if opts.get("force") else [])
                         + (["--prompt", opts["prompt"]] if opts.get("prompt") else []),
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        print("%s に切り替えます。このセッションはいったん終了し、同じ会話のまま開き直します" % a.display)
        return

    name = ag.get("name")
    info = herdr(["pane", "get", pane], check=False).get("result", {}).get("pane", {})
    ensure_trust(a, info.get("foreground_cwd") or info.get("cwd"))
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
    start_agent(name, pane, ["--resume", sid] + ([opts["prompt"]] if opts.get("prompt") else []))
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


def limits_text(data):
    limits = (data or {}).get("rate_limits") or {}
    out = []
    for key, short in (("five_hour", "5h"), ("seven_day", "7d")):
        used = (limits.get(key) or {}).get("used_percentage")
        if isinstance(used, (int, float)):
            out.append("%s %d%%" % (short, max(0, min(100, round(100 - used)))))
    return "  ".join(out)


def limits_resets(data):
    """lim の枠ごとのリセット時刻（"5h:<epoch> 7d:<epoch>"）。読む側は、同じアカウントの
    ターミナルのうちどれの値が新しいかを、リセット時刻と残りで決める（描いた時刻では決めない。
    手の空いた会話も、最後に API を呼んだときの古い値で描き直すため）"""
    limits = (data or {}).get("rate_limits") or {}
    out = []
    for key, short in (("five_hour", "5h"), ("seven_day", "7d")):
        w = limits.get(key) or {}
        if isinstance(w.get("used_percentage"), (int, float)) and isinstance(w.get("resets_at"), (int, float)):
            out.append("%s:%d" % (short, int(w["resets_at"])))
    return " ".join(out)


def gui_hook(term, event, a, data):
    """GUI: このターミナルの会話 ID・アカウント・上限の残りを hc のラベルに載せる。
    herdr-env の mod（herdr-env-marks の残量の欄、agents-env の行）と herdr-shelf / ピッカーが読む"""
    if event == "session-end":
        subprocess.run(["hc", "label", term, "herdr-env.session=", "herdr-env.acct=", "herdr-env.acct_id="],
                       capture_output=True)
        return
    kv = {}
    sid = data.get("session_id")
    if sid:
        kv["session"] = sid
    if a:
        kv["acct_id"] = a.id
        kv["acct"] = a.display
    lim = limits_text(data) if event == "statusline" else ""
    if lim:
        kv["lim"] = lim
        kv["lim_rs"] = limits_resets(data)
        kv["lim_at"] = str(int(time.time()))
    if kv:
        gui_label(term, **kv)


FAILOVER = os.path.join(STATE, "failover.json")


def failover_log(msg):
    os.makedirs(STATE, exist_ok=True)
    with open(os.path.join(STATE, "failover.log"), "a") as f:
        f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))


def stop_failure(a, data, pane, term):
    """StopFailure（rate_limit）: 上限に達したアカウントを記録し、同じ会話を次のアカウントで開き直して続きを頼む"""
    sid = data.get("session_id")
    if data.get("error") != "rate_limit" or not a or not sid:
        return
    if "1M context" in (data.get("last_assistant_message") or ""):
        return  # 1M コンテキストの追加クレジットの話で、上限ではない
    # この会話がそのペイン／ターミナルの主の会話か（中で動かした claude -p などは切り替えない）
    if term:
        if (hc(["get", term, "--json"], check=False).get("labels") or {}).get("herdr-env.session") != sid:
            return
    elif pane:
        ag = agent_in_pane(pane) or {}
        if (ag.get("agent_session") or {}).get("value") != sid:
            return
    else:
        return
    # 上限に達した枠のリセットまで、そのアカウントは使わない（分からなければ1時間）
    until = blocked_until(a, usage_windows())
    mark_exhausted(a, until if until > time.time() else time.time() + 3600)
    # 同じ会話が短い間に何度も切り替わるなら止める（全部のアカウントが上限など）
    try:
        with open(FAILOVER) as f:
            hist = json.load(f)
    except (OSError, ValueError):
        hist = {}
    now = time.time()
    hist = {k: [t for t in v if now - t < 900] for k, v in hist.items()}
    hist = {k: v for k, v in hist.items() if v}
    if len(hist.get(sid, [])) >= len(accounts()):
        failover_log("%s: 15分で %d 回切り替えたので止めます" % (sid, len(hist[sid])))
        notify("Claude の上限: 自動の切り替えを止めました", "15分で何度も上限に達しています")
        return
    nxt = available(exclude=(a.id,))
    if not nxt:
        failover_log("%s: %s が上限、使えるアカウントがありません" % (sid, a.id))
        notify("Claude の上限: 使えるアカウントがありません", "%s が上限に達しました" % a.display)
        return
    b = nxt[0]
    hist.setdefault(sid, []).append(now)
    os.makedirs(STATE, exist_ok=True)
    with open(FAILOVER, "w") as f:
        json.dump(hist, f)
    prompt = ("（自動）%s の利用上限に達したので、%s に切り替えて同じ会話を開き直しました。"
              "中断したところから続きを進めてください。" % (a.label, b.label))
    target = ["--terminal", term] if term else ["--pane", pane]
    failover_log("%s: %s -> %s (%s)" % (sid, a.id, b.id, " ".join(target)))
    log = open(os.path.join(STATE, "switch.log"), "a")
    subprocess.Popen([sys.executable, os.path.abspath(__file__), "switch", b.id] + target
                     + ["--detached", "--force", "--prompt", prompt],
                     stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)


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
    term = os.environ.get("HC_TERMINAL_ID") if not pane else None
    if term:
        gui_hook(term, event, a, data)
    if event == "stop-failure":
        return stop_failure(a, data, pane, term)
    if event == "statusline" and a:
        for w in (data.get("rate_limits") or {}).values():
            if isinstance(w, dict) and (w.get("used_percentage") or 0) >= 100 \
                    and (w.get("resets_at") or 0) > time.time():
                mark_exhausted(a, w["resets_at"])
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
    "of-session": cmd_of_session, "hook": cmd_hook, "default": cmd_default,
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
