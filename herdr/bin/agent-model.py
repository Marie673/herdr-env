#!/usr/bin/env python3
"""フォーカス中の agent（Claude Code / Codex / Antigravity CLI）のモデル選択を開く。

Herdr の GUI（Herdr.app）では cmd+p がピッカー（検索）に取られていて、Claude Code の
モデル切り替えが押せない。代わりに cmd+shift+m（herdr-env-marks.model）からこれを呼ぶ。

  agent-model <terminal>      例: agent-model t7（hc のターミナル）

agent ごとの開き方:
  - Claude Code: alt+p（Claude Code の既定のモデル切り替えキー）を送る。入力中の下書きはそのまま残る
  - Codex / Antigravity CLI: モデル選択のキーが無い（Codex の tui.keymap にも agy の keybindings.json にも
    その操作が無い）ので、`/model` を打って Enter を押す。
    下書きの後ろに打つと下書きごと送ってしまうので、画面を読んで入力欄が空のときだけ打つ。
    動作中・確認待ちのときも打たない

終了コード: 0 開いた / 1 hc の失敗 / 2 対応していないプログラム / 3 動作中・確認待ち / 4 入力欄が空でない・見つからない
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time

# Codex の入力欄が空のときに出る文（codex-cli 0.171 の場合）
CODEX_PLACEHOLDERS = {"Ask Codex to do anything", "Ask a follow-up question"}
# agy の入力欄に一時的に出る案内（空のときだけ出る）
AGY_HINT = re.compile(r"\(shift\+tab to cycle\)$")


def hc_bin():
    return shutil.which("hc") or os.path.expanduser("~/.local/bin/hc")


def hc(args):
    # HC_TERMINAL_ID を外す。付いていると hc は送り主の agent からの入力として扱い、札を付けることがある
    env = {k: v for k, v in os.environ.items() if k != "HC_TERMINAL_ID"}
    out = subprocess.run([hc_bin()] + args, capture_output=True, text=True, env=env, timeout=10)
    if out.returncode != 0:
        raise RuntimeError("hc %s: %s" % (" ".join(args[:2]), (out.stderr or out.stdout).strip()))
    return out.stdout


def hc_json(args):
    s = hc(args + ["--json"])
    return json.loads(s) if s.strip() else {}


def kind_of(info):
    app = ((info.get("status") or {}).get("app") or "").lower()
    if app in ("claude-code", "claude"):
        return "claude"
    if app == "codex":
        return "codex"
    if app in ("antigravity", "agy"):
        return "agy"
    argv = ((info.get("foreground") or {}).get("argv")) or []
    name = os.path.basename(argv[0]) if argv else ""
    return {"claude": "claude", "codex": "codex", "agy": "agy"}.get(name, "")


def codex_composer_empty(lines):
    """Codex の入力欄は画面のいちばん下の `› ` の行から、空行までの続き。"""
    idx = [i for i, l in enumerate(lines) if l.startswith("›")]
    if not idx:
        return None
    i = idx[-1]
    first = lines[i][1:].strip()
    if re.match(r"\d+\.\s", first):  # 選択肢の一覧（/model などのポップアップ）が開いている
        return None
    rest = []
    for l in lines[i + 1:]:
        if not l.strip():
            break
        rest.append(l.strip())
    return (first == "" or first in CODEX_PLACEHOLDERS) and not rest


def agy_composer_empty(lines):
    """agy の入力欄は、画面いっぱいの横線 2 本に挟まれた行（先頭が `> `）。"""
    rules = [i for i, l in enumerate(lines) if len(l.rstrip()) >= 20 and set(l.rstrip()) == {"─"}]
    if len(rules) < 2:
        return None
    a, b = rules[-2], rules[-1]
    body = [l.strip() for l in lines[a + 1:b]]
    if not body or not body[0].startswith(">"):
        return None
    body[0] = body[0][1:].strip()
    text = [l for l in body if l]
    return not text or (len(text) == 1 and bool(AGY_HINT.search(text[0])))


def main(argv):
    if len(argv) != 1 or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0 if argv and argv[0] in ("-h", "--help") else 2
    term = argv[0]
    try:
        info = hc_json(["get", term])
        kind = kind_of(info)
        if kind == "claude":
            hc(["keys", term, "alt+p"])
            return 0
        if kind not in ("codex", "agy"):
            print("モデル切り替えに対応していないプログラムです（Claude Code / Codex / Antigravity CLI だけ）")
            return 2
        state = (info.get("status") or {}).get("state") or ""
        if state in ("working", "blocked"):
            print("動作中・確認待ちの間は /model を打ちません（止まってからもう一度）")
            return 3
        text = hc_json(["read", term]).get("text") or ""
        lines = text.splitlines()
        empty = codex_composer_empty(lines) if kind == "codex" else agy_composer_empty(lines)
        if empty is None:
            print("入力欄が見つかりません（何か開いていれば閉じてからもう一度）")
            return 4
        if not empty:
            print("入力欄に下書きがあるので /model を打ちません（空にしてからもう一度）")
            return 4
        hc(["keys", term, "/", "m", "o", "d", "e", "l"])
        time.sleep(0.3)  # 候補の一覧が出てから Enter
        hc(["keys", term, "enter"])
        return 0
    except (RuntimeError, ValueError, subprocess.TimeoutExpired) as e:
        print(str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
