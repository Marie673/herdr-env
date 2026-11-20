#!/usr/bin/env python3
"""Herdr GUI の Spaces 一覧の各行に、その Space の agent の状態を出す複製（spaces-env）を作る。

標準の Spaces 一覧は、サイドバーを細く畳んだときしか状態を出さない（全体は Agents 欄で見る作り）。
herdr-env では Agents 欄を「選んでいる Space だけ」に絞るので、ほかの Space で agent が動いているかが
見えなくなる。そこで Spaces の各行の右端に出す:

  動作中 = スピナー / 要対応（blocked）= ! / 完了・未確認（done）= 点
  💬 = 人間の返事待ち（Stop フックの旗）/ ⏳ = 外部待ち（herdr-wait）… hc のラベル herdr-env.* から

worktree の Space には、そのブランチの PR の状態も名前の後ろに出す（TUI の pr-status と同じ文言）:

  #123 / #123 draft / #123 競合 / #123 merged / #123 closed、CI ✓ / CI … / CI ✗2、CR ✓ 要修正 など
  … pr-status（herdr/plugins/local/pr-status）が毎分書く hc のラベル herdr-env.pr / ci / rv から。30分で失効

Herdr 標準の mod はコードを配らず、`herdr-app do mod.clone` で複製して必要な所だけ書き換える
（Herdr を更新したら、このスクリプトをもう一度流す）。

使い方: python3 spaces-env.py [--home ~/.config/herdr-app]
"""
import json
import os
import shutil
import subprocess
import sys

home = os.path.expanduser(os.environ.get("HERDR_APP_HOME", "~/.config/herdr-app"))
if len(sys.argv) >= 3 and sys.argv[1] == "--home":
    home = os.path.expanduser(sys.argv[2])
NAME = "spaces-env"
dest = os.path.join(home, "mods", NAME)

if os.path.isdir(dest):
    shutil.rmtree(dest)
subprocess.run(["herdr-app", "do", "mod.clone", json.dumps({"name": "spaces", "as": NAME})],
               check=True, stdout=subprocess.DEVNULL)

path = os.path.join(dest, "main.js")
src = open(path, encoding="utf-8").read()


def patch(old, new, label):
    global src
    if src.count(old) != 1:
        sys.exit(f"spaces-env: 書き換え位置が見つかりません（Herdr の spaces が変わった?）: {label}")
    src = src.replace(old, new)


# 1. terminals を読む。Space ごとの印（💬 / ⏳）をまとめる関数と、行末の状態表示
patch('import { spaces, machines, panes, run, openOverlay, exec, hc, theme, shared, setShared, watchFolder, window, layout } from "herdr";',
      'import { spaces, machines, panes, run, openOverlay, exec, hc, theme, shared, setShared, watchFolder, window, layout, terminals } from "herdr";\n'
      '\n'
      '// herdr-env: Space ごとの印。人間の返事待ち（herdr-env.human）と外部待ち（herdr-env.wait）。6時間で失効\n'
      'function envMarks() {\n'
      '  const now = Date.now() / 1000, TTL = 6 * 3600, out = new Map();\n'
      '  for (const t of terminals()) {\n'
      '    const l = t.labels ?? {}, sp = l["herdr.space"];\n'
      '    if (!sp || t.state !== "live") continue;\n'
      '    const m = out.get(sp) ?? {};\n'
      '    const h = Number(l["herdr-env.human"] || 0), s = Number(l["herdr-env.since"] || 0);\n'
      '    if (h && now - h < TTL && t.status !== "working") m.human = true;\n'
      '    if (l["herdr-env.wait"] && s && now - s < TTL) m.wait = true;\n'
      '    out.set(sp, m);\n'
      '  }\n'
      '  return out;\n'
      '}\n'
      '\n'
      '// herdr-env: 行末の状態。要対応 > 返事待ち > 完了 > 動作中 > 外部待ち。何も無ければ出さない\n'
      'function stateMark(t, status, m = {}) {\n'
      '  const box = (el) => h_flex().w(16).h(16).flex_none().items_center().justify_center().child(el);\n'
      '  if (status === "blocked") return box(div().size(14).rounded(4).bg(t.blocked + "26").text_size(10.5).font_bold().text_color(t.blocked).flex().items_center().justify_center().child("!"));\n'
      '  if (m.human) return box(div().text_size(11).child("💬"));\n'
      '  if (status === "done") return box(div().size(7).rounded(4).bg(t.done));\n'
      '  if (status === "working") return box(svg("mods/workspace-chrome/spinner.svg").size(12).text_color(t.working).spin(900));\n'
      '  if (m.wait) return box(div().text_size(11).child("⏳"));\n'
      '  return null;\n'
      '}',
      "import")

# 2. 行の右端に状態を足す
patch('          .child(g ? this.gitTail(g, tree, w.name, t) : div().flex_1()),\n      );\n  }',
      '          .child(g ? this.gitTail(g, tree, w.name, t) : div().flex_1())\n'
      '          .when(Boolean(stateMark(t, w.status, this.marks?.get(w.id))), (el) => el.child(stateMark(t, w.status, this.marks?.get(w.id)))),\n'
      '      );\n  }',
      "row")

# 3. 描くたびに印を読み直す（行ごとに terminals() を呼ばない）
patch('  render() {\n    const t = theme();\n',
      '  render() {\n    const t = theme();\n    this.marks = envMarks();\n',
      "render")

# 4. PR の状態（pr-status が書くラベル herdr-env.pr / ci / rv / pr_at）を印と一緒に読み、名前の後ろに出す
patch('    if (l["herdr-env.wait"] && s && now - s < TTL) m.wait = true;\n',
      '    if (l["herdr-env.wait"] && s && now - s < TTL) m.wait = true;\n'
      '    const at = Number(l["herdr-env.pr_at"] || 0);\n'
      '    if (!m.pr && at && now - at < 30 * 60 && (l["herdr-env.pr"] || l["herdr-env.ci"] || l["herdr-env.rv"]))\n'
      '      m.pr = { pr: l["herdr-env.pr"] || "", ci: l["herdr-env.ci"] || "", rv: l["herdr-env.rv"] || "" };\n',
      "pr labels")
patch('// herdr-env: 行末の状態。',
      """// herdr-env: PR の状態（pr-status のラベル）。悪いものは赤、待ちは黄、通ったものは緑、merged は紫、closed は薄く
function prTail(t, p) {
  const bad = /競合|✗|要修正|未解決/, wait = /…|draft/, good = /✓|承認/;
  const tone = (s) => /merged/.test(s) ? t.ansi5 : /closed/.test(s) ? t.dim : bad.test(s) ? t.ansi1 : wait.test(s) ? t.ansi3 : good.test(s) ? t.ansi2 : t.dim;
  return h_flex().flex_none().gap(5).items_center().text_size(11.5)
    .children([p.pr, p.ci, p.rv].filter(Boolean).map((s) => div().flex_none().text_color(tone(s)).child(s)));
}

// herdr-env: 行末の状態。""",
      "prTail")
patch('          .when(Boolean(stateMark(t, w.status, this.marks?.get(w.id))), ',
      '          .when(Boolean(this.marks?.get(w.id)?.pr), (el) => el.child(prTail(t, this.marks.get(w.id).pr)))\n'
      '          .when(Boolean(stateMark(t, w.status, this.marks?.get(w.id))), ',
      "row pr")

open(path, "w", encoding="utf-8").write(src)
with open(os.path.join(dest, "mod.json"), "w", encoding="utf-8") as f:
    json.dump({"place": "none", "title": "Spaces (herdr-env)"}, f)
print(f"spaces-env: {dest}")
