"""Usage history tracking -- burn rate, 24h trends, SQLite persistence."""

import json
import math
import os
import sqlite3
from datetime import datetime, timezone, timedelta

from aiquotabar.config import (
    HISTORY_FILE, HISTORY_MAX_AGE, HISTORY_DB,
    SAMPLES_MAX_DAYS, DAILY_MAX_DAYS, LIMIT_HIT_PCT,
    BURN_WINDOW, MIN_SPAN_SECS, RESET_DROP_PCT,
)


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

    # Detect reset: if pct dropped by >=RESET_DROP_PCT, discard old data.
    # This prevents stale pre-reset points from poisoning the regression.
    if entries and (entries[-1]["pct"] - pct) >= RESET_DROP_PCT:
        entries.clear()

    entries.append({"t": now, "pct": pct})
    cutoff = now - HISTORY_MAX_AGE
    history[key] = [e for e in entries if e["t"] >= cutoff]


def _calc_burn_rate(history: dict, key: str, now: float | None = None) -> float | None:
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
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    cutoff = now - BURN_WINDOW
    # Ignore points from the future (clock changes) — they'd blow up the weights.
    recent = [e for e in entries if cutoff <= e["t"] <= now + 60]
    if len(recent) < 2:
        return None

    # Require minimum time span to avoid noisy estimates from clustered points
    span = recent[-1]["t"] - recent[0]["t"]
    if span < MIN_SPAN_SECS:
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


def _calc_eta_minutes(history: dict, key: str, now: float | None = None) -> int | None:
    """Estimate minutes until 100% based on burn rate.

    Returns None if burn rate is non-positive or ETA > 10 hours.
    """
    entries = history.get(key, [])
    if not entries:
        return None
    current_pct = entries[-1]["pct"]
    rate = _calc_burn_rate(history, key, now)
    if rate is None or rate <= 0:
        return None
    remaining = 100 - current_pct
    if remaining <= 0:
        return 0
    eta = remaining / rate  # minutes
    if eta > 600:  # > 10 hours
        return None
    return max(1, round(eta))


# -- SQLite history functions --------------------------------------------------

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

    # Completed days that have not been rolled up yet. Samples are kept for
    # SAMPLES_MAX_DAYS after rollup so the 24h trend chart spans midnight.
    rows = conn.execute(
        "SELECT DISTINCT date(ts, 'unixepoch') AS d FROM samples "
        "WHERE d < ? AND d NOT IN (SELECT DISTINCT date FROM daily_stats) ORDER BY d",
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
        """, (LIMIT_HIT_PCT, day)).fetchall()

        for key, peak, avg, cnt, hits in agg:
            conn.execute("""
                INSERT INTO daily_stats (date, key, peak_pct, avg_pct, limit_hits, samples)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(date, key) DO UPDATE SET
                    peak_pct=excluded.peak_pct, avg_pct=excluded.avg_pct,
                    limit_hits=excluded.limit_hits, samples=excluded.samples
            """, (day, key, peak, avg, hits, cnt))

    # Prune old data
    cutoff_samples = (datetime.now(timezone.utc) - timedelta(days=SAMPLES_MAX_DAYS)).timestamp()
    conn.execute("DELETE FROM samples WHERE ts < ?", (cutoff_samples,))
    cutoff_daily = (datetime.now(timezone.utc) - timedelta(days=DAILY_MAX_DAYS)).strftime("%Y-%m-%d")
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
    # Also count today's samples that are at limit (past days are in daily_stats)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_row = conn.execute(
        "SELECT COUNT(*) FROM samples WHERE key = ? AND pct >= ? "
        "AND date(ts, 'unixepoch') = ?",
        (key, LIMIT_HIT_PCT, today),
    ).fetchone()
    return (row[0] if row else 0) + (today_row[0] if today_row else 0)


def get_trend(conn: sqlite3.Connection, key: str, hours: int = 24,
              max_points: int = 96) -> list[tuple[float, int]]:
    """Samples for `key` over the last `hours`, bucketed to at most max_points
    (keeping each bucket's peak so short spikes survive downsampling)."""
    since = datetime.now(timezone.utc).timestamp() - hours * 3600
    rows = conn.execute(
        "SELECT ts, pct FROM samples WHERE key = ? AND ts >= ? ORDER BY ts",
        (key, since),
    ).fetchall()
    if len(rows) <= max_points:
        return [(float(t), int(p)) for t, p in rows]
    bucket = hours * 3600 / max_points
    out: dict[int, tuple[float, int]] = {}
    for t, p in rows:
        b = int((t - since) // bucket)
        if b not in out or p >= out[b][1]:
            out[b] = (float(t), int(p))
    return [out[b] for b in sorted(out)]


def _get_today_stats(conn: sqlite3.Connection) -> dict[str, dict]:
    """Compute live stats from today's samples (not yet rolled up)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = conn.execute("""
        SELECT key, MAX(pct), CAST(AVG(pct) AS INTEGER), COUNT(*),
               SUM(CASE WHEN pct >= ? THEN 1 ELSE 0 END)
        FROM samples
        WHERE date(ts, 'unixepoch') = ?
        GROUP BY key
    """, (LIMIT_HIT_PCT, today)).fetchall()
    result = {}
    for key, peak, avg, cnt, hits in rows:
        result[key] = {
            "date": today, "peak_pct": peak, "avg_pct": avg,
            "limit_hits": hits, "samples": cnt,
        }
    return result


def cli_history():
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

    print(f"\n{_bold}  AIQuotaBar -- 7-Day Usage History{_reset}\n")

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
            bar = "\u2588" * filled + "\u2591" * (bar_width - filled)
            hit_mark = " \u26a0" if d["limit_hits"] > 0 else ""
            print(f"    {_dim}{day_name}{_reset}  {color}{bar}{_reset}  {pct}%{hit_mark}")

        # Summary line
        peaks = [d["peak_pct"] for d in stats]
        avgs = [d["avg_pct"] for d in stats]
        total_hits = sum(d["limit_hits"] for d in stats)
        avg_all = round(sum(avgs) / len(avgs)) if avgs else 0
        peak_all = max(peaks) if peaks else 0
        summary = f"    avg {avg_all}%  \u00b7  peak {peak_all}%"
        if total_hits > 0:
            summary += f"  \u00b7  hit limit {total_hits}x"
        print(f"  {_dim}{summary}{_reset}\n")

    conn.close()
