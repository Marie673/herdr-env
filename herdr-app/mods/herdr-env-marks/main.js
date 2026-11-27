// herdr-env の印（外部待ち ⏳ / 人間の返事待ち 💬）を出す小さなカード。
//
// herdr-wait と Claude Code の hooks（herdr-activity.sh）が、各ターミナルに hc のラベルを付ける:
//   herdr-env.human = 旗を立てた epoch（返事待ち）
//   herdr-env.wait  = 待ちのラベル / herdr-env.since = 開始 epoch / herdr-env.pid = 結びついた PID
// GUI の agents 一覧は idle / done としか言えないので、「もう用は無い」と「外部の返事待ち」
// 「人間の返事待ち」を見分けられるよう、印のある agent だけをここに並べる。
// 印が無いときは何も描かない（浮かせて置くと、空のときは消える）。
//
// その下に、Claude のアカウントごとの上限の残り（5h / 7d）を出す。claude-account が statusLine から
// 各ターミナルに付けるラベルを読む: herdr-env.acct_id / herdr-env.acct（表示名）/ herdr-env.lim / herdr-env.lim_at。
// 上限はアカウント単位なので、同じアカウントのターミナルのうち一番新しい値を使う（リセット時刻 herdr-env.lim_rs と
// 残りで決める）。6時間たった値は出さない。
// 下部のステータスバーに利用量を出す mod（workspace-status-env）を入れているときは、重ねて出さない。
//
// キーの受け口（keys.json から `herdr-env-marks.<command>`）:
//   accounts … アカウントを選んで起動 / フォーカス中の会話を別アカウントで続ける（claude-accounts のピッカー）
//   shelve   … フォーカス中の agent のセッションを棚にしまう（herdr-shelf）
//   restore  … 棚から開き直す（herdr-shelf restore）
//   model    … フォーカス中の agent（Claude Code / Codex / Antigravity CLI）のモデル選択を開く（agent-model.py）。
//              GUI では cmd+p がピッカーに取られて Claude Code のモデル切り替えが押せないための代わり

import { View, div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { terminals, agents, theme, run, exec, checking, nextCommand, focused, notify, home, readTextFile } from "herdr";

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
    // 利用量をほかで出しているか（下部のステータスバーの mod があるか）
    cx.spawn(async (cx) => {
      try { await readTextFile(`${home()}/.config/herdr-app/mods/workspace-status-env/mod.json`); this.usageElsewhere = true; } catch { this.usageElsewhere = false; }
      cx.notify();
    });
    // 経過時間の表示と、--pid の印の生死を1分ごとに見直す
    cx.timer.every(60000, (cx) => this.checkPids(cx));
    cx.spawn(async (cx) => {
      for (;;) {
        const c = await nextCommand();
        try { await this.command(c); } catch (e) { notify({ title: "herdr-env", body: String(e), level: "error" }); }
        cx.notify();
      }
    });
  }

  // フォーカス中のペインの下に、終わると閉じるターミナルを開く（ピッカー用）
  below(env, script) {
    const f = focused();
    const argv = ["/usr/bin/env", ...env, "/bin/bash", "-lc", script];
    run("pane.split", f?.pane ? { direction: "down", pane: f.pane, argv } : { direction: "down", argv });
  }

  async command(c) {
    const term = focused()?.terminal ?? "";
    if (c === "accounts") {
      this.below([`CLAUDE_ACCOUNT_ORIGIN_TERMINAL=${term}`], 'exec "$HOME/.config/herdr/plugins/local/claude-accounts/bin/pick.sh"');
    } else if (c === "restore") {
      this.below([], 'herdr-shelf restore || read -r -p "Enter で閉じます" _');
    } else if (c === "model") {
      if (!term) return;
      const r = await exec("/usr/bin/python3", [`${home()}/.config/herdr/bin/agent-model.py`, term], { timeout: 15000 });
      // 開けたときはその agent の画面に選択が出るので、知らせるのは開けなかったときだけ
      if (r.code !== 0) notify({ title: "モデルを切り替えられません", body: (r.stdout || r.stderr || "").trim(), level: "warning" });
    } else if (c === "shelve") {
      if (!term) return;
      const r = await exec("/bin/bash", ["-lc", `herdr-shelf shelve --terminal ${term}`], { timeout: 30000 });
      const out = (r.stdout || r.stderr || "").trim();
      notify({ title: r.code === 0 ? "棚にしまいました" : "棚にしまえませんでした", body: out, level: r.code === 0 ? "info" : "error" });
    }
  }

  // アカウントごとの上限の残り
  usage() {
    const now = Date.now() / 1000;
    // ラベルは会話ごとに、その会話が最後に API を呼んだときの値。手の空いた会話も古い値のまま
    // 描き直す（lim_at は新しくなる）ので、lim_at では選ばない。使った量はリセットまで減らないので、
    // 枠ごとに「リセット時刻（herdr-env.lim_rs）が遅い方、同じなら残りが少ない方」を新しい値とする
    const best = new Map();
    for (const t of terminals()) {
      const l = t.labels ?? {};
      const id = l["herdr-env.acct_id"], at = Number(l["herdr-env.lim_at"] || 0);
      if (!id || !l["herdr-env.lim"] || !at || now - at > TTL) continue;
      const rs = Object.fromEntries([...String(l["herdr-env.lim_rs"] ?? "").matchAll(/(\S+):(\d+)/g)].map((m) => [m[1], Number(m[2])]));
      const acc = best.get(id) ?? { id, name: l["herdr-env.acct"] || id, wins: new Map() };
      best.set(id, acc);
      for (const m of String(l["herdr-env.lim"]).matchAll(/(\S+)\s+(\d+)%/g)) {
        const w = { label: m[1], remaining: Number(m[2]), reset: rs[m[1]] ?? 0 };
        if (w.reset && w.reset <= now) continue;
        const key = (x) => [Math.round(x.reset / 3600), -x.remaining];
        const old = acc.wins.get(w.label);
        const [a1, a2] = key(w), [b1, b2] = old ? key(old) : [-1, 0];
        if (!old || a1 > b1 || (a1 === b1 && a2 > b2)) acc.wins.set(w.label, w);
      }
    }
    return [...best.values()]
      .map((a) => ({ id: a.id, name: a.name, lim: ["5h", "7d"].filter((k) => a.wins.has(k)).map((k) => `${k} ${a.wins.get(k).remaining}%`).join("  ") }))
      .filter((a) => a.lim)
      .sort((x, y) => x.name.localeCompare(y.name));
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
    const usage = this.usageElsewhere === false ? this.usage() : [];
    if (!list.length && !usage.length) return div();
    const t = theme();
    const now = Date.now() / 1000;
    // 残りの少ない方で色を決める（20% 以下は赤、50% 以下は黄）
    const tone = (lim) => {
      const low = Math.min(...[...lim.matchAll(/(\d+)%/g)].map((m) => Number(m[1])));
      return low <= 20 ? t.blocked : low <= 50 ? t.working : t.dim;
    };
    const usageRows = usage.map((u) => h_flex()
      .id(`usage-${u.id}`)
      .px(10)
      .h(22)
      .gap(8)
      .items_center()
      .text_size(11.5)
      .child(div().flex_1().min_w(0).truncate().text_color(t.dim).child(u.name))
      .child(div().flex_none().text_color(tone(u.lim)).child(u.lim)));
    return v_flex()
      .w(244)
      .py(4)
      .gap(1)
      .text_size(12)
      .children(usageRows)
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
