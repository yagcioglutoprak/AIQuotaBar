"""`--json`: prints the widget snapshot for scripts, without starting the app."""

import json
import sys

import pytest

from aiquotabar import __main__ as entry
from aiquotabar import widget
from aiquotabar.providers import LimitRow, UsageData

SAMPLE = {
    "version": 1,
    "updated_at": "2026-11-03T10:00:00+00:00",
    "claude": {
        "session": {"label": "Current Session", "pct": 42,
                    "reset_str": "resets in 1h 5m", "resets_at": 1793700000.0},
        "weekly_all": {"label": "All Models", "pct": 17,
                       "reset_str": "resets Thu 00:00", "resets_at": 1794000000.0},
        "weekly_sonnet": None,
        "overages_enabled": None,
    },
    "chatgpt": {"rows": [{"label": "Codex · 5h", "pct": 8, "reset_str": "resets in 3h"}],
                "error": None},
    "cursor": {"rows": None, "error": None},
    "copilot": {"spent": 120, "limit": 300, "pct": 40, "error": None},
    "claude_code": {"today_messages": 3, "week_messages": 10},
    "active_providers": ["claude", "chatgpt", "copilot"],
    "bar_providers": None,
}


@pytest.fixture
def cache(monkeypatch, tmp_path):
    path = tmp_path / "usage.json"
    monkeypatch.setattr(widget, "WIDGET_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(widget, "WIDGET_CACHE_FILE", str(path))

    def no_lock():
        raise AssertionError("--json must not take the single-instance lock")
    monkeypatch.setattr(entry, "_single_instance", no_lock)
    return path


def run(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["claude_bar.py", *args])
    with pytest.raises(SystemExit) as exc:
        entry.main()
    return exc.value.code


@pytest.mark.parametrize("flag", ["--json", "-j"])
def test_prints_every_connected_provider(cache, monkeypatch, capsys, flag):
    cache.write_text(json.dumps(SAMPLE))
    assert run(monkeypatch, flag) == 0
    out = json.loads(capsys.readouterr().out)

    assert out["updated_at"] == SAMPLE["updated_at"]
    assert set(out["providers"]) == {"claude", "chatgpt", "copilot"}  # cursor has no data
    claude = out["providers"]["claude"]["limits"]
    assert claude == [
        {"label": "Current Session", "pct": 42, "reset": "resets in 1h 5m",
         "resets_at": 1793700000.0},
        {"label": "All Models", "pct": 17, "reset": "resets Thu 00:00",
         "resets_at": 1794000000.0},
    ]
    # Older snapshots have no resets_at: still valid, just null.
    assert out["providers"]["chatgpt"]["limits"][0]["resets_at"] is None
    assert out["providers"]["copilot"]["limits"][0]["pct"] == 40


def test_reports_provider_errors(cache, monkeypatch, capsys):
    cache.write_text(json.dumps({**SAMPLE, "cursor": {"rows": None, "error": "Session expired"}}))
    assert run(monkeypatch, "--json") == 0
    assert json.loads(capsys.readouterr().out)["providers"]["cursor"] == {
        "limits": [], "error": "Session expired"}


def test_no_data_yet_fails_on_stderr(cache, monkeypatch, capsys):
    assert run(monkeypatch, "--json") == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "No usage data yet" in captured.err


def test_corrupt_snapshot_fails(cache, monkeypatch, capsys):
    cache.write_text("{not json")
    assert run(monkeypatch, "--json") == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "Could not read" in captured.err


def test_round_trips_what_the_app_writes(cache, monkeypatch, capsys):
    monkeypatch.setattr(widget, "_is_widget_installed", lambda: False)
    row = LimitRow("Current Session", 64, "resets in 2h", resets_at=1793700000.0, window_secs=18000)
    widget._write_widget_cache(UsageData(session=row), [], None, {"cookie_str": "x"})

    assert run(monkeypatch, "-j") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["updated_at"]
    assert out["providers"]["claude"]["limits"] == [
        {"label": "Current Session", "pct": 64, "reset": "resets in 2h",
         "resets_at": 1793700000.0}]
