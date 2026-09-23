"""Deterministic demo data.

Used by `python3 claude_bar.py --demo` (record README GIFs without real
accounts), by tools/screenshots.py, and by the tests. Nothing here touches
the network, the browser, or the user's config.
"""

from __future__ import annotations

import math
import random
import time
from datetime import datetime, timedelta, timezone

from aiquotabar.providers import LimitRow, ProviderData, UsageData, fmt_reset_ts
from aiquotabar.viewmodel import Snapshot

H, D = 3600, 86400


def _row(label: str, pct: int, resets_in: float, window: int, now: float) -> LimitRow:
    ts = now + resets_in
    return LimitRow(label, pct, fmt_reset_ts(ts, now), ts, window)


def _session_trend(now: float, current: int, reset_in: float = 2 * H + 14 * 60,
                   seed: int = 7) -> list[tuple[float, int]]:
    """24h of 5-hour sessions: ramps during work hours, resets every window."""
    rng = random.Random(seed)
    cur_start = now + reset_in - 5 * H
    pts: list[list[float]] = []
    t, pct, prev = now - 24 * H, 0.0, None
    while t < now:
        k = math.floor((t - cur_start) / (5 * H))
        if k != prev:
            pct, prev = 0.0, k
        hour = datetime.fromtimestamp(t).hour
        busy = 1.0 if 9 <= hour < 19 else (0.35 if 19 <= hour < 24 else 0.03)
        pct = min(100.0, pct + rng.random() * 3.4 * busy)
        pts.append([t, pct])
        t += 15 * 60
    # Scale the current session so it lands exactly on the live value.
    cur = [i for i, (tt, _) in enumerate(pts) if tt >= cur_start]
    if cur and pts[cur[-1]][1] > 0:
        f = current / pts[cur[-1]][1]
        for i in cur:
            pts[i][1] *= f
    pts.append([now, current])
    return [(tt, int(min(100, p))) for tt, p in pts]


def _burn_history(key: str, start_pct: int, end_pct: int, now: float) -> list[dict]:
    """Short-term history that gives the burn-rate regression a slope."""
    n = 31
    return [{"t": now - (n - 1 - i) * 60,
             "pct": round(start_pct + (end_pct - start_pct) * i / (n - 1))}
            for i in range(n)]


def demo_snapshot(variant: str = "default", now: float | None = None) -> Snapshot:
    now = now or time.time()
    # Placeholder values so account rows read as connected; demo mode never saves config.
    cfg: dict = {"refresh_interval": 300, "cookie_str": "demo", "chatgpt_cookies": "demo",
                 "cursor_cookies": "demo", "copilot_cookies": "demo"}

    claude = UsageData(
        session=_row("Current Session", 78, 2 * H + 14 * 60, 5 * H, now),
        weekly_all=_row("All Models", 41, 3 * D + 5 * H, 7 * D, now),
        weekly_sonnet=_row("Sonnet Only", 23, 3 * D + 5 * H, 7 * D, now),
    )
    chatgpt = ProviderData("ChatGPT", spent=64.0, limit=100.0, currency="")
    chatgpt._rows = [
        _row("Codex Tasks", 22, 3 * H + 40 * 60, 5 * H, now),
        _row("Codex Tasks Weekly", 64, 2 * D + 6 * H, 7 * D, now),
    ]
    cursor = ProviderData("Cursor", spent=48.0, limit=100.0, currency="")
    cursor._rows = [
        _row("Auto", 48, 11 * D + 3 * H, 30 * D, now),
        _row("API", 12, 11 * D + 3 * H, 30 * D, now),
    ]
    copilot_reset = now + 8 * D + 2 * H
    copilot = ProviderData("Copilot", spent=187.0, limit=300.0, currency="",
                           reset_str=fmt_reset_ts(copilot_reset, now),
                           resets_at=copilot_reset, window_secs=30 * D)
    providers = [chatgpt, cursor, copilot]
    claude_error = None
    detecting: set = set()

    if variant == "states":
        # One healthy card, one near the limit, one signed out, one loading.
        claude = UsageData(
            session=_row("Current Session", 100, 47 * 60, 5 * H, now),
            weekly_all=_row("All Models", 88, 1 * D + 3 * H, 7 * D, now),
            weekly_sonnet=_row("Sonnet Only", 52, 1 * D + 3 * H, 7 * D, now),
        )
        cursor = ProviderData("Cursor", error="403 Client Error: Forbidden for url")
        providers = [chatgpt, cursor]
        detecting = {"copilot"}
    elif variant == "empty":
        cfg = {"refresh_interval": 300}
        claude, providers = None, []
        claude_error = {"kind": "missing", "message": "No claude.ai session found"}
        detecting = {"chatgpt", "cursor", "copilot"}

    history = {
        "claude": _burn_history("claude", 68, 78, now),
        "chatgpt_codex_tasks": _burn_history("chatgpt_codex_tasks", 21, 22, now),
    }
    trends = {}
    if claude and claude.session:
        trends["claude"] = _session_trend(now, claude.session.pct)
    trends["chatgpt_codex_tasks"] = _session_trend(now, 22, reset_in=3 * H + 40 * 60, seed=11)
    trends["cursor_auto"] = [(now - 24 * H + i * 1800, 30 + i * 18 // 48) for i in range(49)]
    trends["copilot"] = [(now - 24 * H + i * 1800, 51 + i * 11 // 48) for i in range(49)]

    return Snapshot(
        config=cfg,
        claude=claude,
        claude_error=claude_error,
        providers=providers,
        cc_stats={"today_messages": 142, "today_sessions": 6, "week_messages": 1187,
                  "week_sessions": 31, "week_tool_calls": 2304},
        history=history,
        trends=trends,
        week_hits={"claude": 2},
        updated_at=now - 40,
        fetching=False,
        detecting=detecting,
        now=now,
    )


def demo_history_rows(now: float | None = None, days: int = 90) -> list[tuple]:
    """(date, key, peak, avg, hits) rows shaped like daily_stats."""
    now = now or time.time()
    rng = random.Random(42)
    today = datetime.fromtimestamp(now, tz=timezone.utc).date()
    profiles = {
        "claude":              (78, 0.9, 60),
        "chatgpt_codex_tasks": (46, 0.7, 60),
        "cursor_auto":         (40, 0.6, 45),
        "copilot":             (55, 0.0, 34),
    }
    rows = []
    for i in range(days, -1, -1):
        day = today - timedelta(days=i)
        weekend = day.weekday() >= 5
        for key, (base, spread, start_days_ago) in profiles.items():
            if i > start_days_ago:
                continue
            if key == "copilot":
                # Monthly allowance: climbs through the month.
                peak = min(100, int(day.day / 30 * 100 * 0.85 + rng.random() * 4))
                avg = max(0, peak - 6)
            else:
                trend = 1 + 0.25 * math.sin(i / 9)
                peak = base * trend * (0.35 if weekend else 1) + rng.gauss(0, 12 * spread)
                peak = max(0, min(100, int(peak)))
                avg = int(peak * (0.45 + rng.random() * 0.2))
            hits = (1 + int(rng.random() * 3)) if peak >= 97 else 0
            rows.append((day.isoformat(), key, peak, avg, hits))
    return rows
