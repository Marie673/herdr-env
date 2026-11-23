#!/usr/bin/env python3
"""Herdr の GUI 下部の利用量バー（herdr-app/mods/workspace-status-env）に出す値を集める。

Claude / Antigravity / Codex / Gemini CLI の利用量を、それぞれが手元に残すものから読む。
入っていないツールは出さない。

  ai-usage-collect.py [--json] [--sync-hc]   集めて JSON で出す（--sync-hc で Codex のターミナルにラベルを付ける）
  ai-usage-collect.py --record-agy           Antigravity の statusLine の JSON（stdin）を残す
  ai-usage-collect.py --record-gemini        Gemini CLI の AfterAgent フックの JSON（stdin）を残す

Claude の値は、既定では claude/statusline.sh が残す statusLine の rate_limits（5h / 7d）と、
Claude Code が ~/.claude.json に持つ cachedUsageUtilization から取る。
モデル別の週の枠まで出したいときは ~/.config/herdr-env/usage.json に {"claude_usage_api": true} を書く
（または HERDR_ENV_CLAUDE_USAGE_API=1）。Keychain にある Claude Code のトークンで、/usage と同じ
API（公開されていない）を5分に1回まで呼ぶ。API が変わると取れなくなる（そのときは既定の値に戻る）。
"""

import datetime
import glob
import json
import os
import subprocess
import sys
import tempfile
import time

HOME = os.path.expanduser("~")
STATE_DIR = os.path.join(
    os.environ.get("XDG_STATE_HOME") or os.path.join(HOME, ".local", "state"),
    "herdr-usage",
)
AGY_STATE = os.path.join(STATE_DIR, "antigravity.json")
GEMINI_STATE = os.path.join(STATE_DIR, "gemini.json")
CLAUDE_ACCOUNTS = os.path.join(HOME, ".config", "claude-accounts", "accounts")


def atomic_write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def parse_iso_epoch(ts):
    if not ts or not isinstance(ts, str):
        return None
    try:
        s = ts.replace("Z", "+00:00")
        ep = datetime.datetime.fromisoformat(s).timestamp()
        # Antigravity emits 1970-01-01T00:00:00Z for untouched buckets (no active reset window).
        return ep if ep > 86400 else None
    except Exception:
        return None


def fmt_countdown(resets_at, now):
    if not resets_at or resets_at <= 86400:
        return ""
    sec = int(resets_at - now)
    if sec <= 0:
        return "reset"
    m = sec // 60
    if m < 60:
        return f"{m}m"
    h = m // 60
    if h < 24:
        return f"{h}h{m % 60:02d}m"
    d = h // 24
    return f"{d}d{h % 24}h"


def fmt_window(minutes):
    if not minutes:
        return ""
    if minutes == 300:
        return "5h"
    if minutes == 10080:
        return "7d"
    if minutes % 1440 == 0:
        return f"{minutes // 1440}d"
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m"


def fmt_tokens(n):
    if n is None or n <= 0:
        return ""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 100_000:
        return f"{n / 1_000:.0f}k"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(int(n))


def agy_bucket_meta(key):
    low = key.lower()
    if low.startswith("gemini"):
        group = "Gemini"
        g_ord = 0
    elif low.startswith("3p"):
        group = "3P (Claude/GPT)"
        g_ord = 1
    else:
        group = key.split("-")[0]
        g_ord = 2

    if "5h" in low or "five" in low or "session" in low:
        win = "5h"
        w_ord = 0
    elif "weekly" in low or "7d" in low:
        win = "7d"
        w_ord = 1
    else:
        win = key
        w_ord = 2

    label = f"{group} {win}" if group != win else win
    return group, win, label, (g_ord, w_ord, key)


def parse_agy_quota(quota_obj, now):
    if not isinstance(quota_obj, dict):
        return [], [], None

    detailed = []
    compact_by_win = {}

    for key, val in quota_obj.items():
        if not isinstance(val, dict) or val.get("disabled"):
            continue
        frac = val.get("remaining_fraction")
        if not isinstance(frac, (int, float)):
            continue
        reset_at = parse_iso_epoch(val.get("reset_time"))
        if reset_at is None and isinstance(val.get("reset_in_seconds"), (int, float)) and val["reset_in_seconds"] > 0:
            reset_at = now + float(val["reset_in_seconds"])
        pct = max(0, min(100, round(frac * 100)))
        if reset_at and reset_at <= now:
            pct = 100
            reset_at = None

        group, win, label, sort_key = agy_bucket_meta(key)
        entry = {
            "key": key,
            "group": group,
            "window": win,
            "label": label,
            "remaining": pct,
            "resets_at": reset_at,
            "countdown": fmt_countdown(reset_at, now),
            "_sort": sort_key,
        }
        detailed.append(entry)

        # For compact bar summary, track the tightest remaining % per window (5h / 7d).
        prev = compact_by_win.get(win)
        if prev is None or pct < prev["remaining"]:
            compact_by_win[win] = {"label": win, "remaining": pct, "resets_at": reset_at}

    detailed.sort(key=lambda e: e["_sort"])
    for e in detailed:
        e.pop("_sort", None)

    order = {"5h": 0, "7d": 1}
    compact = sorted(compact_by_win.values(), key=lambda w: (order.get(w["label"], 50), w["label"]))
    lowest = min((w["remaining"] for w in detailed), default=None)
    return detailed, compact, lowest


def record_agy():
    raw = sys.stdin.read()
    if not raw.strip():
        return
    try:
        data = json.loads(raw)
    except Exception:
        return

    now = time.time()
    quota = data.get("quota") if isinstance(data.get("quota"), dict) else {}
    detailed, compact, lowest = parse_agy_quota(quota, now)

    prev = read_json(AGY_STATE) or {}
    if not quota and prev.get("quota"):
        quota = prev["quota"]
        detailed, compact, lowest = parse_agy_quota(quota, now)

    lim_text = "  ".join(f"{w['label']} {w['remaining']}%" for w in compact)

    snap = {
        "updated_at": int(now),
        "plan_tier": data.get("plan_tier") or prev.get("plan_tier") or "",
        "model": (data.get("model") or {}).get("display_name") or prev.get("model") or "",
        "quota": quota,
        "summary": lim_text,
        "lowest_pct": lowest,
        "cost_usd": (data.get("cost") or {}).get("total_usd"),
    }
    atomic_write_json(AGY_STATE, snap)

    term = os.environ.get("HC_TERMINAL_ID")
    if term and lim_text:
        subprocess.run(
            [
                "hc",
                "label",
                term,
                "herdr-env.acct_id=antigravity",
                "herdr-env.acct=✦ Antigravity",
                f"herdr-env.lim={lim_text}",
                f"herdr-env.lim_at={int(now)}",
            ],
            capture_output=True,
            timeout=3,
        )


def record_gemini():
    raw = sys.stdin.read()
    now = int(time.time())
    prev = read_json(GEMINI_STATE) or {}
    data = {}
    if raw.strip():
        try:
            data = json.loads(raw)
        except Exception:
            data = {}
    snap = {
        "updated_at": now,
        "last_event": data.get("hook_event_name") or data.get("event") or "AfterAgent",
        "session_id": data.get("session_id") or prev.get("session_id") or "",
    }
    atomic_write_json(GEMINI_STATE, snap)


def load_claude_accounts():
    accounts = []
    if os.path.isfile(CLAUDE_ACCOUNTS):
        try:
            with open(CLAUDE_ACCOUNTS, encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if not s or s.startswith("#"):
                        continue
                    parts = s.split(None, 3)
                    if len(parts) < 4:
                        continue
                    acc_id, cfg_dir, badge, name = parts
                    cfg_path = os.path.expanduser(cfg_dir)
                    json_path = (
                        os.path.join(HOME, ".claude.json")
                        if os.path.abspath(cfg_path) == os.path.abspath(os.path.join(HOME, ".claude"))
                        else os.path.join(cfg_path, ".claude.json")
                    )
                    accounts.append(
                        {
                            "id": acc_id,
                            "badge": badge,
                            "name": name,
                            "json_path": json_path,
                            "cfg_dir": os.path.abspath(cfg_path),
                        }
                    )
        except Exception:
            pass
    if not accounts:
        accounts.append(
            {
                "id": "claude",
                "badge": "🟦",
                "name": "Claude",
                "json_path": os.path.join(HOME, ".claude.json"),
                "cfg_dir": os.path.join(HOME, ".claude"),
            }
        )
    return accounts


USAGE_CONFIG = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config"), "herdr-env", "usage.json")


def claude_usage_api_enabled():
    if os.environ.get("HERDR_ENV_CLAUDE_USAGE_API") in ("1", "true", "yes"):
        return True
    return bool((read_json(USAGE_CONFIG) or {}).get("claude_usage_api"))


CLAUDE_USAGE_TTL = 300  # 利用状況 API は5分に1回まで（アカウントごと）
STATUSLINE_DIR = os.path.join(
    os.environ.get("XDG_STATE_HOME") or os.path.join(HOME, ".local", "state"), "herdr-env", "statusline"
)


def claude_keychain_service(cfg_dir):
    # Claude Code の保存名: 既定の ~/.claude は "Claude Code-credentials"、
    # CLAUDE_CONFIG_DIR を使うものは末尾に sha256(フォルダ) の先頭8桁が付く
    import hashlib
    if os.path.abspath(cfg_dir) == os.path.abspath(os.path.join(HOME, ".claude")):
        return "Claude Code-credentials"
    return "Claude Code-credentials-" + hashlib.sha256(cfg_dir.encode()).hexdigest()[:8]


def claude_usage_live(acc, now):
    """Claude Code の /usage と同じ API から、5h / 7d / モデル別の週の枠を取る。
    トークンは Keychain の Claude Code の保存分を読むだけ（期限切れなら取らない。更新は Claude Code に任せる）。
    結果は STATE_DIR に残し、失敗したら前回の値を返す。"""
    cache_path = os.path.join(STATE_DIR, f"claude-usage-{acc['id']}.json")
    cache = read_json(cache_path) or {}
    if cache.get("checked_at") and now - cache["checked_at"] < CLAUDE_USAGE_TTL:
        return cache.get("data"), cache.get("fetched_at")
    cache["checked_at"] = now
    try:
        raw = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-s", claude_keychain_service(acc["cfg_dir"]), "-w"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        oauth = (json.loads(raw) if raw else {}).get("claudeAiOauth") or {}
        token, exp = oauth.get("accessToken"), oauth.get("expiresAt")
        if token and not (isinstance(exp, (int, float)) and exp / 1000 < now):
            import urllib.request
            req = urllib.request.Request(
                "https://api.anthropic.com/api/oauth/usage?skip_spend=1",
                headers={"Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20",
                         "Content-Type": "application/json", "User-Agent": "herdr-env-usage"},
            )
            with urllib.request.urlopen(req, timeout=6) as r:
                data = json.loads(r.read().decode())
            if isinstance(data, dict) and ("limits" in data or "five_hour" in data):
                cache["data"], cache["fetched_at"] = data, now
    except Exception:
        pass
    try:
        atomic_write_json(cache_path, cache)
    except Exception:
        pass
    return cache.get("data"), cache.get("fetched_at")


def claude_windows(util, now):
    """利用状況（API / .claude.json のキャッシュ）を表示の枠にする。limits があればそれを使う
    （モデル別の週の枠、例えば Fable もここに入る）"""
    out = []
    limits = util.get("limits")
    if isinstance(limits, list) and limits:
        for l in limits:
            pct = l.get("percent")
            if not isinstance(pct, (int, float)):
                continue
            kind = l.get("kind") or ""
            scope = l.get("scope") or {}
            model = ((scope.get("model") or {}).get("display_name")) or ""
            surface = scope.get("surface") or ""
            label = {"session": "5h", "weekly_all": "7d"}.get(kind)
            if not label:
                label = "7d " + (model or surface or kind) if l.get("group") == "weekly" else (model or surface or kind)
            reset_at = parse_iso_epoch(l.get("resets_at"))
            out.append({
                "label": label, "remaining": max(0, min(100, round(100 - pct))),
                "resets_at": reset_at, "countdown": fmt_countdown(reset_at, now),
                "active": bool(l.get("is_active")), "severity": l.get("severity") or "",
            })
        return out
    for key, label in (("five_hour", "5h"), ("seven_day", "7d")):
        w = util.get(key)
        if not isinstance(w, dict) or not isinstance(w.get("utilization"), (int, float)):
            continue
        reset_at = parse_iso_epoch(w.get("resets_at"))
        out.append({"label": label, "remaining": max(0, min(100, round(100 - w["utilization"]))),
                    "resets_at": reset_at, "countdown": fmt_countdown(reset_at, now)})
    return out


STATUSLINE_KEEP = 8 * 86400  # 7d の枠より古い会話の分は消す


def window_key(resets_at, remaining):
    """どちらの値が新しいかの順序。使った量は枠がリセットされるまで減らないので、
    リセット時刻が遅い枠ほど新しく、同じ枠なら残りが少ない方が新しい。
    （statusLine の値は会話ごとに最後に API を呼んだときのもので、いつの値かは分からない。
    描いた時刻やファイルの更新時刻で選ぶと、手の空いた会話の古い値に入れ替わる）"""
    return (round(resets_at / 3600) if resets_at else 0, -remaining)


def claude_statusline(acc, now):
    """statusline.sh が会話ごとに残した入力の rate_limits から、5h / 7d それぞれ一番新しい値を選ぶ。
    返すのは {label: window} と、どれかの会話が最後に描いた時刻"""
    name = os.path.basename(acc["cfg_dir"]).lstrip(".")
    best, newest = {}, None
    for path in glob.glob(os.path.join(STATUSLINE_DIR, name, "*.json")):
        try:
            mtime = int(os.path.getmtime(path))
        except OSError:
            continue
        if now - mtime > STATUSLINE_KEEP:
            try:
                os.remove(path)
            except OSError:
                pass
            continue
        newest = max(newest or 0, mtime)
        rl = (read_json(path) or {}).get("rate_limits") or {}
        for key, label in (("five_hour", "5h"), ("seven_day", "7d")):
            w = rl.get(key)
            if not isinstance(w, dict) or not isinstance(w.get("used_percentage"), (int, float)):
                continue
            reset_at = w.get("resets_at") if isinstance(w.get("resets_at"), (int, float)) else None
            if reset_at and reset_at <= now:
                continue
            item = {"label": label, "remaining": max(0, min(100, round(100 - w["used_percentage"]))),
                    "resets_at": reset_at, "countdown": fmt_countdown(reset_at, now)}
            if label not in best or window_key(reset_at, item["remaining"]) > window_key(
                best[label]["resets_at"], best[label]["remaining"]
            ):
                best[label] = item
    return best, newest


def collect_claude(now):
    entries = []
    all_accounts = []
    lowest = None
    use_api = claude_usage_api_enabled()
    for acc in load_claude_accounts():
        live, live_at = claude_usage_live(acc, now) if use_api else (None, None)
        if live:
            util, fetched_at = live, live_at
        else:
            cu = (read_json(acc["json_path"]) or {}).get("cachedUsageUtilization") or {}
            util = cu.get("utilization") or {}
            fetched_ms = cu.get("fetchedAtMs")
            fetched_at = int(fetched_ms / 1000) if isinstance(fetched_ms, (int, float)) else None
        windows = [w for w in claude_windows(util, now) if not (w["resets_at"] and w["resets_at"] <= now)]

        # 5h / 7d は、API（または .claude.json）の値と会話ごとのステータスラインの値のうち新しい方
        sl, sl_at = claude_statusline(acc, now)
        for label, item in sl.items():
            old = next((x for x in windows if x["label"] == label), None)
            if not old:
                windows.insert(0 if label == "5h" else len([x for x in windows if x["label"] == "5h"]), item)
            elif window_key(item["resets_at"], item["remaining"]) > window_key(old["resets_at"], old["remaining"]):
                old.update(item)
        if sl_at:
            fetched_at = max(fetched_at or 0, sl_at)

        acc_low = min((w["remaining"] for w in windows), default=None)
        text = " ".join(f"{w['label']} {w['remaining']}%" for w in windows)
        detail_parts = [
            f"{w['label']} {w['remaining']}%" + (f" (reset {w['countdown']})" if w["countdown"] else "")
            for w in windows
        ]
        item = {
            "id": acc["id"],
            "badge": acc["badge"],
            "name": acc["name"],
            "plan": (util.get("plan") or "") if isinstance(util, dict) else "",
            "text": text,
            "lowest": acc_low,
            "windows": windows,
            "detail": f"{acc['badge']} {acc['name']}: " + (" / ".join(detail_parts) if detail_parts else "データなし"),
            "updated_at": fetched_at,
        }
        all_accounts.append(item)
        if windows:
            entries.append(item)
            if lowest is None or acc_low < lowest:
                lowest = acc_low

    summary = " · ".join(
        f"{e['badge']}{e['text']}" if len(entries) > 1 else e["text"] for e in entries
    )
    tooltip = "\n".join(e["detail"] for e in entries) if entries else "Claude: 取得データなし"
    return {
        "key": "claude",
        "name": "Claude",
        "text": summary or "—",
        "lowest": lowest,
        "accounts": all_accounts,
        "tooltip": tooltip,
    }


def collect_antigravity(now):
    snap = read_json(AGY_STATE)
    if not snap:
        return {
            "key": "antigravity",
            "name": "Antigravity",
            "plan": "",
            "model": "",
            "text": "—",
            "lowest": None,
            "windows": [],
            "cost_usd": None,
            "updated_at": None,
            "tooltip": "Antigravity: agy を開くと自動取得されます",
        }
    detailed, compact, lowest = parse_agy_quota(snap.get("quota") or {}, now)
    text = " ".join(f"{w['label']} {w['remaining']}%" for w in compact)
    lines = ["Antigravity" + (f" ({snap['plan_tier']})" if snap.get("plan_tier") else "")]
    if snap.get("model"):
        lines.append(f"Model: {snap['model']}")
    for w in detailed:
        lines.append(f"  {w['label']}: {w['remaining']}%" + (f" (reset {w['countdown']})" if w["countdown"] else ""))
    if isinstance(snap.get("cost_usd"), (int, float)) and snap["cost_usd"] > 0:
        lines.append(f"  Session est: ${snap['cost_usd']:.2f}")
    return {
        "key": "antigravity",
        "name": "Antigravity",
        "plan": snap.get("plan_tier") or "",
        "model": snap.get("model") or "",
        "text": text or "—",
        "lowest": lowest,
        "windows": detailed,
        "cost_usd": snap.get("cost_usd"),
        "updated_at": snap.get("updated_at"),
        "tooltip": "\n".join(lines),
    }


def read_tail_lines(path, max_bytes=262144):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            chunk = f.read().decode("utf-8", errors="ignore")
        return chunk.splitlines()
    except Exception:
        return []


def collect_codex(now):
    pattern = os.path.join(HOME, ".codex", "sessions", "*", "*", "*", "*.jsonl")
    files = glob.glob(pattern)
    if not files:
        return {
            "key": "codex",
            "name": "Codex",
            "plan": "",
            "text": "—",
            "lowest": None,
            "windows": [],
            "updated_at": None,
            "tooltip": "Codex: セッション履歴なし",
        }
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    rl = None
    src_mtime = None
    for path in files[:6]:
        for line in reversed(read_tail_lines(path)):
            if '"rate_limits"' not in line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("type") == "event_msg":
                payload = obj.get("payload") or {}
                if payload.get("type") == "token_count" and isinstance(payload.get("rate_limits"), dict):
                    rl = payload["rate_limits"]
                    src_mtime = int(os.path.getmtime(path))
                    break
        if rl:
            break

    if not rl:
        return {
            "key": "codex",
            "name": "Codex",
            "plan": "",
            "text": "—",
            "lowest": None,
            "windows": [],
            "updated_at": None,
            "tooltip": "Codex: rate_limits 未取得",
        }

    windows = []
    for slot in ("primary", "secondary"):
        w = rl.get(slot)
        if not isinstance(w, dict):
            continue
        used = w.get("used_percent")
        if not isinstance(used, (int, float)):
            continue
        resets_at = w.get("resets_at")
        lbl = fmt_window(w.get("window_minutes")) or slot
        if isinstance(resets_at, (int, float)) and resets_at <= now:
            rem = 100
            cd = ""
        else:
            rem = max(0, min(100, round(100 - used)))
            cd = fmt_countdown(resets_at, now)
        windows.append({"label": lbl, "remaining": rem, "resets_at": resets_at, "countdown": cd})

    lowest = min((w["remaining"] for w in windows), default=None)
    text = " ".join(f"{w['label']} {w['remaining']}%" for w in windows)
    plan = rl.get("plan_type") or ""
    lines = ["Codex" + (f" ({plan})" if plan else "")]
    for w in windows:
        lines.append(f"  {w['label']}: {w['remaining']}%" + (f" (reset {w['countdown']})" if w["countdown"] else ""))
    return {
        "key": "codex",
        "name": "Codex",
        "plan": plan,
        "text": text or "—",
        "lim_hc": "  ".join(f"{w['label']} {w['remaining']}%" for w in windows),
        "lowest": lowest,
        "windows": windows,
        "updated_at": src_mtime,
        "tooltip": "\n".join(lines),
    }


def collect_gemini(now):
    today_start = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    oauth_exists = os.path.isfile(os.path.join(HOME, ".gemini", "oauth_creds.json"))
    snap = read_json(GEMINI_STATE) or {}

    total_tokens = 0
    sessions_today = 0
    for path in glob.glob(os.path.join(HOME, ".gemini", "tmp", "*", "chats", "*.json")):
        try:
            if os.path.getmtime(path) < today_start:
                continue
            doc = read_json(path) or {}
            sessions_today += 1
            for msg in doc.get("messages") or []:
                tok = msg.get("tokens") or {}
                if isinstance(tok, dict):
                    total_tokens += int(tok.get("input", 0) or 0) + int(tok.get("output", 0) or 0)
        except Exception:
            continue

    tok_str = fmt_tokens(total_tokens)
    if tok_str:
        text = tok_str
    elif oauth_exists:
        text = "0 tok"
    else:
        text = "—"

    lines = ["Gemini CLI"]
    lines.append("  Auth: OAuth ログイン済み" if oauth_exists else "  Auth: 未ログイン（APIキーまたは Antigravity 枠）")
    lines.append(f"  Today: {tok_str or '0'} tokens ({sessions_today} sessions)")
    return {
        "key": "gemini",
        "name": "Gemini",
        "oauth": oauth_exists,
        "tokens_today": total_tokens,
        "tokens_today_str": tok_str or "0",
        "sessions_today": sessions_today,
        "text": text,
        "lowest": None,
        "updated_at": snap.get("updated_at"),
        "tooltip": "\n".join(lines),
    }


def sync_hc_codex(codex_info, now):
    lim = codex_info.get("lim_hc")
    if not lim:
        return
    try:
        res = subprocess.run(["hc", "ls", "--json"], capture_output=True, text=True, timeout=3)
        if res.returncode != 0:
            return
        data = json.loads(res.stdout)
        terms = data.get("terminals") if isinstance(data, dict) else data
        for t in terms or []:
            if t.get("state") != "live":
                continue
            agent = (t.get("agent") or "").lower()
            if "codex" not in agent:
                continue
            addr = t.get("address")
            if not addr:
                continue
            subprocess.run(
                [
                    "hc",
                    "label",
                    addr,
                    "herdr-env.acct_id=codex",
                    "herdr-env.acct=◎ Codex",
                    f"herdr-env.lim={lim}",
                    f"herdr-env.lim_at={int(now)}",
                ],
                capture_output=True,
                timeout=3,
            )
    except Exception:
        pass


def main():
    args = sys.argv[1:]
    if "--record-agy" in args:
        record_agy()
        return
    if "--record-gemini" in args:
        record_gemini()
        return

    now = time.time()
    claude = collect_claude(now)
    agy = collect_antigravity(now)
    codex = collect_codex(now)
    gemini = collect_gemini(now)

    if "--sync-hc" in args:
        sync_hc_codex(codex, now)

    out = {
        "updated_at": int(now),
        "tools": [claude, agy, codex, gemini],
    }
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
