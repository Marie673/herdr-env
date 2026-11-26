// Issues パネル（herdr-env）。右パネルのスタックに Files / Changes と並べて置く。
//
// 今いる Space（フォーカスしているペインのフォルダ）のリポジトリの open な issue / PR を出す。
// ワークツリーの Space でも、本体のリポジトリと同じ一覧になる（git の common dir でまとめる）。
//
//   - 一覧: ●（ワークツリーあり） #番号 タイトル、ラベル（GitHub の色）、PR は CI / CR / レビューの状態
//   - 行を押すと詳細: 本文とコメント（Markdown、画像も）、PR は checks。上の ← で一覧に戻る
//     チェックボックスを押すと本文を書き換え、リアクションを付け外しし、コメントを書ける（GitHub に書き込む）
//   - issue の「着手」: haiku がブランチ名を提案し、直してから gwt --task で新しい Space の Claude Code に渡す
//   - PR の「開く」: そのブランチの Space へ移る。無ければ Claude Code つき / シェルだけで開く
//
// gh の取得と 30 秒のキャッシュは TUI の issue-board と同じスクリプト（issues.sh / prs.sh --json）を使う。
// パネルが見えていないあいだは取りに行かない。

import { View, div, svg, image } from "gpui-kit";
import { v_flex, h_flex, InputState, Input, TextareaState, Textarea, TextView } from "gpui-base";
import { spaces, panes, focused, exec, run, theme, layout, home, appHome, checking, notify } from "herdr";

const BIN = () => `${home()}/.config/herdr/plugins/local/issue-board/bin`;
const PATHS = 'export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"; exec "$@"';
const STALE = 60000;

function find(node, id) {
  if (node?.id === id) return node;
  for (const child of node?.children ?? []) {
    const found = find(child, id);
    if (found) return found;
  }
  return null;
}

// 右パネルが開いていて、Issues が選ばれているか
function shownNow() {
  const root = layout(), panel = find(root, "panel"), body = find(root, "panel-body");
  return Boolean(panel && !panel.hidden && body?.active === "issues");
}

// 今のフォルダ（このマシンの Space だけ。ほかのマシンの Space では gh / gwt を動かさない）
function location() {
  const space = spaces().find((s) => s.active), f = focused();
  if (!space || space.machine) return { cwd: null, remote: Boolean(space?.machine) };
  const pane = f?.pane ? panes().find((p) => p.pane === f.pane) : null;
  return { cwd: pane?.cwd || space.folder || null, remote: false };
}

// 一覧の取得などに使う。PATH に gh / jq / claude の場所を足してから動かす
const run_sh = (argv, cwd, timeout = 60000) => exec("/bin/bash", ["-c", PATHS, "issues-env", ...argv], { cwd, timeout });

// gwt はシェル関数なので、ログインかつ対話の zsh で呼ぶ。ターミナルの外から呼ぶので HERDR_GUI=1 を付ける。
// HERDR_GUI を知らない古い gwt はスペースを開かずに cd して終わるので、herdr-gui の出力（"gwt: <branch> -> "）で確かめる
async function gwt(root, args) {
  const r = await exec("/usr/bin/env",
    ["-u", "HC_TERMINAL_ID", "-u", "HERDR_ENV", "-u", "HERDR_PANE_ID", "HERDR_GUI=1", "/bin/zsh", "-lic", 'gwt "$@"', "gwt", ...args],
    { cwd: root, timeout: 180000 });
  if (r.code !== 0) throw new Error(lastLine(r.stderr) || lastLine(r.stdout) || "gwt が失敗しました");
  if (!r.stdout.includes(`gwt: ${args[0]} -> `))
    throw new Error("gwt がスペースを開きませんでした。gwt が古いようです（zsh/gwq-herdr.zsh を source し直してください）");
  return r;
}

const lastLine = (s) => (s || "").trim().split("\n").filter(Boolean).pop() ?? "";

function ago(iso) {
  const t = Date.parse(iso || "");
  if (!t) return "";
  const m = Math.max(0, Math.round((Date.now() - t) / 60000));
  if (m < 60) return `${m}分前`;
  if (m < 60 * 24) return `${Math.round(m / 60)}時間前`;
  if (m < 60 * 24 * 30) return `${Math.round(m / 60 / 24)}日前`;
  return iso.slice(0, 10);
}

// ラベルの文字色: 背景が明るければ黒、暗ければ白
function labelFg(hex) {
  const h = /^[0-9a-f]{6}$/i.test(hex || "") ? hex : "888888";
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
  return (r * 299 + g * 587 + b * 114) / 1000 > 140 ? "#1f2328" : "#ffffff";
}

function checkClass(c) {
  if (c.__typename === "CheckRun") {
    if (c.status !== "COMPLETED") return "pending";
    if (c.conclusion === "SUCCESS" || c.conclusion === "NEUTRAL") return "ok";
    return c.conclusion === "SKIPPED" ? "skip" : "fail";
  }
  if (c.state === "PENDING" || c.state === "EXPECTED") return "pending";
  return c.state === "SUCCESS" ? "ok" : "fail";
}
const checksOf = (pr) => pr.commits?.nodes?.[0]?.commit?.statusCheckRollup?.contexts?.nodes ?? [];
const isCR = (c) => (c.name || c.context || "").toLowerCase().includes("coderabbit");

// PR の状態バッジ（TUI の prs.sh と同じ文言）。tone: bad / wait / good / dim
function prBadges(pr) {
  const out = [], ctx = checksOf(pr);
  const ci = ctx.filter((c) => !isCR(c)).map(checkClass), cr = ctx.filter(isCR).map(checkClass);
  const unresolved = (pr.reviewThreads?.nodes ?? []).filter((r) => !r.isResolved).length;
  if (pr.isDraft) out.push({ text: "draft", tone: "dim" });
  if (pr.mergeable === "CONFLICTING") out.push({ text: "競合", tone: "bad" });
  if (ci.includes("fail")) out.push({ text: `CI✗${ci.filter((c) => c === "fail").length}`, tone: "bad" });
  else if (ci.includes("pending")) out.push({ text: "CI…", tone: "wait" });
  else if (ci.includes("ok")) out.push({ text: "CI✓", tone: "good" });
  if (cr.includes("pending")) out.push({ text: "CR…", tone: "wait" });
  else if (cr.includes("ok")) out.push({ text: "CR✓", tone: "good" });
  if (pr.reviewDecision === "CHANGES_REQUESTED") out.push({ text: "要修正", tone: "bad" });
  else if (unresolved > 0) out.push({ text: `未解決${unresolved}`, tone: "wait" });
  else if (pr.reviewDecision === "APPROVED") out.push({ text: "承認", tone: "good" });
  return out;
}

// ---- 本文の画像 ----
// TextView の Markdown は画像を描かないので、本文を画像の所で切り、画像は落としてきたファイルを image() で出す。
// 非公開リポジトリの添付（github.com/user-attachments/assets/<uuid>）はそのままでは取れないので、
// API の body_html にある署名つきの URL（5分有効）を uuid で引いて使う。
// 落としたファイルは <Herdr の設定>/data/issues-env/img/ に置き、14日使わなければ消す。

const IMG_DIR = "data/issues-env/img";
// [![alt](画像)](リンク) / ![alt](画像) / <img src="画像">
const IMG_RE = /\[!\[([^\]]*)\]\((\S+?)(?:\s+"[^"]*")?\)\]\([^)]*\)|!\[([^\]]*)\]\((\S+?)(?:\s+"[^"]*")?\)|<img\b[^>]*?\bsrc=["']([^"']+)["'][^>]*>/gi;
const UUID_RE = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;
const attached = (url) => /^https:\/\/(github\.com\/user-attachments\/assets\/|private-user-images\.githubusercontent\.com\/)/.test(url);

// 本文を [{ text } | { img: { alt, url } } | { task: { checked, text, indent, index } }] に分ける。
// コードブロックの中は切らない。表の中の画像は、表を崩さないよう表では「🖼 alt」にして、表のすぐ後ろに並べる。
// チェックボックスの行（- [ ] / - [x]）は押せるように別にする。index は本文の中で何番目のチェックボックスか
const TASK_RE = /^(\s*)(?:[-*+]|\d+[.)])\s+\[([ xX])\]\s?(.*?)\r?$/;
const FENCE_RE = /^\s*(```|~~~)/;
function toImg(m) {
  const url = (m[2] ?? m[4] ?? m[5] ?? "").replace(/&amp;/g, "&");
  if (!/^https?:\/\//.test(url)) return null;
  const width = Number(/\bwidth=["']?(\d+)/i.exec(m[0])?.[1] ?? 0) || null; // <img width="300">
  return { alt: m[1] ?? m[3] ?? (/\balt=["']([^"']*)["']/i.exec(m[0])?.[1] ?? ""), url, width };
}
function segments(md) {
  const out = [], lines = (md || "").split("\n");
  let buf = [], fence = false, held = [], tasks = 0;
  const flush = () => { const t = buf.join("\n"); if (t.trim()) out.push({ text: t }); buf = []; };
  lines.forEach((line, i) => {
    const marker = FENCE_RE.test(line);
    if (marker) fence = !fence;
    if (fence || marker) { buf.push(line); return; }
    const task = TASK_RE.exec(line);
    if (task) {
      flush();
      out.push({ task: { checked: task[2] !== " ", text: task[3], indent: task[1].replace(/\t/g, "  ").length, index: tasks++ } });
      return;
    }
    if (/^\s*\|/.test(line)) {
      buf.push(line.replace(IMG_RE, (...m) => { const img = toImg(m); if (!img) return m[0]; held.push(img); return `🖼 ${img.alt || "画像"}`; }));
      if (!/^\s*\|/.test(lines[i + 1] ?? "") && held.length) { flush(); out.push(...held.map((img) => ({ img }))); held = []; }
      return;
    }
    let last = 0;
    for (const m of line.matchAll(IMG_RE)) {
      const img = toImg(m);
      if (!img) continue;
      const before = line.slice(last, m.index);
      // 画像だけの箇条書き（"- ![…](…)"）は、記号だけの行を残さない
      if (before.trim() && !/^\s*([-*+]|\d+\.)\s*$/.test(before)) buf.push(before);
      flush();
      out.push({ img });
      last = m.index + m[0].length;
    }
    if (last === 0) buf.push(line);
    else if (line.slice(last).trim()) buf.push(line.slice(last));
  });
  flush();
  return out;
}

// index 番目のチェックボックスを切り替えた本文。今の状態が was でなければ null（ほかで書き換わった）
function toggleTask(md, index, was) {
  const lines = (md || "").split("\n");
  let fence = false, k = 0;
  for (let i = 0; i < lines.length; i++) {
    const marker = FENCE_RE.test(lines[i]);
    if (marker) fence = !fence;
    if (fence || marker) continue;
    const m = TASK_RE.exec(lines[i]);
    if (!m || k++ !== index) continue;
    if ((m[2] !== " ") !== was) return null;
    lines[i] = lines[i].replace(/\[([ xX])\]/, was ? "[ ]" : "[x]");
    return lines.join("\n");
  }
  return null;
}

// ---- GitHub への読み書き（GraphQL）----

const REACTIONS = [["THUMBS_UP", "👍"], ["THUMBS_DOWN", "👎"], ["LAUGH", "😄"], ["HOORAY", "🎉"], ["CONFUSED", "😕"], ["HEART", "❤️"], ["ROCKET", "🚀"], ["EYES", "👀"]];
const REACTED = "reactionGroups { content viewerHasReacted reactors { totalCount } }";
const POST = `__typename id body bodyHTML createdAt viewerCanUpdate viewerCanReact author { login } ${REACTED}`;
const DETAIL_FIELDS = `id title body bodyHTML url createdAt viewerCanUpdate viewerCanReact author { login }
  labels(first: 20) { nodes { name color } } ${REACTED}
  comments(last: 100) { nodes { ${POST} } }`;
// PR はレビュー（承認・変更の依頼・コメント）もコメントと並べて出す
const DETAIL_QUERY = `query($owner: String!, $repo: String!, $n: Int!) { viewer { login } repository(owner: $owner, name: $repo) {
  issueOrPullRequest(number: $n) { __typename ... on Issue { ${DETAIL_FIELDS} }
    ... on PullRequest { ${DETAIL_FIELDS} reviews(last: 50) { nodes { ${POST} state } } } } } }`;
const BODY_QUERY = `query($id: ID!) { node(id: $id) { ... on Issue { body } ... on PullRequest { body } ... on IssueComment { body } ... on PullRequestReview { body } } }`;
const UPDATE = {
  Issue: "mutation($id: ID!, $body: String!) { updateIssue(input: { id: $id, body: $body }) { issue { id } } }",
  PullRequest: "mutation($id: ID!, $body: String!) { updatePullRequest(input: { pullRequestId: $id, body: $body }) { pullRequest { id } } }",
  IssueComment: "mutation($id: ID!, $body: String!) { updateIssueComment(input: { id: $id, body: $body }) { issueComment { id } } }",
  PullRequestReview: "mutation($id: ID!, $body: String!) { updatePullRequestReview(input: { pullRequestReviewId: $id, body: $body }) { pullRequestReview { id } } }",
};
// event: APPROVE（承認）/ REQUEST_CHANGES（変更を依頼）/ COMMENT
const REVIEW = "mutation($id: ID!, $event: PullRequestReviewEvent!, $body: String!) { addPullRequestReview(input: { pullRequestId: $id, event: $event, body: $body }) { pullRequestReview { id state } } }";
const REVIEW_STATE = { APPROVED: ["承認しました", "good"], CHANGES_REQUESTED: ["変更を依頼しました", "bad"], COMMENTED: ["レビューしました", "dim"], DISMISSED: ["レビュー（取り下げ）", "dim"] };
const REACT = (add) => `mutation($id: ID!, $c: ReactionContent!) { ${add ? "addReaction" : "removeReaction"}(input: { subjectId: $id, content: $c }) { subject { id } } }`;
const COMMENT = "mutation($id: ID!, $body: String!) { addComment(input: { subjectId: $id, body: $body }) { commentEdge { node { id } } } }";

// gh api graphql。owner / repo は gh がリポジトリから埋める（-F の {owner} / {repo}）。文字列は -f でそのまま渡す
async function gql(root, query, vars = {}) {
  const args = ["gh", "api", "graphql", "-f", `query=${query}`];
  for (const [k, v] of Object.entries(vars)) args.push(typeof v === "number" || /^\{(owner|repo)\}$/.test(v) ? "-F" : "-f", `${k}=${v}`);
  const r = await run_sh(args, root, 30000);
  let j = null;
  try { j = JSON.parse(r.stdout); } catch {}
  if (j?.errors?.length) throw new Error(j.errors[0].message);
  if (r.code !== 0 || !j?.data) throw new Error(lastLine(r.stderr) || "GitHub に問い合わせられませんでした");
  return j.data;
}

// 画像の置き場の名前（URL か添付の uuid から）
function imageKey(url) {
  const id = attached(url) ? (UUID_RE.exec(url)?.[0] ?? url) : url;
  let h = 0x811c9dc5;
  for (let i = 0; i < id.length; i++) { h ^= id.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; }
  return h.toString(16).padStart(8, "0") + id.length.toString(16);
}

// 落とす（あれば使い回す）。標準出力に「ファイル名 幅 高さ」を出す（大きさは sips で測る。測れなければ 0 0）
const FETCH = `cd "$1" || exit 1; key="$2"; url="$3"
size() { sips -g pixelWidth -g pixelHeight "$1" 2>/dev/null | awk '/pixelWidth/{w=$2} /pixelHeight/{h=$2} END{print (w?w:0), (h?h:0)}'; }
for f in "$key".*; do [ -f "$f" ] && [ "\${f##*.}" != part ] && { touch "$f"; echo "$f $(size "$f")"; exit 0; }; done
ct=$(curl -sfL --max-time 30 --max-filesize 20000000 -o "$key.part" -w '%{content_type}' "$url") || { rm -f "$key.part"; exit 1; }
case "$ct" in image/png*) e=png ;; image/jpeg*) e=jpg ;; image/gif*) e=gif ;; image/webp*) e=webp ;; image/svg*) e=svg ;; *) rm -f "$key.part"; exit 2 ;; esac
mv "$key.part" "$key.$e" && echo "$key.$e $(size "$key.$e")"`;

const numberIn = (branch, n) => new RegExp(`(^|[^0-9])${n}($|[^0-9])`).test(branch);

export default class IssuesEnv extends View {
  init(_props, cx) {
    this.repos = new Map(); // 本体のパス -> { issues, prs, worktrees, at, loading, error, detail }
    this.roots = new Map(); // フォルダ -> 本体のパス（null: git ではない）
    this.mode = "issue";
    try { if (localStorage.getItem("mode") === "pr") this.mode = "pr"; } catch {}
    this.filter = "";
    this.input = InputState.new({ placeholder: "絞り込み（番号・タイトル・ラベル・作成者）" });
    this.input.on("change", (_e, cx) => { this.filter = this.input.value(); cx.notify(); });
    this.branchInput = InputState.new({ placeholder: "ブランチ名" });
    this.branchInput.on("submit", (_e, cx) => this.submitStart(cx));
    this.images = new Map(); // 画像の置き場の名前 -> { state: "loading" | "ok" | "error", file }
    this.drafts = new Map(); // "<本体のパス>#<番号>" -> 書きかけのコメント
    this.comment = TextareaState.new({ placeholder: "コメントを書く（Markdown）", rows: 3 });
    this.comment.set_auto_grow(3, 12);
    this.comment.on("change", (_e, cx) => {
      const d = this.current()?.detail;
      if (!d) return;
      const had = Boolean(this.drafts.get(d.key)?.trim()), now = this.comment.value();
      this.drafts.set(d.key, now);
      if (had !== Boolean(now.trim())) cx.notify();
    });
    if (checking()) return;
    // 14日使っていない画像を消す（使うたびに touch している）
    exec("/bin/sh", ["-c", 'mkdir -p "$1" && find "$1" -type f -mtime +14 -delete', "issues-env", `${appHome()}/${IMG_DIR}`]).catch(() => {});
    cx.timer.every(STALE, (cx) => { const r = this.current(); if (r && shownNow()) { this.load(r.root, false, cx); cx.notify(); } });
  }

  // 今のフォルダのリポジトリ（まだ分からなければ調べに行き、null を返す）
  current(cx) {
    const { cwd } = location();
    if (!cwd) return null;
    if (!this.roots.has(cwd)) {
      if (!cx || checking()) return null;
      this.roots.set(cwd, undefined);
      cx.spawn(async (cx) => {
        let root = null;
        try {
          const r = await exec("git", ["rev-parse", "--path-format=absolute", "--git-common-dir", "--show-toplevel"], { cwd, timeout: 5000 });
          if (r.code === 0) {
            const [common, top] = r.stdout.trim().split("\n");
            root = common.endsWith("/.git") ? common.slice(0, -5) : top;
          }
        } catch {}
        this.roots.set(cwd, root);
        cx.notify();
      });
      return null;
    }
    const root = this.roots.get(cwd);
    if (!root) return null;
    if (!this.repos.has(root)) this.repos.set(root, { root, issues: null, prs: null, worktrees: [], at: 0, loading: false, error: null, detail: null });
    return this.repos.get(root);
  }

  // render からも呼ぶので、ここでは cx.notify() しない（イベントから呼んだら呼び手が notify する）
  load(root, fresh, cx) {
    const repo = this.repos.get(root);
    if (!repo || repo.loading) return;
    repo.loading = true;
    cx.spawn(async (cx) => {
      const flags = fresh ? ["--json", "--fresh"] : ["--json"];
      const get = async (script) => {
        const r = await run_sh([`${BIN()}/${script}`, ...flags], root);
        if (r.code !== 0) throw new Error(lastLine(r.stderr) || `${script} が失敗しました`);
        const v = JSON.parse(r.stdout || "[]");
        return Array.isArray(v) ? v : [];
      };
      try {
        const [issues, prs, wt] = await Promise.all([
          get("issues.sh"), get("prs.sh"),
          exec("git", ["worktree", "list", "--porcelain"], { cwd: root, timeout: 10000 }),
        ]);
        const worktrees = [];
        let path = null;
        for (const line of (wt.code === 0 ? wt.stdout : "").split("\n")) {
          if (line.startsWith("worktree ")) path = line.slice(9);
          else if (line.startsWith("branch refs/heads/") && path && path !== root) worktrees.push({ path, branch: line.slice(18) });
        }
        Object.assign(repo, { issues, prs, worktrees, error: null });
      } catch (e) {
        repo.error = String(e?.message ?? e).includes("No such file")
          ? "issue-board のスクリプトがありません（install.sh を流してください）"
          : String(e?.message ?? e);
      } finally {
        repo.loading = false;
        repo.at = Date.now();
        cx.notify();
      }
    });
  }

  setMode(mode, cx) {
    this.mode = mode;
    try { localStorage.setItem("mode", mode); } catch {}
    cx.notify();
  }

  issueWorktree(repo, n) { return repo.worktrees.find((w) => numberIn(w.branch, n)); }
  prWorktree(repo, pr) { return repo.worktrees.find((w) => w.branch === pr.headRefName); }
  spaceFor(path) { return spaces().find((s) => !s.machine && s.folder === path); }

  // ---- 詳細 ----

  open(repo, kind, n, cx) {
    repo.detail = { kind, n, key: `${repo.root}#${n}`, info: null, error: null, start: null, busy: null, note: null, signed: new Map() };
    this.picker = null;
    this.comment.set_value(this.drafts.get(repo.detail.key) ?? "");
    cx.notify();
    this.fetchDetail(repo, repo.detail, cx);
  }

  // 本文・コメント・リアクションを取り直す（開いたときと、書き込んだあと）
  fetchDetail(repo, d, cx) {
    cx.spawn(async (cx) => {
      try {
        const data = await gql(repo.root, DETAIL_QUERY, { owner: "{owner}", repo: "{repo}", n: d.n });
        const node = data.repository?.issueOrPullRequest;
        if (!node) throw new Error(`#${d.n} が見つかりません`);
        // コメントとレビューを時刻順に。本文の無い「レビューしました」（コードへのコメントだけ）と書きかけは出さない
        const reviews = (node.reviews?.nodes ?? []).filter((r) => r.state !== "PENDING" && (r.state !== "COMMENTED" || r.body?.trim()));
        const comments = [...(node.comments?.nodes ?? []), ...reviews].sort((a, b) => Date.parse(a.createdAt) - Date.parse(b.createdAt));
        d.info = { ...node, labels: node.labels?.nodes ?? [], comments, viewer: data.viewer?.login ?? "" };
        d.error = null;
        // 非公開の添付の署名つき URL（5分有効）
        for (const html of [node.bodyHTML, ...d.info.comments.map((c) => c.bodyHTML)])
          for (const m of (html ?? "").matchAll(/src="(https:\/\/private-user-images\.githubusercontent\.com\/[^"]+)"/g)) {
            const id = UUID_RE.exec(m[1])?.[0];
            if (id) d.signed.set(id.toLowerCase(), m[1].replace(/&amp;/g, "&"));
          }
      } catch (e) { if (!d.info) d.error = String(e?.message ?? e); }
      cx.notify();
      if (d.info) this.loadImages(d, cx);
    });
  }

  // 本文とコメントの画像を落とす
  async loadImages(d, cx) {
    const urls = [d.info.body, ...(d.info.comments ?? []).map((c) => c.body)]
      .flatMap((b) => segments(b).filter((s) => s.img).map((s) => s.img.url));
    const todo = [...new Set(urls)].filter((u) => !this.images.has(imageKey(u)) || this.images.get(imageKey(u)).state === "error");
    if (!todo.length) return;
    for (const u of todo) this.images.set(imageKey(u), { state: "loading" });
    cx.notify();
    const dir = `${appHome()}/${IMG_DIR}`;
    await Promise.all(todo.map(async (u) => {
      const key = imageKey(u), id = UUID_RE.exec(u)?.[0]?.toLowerCase();
      const url = attached(u) ? d.signed.get(id) ?? u : u;
      try {
        const r = await exec("/bin/sh", ["-c", FETCH, "issues-env", dir, key, url], { timeout: 40000 });
        const [file, w, h] = r.stdout.trim().split(" ");
        this.images.set(key, r.code === 0 && file ? { state: "ok", file: `${IMG_DIR}/${file}`, w: Number(w) || 0, h: Number(h) || 0 } : { state: "error" });
      } catch { this.images.set(key, { state: "error" }); }
      cx.notify();
    }));
  }

  // ---- 書き込み（チェックボックス・リアクション・コメント）----

  // 書き込みを1つずつ動かす。失敗したら元に戻して理由を出す
  write(repo, d, fn, undo, cx) {
    if (d.writing) return;
    d.writing = true;
    cx.notify();
    cx.spawn(async (cx) => {
      try { await fn(); }
      catch (e) { undo?.(); notify({ title: "GitHub に書き込めませんでした", body: String(e?.message ?? e), level: "error" }); }
      finally { d.writing = false; cx.notify(); }
    });
  }

  // subject: 本文（d.info）かコメント。__typename が無いのはコメント
  toggleTask(repo, d, subject, task, cx) {
    const type = subject.__typename ?? "IssueComment", before = subject.body;
    const local = toggleTask(before, task.index, task.checked);
    if (local == null) return;
    subject.body = local;
    this.write(repo, d, async () => {
      // 書き込む直前の本文で切り替える（ほかで書き換わっていれば止める）
      const fresh = (await gql(repo.root, BODY_QUERY, { id: subject.id })).node?.body ?? "";
      const next = toggleTask(fresh, task.index, task.checked);
      if (next == null) { subject.body = fresh; throw new Error("ほかで本文が書き換わっています。今の本文を出し直しました"); }
      await gql(repo.root, UPDATE[type], { id: subject.id, body: next });
      subject.body = next;
    }, () => { if (subject.body === local) subject.body = before; }, cx);
  }

  react(repo, d, subject, content, cx) {
    const groups = subject.reactionGroups ?? (subject.reactionGroups = []);
    let g = groups.find((x) => x.content === content);
    if (!g) groups.push(g = { content, viewerHasReacted: false, reactors: { totalCount: 0 } });
    const was = g.viewerHasReacted;
    const flip = (on) => { g.viewerHasReacted = on; g.reactors.totalCount = Math.max(0, g.reactors.totalCount + (on ? 1 : -1)); };
    flip(!was);
    this.picker = null;
    this.write(repo, d, () => gql(repo.root, REACT(!was), { id: subject.id, c: content }), () => flip(was), cx);
  }

  // PR のレビュー。承認と変更の依頼は、押し間違えないよう2回押しで送る（5秒で戻る）
  review(repo, d, event, cx) {
    const body = this.comment.value();
    if (!d.info?.id || (event === "REQUEST_CHANGES" && !body.trim())) return;
    if (d.confirm !== event) {
      d.confirm = event;
      cx.timer.after(5000, (cx) => { if (d.confirm === event) { d.confirm = null; cx.notify(); } });
      cx.notify();
      return;
    }
    d.confirm = null;
    this.write(repo, d, async () => {
      await gql(repo.root, REVIEW, { id: d.info.id, event, body });
      this.drafts.delete(d.key);
      if (this.current()?.detail === d) this.comment.set_value("");
      notify({ title: `#${d.n} を${event === "APPROVE" ? "承認しました" : "変更を依頼しました"}`, level: "success" });
      this.fetchDetail(repo, d, cx);
      this.load(repo.root, true, cx); // 一覧のバッジ（承認 / 要修正）も取り直す
    }, null, cx);
  }

  postComment(repo, d, cx) {
    const body = this.comment.value();
    if (!body.trim() || !d.info?.id) return;
    this.write(repo, d, async () => {
      await gql(repo.root, COMMENT, { id: d.info.id, body });
      this.drafts.delete(d.key);
      if (this.current()?.detail === d) this.comment.set_value("");
      this.fetchDetail(repo, d, cx);
    }, null, cx);
  }

  back(repo, cx) { repo.detail = null; cx.notify(); }

  browse(repo, d) {
    if (d.info?.url) exec("/usr/bin/open", [d.info.url]);
    else run_sh(["gh", d.kind, "view", String(d.n), "--web"], repo.root);
  }

  // 実行中の表示を出してから非同期で動かし、終わったら一覧（ワークツリー）を取り直す
  act(repo, d, label, fn, cx) {
    if (d.busy) return;
    d.busy = label; d.note = null;
    cx.notify();
    cx.spawn(async (cx) => {
      try { await fn(); }
      catch (e) { d.note = { error: true, text: String(e?.message ?? e) }; }
      finally {
        d.busy = null;
        cx.notify();
        this.load(repo.root, false, cx);
      }
    });
  }

  async openWorktree(repo, branch, path, args = []) {
    const sp = path && this.spaceFor(path);
    if (sp) { run("space.select", { id: sp.id }); return; }
    await gwt(repo.root, [branch, ...args]);
  }

  async openPr(repo, pr, args) {
    const wt = this.prWorktree(repo, pr);
    if (!wt) {
      const has = await exec("git", ["show-ref", "--verify", "--quiet", `refs/heads/${pr.headRefName}`], { cwd: repo.root });
      if (has.code !== 0) {
        const f = await exec("git", ["fetch", "-q", "origin", pr.headRefName], { cwd: repo.root, timeout: 60000 });
        if (f.code !== 0) throw new Error(`origin/${pr.headRefName} を取れませんでした: ${lastLine(f.stderr)}`);
        const b = await exec("git", ["branch", "-q", "--track", pr.headRefName, `origin/${pr.headRefName}`], { cwd: repo.root });
        if (b.code !== 0) throw new Error(lastLine(b.stderr) || "ブランチを作れませんでした");
      }
    }
    // Space の名前は PR のタイトルにする（ブランチ名はサイドバーに別に出る）
    const title = pr.title ? ["--title", pr.title] : [];
    await this.openWorktree(repo, pr.headRefName, wt?.path, [...args, ...title]);
  }

  // issue の着手: ブランチ名の提案を取りに行き、入力欄を出す
  beginStart(repo, d, cx) {
    const issue = (repo.issues ?? []).find((i) => i.number === d.n);
    const title = d.info?.title ?? issue?.title ?? "";
    const labels = (d.info?.labels ?? issue?.labels ?? []).map((l) => l.name).join(", ");
    d.start = { title, suggesting: true, confirm: null };
    d.note = null;
    this.branchInput.set_value("");
    cx.notify();
    const start = d.start;
    cx.spawn(async (cx) => {
      let name = `issue-${d.n}`;
      try {
        const r = await run_sh([`${BIN()}/suggest-branch.sh`, String(d.n), title, labels], repo.root, 45000);
        if (r.code === 0 && r.stdout.trim()) name = r.stdout.trim();
      } catch {}
      if (d.start !== start) return;
      start.suggesting = false;
      if (!this.branchInput.value()) this.branchInput.set_value(name);
      this.branchInput.focus();
      cx.notify();
    });
  }

  submitStart(cx) {
    const repo = this.current(), d = repo?.detail;
    if (!d?.start || d.start.suggesting || d.busy) return;
    const branch = this.branchInput.value().trim();
    if (!branch) return;
    const start = d.start;
    this.act(repo, d, "Space を開いています…", async () => {
      const ok = await exec("git", ["check-ref-format", "--branch", branch], { cwd: repo.root });
      if (ok.code !== 0) throw new Error("ブランチ名に使えない文字があります");
      const has = await exec("git", ["show-ref", "--verify", "--quiet", `refs/heads/${branch}`], { cwd: repo.root });
      if (has.code === 0 && start.confirm !== branch) {
        start.confirm = branch;
        d.note = { error: true, text: "同じ名前のブランチが既にあります。もう一度「着手」を押すと、そのブランチで開きます" };
        return;
      }
      const task = `GitHub issue #${d.n}「${start.title}」に着手してください。\n`
        + `- まず \`gh issue view ${d.n} --comments\` で本文と議論を読む\n`
        + "- 方針の分かれ目があれば、実装の前に確認する\n"
        + `- PR の本文には \`Closes #${d.n}\` を入れる`;
      const title = start.title ? ["--title", start.title] : [];
      await gwt(repo.root, [branch, "--task", task, ...title]);
      d.start = null;
      d.note = { error: false, text: `${branch} の Space で Claude Code に渡しました` };
      notify({ title: `#${d.n} に着手`, body: branch, level: "success" });
    }, cx);
  }

  // ---- 描画 ----

  button(t, id, label, onClick, { primary = false, disabled = false } = {}) {
    return div().id(id).h(28).px(10).flex().flex_none().items_center().rounded(7).text_size(12)
      .when(primary, (el) => el.bg(disabled ? t.raised : t.solid).text_color(disabled ? t.dim : t.solid_foreground))
      .when(!primary, (el) => el.border_1().border_color(t.border).text_color(disabled ? t.dim : t.foreground))
      .when(!disabled, (el) => el.cursor_pointer().on_click(onClick).when(!primary, (el) => el.hover((s) => s.bg(t.hover))))
      .child(label);
  }

  chips(t, labels) {
    return (labels ?? []).map((l) => div().flex_none().px(6).h(17).flex().items_center().rounded(9).text_size(10.5)
      .bg(`#${/^[0-9a-f]{6}$/i.test(l.color || "") ? l.color : "888888"}`).text_color(labelFg(l.color)).child(l.name));
  }

  badges(t, list) {
    const color = { bad: t.ansi1, wait: t.ansi3, good: t.ansi2, dim: t.dim };
    return list.map((b) => div().flex_none().text_size(11).text_color(color[b.tone]).child(b.text));
  }

  matches(text) {
    const terms = this.filter.toLowerCase().split(/\s+/).filter(Boolean);
    return terms.every((w) => text.includes(w.replace(/^#/, "")));
  }

  rows(t, repo) {
    const pr = this.mode === "pr";
    const list = (pr ? repo.prs : repo.issues) ?? [];
    const out = [];
    for (const item of list) {
      const labels = pr ? item.labels?.nodes ?? [] : item.labels ?? [];
      const author = item.author?.login ?? "";
      if (this.filter && !this.matches(`${item.number} ${item.title} ${labels.map((l) => l.name).join(" ")} ${author}`.toLowerCase())) continue;
      const wip = pr ? this.prWorktree(repo, item) : this.issueWorktree(repo, item.number);
      out.push(v_flex().id(`row-${item.number}`).flex_none().px(10).py(7).gap(4).rounded(7).cursor_pointer()
        .hover((s) => s.bg(t.hover))
        .on_click((_e, cx) => this.open(repo, pr ? "pr" : "issue", item.number, cx))
        .child(h_flex().gap(6).items_start()
          .child(div().w(8).flex_none().text_color(t.ansi3).child(wip ? "●" : ""))
          .child(div().flex_none().text_color(t.ansi6).child(`#${item.number}`))
          .child(div().flex_1().min_w(0).child(item.title)))
        .child(h_flex().pl(14).gap(5).flex_wrap().items_center()
          .children(pr ? this.badges(t, prBadges(item)) : [])
          .children(this.chips(t, labels))
          .child(div().flex_none().text_size(11).text_color(t.dim).child(`@${author}${item.updatedAt ? ` · ${ago(item.updatedAt)}` : ""}`))));
    }
    return out;
  }

  list(t, repo) {
    const pr = this.mode === "pr";
    const items = pr ? repo.prs : repo.issues;
    const tab = (mode, label, count) => div().id(`mode-${mode}`).h(26).px(10).flex().items_center().rounded(6).text_size(12).cursor_pointer()
      .bg(this.mode === mode ? t.selected : t.background).text_color(this.mode === mode ? t.foreground : t.muted)
      .hover((s) => s.bg(t.hover))
      .on_click((_e, cx) => this.setMode(mode, cx))
      .child(count == null ? label : `${label} ${count}`);
    const rows = items ? this.rows(t, repo) : [];
    const empty = repo.error ? repo.error
      : !items ? "読み込んでいます…"
      : !rows.length ? (this.filter ? "当てはまるものがありません" : pr ? "open な PR はありません" : "open な issue はありません")
      : null;
    return v_flex().flex_1().min_h(0)
      .child(h_flex().h(40).flex_none().px(10).gap(4).items_center()
        .child(tab("issue", "Issues", repo.issues?.length))
        .child(tab("pr", "PRs", repo.prs?.length))
        .child(div().flex_1())
        .child(div().id("refresh").size(26).flex().items_center().justify_center().rounded(6).cursor_pointer()
          .text_color(t.muted).hover((s) => s.bg(t.hover))
          .on_click((_e, cx) => { this.load(repo.root, true, cx); cx.notify(); })
          .child(repo.loading ? svg("mods/workspace-chrome/spinner.svg").size(13).spin(900) : div().child("↻"))))
      .child(div().flex_none().px(10).pb(8)
        .child(div().id("filter").h(30).px(9).flex().items_center().rounded(7).border_1().border_color(t.border).bg(t.background)
          .on_mouse_down("left", (_e, cx) => cx.stop_propagation())
          .child(Input.new(this.input).h_full().w_full().text_size(12).text_color(t.foreground))))
      .child(v_flex().id("issues-list").flex_1().min_h(0).overflow_y_scrollbar().px(4).pb(6)
        .children(rows)
        .when(Boolean(empty), (el) => el.child(div().p(10).text_size(12).text_color(repo.error ? t.ansi1 : t.dim).child(empty))));
  }

  actions(t, repo, d) {
    const busy = Boolean(d.busy), out = [];
    if (d.kind === "issue") {
      const wt = this.issueWorktree(repo, d.n);
      if (wt) {
        const sp = this.spaceFor(wt.path);
        out.push(this.button(t, "act-space", sp ? "Space へ移る" : "Space を開く",
          (_e, cx) => this.act(repo, d, "Space を開いています…", () => this.openWorktree(repo, wt.branch, wt.path), cx), { primary: true, disabled: busy }));
      } else if (!d.start) {
        out.push(this.button(t, "act-start", "着手", (_e, cx) => this.beginStart(repo, d, cx), { primary: true, disabled: busy }));
      }
    } else {
      const pr = (repo.prs ?? []).find((p) => p.number === d.n);
      const wt = pr && this.prWorktree(repo, pr);
      if (pr && wt) {
        const sp = this.spaceFor(wt.path);
        out.push(this.button(t, "act-space", sp ? "Space へ移る" : "Space を開く",
          (_e, cx) => this.act(repo, d, "Space を開いています…", () => this.openPr(repo, pr, []), cx), { primary: true, disabled: busy }));
      } else if (pr && !pr.isCrossRepository) {
        out.push(this.button(t, "act-agent", "Claude Code で開く",
          (_e, cx) => this.act(repo, d, "Space を開いています…", () => this.openPr(repo, pr, []), cx), { primary: true, disabled: busy }));
        out.push(this.button(t, "act-shell", "シェルで開く",
          (_e, cx) => this.act(repo, d, "Space を開いています…", () => this.openPr(repo, pr, ["--no-agent"]), cx), { disabled: busy }));
      }
    }
    out.push(this.button(t, "act-web", "ブラウザ", () => this.browse(repo, d)));
    return out;
  }

  startForm(t, repo, d) {
    const s = d.start;
    return v_flex().gap(6).p(10).rounded(8).border_1().border_color(t.border).bg(t.surface)
      .child(div().text_size(12).text_color(t.muted).child("新しい Space の Claude Code にこの issue を渡します。ブランチ名（= Space 名）を確認してください"))
      .child(div().id("branch").h(30).px(9).flex().items_center().rounded(7).border_1().border_color(t.input).bg(t.background)
        .on_mouse_down("left", (_e, cx) => cx.stop_propagation())
        .child(s.suggesting ? div().text_size(12).text_color(t.dim).child("ブランチ名を考えています…")
          : Input.new(this.branchInput).h_full().w_full().text_size(12).text_color(t.foreground)))
      .child(h_flex().gap(6)
        .child(this.button(t, "start-go", "着手", (_e, cx) => this.submitStart(cx), { primary: true, disabled: s.suggesting || Boolean(d.busy) }))
        .child(this.button(t, "start-cancel", "やめる", (_e, cx) => { d.start = null; d.note = null; cx.notify(); }, { disabled: Boolean(d.busy) })));
  }

  checks(t, pr) {
    const order = { fail: 0, pending: 1, ok: 2, skip: 3 };
    const mark = { fail: ["✗", t.ansi1], pending: ["…", t.ansi3], ok: ["✓", t.ansi2], skip: ["-", t.dim] };
    const list = checksOf(pr).map((c) => ({ c, k: checkClass(c) })).sort((a, b) => order[a.k] - order[b.k]);
    if (!list.length) return null;
    return v_flex().gap(2)
      .child(div().text_size(11).font_semibold().text_color(t.muted).child("checks"))
      .children(list.map(({ c, k }, i) => h_flex().id(`check-${i}`).gap(6).text_size(12)
        .when(Boolean(c.detailsUrl || c.targetUrl), (el) => el.cursor_pointer().hover((s) => s.bg(t.hover)).on_click(() => exec("/usr/bin/open", [c.detailsUrl || c.targetUrl])))
        .child(div().w(12).flex_none().text_color(mark[k][1]).child(mark[k][0]))
        .child(div().flex_1().min_w(0).truncate().child(c.name || c.context || "?"))));
  }

  detail(t, repo, d) {
    const issue = d.kind === "issue" ? (repo.issues ?? []).find((i) => i.number === d.n) : null;
    const pr = d.kind === "pr" ? (repo.prs ?? []).find((p) => p.number === d.n) : null;
    const info = d.info ?? {};
    const title = info.title ?? issue?.title ?? pr?.title ?? "";
    const labels = info.labels ?? issue?.labels ?? pr?.labels?.nodes ?? [];
    const author = info.author?.login ?? issue?.author?.login ?? pr?.author?.login ?? "";
    const comments = info.comments ?? [];
    // 幅の広い表やコードがパネルを押し広げないよう、幅をパネルに合わせて横ははみ出させない
    const piece = (id, text) => div().w_full().min_w(0).overflow_x_hidden().child(TextView.markdown(id, text).selectable(true));
    // 画像の所で切って並べる。画像は押すとブラウザで開く
    const pic = (id, { alt, url, width }) => {
      const st = this.images.get(imageKey(url));
      const open = () => exec("/usr/bin/open", [url]);
      const caption = alt && !/^(image|img|screenshot|画像)$/i.test(alt.trim());
      // GitHub と同じく元の大きさ（<img width> があればそれ）で、パネルより広ければ縮める
      const avail = Math.max(120, (this.detailWidth ?? 340) - 24);
      const want = Math.min(width || st?.w || avail, avail);
      const sized = (el) => st.w && st.h ? el.w(want).h(Math.round(want * st.h / st.w)) : el.w_full();
      if (st?.state === "ok") return v_flex().id(id).w_full().gap(3).cursor_pointer().on_click(open)
        .child(sized(image(st.file)))
        .when(caption, (el) => el.child(div().text_size(11).text_color(t.dim).child(alt)));
      return div().id(id).px(8).py(6).rounded(6).border_1().border_color(t.border).text_size(12).text_color(t.dim).cursor_pointer()
        .hover((s) => s.bg(t.hover)).on_click(open)
        .child(`🖼 ${alt || "画像"}${st?.state === "error" ? "（読み込めません。押すとブラウザで開く）" : "（読み込んでいます…）"}`);
    };
    // チェックボックス。書き換えられる人（viewerCanUpdate）だけ押せる
    const task = (id, subject, tk) => {
      const can = subject.viewerCanUpdate && !d.writing;
      return h_flex().id(id).w_full().min_w(0).gap(7).items_start().pl(tk.indent * 9)
        .child(div().id(`${id}-box`).mt(3).size(15).flex_none().flex().items_center().justify_center().rounded(4).border_1()
          .border_color(tk.checked ? t.accent : t.input).bg(tk.checked ? t.accent : t.background)
          .text_size(11).text_color(t.accent_foreground)
          .when(can, (el) => el.cursor_pointer().on_click((_e, cx) => this.toggleTask(repo, d, subject, tk, cx)))
          .child(tk.checked ? "✓" : ""))
        .child(div().flex_1().min_w(0).when(tk.checked, (el) => el.opacity(0.65)).child(TextView.markdown(`${id}-text`, tk.text || " ").selectable(true)));
    };
    const md = (id, subject) => {
      const segs = segments(subject.body);
      if (!segs.length) return piece(id, "_（本文なし）_");
      return v_flex().w_full().min_w(0).gap(8).children(segs.map((s, i) =>
        s.img ? pic(`${id}-img-${i}`, s.img) : s.task ? task(`${id}-task-${i}`, subject, s.task) : piece(`${id}-${i}`, s.text)));
    };
    // リアクション。付いているものと、＋で全部から選ぶ
    const reactions = (id, subject) => {
      const can = subject.viewerCanReact && !d.writing;
      const groups = (subject.reactionGroups ?? []).filter((g) => g.reactors?.totalCount > 0);
      const pill = (content, emoji, g, key) => div().id(key).h(24).px(7).flex().items_center().gap(4).rounded(12).text_size(12)
        .border_1().border_color(g?.viewerHasReacted ? t.accent : t.border).bg(g?.viewerHasReacted ? t.selected : t.background)
        .when(can, (el) => el.cursor_pointer().hover((s) => s.bg(t.hover)).on_click((_e, cx) => this.react(repo, d, subject, content, cx)))
        .child(emoji).when(Boolean(g?.reactors?.totalCount), (el) => el.child(div().text_color(t.muted).child(String(g.reactors.totalCount))));
      return v_flex().gap(4)
        .child(h_flex().gap(4).flex_wrap().items_center()
          .children(groups.map((g) => pill(g.content, REACTIONS.find(([c]) => c === g.content)?.[1] ?? g.content, g, `${id}-${g.content}`)))
          .when(can, (el) => el.child(div().id(`${id}-add`).h(24).px(7).flex().items_center().rounded(12).text_size(12).cursor_pointer()
            .border_1().border_color(t.border).text_color(t.muted).hover((s) => s.bg(t.hover))
            .on_click((_e, cx) => { this.picker = this.picker === subject.id ? null : subject.id; cx.notify(); })
            .child("☺＋"))))
        .when(this.picker === subject.id && can, (el) => el.child(h_flex().gap(4).flex_wrap()
          .children(REACTIONS.map(([c, e]) => {
            const mine = (subject.reactionGroups ?? []).some((g) => g.content === c && g.viewerHasReacted);
            return pill(c, e, mine ? { viewerHasReacted: true } : null, `${id}-pick-${c}`);
          }))));
    };
    const draft = this.drafts.get(d.key) ?? "";
    const commentBox = () => v_flex().gap(6).pt(10).border_t(1).border_color(t.border)
      .child(div().id("comment-box").w_full().px(9).py(6).rounded(7).border_1().border_color(t.input).bg(t.background)
        .on_mouse_down("left", (_e, cx) => cx.stop_propagation())
        .child(Textarea.new(this.comment).w_full().text_size(12.5).text_color(t.foreground)))
      .child(h_flex().gap(6).items_center().flex_wrap()
        .child(this.button(t, "comment-post", d.writing ? "書き込んでいます…" : "コメント", (_e, cx) => this.postComment(repo, d, cx),
          { primary: true, disabled: d.writing || !draft.trim() }))
        // PR のレビュー。自分の PR は承認も変更の依頼もできない（GitHub の決まり）
        .when(d.kind === "pr" && Boolean(info.id) && info.viewer !== author, (el) => el
          .child(this.button(t, "review-approve", d.confirm === "APPROVE" ? "もう一度押すと承認" : "承認",
            (_e, cx) => this.review(repo, d, "APPROVE", cx), { primary: d.confirm === "APPROVE", disabled: d.writing }))
          .child(this.button(t, "review-changes", d.confirm === "REQUEST_CHANGES" ? "もう一度押すと変更を依頼" : "変更を依頼",
            (_e, cx) => this.review(repo, d, "REQUEST_CHANGES", cx), { primary: d.confirm === "REQUEST_CHANGES", disabled: d.writing || !draft.trim() }))))
      .child(div().text_size(11).text_color(t.dim).child(
        d.kind === "pr" && info.viewer !== author ? "GitHub にそのまま書き込みます。変更の依頼には本文が要ります。承認では欄の文がレビューの本文になります" : "GitHub にそのまま書き込みます"));
    return v_flex().id("issues-detail").flex_1().min_h(0).min_w(0).w_full().overflow_y_scrollbar().p(12).gap(10)
      .on_size_change((size, cx) => { if (size.width !== this.detailWidth) { this.detailWidth = size.width; cx.notify(); } })
      .child(div().text_size(15).font_semibold().child(title))
      .child(h_flex().gap(5).flex_wrap().items_center()
        .child(div().flex_none().text_size(11.5).text_color(t.ansi6).child(`#${d.n}`))
        .children(pr ? this.badges(t, prBadges(pr)) : [])
        .children(this.chips(t, labels)))
      .child(div().min_w(0).truncate().text_size(11.5).text_color(t.dim)
        .child([`@${author}`, info.createdAt ? info.createdAt.slice(0, 10) : "", pr?.headRefName ?? ""].filter(Boolean).join(" · ")))
      .child(h_flex().gap(6).flex_wrap().children(this.actions(t, repo, d)))
      .when(Boolean(d.start), (el) => el.child(this.startForm(t, repo, d)))
      .when(Boolean(d.busy), (el) => el.child(div().text_size(12).text_color(t.muted).child(d.busy)))
      .when(Boolean(d.note), (el) => el.child(div().text_size(12).text_color(d.note.error ? t.ansi1 : t.ansi2).child(d.note.text)))
      .when(Boolean(pr?.isCrossRepository), (el) => el.child(div().text_size(12).text_color(t.dim).child(`フォークからの PR なので開けません（gh pr checkout ${d.n} で取ってください）`)))
      .when(Boolean(pr), (el) => { const c = this.checks(t, pr); return c ? el.child(c) : el; })
      .child(div().h(1).flex_none().bg(t.border))
      .child(d.error ? div().text_size(12).text_color(t.ansi1).child(d.error)
        : d.info ? md(`body-${d.kind}-${d.n}`, info)
        : div().text_size(12).text_color(t.dim).child("読み込んでいます…"))
      .when(Boolean(d.info), (el) => el.child(reactions(`react-${d.n}`, info)))
      .children(comments.map((c, i) => v_flex().gap(6).pt(8).border_t(1).border_color(t.border)
        .child(h_flex().gap(6).items_center().text_size(11.5)
          .child(div().text_color(t.muted).child(`@${c.author?.login ?? "?"}`))
          .when(Boolean(c.state && REVIEW_STATE[c.state]), (el) => el.children(this.badges(t, [{ text: REVIEW_STATE[c.state][0], tone: REVIEW_STATE[c.state][1] }])))
          .child(div().text_color(t.dim).child(ago(c.createdAt))))
        .when(Boolean(c.body?.trim()) || !c.state, (el) => el.child(md(`comment-${d.kind}-${d.n}-${i}`, c)))
        .child(reactions(`react-c-${i}`, c))))
      .when(Boolean(d.info), (el) => el.child(commentBox()));
  }

  render(cx) {
    const t = theme();
    const repo = this.current(cx);
    const shown = shownNow();
    if (repo && shown && !repo.loading && Date.now() - repo.at > STALE) this.load(repo.root, false, cx);
    const d = repo?.detail;
    const { remote, cwd } = location();
    const title = d ? `${d.kind === "pr" ? "PR" : "Issue"} #${d.n}` : "Issues";
    const sub = repo ? repo.root.split("/").pop() : "";
    const body = repo ? (d ? this.detail(t, repo, d) : this.list(t, repo))
      : div().p(12).text_size(12).text_color(t.dim).child(
        remote ? "ほかのマシンの Space では出しません"
          : !cwd ? "フォルダがありません"
          : this.roots.get(cwd) === null ? "git のリポジトリではありません"
          : "読み込んでいます…");
    return v_flex().id("issues").size_full().min_h(0).min_w(0).rounded(10).overflow_hidden().bg(t.background).border_1().border_color(t.border)
      .text_color(t.foreground).text_size(13)
      .child(h_flex().h(42).flex_none().px(14).gap(8).items_center().border_b(1).border_color(t.border)
        .when(Boolean(d), (el) => el.child(div().id("issues-back").w(20).cursor_pointer().text_color(t.muted).on_click((_e, cx) => this.back(repo, cx)).child("←")))
        .child(div().font_semibold().child(title))
        .child(div().flex_1().min_w(0).truncate().text_right().text_size(12).text_color(t.dim).child(sub)))
      .child(body);
  }
}
