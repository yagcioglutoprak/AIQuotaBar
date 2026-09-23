"""Drive the native shell (ui.py + webview.py) through the fake Cocoa modules.

These don't prove anything about AppKit itself, but they execute every Python
path — startup, fetch, bridge actions, windows, history, alerts — so typos,
wrong signatures and logic slips surface here instead of on a user's Mac.
"""

import base64
import json
import os
import time

import pytest

from tests import fake_macos
from curl_cffi.requests.exceptions import HTTPError

PNG = "data:image/png;base64," + base64.b64encode(
    b"\x89PNG\r\n\x1a\n" + b"\x00" * 32).decode()


def js_calls(prefix):
    """States passed to evaluateJavaScript that start with `prefix`."""
    out = []
    for _obj, method, args in fake_macos.CALLS:
        if method == "evaluateJavaScript_completionHandler_" and args[0].startswith(prefix):
            out.append(json.loads(args[0][len(prefix):-2]))
    return out


def send(host, **msg):
    host._receive(json.dumps(msg))


class SyncThread:
    """Runs the target inline so fetch/detect cycles are deterministic."""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self.target, self.args, self.kwargs = target, args, kwargs or {}

    def start(self):
        self.target(*self.args, **self.kwargs)


@pytest.fixture
def ui(monkeypatch):
    import threading
    import types
    from aiquotabar import ui as mod
    monkeypatch.setattr(mod, "threading", types.SimpleNamespace(Thread=SyncThread, Lock=threading.Lock))
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)

    # Never touch the network or real browsers from tests; individual tests
    # override these with canned responses.
    def offline(*a, **k):
        raise ConnectionError("offline in tests")
    monkeypatch.setattr(mod, "fetch_raw", offline)
    monkeypatch.setattr(mod, "_auto_detect_cookies", lambda: None)
    for pid in list(mod.DETECTORS):
        monkeypatch.setitem(mod.DETECTORS, pid, lambda: None)
    monkeypatch.setattr(mod, "PROVIDER_REGISTRY", {
        k: (name, lambda key, name=name: offline()) for k, (name, _fn) in mod.PROVIDER_REGISTRY.items()})
    opened = []
    monkeypatch.setattr(mod, "_open", opened.append)
    monkeypatch.setattr(mod, "_check_and_apply_update", lambda: False)
    monkeypatch.setattr(mod, "_write_widget_cache", lambda *a, **k: None)
    monkeypatch.setattr(mod.subprocess, "Popen", lambda *a, **k: opened.append(a[0]))
    mod.opened = opened
    fake_macos.CALLS.clear()
    fake_macos.NOTIFICATIONS.clear()
    return mod


def start(app):
    app._deferred_startup(fake_macos.Timer(None, 0))
    assert app._web_ok and app._panel is not None
    send(app._panel.host, action="ready", view="panel")
    return app


def test_demo_app_end_to_end(ui):
    app = ui.ClaudeBar(demo=True)
    app._fetch_and_update()
    start(app)
    app._flush_ui(None)

    # The status item got an attributed title and the panel a full state.
    assert any(m == "setAttributedTitle_" for _, m, _ in fake_macos.CALLS)
    states = js_calls("AIQ.render(")
    panel = [s for s in states if s["view"] == "panel"][-1]
    assert panel["state"] == "ok" and panel["hero"]["name"] == "Claude"
    assert any(c.startswith("AIQ.boot(") for _, m, a in fake_macos.CALLS
               if m == "evaluateJavaScript_completionHandler_" for c in [a[0]])

    # Demo mode opens the welcome window on launch.
    assert "welcome" in app._windows

    # Panel: resize, show, toggle.
    send(app._panel.host, action="resize", height=720)
    app._panel.toggle()
    assert app._panel.visible
    send(app._panel.host, action="close")
    assert not app._panel.visible
    app._panel.toggle()                       # the same click that dismissed it
    assert not app._panel.visible

    # Settings window opens on the requested tab and renders.
    send(app._panel.host, action="open_settings", tab="accounts")
    win = app._windows["settings"]
    send(win.host, action="ready", view="settings")
    settings = [s for s in js_calls("AIQ.render(") if s["view"] == "settings"]
    assert settings[0]["tab"] == "accounts"

    # History window renders demo history.
    send(app._panel.host, action="open_history")
    hwin = app._windows["history"]
    send(hwin.host, action="ready", view="history")
    hist = [s for s in js_calls("AIQ.render(") if s["view"] == "history"][-1]
    assert hist["series"] and hist["trends"]

    # Closing a window tears down the bridge and forgets it.
    hwin._closed()
    assert "history" not in app._windows and hwin.host.bridge.py_callback is None


def test_settings_actions_are_validated(ui):
    app = start(ui.ClaudeBar(demo=True))
    host = app._panel.host
    send(host, action="set", key="refresh_interval", value=60)
    assert app.config["refresh_interval"] == 60 and app._timer.interval == 60
    send(host, action="set", key="refresh_interval", value=1)          # rejected
    assert app.config["refresh_interval"] == 60
    send(host, action="set", key="cookie_str", value="stolen")         # not settable
    assert app.config["cookie_str"] == "demo"
    send(host, action="set", key="bar_providers", value=["Cursor"])
    assert app.config["bar_providers"] == ["Cursor"]
    send(host, action="set_api_key", key="cookie_str", value="x")      # only API keys
    assert app.config["cookie_str"] == "demo"
    send(host, action="set_api_key", key="openai_key", value="  sk-test  ")
    assert app.config["openai_key"] == "sk-test"
    send(host, action="bogus_action")
    send(host, action=None)
    host._receive("not json")


def test_open_url_allow_list(ui):
    app = start(ui.ClaudeBar(demo=True))
    send(app._panel.host, action="open_url", url="https://claude.ai/settings/usage")
    send(app._panel.host, action="open_url", url="https://evil.example/claude.ai")
    send(app._panel.host, action="open_url", url="file:///etc/passwd")
    send(app._panel.host, action="open_url", url="javascript:alert(1)")
    assert ui.opened == ["https://claude.ai/settings/usage"]


def test_share_image_copy_save_and_x(ui):
    app = start(ui.ClaudeBar(demo=True))
    app._fetch_and_update()
    send(app._panel.host, action="share_image", mode="copy", png=PNG)
    assert any(m == "setData_forType_" for _, m, _ in fake_macos.CALLS)
    send(app._panel.host, action="share_image", mode="save", png=PNG)
    saved = [f for f in os.listdir(os.path.expanduser("~/Downloads")) if f.startswith("AIQuotaBar")]
    assert saved
    send(app._panel.host, action="share_image", mode="x", png=PNG)
    assert any(isinstance(u, str) and u.startswith("https://x.com/intent/post?text=Claude%2078%25")
               for u in ui.opened)
    send(app._panel.host, action="share_image", mode="copy", png="data:text/html,<b>")   # ignored


def test_real_fetch_path_with_stubbed_network(ui, monkeypatch):
    reset = "2030-01-01T10:00:00+00:00"
    raw = {"usage": {"five_hour": {"utilization": 85, "resets_at": reset},
                     "seven_day": {"utilization": 30, "resets_at": reset}},
           "org_id": "org"}
    monkeypatch.setattr(ui, "fetch_raw", lambda cookie: raw)
    detected = []

    def detect(pid, value):
        def fn():
            detected.append(pid)
            return value
        return fn
    monkeypatch.setitem(ui.DETECTORS, "claude", detect("claude", "sessionKey=abc; lastActiveOrg=org"))
    monkeypatch.setattr(ui, "_auto_detect_cookies", detect("claude", "sessionKey=abc; lastActiveOrg=org"))
    monkeypatch.setitem(ui.DETECTORS, "chatgpt", detect("chatgpt", "__Secure-next-auth.session-token=t"))
    monkeypatch.setitem(ui.DETECTORS, "cursor", detect("cursor", None))
    monkeypatch.setitem(ui.DETECTORS, "copilot", detect("copilot", None))

    from aiquotabar.providers import LimitRow, ProviderData

    def fake_chatgpt(cookie):
        pd = ProviderData("ChatGPT", spent=40.0, limit=100.0, currency="")
        pd._rows = [LimitRow("Codex Tasks", 40, "", time.time() + 3600, 18000)]
        return pd
    registry = dict(ui.PROVIDER_REGISTRY)
    registry["chatgpt_cookies"] = ("ChatGPT", fake_chatgpt)
    monkeypatch.setattr(ui, "PROVIDER_REGISTRY", registry)

    app = ui.ClaudeBar()               # the constructor runs the first fetch cycle
    assert app._last_data.session.pct == 85
    assert [p.name for p in app._provider_data] == ["ChatGPT"]
    assert set(detected) >= {"claude", "chatgpt", "cursor", "copilot"}
    assert "claude" in app._trends and "chatgpt_codex_tasks" in app._trends
    # 85% crosses the default 80% warning → one notification, not repeated.
    assert any("Claude Current Session is at 85%" in n[1] for n in fake_macos.NOTIFICATIONS)
    n = len(fake_macos.NOTIFICATIONS)
    app._fetch_and_update()
    assert len(fake_macos.NOTIFICATIONS) == n
    # Detection for missing providers is throttled, not repeated every cycle.
    assert detected.count("cursor") == 1

    # Config on disk has the detected cookies (and no demo leftovers).
    with open(os.path.expanduser("~/.claude_bar_config.json")) as f:
        saved = json.load(f)
    assert saved["cookie_str"].startswith("sessionKey=") and "chatgpt_cookies" in saved


def test_claude_auth_failure_recovers_with_fresh_cookie(ui, monkeypatch):
    class Resp:
        status_code = 403
    calls = {"n": 0}

    def fetch_raw(cookie):
        calls["n"] += 1
        if cookie == "old":
            err = HTTPError("403 Forbidden")
            err.response = Resp()
            raise err
        return {"usage": {"five_hour": {"utilization": 5, "resets_at": None}}}
    monkeypatch.setattr(ui, "fetch_raw", fetch_raw)
    monkeypatch.setattr(ui, "_auto_detect_cookies", lambda: "fresh")
    from aiquotabar.config import save_config
    save_config({"cookie_str": "old", "disabled_providers": ["chatgpt", "cursor", "copilot"]})
    app = ui.ClaudeBar()               # first failure
    assert app._claude_error["kind"] == "auth"
    app._fetch_and_update()           # second failure → re-detect → schedules a retry
    assert app.config["cookie_str"] == "fresh"
    app._fetch_claude()
    assert app._claude_error is None and app._last_data.session.pct == 5


def test_disable_and_enable_provider(ui):
    app = start(ui.ClaudeBar(demo=True))
    app._fetch_and_update()
    send(app._panel.host, action="disable", provider="chatgpt")
    assert "chatgpt" in app.config["disabled_providers"]
    assert all(p.name != "ChatGPT" for p in app._provider_data)
    send(app._panel.host, action="enable", provider="chatgpt")
    assert "chatgpt" not in app.config["disabled_providers"]
    send(app._panel.host, action="disable", provider="nonsense")


def test_launch_at_login_toggle_writes_and_removes_plist(ui):
    app = start(ui.ClaudeBar())
    send(app._panel.host, action="set_login", value=False)
    assert not os.path.exists(ui.LAUNCH_AGENT) and app.config["launch_at_login"] is False
    send(app._panel.host, action="set_login", value=True)
    assert os.path.exists(ui.LAUNCH_AGENT)
    # A user who turned it off is not re-added on the next launch.
    send(app._panel.host, action="set_login", value=False)
    ui.ClaudeBar()
    assert not os.path.exists(ui.LAUNCH_AGENT)


def test_web_page_that_never_loads_falls_back_to_menu(ui):
    app = ui.ClaudeBar(demo=True)
    app._deferred_startup(fake_macos.Timer(None, 0))
    app._panel.toggle()                       # page not ready yet → queued
    assert app._panel.show_when_ready and not app._panel.visible
    app._panel.host.created_at -= 10
    app._flush_ui(None)
    assert app._web_ok is False
    titles = [getattr(i, "title", None) for i in app.menu if i is not None]
    assert "CLAUDE" in titles


def test_single_instance_lock(tmp_path):
    from aiquotabar import __main__ as entry
    assert entry._single_instance() is True
    fd = entry._lock_fd
    entry._lock_fd = None
    try:
        assert entry._single_instance() is False
    finally:
        os.close(fd)


def test_ui_failures_never_escape_timer_callbacks(ui, monkeypatch):
    app = ui.ClaudeBar(demo=True)

    def boom(*a, **k):
        raise RuntimeError("AppKit exploded")
    monkeypatch.setattr(ui.webview, "Panel", boom)
    app._deferred_startup(fake_macos.Timer(None, 0))       # must not raise
    assert app._web_ok is False and app._panel is None
    monkeypatch.setattr(app, "_apply", boom)
    app._post_update()
    app._flush_ui(None)                                     # must not raise
