import json
import sqlite3
import time
from datetime import datetime, timezone

import pytest

from aiquotabar import history, providers
from aiquotabar.providers import (
    CurlHTTPError, _next_month_reset, _parse_ts, _parse_wham_usage, fmt_reset_ts, parse_usage,
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


def test_wham_additional_rate_limits_include_secondary_window():
    pd = _parse_wham_usage({
        "rate_limit": {"primary_window": {"used_percent": 5}},
        "additional_rate_limits": [{
            "name": "gpt_5_codex",
            "primary_window": {"used_percent": 20, "limit_window_seconds": 18000},
            "secondary_window": {"used_percent": 71, "reset_after_seconds": 3 * D,
                                 "limit_window_seconds": 604800},
        }],
    })
    labels = [(r.label, r.pct, r.window_secs) for r in pd._rows]
    assert labels == [("Codex Tasks", 5, None), ("Gpt 5 Codex", 20, 18000),
                      ("Gpt 5 Codex Weekly", 71, 604800)]
    assert pd._rows[2].resets_at is not None
    assert pd.spent == 71.0


def test_wham_without_secondary_is_unchanged():
    pd = _parse_wham_usage({"rate_limit": {"primary_window": {"used_percent": 5}}})
    assert [r.label for r in pd._rows] == ["Codex Tasks"]


# ── Claude org id ────────────────────────────────────────────────────────────

def test_org_id_prefers_uuid(monkeypatch):
    uuid = "0f6c1d2e-3b4a-5c6d-7e8f-9a0b1c2d3e4f"
    monkeypatch.setattr(providers, "_get", lambda url, cookies: [{"id": 123456789, "uuid": uuid}])
    assert providers._org_id_from_api({}) == uuid


def test_org_id_prefers_uuid_in_nested_shapes(monkeypatch):
    uuid = "0f6c1d2e-3b4a-5c6d-7e8f-9a0b1c2d3e4f"
    answers = {
        "/api/organizations": RuntimeError("404"),
        "/api/bootstrap": {"account": {"memberships": [
            {"organization": {"id": 123456789, "uuid": uuid}}]}},
    }

    def fake_get(url, cookies):
        ans = answers.get(url.removeprefix("https://claude.ai"), {})
        if isinstance(ans, Exception):
            raise ans
        return ans

    monkeypatch.setattr(providers, "_get", fake_get)
    assert providers._org_id_from_api({}) == uuid


def test_org_id_falls_back_to_id_without_uuid(monkeypatch):
    monkeypatch.setattr(providers, "_get", lambda url, cookies: [{"id": "legacy-id"}])
    assert providers._org_id_from_api({}) == "legacy-id"


# ── ChatGPT Cloudflare cookies ───────────────────────────────────────────────

class _Resp:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise CurlHTTPError(f"HTTP {self.status_code}", response=self)

    def json(self):
        return self._body


_CHATGPT_COOKIES = "__Secure-next-auth.session-token=abc; cf_clearance=cf; __cf_bm=bm"
_WHAM = {"rate_limit": {"primary_window": {"used_percent": 30}}}


def _fake_chatgpt(monkeypatch, status_without_cf):
    calls = []

    def fake_get(url, headers=None, cookies=None, **kw):
        has_cf = "cf_clearance" in (cookies or {})
        calls.append((url.rsplit("/", 1)[-1], has_cf))
        if not has_cf and status_without_cf != 200:
            return _Resp(status_without_cf)
        return _Resp(200, {"accessToken": "tok"} if url.endswith("/session") else _WHAM)

    monkeypatch.setattr(providers.requests, "get", fake_get)
    return calls


def test_chatgpt_strips_cloudflare_cookies_by_default(monkeypatch):
    calls = _fake_chatgpt(monkeypatch, 200)
    pd = providers.fetch_chatgpt(_CHATGPT_COOKIES)
    assert pd.error is None and pd.spent == 30.0
    assert calls == [("session", False), ("usage", False)]


def test_chatgpt_retries_with_cloudflare_cookies_on_403(monkeypatch):
    calls = _fake_chatgpt(monkeypatch, 403)
    pd = providers.fetch_chatgpt(_CHATGPT_COOKIES)
    assert pd.error is None and pd.spent == 30.0
    assert calls == [("session", False), ("session", True), ("usage", False), ("usage", True)]


def test_chatgpt_does_not_retry_other_errors(monkeypatch):
    calls = _fake_chatgpt(monkeypatch, 401)
    pd = providers.fetch_chatgpt(_CHATGPT_COOKIES)
    assert pd.error and calls == [("session", False)]


def test_chatgpt_403_without_cloudflare_cookies_is_not_retried(monkeypatch):
    calls = _fake_chatgpt(monkeypatch, 403)
    pd = providers.fetch_chatgpt("__Secure-next-auth.session-token=abc")
    assert pd.error and calls == [("session", False)]


# ── ChatGPT through the Codex CLI login ──────────────────────────────────────

@pytest.fixture
def codex_login(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    def write(tokens):
        (tmp_path / "auth.json").write_text(json.dumps({"auth_mode": "chatgpt", "tokens": tokens}))
    return write


def _fake_wham(monkeypatch, session_status=200, usage_status=200):
    calls = []

    def fake_get(url, headers=None, cookies=None, **kw):
        calls.append((url.rsplit("/", 1)[-1], dict(headers or {}), bool(cookies)))
        if url.endswith("/session"):
            return _Resp(session_status, {"accessToken": "browser-tok"})
        return _Resp(usage_status, _WHAM)
    monkeypatch.setattr(providers.requests, "get", fake_get)
    return calls


def test_chatgpt_falls_back_to_codex_login_when_browser_session_fails(monkeypatch, codex_login):
    codex_login({"access_token": "codex-tok", "account_id": "acct-1"})
    calls = _fake_wham(monkeypatch, session_status=401)
    pd = providers.fetch_chatgpt(_CHATGPT_COOKIES)
    assert pd.error is None and pd.spent == 30.0
    assert [c[0] for c in calls] == ["session", "usage"]
    _, headers, sent_cookies = calls[1]
    assert headers["Authorization"] == "Bearer codex-tok"
    assert headers["ChatGPT-Account-Id"] == "acct-1"
    assert sent_cookies is False


def test_working_browser_session_never_reads_the_codex_login(monkeypatch, codex_login):
    codex_login({"access_token": "codex-tok"})
    calls = _fake_wham(monkeypatch)
    assert providers.fetch_chatgpt(_CHATGPT_COOKIES).error is None
    assert all(h.get("Authorization") != "Bearer codex-tok" for _, h, _ in calls)


def test_codex_marker_skips_the_browser(monkeypatch, codex_login):
    codex_login({"access_token": "codex-tok"})
    calls = _fake_wham(monkeypatch)
    pd = providers.fetch_chatgpt(providers.CODEX_CLI_SESSION)
    assert pd.error is None and pd.spent == 30.0
    assert [c[0] for c in calls] == ["usage"]
    assert "ChatGPT-Account-Id" not in calls[0][1]


def test_codex_marker_without_a_login_reports_signed_out(monkeypatch, codex_login):
    calls = _fake_wham(monkeypatch)
    pd = providers.fetch_chatgpt(providers.CODEX_CLI_SESSION)
    assert pd.error == "Not logged in" and calls == []


def test_both_logins_failing_keeps_the_browser_error(monkeypatch, codex_login):
    codex_login({"access_token": "expired-tok"})
    calls = _fake_wham(monkeypatch, session_status=401, usage_status=401)
    pd = providers.fetch_chatgpt(_CHATGPT_COOKIES)
    assert "401" in pd.error
    assert [c[0] for c in calls] == ["session", "usage"]


@pytest.mark.parametrize("content", [
    '{"auth_mode": "apikey", "OPENAI_API_KEY": "sk-x", "tokens": null}',
    '{"tokens": {"access_token": ""}}',
    "not json",
    "[]",
])
def test_codex_login_ignores_files_without_a_chatgpt_token(tmp_path, monkeypatch, content):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "auth.json").write_text(content)
    assert providers._codex_login() is None


def test_detection_falls_back_to_the_codex_login(monkeypatch, codex_login):
    monkeypatch.setattr(providers, "_BROWSER_COOKIE3_OK", True)
    monkeypatch.setattr(providers, "_run_cookie_detection", lambda domain, target: [])
    assert providers._auto_detect_chatgpt_cookies() is None
    codex_login({"access_token": "codex-tok"})
    assert providers._auto_detect_chatgpt_cookies() == providers.CODEX_CLI_SESSION
    monkeypatch.setattr(providers, "_run_cookie_detection", lambda domain, target: ["a=1"])
    assert providers._auto_detect_chatgpt_cookies() == "a=1"        # a browser session wins
    # Without browser_cookie3 (a failed install) the Codex login still works.
    monkeypatch.setattr(providers, "_BROWSER_COOKIE3_OK", False)
    assert providers._auto_detect_chatgpt_cookies() == providers.CODEX_CLI_SESSION


# ── browser cookie detection (the real child-process script) ─────────────────

_FAKE_BROWSER_COOKIE3 = """
import json, os

class _C:
    def __init__(self, name, value, expires):
        self.name, self.value, self.expires = name, value, expires

_JARS = json.loads(os.environ["FAKE_JARS"])

def _jar(browser):
    def fn(domain_name=None):
        if browser not in _JARS:
            raise RuntimeError("no profile")
        return [_C(*c) for c in _JARS[browser]]
    return fn

firefox = _jar("firefox")
chrome = _jar("chrome")
"""


@pytest.fixture
def fake_browsers(tmp_path, monkeypatch):
    (tmp_path / "browser_cookie3.py").write_text(_FAKE_BROWSER_COOKIE3)
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))

    def use(jars):
        monkeypatch.setenv("FAKE_JARS", json.dumps(jars))
    return use


TOKEN = "__Secure-next-auth.session-token"


def test_detects_chunked_nextauth_session(fake_browsers):
    fake_browsers({"chrome": [[f"{TOKEN}.0", "aaa", 2e9], [f"{TOKEN}.1", "bbb", 2e9],
                              ["oai-did", "x", 2e9]]})
    found = providers._run_cookie_detection("chatgpt.com", TOKEN)
    assert found == [f"{TOKEN}.0=aaa; {TOKEN}.1=bbb; oai-did=x"]


def test_detects_unchunked_session_and_ranks_by_expiry(fake_browsers):
    fake_browsers({"firefox": [[TOKEN, "old", 1.9e12]],          # ms, normalised
                   "chrome": [[f"{TOKEN}.0", "new", 2.0e9]]})
    found = providers._run_cookie_detection("chatgpt.com", TOKEN)
    assert found == [f"{TOKEN}.0=new", f"{TOKEN}=old"]


def test_detection_ignores_look_alike_cookie_names(fake_browsers):
    fake_browsers({"chrome": [[f"{TOKEN}-legacy", "x", 2e9], [f"{TOKEN}.1", "y", 2e9]],
                   "firefox": [["sessionKeyLC", "z", 2e9]]})
    assert providers._run_cookie_detection("chatgpt.com", TOKEN) == []
    assert providers._run_cookie_detection("claude.ai", "sessionKey") == []


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
