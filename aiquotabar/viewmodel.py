"""Platform-independent view model.

Turns the app's raw state (UsageData, ProviderData, history, config) into
plain JSON-able dicts that the web UI renders. Nothing in here touches
AppKit, so it runs (and is tested) on any OS. The native shell in ui.py and
the screenshot tool both feed their state through these functions.
"""

from __future__ import annotations

import base64
import os
import time
from dataclasses import dataclass, field

from aiquotabar import theme
from aiquotabar.config import (
    NOTIF_DEFAULTS, PACING_ALERT_MINUTES, REFRESH_INTERVALS,
    notif_enabled, severity, thresholds,
)
from aiquotabar.history import _calc_eta_minutes
from aiquotabar.providers import (
    LimitRow, ProviderData, UsageData, PROVIDER_REGISTRY, COOKIE_PROVIDERS,
    fmt_reset_ts,
)

REPO_URL = "https://github.com/yagcioglutoprak/AIQuotaBar"

# Cookie-provider config keys by provider id (Claude uses "cookie_str").
COOKIE_KEYS = {
    "claude":  "cookie_str",
    "chatgpt": "chatgpt_cookies",
    "copilot": "copilot_cookies",
    "cursor":  "cursor_cookies",
}

USAGE_PAGES = {
    "claude":  "https://claude.ai/settings/usage",
    "chatgpt": "https://chatgpt.com/codex/settings/usage",
    "cursor":  "https://cursor.com/dashboard?tab=usage",
    "copilot": "https://github.com/settings/billing/premium_requests_usage",
}

SIGN_IN_DOMAINS = {
    "claude": "claude.ai", "chatgpt": "chatgpt.com",
    "cursor": "cursor.com", "copilot": "github.com",
}


ASSET_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


def boot_assets(asset_dir: str = ASSET_DIR) -> dict:
    """Provider icons as data: URIs, sent to the page once after it loads.

    data: URIs keep the share-card canvas untainted (file:// images would
    block toDataURL) and avoid WebKit's file-access rules altogether.
    """
    icons = {}
    for pid, p in theme.PROVIDERS.items():
        try:
            with open(os.path.join(asset_dir, p["icon"]), "rb") as f:
                icons[pid] = "data:image/png;base64," + base64.b64encode(f.read()).decode()
        except OSError:
            icons[pid] = ""
    return {"icons": icons}


# ── snapshot ─────────────────────────────────────────────────────────────────

@dataclass
class Snapshot:
    """Everything the UI needs, captured at one instant."""
    config: dict
    claude: UsageData | None = None
    claude_error: dict | None = None          # {"kind": "auth"|"missing"|"network", "message": str}
    providers: list[ProviderData] = field(default_factory=list)
    cc_stats: dict | None = None
    history: dict = field(default_factory=dict)       # short-term history (burn rate)
    trends: dict = field(default_factory=dict)        # history key -> [(ts, pct)]
    week_hits: dict = field(default_factory=dict)     # history key -> int
    updated_at: float | None = None
    fetching: bool = False
    detecting: set = field(default_factory=set)       # provider ids being auto-detected
    now: float = field(default_factory=time.time)


# ── formatting ───────────────────────────────────────────────────────────────

def fmt_duration(secs: float) -> str:
    """47m, 1h 30m, 2d 4h."""
    secs = max(0, int(secs))
    if secs < 3600:
        return f"{max(1, secs // 60)}m"
    if secs < 86400:
        h, rem = divmod(secs, 3600)
        m = rem // 60
        return f"{h}h {m}m" if m else f"{h}h"
    d, rem = divmod(secs, 86400)
    h = rem // 3600
    return f"{d}d {h}h" if h else f"{d}d"


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def fmt_count(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def history_key(prefix: str, label: str) -> str:
    """History key for a multi-row provider row (matches ui.py recording)."""
    return f"{prefix}_{label.lower().replace(' ', '_')}"


_CLAUDE_LABELS = {
    "Current Session": ("Current session", "5-hour window"),
    "All Models":      ("Weekly · all models", "7-day window"),
    "Sonnet Only":     ("Weekly · Sonnet", "7-day window"),
    "Opus Only":       ("Weekly · Opus", "7-day window"),
}


def _humanize(label: str) -> str:
    """'Codex Tasks Weekly' -> 'Codex tasks · weekly'."""
    words = label.split()
    tail = ""
    if len(words) > 1 and words[-1] in ("Weekly", "Long"):
        tail = " · " + words[-1].lower()
        words = words[:-1]
    base = " ".join(words)
    if base.isupper():          # "API"
        return base + tail
    parts = base.split(" ")
    base = " ".join([parts[0]] + [w if w.isupper() else w.lower() for w in parts[1:]])
    return base + tail


# ── meters ───────────────────────────────────────────────────────────────────

def _pace(resets_at: float | None, window: int | None, now: float) -> float | None:
    """Share of the window already elapsed, 0–100 (where an even pace would be)."""
    if not resets_at or not window:
        return None
    remaining = resets_at - now
    if remaining <= 0 or remaining > window:
        return None
    return round((1 - remaining / window) * 100, 1)


def _note(pct: int, resets_at: float | None, window: int | None,
          eta_min: int | None, now: float) -> dict | None:
    """The one line of advice a meter shows, or None when there's nothing to say."""
    remaining = (resets_at - now) if resets_at else None
    if pct >= 100:
        if remaining and remaining > 0:
            return {"tone": "crit", "text": f"Limit reached · back in {fmt_duration(remaining)}"}
        return {"tone": "crit", "text": "Limit reached"}
    # Recent burn rate (short-term history regression)
    if eta_min is not None and (remaining is None or eta_min * 60 < remaining):
        tone = "crit" if eta_min <= PACING_ALERT_MINUTES else "warn"
        return {"tone": tone, "text": f"Runs out in ~{fmt_duration(eta_min * 60)} at this pace"}
    # Whole-window average pace
    if remaining and window and 0 < remaining <= window and pct >= 10:
        elapsed = window - remaining
        if elapsed >= window * 0.1:
            rate = pct / elapsed
            t_full = (100 - pct) / rate
            if t_full < remaining:
                return {"tone": "warn",
                        "text": f"Runs out in ~{fmt_duration(t_full)} at this pace"}
    return None


def build_meter(key: str | None, label: str, sublabel: str, pct: int,
                resets_at: float | None, window: int | None, reset_str: str,
                snap: Snapshot) -> dict:
    eta = _calc_eta_minutes(snap.history, key, snap.now) if key else None
    sev = severity(pct, snap.config)
    reset_text = fmt_reset_ts(resets_at, snap.now) if resets_at else reset_str
    return {
        "key": key,
        "label": label,
        "sublabel": sublabel,
        "pct": int(pct),
        "severity": sev,
        "reset_ts": resets_at,
        "reset_text": _cap(reset_text),
        "pace_pct": _pace(resets_at, window, snap.now),
        "note": _note(pct, resets_at, window, eta, snap.now),
    }


def _row_meter(key, row: LimitRow, label: str, sublabel: str, snap: Snapshot) -> dict:
    return build_meter(key, label, sublabel, row.pct, row.resets_at,
                       row.window_secs, row.reset_str, snap)


# ── provider cards ───────────────────────────────────────────────────────────

def _brand(pid: str) -> dict:
    p = theme.PROVIDERS[pid]
    return {"id": pid, "name": p["name"], "color": p["color"],
            "icon": p["icon"], "mask": p["tint"] is not None}


def _friendly_error(pid: str, message: str | None) -> dict:
    """Map a raw fetch error to something a person can act on."""
    msg = (message or "").lower()
    name = theme.PROVIDERS[pid]["name"]
    domain = SIGN_IN_DOMAINS[pid]
    if any(s in msg for s in ("401", "403", "not logged in", "session", "expired", "unauthorized")):
        return {"title": "Signed out",
                "detail": f"Sign in to {domain} in your browser, then reconnect.",
                "action": {"id": "detect", "provider": pid, "label": "Reconnect"}}
    if any(s in msg for s in ("timed out", "timeout", "resolve", "connection", "network")):
        return {"title": "Can't reach " + name,
                "detail": "Check your connection. Retrying automatically.",
                "action": {"id": "refresh", "label": "Retry"}}
    return {"title": f"{name} didn't respond",
            "detail": (message or "Unknown error")[:90],
            "action": {"id": "refresh", "label": "Retry"}}


def _card_status(meters: list[dict]) -> dict | None:
    worst = max((m["pct"] for m in meters), default=0)
    sevs = {m["severity"] for m in meters}
    if worst >= 100:
        return {"tone": "crit", "text": "Limit reached"}
    if "crit" in sevs:
        return {"tone": "crit", "text": "Almost out"}
    if "warn" in sevs or any((m["note"] or {}).get("tone") in ("warn", "crit") for m in meters):
        return {"tone": "warn", "text": "Running low"}
    return None


def _trend(snap: Snapshot, key: str) -> dict | None:
    pts = snap.trends.get(key) or []
    if len(pts) < 3:
        return None
    vals = [p for _, p in pts]
    if max(vals) - min(vals) < 1 and max(vals) == 0:
        return None
    return {"key": key, "points": [[round(t), int(p)] for t, p in pts],
            "since": snap.now - 24 * 3600, "until": snap.now}


def _claude_card(snap: Snapshot) -> dict | None:
    cfg = snap.config
    if "claude" in cfg.get("disabled_providers", []):
        return None
    card = _brand("claude")
    card["usage_url"] = USAGE_PAGES["claude"]
    data = snap.claude
    rows = []
    if data:
        rows = [(r, k) for r, k in (
            (data.session, "claude"), (data.weekly_all, None),
            (data.weekly_sonnet, None), (data.weekly_opus, None)) if r]
    if rows:
        meters = []
        for row, key in rows:
            label, sub = _CLAUDE_LABELS.get(row.label, (row.label, ""))
            meters.append(_row_meter(key, row, label, sub, snap))
        card.update(state="ok", meters=meters, status=_card_status(meters),
                    trend=_trend(snap, "claude"),
                    hits=snap.week_hits.get("claude", 0))
        if snap.claude_error and snap.claude_error.get("kind") in ("auth", "network"):
            card["stale"] = True
        return card
    err = snap.claude_error
    if "claude" in snap.detecting or (err is None and snap.fetching):
        card.update(state="loading", meters=[])
        return card
    if err is None:
        return None
    if err.get("kind") == "missing":
        card.update(state="error", meters=[], error={
            "title": "Not connected",
            "detail": "Sign in to claude.ai in your browser, then detect.",
            "action": {"id": "detect", "provider": "claude", "label": "Detect"}})
    else:
        card.update(state="error", meters=[],
                    error=_friendly_error("claude", err.get("message")))
    return card


def _multi_row_card(pid: str, pd: ProviderData, snap: Snapshot) -> dict:
    card = _brand(pid)
    card["usage_url"] = USAGE_PAGES[pid]
    rows = getattr(pd, "_rows", None) or []
    meters = []
    for row in rows:
        key = history_key(pid, row.label)
        label = _humanize(row.label)
        if pid == "cursor":
            label = f"{row.label} usage"
            sub = "Included in plan"
        else:
            sub = ("5-hour window" if row.window_secs == 5 * 3600 else
                   "7-day window" if row.window_secs == 7 * 86400 else "")
        meters.append(_row_meter(key, row, label, sub, snap))
    main_key = meters[0]["key"] if meters else None
    card.update(state="ok", meters=meters, status=_card_status(meters),
                trend=_trend(snap, main_key) if main_key else None,
                hits=sum(snap.week_hits.get(m["key"], 0) for m in meters))
    return card


def _copilot_card(pd: ProviderData, snap: Snapshot) -> dict:
    card = _brand("copilot")
    card["usage_url"] = USAGE_PAGES["copilot"]
    meters = []
    if pd.pct is not None:
        meters.append(build_meter("copilot", "Premium requests", "Monthly allowance",
                                  pd.pct, pd.resets_at, pd.window_secs, pd.reset_str, snap))
    summary = None
    if pd.spent is not None and pd.limit:
        summary = f"{int(pd.spent)} / {int(pd.limit)}"
    elif pd.spent is not None:
        summary = f"{int(pd.spent)} used"
    card.update(state="ok", meters=meters, summary=summary,
                status=_card_status(meters), trend=_trend(snap, "copilot"),
                hits=snap.week_hits.get("copilot", 0))
    return card


def _provider_card(pid: str, snap: Snapshot) -> dict | None:
    cfg = snap.config
    if pid in cfg.get("disabled_providers", []):
        return None
    name = theme.PROVIDERS[pid]["name"]
    pd = next((p for p in snap.providers if p.name == name), None)
    if pd is None:
        if pid in snap.detecting:
            card = _brand(pid)
            card.update(state="loading", meters=[])
            return card
        return None
    if pd.error:
        card = _brand(pid)
        card["usage_url"] = USAGE_PAGES[pid]
        card.update(state="error", meters=[], error=_friendly_error(pid, pd.error))
        return card
    if pid == "copilot":
        return _copilot_card(pd, snap)
    return _multi_row_card(pid, pd, snap)


def _extras(snap: Snapshot) -> list[dict]:
    """Secondary, non-quota rows: Claude Code activity and API spend."""
    out = []
    cc = snap.cc_stats
    if cc and (cc.get("today_messages") or cc.get("week_messages")):
        rows = [{"label": "Today",
                 "value": f"{fmt_count(cc.get('today_messages', 0))} messages · "
                          f"{cc.get('today_sessions', 0)} sessions"},
                {"label": "This week",
                 "value": f"{fmt_count(cc.get('week_messages', 0))} messages · "
                          f"{fmt_count(cc.get('week_tool_calls', 0))} tool calls"}]
        out.append({"id": "claude_code", "name": "Claude Code", "glyph": "terminal",
                    "color": theme.brand_color("claude"), "rows": rows})
    known = {p["name"] for p in theme.PROVIDERS.values()}
    for pd in snap.providers:
        if pd.name in known:
            continue
        sym = "¥" if pd.currency == "CNY" else ("$" if pd.currency == "USD" else "")
        item = {"id": pd.name.lower().split()[0], "name": f"{pd.name} API",
                "glyph": "key", "color": theme.NEUTRAL, "rows": []}
        if pd.error:
            item["rows"].append({"label": "Error", "value": pd.error[:60], "tone": "warn"})
        elif pd.pct is not None:
            item["rows"].append({"label": _cap(pd.period),
                                 "value": f"{sym}{pd.spent:.2f} of {sym}{pd.limit:.2f}"})
            item["meter"] = {"pct": pd.pct, "severity": severity(pd.pct, snap.config)}
        elif pd.balance is not None:
            item["rows"].append({"label": "Balance", "value": f"{sym}{pd.balance:.2f}"})
        elif pd.spent is not None:
            item["rows"].append({"label": _cap(pd.period), "value": f"{sym}{pd.spent:.2f}"})
        out.append(item)
    return out


_SEV_RANK = {"ok": 0, "warn": 1, "crit": 2}


def _pick_hero(cards: list[dict]) -> dict | None:
    """The binding constraint: the meter closest to cutting you off.

    Marks the chosen meter `hero` (its card then doesn't repeat the note)
    and keeps the 24h trend only on the hero's card to keep the panel short.
    """
    best, best_score = None, None
    for card in cards:
        for i, m in enumerate(card.get("meters") or []):
            note_tone = (m["note"] or {}).get("tone")
            score = (_SEV_RANK[m["severity"]],
                     1 if note_tone in ("warn", "crit") else 0,
                     m["pct"], -i)
            if best_score is None or score > best_score:
                best, best_score = (card, m), score
    if best is None:
        return None
    card, m = best
    m["hero"] = True
    for other in cards:
        if other is not card:
            other["trend"] = None
    left = max(0, 100 - m["pct"])
    insight = m["note"]
    if left == 0:
        insight = None      # the headline already says it; the sub line says when it's back
    elif insight is None:
        pace = m["pace_pct"]
        if pace is not None and m["pct"] - pace > 10:
            insight = {"tone": "info",
                       "text": f"Using it faster than an even pace (+{round(m['pct'] - pace)}%)"}
        elif pace is not None and pace - m["pct"] > 10:
            insight = {"tone": "good", "text": "Comfortably under an even pace"}
        elif pace is not None:
            insight = {"tone": "good", "text": "Right on an even pace"}
        elif m["pct"] < 50:
            insight = {"tone": "good", "text": "Plenty of headroom"}
        else:
            insight = {"tone": "info", "text": "Worth keeping an eye on"}
    return {
        "provider": card["id"], "name": card["name"], "color": card["color"],
        "icon": card["icon"], "mask": card["mask"],
        "label": m["label"], "pct": m["pct"], "severity": m["severity"],
        "headline": "Limit reached" if left == 0 else f"{left}% left",
        "reset_ts": m["reset_ts"], "reset_text": m["reset_text"],
        "pace_pct": m["pace_pct"], "insight": insight,
    }


def _connect_hint(snap: Snapshot, shown: set) -> list[dict]:
    disabled = set(snap.config.get("disabled_providers", []))
    return [_brand(pid) for pid in theme.PROVIDER_ORDER
            if pid not in shown and pid not in disabled]


def build_panel_state(snap: Snapshot) -> dict:
    cards = []
    c = _claude_card(snap)
    if c:
        cards.append(c)
    for pid in theme.PROVIDER_ORDER[1:]:
        card = _provider_card(pid, snap)
        if card:
            cards.append(card)

    interval = snap.config.get("refresh_interval", 300)
    stale = bool(snap.updated_at and snap.now - snap.updated_at > max(3 * interval, 900))
    has_data = any(card.get("meters") for card in cards)
    if has_data:
        state = "ok"
    elif snap.fetching or snap.detecting:
        state = "loading"
    elif cards:
        state = "error"
    else:
        state = "empty"
    warn, crit = thresholds(snap.config)
    return {
        "view": "panel",
        "state": state,
        "now": snap.now,
        "hero": _pick_hero(cards),
        "cards": cards,
        "extras": _extras(snap),
        "connect": _connect_hint(snap, {c["id"] for c in cards}),
        "footer": {
            "updated_ts": snap.updated_at,
            "fetching": snap.fetching,
            "stale": stale,
            "interval_text": next((k for k, v in REFRESH_INTERVALS.items() if v == interval),
                                  f"{interval // 60} min"),
        },
        "thresholds": {"warn": warn, "crit": crit},
        "share": share_summary(cards),
    }


def share_summary(cards: list[dict]) -> dict:
    """Numbers for the share card + the text for 'Post on X'."""
    items = []
    for card in cards:
        meters = card.get("meters") or []
        if not meters:
            continue
        m = meters[0]
        items.append({"id": card["id"], "name": card["name"], "color": card["color"],
                      "icon": card["icon"], "mask": card["mask"],
                      "label": m["label"], "pct": m["pct"], "severity": m["severity"],
                      "reset_text": m["reset_text"],
                      "extra": [{"label": x["label"], "pct": x["pct"]} for x in meters[1:3]]})
    parts = [f"{i['name']} {i['pct']}%" for i in items]
    stats = " · ".join(parts) if parts else "my AI usage"
    return {
        "items": items,
        "text": f"{stats} — tracking my AI limits live in the macOS menu bar with AIQuotaBar",
        "url": REPO_URL,
    }


# ── status bar ───────────────────────────────────────────────────────────────

def bar_segments(snap: Snapshot) -> list[dict]:
    """Which providers the menu bar shows, with the percentage for each.

    Claude uses its session (5-hour) limit — it decides whether you can keep
    working right now. Other providers use their worst row.
    """
    available: dict[str, dict] = {}
    data = snap.claude
    if data:
        primary = data.session or data.weekly_all or data.weekly_sonnet
        if primary:
            weekly_maxed = any(r and r.pct >= thresholds(snap.config)[1]
                               for r in (data.weekly_all, data.weekly_sonnet, data.weekly_opus))
            available["Claude"] = {
                "id": "claude", "name": "Claude", "pct": primary.pct,
                "severity": severity(primary.pct, snap.config),
                "reset_ts": primary.resets_at,
                "weekly_maxed": weekly_maxed and primary is data.session
                                and severity(primary.pct, snap.config) != "crit",
            }
    for pd in snap.providers:
        pid = theme.NAME_TO_ID.get(pd.name)
        if not pid or pd.error:
            continue
        rows = getattr(pd, "_rows", None) or []
        if rows:
            worst = max(rows, key=lambda r: r.pct)
            pct, reset_ts = worst.pct, worst.resets_at
        elif pd.pct is not None:
            pct, reset_ts = pd.pct, pd.resets_at
        else:
            continue
        available[pd.name] = {"id": pid, "name": pd.name, "pct": pct,
                              "severity": severity(pct, snap.config),
                              "reset_ts": reset_ts, "weekly_maxed": False}
    order = [theme.PROVIDERS[p]["name"] for p in theme.PROVIDER_ORDER]
    chosen = snap.config.get("bar_providers")
    if chosen:
        return [available[n] for n in chosen if n in available]
    return [available[n] for n in order if n in available][:2]


def bar_auto_names(snap: Snapshot) -> list[str]:
    cfg = dict(snap.config)
    cfg.pop("bar_providers", None)
    return [s["name"] for s in bar_segments(Snapshot(**{**snap.__dict__, "config": cfg}))]


# ── fallback text menu (used when WebKit is unavailable) ─────────────────────

def menu_lines(panel: dict) -> list[str | None]:
    """Plain-text rendering of the panel for a classic NSMenu."""
    def bar(pct: int, width: int = 14) -> str:
        filled = round(pct / 100 * width)
        return "█" * filled + "░" * (width - filled)

    dot = {"ok": "🟢", "warn": "🟡", "crit": "🔴"}
    lines: list[str | None] = []
    for card in panel["cards"]:
        lines.append(card["name"].upper())
        if card.get("error"):
            lines.append(f"  ⚠️  {card['error']['title']} — {card['error']['detail']}")
        for m in card.get("meters") or []:
            lines.append(f"  {dot[m['severity']]} {m['label']}  {m['pct']}%")
            tail = f"  {m['reset_text']}" if m["reset_text"] else ""
            lines.append(f"  {bar(m['pct'])}{tail}")
            if m["note"]:
                lines.append(f"  ⏱ {m['note']['text']}")
        if card.get("summary"):
            lines.append(f"  {card['summary']}")
        lines.append(None)
    for x in panel["extras"]:
        lines.append(x["name"].upper())
        for r in x["rows"]:
            lines.append(f"  {r['label']}  {r['value']}")
        lines.append(None)
    return lines


# ── settings ─────────────────────────────────────────────────────────────────

NOTIF_GROUPS = [
    ("claude", [("claude_warning", "Usage warnings"), ("claude_pacing", "Pace alerts"),
                ("claude_reset", "Reset alerts")]),
    ("chatgpt", [("chatgpt_warning", "Usage warnings"), ("chatgpt_pacing", "Pace alerts"),
                 ("chatgpt_reset", "Reset alerts")]),
    ("cursor", [("cursor_warning", "Usage warnings"), ("cursor_pacing", "Pace alerts")]),
    ("copilot", [("copilot_pacing", "Pace alerts")]),
]


def _mask_key(key: str) -> str:
    return (key[:3] + "…" + key[-4:]) if len(key) > 10 else "••••"


def build_settings_state(cfg: dict, *, snap: Snapshot | None = None,
                         launch_at_login: bool = False, widget_installed: bool = False,
                         version: str = "", tab: str | None = None) -> dict:
    snap = snap or Snapshot(config=cfg)
    disabled = set(cfg.get("disabled_providers", []))
    accounts = []
    for pid in theme.PROVIDER_ORDER:
        b = _brand(pid)
        name = b["name"]
        connected = bool(cfg.get(COOKIE_KEYS[pid]))
        err = None
        if pid == "claude":
            if snap.claude_error and snap.claude_error.get("kind") != "missing":
                err = snap.claude_error.get("message")
        else:
            pd = next((p for p in snap.providers if p.name == name), None)
            err = pd.error if pd else None
        if pid in disabled:
            status, detail = "off", "Turned off"
        elif pid in snap.detecting:
            status, detail = "busy", "Looking in your browsers…"
        elif err and connected:
            status, detail = "error", _friendly_error(pid, err)["title"]
        elif connected:
            status, detail = "on", "Connected via browser session"
        else:
            status, detail = "missing", f"Sign in to {SIGN_IN_DOMAINS[pid]} in your browser"
        b.update(status=status, detail=detail, manual=pid == "claude",
                 usage_url=USAGE_PAGES[pid])
        accounts.append(b)

    api_keys = []
    for cfg_key, (name, _fn) in PROVIDER_REGISTRY.items():
        if cfg_key in COOKIE_PROVIDERS:
            continue
        val = cfg.get(cfg_key) or ""
        api_keys.append({"key": cfg_key, "name": name, "set": bool(val),
                         "masked": _mask_key(val) if val else ""})

    notifs = []
    for pid, items in NOTIF_GROUPS:
        notifs.append({"provider": pid, "name": theme.PROVIDERS[pid]["name"],
                       "color": theme.PROVIDERS[pid]["color"],
                       "items": [{"key": k, "label": lbl, "on": notif_enabled(cfg, k)}
                                 for k, lbl in items]})
    warn, crit = thresholds(cfg)
    names = [theme.PROVIDERS[p]["name"] for p in theme.PROVIDER_ORDER]
    chosen = cfg.get("bar_providers") or []
    auto = bar_auto_names(snap) if not chosen else []
    return {
        "view": "settings",
        "tab": tab or "general",
        "version": version,
        "general": {
            "launch_at_login": launch_at_login,
            "refresh_interval": cfg.get("refresh_interval", 300),
            "intervals": [{"secs": v, "label": k} for k, v in REFRESH_INTERVALS.items()],
            "warn": warn, "crit": crit,
        },
        "menubar": {
            "auto": not chosen,
            "providers": [{"name": n, "id": theme.NAME_TO_ID[n],
                           "color": theme.PROVIDERS[theme.NAME_TO_ID[n]]["color"],
                           "icon": theme.PROVIDERS[theme.NAME_TO_ID[n]]["icon"],
                           "mask": theme.PROVIDERS[theme.NAME_TO_ID[n]]["tint"] is not None,
                           "on": (n in chosen) if chosen else (n in auto)} for n in names],
            "show_reset": bool(cfg.get("bar_show_reset", False)),
            "show_cc": bool(cfg.get("bar_show_cc", True)),
        },
        "accounts": accounts,
        "api_keys": api_keys,
        "notifications": notifs,
        "widget": {"installed": widget_installed},
        "repo_url": REPO_URL,
    }


_BOOL_SETTINGS = {"bar_show_reset", "bar_show_cc"}


def apply_setting(cfg: dict, key: str, value) -> bool:
    """Validate and apply one setting coming from the web UI.

    Returns True if cfg changed. Anything not on the allow-list is rejected,
    so the page can never write arbitrary keys (like cookies) into the config.
    """
    if key == "refresh_interval":
        if value in REFRESH_INTERVALS.values():
            cfg[key] = value
            return True
        return False
    if key in _BOOL_SETTINGS:
        if isinstance(value, bool):
            cfg[key] = value
            return True
        return False
    if key == "bar_providers":
        names = {p["name"] for p in theme.PROVIDERS.values()}
        if value is None or value == []:
            cfg.pop("bar_providers", None)
            return True
        if isinstance(value, list) and all(isinstance(v, str) and v in names for v in value):
            order = [theme.PROVIDERS[p]["name"] for p in theme.PROVIDER_ORDER]
            cfg[key] = [n for n in order if n in value]
            return True
        return False
    if key in ("warn_threshold", "crit_threshold"):
        if not isinstance(value, int) or isinstance(value, bool):
            return False
        warn, crit = thresholds(cfg)
        warn, crit = (value, crit) if key == "warn_threshold" else (warn, value)
        if not (50 <= warn < crit <= 100):
            return False
        cfg["warn_threshold"], cfg["crit_threshold"] = warn, crit
        return True
    if key.startswith("notifications."):
        nkey = key.split(".", 1)[1]
        if nkey in NOTIF_DEFAULTS and isinstance(value, bool):
            cfg.setdefault("notifications", {})[nkey] = value
            return True
        return False
    return False


# ── history window ───────────────────────────────────────────────────────────

def history_label(key: str) -> str:
    if key == "claude":
        return "Claude · session"
    if key == "copilot":
        return "Copilot · premium requests"
    for pid in ("chatgpt", "cursor"):
        if key.startswith(pid + "_"):
            rest = key[len(pid) + 1:].replace("_", " ")
            rest = "API" if rest == "api" else rest
            return f"{theme.PROVIDERS[pid]['name']} · {rest}"
    return key.replace("_", " ").title()


def build_history_state(daily: list[tuple], trends: dict, now: float | None = None) -> dict:
    """daily: rows of (date, key, peak_pct, avg_pct, limit_hits) for up to 90 days
    (today included). trends: key -> [(ts, pct)] for the last 24 hours."""
    now = now or time.time()
    series: dict[str, dict] = {}
    for date, key, peak, avg, hits in daily:
        s = series.setdefault(key, {"key": key, "label": history_label(key),
                                    "provider": next((p for p in theme.PROVIDER_ORDER
                                                      if key == p or key.startswith(p + "_")), None),
                                    "color": theme.history_color(key), "days": []})
        s["days"].append({"date": date, "peak": int(peak), "avg": int(avg), "hits": int(hits)})
    order = {p: i for i, p in enumerate(theme.PROVIDER_ORDER)}
    out = sorted(series.values(), key=lambda s: (order.get(s["provider"], 99), s["key"]))
    out = [s for s in out if any(d["peak"] > 0 for d in s["days"])]
    return {
        "view": "history",
        "now": now,
        "series": out,
        "trends": [{"key": k, "label": history_label(k), "color": theme.history_color(k),
                    "points": [[round(t), int(p)] for t, p in pts]}
                   for k, pts in trends.items() if len(pts) >= 2],
    }
