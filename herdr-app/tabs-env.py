#!/usr/bin/env python3
"""Herdr GUI のタブバーに「Issues」ボタンを足す複製（tabs-env）を作る。

標準のタブバーは、右パネル（Files / Changes）を開くボタンを2つだけ固定で持つ。herdr-env は右パネルの
スタックに Issues（mods/issues-env）を足すので、そのボタンも並べる。押すと右パネルを Issues にして開き、
もう一度押すと閉じる（Files / Changes と同じ。Space ごとに覚えるのも同じ）。

キーからも開けるよう、コマンド tabs-env.issues を受ける（setup.sh が cmd+shift+i に割り当てる）。
タブバーは常に見えているので、パネルが閉じていても届く（issues-env 自身は閉じたパネルの中にいて届かない）。

Herdr 標準の mod はコードを配らず、`herdr-app do mod.clone` で複製して必要な所だけ書き換える
（Herdr を更新したら、このスクリプトをもう一度流す）。

使い方: python3 tabs-env.py [--home ~/.config/herdr-app]
"""
import json
import os
import shutil
import subprocess
import sys

home = os.path.expanduser(os.environ.get("HERDR_APP_HOME", "~/.config/herdr-app"))
if len(sys.argv) >= 3 and sys.argv[1] == "--home":
    home = os.path.expanduser(sys.argv[2])
NAME = "tabs-env"
dest = os.path.join(home, "mods", NAME)

if os.path.isdir(dest):
    shutil.rmtree(dest)
subprocess.run(["herdr-app", "do", "mod.clone", json.dumps({"name": "tabs", "as": NAME})],
               check=True, stdout=subprocess.DEVNULL)

path = os.path.join(dest, "main.js")
src = open(path, encoding="utf-8").read()


def patch(old, new, label):
    global src
    if src.count(old) != 1:
        sys.exit(f"tabs-env: 書き換え位置が見つかりません（Herdr の tabs が変わった?）: {label}")
    src = src.replace(old, new)


# 1. コマンドを受ける
patch('import { spaces, panes, run, openOverlay, theme, window, shared, layout, settings } from "herdr";',
      'import { spaces, panes, run, openOverlay, theme, window, shared, layout, settings, nextCommand, checking } from "herdr";',
      "import")
patch('  init() {\n    this.pressed = null;\n',
      '  init(_props, cx) {\n'
      '    this.pressed = null;\n'
      '    // herdr-env: tabs-env.issues で右パネルの Issues を出し入れする\n'
      '    if (!checking()) cx.spawn(async (cx) => {\n'
      '      for (;;) {\n'
      '        const c = await nextCommand();\n'
      '        if (c === "issues") {\n'
      '          const root = layout(), panel = find(root, "panel"), body = find(root, "panel-body");\n'
      '          if (panel && !panel.hidden && body?.active === "issues") run("layout.hide", { id: "panel" });\n'
      '          else { run("layout.select", { stack: "panel-body", child: "issues" }); run("layout.show", { id: "panel" }); }\n'
      '        }\n'
      '        cx.notify();\n'
      '      }\n'
      '    });\n',
      "init")

# 2. Issues もパネルの候補に入れる（Space ごとに覚えた選択を戻せるように）
patch('    const enabled = shared("workspace.panels") ?? ["files", "changes"];\n',
      '    const enabled = [...new Set([...(shared("workspace.panels") ?? ["files", "changes"]), ...(find(root, "issues") ? ["issues"] : [])])];\n',
      "enabled")

# 3. ボタン
patch('      .when(enabled.includes("changes"), el => el.child(panelButton("changes", "Changes", "mods/changes/changes.svg")))\n',
      '      .when(enabled.includes("changes"), el => el.child(panelButton("changes", "Changes", "mods/changes/changes.svg")))\n'
      '      .when(enabled.includes("issues"), el => el.child(panelButton("issues", "Issues", "mods/issues-env/issue.svg")))\n',
      "button")

open(path, "w", encoding="utf-8").write(src)
with open(os.path.join(dest, "mod.json"), "w", encoding="utf-8") as f:
    json.dump({"place": "none", "title": "Tabs (herdr-env)"}, f)
print(f"tabs-env: {dest}")
