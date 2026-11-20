// herdr-env の印（外部待ち ⏳ / 人間の返事待ち 💬）を出す小さなカード。
//
// herdr-wait と Claude Code の hooks（herdr-activity.sh）が、各ターミナルに hc のラベルを付ける:
//   herdr-env.human = 旗を立てた epoch（返事待ち）
//   herdr-env.wait  = 待ちのラベル / herdr-env.since = 開始 epoch / herdr-env.pid = 結びついた PID
// GUI の agents 一覧は idle / done としか言えないので、「もう用は無い」と「外部の返事待ち」
// 「人間の返事待ち」を見分けられるよう、印のある agent だけをここに並べる。
// 印が無いときは何も描かない（浮かせて置くと、空のときは消える）。

import { View, div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { terminals, agents, theme, run, exec, checking } from "herdr";

const TTL = 6 * 3600; // herdr-wait と同じ。消し忘れても居座らない

// agent がタイトルの前に付ける状態記号やスピナーを落とす（agents 一覧と同じ考え方）
const clean = (s) => (s ?? "").replace(/^[^\p{L}\p{N}\p{Ps}\p{Pi}]+/u, "").trim();

function elapsed(sec) {
  const m = Math.max(0, Math.floor(sec / 60));
  return m < 60 ? `${m}分` : `${Math.floor(m / 60)}時間${m % 60}分`;
}

export default class HerdrEnvMarks extends View {
  init(_props, cx) {
    this.dead = new Set(); // 待っていたプロセスが終わった PID
    if (checking()) return;
    // 経過時間の表示と、--pid の印の生死を1分ごとに見直す
    cx.timer.every(60000, (cx) => this.checkPids(cx));
  }

  marks() {
    const now = Date.now() / 1000;
    const byAddr = new Map(agents().map((a) => [a.address, a]));
    const out = [];
    for (const t of terminals()) {
      const l = t.labels ?? {};
      if (t.state !== "live") continue;
      const human = Number(l["herdr-env.human"] || 0);
      const since = Number(l["herdr-env.since"] || 0);
      const pid = l["herdr-env.pid"] || "";
      const waiting = Boolean(l["herdr-env.wait"]) && since > 0 && now - since < TTL && !(pid && this.dead.has(pid));
      const asking = human > 0 && now - human < TTL;
      if (!waiting && !asking) continue;
      const a = byAddr.get(t.address);
      // 人間の返事待ちは、agent が次のターンを始めたら（working）出さない
      const humanTurn = asking && a?.status !== "working";
      if (!humanTurn && !waiting) continue;
      out.push({
        address: t.address,
        pane: a?.pane,
        where: a ? clean(a.title) || a.tab_name || a.space_name : (l.where ?? t.address),
        human: humanTurn,
        text: humanTurn ? "💬 要返答" : `⏳ ${l["herdr-env.wait"]}`,
        since: humanTurn ? human : since,
        pid,
      });
    }
    // 人間の返事待ちを上に
    return out.sort((x, y) => (y.human - x.human) || (x.since - y.since));
  }

  checkPids(cx) {
    const pids = [...new Set(this.marks().map((m) => m.pid).filter(Boolean))];
    if (!pids.length) { cx.notify(); return; }
    cx.spawn(async (cx) => {
      for (const p of pids) {
        try {
          const r = await exec("/bin/kill", ["-0", p], { timeout: 3000 });
          if (r.code !== 0) this.dead.add(p);
        } catch { /* 確かめられないときは出したままにする */ }
      }
      cx.notify();
    });
  }

  render(_cx) {
    const list = this.marks();
    if (!list.length) return div();
    const t = theme();
    const now = Date.now() / 1000;
    return v_flex()
      .w(244)
      .py(4)
      .gap(1)
      .text_size(12)
      .children(list.map((m) => h_flex()
        .id(`mark-${m.address}`)
        .px(10)
        .h(26)
        .gap(8)
        .items_center()
        .rounded(6)
        .cursor_pointer()
        .hover((s) => s.bg(t.hover))
        .on_click(() => m.pane ? run("pane.focus", { pane: m.pane }) : run("terminal.peek", { terminal: m.address }))
        .child(div().flex_none().max_w(130).truncate().text_color(m.human ? t.blocked : t.working).child(m.text))
        .child(div().flex_1().min_w(0).truncate().text_color(t.dim).child(m.where))
        .child(div().flex_none().text_size(11).text_color(t.dim).child(elapsed(now - m.since)))));
  }
}
