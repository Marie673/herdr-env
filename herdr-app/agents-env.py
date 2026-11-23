#!/usr/bin/env python3
"""Herdr GUI の agents 一覧を「選んでいる Space の agent だけ」に絞れるようにした複製（agents-env）を作る。

TUI の agent-view-space プラグインの GUI 版。Herdr 標準の mod はコードを配らず、その場で
`herdr-app do mod.clone` で複製して、必要な所だけ書き換える（Herdr を更新したら、このスクリプトを
もう一度流せば新しい agents から作り直す）。

  - 既定は「選んでいる Space だけ」。Agents 見出しの右のボタンか cmd+shift+a で全部 / この Space を切り替える
  - 切り替えは shared("herdr-env.agents.scope") に持つ（"all" で全部）
  - Claude のアカウントを2つ以上使っているときは、行にアカウントの色の丸（claude-account の印 🟦 / 🟪 の色）を出す

使い方: python3 agents-env.py [--home ~/.config/herdr-app]
"""
import json
import os
import shutil
import subprocess
import sys

home = os.path.expanduser(os.environ.get("HERDR_APP_HOME", "~/.config/herdr-app"))
if len(sys.argv) >= 3 and sys.argv[1] == "--home":
    home = os.path.expanduser(sys.argv[2])
NAME = "agents-env"
dest = os.path.join(home, "mods", NAME)

# 作り直す（前の複製は消す。中身はこのスクリプトから毎回同じに作れる）
if os.path.isdir(dest):
    shutil.rmtree(dest)
subprocess.run(["herdr-app", "do", "mod.clone", json.dumps({"name": "agents", "as": NAME})],
               check=True, stdout=subprocess.DEVNULL)

path = os.path.join(dest, "main.js")
src = open(path, encoding="utf-8").read()


def patch(old, new, label):
    global src
    if src.count(old) != 1:
        sys.exit(f"agents-env: 書き換え位置が見つかりません（Herdr の agents が変わった?）: {label}")
    src = src.replace(old, new)


# 1. import に nextCommand / checking を足す
patch('import { agents, terminals, panes, focused, spaces, run, theme, shared, setShared, setAgentOrder, openOverlay, closeOverlay, props, window, layout } from "herdr";',
      'import { agents, terminals, panes, focused, spaces, run, theme, shared, setShared, setAgentOrder, openOverlay, closeOverlay, props, window, layout, nextCommand, checking } from "herdr";\n\n'
      '// herdr-env: 選んでいる Space の agent だけを出す（"all" で全部）\n'
      'const SCOPE_KEY = "herdr-env.agents.scope";\n'
      'const scopeAll = () => shared(SCOPE_KEY) === "all";\n'
      'const toggleScope = () => setShared(SCOPE_KEY, scopeAll() ? "space" : "all");',
      "import")

# 2. キーの command（agents-env.scope）を受ける
patch("    if (props()?.sortMenu) return;\n    this.folded = new Set();",
      "    if (props()?.sortMenu) return;\n"
      "    if (!checking()) cx.spawn(async (cx) => { for (;;) { const c = await nextCommand(); if (c === \"scope\") toggleScope(); cx.notify(); } });\n"
      "    this.folded = new Set();",
      "init")

# 3. 一覧を絞る
patch("    let list = agents();\n",
      "    let list = agents();\n"
      "    const here = focused()?.space;\n"
      "    if (!scopeAll() && here) list = list.filter((a) => a.space === here || a.pinned);\n",
      "filter")

# 4. 見出しに今の範囲と切り替えボタン
patch('            .child(div().text_size(11.5).font_medium().text_color(t.dim).child("Agents"))',
      '            .child(div().text_size(11.5).font_medium().text_color(t.dim).child(scopeAll() ? "Agents" : "Agents · この Space"))\n'
      '            .child(div().flex_1())\n'
      '            .child(\n'
      '              div()\n'
      '                .id("ag-scope")\n'
      '                .mr(8)\n'
      '                .text_size(11)\n'
      '                .text_color(scopeAll() ? t.dim : t.accent)\n'
      '                .hover((s) => s.text_color(t.foreground))\n'
      '                .on_click((_e, cx) => { toggleScope(); cx.notify(); })\n'
      '                .tooltip(scopeAll() ? "この Space の agent だけ出す (⌘⇧A)" : "全部の agent を出す (⌘⇧A)")\n'
      '                .child(svg("mods/workspace-chrome/folder.svg").size(14)),\n'
      '            )',
      "header")

# 5. どのアカウントで動いているか（claude-account が付ける herdr-env.acct の先頭の印 🟦 / 🟪）を行に出す。
#    アカウントを2つ以上使っているときだけ（1つなら言うまでもない）。絵文字のままだと浮くので、テーマの色の小さな丸にする
patch("    const all = terminals();\n",
      "    const all = terminals();\n"
      "    const acctOf = new Map(all.map((x) => [x.address, (x.labels ?? {})[\"herdr-env.acct\"] || \"\"]));\n"
      "    const manyAccts = new Set(all.filter((x) => x.state === \"live\").map((x) => (x.labels ?? {})[\"herdr-env.acct_id\"]).filter(Boolean)).size > 1;\n",
      "accounts")
patch("        .when(Boolean(machine), el => el.child(div().text_size(10).text_color(t.dim).child(machine)))\n",
      "        .when(manyAccts && Boolean(acctOf.get(a.address)), el => el.child(div().size(6).flex_none().rounded(3)"
      ".bg(t[{ \"🟦\": \"ansi4\", \"🟩\": \"ansi2\", \"🟪\": \"ansi5\", \"🟥\": \"ansi1\", \"🟨\": \"ansi3\", \"🟧\": \"ansi3\" }[[...acctOf.get(a.address)][0]]] ?? t.dim)"
      ".tooltip(acctOf.get(a.address))))\n"
      "        .when(Boolean(machine), el => el.child(div().text_size(10).text_color(t.dim).child(machine)))\n",
      "row account")

open(path, "w", encoding="utf-8").write(src)
with open(os.path.join(dest, "mod.json"), "w", encoding="utf-8") as f:
    json.dump({"place": "none", "title": "Agents (herdr-env)"}, f)
print(f"agents-env: {dest}")
