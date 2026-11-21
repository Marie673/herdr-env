// issue-board（TUI のローカルプラグイン）の GUI 版の入口。
//
// 対象リポジトリの main の Space に来たら、今のタブの右に issue / PR 一覧（fzf の board.sh）を開く。
// 一覧そのものは TUI と同じスクリプトをターミナルで動かすだけ。board.sh が自分のターミナルに
// hc のラベル herdr-env.board=1 を付けるので、それでタブに一覧があるかを見る。
//
//   - 対象: ~/.config/herdr/plugins/config/issue-board/config.json の repos（TUI と共有。"*" で全部）
//   - main の Space だけ（ワークツリーの Space には出さない）
//   - 一覧を閉じると、その Space では自動で開かなくなる。issue-board-env.toggle（cmd+shift+i）で戻す
//   - 何も描かない（レイアウトには浮かせて置いてあるだけ）

import { View, div } from "gpui-kit";
import { focused, spaces, terminals, panes, run, exec, home, readTextFile, nextCommand, checking } from "herdr";

const BOARD = () => `${home()}/.config/herdr/plugins/local/issue-board/bin/board.sh`;
const CONFIG = () => `${home()}/.config/herdr/plugins/config/issue-board/config.json`;
const base = (p) => p.replace(/\/+$/, "").split("/").pop();

export default class IssueBoardEnv extends View {
  init(_props, cx) {
    this.isMain = new Map(); // folder -> true / false（.git がディレクトリなら main のチェックアウト）
    this.pending = new Map(); // tab -> 開きに行った時刻
    this.opened = new Map(); // tab -> 一覧のターミナル
    try { this.hidden = new Set(JSON.parse(localStorage.getItem("hidden") ?? "[]")); } catch { this.hidden = new Set(); }
    this.repos = null;
    if (checking()) return;
    cx.spawn(async (cx) => {
      try { this.repos = JSON.parse(await readTextFile(CONFIG())).repos ?? []; } catch { this.repos = []; }
      cx.notify();
    });
    cx.spawn(async (cx) => {
      for (;;) {
        const c = await nextCommand();
        if (c === "toggle") this.toggle();
        cx.notify();
      }
    });
    cx.timer.every(1000, (cx) => this.tick(cx));
  }

  saveHidden() {
    localStorage.setItem("hidden", JSON.stringify([...this.hidden]));
  }

  boards() {
    return terminals().filter((t) => t.state === "live" && t.labels?.["herdr-env.board"]);
  }

  toggle() {
    const sp = focused()?.space;
    if (!sp) return;
    if (this.hidden.has(sp)) {
      this.hidden.delete(sp);
    } else {
      this.hidden.add(sp);
      const tab = focused()?.tab;
      for (const b of this.boards().filter((b) => b.labels["herdr.tab"] === tab)) {
        const p = panes().find((p) => p.terminal === b.address);
        if (p) run("pane.close", { pane: p.pane });
      }
    }
    this.saveHidden();
  }

  tick(cx) {
    if (!this.repos?.length) return;
    const f = focused();
    if (!f?.space || !f.tab || !f.pane) return;
    const sp = spaces().find((s) => s.id === f.space);
    if (!sp?.folder || sp.machine) return;
    if (!this.repos.includes("*") && !this.repos.includes(base(sp.folder))) return;

    const boards = this.boards();
    const live = new Set(boards.map((b) => b.address));
    const tabs = new Set(spaces().flatMap((s) => s.tabs.map((t) => t.id)));
    // 開いていた一覧が消えた（人が閉じた）タブは、その Space ごと自動で開かないことにする
    for (const [tab, addr] of this.opened) {
      if (live.has(addr)) continue;
      this.opened.delete(tab);
      if (tabs.has(tab)) {
        const owner = spaces().find((s) => s.tabs.some((t) => t.id === tab));
        if (owner) { this.hidden.add(owner.id); this.saveHidden(); }
      }
    }
    for (const b of boards) this.opened.set(b.labels["herdr.tab"], b.address);
    if (this.hidden.has(sp.id)) return;
    if (boards.some((b) => b.labels["herdr.tab"] === f.tab)) return;

    const main = this.isMain.get(sp.folder);
    if (main === undefined) {
      this.isMain.set(sp.folder, null);
      cx.spawn(async (cx) => {
        try {
          const r = await exec("/bin/test", ["-d", `${sp.folder}/.git`], { timeout: 3000 });
          this.isMain.set(sp.folder, r.code === 0);
        } catch { this.isMain.delete(sp.folder); }
        cx.notify();
      });
      return;
    }
    if (!main) return;

    const now = Date.now();
    if (now - (this.pending.get(f.tab) ?? 0) < 15000) return;
    this.pending.set(f.tab, now);
    // 一覧は右に開き、キーボードは元のペインに戻す
    run("pane.split", { direction: "right", pane: f.pane, argv: ["/usr/bin/env", `ISSUE_BOARD_REPO_ROOT=${sp.folder}`, "/bin/bash", BOARD()] });
    run("pane.focus", { pane: f.pane });
  }

  render(_cx) {
    return div();
  }
}
