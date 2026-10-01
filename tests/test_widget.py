"""Widget cache writer: what it writes, and when it launches the host app."""

import json
import os

import pytest

from aiquotabar import widget
from aiquotabar.providers import LimitRow, UsageData


@pytest.fixture
def launched(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(widget.subprocess, "Popen", lambda cmd, **k: calls.append(cmd))
    monkeypatch.setattr(widget, "WIDGET_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(widget, "WIDGET_CACHE_FILE", str(tmp_path / "usage.json"))
    return calls


def write(config):
    data = UsageData(session=LimitRow("Current Session", 42, "resets in 1h"))
    widget._write_widget_cache(data, [], None, config)


def test_writes_cache_and_reloads_installed_widget(launched, monkeypatch, tmp_path):
    monkeypatch.setattr(widget, "_is_widget_installed", lambda: True)
    write({"cookie_str": "x"})
    with open(tmp_path / "usage.json") as f:
        assert json.load(f)["claude"]["session"]["pct"] == 42
    assert launched == [["open", "-g", "-a", "AIQuotaBarHost", "--args", "--reload-widget"]]


def test_does_not_launch_a_host_that_is_not_installed(launched, monkeypatch, tmp_path):
    monkeypatch.setattr(widget, "_is_widget_installed", lambda: False)
    write({})
    assert os.path.exists(tmp_path / "usage.json") and launched == []


def test_turned_off_widget_is_left_alone(launched, monkeypatch, tmp_path):
    monkeypatch.setattr(widget, "_is_widget_installed", lambda: True)
    write({"widget_enabled": False})
    assert not os.path.exists(tmp_path / "usage.json") and launched == []


def test_quit_widget_host_does_not_launch_it(launched):
    widget._quit_widget_host()
    assert launched[0][0] == "osascript" and "is running" in launched[0][2]


def test_codex_chatgpt_and_extra_accounts(launched, monkeypatch, tmp_path):
    from aiquotabar.providers import ProviderData
    monkeypatch.setattr(widget, "_is_widget_installed", lambda: False)
    pd = ProviderData("ChatGPT", spent=10.0, limit=100.0, source="codex")
    pd._rows = [LimitRow("Codex Tasks", 10, "")]
    data = UsageData(session=LimitRow("Current Session", 42, "resets in 1h"))
    widget._write_widget_cache(data, [pd], None, {
        "cookie_str": "x", "bar_providers": ["Claude", "claude@0a1b2c3d", "ChatGPT"]})
    with open(tmp_path / "usage.json") as f:
        cache = json.load(f)
    assert cache["active_providers"] == ["claude", "chatgpt"]     # Codex counts as signed in
    assert cache["bar_providers"] == ["claude", "chatgpt"]        # extra accounts stay out
    assert cache["chatgpt"]["rows"][0]["pct"] == 10
