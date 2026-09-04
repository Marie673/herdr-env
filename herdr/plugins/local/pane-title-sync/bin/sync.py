#!/usr/bin/env python3
"""ペイン名 / タブ名を、そのペインで動いているエージェントの会話タイトルに同期する。

herdr のサイドバー行は terminal_title_stripped を主役に表示しているので、
同じ文字列をペイン枠とタブに出すことで「サイドバー行 ↔ 画面上のペイン」が
文字列一致で辿れるようにするのが狙い。

手動で付けた名前は上書きしない。state.json に「自分が付けた名前」を覚えておき、
現在の名前がそれと一致する（＝自分が前回付けたまま）か、未設定のときだけ書き換える。
"""
import json
import os
import socket
import sys
import unicodedata

SOCK = os.environ.get("HERDR_SOCKET_PATH") or os.path.expanduser(
    "~/.config/herdr/herdr.sock"
)
STATE_DIR = os.environ.get("HERDR_PLUGIN_STATE_DIR") or os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)
STATE = os.path.join(STATE_DIR, "state.json")

MAX_COLS = 24  # ペイン枠に収まる表示幅の上限


def api(method, params=None):
    req = json.dumps({"id": "pane-title-sync", "method": method, "params": params or {}})
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(5)
    s.connect(SOCK)
    s.sendall(req.encode() + b"\n")
    buf = b""
    while b"\n" not in buf:
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
    s.close()
    res = json.loads(buf.split(b"\n")[0].decode())
    if "error" in res:
        raise RuntimeError(res["error"])
    return res["result"]


def width(text):
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def clip(text, cols=MAX_COLS):
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


def desired_pane_label(pane):
    """このペインに付けたい名前。付けるべきでなければ None。"""
    if not pane.get("agent"):
        return None
    title = (pane.get("terminal_title_stripped") or "").strip()
    # codex は terminal_title が固定文字列（ログイン名）で会話タイトルにならない
    if not title or title.lower() == os.path.basename(os.path.expanduser("~")).lower():
        cwd = os.path.basename(pane.get("foreground_cwd") or pane.get("cwd") or "")
        title = f"{pane['agent']} {cwd}".strip()
    return clip(title)


def load_state():
    try:
        with open(STATE) as f:
            st = json.load(f)
    except (OSError, ValueError):
        st = {}
    st.setdefault("panes", {})
    st.setdefault("tabs", {})
    return st


def save_state(st):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(STATE, "w") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


def ours(current, remembered, default_ok):
    """今の名前を書き換えてよいか。"""
    if current is None:
        return True
    if remembered is not None and current == remembered:
        return True
    return default_ok(current)


def sync():
    st = load_state()
    panes = api("pane.list")["panes"]
    tabs = api("tab.list")["tabs"]
    by_tab = {}
    for p in panes:
        by_tab.setdefault(p["tab_id"], []).append(p)

    changed = 0
    for p in panes:
        want = desired_pane_label(p)
        if want is None:
            continue
        pid = p["pane_id"]
        if not ours(p.get("label"), st["panes"].get(pid), lambda _c: False):
            continue
        if p.get("label") != want:
            api("pane.rename", {"pane_id": pid, "label": want})
            changed += 1
        st["panes"][pid] = want

    for t in tabs:
        members = [p for p in by_tab.get(t["tab_id"], []) if desired_pane_label(p)]
        if not members:
            continue
        # フォーカス中のペインを使うとタブ名がフォーカス移動のたびに変わるので、
        # 常に先頭のペインを使って名前を安定させる
        base = desired_pane_label(members[0])
        want = base if len(members) == 1 else clip(base, MAX_COLS - 3) + f" +{len(members) - 1}"
        tid = t["tab_id"]
        # 既定のタブ名はワークスペース内の連番（"1", "2", ...）なので数字だけなら未命名扱い
        if not ours(t.get("label"), st["tabs"].get(tid), lambda c: c.isdigit()):
            continue
        if t.get("label") != want:
            api("tab.rename", {"tab_id": tid, "label": want})
            changed += 1
        st["tabs"][tid] = want

    save_state(st)
    return changed


def clear():
    """自分が付けた名前だけ元に戻す。"""
    st = load_state()
    for pid in list(st["panes"]):
        try:
            api("pane.rename", {"pane_id": pid, "label": None})
        except RuntimeError:
            pass
    for tid, label in list(st["tabs"].items()):
        try:
            t = next(x for x in api("tab.list")["tabs"] if x["tab_id"] == tid)
        except (RuntimeError, StopIteration):
            continue
        # tab.rename は空文字を受け付けないので、既定の連番に戻す
        if t.get("label") == label:
            api("tab.rename", {"tab_id": tid, "label": str(t.get("number", 1))})
    save_state({"panes": {}, "tabs": {}})


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--clear":
        clear()
    else:
        print(f"renamed {sync()}")
