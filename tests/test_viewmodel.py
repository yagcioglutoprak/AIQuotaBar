import time

from aiquotabar.demo import demo_history_rows, demo_snapshot
from aiquotabar.providers import LimitRow, ProviderData, UsageData
from aiquotabar.viewmodel import (
    Snapshot, apply_setting, bar_segments, build_history_state, build_meter,
    build_panel_state, build_settings_state, fmt_duration, menu_lines, _humanize,
)

H, D = 3600, 86400
NOW = 1_800_000_000.0


def snap(**kw):
    base = dict(config={"refresh_interval": 300}, now=NOW)
    base.update(kw)
    return Snapshot(**base)


def row(label, pct, resets_in=None, window=None):
    ts = NOW + resets_in if resets_in is not None else None
    return LimitRow(label, pct, "", ts, window)


# ── formatting ───────────────────────────────────────────────────────────────

def test_fmt_duration():
    assert fmt_duration(30) == "1m"
    assert fmt_duration(47 * 60) == "47m"
    assert fmt_duration(3600) == "1h"
    assert fmt_duration(2 * H + 14 * 60) == "2h 14m"
    assert fmt_duration(2 * D + 4 * H) == "2d 4h"
    assert fmt_duration(-5) == "1m"


def test_humanize_labels():
    assert _humanize("Codex Tasks") == "Codex tasks"
    assert _humanize("Codex Tasks Weekly") == "Codex tasks · weekly"
    assert _humanize("API") == "API"


# ── meters ───────────────────────────────────────────────────────────────────

def test_pace_marker_is_share_of_window_elapsed():
    s = snap()
    m = build_meter(None, "Session", "", 30, NOW + 2.5 * H, 5 * H, "", s)
    assert m["pace_pct"] == 50.0
    assert m["reset_text"] == "Resets in 2h 30m"


def test_no_pace_without_window_or_reset():
    s = snap()
    assert build_meter(None, "x", "", 30, None, 5 * H, "resets soon", s)["pace_pct"] is None
    assert build_meter(None, "x", "", 30, NOW + H, None, "", s)["pace_pct"] is None


def test_severity_uses_configurable_thresholds():
    s = snap(config={"warn_threshold": 60, "crit_threshold": 70})
    assert build_meter(None, "x", "", 59, None, None, "", s)["severity"] == "ok"
    assert build_meter(None, "x", "", 60, None, None, "", s)["severity"] == "warn"
    assert build_meter(None, "x", "", 70, None, None, "", s)["severity"] == "crit"


def test_limit_reached_note_says_when_it_is_back():
    m = build_meter(None, "x", "", 100, NOW + 47 * 60, 5 * H, "", snap())
    assert m["note"] == {"tone": "crit", "text": "Limit reached · back in 47m"}


def test_window_average_projection_warns_before_reset():
    # 6 of 7 days gone, 90% used: 10% left at ~15%/day → runs out in ~16h < 24h left.
    m = build_meter(None, "Weekly", "", 90, NOW + 1 * D, 7 * D, "", snap())
    assert m["note"]["tone"] == "warn"
    assert m["note"]["text"].startswith("Runs out in ~16h")


def test_under_pace_has_no_note():
    m = build_meter(None, "Weekly", "", 20, NOW + 1 * D, 7 * D, "", snap())
    assert m["note"] is None


def test_burn_rate_eta_note():
    hist = {"claude": [{"t": NOW - (30 - i) * 60, "pct": 50 + i} for i in range(31)]}
    s = snap(history=hist)
    m = build_meter("claude", "Session", "", 80, NOW + 4 * H, 5 * H, "", s)
    # ~1%/min with 20% left → ~20 minutes, which is inside the 30-minute alert window
    assert m["note"]["tone"] == "crit"
    assert "Runs out in ~" in m["note"]["text"]


# ── panel ────────────────────────────────────────────────────────────────────

def test_hero_is_the_binding_constraint():
    data = UsageData(session=row("Current Session", 20, 3 * H, 5 * H),
                     weekly_all=row("All Models", 91, 2 * D, 7 * D))
    st = build_panel_state(snap(claude=data))
    assert st["hero"]["label"] == "Weekly · all models"
    assert st["hero"]["headline"] == "9% left"
    assert st["hero"]["severity"] == "warn"
    claude = st["cards"][0]
    assert [m.get("hero", False) for m in claude["meters"]] == [False, True]


def test_hero_limit_reached_has_no_redundant_insight():
    data = UsageData(session=row("Current Session", 100, 30 * 60, 5 * H))
    st = build_panel_state(snap(claude=data))
    assert st["hero"]["headline"] == "Limit reached"
    assert st["hero"]["insight"] is None


def test_providers_render_even_when_claude_is_missing():
    cg = ProviderData("ChatGPT", spent=10.0, limit=100.0, currency="")
    cg._rows = [row("Codex Tasks", 10, H, 5 * H)]
    st = build_panel_state(snap(claude_error={"kind": "missing", "message": ""}, providers=[cg]))
    names = [c["name"] for c in st["cards"]]
    assert names == ["Claude", "ChatGPT"]
    assert st["cards"][0]["state"] == "error"
    assert st["cards"][0]["error"]["action"]["id"] == "detect"
    assert st["state"] == "ok"


def test_auth_error_maps_to_reconnect():
    st = build_panel_state(snap(providers=[ProviderData("Cursor", error="403 Client Error: Forbidden")]))
    card = st["cards"][0]
    assert card["state"] == "error"
    assert card["error"]["title"] == "Signed out"
    assert card["error"]["action"] == {"id": "detect", "provider": "cursor", "label": "Reconnect"}


def test_disabled_providers_are_hidden_everywhere():
    cg = ProviderData("ChatGPT", spent=1.0, limit=100.0, currency="")
    cg._rows = [row("Codex Tasks", 1)]
    s = snap(config={"disabled_providers": ["chatgpt", "claude"]}, providers=[cg],
             claude=UsageData(session=row("Current Session", 5)))
    st = build_panel_state(s)
    assert st["cards"] == []
    assert {p["id"] for p in st["connect"]} == {"cursor", "copilot"}
    settings = build_settings_state(s.config, snap=s)
    assert {a["id"]: a["status"] for a in settings["accounts"]}["chatgpt"] == "off"


def test_copilot_card_summary_and_meter():
    cp = ProviderData("Copilot", spent=187.0, limit=300.0, currency="",
                      resets_at=NOW + 8 * D, window_secs=30 * D)
    st = build_panel_state(snap(providers=[cp]))
    card = st["cards"][0]
    assert card["summary"] == "187 / 300"
    assert card["meters"][0]["pct"] == 62
    assert card["meters"][0]["pace_pct"] is not None


def test_empty_and_loading_states():
    assert build_panel_state(snap())["state"] == "empty"
    assert build_panel_state(snap(fetching=True))["state"] == "loading"
    st = build_panel_state(snap(detecting={"chatgpt"}))
    assert st["state"] == "loading"
    assert st["cards"][0]["state"] == "loading"


def test_extras_include_claude_code_and_api_spend():
    oa = ProviderData("OpenAI", spent=12.4, limit=50.0)
    mm = ProviderData("MiniMax", balance=88.0, currency="CNY")
    st = build_panel_state(snap(providers=[oa, mm], cc_stats={
        "today_messages": 1500, "today_sessions": 3, "week_messages": 9000,
        "week_sessions": 20, "week_tool_calls": 40}))
    extras = {x["id"]: x for x in st["extras"]}
    assert extras["claude_code"]["rows"][0]["value"] == "1.5k messages · 3 sessions"
    assert extras["openai"]["rows"][0]["value"] == "$12.40 of $50.00"
    assert extras["openai"]["meter"]["pct"] == 25
    assert extras["minimax"]["rows"][0]["value"] == "¥88.00"


def test_stale_footer():
    st = build_panel_state(snap(updated_at=NOW - 3600))
    assert st["footer"]["stale"] is True
    assert build_panel_state(snap(updated_at=NOW - 60))["footer"]["stale"] is False


def test_share_summary_text():
    st = build_panel_state(demo_snapshot(now=NOW))
    assert st["share"]["text"].startswith("Claude 78% · ChatGPT 22% · Cursor 48% · Copilot 62%")
    assert st["share"]["url"].endswith("/AIQuotaBar")


# ── status bar ───────────────────────────────────────────────────────────────

def test_bar_segments_auto_picks_first_two_in_order():
    s = demo_snapshot(now=NOW)
    assert [x["name"] for x in bar_segments(s)] == ["Claude", "ChatGPT"]
    # Claude uses the session row, ChatGPT its worst row
    assert [x["pct"] for x in bar_segments(s)] == [78, 64]


def test_bar_segments_respects_user_choice():
    s = demo_snapshot(now=NOW)
    s.config["bar_providers"] = ["Copilot", "Cursor"]
    assert [x["name"] for x in bar_segments(s)] == ["Copilot", "Cursor"]


def test_weekly_maxed_marker():
    data = UsageData(session=row("Current Session", 10), weekly_all=row("All Models", 97))
    seg = bar_segments(snap(claude=data))[0]
    assert seg["weekly_maxed"] is True


def test_menu_lines_fallback():
    lines = menu_lines(build_panel_state(demo_snapshot(now=NOW)))
    text = "\n".join(line for line in lines if line)
    assert "CLAUDE" in text and "Current session  78%" in text and "187 / 300" in text


# ── settings ─────────────────────────────────────────────────────────────────

def test_apply_setting_allow_list():
    cfg = {}
    assert apply_setting(cfg, "refresh_interval", 60) and cfg["refresh_interval"] == 60
    assert not apply_setting(cfg, "refresh_interval", 7)
    assert not apply_setting(cfg, "cookie_str", "evil")
    assert "cookie_str" not in cfg
    assert not apply_setting(cfg, "bar_show_reset", "yes")
    assert apply_setting(cfg, "bar_show_reset", True)
    assert apply_setting(cfg, "notifications.claude_pacing", False)
    assert cfg["notifications"]["claude_pacing"] is False
    assert not apply_setting(cfg, "notifications.bogus", False)


def test_apply_setting_bar_providers_normalises_order():
    cfg = {}
    assert apply_setting(cfg, "bar_providers", ["Copilot", "Claude"])
    assert cfg["bar_providers"] == ["Claude", "Copilot"]
    assert not apply_setting(cfg, "bar_providers", ["Gemini"])
    assert apply_setting(cfg, "bar_providers", [])
    assert "bar_providers" not in cfg


def test_apply_setting_thresholds_must_stay_ordered():
    cfg = {}
    assert apply_setting(cfg, "warn_threshold", 70)
    assert (cfg["warn_threshold"], cfg["crit_threshold"]) == (70, 95)
    assert not apply_setting(cfg, "warn_threshold", 96)       # above crit
    assert not apply_setting(cfg, "crit_threshold", 60)       # below warn
    assert not apply_setting(cfg, "warn_threshold", True)     # bools aren't ints here
    assert apply_setting(cfg, "crit_threshold", 90)


def test_settings_state_masks_api_keys():
    st = build_settings_state({"openai_key": "sk-abcdefghijklmnop1234"})
    key = next(k for k in st["api_keys"] if k["key"] == "openai_key")
    assert key["set"] and key["masked"] == "sk-…1234"
    assert "abcdefghijklmnop" not in str(st)


# ── history ──────────────────────────────────────────────────────────────────

def test_history_state_orders_series_and_drops_empty():
    rows = [("2026-09-20", "copilot", 10, 5, 0), ("2026-09-20", "claude", 50, 20, 0),
            ("2026-09-20", "cursor_api", 0, 0, 0)]
    st = build_history_state(rows, {"claude": [(NOW - 60, 10), (NOW, 20)]}, NOW)
    assert [s["key"] for s in st["series"]] == ["claude", "copilot"]
    assert st["series"][0]["label"] == "Claude · session"
    assert st["trends"][0]["key"] == "claude"


def test_demo_history_rows_shape():
    rows = demo_history_rows(time.time(), days=10)
    assert rows and all(len(r) == 5 and 0 <= r[2] <= 100 for r in rows)
