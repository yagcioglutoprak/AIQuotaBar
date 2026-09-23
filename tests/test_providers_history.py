import sqlite3
import time
from datetime import datetime, timezone

from aiquotabar import history
from aiquotabar.providers import (
    _next_month_reset, _parse_ts, _parse_wham_usage, fmt_reset_ts, parse_usage,
)

H, D = 3600, 86400


def test_parse_ts_variants():
    assert _parse_ts(1_700_000_000) == 1_700_000_000.0
    assert _parse_ts(1_700_000_000_000) == 1_700_000_000.0            # milliseconds
    assert _parse_ts("2026-01-01T00:00:00Z") == datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
    assert _parse_ts("2026-01-01T00:00:00.123+00:00") is not None
    assert _parse_ts(None) is None and _parse_ts("garbage") is None


def test_fmt_reset_ts():
    now = 1_800_000_000.0
    assert fmt_reset_ts(now - 5, now) == "resets soon"
    assert fmt_reset_ts(now + 2 * H + 14 * 60, now) == "resets in 2h 14m"
    assert fmt_reset_ts(now + 30, now) == "resets in 1m"
    assert fmt_reset_ts(now + 3 * D, now).startswith("resets ")          # weekday + time
    assert fmt_reset_ts(now + 11 * D + 60, now) == "resets in 11d"


def test_parse_usage_keeps_timestamps_and_windows():
    reset = "2030-01-01T10:00:00.000000+00:00"
    raw = {"usage": {
        "five_hour": {"utilization": 41.6, "resets_at": reset},
        "seven_day": {"utilization": 12, "resets_at": reset},
        "seven_day_sonnet": None,
        "seven_day_opus": {"utilization": 3, "resets_at": reset},
        "extra_usage": None,
    }}
    data = parse_usage(raw)
    assert data.session.pct == 42 and data.session.window_secs == 5 * H
    assert data.session.resets_at == _parse_ts(reset)
    assert data.weekly_all.window_secs == 7 * D
    assert data.weekly_sonnet is None
    assert data.weekly_opus.label == "Opus Only"


def test_wham_primary_and_secondary_windows():
    now = time.time()
    pd = _parse_wham_usage({
        "rate_limit": {
            "primary_window": {"used_percent": 12, "reset_at": now + H, "limit_window_seconds": 18000},
            "secondary_window": {"used_percent": 64, "reset_after_seconds": 2 * D,
                                 "limit_window_seconds": 604800},
        },
        "code_review_rate_limit": {"primary_window": {"used_percent": 0}},
    })
    labels = [(r.label, r.pct, r.window_secs) for r in pd._rows]
    assert labels == [("Codex Tasks", 12, 18000), ("Codex Tasks Weekly", 64, 604800),
                      ("Code Review", 0, None)]
    assert abs(pd._rows[1].resets_at - (now + 2 * D)) < 5
    assert pd.spent == 64.0


def test_wham_without_secondary_is_unchanged():
    pd = _parse_wham_usage({"rate_limit": {"primary_window": {"used_percent": 5}}})
    assert [r.label for r in pd._rows] == ["Codex Tasks"]


def test_next_month_reset():
    ts, window = _next_month_reset(datetime(2026, 2, 10, 15, tzinfo=timezone.utc))
    assert datetime.fromtimestamp(ts, tz=timezone.utc) == datetime(2026, 3, 1, tzinfo=timezone.utc)
    assert window == 28 * D
    ts, _ = _next_month_reset(datetime(2026, 12, 31, 23, tzinfo=timezone.utc))
    assert datetime.fromtimestamp(ts, tz=timezone.utc).year == 2027


def _db():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE samples (ts REAL NOT NULL, key TEXT NOT NULL, pct INTEGER NOT NULL)")
    conn.execute("""CREATE TABLE daily_stats (date TEXT NOT NULL, key TEXT NOT NULL,
        peak_pct INTEGER NOT NULL, avg_pct INTEGER NOT NULL, limit_hits INTEGER NOT NULL DEFAULT 0,
        samples INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (date, key))""")
    return conn


def test_rollup_keeps_samples_and_does_not_double_count_hits():
    conn = _db()
    now = time.time()
    yesterday = now - D
    for i in range(5):
        conn.execute("INSERT INTO samples VALUES (?,?,?)", (yesterday + i, "claude", 99))
    conn.execute("INSERT INTO samples VALUES (?,?,?)", (now, "claude", 99))
    history._rollup_daily_stats(conn)
    history._rollup_daily_stats(conn)    # idempotent
    assert conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 6   # kept for trends
    assert conn.execute("SELECT limit_hits FROM daily_stats").fetchone()[0] == 5
    assert history._get_week_limit_hits(conn, "claude") == 6                # 5 past + 1 today


def test_get_trend_downsamples_but_keeps_peaks():
    conn = _db()
    now = time.time()
    for i in range(500):
        conn.execute("INSERT INTO samples VALUES (?,?,?)", (now - i * 120, "claude", 100 if i == 250 else 10))
    pts = history.get_trend(conn, "claude", hours=24, max_points=96)
    assert len(pts) <= 96
    assert max(p for _, p in pts) == 100
    assert [t for t, _ in pts] == sorted(t for t, _ in pts)
