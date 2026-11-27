// herdr-env: 下部のステータスバー。TUI で使っていた Agent Usage（usagebar）の見た目に寄せた利用量の表示。
//
//   バー:   This computer · ブランチ · N agents │ 🟦 Work 5h ▬▬▬ 73% 2h10m  7d ▬▬▬ 81% · Antigravity … · Codex …
//   クリック: Agent Usage と同じ並び（見出し「Claude · Work」、横長のバー、「73% left」、リセットまでの時間）のポップアップ。
//            ⊞ で右のペインに固定できる
//
// データは ~/.config/herdr/bin/ai-usage-collect.py（各ツールのキャッシュを読む）と、
// claude-account が hc のラベル herdr-env.lim に書く Claude の残り（集計に無いアカウントだけ）から。
// 残り（% left）は多いほど安全。50% 以下は黄、20% 以下は赤。

import { View, div, svg, image } from "gpui-kit";
import { h_flex, v_flex } from "gpui-base";
import {
  spaces,
  agents,
  terminals,
  theme,
  settings,
  run,
  exec,
  home,
  window,
  keys,
  props,
  openOverlay,
  closeOverlay,
  checking,
  nextCommand,
} from "herdr";

const TTL = 6 * 3600;
const REFRESH_MS = 60000;

const mono = () => settings()?.font_family ?? "JetBrains Mono";

// ツールのアイコン（Herdr 付属の ~/.config/herdr-app/icons/）。Gemini だけ単色なので文字色で塗る
const ICON = {
  claude: { path: "icons/claude.svg", tint: false },
  antigravity: { path: "icons/antigravity.svg", tint: false },
  codex: { path: "icons/codex.svg", tint: false },
  gemini: { path: "icons/gemini.svg", tint: true },
};
function toolIcon(key, size, color) {
  const spec = ICON[key];
  if (!spec) return null;
  return spec.tint ? svg(spec.path).size(size).flex_none().text_color(color) : image(spec.path).w(size).h(size).flex_none();
}

// アカウントの色（claude-account の印 🟦 🟩 🟪 … をテーマの色に置き換えて、小さな点で出す）
const BADGE_COLOR = { "🟦": "ansi4", "🟩": "ansi2", "🟪": "ansi5", "🟥": "ansi1", "🟨": "ansi3", "🟧": "ansi3" };
function accountDot(t, badge, size = 7) {
  const c = t[BADGE_COLOR[badge]] ?? t.dim;
  return div().size(size).flex_none().rounded(size / 2).bg(c);
}

function ago(epochSec) {
  if (!epochSec) return "";
  const m = Math.floor(Math.max(0, Date.now() / 1000 - epochSec) / 60);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  return h < 24 ? `${h}h ago` : `${Math.floor(h / 24)}d ago`;
}

function tone(t, pct) {
  if (pct === null || pct === undefined) return t.dim;
  // TUI の usagebar と同じく 緑 / 黄 / 赤（テーマの done は青のことがあるので ANSI の色を使う）
  if (pct <= 20) return t.ansi1;
  if (pct <= 50) return t.ansi3;
  return t.ansi2;
}

function parseLimWindows(limStr) {
  const out = [];
  for (const m of String(limStr ?? "").matchAll(/(\S+)\s+(\d+)%/g)) {
    out.push({ label: m[1], remaining: Math.max(0, Math.min(100, Number(m[2]))), countdown: "" });
  }
  return out;
}

function hhmm(epochSec) {
  if (!epochSec) return "—";
  const d = new Date(epochSec * 1000);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

// 細い角丸のバー: 薄い同系色の溝に、残りの分を塗る
function bar(t, pct, w, h) {
  const p = pct === null || pct === undefined ? 0 : Math.max(0, Math.min(100, pct));
  const c = tone(t, pct);
  const fill = p > 0 ? Math.max(h, Math.round((w * p) / 100)) : 0;
  return div()
    .w(w)
    .h(h)
    .flex_none()
    .rounded(h / 2)
    .bg(c + "2a")
    .overflow_hidden()
    .when(fill > 0, (el) => el.child(div().w(fill).h(h).rounded(h / 2).bg(c)));
}

// 画面に出す単位（Claude はアカウントごと、ほかはツールごと）に並べ直す
function blocks(m) {
  const out = [];
  for (const a of m.claude.accounts) {
    out.push({
      key: `claude-${a.id}`,
      icon: "claude",
      title: a.name,
      badge: a.badge,
      sub: "",
      short: a.name,
      windows: a.windows,
      // バーでは「7d Fable」を「Fable」と短く
      compact: a.windows.map((w) => ({ ...w, label: w.label.replace(/^7d (?=\S)/, "") })),
      updatedAt: a.updated_at,
    });
  }
  if (m.antigravity.windows.length || m.antigravity.activePanes) {
    out.push({
      key: "antigravity",
      icon: "antigravity",
      title: "Antigravity",
      sub: m.antigravity.plan,
      short: "Antigravity",
      // バーには Gemini の枠だけ（3P は使ったときだけ減るので詳細で見る）
      windows: m.antigravity.windows.map((w) => ({ ...w, label: w.label.replace(/^3P \([^)]*\)/, "3P") })),
      compact: m.antigravity.windows.filter((w) => !w.group || w.group === "Gemini").map((w) => ({ ...w, label: w.window ?? w.label })),
      note: m.antigravity.model ? `model  ${m.antigravity.model}` : "",
      updatedAt: m.antigravity.updatedAt,
    });
  }
  if (m.codex.windows.length || m.codex.activePanes) {
    out.push({ key: "codex", icon: "codex", title: "Codex", sub: m.codex.plan, short: "Codex", windows: m.codex.windows, updatedAt: m.codex.updatedAt });
  }
  if (m.gemini.tokensToday > 0 || m.gemini.activePanes) {
    out.push({
      key: "gemini",
      icon: "gemini",
      title: "Gemini CLI",
      sub: m.gemini.oauth ? "OAuth" : "API",
      short: "Gemini",
      windows: [],
      note: `today  ${m.gemini.tokensTodayStr} tokens · ${m.gemini.sessionsToday} sessions`,
      compactText: m.gemini.tokensToday > 0 ? `${m.gemini.tokensTodayStr} today` : "",
      updatedAt: m.gemini.updatedAt,
    });
  }
  return out;
}

export default class WorkspaceStatusEnv extends View {
  init(_props, cx) {
    // ポップアップ / ペインは別のインスタンス。集計を待たずに出せるよう、バーが持っている値を受け取る
    this.disk = props()?.disk ?? null;
    this.loading = false;
    if (checking()) return;
    this.refresh(cx);
    cx.timer.every(REFRESH_MS, (cx) => this.refresh(cx));
    // キー / herdr-app do から: workspace-status-env.usage で詳細を開く（もう一度で閉じる）
    if (!props()?.detail) {
      cx.spawn(async (cx) => {
        for (;;) {
          const c = await nextCommand();
          if (c === "usage") {
            if (this.overlay) { closeOverlay(this.overlay); this.overlay = null; } else this.openDetail();
          }
          cx.notify();
        }
      });
    }
  }

  refresh(cx) {
    if (this.loading) return;
    this.loading = true;
    const script = `${home()}/.config/herdr/bin/ai-usage-collect.py`;
    cx.spawn(async (cx) => {
      try {
        const r = await exec("/usr/bin/python3", [script, "--json", "--sync-hc"], { timeout: 8000 });
        if (r.code === 0 && r.stdout.trim()) {
          this.disk = JSON.parse(r.stdout);
        }
      } catch {
        // Keep last good snapshot if collector fails temporarily.
      } finally {
        this.loading = false;
        cx.notify();
      }
    });
  }

  hint() {
    const mac = window()?.platform === "macos";
    const mod = { cmd: mac ? "⌘" : "Ctrl+", ctrl: mac ? "⌃" : "Ctrl+", shift: mac ? "⇧" : "Shift+", alt: mac ? "⌥" : "Alt+", option: mac ? "⌥" : "Alt+" };
    const b = keys().find((k) => k.section === "global" && k.action === "app.palette");
    const parts = b && b.keys.endsWith("--") ? [...b.keys.slice(0, -2).split("-"), "-"] : b?.keys.split("-");
    const key = parts && parts.map((p) => mod[p.toLowerCase()] ?? p.toUpperCase()).join("");
    return key ? `${key}  commands` : "commands";
  }

  // Merge live hc labels from terminals() with disk snapshots so both the status bar and detail view stay current.
  buildModel() {
    const now = Date.now() / 1000;
    const allTerms = terminals().filter((x) => x.state === "live");
    const counts = { claude: 0, antigravity: 0, codex: 0, gemini: 0 };
    const liveClaude = new Map();
    let liveAgy = null;
    let liveCodex = null;

    for (const term of allTerms) {
      const k = (term.program?.key || term.agent || "").toLowerCase();
      if (k.includes("claude")) counts.claude += 1;
      else if (k.includes("antigravity") || k === "agy") counts.antigravity += 1;
      else if (k.includes("codex")) counts.codex += 1;
      else if (k.includes("gemini")) counts.gemini += 1;

      const l = term.labels ?? {};
      const id = l["herdr-env.acct_id"];
      const lim = l["herdr-env.lim"];
      const at = Number(l["herdr-env.lim_at"] || 0);
      if (!id || !lim || !at || now - at > TTL) continue;
      const entry = { id, name: l["herdr-env.acct"] || id, lim, at };
      if (id === "antigravity") {
        if (!liveAgy || at > liveAgy.at) liveAgy = entry;
      } else if (id === "codex") {
        if (!liveCodex || at > liveCodex.at) liveCodex = entry;
      } else {
        if ((liveClaude.get(id)?.at ?? 0) < at) liveClaude.set(id, entry);
      }
    }

    const byKey = new Map((this.disk?.tools ?? []).map((x) => [x.key, x]));

    // 1. Claude (merge disk accounts with fresher live hc labels, preserving reset countdowns from disk)
    const diskClaude = byKey.get("claude");
    const claudeMap = new Map();
    for (const a of diskClaude?.accounts ?? []) {
      claudeMap.set(a.id, {
        id: a.id,
        badge: a.badge,
        name: a.name,
        windows: [...(a.windows ?? [])],
        updated_at: a.updated_at,
      });
    }
    for (const [id, live] of liveClaude.entries()) {
      const parsed = parseLimWindows(live.lim);
      const prev = claudeMap.get(id);
      // 集計（ai-usage-collect.py）が値を持っていればそちらだけを使う。ラベルは会話ごとに最後に API を
      // 呼んだときの値で、手の空いた会話も古い値のまま lim_at を新しくするので、時刻で選ぶと値が入れ替わり続ける
      // （集計は会話ごとの値から、リセット時刻と残りで新しいものを選んでいる）
      const mergedWins = prev?.windows?.length ? prev.windows : parsed;
      const cleanName = live.name.replace(/^[^\p{L}\p{N}]+/u, "").trim() || live.name;
      const badge = prev?.badge || [...live.name][0] || "🟦";
      claudeMap.set(id, {
        id,
        badge,
        name: prev?.name || cleanName,
        windows: mergedWins,
        updated_at: Math.max(live.at, prev?.updated_at ?? 0),
      });
    }
    const claudeAccounts = [...claudeMap.values()];
    const claudeActive = claudeAccounts.filter((a) => a.windows.length > 0);
    const claudeLowest = claudeActive.length
      ? Math.min(...claudeActive.flatMap((a) => a.windows.map((w) => w.remaining)))
      : null;

    // 2. Antigravity
    const diskAgy = byKey.get("antigravity");
    const agyWindows =
      diskAgy?.windows?.length
        ? diskAgy.windows
        : liveAgy
          ? parseLimWindows(liveAgy.lim)
          : [];
    const agyLowest = agyWindows.length ? Math.min(...agyWindows.map((w) => w.remaining)) : null;

    // 3. Codex
    const diskCodex = byKey.get("codex");
    const codexWindows =
      diskCodex?.windows?.length
        ? diskCodex.windows
        : liveCodex
          ? parseLimWindows(liveCodex.lim)
          : [];
    const codexLowest = codexWindows.length ? Math.min(...codexWindows.map((w) => w.remaining)) : null;

    // 4. Gemini CLI
    const diskGemini = byKey.get("gemini");

    return {
      updatedAt: this.disk?.updated_at ?? null,
      claude: {
        key: "claude",
        name: "Claude",
        activePanes: counts.claude,
        accounts: claudeAccounts,
        lowest: claudeLowest,
        compactText: claudeLowest !== null ? `${claudeLowest}%` : "—",
        tooltip:
          (claudeActive.length
            ? claudeActive
                .map((a) => `${a.badge} ${a.name}: ${a.windows.map((w) => `${w.label} ${w.remaining}%`).join(" / ")}`)
                .join("\n")
            : "Claude: データ待機中") + "\nクリックで詳細を表示",
      },
      antigravity: {
        key: "antigravity",
        name: "Antigravity",
        plan: diskAgy?.plan ?? "",
        model: diskAgy?.model ?? "",
        activePanes: counts.antigravity,
        windows: agyWindows,
        costUsd: diskAgy?.cost_usd ?? null,
        lowest: agyLowest,
        updatedAt: diskAgy?.updated_at ?? liveAgy?.at ?? null,
        compactText: agyLowest !== null ? `${agyLowest}%` : "—",
        tooltip: (diskAgy?.tooltip ?? "Antigravity: agy 起動後に自動取得") + "\nクリックで詳細を表示",
      },
      codex: {
        key: "codex",
        name: "Codex",
        plan: diskCodex?.plan ?? "",
        activePanes: counts.codex,
        windows: codexWindows,
        lowest: codexLowest,
        updatedAt: diskCodex?.updated_at ?? liveCodex?.at ?? null,
        compactText: codexLowest !== null ? `${codexLowest}%` : "—",
        tooltip: (diskCodex?.tooltip ?? "Codex: データなし") + "\nクリックで詳細を表示",
      },
      gemini: {
        key: "gemini",
        name: "Gemini CLI",
        activePanes: counts.gemini,
        oauth: Boolean(diskGemini?.oauth),
        tokensToday: diskGemini?.tokens_today ?? 0,
        tokensTodayStr: diskGemini?.tokens_today_str ?? "0",
        sessionsToday: diskGemini?.sessions_today ?? 0,
        lowest: null,
        updatedAt: diskGemini?.updated_at ?? null,
        compactText: diskGemini?.text ?? "—",
        tooltip: (diskGemini?.tooltip ?? "Gemini CLI: データなし") + "\nクリックで詳細を表示",
      },
    };
  }

  openDetail() {
    // ポップアップは中身の高さに縮むので、並べる行数から高さを決めて渡す
    const bs = blocks(this.buildModel());
    const body = bs.reduce((h, b) => h + 22 + Math.max(1, b.windows.length) * 26 + (b.note ? 20 : 0), 0) + Math.max(0, bs.length - 1) * 16;
    const height = Math.min(640, 28 + 24 + 12 + body + 12 + 13 + 12 + 20);
    this.overlay = openOverlay({ mod: "workspace-status-env", kind: "popup", width: 560, height, at: "pointer", props: { detail: true, disk: this.disk } });
  }

  // Agent Usage の1行: 「5h  ▬▬▬▬▬▬▬▬▬▬▬  60% left   56m」
  usageRow(t, id, w) {
    const pct = w.remaining;
    return h_flex()
      .id(id)
      .h(24)
      .gap(12)
      .items_center()
      .pl(23)
      .child(div().w(110).flex_none().truncate().text_color(t.foreground).child(w.label))
      .child(bar(t, pct, 200, 6))
      .child(div().w(80).flex_none().text_right().text_color(tone(t, pct)).child(`${pct}% left`))
      .child(div().flex_1().min_w(0).truncate().text_color(t.dim).child(w.countdown ? `↻ ${w.countdown}` : ""));
  }

  renderDetail(_cx) {
    const t = theme();
    const p = props() ?? {};
    const m = this.buildModel();
    const isPopup = Boolean(p.overlay);
    const sections = [];
    for (const b of blocks(m)) {
      const rows = [];
      rows.push(
        h_flex()
          .id(`h-${b.key}`)
          .gap(8)
          .items_center()
          .child(toolIcon(b.icon, 15, t.foreground))
          .child(div().font_bold().text_color(t.foreground).child(b.title))
          .when(Boolean(b.badge), (el) => el.child(accountDot(t, b.badge)))
          .when(Boolean(b.sub), (el) => el.child(div().text_color(t.dim).child("·")).child(div().text_color(t.foreground).child(b.sub)))
          .child(div().flex_1())
          .when(Boolean(b.updatedAt), (el) => el.child(div().text_size(11).text_color(t.dim).child(ago(b.updatedAt)))),
      );
      for (const w of b.windows) rows.push(this.usageRow(t, `r-${b.key}-${w.key ?? w.label}`, w));
      if (b.note) rows.push(div().pl(16).text_color(t.dim).child(b.note));
      if (!b.windows.length && !b.note) rows.push(div().pl(16).text_color(t.dim).child("まだ取得していません（そのアカウントで1ターン動かすと出ます）"));
      sections.push(v_flex().id(`s-${b.key}`).gap(2).children(rows));
    }

    const btn = (id, label, tip, onClick) =>
      div().id(id).px(6).rounded(4).text_color(t.dim).cursor_pointer()
        .hover((s) => s.bg(t.hover).text_color(t.foreground)).tooltip(tip).on_click(onClick).child(label);

    return v_flex()
      .w_full()
      .when(!isPopup, (el) => el.size_full().min_h(0))
      .bg(isPopup ? t.popover : t.background)
      .p(14)
      .gap(12)
      .font_family(mono())
      .text_size(12.5)
      .text_color(t.foreground)
      .child(
        h_flex()
          .gap(10)
          .items_center()
          .child(div().text_color(t.dim).child("Agent Usage"))
          .child(div().flex_1().h(1).bg(t.border))
          .when(isPopup, (el) => el.child(btn("usage-close", "✕", "閉じる (Esc)", () => closeOverlay(p.overlay)))),
      )
      // 高さいっぱいに広げると、ポップアップでは高さ0に潰れて何も見えなくなる。ペインのときだけ広げてスクロールさせる
      .child(
        v_flex()
          .id("usage-scroll")
          .gap(16)
          .when(!isPopup, (el) => el.flex_1().min_h(0).overflow_y_scrollbar())
          .when(isPopup, (el) => el.flex_none())
          .children(sections),
      )
      .child(div().h(1).bg(t.border))
      .child(
        h_flex()
          .gap(10)
          .items_center()
          .text_color(t.dim)
          .child(div().child(`Updated ${hhmm(m.updatedAt)}`))
          .child(div().child("·"))
          .child(btn("usage-refresh", this.loading ? "…" : "r refresh", "取り直す", (_e, cx) => this.refresh(cx)))
          .when(isPopup, (el) =>
            el.child(div().child("·")).child(
              btn("usage-pin", "⊞ pane", "右のペインに固定する", () => {
                closeOverlay(p.overlay);
                run("pane.split", { direction: "right", mod: "workspace-status-env", props: { detail: true, inPane: true, disk: this.disk } });
              }),
            ),
          ),
      );
  }

  // バーの1区切り（チップ）: [アイコン Work  ━━━  96%  4h25m]
  segment(t, b) {
    // バーには1枠だけ: 5h があれば 5h、無ければ 7d。ほかの枠が 20% 以下まで減っていたらそちらを出す
    const all = b.compact ?? b.windows;
    const pick = all.find((w) => w.label === "5h") ?? all.find((w) => w.label === "7d") ?? all[0];
    const low = all.filter((w) => w.remaining <= 20).sort((x, y) => x.remaining - y.remaining)[0];
    const w = low && pick && low.remaining < pick.remaining ? low : pick;
    const tip = [`${b.title}${b.sub ? " · " + b.sub : ""}`, ...b.windows.map((x) => `  ${x.label}  ${x.remaining}% left${x.countdown ? "  ↻ " + x.countdown : ""}`), ...(b.note ? ["  " + b.note] : [])].join("\n");
    return h_flex()
      .id(`seg-${b.key}`)
      .pl(6)
      .pr(8)
      .h(20)
      .gap(6)
      .rounded(10)
      .items_center()
      .bg(t.foreground + "0d")
      .cursor_pointer()
      .hover((s) => s.bg(t.hover))
      .tooltip(tip + "\nクリックで詳細")
      .on_click(() => this.openDetail())
      .child(toolIcon(b.icon, 12, t.foreground))
      .child(div().font_medium().text_color(t.foreground).child(b.short))
      .when(Boolean(w), (el) =>
        el
          .child(div().text_size(10.5).text_color(t.dim).child(w.label))
          .child(bar(t, w.remaining, 40, 4))
          .child(div().font_medium().text_color(tone(t, w.remaining)).child(`${w.remaining}%`))
          .when(Boolean(w.countdown), (el) => el.child(div().text_size(10.5).text_color(t.dim).child(w.countdown))),
      )
      .when(Boolean(b.compactText), (el) => el.child(div().text_color(t.dim).child(b.compactText)));
  }

  render(cx) {
    if (props()?.detail) return this.renderDetail(cx);

    const t = theme();
    const space = spaces().find((s) => s.active);
    const list = agents();
    const needs = list.filter((a) => a.status === "blocked").length;
    const label = (icon, text) => h_flex().gap(6).items_center().child(svg(`mods/workspace-chrome/${icon}.svg`).size(13)).child(text);
    // データの無いもの（使っていないアカウント等）はバーに出さない
    const segs = blocks(this.buildModel()).filter((b) => (b.compact ?? b.windows).length || b.compactText);

    return h_flex()
      .size_full()
      .px(14)
      .gap(14)
      .items_center()
      .bg(t.chrome)
      .border_t(1)
      .border_color(t.border)
      .text_size(11.8)
      .text_color(t.muted)
      .child(label(space?.machine ? "server" : "monitor", space?.machine ?? "This computer"))
      .when(Boolean(space?.branch), (el) => el.child(label("branch", space.branch)))
      .child(
        h_flex()
          .gap(6)
          .items_center()
          .child(div().size(6).rounded(3).bg(t.dim))
          .child(`${list.length} agents`)
          .when(needs > 0, (el) => el.child(div().text_color(t.blocked).child(`· ${needs} needs you`))),
      )
      .when(segs.length > 0, (el) =>
        el.child(div().w(1).h(12).bg(t.border)).child(
          h_flex()
            .id("usage-strip")
            .min_w(0)
            .gap(6)
            .items_center()
            .text_size(11.5)
            .children(segs.map((b) => this.segment(t, b))),
        ),
      )
      .child(div().flex_1())
      .child(div().id("palette").text_color(t.dim).on_click(() => run("app.palette")).child(this.hint()));
  }
}
