#!/usr/bin/env python3
"""
Claude Usage Menu Bar — macOS status bar app

Sections shown (matching claude.ai/settings/usage):
  1. Plan usage limits → Current session
  2. Weekly limits     → All models + Sonnet only
  3. Extra usage       → toggle status

Setup:
  pip install -r requirements.txt
  python3 claude_bar.py
"""

import rumps
from curl_cffi import requests  # Chrome TLS fingerprint — bypasses Cloudflare
from curl_cffi.requests.exceptions import HTTPError as CurlHTTPError
import atexit
import json
import math
import os
import subprocess
import sqlite3
import sys
import tempfile
import time
import threading
import logging
import logging.handlers
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

try:
    import browser_cookie3
    _BROWSER_COOKIE3_OK = True
except ImportError:
    _BROWSER_COOKIE3_OK = False

# ── logging ──────────────────────────────────────────────────────────────────

LOG_FILE = os.path.expanduser("~/.claude_bar.log")
_log_handler = logging.handlers.RotatingFileHandler(
    LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3,
)
_log_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logging.basicConfig(handlers=[_log_handler], level=logging.DEBUG)
log = logging.getLogger(__name__)

CONFIG_FILE = os.path.expanduser("~/.claude_bar_config.json")

REFRESH_INTERVALS = {
    "1 min":  60,
    "5 min":  300,
    "15 min": 900,
}
DEFAULT_REFRESH = 300

WARN_THRESHOLD = 80   # notify when any limit crosses this %
CRIT_THRESHOLD = 95   # title turns red emoji above this %

WIDGET_HOST_APP = "/Applications/AIQuotaBarHost.app"
WIDGET_CACHE_DIR = os.path.expanduser(
    "~/Library/Application Support/AIQuotaBar"
)
WIDGET_CACHE_FILE = os.path.join(WIDGET_CACHE_DIR, "usage.json")

# ── notification defaults ─────────────────────────────────────────────────────
# Keys stored in config under "notifications": { key: bool }
_NOTIF_DEFAULTS = {
    "claude_reset":   True,   # notify when Claude session/weekly resets
    "chatgpt_reset":  True,   # notify when ChatGPT rate-limit resets
    "claude_warning": True,   # notify when Claude usage crosses WARN/CRIT
    "chatgpt_warning":True,   # notify when ChatGPT usage crosses WARN/CRIT
    "claude_pacing":  True,   # predictive alert when Claude ETA < 30 min
    "chatgpt_pacing": True,   # predictive alert when ChatGPT ETA < 30 min
    "copilot_pacing": True,   # predictive alert when Copilot ETA < 30 min
    "cursor_warning": True,   # notify when Cursor usage crosses WARN/CRIT
    "cursor_pacing":  True,   # predictive alert when Cursor ETA < 30 min
}

# ── usage history + burn rate ────────────────────────────────────────────────

HISTORY_FILE = os.path.expanduser("~/.claude_bar_history.json")
HISTORY_MAX_AGE = 24 * 3600  # prune entries older than 24 h
PACING_ALERT_MINUTES = 30    # alert when ETA drops below this

# ── SQLite long-term history ─────────────────────────────────────────────────
HISTORY_DB = os.path.join(os.path.expanduser("~/Library/Application Support/AIQuotaBar"), "history.db")
_SAMPLES_MAX_DAYS = 7
_DAILY_MAX_DAYS = 90
_LIMIT_HIT_PCT = 95
_BURN_WINDOW = 30 * 60       # regression window: 30 minutes
_MIN_SPAN_SECS = 5 * 60      # need ≥5 min of data before showing ETA
_RESET_DROP_PCT = 30          # pct drop that signals a reset

_HISTORY_COLORS = {
    "claude": "#D97757", "chatgpt": "#74AA9C",
    "copilot": "#6E40C9", "cursor": "#00A0D1",
}


def _nscolor(hex_str: str, alpha: float = 1.0):
    """Convert a hex color string like '#D97757' to an NSColor."""
    from AppKit import NSColor
    h = hex_str.lstrip("#")
    r, g, b = int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, alpha)


def _load_history() -> dict:
    """Load usage history from disk. Returns {"claude": [...], ...}."""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_history(history: dict):
    """Persist usage history (atomic write)."""
    tmp = HISTORY_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(history, f)
    os.replace(tmp, HISTORY_FILE)


def _append_history(history: dict, key: str, pct: int):
    """Append a timestamped pct snapshot, detect resets, and prune."""
    now = datetime.now(timezone.utc).timestamp()
    entries = history.setdefault(key, [])

    # Detect reset: if pct dropped by ≥_RESET_DROP_PCT, discard old data.
    # This prevents stale pre-reset points from poisoning the regression.
    if entries and (entries[-1]["pct"] - pct) >= _RESET_DROP_PCT:
        entries.clear()

    entries.append({"t": now, "pct": pct})
    cutoff = now - HISTORY_MAX_AGE
    history[key] = [e for e in entries if e["t"] >= cutoff]


def _calc_burn_rate(history: dict, key: str) -> float | None:
    """Recency-weighted linear regression over the last 30 min.

    Uses exponential decay weighting (half-life = 10 min) so recent
    data points dominate and old bursts fade quickly.
    Timestamps are centered around their mean for numerical stability.

    Returns pct per minute (positive = increasing usage), or None if
    insufficient data or time span < 5 minutes.
    """
    entries = history.get(key, [])
    if len(entries) < 2:
        return None
    now = datetime.now(timezone.utc).timestamp()
    cutoff = now - _BURN_WINDOW
    recent = [e for e in entries if e["t"] >= cutoff]
    if len(recent) < 2:
        return None

    # Require minimum time span to avoid noisy estimates from clustered points
    span = recent[-1]["t"] - recent[0]["t"]
    if span < _MIN_SPAN_SECS:
        return None

    # Center timestamps for numerical stability
    t_mean = sum(e["t"] for e in recent) / len(recent)

    # Exponential decay weights: half-life of 10 minutes
    half_life = 10 * 60  # seconds
    decay = math.log(2) / half_life

    # Weighted linear regression
    sw = 0.0    # sum of weights
    swt = 0.0   # sum of w * t_centered
    swp = 0.0   # sum of w * pct
    swtp = 0.0  # sum of w * t_centered * pct
    swt2 = 0.0  # sum of w * t_centered^2

    for e in recent:
        tc = e["t"] - t_mean
        w = math.exp(-decay * (now - e["t"]))
        sw += w
        swt += w * tc
        swp += w * e["pct"]
        swtp += w * tc * e["pct"]
        swt2 += w * tc * tc

    denom = sw * swt2 - swt * swt
    if abs(denom) < 1e-10:
        return None
    slope = (sw * swtp - swt * swp) / denom  # pct per second
    return slope * 60  # pct per minute


def _calc_eta_minutes(history: dict, key: str) -> int | None:
    """Estimate minutes until 100% based on burn rate.

    Returns None if burn rate is non-positive or ETA > 10 hours.
    """
    entries = history.get(key, [])
    if not entries:
        return None
    current_pct = entries[-1]["pct"]
    rate = _calc_burn_rate(history, key)
    if rate is None or rate <= 0:
        return None
    remaining = 100 - current_pct
    if remaining <= 0:
        return 0
    eta = remaining / rate  # minutes
    if eta > 600:  # > 10 hours
        return None
    return max(1, round(eta))


def _fmt_eta(minutes: int) -> str:
    """Format ETA: 47 → '47 min', 90 → '1h 30 min', 360 → '6h 0 min'."""
    if minutes < 60:
        return f"{minutes} min"
    h, m = divmod(minutes, 60)
    return f"{h}h {m} min"


def _sparkline(history: dict, key: str, width: int = 20) -> str:
    """Render a sparkline from history using block chars.

    Returns empty string if fewer than 3 points or no meaningful variation.
    """
    entries = history.get(key, [])
    if len(entries) < 3:
        return ""
    blocks = "▁▂▃▄▅▆▇█"
    pts = [e["pct"] for e in entries[-width:]]
    lo, hi = min(pts), max(pts)
    # Skip if all values are the same (no variation → flat line looks bad)
    if hi - lo < 2:
        return ""
    span = hi - lo
    return "".join(blocks[min(7, int((p - lo) / span * 7))] for p in pts)


# ── SQLite history functions ──────────────────────────────────────────────────

def _init_history_db() -> sqlite3.Connection:
    """Create/open the SQLite history database. Returns a WAL-mode connection."""
    os.makedirs(os.path.dirname(HISTORY_DB), exist_ok=True)
    conn = sqlite3.connect(HISTORY_DB, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS samples (
            ts   REAL NOT NULL,
            key  TEXT NOT NULL,
            pct  INTEGER NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_samples_key_ts ON samples(key, ts)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_stats (
            date       TEXT NOT NULL,
            key        TEXT NOT NULL,
            peak_pct   INTEGER NOT NULL,
            avg_pct    INTEGER NOT NULL,
            limit_hits INTEGER NOT NULL DEFAULT 0,
            samples    INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (date, key)
        )
    """)
    conn.commit()
    return conn


def _record_sample(conn: sqlite3.Connection, key: str, pct: int):
    """Insert one usage sample into the samples table (caller should batch commits)."""
    now = datetime.now(timezone.utc).timestamp()
    conn.execute("INSERT INTO samples (ts, key, pct) VALUES (?, ?, ?)", (now, key, pct))


def _rollup_daily_stats(conn: sqlite3.Connection):
    """Aggregate completed days from samples into daily_stats, then prune old data."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Find all distinct dates in samples that are before today
    rows = conn.execute(
        "SELECT DISTINCT date(ts, 'unixepoch') AS d FROM samples WHERE d < ? ORDER BY d",
        (today,),
    ).fetchall()

    for (day,) in rows:
        # Aggregate that day's samples per key
        agg = conn.execute("""
            SELECT key, MAX(pct), CAST(AVG(pct) AS INTEGER), COUNT(*),
                   SUM(CASE WHEN pct >= ? THEN 1 ELSE 0 END)
            FROM samples
            WHERE date(ts, 'unixepoch') = ?
            GROUP BY key
        """, (_LIMIT_HIT_PCT, day)).fetchall()

        for key, peak, avg, cnt, hits in agg:
            conn.execute("""
                INSERT INTO daily_stats (date, key, peak_pct, avg_pct, limit_hits, samples)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(date, key) DO UPDATE SET
                    peak_pct=excluded.peak_pct, avg_pct=excluded.avg_pct,
                    limit_hits=excluded.limit_hits, samples=excluded.samples
            """, (day, key, peak, avg, hits, cnt))

        # Delete rolled-up samples
        conn.execute("DELETE FROM samples WHERE date(ts, 'unixepoch') = ?", (day,))

    # Prune old data
    cutoff_samples = (datetime.now(timezone.utc) - timedelta(days=_SAMPLES_MAX_DAYS)).timestamp()
    conn.execute("DELETE FROM samples WHERE ts < ?", (cutoff_samples,))
    cutoff_daily = (datetime.now(timezone.utc) - timedelta(days=_DAILY_MAX_DAYS)).strftime("%Y-%m-%d")
    conn.execute("DELETE FROM daily_stats WHERE date < ?", (cutoff_daily,))
    conn.commit()


def _get_weekly_stats(conn: sqlite3.Connection, key: str) -> list[dict]:
    """Return last 7 days of daily_stats for a given key, ordered by date."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT date, peak_pct, avg_pct, limit_hits, samples FROM daily_stats "
        "WHERE key = ? AND date >= ? ORDER BY date",
        (key, cutoff),
    ).fetchall()
    return [
        {"date": r[0], "peak_pct": r[1], "avg_pct": r[2], "limit_hits": r[3], "samples": r[4]}
        for r in rows
    ]


def _get_week_limit_hits(conn: sqlite3.Connection, key: str) -> int:
    """Return total number of limit-hit samples in the past 7 days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    row = conn.execute(
        "SELECT COALESCE(SUM(limit_hits), 0) FROM daily_stats WHERE key = ? AND date >= ?",
        (key, cutoff),
    ).fetchone()
    # Also count today's samples that are at limit
    cutoff_ts = (datetime.now(timezone.utc) - timedelta(days=7)).timestamp()
    today_row = conn.execute(
        "SELECT COUNT(*) FROM samples WHERE key = ? AND pct >= ? AND ts >= ?",
        (key, _LIMIT_HIT_PCT, cutoff_ts),
    ).fetchone()
    return (row[0] if row else 0) + (today_row[0] if today_row else 0)


def _weekly_sparkline(daily_stats: list[dict], width: int = 7) -> str:
    """Render a 7-day sparkline from daily peak values."""
    if len(daily_stats) < 2:
        return ""
    blocks = "▁▂▃▄▅▆▇█"
    pts = [d["peak_pct"] for d in daily_stats[-width:]]
    lo, hi = min(pts), max(pts)
    if hi - lo < 2:
        return ""
    span = hi - lo
    return "".join(blocks[min(7, int((p - lo) / span * 7))] for p in pts)


def _get_today_stats(conn: sqlite3.Connection) -> dict[str, dict]:
    """Compute live stats from today's samples (not yet rolled up)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = conn.execute("""
        SELECT key, MAX(pct), CAST(AVG(pct) AS INTEGER), COUNT(*),
               SUM(CASE WHEN pct >= ? THEN 1 ELSE 0 END)
        FROM samples
        WHERE date(ts, 'unixepoch') = ?
        GROUP BY key
    """, (_LIMIT_HIT_PCT, today)).fetchall()
    result = {}
    for key, peak, avg, cnt, hits in rows:
        result[key] = {
            "date": today, "peak_pct": peak, "avg_pct": avg,
            "limit_hits": hits, "samples": cnt,
        }
    return result


def _fetch_history_data(conn: sqlite3.Connection) -> dict | None:
    """Gather all history data for the Usage History window."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cutoff_90 = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d")

    # All daily_stats rows within 90 days
    past_rows = conn.execute(
        "SELECT date, key, peak_pct, avg_pct, limit_hits, samples "
        "FROM daily_stats WHERE date >= ? ORDER BY date",
        (cutoff_90,),
    ).fetchall()

    # Today's live data
    today_stats = _get_today_stats(conn)

    # Merge into per-key and per-day structures
    per_key: dict[str, list[dict]] = {}
    per_day: dict[str, int] = {}  # date -> max avg across all providers (daily metric)
    per_day_detail: dict[str, dict] = {}  # date -> {key: {peak_pct, avg_pct}}

    for date, key, peak, avg, hits, cnt in past_rows:
        per_key.setdefault(key, []).append({
            "date": date, "peak_pct": peak, "avg_pct": avg,
            "limit_hits": hits, "samples": cnt,
        })
        per_day[date] = max(per_day.get(date, 0), avg)
        per_day_detail.setdefault(date, {})[key] = {"peak_pct": peak, "avg_pct": avg}

    for key, stat in today_stats.items():
        per_key.setdefault(key, []).append(stat)
        per_day[today] = max(per_day.get(today, 0), stat["avg_pct"])
        per_day_detail.setdefault(today, {})[key] = {
            "peak_pct": stat["peak_pct"], "avg_pct": stat["avg_pct"],
        }

    # Intraday 5-hour windows for today (from raw samples)
    today_windows: dict[str, dict[int, int]] = {}
    try:
        raw = conn.execute(
            "SELECT key, ts, pct FROM samples WHERE date(ts, 'unixepoch') = ? ORDER BY ts",
            (today,),
        ).fetchall()
        for key, ts, pct in raw:
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            widx = min(dt.hour // 5, 4)
            bucket = today_windows.setdefault(key, {})
            bucket[widx] = max(bucket.get(widx, 0), pct)
    except Exception:
        log.debug("Failed to fetch intraday windows", exc_info=True)

    if not per_day:
        return None

    # Build provider summaries
    _day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    providers = []
    for key in sorted(per_key):
        stats = per_key[key]
        peaks = [d["peak_pct"] for d in stats]
        avgs = [d["avg_pct"] for d in stats]
        avg_val = round(sum(avgs) / len(avgs)) if avgs else 0
        peak_val = max(peaks) if peaks else 0
        total_hits = sum(d["limit_hits"] for d in stats)
        # Color: use parent provider color for sub-keys
        color = _HISTORY_COLORS.get(key)
        if color is None:
            for prefix in ("chatgpt", "cursor", "claude", "copilot"):
                if key.startswith(prefix):
                    color = _HISTORY_COLORS[prefix]
                    break
            else:
                color = "#AAAAAA"
        label = (key.replace("_", " ").title()
                 .replace("Chatgpt", "ChatGPT").replace("Api", "API"))

        # Last 7 days for bar chart
        cutoff_7 = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
        weekly = [d for d in stats if d["date"] >= cutoff_7]

        providers.append({
            "key": key, "label": label, "color": color,
            "weekly": weekly, "avg": avg_val, "peak": peak_val, "hits": total_hits,
        })

    # Summary
    all_peaks = list(per_day.items())
    highest = max(all_peaks, key=lambda x: x[1])
    lowest = min(all_peaks, key=lambda x: x[1])
    avg_overall = round(sum(v for _, v in all_peaks) / len(all_peaks)) if all_peaks else 0
    total_hits = sum(p["hits"] for p in providers)
    earliest = min(per_day.keys())

    def _fmt_day(date_str):
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            return f"{_day_names[dt.weekday()]} {dt.strftime('%b %d')}"
        except Exception:
            return date_str

    return {
        "days": per_day,
        "providers": providers,
        "per_day_detail": per_day_detail,
        "today_windows": today_windows,
        "summary": {
            "total_days": len(per_day),
            "earliest": earliest,
            "highest": (_fmt_day(highest[0]), highest[1]),
            "lowest": (_fmt_day(lowest[0]), lowest[1]),
            "avg": avg_overall,
            "total_hits": total_hits,
        },
    }


# ── config ────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            corrupt = CONFIG_FILE + ".bak"
            log.warning("Config file corrupt (%s), resetting. Backup at %s", e, corrupt)
            try:
                os.replace(CONFIG_FILE, corrupt)
            except OSError:
                pass
    return {}


def save_config(cfg: dict):
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_FILE)


def _notif_enabled(cfg: dict, key: str) -> bool:
    """Return True if the named notification is enabled (defaults to True)."""
    return cfg.get("notifications", {}).get(key, _NOTIF_DEFAULTS.get(key, True))


def _set_notif(cfg: dict, key: str, value: bool):
    """Persist a single notification toggle."""
    cfg.setdefault("notifications", {})[key] = value
    save_config(cfg)


# ── data models ───────────────────────────────────────────────────────────────

@dataclass
class LimitRow:
    label: str
    pct: int          # 0–100
    reset_str: str    # e.g. "resets in 1h 23m" or "resets Thu 00:00"


@dataclass
class UsageData:
    session: LimitRow | None = None
    weekly_all: LimitRow | None = None
    weekly_sonnet: LimitRow | None = None
    overages_enabled: bool | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class ProviderData:
    """Usage/billing data for a third-party API provider."""
    name: str
    spent: float | None = None    # current period spend
    limit: float | None = None    # hard/soft limit
    balance: float | None = None  # prepaid balance (for credit-based providers)
    currency: str = "USD"
    period: str = "this month"
    error: str | None = None
    _rows: list = field(default_factory=list, repr=False)

    @property
    def pct(self) -> int | None:
        if self.spent is not None and self.limit and self.limit > 0:
            return min(100, round(self.spent / self.limit * 100))
        return None


# ── claude.ai API ─────────────────────────────────────────────────────────────

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://claude.ai/settings/usage",
    "Origin": "https://claude.ai",
}
# Cloudflare fingerprint-checks Chrome aggressively; Safari passes cleanly.
_IMPERSONATE = "safari184"
# Cloudflare-bound cookies are tied to the real browser fingerprint —
# sending them from a different TLS stack causes a mismatch → 403.
_CF_COOKIE_KEYS = frozenset({"cf_clearance", "__cf_bm", "_cfuvid"})


def parse_cookie_string(raw: str) -> dict:
    """Parse 'key=val; key2=val2' or just a bare sessionKey value."""
    raw = raw.strip()
    if "=" not in raw:
        return {"sessionKey": raw}
    cookies = {}
    for part in raw.split(";"):
        part = part.strip()
        if "=" in part:
            k, _, v = part.partition("=")
            cookies[k.strip()] = v.strip()
    return cookies


def _strip_cf_cookies(cookies: dict) -> dict:
    return {k: v for k, v in cookies.items() if k not in _CF_COOKIE_KEYS}


def _get(url: str, cookies: dict) -> dict | list:
    r = requests.get(
        url, cookies=_strip_cf_cookies(cookies), headers=HEADERS, timeout=15,
        impersonate=_IMPERSONATE,
    )
    log.debug("GET %s  status=%s  body=%s", url, r.status_code, r.text[:800])
    r.raise_for_status()
    return r.json()


def _org_id_from_cookies(cookies: dict) -> str | None:
    return cookies.get("lastActiveOrg") or cookies.get("routingHint")


def _org_id_from_api(cookies: dict) -> str | None:
    for path in (
        "/api/organizations",
        "/api/bootstrap",
        "/api/auth/current_account",
        "/api/account",
    ):
        try:
            data = _get(f"https://claude.ai{path}", cookies)
            if isinstance(data, list) and data:
                return data[0].get("id") or data[0].get("uuid")
            if isinstance(data, dict):
                for candidate in (
                    data.get("organization_id"),
                    data.get("org_id"),
                    (data.get("organizations") or [{}])[0].get("id"),
                    (data.get("account", {}).get("memberships") or [{}])[0]
                        .get("organization", {}).get("id"),
                ):
                    if candidate:
                        return candidate
        except Exception as e:
            log.debug("endpoint %s failed: %s", path, e)
    return None



def fetch_raw(cookie_str: str) -> dict:
    cookies = parse_cookie_string(cookie_str)
    log.debug("using cookies keys: %s", list(cookies.keys()))

    org_id = _org_id_from_cookies(cookies)
    log.debug("org_id from cookie: %s", org_id)

    if not org_id:
        org_id = _org_id_from_api(cookies)
        log.debug("org_id from api: %s", org_id)

    if not org_id:
        raise ValueError(
            "Could not find organization id.\n"
            "Make sure you copied ALL cookies (including lastActiveOrg)."
        )

    usage = _get(
        f"https://claude.ai/api/organizations/{org_id}/usage", cookies
    )
    log.debug("usage full response: %s", json.dumps(usage, indent=2))
    return {"usage": usage, "org_id": org_id}


# ── third-party provider APIs ────────────────────────────────────────────────

def _api_get(url: str, headers: dict, cookies: dict | None = None) -> dict:
    clean = _strip_cf_cookies(cookies) if cookies else None
    r = requests.get(url, headers=headers, cookies=clean, timeout=10, impersonate=_IMPERSONATE)
    r.raise_for_status()
    return r.json()


_CHATGPT_HEADERS = {
    "Accept": "application/json",
    "Referer": "https://chatgpt.com/codex/settings/usage",
}


def _chatgpt_access_token(cookies: dict) -> str | None:
    """Exchange session cookie for a short-lived Bearer token."""
    data = _api_get("https://chatgpt.com/api/auth/session", _CHATGPT_HEADERS, cookies)
    return data.get("accessToken")


def _parse_wham_window(window: dict, label: str) -> LimitRow | None:
    """Parse a single rate-limit window dict into a LimitRow."""
    if not window or not isinstance(window, dict):
        return None
    pw = window.get("primary_window") or {}
    pct = min(100, int(pw.get("used_percent", 0)))
    reset_str = _fmt_reset(pw.get("reset_at")) if pw.get("reset_at") else ""
    return LimitRow(label, pct, reset_str)


def _parse_wham_usage(data: dict) -> ProviderData:
    """Parse /backend-api/wham/usage response.

    Confirmed shape (2026-02):
      rate_limit.primary_window.used_percent  (0-100)
      rate_limit.primary_window.reset_at      (Unix timestamp)
      code_review_rate_limit  — same structure
    """
    log.debug("wham/usage raw: %s", json.dumps(data, indent=2))

    rows: list[LimitRow] = []

    label_map = {
        "rate_limit":            "Codex Tasks",
        "code_review_rate_limit": "Code Review",
    }
    for key, label in label_map.items():
        row = _parse_wham_window(data.get(key), label)
        if row is not None:
            rows.append(row)

    # additional_rate_limits may be a list of extra buckets
    for extra in (data.get("additional_rate_limits") or []):
        if isinstance(extra, dict):
            name = extra.get("name") or extra.get("type") or "Extra"
            row = _parse_wham_window(extra, name.replace("_", " ").title())
            if row:
                rows.append(row)

    if not rows:
        return ProviderData("ChatGPT", error="No rate limit data in response")

    worst = max(rows, key=lambda r: r.pct)
    pd = ProviderData("ChatGPT", spent=float(worst.pct), limit=100.0, currency="")
    pd._rows = rows
    return pd


def fetch_chatgpt(cookie_str: str) -> ProviderData:
    """Fetch ChatGPT / Codex usage via /backend-api/wham/usage."""
    cookies = parse_cookie_string(cookie_str)
    try:
        token = _chatgpt_access_token(cookies)
        if not token:
            return ProviderData("ChatGPT", error="Not logged in")
        h = {**_CHATGPT_HEADERS, "Authorization": f"Bearer {token}"}
        data = _api_get("https://chatgpt.com/backend-api/wham/usage", h, cookies)
        return _parse_wham_usage(data)
    except Exception as e:
        log.debug("fetch_chatgpt failed: %s", e)
        return ProviderData("ChatGPT", error=str(e)[:80])


def fetch_openai(api_key: str) -> ProviderData:
    h = {"Authorization": f"Bearer {api_key}"}
    try:
        sub = _api_get(
            "https://api.openai.com/v1/dashboard/billing/subscription", h
        )
        hard_limit = float(
            sub.get("hard_limit_usd") or sub.get("system_hard_limit_usd") or 0
        )
        now = datetime.now()
        start = now.replace(day=1).strftime("%Y-%m-%d")
        end = (now + timedelta(days=1)).strftime("%Y-%m-%d")
        usage = _api_get(
            f"https://api.openai.com/v1/dashboard/billing/usage"
            f"?start_date={start}&end_date={end}", h
        )
        spent = float(usage.get("total_usage", 0)) / 100  # cents → dollars
        return ProviderData(
            "OpenAI", spent=spent,
            limit=hard_limit or None, currency="USD", period="this month",
        )
    except Exception as e:
        log.debug("fetch_openai failed: %s", e)
        return ProviderData("OpenAI", error=str(e)[:80])


def fetch_minimax(api_key: str) -> ProviderData:
    h = {"Authorization": f"Bearer {api_key}"}
    try:
        data = _api_get("https://api.minimax.chat/v1/account_information", h)
        balance = float(
            data.get("available_balance") or data.get("balance") or 0
        )
        return ProviderData("MiniMax", balance=balance, currency="CNY")
    except Exception as e:
        log.debug("fetch_minimax failed: %s", e)
        return ProviderData("MiniMax", error=str(e)[:80])


def fetch_glm(api_key: str) -> ProviderData:
    h = {"Authorization": f"Bearer {api_key}"}
    try:
        data = _api_get(
            "https://open.bigmodel.cn/api/paas/v4/account/balance", h
        )
        balance = float(
            data.get("total_balance") or data.get("balance") or 0
        )
        return ProviderData("GLM (Zhipu)", balance=balance, currency="CNY")
    except Exception as e:
        log.debug("fetch_glm failed: %s", e)
        return ProviderData("GLM (Zhipu)", error=str(e)[:80])


def fetch_copilot(cookie_str: str) -> ProviderData:
    """Fetch GitHub Copilot premium request usage via browser cookies."""
    cookies = parse_cookie_string(cookie_str)
    try:
        r = requests.get(
            "https://github.com/settings/billing/copilot_usage_card",
            cookies=_strip_cf_cookies(cookies),
            headers={
                "Accept": "application/json",
                "Referer": "https://github.com/settings/billing/premium_requests_usage",
            },
            timeout=10,
            impersonate=_IMPERSONATE,
        )
        r.raise_for_status()
        data = r.json()
        log.debug("copilot_usage_card: %s", json.dumps(data, indent=2))
        used = float(data.get("discountQuantity", 0))
        limit = float(data.get("userPremiumRequestEntitlement", 0))
        return ProviderData(
            "Copilot", spent=used, limit=limit or None,
            currency="", period="this month",
        )
    except Exception as e:
        log.debug("fetch_copilot failed: %s", e)
        return ProviderData("Copilot", error=str(e)[:80])


def fetch_cursor(cookie_str: str) -> ProviderData:
    """Fetch Cursor IDE usage via browser cookies (WorkOS session)."""
    cookies = parse_cookie_string(cookie_str)
    try:
        r = requests.get(
            "https://cursor.com/api/usage-summary",
            cookies=_strip_cf_cookies(cookies),
            headers={
                "Accept": "application/json",
                "Referer": "https://cursor.com/dashboard?tab=usage",
            },
            timeout=10,
            impersonate=_IMPERSONATE,
        )
        r.raise_for_status()
        data = r.json()
        log.debug("cursor usage-summary: %s", json.dumps(data, indent=2))
        plan = (data.get("individualUsage") or {}).get("plan") or {}
        auto_pct = int(round(float(plan.get("autoPercentUsed", 0))))
        api_pct = int(round(float(plan.get("apiPercentUsed", 0))))
        total_pct = int(round(float(plan.get("totalPercentUsed", 0))))
        # Build reset string from billingCycleEnd
        reset_str = ""
        cycle_end = data.get("billingCycleEnd")
        if cycle_end:
            try:
                end_dt = datetime.fromisoformat(cycle_end.replace("Z", "+00:00"))
                delta = end_dt - datetime.now(timezone.utc)
                if delta.total_seconds() > 0:
                    days = delta.days
                    hours = delta.seconds // 3600
                    if days > 0:
                        reset_str = f"resets in {days}d {hours}h"
                    else:
                        reset_str = f"resets in {hours}h"
            except (ValueError, TypeError):
                pass
        rows = [
            LimitRow(label="Auto", pct=auto_pct, reset_str=reset_str),
            LimitRow(label="API", pct=api_pct, reset_str=reset_str),
        ]
        pd = ProviderData("Cursor", spent=float(total_pct), limit=100.0, currency="")
        pd._rows = rows
        return pd
    except Exception as e:
        log.debug("fetch_cursor failed: %s", e)
        return ProviderData("Cursor", error=str(e)[:80])


# Registry: config_key → (display_name, fetch_fn)
# chatgpt_cookies / copilot_cookies are cookie-based (auto-detected);
# others are API key-based.
PROVIDER_REGISTRY: dict[str, tuple[str, callable]] = {
    "chatgpt_cookies": ("ChatGPT",     fetch_chatgpt),
    "copilot_cookies": ("Copilot",     fetch_copilot),
    "cursor_cookies":  ("Cursor",      fetch_cursor),
    "openai_key":      ("OpenAI",      fetch_openai),
    "minimax_key":     ("MiniMax",     fetch_minimax),
    "glm_key":         ("GLM (Zhipu)", fetch_glm),
}

# Cookie-based providers (auto-detected from browser, not manually entered)
_COOKIE_PROVIDERS = {"chatgpt_cookies", "copilot_cookies", "cursor_cookies"}


# ── time helpers ──────────────────────────────────────────────────────────────

_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _fmt_reset(val) -> str:
    if val is None:
        return ""
    try:
        if isinstance(val, (int, float)):
            dt = datetime.fromtimestamp(val, tz=timezone.utc)
        else:
            s = str(val).rstrip("Z")
            if "+" not in s[10:] and s[-6] != "+":
                s += "+00:00"
            dt = datetime.fromisoformat(s)
        now = datetime.now(timezone.utc)
        delta = dt - now
        secs = delta.total_seconds()
        if secs <= 0:
            return "resets soon"
        if secs < 3600 * 20:
            h, rem = divmod(int(secs), 3600)
            m = rem // 60
            if h > 0:
                return f"resets in {h}h {m}m"
            return f"resets in {m}m"
        day = _DAYS[dt.weekday()]
        return f"resets {day} {dt.strftime('%H:%M')}"
    except Exception:
        log.debug("_fmt_reset failed for %r", val, exc_info=True)
        return str(val)[:20]


# ── parser ────────────────────────────────────────────────────────────────────

def _row(data: dict, key: str, label: str) -> LimitRow | None:
    bucket = data.get(key)
    if not bucket or not isinstance(bucket, dict):
        return None
    raw = float(bucket.get("utilization", 0))
    # API returns 0-100 percentage for all fields (five_hour, seven_day, etc.)
    pct = min(100, round(raw))
    reset = _fmt_reset(bucket.get("resets_at"))
    return LimitRow(label, pct, reset)


def parse_usage(raw: dict) -> UsageData:
    """
    API response shape (confirmed):
      five_hour        → Plan usage limits / Current session
      seven_day        → Weekly limits / All models
      seven_day_sonnet → Weekly limits / Sonnet only
      extra_usage      → Extra usage toggle (null = off)
    """
    u = raw.get("usage", {})
    extra = u.get("extra_usage")
    overages = bool(extra) if extra is not None else None

    return UsageData(
        session=_row(u, "five_hour", "Current Session"),
        weekly_all=_row(u, "seven_day", "All Models"),
        weekly_sonnet=_row(u, "seven_day_sonnet", "Sonnet Only"),
        overages_enabled=overages,
        raw=raw,
    )


# ── Brand icon helpers ────────────────────────────────────────────────────────

_ICON_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
_ICON_SIZE  = 14   # points — matches menu bar font height
_icon_cache: dict = {}


def _bar_icon(filename: str, tint_hex: str | None = None):
    """Lazy-load and cache a menu bar icon (14×14 pt NSImage).

    tint_hex: e.g. '#74AA9C' — applied to monochrome (black) icons so they
              show in the brand color. Pass None for already-coloured icons.
    """
    key = (filename, tint_hex)
    if key in _icon_cache:
        return _icon_cache[key]
    img = None
    try:
        from AppKit import NSImage, NSColor
        path = os.path.join(_ICON_DIR, filename)
        raw = NSImage.alloc().initWithContentsOfFile_(path)
        if raw:
            img = raw.copy()
            img.setSize_((_ICON_SIZE, _ICON_SIZE))
            if tint_hex:
                r = int(tint_hex[1:3], 16) / 255
                g = int(tint_hex[3:5], 16) / 255
                b = int(tint_hex[5:7], 16) / 255
                color = NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 1.0)
                img.setTemplate_(True)
                if hasattr(img, "imageWithTintColor_"):
                    img = img.imageWithTintColor_(color)
    except Exception as e:
        log.debug("_bar_icon %s: %s", filename, e)
    _icon_cache[key] = img
    return img


def _icon_astr(img, base_attrs: dict):
    """Wrap an NSImage in an NSAttributedString via NSTextAttachment."""
    from AppKit import NSTextAttachment, NSAttributedString
    from Foundation import NSMakeRect, NSMutableAttributedString
    att = NSTextAttachment.alloc().init()
    att.setImage_(img)
    att.setBounds_(NSMakeRect(0, -3, _ICON_SIZE, _ICON_SIZE))
    astr = NSAttributedString.attributedStringWithAttachment_(att)
    m = NSMutableAttributedString.alloc().initWithAttributedString_(astr)
    for k, v in base_attrs.items():
        m.addAttribute_value_range_(k, v, (0, m.length()))
    return m


# ── Sticky toggle view (menu stays open on click) ────────────────────────────

_HAS_TOGGLE_VIEW = False
try:
    from AppKit import NSView, NSTextField, NSFont, NSColor, NSBezierPath, NSTrackingArea
    from Foundation import NSMakeRect
    import objc

    _TRACK_FLAGS = 0x01 | 0x80   # mouseEnteredAndExited | activeInActiveApp

    class _BarToggleView(NSView):
        """Custom NSView for menu items — clicking does NOT dismiss the menu."""

        def initWithFrame_(self, frame):
            self = objc.super(_BarToggleView, self).initWithFrame_(frame)
            if self:
                self._action = None
                self._label = None
                self._hovering = False
                area = NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
                    self.bounds(), _TRACK_FLAGS, self, None,
                )
                self.addTrackingArea_(area)
            return self

        def mouseUp_(self, event):
            if callable(self._action):
                self._action()

        def mouseEntered_(self, event):
            self._hovering = True
            self.setNeedsDisplay_(True)

        def mouseExited_(self, event):
            self._hovering = False
            self.setNeedsDisplay_(True)

        def drawRect_(self, rect):
            if self._hovering:
                NSColor.selectedMenuItemColor().set()
                NSBezierPath.fillRect_(rect)
                if self._label:
                    self._label.setTextColor_(NSColor.selectedMenuItemTextColor())
            else:
                if self._label:
                    self._label.setTextColor_(NSColor.labelColor())

    _HAS_TOGGLE_VIEW = True
except Exception:
    pass


# ── Claude Code local stats ───────────────────────────────────────────────────

CC_STATS_FILE = os.path.expanduser("~/.claude/stats-cache.json")


def fetch_claude_code_stats() -> dict | None:
    """Read Claude Code usage from ~/.claude/stats-cache.json (no network needed).

    Returns dict with today_messages, today_sessions, week_messages,
    week_sessions, week_tool_calls — or None if the file doesn't exist.
    """
    if not os.path.exists(CC_STATS_FILE):
        return None
    try:
        with open(CC_STATS_FILE) as f:
            data = json.load(f)
        today = datetime.now().strftime("%Y-%m-%d")
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        entries = data.get("dailyActivity", [])
        today_e = next((e for e in entries if e["date"] == today), None)
        week_e  = [e for e in entries if e["date"] >= week_ago]
        return {
            "today_messages":   today_e["messageCount"]  if today_e else 0,
            "today_sessions":   today_e["sessionCount"]  if today_e else 0,
            "week_messages":    sum(e["messageCount"]  for e in week_e),
            "week_sessions":    sum(e["sessionCount"]  for e in week_e),
            "week_tool_calls":  sum(e["toolCallCount"] for e in week_e),
            "last_date": max((e["date"] for e in entries), default=None),
        }
    except Exception as e:
        log.debug("fetch_claude_code_stats failed: %s", e)
        return None


def _write_widget_cache(
    data: UsageData,
    providers: list[ProviderData],
    cc_stats: dict | None,
    config: dict | None = None,
) -> None:
    """Write current usage snapshot for the WidgetKit widget.

    Writes to ~/Library/Application Support/AIQuotaBar/usage.json
    using atomic replace so the widget never reads a partial file.
    Failures are logged but never crash the main app.
    """
    try:
        def _row_dict(row: LimitRow | None) -> dict | None:
            if row is None:
                return None
            return {"label": row.label, "pct": row.pct, "reset_str": row.reset_str}

        def _active_providers(cfg: dict) -> list[str]:
            """Return list of provider IDs the user has configured."""
            active = []
            if cfg.get("cookie_str"):
                active.append("claude")
            _key_map = {
                "chatgpt_cookies": "chatgpt",
                "copilot_cookies": "copilot",
                "cursor_cookies":  "cursor",
            }
            for cfg_key, prov_id in _key_map.items():
                if cfg.get(cfg_key):
                    active.append(prov_id)
            # Fallback: always show at least Claude
            return active or ["claude"]

        def _bar_providers(cfg: dict) -> list[str] | None:
            """User's explicit bar provider choices (lowercase IDs), or None for auto."""
            chosen = cfg.get("bar_providers")
            if not chosen:
                return None
            return [n.lower() for n in chosen]

        def _copilot_block(provs: list[ProviderData]) -> dict:
            pd = next((p for p in provs if p.name == "Copilot"), None)
            if not pd:
                return {"spent": None, "limit": None, "pct": None, "error": None}
            if pd.error:
                return {"spent": None, "limit": None, "pct": None, "error": pd.error}
            pct = int(round(pd.spent / pd.limit * 100)) if pd.limit else 0
            return {
                "spent": pd.spent,
                "limit": pd.limit,
                "pct": pct,
                "error": None,
            }

        # ChatGPT rows
        chatgpt_pd = next((p for p in providers if p.name == "ChatGPT"), None)
        chatgpt_rows = None
        chatgpt_error = None
        if chatgpt_pd:
            if chatgpt_pd.error:
                chatgpt_error = chatgpt_pd.error
            else:
                raw_rows = getattr(chatgpt_pd, "_rows", None) or []
                chatgpt_rows = [_row_dict(r) for r in raw_rows]

        # Cursor rows
        cursor_pd = next((p for p in providers if p.name == "Cursor"), None)
        cursor_rows = None
        cursor_error = None
        if cursor_pd:
            if cursor_pd.error:
                cursor_error = cursor_pd.error
            else:
                raw_rows = getattr(cursor_pd, "_rows", None) or []
                cursor_rows = [_row_dict(r) for r in raw_rows]

        payload = {
            "version": 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "claude": {
                "session": _row_dict(data.session),
                "weekly_all": _row_dict(data.weekly_all),
                "weekly_sonnet": _row_dict(data.weekly_sonnet),
                "overages_enabled": data.overages_enabled,
            },
            "chatgpt": {
                "rows": chatgpt_rows,
                "error": chatgpt_error,
            },
            "cursor": {
                "rows": cursor_rows,
                "error": cursor_error,
            },
            "copilot": _copilot_block(providers),
            "claude_code": {
                "today_messages": (cc_stats or {}).get("today_messages", 0),
                "week_messages": (cc_stats or {}).get("week_messages", 0),
            },
            "active_providers": _active_providers(config or {}),
            "bar_providers": _bar_providers(config or {}),
        }

        os.makedirs(WIDGET_CACHE_DIR, exist_ok=True)
        tmp = os.path.join(WIDGET_CACHE_DIR, ".usage.json.tmp")
        with open(tmp, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, WIDGET_CACHE_FILE)
        log.debug("widget cache written: %s", WIDGET_CACHE_FILE)

        # Nudge WidgetKit to reload (non-blocking, best-effort)
        subprocess.Popen(
            ["open", "-g", "-a", "AIQuotaBarHost", "--args", "--reload-widget"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        log.debug("_write_widget_cache failed", exc_info=True)


def _is_widget_installed() -> bool:
    """Check if the AIQuotaBarHost widget app is installed."""
    return os.path.isdir(WIDGET_HOST_APP)


def _show_welcome_window(gif_path: str, widget_installed: bool) -> None:
    """Show a native macOS welcome window with side-by-side GIFs."""
    from AppKit import (
        NSWindow, NSImageView, NSImage, NSTextField, NSButton, NSFont,
        NSMakeRect, NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
        NSWindowStyleMaskFullSizeContentView,
        NSBackingStoreBuffered, NSTextAlignmentCenter,
        NSColor, NSBezelStyleRounded,
        NSApplication, NSFloatingWindowLevel, NSScreen,
        NSVisualEffectView, NSView,
    )
    import Quartz

    PAD = 24
    GAP = 16

    # Load both GIFs
    assets_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
    demo_img = NSImage.alloc().initWithContentsOfFile_(
        os.path.join(assets_dir, "demo.gif")
    )
    widget_img = NSImage.alloc().initWithContentsOfFile_(gif_path)

    # Both GIFs at same height; widths from aspect ratios
    GIF_H = 480
    def _w_for_h(img, h):
        if not img:
            return 200
        iw, ih = img.size().width, img.size().height
        return int(h * iw / ih) if ih > 0 else 200

    # Left GIF: fixed wider width, fill-scaled (crops top/bottom)
    demo_w = 320
    widget_w = _w_for_h(widget_img, GIF_H)
    WIN_W = PAD + demo_w + GAP + widget_w + PAD
    WIN_H = 70 + GIF_H + 20 + 150 + 54  # header + gifs + labels + info + button

    # Centre on screen
    screen = NSScreen.mainScreen().frame()
    sx = (screen.size.width - WIN_W) / 2
    sy = (screen.size.height - WIN_H) / 2

    win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(sx, sy, WIN_W, WIN_H),
        (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
         | NSWindowStyleMaskFullSizeContentView),
        NSBackingStoreBuffered,
        False,
    )
    win.setTitle_("")
    win.setTitlebarAppearsTransparent_(True)
    win.setTitleVisibility_(1)
    win.setLevel_(NSFloatingWindowLevel)
    win.setMovableByWindowBackground_(True)

    content = win.contentView()
    content.setWantsLayer_(True)

    # ── Vibrancy background ──
    blur = NSVisualEffectView.alloc().initWithFrame_(content.bounds())
    blur.setAutoresizingMask_(18)
    blur.setBlendingMode_(0)
    blur.setMaterial_(3)
    blur.setState_(1)
    content.addSubview_(blur)

    border_color = Quartz.CGColorCreateGenericRGB(1, 1, 1, 0.08)

    def _make_gif(img, x, y, w, h, fill=False):
        c = NSView.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
        c.setWantsLayer_(True)
        c.layer().setCornerRadius_(10)
        c.layer().setMasksToBounds_(True)
        c.layer().setBorderWidth_(0.5)
        c.layer().setBorderColor_(border_color)
        content.addSubview_(c)
        if img:
            if fill:
                # Scale to fill: size image view to cover container, clip overflow
                iw, ih = img.size().width, img.size().height
                ratio = iw / ih if ih > 0 else 1.0
                # Scale by width → compute height needed
                iv_w = w
                iv_h = int(w / ratio)
                iv_y = h - iv_h  # align to top
                iv = NSImageView.alloc().initWithFrame_(
                    NSMakeRect(0, iv_y, iv_w, iv_h))
            else:
                iv = NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
            iv.setImage_(img)
            iv.setAnimates_(True)
            iv.setImageScaling_(3)
            iv.setImageAlignment_(0)
            iv.setWantsLayer_(True)
            iv.layer().setMagnificationFilter_(Quartz.kCAFilterTrilinear)
            iv.layer().setMinificationFilter_(Quartz.kCAFilterTrilinear)
            iv.layer().setShouldRasterize_(True)
            iv.layer().setRasterizationScale_(3.0)
            c.addSubview_(iv)

    def _label(text, x, y, w, align=NSTextAlignmentCenter, size=11, weight=0.3):
        lbl = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, w, 14))
        lbl.setStringValue_(text)
        lbl.setBezeled_(False)
        lbl.setDrawsBackground_(False)
        lbl.setEditable_(False)
        lbl.setSelectable_(False)
        lbl.setAlignment_(align)
        lbl.setFont_(NSFont.systemFontOfSize_weight_(size, weight))
        lbl.setTextColor_(NSColor.secondaryLabelColor())
        content.addSubview_(lbl)

    # ── Title + subtitle ──
    y_top = WIN_H - 52
    t = NSTextField.alloc().initWithFrame_(
        NSMakeRect(PAD, y_top, WIN_W - PAD * 2, 28)
    )
    t.setStringValue_("Welcome to AIQuotaBar")
    t.setBezeled_(False)
    t.setDrawsBackground_(False)
    t.setEditable_(False)
    t.setSelectable_(False)
    t.setAlignment_(NSTextAlignmentCenter)
    t.setFont_(NSFont.systemFontOfSize_weight_(20, 0.56))
    content.addSubview_(t)

    _label("Monitor your Claude and ChatGPT usage limits in real time.",
           PAD, y_top - 22, WIN_W - PAD * 2, size=12)

    # ── Side-by-side GIFs ──
    gif_y = y_top - 22 - 14 - GIF_H
    _make_gif(demo_img, PAD, gif_y, demo_w, GIF_H, fill=True)
    _label("Menu Bar", PAD, gif_y - 16, demo_w)

    widget_x = PAD + demo_w + GAP
    _make_gif(widget_img, widget_x, gif_y, widget_w, GIF_H)
    _label("Desktop Widget", widget_x, gif_y - 16, widget_w)

    # ── Info rows ──
    info_y = gif_y - 40
    inner_w = WIN_W - PAD * 2
    rows = [
        ("Menu Bar",
         "Click the diamond icon to see session limits, weekly caps, and reset times."),
        ("Desktop Widget",
         "Installed and synced. Right-click desktop > Edit Widgets > 'AI Quota'."
         if widget_installed else
         "Available to install. Check 'Desktop Widget' in the menu bar."),
        ("Auto Refresh",
         "Data updates every 60 seconds. Alerts at 80% and 95% usage."),
    ]
    for heading, desc in rows:
        h = NSTextField.alloc().initWithFrame_(
            NSMakeRect(PAD, info_y, inner_w, 16)
        )
        h.setStringValue_(heading)
        h.setBezeled_(False)
        h.setDrawsBackground_(False)
        h.setEditable_(False)
        h.setSelectable_(False)
        h.setAlignment_(NSTextAlignmentCenter)
        h.setFont_(NSFont.systemFontOfSize_weight_(12, 0.4))
        content.addSubview_(h)
        info_y -= 16

        d = NSTextField.alloc().initWithFrame_(
            NSMakeRect(PAD, info_y, inner_w, 14)
        )
        d.setStringValue_(desc)
        d.setBezeled_(False)
        d.setDrawsBackground_(False)
        d.setEditable_(False)
        d.setSelectable_(False)
        d.setAlignment_(NSTextAlignmentCenter)
        d.setFont_(NSFont.systemFontOfSize_(11))
        d.setTextColor_(NSColor.secondaryLabelColor())
        content.addSubview_(d)
        info_y -= 22

    # ── "Got it" button ──
    btn_w, btn_h = 120, 30
    btn = NSButton.alloc().initWithFrame_(
        NSMakeRect((WIN_W - btn_w) / 2, 14, btn_w, btn_h)
    )
    btn.setTitle_("Got it")
    btn.setBezelStyle_(NSBezelStyleRounded)
    btn.setKeyEquivalent_("\r")
    btn.setAction_(b"performClose:")
    btn.setTarget_(win)
    content.addSubview_(btn)

    win.makeKeyAndOrderFront_(None)
    NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    _show_welcome_window._active_win = win


def _ensure_history_handler():
    """Lazily create an ObjC click handler for heatmap cells."""
    h = getattr(_ensure_history_handler, '_inst', None)
    if h is not None:
        return h
    try:
        from AppKit import NSObject

        class _HMapHandler(NSObject):
            def cellClicked_(self, sender):
                fn = getattr(type(self), '_on_click', None)
                if fn:
                    fn(sender.tag())
        _ensure_history_handler._inst = _HMapHandler.alloc().init()
    except Exception:
        log.debug("Failed to create history click handler", exc_info=True)
        _ensure_history_handler._inst = None
    return _ensure_history_handler._inst


def _show_history_window(conn: sqlite3.Connection) -> None:
    """Show a native macOS Usage History window with heatmap, stats, and charts."""
    from AppKit import (
        NSWindow, NSTextField, NSFont, NSColor, NSView, NSScrollView,
        NSButton, NSMakeRect, NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
        NSWindowStyleMaskFullSizeContentView, NSBackingStoreBuffered,
        NSTextAlignmentCenter, NSTextAlignmentLeft,
        NSApplication, NSFloatingWindowLevel, NSScreen,
        NSVisualEffectView,
    )
    import Quartz

    # Reuse existing window if open
    existing = getattr(_show_history_window, "_active_win", None)
    if existing is not None:
        try:
            existing.makeKeyAndOrderFront_(None)
            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            return
        except Exception:
            pass

    data = _fetch_history_data(conn)
    if data is None:
        return

    WIN_W = 560
    WIN_H = 680
    PAD = 28
    inner_w = WIN_W - PAD * 2

    summary = data["summary"]
    days = data["days"]
    per_day_detail = data.get("per_day_detail", {})
    today_windows = data.get("today_windows", {})
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # Only show providers that have actual usage data
    providers = [p for p in data["providers"] if p["peak"] > 0]

    # ── Helpers ──────────────────────────────────────────────────────────

    def _cg(hex_str, alpha=1.0):
        h = hex_str.lstrip("#")
        r, g, b = int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255
        return Quartz.CGColorCreateGenericRGB(r, g, b, alpha)

    dark_bg = _cg("#1C1C2A")
    card_bg = _cg("#232336")
    track_bg = _cg("#1C1C2A")
    dim = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.55, 0.55, 0.6, 1.0)
    dimmer = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.4, 0.4, 0.45, 1.0)

    # ── Precompute heatmap cell sizing (needed for doc_h) ────────────────
    today = datetime.now(timezone.utc).date()
    _hm_start = today - timedelta(days=89)
    _hm_start -= timedelta(days=_hm_start.weekday())
    _hm_num_days = (today - _hm_start).days + 1
    _hm_num_cols = (_hm_num_days + 6) // 7
    DAY_LABEL_W = 32
    _hm_avail = inner_w - DAY_LABEL_W
    HGAP = 3
    CELL = max(10, int((_hm_avail - HGAP * (_hm_num_cols - 1)) / _hm_num_cols))
    HSTEP = CELL + HGAP

    # ── Compute total content height ─────────────────────────────────────
    HEATMAP_H = 7 * HSTEP + 14 + CELL + 8  # 7 rows + month labels + legend
    CARD_H = 60
    BAR_H = 14
    BAR_GAP = 5
    INTRADAY_BLOCK = 20 + 5 * (BAR_H + BAR_GAP) + 8

    doc_h = PAD + 16          # top padding (below titlebar)
    doc_h += 32 + 22          # title + subtitle
    doc_h += 16 + HEATMAP_H   # gap + heatmap
    doc_h += 24               # day info row below heatmap
    doc_h += 24 + CARD_H      # gap + stat cards
    PROV_HEADER = 22 + 18     # colored dot/name + summary line
    for p in providers:
        doc_h += 24 + PROV_HEADER
        if p["key"] in today_windows:
            doc_h += INTRADAY_BLOCK
        else:
            doc_h += 7 * (BAR_H + BAR_GAP) + 16
    doc_h += 24 + 20 + PAD    # gap + footer + bottom pad
    doc_h = max(doc_h, WIN_H)

    # Placement helpers: top_y is logical offset from top, converted to
    # NSView bottom-up coordinates via  real_y = parent_h - top_y - h

    def _v(parent, x, top_y, w, h, bg=None, corner=0, ph=None, tooltip=None):
        real_y = (ph or doc_h) - top_y - h
        v = NSView.alloc().initWithFrame_(NSMakeRect(x, real_y, w, h))
        v.setWantsLayer_(True)
        if bg:
            v.layer().setBackgroundColor_(bg)
        if corner:
            v.layer().setCornerRadius_(corner)
            v.layer().setMasksToBounds_(True)
        if tooltip:
            v.setToolTip_(tooltip)
        parent.addSubview_(v)
        return v

    def _lbl(parent, text, x, top_y, w, h=0, size=12, weight=0.0,
             color=None, align=NSTextAlignmentLeft, mono=False, ph=None):
        if h == 0:
            h = int(size * 1.5 + 2)
        real_y = (ph or doc_h) - top_y - h
        lbl = NSTextField.alloc().initWithFrame_(NSMakeRect(x, real_y, w, h))
        lbl.setStringValue_(text)
        lbl.setBezeled_(False)
        lbl.setDrawsBackground_(False)
        lbl.setEditable_(False)
        lbl.setSelectable_(False)
        lbl.setAlignment_(align)
        if mono:
            lbl.setFont_(NSFont.monospacedDigitSystemFontOfSize_weight_(size, weight))
        else:
            lbl.setFont_(NSFont.systemFontOfSize_weight_(size, weight))
        lbl.setTextColor_(color or NSColor.labelColor())
        parent.addSubview_(lbl)
        return lbl

    # ── Build window ─────────────────────────────────────────────────────
    screen = NSScreen.mainScreen().frame()
    sx = (screen.size.width - WIN_W) / 2
    sy = (screen.size.height - WIN_H) / 2

    win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(sx, sy, WIN_W, WIN_H),
        (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
         | NSWindowStyleMaskFullSizeContentView),
        NSBackingStoreBuffered,
        False,
    )
    win.setTitle_("")
    win.setTitlebarAppearsTransparent_(True)
    win.setTitleVisibility_(1)
    win.setLevel_(NSFloatingWindowLevel)
    win.setMovableByWindowBackground_(True)

    content = win.contentView()
    content.setWantsLayer_(True)

    blur = NSVisualEffectView.alloc().initWithFrame_(content.bounds())
    blur.setAutoresizingMask_(18)
    blur.setBlendingMode_(0)
    blur.setMaterial_(3)
    blur.setState_(1)
    content.addSubview_(blur)

    scroll = NSScrollView.alloc().initWithFrame_(content.bounds())
    scroll.setAutoresizingMask_(18)
    scroll.setHasVerticalScroller_(True)
    scroll.setDrawsBackground_(False)
    scroll.setBorderType_(0)
    content.addSubview_(scroll)

    doc = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, WIN_W, doc_h))
    scroll.setDocumentView_(doc)

    # ── Layout (top-down y cursor) ───────────────────────────────────────
    y = PAD + 16

    # Title
    _lbl(doc, "Usage History", PAD, y, inner_w, size=22, weight=0.56)
    y += 32
    _lbl(doc, f"Average daily usage across all providers  \u00b7  {summary['total_days']} days tracked",
         PAD, y, inner_w, size=12, color=dim)
    y += 30

    # ── Heatmap (hero section — fills available width) ──────────────────
    HLEFT = PAD + DAY_LABEL_W
    hm_top = y
    start = _hm_start

    # Day labels
    for row, dl in enumerate(["Mon", "", "Wed", "", "Fri", "", "Sun"]):
        if dl:
            _lbl(doc, dl, PAD, hm_top + row * HSTEP + 1, DAY_LABEL_W - 4,
                 size=9, color=dimmer)

    # Cells (interactive NSButtons for click-to-inspect)
    handler = _ensure_history_handler()
    date_for_tag = {}  # tag -> date_str
    tag_counter = [0]

    current = start
    col = 0
    last_month = -1
    while current <= today:
        row = current.weekday()
        cx = HLEFT + col * HSTEP
        cy = hm_top + row * HSTEP

        if current.month != last_month and row == 0:
            _lbl(doc, current.strftime("%b"), cx, hm_top - 14, 40,
                 size=9, color=dimmer)
            last_month = current.month

        ds = current.strftime("%Y-%m-%d")
        pct = days.get(ds, -1)
        if pct < 0:
            cc = dark_bg
            tip = f"{ds}  \u2013  No data"
        else:
            t = min(pct / 100, 1.0)
            r = 0.14 + t * 0.71
            g = 0.14 + t * 0.33
            b = 0.20 + t * 0.14
            cc = Quartz.CGColorCreateGenericRGB(r, g, b, 1.0)
            tip = f"{ds}  \u2013  Peak {pct}%"

        tag = tag_counter[0]
        tag_counter[0] += 1
        date_for_tag[tag] = ds

        real_y = doc_h - cy - CELL
        btn = NSButton.alloc().initWithFrame_(NSMakeRect(cx, real_y, CELL, CELL))
        btn.setBordered_(False)
        btn.setTitle_("")
        btn.setWantsLayer_(True)
        btn.layer().setBackgroundColor_(cc)
        btn.layer().setCornerRadius_(3)
        btn.layer().setMasksToBounds_(True)
        btn.setToolTip_(tip)
        if handler:
            btn.setTarget_(handler)
            btn.setAction_(b"cellClicked:")
            btn.setTag_(tag)
        doc.addSubview_(btn)

        if row == 6:
            col += 1
        current += timedelta(days=1)

    # Legend
    legend_y = hm_top + 7 * HSTEP + 8
    lx = HLEFT
    _lbl(doc, "Less", lx - 30, legend_y + 1, 28, size=9, color=dimmer)
    for lv in [0, 25, 50, 75, 100]:
        t = lv / 100
        lr = 0.14 + t * 0.71
        lg = 0.14 + t * 0.33
        lb = 0.20 + t * 0.14
        _v(doc, lx, legend_y, CELL, CELL,
           Quartz.CGColorCreateGenericRGB(lr, lg, lb, 1.0), corner=3)
        lx += HSTEP
    _lbl(doc, "More", lx + 3, legend_y + 1, 30, size=9, color=dimmer)

    y = legend_y + CELL + 16

    # ── Selected Day info row (updatable on click) ────────────────────
    _day_names_fmt = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    def _fmt_date_label(ds):
        try:
            dt = datetime.strptime(ds, "%Y-%m-%d")
            return f"{_day_names_fmt[dt.weekday()]} {dt.strftime('%b %d')}"
        except Exception:
            return ds

    today_detail = per_day_detail.get(today_str, {})
    info_parts = []
    for key, stats in sorted(today_detail.items()):
        lbl_name = (key.replace("_", " ").title()
                    .replace("Chatgpt", "ChatGPT").replace("Api", "API"))
        info_parts.append(f"{lbl_name} {stats['avg_pct']}%")
    initial_info = (f"{_fmt_date_label(today_str)}  \u2014  "
                    + "  \u00b7  ".join(info_parts)) if info_parts else "Click a cell to see day details"

    info_label = _lbl(doc, initial_info, PAD, y, inner_w, size=11, color=dim)
    y += 24

    # Wire click handler to update info label
    if handler:
        def _on_click(tag):
            ds = date_for_tag.get(tag, "")
            if not ds:
                return
            detail = per_day_detail.get(ds, {})
            parts = []
            for key, stats in sorted(detail.items()):
                name = (key.replace("_", " ").title()
                        .replace("Chatgpt", "ChatGPT").replace("Api", "API"))
                parts.append(f"{name} {stats['avg_pct']}%")
            text = f"{_fmt_date_label(ds)}  \u2014  "
            text += "  \u00b7  ".join(parts) if parts else "No data"
            info_label.setStringValue_(text)
        type(handler)._on_click = _on_click

    # ── Stats Cards ──────────────────────────────────────────────────────
    CARD_GAP = 10
    card_w = (inner_w - CARD_GAP * 3) / 4
    cards = [
        (f"{summary['highest'][1]}%", "Highest Day", summary["highest"][0], "#D97757"),
        (f"{summary['lowest'][1]}%", "Lowest Day", summary["lowest"][0], "#74AA9C"),
        (f"{summary['avg']}%", "Daily Avg", "", "#6E40C9"),
        (f"{summary['total_hits']}x", "Hit Limit", "", "#00A0D1"),
    ]
    for i, (val, lbl, sub, clr) in enumerate(cards):
        cx = PAD + i * (card_w + CARD_GAP)
        card = _v(doc, cx, y, card_w, CARD_H, card_bg, corner=10)
        # Accent bar
        _v(card, 0, 8, 3, CARD_H - 16, _cg(clr), corner=1.5, ph=CARD_H)
        # Value
        _lbl(card, val, 12, 10, card_w - 16, size=17, weight=0.56, mono=True, ph=CARD_H)
        # Label
        _lbl(card, lbl, 12, 32, card_w - 16, size=10, color=dim, ph=CARD_H)
        # Sub-label
        if sub:
            _lbl(card, sub, 12, 44, card_w - 16, size=9, color=dimmer, ph=CARD_H)

    y += CARD_H + 24

    # ── Per-provider sections (only those with data) ─────────────────────
    _day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    BLABEL_W = 32
    BPCT_W = 38

    # Metric context: what the % means for each provider key
    _metric_hint = {
        "claude": "5-hour session window",
        "copilot": "rate limit",
    }

    for prov in providers:
        # Colored dot + name
        _v(doc, PAD, y + 5, 8, 8, _cg(prov["color"]), corner=4)
        _lbl(doc, prov["label"], PAD + 14, y, 250, size=14, weight=0.5)
        # Metric hint next to name
        hint = _metric_hint.get(prov["key"], "rate limit")
        _lbl(doc, hint, PAD + 14 + len(prov["label"]) * 9, y + 2, 200,
             size=10, color=dimmer)
        y += 22

        # Summary
        stxt = f"Avg {prov['avg']}%  \u00b7  Peak {prov['peak']}%"
        if prov["hits"] > 0:
            stxt += f"  \u00b7  Hit limit {prov['hits']}x"
        _lbl(doc, stxt, PAD, y, inner_w, size=11, color=dim)
        y += 18

        bar_area = inner_w - BLABEL_W - BPCT_W - 8
        accent = _cg(prov["color"])

        # Intraday 5h windows replace the 7-day chart when available
        prov_windows = today_windows.get(prov["key"], {})
        if prov_windows:
            _lbl(doc, "Today\u2019s Sessions", PAD, y, inner_w, size=11, weight=0.4, color=dim)
            y += 20
            window_labels = ["00\u201305h", "05\u201310h", "10\u201315h", "15\u201320h", "20\u201324h"]
            for widx in range(5):
                wpct = prov_windows.get(widx, 0)
                wy = y + widx * (BAR_H + BAR_GAP)
                _lbl(doc, window_labels[widx], PAD, wy + 1, BLABEL_W + 10,
                     h=BAR_H, size=9, color=dim)
                _v(doc, PAD + BLABEL_W + 10, wy, bar_area - 10, BAR_H, track_bg, corner=4)
                if wpct > 0:
                    bw = max(6, (bar_area - 10) * wpct / 100)
                    _v(doc, PAD + BLABEL_W + 10, wy, bw, BAR_H, accent, corner=4)
                _lbl(doc, f"{wpct}%", PAD + BLABEL_W + bar_area + 6, wy + 1, BPCT_W,
                     h=BAR_H, size=10, weight=0.3, color=dim, mono=True)
            y += 5 * (BAR_H + BAR_GAP) + 8
        else:
            # 7-day bar chart (fallback when no intraday data)
            day_data = {d["date"]: d["peak_pct"] for d in prov["weekly"]}
            for i in range(7):
                day = today - timedelta(days=6 - i)
                ds = day.strftime("%Y-%m-%d")
                pct = day_data.get(ds, 0)
                by = y + i * (BAR_H + BAR_GAP)

                _lbl(doc, _day_names[day.weekday()], PAD, by + 1, BLABEL_W,
                     h=BAR_H, size=10, color=dim)
                _v(doc, PAD + BLABEL_W, by, bar_area, BAR_H, track_bg, corner=4)
                if pct > 0:
                    bw = max(6, bar_area * pct / 100)
                    _v(doc, PAD + BLABEL_W, by, bw, BAR_H, accent, corner=4)
                _lbl(doc, f"{pct}%", PAD + BLABEL_W + bar_area + 6, by + 1, BPCT_W,
                     h=BAR_H, size=10, weight=0.3, color=dim, mono=True)
            y += 7 * (BAR_H + BAR_GAP) + 16

    # ── Footer ───────────────────────────────────────────────────────────
    _lbl(doc, f"Data since {summary['earliest']}  \u00b7  {summary['total_days']} days tracked",
         PAD, y, inner_w, size=10, color=dimmer, align=NSTextAlignmentCenter)

    # ── Scroll to top ────────────────────────────────────────────────────
    visible_h = scroll.contentSize().height
    if doc_h > visible_h:
        clip = scroll.contentView()
        clip.scrollToPoint_((0, doc_h - visible_h))
        scroll.reflectScrolledClipView_(clip)

    win.makeKeyAndOrderFront_(None)
    NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    _show_history_window._active_win = win


def _fmt_count(n: int) -> str:
    """Format a message count compactly: 1234 → '1.2k', 999 → '999'."""
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


# ── display helpers ───────────────────────────────────────────────────────────

def _bar(pct: int, width: int = 14) -> str:
    filled = round(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)


def _status_icon(pct: int) -> str:
    if pct >= CRIT_THRESHOLD:
        return "🔴"
    if pct >= WARN_THRESHOLD:
        return "🟡"
    return "🟢"


def _row_lines(row: LimitRow) -> list[str]:
    bar = _bar(row.pct)
    line1 = f"  {row.label}  {row.pct}%"
    line2 = f"  {bar}  {row.reset_str}" if row.reset_str else f"  {bar}"
    return [line1, line2]


def _provider_lines(pd: ProviderData) -> list[str]:
    sym = "¥" if pd.currency == "CNY" else ("" if pd.currency == "" else "$")
    if pd.error:
        return [f"  ⚠️  {pd.error[:60]}"]
    # ChatGPT multi-row format (stored in pd._rows by _parse_wham_usage)
    rows = getattr(pd, "_rows", None)
    if rows:
        lines = []
        for row in rows:
            for line in _row_lines(row):
                if line:
                    lines.append(line)
        return lines
    # Standard spending / balance format
    lines = []
    if pd.pct is not None:
        bar = _bar(pd.pct)
        lines.append(f"  {sym}{pd.spent:.2f} / {sym}{pd.limit:.2f} {pd.period}")
        lines.append(f"  {bar}  {pd.pct}%")
    elif pd.balance is not None:
        lines.append(f"  {sym}{pd.balance:.2f} remaining")
    elif pd.spent is not None:
        lines.append(f"  {sym}{pd.spent:.2f} {pd.period}")
    return lines


def _mi(title: str) -> rumps.MenuItem:
    """Display-only menu item (non-clickable but visually active)."""
    item = rumps.MenuItem(title)
    item.set_callback(None)
    item._menuitem.setEnabled_(True)
    return item


def _colored_mi(title: str, color_hex: str) -> rumps.MenuItem:
    """Display-only menu item with brand-colored text."""
    item = rumps.MenuItem(title)
    item.set_callback(None)
    item._menuitem.setEnabled_(True)
    try:
        from AppKit import NSColor, NSForegroundColorAttributeName
        from Foundation import NSAttributedString
        r = int(color_hex[1:3], 16) / 255
        g = int(color_hex[3:5], 16) / 255
        b = int(color_hex[5:7], 16) / 255
        color = NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 0.75)
        astr = NSAttributedString.alloc().initWithString_attributes_(
            title, {NSForegroundColorAttributeName: color}
        )
        item._menuitem.setAttributedTitle_(astr)
    except Exception as e:
        log.debug("_colored_mi: %s", e)
    return item


def _menu_icon(filename: str, tint_hex: str | None = None, size: int = 16):
    """Load an NSImage for use in a menu item, optionally tinted."""
    try:
        from AppKit import NSImage, NSColor
        path = os.path.join(_ICON_DIR, filename)
        raw = NSImage.alloc().initWithContentsOfFile_(path)
        if not raw:
            return None
        img = raw.copy()
        img.setSize_((size, size))
        if tint_hex:
            r = int(tint_hex[1:3], 16) / 255
            g = int(tint_hex[3:5], 16) / 255
            b = int(tint_hex[5:7], 16) / 255
            color = NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 1.0)
            img.setTemplate_(True)
            if hasattr(img, "imageWithTintColor_"):
                img = img.imageWithTintColor_(color)
        return img
    except Exception as e:
        log.debug("_menu_icon %s: %s", filename, e)
        return None


def _section_header_mi(title: str, icon_filename: str | None,
                        color_hex: str, icon_tint: str | None = None) -> rumps.MenuItem:
    """Section header with brand icon and colored bold title."""
    item = rumps.MenuItem(title)
    item.set_callback(None)
    item._menuitem.setEnabled_(True)
    try:
        from AppKit import (NSColor, NSFont,
                            NSForegroundColorAttributeName, NSFontAttributeName)
        from Foundation import NSAttributedString
        r = int(color_hex[1:3], 16) / 255
        g = int(color_hex[3:5], 16) / 255
        b = int(color_hex[5:7], 16) / 255
        color = NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 1.0)
        font = NSFont.boldSystemFontOfSize_(13)
        attrs = {NSFontAttributeName: font, NSForegroundColorAttributeName: color}
        astr = NSAttributedString.alloc().initWithString_attributes_(title, attrs)
        item._menuitem.setAttributedTitle_(astr)
        if icon_filename:
            img = _menu_icon(icon_filename, tint_hex=icon_tint)
            if img:
                item._menuitem.setImage_(img)
    except Exception as e:
        log.debug("_section_header_mi: %s", e)
    return item


# ── login item helpers ────────────────────────────────────────────────────────

def _script_path() -> str:
    return os.path.abspath(__file__)


def _is_login_item() -> bool:
    try:
        result = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to get the name of every login item'],
            capture_output=True, text=True, timeout=10,
        )
        return "claude_bar" in result.stdout.lower()
    except Exception:
        return False


def _add_login_item():
    import plistlib
    path = _script_path()
    plist = os.path.expanduser("~/Library/LaunchAgents/com.claudebar.plist")
    python_exe = sys.executable
    plist_data = {
        "Label": "com.claudebar",
        "ProgramArguments": [python_exe, path],
        "RunAtLoad": True,
        "KeepAlive": False,
    }
    with open(plist, "wb") as f:
        plistlib.dump(plist_data, f)
    result = subprocess.run(["launchctl", "load", plist], capture_output=True)
    if result.returncode != 0:
        log.warning("launchctl load failed: %s", result.stderr.decode(errors="replace"))


def _remove_login_item():
    plist = os.path.expanduser("~/Library/LaunchAgents/com.claudebar.plist")
    if os.path.exists(plist):
        subprocess.run(["launchctl", "unload", plist], capture_output=True)
        os.remove(plist)


# ── native macOS dialogs via osascript ───────────────────────────────────────

def _ask_text(title: str, prompt: str, default: str = "") -> str | None:
    def _esc(s: str) -> str:
        """Escape a string for safe embedding in AppleScript double-quoted strings."""
        return s.replace("\\", "\\\\").replace('"', '\\"')
    script = (
        f'display dialog "{_esc(prompt)}" '
        f'default answer "{_esc(default)}" '
        f'with title "{_esc(title)}" '
        f'buttons {{"Cancel", "Save"}} default button "Save"'
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            return None
        out = result.stdout.strip()
        if "text returned:" in out:
            return out.split("text returned:")[-1].strip()
    except Exception:
        log.exception("_ask_text failed")
    return None


def _clipboard_text() -> str:
    try:
        result = subprocess.run(
            ["pbpaste"], capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip()
    except Exception:
        return ""


_keychain_warned = False  # show the dialog at most once per session


def _warn_keychain_once():
    """Show a one-time dialog before the macOS Keychain prompt appears."""
    global _keychain_warned
    if _keychain_warned:
        return
    _keychain_warned = True
    subprocess.run(
        ["osascript", "-e",
         'display dialog "Claude Usage Bar needs one-time access to your '
         'browser cookies to read your Claude usage.\\n\\n'
         'macOS will show a security prompt — click \\"Always Allow\\" '
         'and it will never ask again." '
         'with title "Claude Usage Bar — One-time Setup" '
         'buttons {"OK"} default button "OK" '
         'with icon note'],
        capture_output=True, timeout=60,
    )


# Script run in a child process — isolates browser_cookie3 C-library crashes
# (libcrypto / sqlite segfaults on Chromium decryption don't kill the main app).
_DETECT_SCRIPT = r"""
import sys, json

domain  = sys.argv[1]
target  = sys.argv[2]

BROWSERS = [
    'firefox', 'librewolf', 'chrome', 'arc', 'brave',
    'edge', 'chromium', 'opera', 'vivaldi', 'safari',
]

# Collect candidates from every browser that has the target cookie.
# Pick the one with the latest expiry on the target cookie so we always
# use the freshest session (handles the case where the user is logged in
# to multiple browsers simultaneously).
candidates = []  # list of (expires, cookie_str)

try:
    import browser_cookie3
    for name in BROWSERS:
        fn = getattr(browser_cookie3, name, None)
        if fn is None:
            continue
        try:
            jar = fn(domain_name=domain)
            cookies = {x.name: x for x in jar}
            if target not in cookies:
                continue
            expires = cookies[target].expires or 0
            cookie_str = '; '.join(f'{k}={c.value}' for k, c in cookies.items())
            candidates.append((expires, cookie_str))
        except Exception:
            pass
except Exception:
    pass

# Best = latest expiry; tie-break by longest cookie string (richest jar)
result = None
if candidates:
    candidates.sort(key=lambda x: (x[0], len(x[1])), reverse=True)
    result = candidates[0][1]

print(json.dumps(result))
"""


def _run_cookie_detection(domain: str, target_cookie: str) -> str | None:
    """Run browser_cookie3 in an isolated child process (crash-safe)."""
    try:
        r = subprocess.run(
            [sys.executable, "-c", _DETECT_SCRIPT, domain, target_cookie],
            capture_output=True, text=True, timeout=60,
        )
        log.debug("cookie-detect rc=%d out=%r err=%r",
                  r.returncode, r.stdout[:200], r.stderr[:200])
        if r.stdout.strip():
            return json.loads(r.stdout.strip())
    except Exception as e:
        log.debug("_run_cookie_detection failed: %s", e)
    return None


def _auto_detect_cookies() -> str | None:
    """Detect claude.ai session cookies from the browser (crash-safe subprocess)."""
    if not _BROWSER_COOKIE3_OK:
        return None
    _warn_keychain_once()
    return _run_cookie_detection("claude.ai", "sessionKey")


def _auto_detect_chatgpt_cookies() -> str | None:
    """Detect chatgpt.com session cookies from the browser (crash-safe subprocess)."""
    if not _BROWSER_COOKIE3_OK:
        return None
    return _run_cookie_detection("chatgpt.com", "__Secure-next-auth.session-token")


def _auto_detect_copilot_cookies() -> str | None:
    """Detect github.com session cookies from the browser (crash-safe subprocess)."""
    if not _BROWSER_COOKIE3_OK:
        return None
    return _run_cookie_detection("github.com", "user_session")


def _auto_detect_cursor_cookies() -> str | None:
    """Detect cursor.com session cookies from the browser (crash-safe subprocess)."""
    if not _BROWSER_COOKIE3_OK:
        return None
    return _run_cookie_detection("cursor.com", "WorkosCursorSessionToken")


def _notify(title: str, subtitle: str, message: str = ""):
    """rumps.notification wrapper — silently swallows if the notification
    center is unavailable (e.g. missing Info.plist in dev environments)."""
    try:
        rumps.notification(title, subtitle, message)
    except Exception as e:
        log.debug("notification suppressed: %s", e)


def _show_text(title: str, text: str):
    try:
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False,
            prefix="claude_usage_raw_"
        )
        tmp.write(text)
        tmp.close()
        subprocess.Popen(["open", "-a", "TextEdit", tmp.name])
        # Schedule cleanup after 60 seconds (enough time for TextEdit to open)
        def _cleanup():
            time.sleep(60)
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
        threading.Thread(target=_cleanup, daemon=True).start()
    except Exception:
        log.exception("_show_text failed")


# ── app ───────────────────────────────────────────────────────────────────────

class ClaudeBar(rumps.App):
    def __init__(self):
        super().__init__("◆", quit_button=None)
        self.config = load_config()
        self._last_raw: dict = {}
        self._last_data: UsageData | None = None
        self._provider_data: list[ProviderData] = []
        self._warned_pcts: set[str] = set()   # track which rows we've notified
        self._prev_pcts: dict[str, int] = {}  # previous pct per row key (reset detection)
        self._auth_fail_count = 0
        self._fetching = False
        self._last_updated: datetime | None = None

        self._refresh_interval = self.config.get("refresh_interval", DEFAULT_REFRESH)
        self._cc_stats: dict | None = None   # Claude Code local stats
        self._history = _load_history()       # usage history for burn rate / sparkline
        self._pacing_alerted: set[str] = set()  # track which providers we've pacing-alerted
        self._history_db = _init_history_db()
        self._last_rollup = 0
        try:
            _rollup_daily_stats(self._history_db)
            self._last_rollup = time.time()
        except Exception:
            log.exception("startup rollup failed")

        # Thread-safe UI update queue (background thread → main thread)
        self._ui_pending_title: str | None = None
        self._ui_pending_data: UsageData | None = None
        self._ui_lock = threading.Lock()
        self._config_lock = threading.Lock()
        self._db_lock = threading.Lock()
        self._login_item_cached: bool | None = None

        if not _is_login_item():
            _add_login_item()

        self._rebuild_menu(None)
        self._timer = rumps.Timer(self._on_timer, self._refresh_interval)
        self._timer.start()
        # Fast ticker: drains pending UI updates on the main thread (avoids AppKit crashes)
        self._ui_ticker = rumps.Timer(self._flush_ui, 0.25)
        self._ui_ticker.start()

        # Deferred startup info (runs after the run loop is active)
        self._welcome_timer = rumps.Timer(self._deferred_welcome, 2)
        self._welcome_timer.start()

        # Always try to fetch on startup — browser JS works even without saved cookies
        atexit.register(self._shutdown)
        self._schedule_fetch()

    def _shutdown(self):
        """Clean up resources on exit."""
        try:
            self._history_db.close()
        except Exception:
            pass

    def _is_login_item_cached(self) -> bool:
        if self._login_item_cached is None:
            self._login_item_cached = _is_login_item()
        return self._login_item_cached

    # ── menu ─────────────────────────────────────────────────────────────────

    def _rebuild_menu(self, data: UsageData | None):
        items: list = []

        # ── ◆  CLAUDE section ─────────────────────────────────────────────
        items.append(_section_header_mi("  Claude", "claude_icon.png", "#D97757"))

        if data is None or not any([data.session, data.weekly_all, data.weekly_sonnet]):
            items.append(_mi("  No data — click Auto-detect from Browser"))
        else:
            if data.session:
                lines = _row_lines(data.session)
                items.append(_mi(lines[0]))
                items.append(_colored_mi(lines[1], "#D97757"))
                # ETA + sparkline for Claude session
                eta = _calc_eta_minutes(self._history, "claude")
                if eta is not None:
                    items.append(_mi(f"  ⏱ Limit in ~{_fmt_eta(eta)}"))
                spark = _sparkline(self._history, "claude")
                if spark:
                    items.append(_mi(f"  {spark}"))
                    items.append(_mi(f"  📈 24h usage trend"))
                try:
                    hits = _get_week_limit_hits(self._history_db, "claude")
                except Exception:
                    hits = 0
                if hits > 0:
                    items.append(_mi(f"  Hit limit {hits}x this week"))
                items.append(None)

            for row in [data.weekly_all, data.weekly_sonnet]:
                if row:
                    lines = _row_lines(row)
                    items.append(_mi(lines[0]))
                    items.append(_colored_mi(lines[1], "#D97757"))
                    items.append(None)

            if data.overages_enabled is not None:
                status = "✅  On" if data.overages_enabled else "⛔  Off"
                items.append(_mi(f"  Extra usage  {status}"))
                items.append(None)

        # ── ◇  CHATGPT section (if detected) ──────────────────────────────
        chatgpt_pd = next(
            (pd for pd in self._provider_data if pd.name == "ChatGPT"), None
        )
        if chatgpt_pd:
            items.append(_section_header_mi("  ChatGPT", "chatgpt_icon_clean.png",
                                            "#74AA9C", icon_tint="#74AA9C"))
            rows = getattr(chatgpt_pd, "_rows", None)
            if rows:
                for row in rows:
                    lines = _row_lines(row)
                    items.append(_mi(lines[0]))
                    items.append(_colored_mi(lines[1], "#74AA9C"))
                    hkey = f"chatgpt_{row.label.lower().replace(' ', '_')}"
                    eta = _calc_eta_minutes(self._history, hkey)
                    if eta is not None:
                        items.append(_mi(f"  ⏱ Limit in ~{_fmt_eta(eta)}"))
                    spark = _sparkline(self._history, hkey)
                    if spark:
                        items.append(_mi(f"  {spark}"))
                        items.append(_mi(f"  📈 24h usage trend"))
                    try:
                        hits = _get_week_limit_hits(self._history_db, hkey)
                    except Exception:
                        hits = 0
                    if hits > 0:
                        items.append(_mi(f"  Hit limit {hits}x this week"))
                    items.append(None)
            else:
                for line in _provider_lines(chatgpt_pd):
                    if line:
                        items.append(_mi(line))
                items.append(None)

        # ── ◇  COPILOT section (if detected) ─────────────────────────────────
        copilot_pd = next(
            (pd for pd in self._provider_data if pd.name == "Copilot"), None
        )
        if copilot_pd:
            items.append(_section_header_mi("  GitHub Copilot", "copilot.png", "#6E40C9", icon_tint="#9B6BFF"))
            for line in _provider_lines(copilot_pd):
                if line:
                    items.append(_mi(line))
            # ETA + sparkline for Copilot
            eta = _calc_eta_minutes(self._history, "copilot")
            if eta is not None:
                items.append(_mi(f"  ⏱ Limit in ~{_fmt_eta(eta)}"))
            spark = _sparkline(self._history, "copilot")
            if spark:
                items.append(_mi(f"  {spark}"))
                items.append(_mi(f"  📈 24h usage trend"))
            try:
                hits = _get_week_limit_hits(self._history_db, "copilot")
            except Exception:
                hits = 0
            if hits > 0:
                items.append(_mi(f"  Hit limit {hits}x this week"))
            items.append(None)

        # ── ◇  CURSOR section (if detected) ──────────────────────────────────
        cursor_pd = next(
            (pd for pd in self._provider_data if pd.name == "Cursor"), None
        )
        if cursor_pd:
            items.append(_section_header_mi("  Cursor", "cursor.png", "#00A0D1", icon_tint="#00A0D1"))
            rows = getattr(cursor_pd, "_rows", None)
            if rows:
                for row in rows:
                    lines = _row_lines(row)
                    items.append(_mi(lines[0]))
                    items.append(_colored_mi(lines[1], "#00A0D1"))
                    hkey = f"cursor_{row.label.lower().replace(' ', '_')}"
                    eta = _calc_eta_minutes(self._history, hkey)
                    if eta is not None:
                        items.append(_mi(f"  ⏱ Limit in ~{_fmt_eta(eta)}"))
                    spark = _sparkline(self._history, hkey)
                    if spark:
                        items.append(_mi(f"  {spark}"))
                        items.append(_mi(f"  📈 24h usage trend"))
                    try:
                        hits = _get_week_limit_hits(self._history_db, hkey)
                    except Exception:
                        hits = 0
                    if hits > 0:
                        items.append(_mi(f"  Hit limit {hits}x this week"))
                    items.append(None)
            else:
                for line in _provider_lines(cursor_pd):
                    if line:
                        items.append(_mi(line))
                items.append(None)

        # ── ◆  CLAUDE CODE section ────────────────────────────────────────────
        if self._cc_stats:
            cc = self._cc_stats
            items.append(_section_header_mi("  Claude Code", "claude_icon.png", "#D97757"))
            if cc["today_messages"] > 0:
                items.append(_mi(
                    f"  Today     {_fmt_count(cc['today_messages'])} msgs"
                    f"  ·  {cc['today_sessions']} sessions"
                ))
            wm = cc["week_messages"]
            if wm > 0:
                items.append(_mi(
                    f"  This week  {_fmt_count(wm)} msgs"
                    f"  ·  {cc['week_sessions']} sessions"
                    f"  ·  {_fmt_count(cc['week_tool_calls'])} tools"
                ))
            if cc.get("last_date"):
                items.append(_mi(f"  Last active  {cc['last_date']}"))
            items.append(None)

        # ── Other API providers ────────────────────────────────────────────
        for pd in self._provider_data:
            if pd.name in ("ChatGPT", "Copilot", "Cursor"):
                continue
            items.append(_mi(f"  {pd.name}"))
            items.append(None)
            for line in _provider_lines(pd):
                if line:
                    items.append(_mi(line))
            items.append(None)

        # ── Usage History window ──────────────────────────────────────────
        try:
            today_stats = _get_today_stats(self._history_db)
            past_keys = {r[0] for r in self._history_db.execute(
                "SELECT DISTINCT key FROM daily_stats"
            ).fetchall()}
            has_history = bool(past_keys or today_stats)
            if has_history:
                items.append(rumps.MenuItem(
                    "Usage History\u2026", callback=self._open_history_window,
                ))
                items.append(None)
        except Exception:
            log.exception("Usage History menu item failed")

        # ── Footer ────────────────────────────────────────────────────────
        if self._last_updated:
            t = self._last_updated.strftime("%H:%M")
            items.append(_mi(f"  Updated {t}"))
            items.append(None)

        # ── Actions ──────────────────────────────────────────────────────
        items.append(rumps.MenuItem("Refresh Now", callback=self._do_refresh))
        items.append(rumps.MenuItem("Open claude.ai/settings/usage", callback=self._open_usage_page))
        items.append(rumps.MenuItem("Share on X / Twitter…", callback=self._share_on_x))
        items.append(rumps.MenuItem("⭐ Star on GitHub", callback=self._open_github))
        items.append(None)

        # Status bar display submenu
        bar_menu = rumps.MenuItem("Status Bar")
        chosen = self.config.get("bar_providers") or []
        # In auto mode, compute which providers would be shown
        if not chosen:
            available_names = {"Claude"} if self._last_data else set()
            for pd in self._provider_data:
                if self._provider_bar_pct(pd) is not None:
                    available_names.add(pd.name)
            auto_shown = [n for n in self._BAR_PRIORITY if n in available_names][:2]
        else:
            auto_shown = []
        self._bar_toggle_views = {}
        for name in self._BAR_PRIORITY:
            is_on = name in chosen if chosen else name in auto_shown
            item = self._make_sticky_toggle(name, is_on, name)
            bar_menu.add(item)
        bar_menu.add(None)
        is_auto = not chosen
        auto_item = rumps.MenuItem(
            "✓ Auto (top 2 active)" if is_auto else "Reset to Auto",
            callback=self._bar_reset_auto,
        )
        bar_menu.add(auto_item)
        items.append(bar_menu)

        # Refresh interval submenu
        interval_menu = rumps.MenuItem("Refresh Interval")
        for label, secs in REFRESH_INTERVALS.items():
            item = rumps.MenuItem(label, callback=self._make_interval_cb(secs, label))
            item._menuitem.setState_(1 if secs == self._refresh_interval else 0)
            interval_menu.add(item)
        items.append(interval_menu)

        # Notifications submenu
        notif_menu = rumps.MenuItem("Notifications")
        _notif_labels = [
            ("claude_warning",  "Claude — usage warnings (80% / 95%)"),
            ("claude_reset",    "Claude — reset alerts"),
            ("claude_pacing",   "Claude — pacing alert (ETA < 30 min)"),
            ("chatgpt_warning", "ChatGPT — usage warnings (80% / 95%)"),
            ("chatgpt_reset",   "ChatGPT — reset alerts"),
            ("chatgpt_pacing",  "ChatGPT — pacing alert (ETA < 30 min)"),
            ("copilot_pacing",  "Copilot — pacing alert (ETA < 30 min)"),
            ("cursor_warning",  "Cursor — usage warnings (80% / 95%)"),
            ("cursor_pacing",   "Cursor — pacing alert (ETA < 30 min)"),
        ]
        for nkey, nlabel in _notif_labels:
            item = rumps.MenuItem(nlabel, callback=self._make_notif_toggle_cb(nkey))
            item._menuitem.setState_(1 if _notif_enabled(self.config, nkey) else 0)
            notif_menu.add(item)
        items.append(notif_menu)
        items.append(None)

        # API providers submenu
        providers_menu = rumps.MenuItem("API Providers")
        for cfg_key, (name, _) in PROVIDER_REGISTRY.items():
            is_set = bool(self.config.get(cfg_key))
            if cfg_key in _COOKIE_PROVIDERS:
                label = f"{'✓' if is_set else '+'} {name} (auto-detect)"
            else:
                label = f"{'✓' if is_set else '+'} {name} API Key…"
            providers_menu.add(rumps.MenuItem(
                label, callback=self._make_provider_key_cb(cfg_key, name)
            ))
        items.append(providers_menu)

        items.append(None)
        items.append(rumps.MenuItem("Auto-detect from Browser", callback=self._auto_detect_menu))
        items.append(rumps.MenuItem("Set Session Cookie…", callback=self._set_cookie))
        items.append(rumps.MenuItem("Paste Cookie from Clipboard", callback=self._paste_cookie))
        items.append(rumps.MenuItem("Show Raw API Data…", callback=self._show_raw))
        items.append(None)

        login_item = rumps.MenuItem("Launch at Login", callback=self._toggle_login_item)
        login_item._menuitem.setState_(1 if self._is_login_item_cached() else 0)
        items.append(login_item)

        # Desktop Widget status
        if _is_widget_installed():
            widget_item = rumps.MenuItem(
                "Desktop Widget  ✓  Installed",
                callback=self._open_widget_settings,
            )
        else:
            widget_item = rumps.MenuItem(
                "Desktop Widget  ·  Not Installed",
                callback=self._install_widget_prompt,
            )
        items.append(widget_item)

        items.append(None)
        items.append(rumps.MenuItem("Quit", callback=rumps.quit_application))

        self.menu.clear()
        self.menu = items
        # Prevent macOS from auto-disabling display-only items
        try:
            ns_menu = self._nsapp.nsstatusitem.menu()
            if ns_menu:
                ns_menu.setAutoenablesItems_(False)
        except Exception:
            pass

    # ── thread-safe UI helpers ────────────────────────────────────────────────

    def _post_title(self, title: str):
        """Queue a title update from any thread."""
        with self._ui_lock:
            self._ui_pending_title = title

    def _post_data(self, data: UsageData):
        """Queue a full UI update (title + menu) from any thread."""
        with self._ui_lock:
            self._ui_pending_data = data

    def _flush_ui(self, _timer):
        """Main-thread ticker: apply any queued updates from background threads."""
        with self._ui_lock:
            title = self._ui_pending_title
            data = self._ui_pending_data
            self._ui_pending_title = None
            self._ui_pending_data = None
        if data is not None:
            self._apply(data)
        elif title is not None:
            self.title = title

    # ── widget ─────────────────────────────────────────────────────────────────

    def _deferred_welcome(self, _timer):
        """Runs once after the run loop is active, then stops itself."""
        _timer.stop()
        self._check_widget_status()

    def _check_widget_status(self):
        """Show startup info about what the app is doing."""
        seen_welcome = self.config.get("seen_welcome", False)
        widget_ok = _is_widget_installed()

        if not seen_welcome:
            # First launch — show native welcome window with GIF
            gif_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "assets", "widget_info.gif",
            )
            if os.path.isfile(gif_path):
                _show_welcome_window(gif_path, widget_ok)
            else:
                # Fallback to notification if GIF missing
                rumps.notification(
                    title="Welcome to AIQuotaBar",
                    subtitle="Monitoring Claude + ChatGPT usage",
                    message="Click the diamond in your menu bar to get started.",
                    sound=True,
                )
            self.config["seen_welcome"] = True
            save_config(self.config)
        else:
            # Subsequent launches — brief notification
            if widget_ok:
                rumps.notification(
                    title="AIQuotaBar",
                    subtitle="Running",
                    message="Menu bar and desktop widget are synced.",
                    sound=False,
                )
            else:
                rumps.notification(
                    title="AIQuotaBar",
                    subtitle="Running",
                    message=(
                        "Tracking usage from your menu bar. "
                        "A desktop widget is also available — check the menu."
                    ),
                    sound=False,
                )

    def _open_widget_settings(self, _sender):
        """Open the widget host app (shows add-widget instructions)."""
        subprocess.Popen(
            ["open", "-a", "AIQuotaBarHost"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def _install_widget_prompt(self, _sender):
        """Show instructions for building/installing the widget."""
        widget_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "AIQuotaBarWidget"
        )
        if os.path.isdir(widget_dir):
            build_script = os.path.join(widget_dir, "build_widget.sh")
            if os.path.isfile(build_script):
                rumps.alert(
                    title="Install Desktop Widget",
                    message=(
                        "To install the desktop widget, run in Terminal:\n\n"
                        f"cd {widget_dir}\n"
                        "./build_widget.sh\n\n"
                        "Then right-click desktop → Edit Widgets → search 'AI Quota'."
                    ),
                )
                return
        rumps.alert(
            title="Desktop Widget Not Found",
            message=(
                "The widget project was not found.\n\n"
                "Make sure the AIQuotaBarWidget folder exists "
                "in the AIQuotaBar directory."
            ),
        )

    # ── fetch ─────────────────────────────────────────────────────────────────

    def _on_timer(self, _timer):
        self._schedule_fetch()

    def _schedule_fetch(self):
        with self._ui_lock:
            if self._fetching:
                return
            self._fetching = True
        threading.Thread(target=self._fetch_and_update, daemon=True).start()

    def _fetch_and_update(self):

        try:
            with self._config_lock:
                sk = self.config.get("cookie_str")
            if not sk:
                sk = _auto_detect_cookies()
                if sk:
                    with self._config_lock:
                        self.config["cookie_str"] = sk
                        save_config(self.config)
            if not sk:
                self._post_title("◆")
                self._fetching = False
                return
            raw = fetch_raw(sk)
            self._last_raw = raw
            self._auth_fail_count = 0
            data = parse_usage(raw)
            self._last_data = data
            self._last_updated = datetime.now()
            log.debug("parsed UsageData: %s", data)
            self._check_warnings(data)
            self._fetch_providers()
            self._check_provider_warnings(self._provider_data)
            self._cc_stats = fetch_claude_code_stats()

            # ── record usage history ──
            if data.session:
                _append_history(self._history, "claude", data.session.pct)
            # Per-row history for multi-limit providers (avoids mixing
            # different limit types which made ETAs jump around).
            for prefix, pname in [("chatgpt", "ChatGPT"), ("cursor", "Cursor")]:
                pd = next((p for p in self._provider_data if p.name == pname), None)
                if pd and not pd.error:
                    rows = getattr(pd, "_rows", None)
                    if rows:
                        for row in rows:
                            hkey = f"{prefix}_{row.label.lower().replace(' ', '_')}"
                            _append_history(self._history, hkey, row.pct)
            copilot_pd = next(
                (pd for pd in self._provider_data if pd.name == "Copilot"), None
            )
            if copilot_pd and not copilot_pd.error and copilot_pd.pct is not None:
                _append_history(self._history, "copilot", copilot_pd.pct)
            _save_history(self._history)

            # ── record to SQLite history ──
            try:
                with self._db_lock:
                    if data.session:
                        _record_sample(self._history_db, "claude", data.session.pct)
                    for prefix, pname in [("chatgpt", "ChatGPT"), ("cursor", "Cursor")]:
                        pd = next((p for p in self._provider_data if p.name == pname), None)
                        if pd and not pd.error:
                            rows = getattr(pd, "_rows", None)
                            if rows:
                                for row in rows:
                                    hkey = f"{prefix}_{row.label.lower().replace(' ', '_')}"
                                    _record_sample(self._history_db, hkey, row.pct)
                    if copilot_pd and not copilot_pd.error and copilot_pd.pct is not None:
                        _record_sample(self._history_db, "copilot", copilot_pd.pct)
                    self._history_db.commit()
                    # Periodic rollup (every hour)
                    if time.time() - self._last_rollup > 3600:
                        _rollup_daily_stats(self._history_db)
                        self._last_rollup = time.time()
            except Exception:
                log.exception("SQLite history recording failed")

            self._check_pacing_alerts()

            self._post_data(data)          # ← main thread applies title + menu
            _write_widget_cache(data, self._provider_data, self._cc_stats, self.config)
        except CurlHTTPError as e:
            resp = getattr(e, "response", None)
            code = getattr(resp, "status_code", 0) or 0
            log.error("HTTP error: %s (status=%s)", e, code, exc_info=True)
            if code in (401, 403):
                self._auth_fail_count += 1
                self._post_title("◆ !")
                if self._auth_fail_count >= 2:
                    self._auth_fail_count = 0
                    cookie_str = _auto_detect_cookies()
                    if cookie_str:
                        with self._config_lock:
                            self.config["cookie_str"] = cookie_str
                            save_config(self.config)
                        self._warned_pcts.clear()
                        log.info("Auth failed — auto-detected fresh cookies from browser")
                        self._schedule_fetch()
                    else:
                        _notify(
                            "Claude Usage Bar",
                            "Session expired — please update your cookie",
                            "Click: Set Session Cookie… or Auto-detect from Browser",
                        )
            else:
                self._post_title("◆ err")
        except Exception:
            log.exception("fetch failed")
            self._post_title("◆ ?")
        finally:
            self._fetching = False

    def _check_warnings(self, data: UsageData):
        """Send macOS notification when a Claude limit crosses a threshold or resets."""
        rows = [
            (data.session,       "session"),
            (data.weekly_all,    "weekly_all"),
            (data.weekly_sonnet, "weekly_sonnet"),
        ]
        warn_enabled = _notif_enabled(self.config, "claude_warning")
        reset_enabled = _notif_enabled(self.config, "claude_reset")

        for row, key in rows:
            if row is None:
                continue
            warn_key = f"{key}_{WARN_THRESHOLD}"
            crit_key = f"{key}_{CRIT_THRESHOLD}"
            prev = self._prev_pcts.get(key)

            # Reset detection: pct dropped significantly (≥10 pp) from above-warn to below
            if (reset_enabled and prev is not None
                    and prev >= WARN_THRESHOLD and row.pct < WARN_THRESHOLD
                    and (prev - row.pct) >= 10):
                self._warned_pcts.discard(warn_key)
                self._warned_pcts.discard(crit_key)
                _notify(
                    "Claude Usage Bar ✅",
                    f"{row.label} has reset!",
                    f"Now at {row.pct}% — you're good to go.",
                )

            if warn_enabled:
                if row.pct >= CRIT_THRESHOLD and crit_key not in self._warned_pcts:
                    self._warned_pcts.add(crit_key)
                    _notify(
                        "Claude Usage Bar 🔴",
                        f"{row.label} is at {row.pct}%!",
                        row.reset_str or "Limit almost reached",
                    )
                elif row.pct >= WARN_THRESHOLD and warn_key not in self._warned_pcts:
                    self._warned_pcts.add(warn_key)
                    _notify(
                        "Claude Usage Bar 🟡",
                        f"{row.label} is at {row.pct}%",
                        row.reset_str or "Approaching limit",
                    )
                elif row.pct < WARN_THRESHOLD:
                    self._warned_pcts.discard(warn_key)
                    self._warned_pcts.discard(crit_key)

            self._prev_pcts[key] = row.pct

    def _check_provider_warnings(self, provider_data: list):
        """Send macOS notification when provider rate limits cross a threshold or reset."""
        _warn_providers = [
            ("ChatGPT", "chatgpt", "chatgpt_warning", "chatgpt_reset"),
            ("Cursor",  "cursor",  "cursor_warning",  None),
        ]
        for pname, prefix, warn_nkey, reset_nkey in _warn_providers:
            pd = next((p for p in provider_data if p.name == pname), None)
            if pd is None or pd.error:
                continue

            rows = getattr(pd, "_rows", None) or []
            warn_enabled = _notif_enabled(self.config, warn_nkey)
            reset_enabled = _notif_enabled(self.config, reset_nkey) if reset_nkey else False

            for row in rows:
                key = f"{prefix}_{row.label}"
                warn_key = f"{key}_{WARN_THRESHOLD}"
                crit_key = f"{key}_{CRIT_THRESHOLD}"
                prev = self._prev_pcts.get(key)

                if (reset_enabled and prev is not None
                        and prev >= WARN_THRESHOLD and row.pct < WARN_THRESHOLD
                        and (prev - row.pct) >= 10):
                    self._warned_pcts.discard(warn_key)
                    self._warned_pcts.discard(crit_key)
                    _notify(
                        "Claude Usage Bar ✅",
                        f"{pname} {row.label} has reset!",
                        f"Now at {row.pct}% — you're good to go.",
                    )

                if warn_enabled:
                    if row.pct >= CRIT_THRESHOLD and crit_key not in self._warned_pcts:
                        self._warned_pcts.add(crit_key)
                        _notify(
                            "Claude Usage Bar 🔴",
                            f"{pname} {row.label} is at {row.pct}%!",
                            row.reset_str or "Limit almost reached",
                        )
                    elif row.pct >= WARN_THRESHOLD and warn_key not in self._warned_pcts:
                        self._warned_pcts.add(warn_key)
                        _notify(
                            "Claude Usage Bar 🟡",
                            f"{pname} {row.label} is at {row.pct}%",
                            row.reset_str or "Approaching limit",
                        )
                    elif row.pct < WARN_THRESHOLD:
                        self._warned_pcts.discard(warn_key)
                        self._warned_pcts.discard(crit_key)

                self._prev_pcts[key] = row.pct

    def _check_pacing_alerts(self):
        """Send predictive notification when ETA drops below PACING_ALERT_MINUTES."""
        # Static entries (single history key per provider)
        checks: list[tuple[str, str, str]] = [
            ("claude",  "claude_pacing",  "Claude session"),
            ("copilot", "copilot_pacing", "Copilot"),
        ]
        # Dynamic per-row entries for multi-limit providers
        for prefix, pname, nkey in [
            ("chatgpt", "ChatGPT", "chatgpt_pacing"),
            ("cursor",  "Cursor",  "cursor_pacing"),
        ]:
            pd = next((p for p in self._provider_data if p.name == pname), None)
            if pd and not pd.error:
                rows = getattr(pd, "_rows", None) or []
                for row in rows:
                    hkey = f"{prefix}_{row.label.lower().replace(' ', '_')}"
                    checks.append((hkey, nkey, f"{pname} {row.label}"))

        for hkey, nkey, label in checks:
            if not _notif_enabled(self.config, nkey):
                continue
            eta = _calc_eta_minutes(self._history, hkey)
            if eta is not None and eta <= PACING_ALERT_MINUTES:
                if hkey not in self._pacing_alerted:
                    self._pacing_alerted.add(hkey)
                    _notify(
                        "Claude Usage Bar ⏱",
                        f"Slow down — {label} limit in ~{_fmt_eta(eta)}",
                        "At your current pace you'll hit the cap soon.",
                    )
            else:
                self._pacing_alerted.discard(hkey)

    # Bar icon/color config per provider name
    _BAR_PROVIDERS = {
        "Claude":  {"icon": "claude_icon.png",        "tint": None,      "color": "#D97757", "sym": "●"},
        "ChatGPT": {"icon": "chatgpt_icon_clean.png", "tint": "#74AA9C", "color": "#74AA9C", "sym": "◇"},
        "Cursor":  {"icon": "cursor.png",             "tint": "#6699FF", "color": "#6699FF", "sym": "◈"},
        "Copilot": {"icon": "copilot.png",            "tint": "#8CBFF3", "color": "#8CBFF3", "sym": "◆"},
    }

    def _set_bar_title(self, provider_segments: list[tuple[str, int, str]],
                       cc_msgs: int | None = None):
        """Multi-indicator attributed title with brand logo icons.

        provider_segments: list of (provider_name, pct, extra_suffix)
          e.g. [("Claude", 36, " ·"), ("ChatGPT", 12, "")]

        Falls back to colored text symbols if AppKit / icons unavailable.
        """
        try:
            from AppKit import (NSColor, NSFont,
                                NSForegroundColorAttributeName, NSFontAttributeName)
            from Foundation import NSMutableAttributedString, NSAttributedString

            def _rgb(hex_str):
                r = int(hex_str[1:3], 16) / 255
                g = int(hex_str[3:5], 16) / 255
                b = int(hex_str[5:7], 16) / 255
                return NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 1.0)

            font = NSFont.menuBarFontOfSize_(0)
            base = {NSFontAttributeName: font} if font else {}

            s = NSMutableAttributedString.alloc().initWithString_("",)

            for i, (name, pct, suffix) in enumerate(provider_segments):
                cfg = self._BAR_PROVIDERS.get(name, {})
                color_hex = cfg.get("color", "#AAAAAA")
                color = _rgb(color_hex)

                if i > 0:
                    s.appendAttributedString_(
                        NSAttributedString.alloc().initWithString_attributes_("   ", base)
                    )

                icon_file = cfg.get("icon")
                tint = cfg.get("tint")
                img = _bar_icon(icon_file, tint_hex=tint) if icon_file else None
                if img:
                    s.appendAttributedString_(_icon_astr(img, base))
                else:
                    sym = cfg.get("sym", "●")
                    seg = NSMutableAttributedString.alloc().initWithString_attributes_(f"{sym} ", base)
                    seg.addAttribute_value_range_(NSForegroundColorAttributeName, color, (0, len(sym)))
                    s.appendAttributedString_(seg)

                s.appendAttributedString_(
                    NSAttributedString.alloc().initWithString_attributes_(f" {pct}%{suffix}", base)
                )

            # ── Claude Code  ◆ 3.2k ────────────────────────
            if cc_msgs is not None and cc_msgs > 0:
                cc_color = _rgb("#D97757")
                seg = NSMutableAttributedString.alloc().initWithString_attributes_(
                    f"   ◆ {_fmt_count(cc_msgs)}", base
                )
                seg.addAttribute_value_range_(NSForegroundColorAttributeName, cc_color, (3, 2))
                s.appendAttributedString_(seg)

            self._nsapp.nsstatusitem.setAttributedTitle_(s)
            return
        except Exception as e:
            log.debug("_set_bar_title failed: %s", e)
        # Plain-text fallback
        parts = []
        for name, pct, suffix in provider_segments:
            cfg = self._BAR_PROVIDERS.get(name, {})
            sym = cfg.get("sym", "●")
            parts.append(f"{sym} {pct}%{suffix}")
        if cc_msgs is not None and cc_msgs > 0:
            parts.append(f"◆ {_fmt_count(cc_msgs)}")
        self.title = "  ".join(parts)

    def _provider_bar_pct(self, pd: ProviderData) -> int | None:
        """Extract a single percentage for the menu bar from a provider."""
        if pd.error:
            return None
        rows = getattr(pd, "_rows", None)
        if rows:
            return max(r.pct for r in rows)
        if pd.pct is not None:
            return pd.pct
        return None

    # Priority order for the 2 bar slots (highest first)
    _BAR_PRIORITY = ["Claude", "ChatGPT", "Cursor", "Copilot"]

    def _apply(self, data: UsageData):
        primary = data.session or data.weekly_all or data.weekly_sonnet
        if primary:
            weekly_maxed = any(
                r and r.pct >= CRIT_THRESHOLD
                for r in [data.weekly_all, data.weekly_sonnet]
            )
            extra = " ·" if (weekly_maxed and primary is data.session
                             and primary.pct < CRIT_THRESHOLD) else ""

            # Collect all available segments
            available: dict[str, tuple[str, int, str]] = {}
            available["Claude"] = ("Claude", primary.pct, extra)
            for pd in self._provider_data:
                bar_pct = self._provider_bar_pct(pd)
                if bar_pct is not None:
                    available[pd.name] = (pd.name, bar_pct, "")

            # User-configured bar providers, or auto top 2 by priority
            chosen = self.config.get("bar_providers")
            if chosen:
                segments = [available[n] for n in chosen if n in available]
            else:
                segments = [available[n] for n in self._BAR_PRIORITY
                            if n in available][:2]

            # Claude Code weekly messages
            cc_msgs: int | None = None
            if self._cc_stats:
                cc_msgs = self._cc_stats.get("week_messages")

            self._set_bar_title(segments, cc_msgs=cc_msgs)
        else:
            self.title = "◆"
        self._rebuild_menu(data)

    def _fetch_providers(self):
        """Fetch all configured third-party API providers (sync, called from fetch thread)."""
        # Auto-detect ChatGPT cookies if not saved yet
        _cookie_detectors = {
            "chatgpt_cookies": _auto_detect_chatgpt_cookies,
            "copilot_cookies": _auto_detect_copilot_cookies,
            "cursor_cookies":  _auto_detect_cursor_cookies,
        }
        for cfg_key in _COOKIE_PROVIDERS:
            with self._config_lock:
                has_key = bool(self.config.get(cfg_key))
            if not has_key:
                detect_fn = _cookie_detectors.get(cfg_key)
                if detect_fn:
                    ck = detect_fn()
                    if ck:
                        with self._config_lock:
                            self.config[cfg_key] = ck
                            save_config(self.config)

        with self._config_lock:
            keys_snapshot = {k: self.config.get(k) for k in PROVIDER_REGISTRY}
        tasks = []
        for cfg_key, (name, fetch_fn) in PROVIDER_REGISTRY.items():
            key = keys_snapshot.get(cfg_key)
            if key:
                tasks.append((fetch_fn, key))
        if tasks:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            results = []
            with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
                futures = {pool.submit(fn, k): (fn, k) for fn, k in tasks}
                for future in as_completed(futures):
                    try:
                        results.append(future.result())
                    except Exception:
                        log.exception("provider fetch failed")
            self._provider_data = results
        else:
            self._provider_data = []

    # ── callbacks ─────────────────────────────────────────────────────────────

    def _do_refresh(self, _sender):
        self._schedule_fetch()

    def _open_usage_page(self, _sender):
        subprocess.Popen(["open", "https://claude.ai/settings/usage"])

    def _open_history_window(self, _sender):
        try:
            _show_history_window(self._history_db)
        except Exception:
            log.exception("Failed to open history window")

    def _open_github(self, _sender):
        subprocess.Popen(["open", "https://github.com/yagcioglutoprak/AIQuotaBar"])

    def _share_on_x(self, _sender):
        data = self._last_data
        if data and data.session:
            pct = int(data.session.pct)
            icon = _status_icon(pct)
            text = (
                f"I'm at {pct}% of my Claude session limit {icon}\n"
                f"Tracking Claude + ChatGPT + Cursor usage live in my macOS menu bar "
                f"— zero setup, auto-detects from browser\n"
                f"github.com/yagcioglutoprak/AIQuotaBar"
            )
        else:
            text = (
                "Track Claude + ChatGPT + Cursor usage live in your macOS menu bar "
                "— zero setup, auto-detects from browser\n"
                "github.com/yagcioglutoprak/AIQuotaBar"
            )
        url = "https://x.com/intent/post?text=" + urllib.parse.quote(text)
        subprocess.Popen(["open", url])

    def _make_provider_key_cb(self, cfg_key: str, name: str):
        def _cb(_sender):
            if cfg_key in _COOKIE_PROVIDERS:
                # Cookie-based: re-run auto-detect
                _detectors = {
                    "chatgpt_cookies": _auto_detect_chatgpt_cookies,
                    "copilot_cookies": _auto_detect_copilot_cookies,
                    "cursor_cookies":  _auto_detect_cursor_cookies,
                }
                detect_fn = _detectors.get(cfg_key)
                if detect_fn:
                    ck = detect_fn()
                    if ck:
                        self.config[cfg_key] = ck
                        save_config(self.config)
                        _notify("Claude Usage Bar", f"{name} cookies updated ✓", "Fetching usage…")
                        self._schedule_fetch()
                    else:
                        _notify("Claude Usage Bar", f"Could not find {name} session",
                                f"Make sure you are logged into {name} in your browser.")
                return
            # API key-based
            current = self.config.get(cfg_key, "")
            key = _ask_text(
                title=f"Claude Usage Bar — {name}",
                prompt=f"Paste your {name} API key.\nLeave blank to remove.",
                default=current,
            )
            if key is None:
                return
            if key.strip():
                self.config[cfg_key] = key.strip()
            else:
                self.config.pop(cfg_key, None)
            save_config(self.config)
            self._schedule_fetch()
        return _cb

    def _make_notif_toggle_cb(self, nkey: str):
        def _cb(sender):
            current = _notif_enabled(self.config, nkey)
            _set_notif(self.config, nkey, not current)
            sender._menuitem.setState_(0 if current else 1)
        return _cb

    def _make_interval_cb(self, secs: int, label: str):
        def _cb(_sender):
            self._refresh_interval = secs
            self.config["refresh_interval"] = secs
            save_config(self.config)
            self._timer.stop()
            self._timer = rumps.Timer(self._on_timer, secs)
            self._timer.start()
            self._rebuild_menu(self._last_data)
        return _cb

    _TOGGLE_ICONS = {
        "Claude":  ("claude_icon.png",        None),
        "ChatGPT": ("chatgpt_icon_clean.png", "#74AA9C"),
        "Cursor":  ("cursor.png",             "#6699FF"),
        "Copilot": ("copilot.png",            "#8CBFF3"),
    }

    def _make_sticky_toggle(self, display_name: str, is_on: bool, name: str):
        """Create a menu item that stays open on click (custom NSView) with a real icon."""
        item = rumps.MenuItem("")

        if _HAS_TOGGLE_VIEW:
            from AppKit import NSImageView

            view_w, view_h = 220, 22
            check_w = 22          # space for checkmark
            icon_sz = 16
            icon_pad = 4
            label_x = check_w + icon_sz + icon_pad + 4

            view = _BarToggleView.alloc().initWithFrame_(NSMakeRect(0, 0, view_w, view_h))

            # Checkmark label
            check = NSTextField.labelWithString_("✓" if is_on else "")
            check.setFont_(NSFont.menuFontOfSize_(14))
            check.setFrame_(NSMakeRect(6, 1, check_w - 4, view_h - 2))
            check.setBezeled_(False)
            check.setDrawsBackground_(False)
            check.setEditable_(False)
            check.setSelectable_(False)
            view.addSubview_(check)

            # Real icon
            icon_file, icon_tint = self._TOGGLE_ICONS.get(name, (None, None))
            if icon_file:
                img = _menu_icon(icon_file, tint_hex=icon_tint, size=icon_sz)
                if img:
                    iv = NSImageView.alloc().initWithFrame_(
                        NSMakeRect(check_w, (view_h - icon_sz) / 2, icon_sz, icon_sz)
                    )
                    iv.setImage_(img)
                    view.addSubview_(iv)

            # Provider name label
            label = NSTextField.labelWithString_(display_name)
            label.setFont_(NSFont.menuFontOfSize_(14))
            label.setFrame_(NSMakeRect(label_x, 1, view_w - label_x - 4, view_h - 2))
            label.setBezeled_(False)
            label.setDrawsBackground_(False)
            label.setEditable_(False)
            label.setSelectable_(False)
            view.addSubview_(label)

            view._label = label
            view._check = check
            self._bar_toggle_views[name] = (view, label, check)

            def _make_action(n):
                def _action():
                    self._do_bar_toggle(n)
                return _action

            view._action = _make_action(name)
            item._menuitem.setView_(view)
        else:
            # Fallback: standard menu item (will close on click)
            item = rumps.MenuItem(display_name, callback=lambda _: self._do_bar_toggle(name))
            item._menuitem.setState_(1 if is_on else 0)
            icon_file, icon_tint = self._TOGGLE_ICONS.get(name, (None, None))
            if icon_file:
                img = _menu_icon(icon_file, tint_hex=icon_tint, size=16)
                if img:
                    item._menuitem.setImage_(img)

        return item

    def _do_bar_toggle(self, name: str):
        """Toggle a provider in the status bar and update views in-place."""
        chosen = self.config.get("bar_providers")
        if not chosen:
            # Switching from auto → manual: seed with current auto selection
            available_names = {"Claude"} if self._last_data else set()
            for pd in self._provider_data:
                if self._provider_bar_pct(pd) is not None:
                    available_names.add(pd.name)
            chosen = [n for n in self._BAR_PRIORITY if n in available_names][:2]
        if name in chosen:
            chosen.remove(name)
        else:
            chosen.append(name)
        # If empty after removal, go back to auto
        if not chosen:
            self.config.pop("bar_providers", None)
        else:
            self.config["bar_providers"] = chosen
        save_config(self.config)

        # Update all toggle views in-place (no menu rebuild needed)
        effective = self.config.get("bar_providers")
        if not effective:
            available_names = {"Claude"} if self._last_data else set()
            for pd in self._provider_data:
                if self._provider_bar_pct(pd) is not None:
                    available_names.add(pd.name)
            auto_shown = set(
                [n for n in self._BAR_PRIORITY if n in available_names][:2]
            )
        else:
            auto_shown = None

        for n, (view, label, check) in self._bar_toggle_views.items():
            is_on = n in effective if effective else n in auto_shown
            check.setStringValue_("✓" if is_on else "")

        if self._last_data:
            self._apply(self._last_data)

    def _bar_reset_auto(self, _sender):
        """Reset bar display to auto-detect (top 2 active providers)."""
        self.config.pop("bar_providers", None)
        save_config(self.config)
        self._rebuild_menu(self._last_data)
        if self._last_data:
            self._apply(self._last_data)

    def _set_cookie(self, _sender):
        key = _ask_text(
            title="Claude Usage — Set Cookies",
            prompt=(
                "Paste ALL cookies from claude.ai (needed to bypass Cloudflare)\n\n"
                "How to get them:\n"
                "  1. Open https://claude.ai/settings/usage in Chrome\n"
                "  2. F12 → Network tab → click any request to claude.ai\n"
                "  3. In Headers, find the 'cookie:' row\n"
                "  4. Right-click it → Copy value  (long string with semicolons)"
            ),
            default=self.config.get("cookie_str", ""),
        )
        if key:
            self.config["cookie_str"] = key.strip()
            save_config(self.config)
            self._warned_pcts.clear()
            self._auth_fail_count = 0
            self._schedule_fetch()

    def _paste_cookie(self, _sender):
        text = _clipboard_text()
        if not text or ("sessionKey" not in text and "=" not in text):
            _notify(
                "Claude Usage Bar",
                "Nothing useful in clipboard",
                "Copy your cookie string from Chrome DevTools first.",
            )
            return
        self.config["cookie_str"] = text
        save_config(self.config)
        self._warned_pcts.clear()
        self._auth_fail_count = 0
        self._schedule_fetch()
        _notify(
            "Claude Usage Bar",
            "Cookie updated from clipboard ✓",
            "Fetching usage data…",
        )

    def _show_raw(self, _sender):
        text = json.dumps(self._last_raw.get("usage", self._last_raw), indent=2)
        _show_text(title="Claude Usage — Raw API Response", text=text)

    def _toggle_login_item(self, sender):
        if _is_login_item():
            _remove_login_item()
            self._login_item_cached = False
            sender._menuitem.setState_(0)
            _notify("Claude Usage Bar", "Removed from Login Items", "")
        else:
            _add_login_item()
            self._login_item_cached = True
            sender._menuitem.setState_(1)
            _notify("Claude Usage Bar", "Added to Login Items", "Will launch automatically on login")

    def _try_auto_detect(self):
        """Background: silently try to grab cookies from the browser on first run."""
        cookie_str = _auto_detect_cookies()
        if cookie_str:
            self.config["cookie_str"] = cookie_str
            save_config(self.config)
            _notify(
                "Claude Usage Bar",
                "Cookies auto-detected from your browser ✓",
                "Fetching usage data…",
            )
            self._schedule_fetch()

    def _auto_detect_menu(self, _sender):
        """Menu item: manually trigger auto-detect (runs in background thread)."""
        if not _BROWSER_COOKIE3_OK:
            _notify(
                "Claude Usage Bar",
                "browser-cookie3 not installed",
                "Run: pip install browser-cookie3",
            )
            return
        # Run cookie detection in a background thread — browser_cookie3 accesses
        # SQLite databases and Keychain which can hard-crash if called on the main thread.
        threading.Thread(target=self._do_auto_detect, daemon=True).start()

    def _do_auto_detect(self):
        """Background: detect cookies then schedule a fetch."""
        try:
            cookie_str = _auto_detect_cookies()
        except Exception:
            log.exception("_auto_detect_cookies failed")
            cookie_str = None
        if cookie_str:
            with self._config_lock:
                self.config["cookie_str"] = cookie_str
                save_config(self.config)
            self._warned_pcts.clear()
            self._auth_fail_count = 0
            _notify("Claude Usage Bar", "Cookies auto-detected ✓", "Fetching usage data…")
            self._schedule_fetch()
        else:
            _notify(
                "Claude Usage Bar",
                "Could not find claude.ai session in any browser",
                "Make sure you are logged in to claude.ai in Chrome, Firefox, or Safari.",
            )


def _cli_history():
    """Print a 7-day usage history chart to the terminal."""
    if not os.path.exists(HISTORY_DB):
        print("No history data yet. Run AIQuotaBar for a while first.")
        return

    conn = sqlite3.connect(HISTORY_DB)
    conn.execute("PRAGMA journal_mode=WAL")
    keys = [r[0] for r in conn.execute(
        "SELECT DISTINCT key FROM daily_stats ORDER BY key"
    ).fetchall()]

    if not keys:
        print("No history data yet. Run AIQuotaBar for a while first.")
        conn.close()
        return

    # ANSI color map per provider prefix
    _colors = {
        "claude": "\033[38;5;209m",   # orange
        "chatgpt": "\033[38;5;114m",  # green
        "copilot": "\033[38;5;141m",  # purple
        "cursor": "\033[38;5;45m",    # cyan
    }
    _reset = "\033[0m"
    _dim = "\033[2m"
    _bold = "\033[1m"

    print(f"\n{_bold}  AIQuotaBar — 7-Day Usage History{_reset}\n")

    for key in keys:
        stats = _get_weekly_stats(conn, key)
        if not stats:
            continue

        # Determine color from key prefix
        prefix = key.split("_")[0]
        color = _colors.get(prefix, "")
        label = key.replace("_", " ").title()

        print(f"  {color}{_bold}{label}{_reset}")

        bar_width = 30
        for d in stats:
            try:
                day_name = datetime.strptime(d["date"], "%Y-%m-%d").strftime("%a")
            except Exception:
                day_name = d["date"][-5:]
            pct = d["peak_pct"]
            filled = round(pct / 100 * bar_width)
            bar = "█" * filled + "░" * (bar_width - filled)
            hit_mark = " ⚠" if d["limit_hits"] > 0 else ""
            print(f"    {_dim}{day_name}{_reset}  {color}{bar}{_reset}  {pct}%{hit_mark}")

        # Summary line
        peaks = [d["peak_pct"] for d in stats]
        avgs = [d["avg_pct"] for d in stats]
        total_hits = sum(d["limit_hits"] for d in stats)
        avg_all = round(sum(avgs) / len(avgs)) if avgs else 0
        peak_all = max(peaks) if peaks else 0
        summary = f"    avg {avg_all}%  ·  peak {peak_all}%"
        if total_hits > 0:
            summary += f"  ·  hit limit {total_hits}x"
        print(f"  {_dim}{summary}{_reset}\n")

    conn.close()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("--history", "-H"):
        _cli_history()
    else:
        ClaudeBar().run()
