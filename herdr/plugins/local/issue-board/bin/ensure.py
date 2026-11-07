#!/usr/bin/env python3
"""対象リポジトリの main の space で、今のタブの右に issue 一覧ペインが無ければ開く。

  ensure.py event     workspace.focused / tab.focused / tab.created のフック
  ensure.py startup   サーバ起動時。前のサーバで開いたペインの記録を捨てて、今の space に開き直す
  ensure.py toggle    今の space で出す / 出さないを切り替える（閉じると、その space では自動で開かない）

herdr のペイン情報にはプラグインの所有者が出ないので、開いたペインは state.json に tab_id -> pane_id で覚える。
"""
import fcntl
import json
import os
import shutil
import subprocess
import sys
import urllib.request

PLUGIN = "issue-board"
HOME = os.path.expanduser("~")
HERDR = os.environ.get("HERDR_BIN_PATH") or shutil.which("herdr") or "/opt/homebrew/bin/herdr"
CONFIG_DIR = os.environ.get("HERDR_PLUGIN_CONFIG_DIR") or os.path.join(HOME, ".config/herdr/plugins/config", PLUGIN)
STATE_DIR = os.environ.get("HERDR_PLUGIN_STATE_DIR") or os.path.join(HOME, ".local/state/herdr/plugins", PLUGIN)
DEFAULTS = {"repos": ["ai-president-next"], "width": 0.34}


def herdr(*args):
    p = subprocess.run([HERDR] + list(args), capture_output=True, text=True)
    if p.returncode != 0:
        return None
    try:
        return json.loads(p.stdout).get("result")
    except ValueError:
        return None


def load_config():
    conf = dict(DEFAULTS)
    try:
        with open(os.path.join(CONFIG_DIR, "config.json")) as f:
            conf.update(json.load(f))
    except (OSError, ValueError):
        pass
    return conf


def load_state():
    try:
        with open(os.path.join(STATE_DIR, "state.json")) as f:
            st = json.load(f)
    except (OSError, ValueError):
        st = {}
    st.setdefault("boards", {})
    st.setdefault("hidden", [])
    return st


def save_state(st):
    path = os.path.join(STATE_DIR, "state.json")
    with open(path + ".tmp", "w") as f:
        json.dump(st, f)
    os.replace(path + ".tmp", path)


def target_workspace(ws_id, conf):
    """対象リポジトリの main の space なら workspace 情報を返す"""
    res = herdr("workspace", "get", ws_id)
    ws = (res or {}).get("workspace") or {}
    wt = ws.get("worktree") or {}
    if wt.get("is_linked_worktree") or wt.get("repo_name") not in conf["repos"]:
        return None
    return ws


def board_alive(st, tab_id):
    pane_id = st["boards"].get(tab_id)
    if not pane_id:
        return False
    pane = (herdr("pane", "get", pane_id) or {}).get("pane") or {}
    if pane.get("tab_id") == tab_id:
        return True
    del st["boards"][tab_id]
    return False


def open_board(st, ws, tab_id, conf):
    panes = (herdr("pane", "list", "--workspace", ws["workspace_id"]) or {}).get("panes") or []
    panes = [p for p in panes if p.get("tab_id") == tab_id]
    if not panes:
        return
    target = next((p for p in panes if p.get("focused")), panes[0])
    repo_root = ws["worktree"].get("repo_root") or ws["worktree"]["checkout_path"]
    res = herdr("plugin", "pane", "open", "--plugin", PLUGIN, "--entrypoint", "board",
                "--placement", "split", "--direction", "right", "--target-pane", target["pane_id"],
                "--env","ISSUE_BOARD_REPO_ROOT=" + repo_root, "--no-focus")
    pane_id = find_value(res, "pane_id")
    if not pane_id:
        return
    st["boards"][tab_id] = pane_id
    fit_width(target["pane_id"], pane_id, float(conf.get("width", 0.34)))


def fit_width(left_pane, board_pane, frac):
    """一覧の幅をタブ幅の frac にする。amount はタブ幅に対する割合で、0 なら今のレイアウトだけ返る"""
    res = herdr("pane", "resize", "--pane", left_pane, "--direction", "right", "--amount", "0") or {}
    layout = (res.get("resize") or {}).get("layout") or {}
    area = (layout.get("area") or {}).get("width") or 0
    width = next((p["rect"]["width"] for p in layout.get("panes", []) if p.get("pane_id") == board_pane), 0)
    if not area or not width:
        return
    delta = width / float(area) - frac  # 正なら一覧が太すぎる → 境界を右へ
    if abs(delta) > 0.01:
        herdr("pane", "resize", "--pane", left_pane, "--direction", "right" if delta > 0 else "left",
              "--amount", "%.3f" % abs(delta))


def find_value(obj, key):
    if isinstance(obj, dict):
        if isinstance(obj.get(key), str):
            return obj[key]
        for v in obj.values():
            r = find_value(v, key)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_value(v, key)
            if r:
                return r
    return None


def ensure(st, ws_id, conf):
    if not ws_id or ws_id in st["hidden"]:
        return
    ws = target_workspace(ws_id, conf)
    if not ws:
        return
    tab_id = ws.get("active_tab_id")
    if not tab_id:
        return
    if board_alive(st, tab_id):
        poke(st["boards"][tab_id])
    else:
        open_board(st, ws, tab_id, conf)


def poke(pane_id):
    """タブに来たら、その一覧にすぐ reload を送る（gh を呼ぶかは list.sh の共有キャッシュ次第）"""
    try:
        with open(os.path.join(STATE_DIR, "port-" + pane_id.replace(":", "_"))) as f:
            port = int(f.read().strip())
    except (OSError, ValueError):
        return
    req = urllib.request.Request("http://localhost:%d" % port, method="POST",
                                 data=("reload(%s/list.sh)" % os.path.dirname(os.path.abspath(__file__))).encode())
    try:
        urllib.request.urlopen(req, timeout=2).read()
    except Exception:
        pass


def event_workspace():
    try:
        ev = json.loads(os.environ.get("HERDR_PLUGIN_EVENT_JSON") or "{}")
    except ValueError:
        ev = {}
    return find_value(ev, "workspace_id") or os.environ.get("HERDR_WORKSPACE_ID")


def focused_workspace():
    res = herdr("workspace", "list") or {}
    for ws in res.get("workspaces", []):
        if ws.get("focused"):
            return ws["workspace_id"]
    return None


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "event"
    conf = load_config()
    os.makedirs(STATE_DIR, exist_ok=True)
    lock = open(os.path.join(STATE_DIR, "lock"), "w")
    fcntl.flock(lock, fcntl.LOCK_EX)  # focused と tab.focused が同時に来ても二重に開かない
    st = load_state()

    if cmd == "startup":
        # 記録のペインは閉じない（別のペインかもしれない）。同じタブに残っていれば一覧とみなし、
        # 無いタブの記録は board_alive が捨てる
        ensure(st, focused_workspace(), conf)
    elif cmd == "toggle":
        ws_id = os.environ.get("HERDR_WORKSPACE_ID") or focused_workspace()
        if ws_id in st["hidden"]:
            st["hidden"].remove(ws_id)
            ensure(st, ws_id, conf)
        elif ws_id:
            st["hidden"].append(ws_id)
            for tab_id, pane_id in list(st["boards"].items()):
                pane = (herdr("pane", "get", pane_id) or {}).get("pane") or {}
                if pane.get("workspace_id") == ws_id:
                    herdr("pane", "close", pane_id)
                    del st["boards"][tab_id]
    else:
        ensure(st, event_workspace(), conf)

    save_state(st)
    return 0


if __name__ == "__main__":
    sys.exit(main())
