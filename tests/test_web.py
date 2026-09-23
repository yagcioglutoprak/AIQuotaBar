"""Render the real web UI in headless Chromium and check behaviour.

Skipped when Playwright isn't installed (it's a dev-only dependency):
    pip install playwright pytest
"""

import os
import time

import pytest

pw = pytest.importorskip("playwright.sync_api")

from aiquotabar.demo import demo_history_rows, demo_snapshot  # noqa: E402
from aiquotabar.providers import ProviderData  # noqa: E402
from aiquotabar.viewmodel import (  # noqa: E402
    WEB_DIR, boot_assets, build_history_state, build_panel_state, build_settings_state,
)

INDEX = "file://" + os.path.join(WEB_DIR, "index.html")


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:     # no browser available in this environment
            pytest.skip(f"chromium unavailable: {e}")
        yield b
        b.close()


def open_view(browser, view, state, theme="dark"):
    page = browser.new_page(viewport={"width": 800, "height": 900}, color_scheme=theme)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.goto(f"{INDEX}?view={view}")
    page.evaluate("([a, s]) => { AIQ.boot(a); AIQ.render(s); }", [boot_assets(), state])
    return page, errors


def outbox(page):
    return page.evaluate("window.AIQ_OUTBOX")


def test_every_view_renders_without_errors(browser):
    now = time.time()
    snap = demo_snapshot(now=now)
    views = {
        "panel": build_panel_state(snap),
        "settings": build_settings_state(snap.config, snap=snap),
        "history": build_history_state(demo_history_rows(now), snap.trends, now),
        "welcome": {**build_settings_state(snap.config, snap=snap), "view": "welcome"},
    }
    for variant in ("states", "empty"):
        views[f"panel-{variant}"] = build_panel_state(demo_snapshot(variant, now))
    for name, state in views.items():
        for theme in ("light", "dark"):
            page, errors = open_view(browser, name.split("-")[0], state, theme)
            assert page.locator("#app").inner_text().strip(), name
            assert errors == [], (name, theme, errors)
            page.close()


def test_panel_reports_ready_and_size(browser):
    page, _ = open_view(browser, "panel", build_panel_state(demo_snapshot()))
    msgs = outbox(page)
    assert msgs[0] == {"action": "ready", "view": "panel"}
    assert any(m["action"] == "resize" and m["height"] > 300 for m in msgs)


def test_panel_buttons_post_actions(browser):
    page, _ = open_view(browser, "panel", build_panel_state(demo_snapshot()))
    page.get_by_title("Refresh  ⌘R").click()
    page.get_by_title("Settings  ⌘,").click()
    page.get_by_role("button", name="History").click()
    actions = [m["action"] for m in outbox(page)]
    assert {"refresh", "open_settings", "open_history"} <= set(actions)


def test_share_menu_produces_png(browser):
    page, _ = open_view(browser, "panel", build_panel_state(demo_snapshot()))
    page.get_by_title("Share").click()
    page.get_by_role("menuitem", name="Copy image").click()
    page.wait_for_function("window.AIQ_OUTBOX.some(m => m.action === 'share_image')")
    msg = next(m for m in outbox(page) if m["action"] == "share_image")
    assert msg["mode"] == "copy" and msg["png"].startswith("data:image/png;base64,")
    assert len(msg["png"]) > 20_000


def test_untrusted_text_is_never_parsed_as_html(browser):
    evil = '<img src=x onerror="window.pwned=1">'
    state = build_panel_state(demo_snapshot())
    state["cards"].append({"id": "cursor", "name": "Cursor", "color": "#3A7BD5", "icon": "cursor.png",
                           "mask": True, "state": "error", "meters": [],
                           "error": {"title": evil, "detail": evil,
                                     "action": {"id": "refresh", "label": evil}}})
    page, errors = open_view(browser, "panel", state)
    page.wait_for_timeout(100)
    assert page.evaluate("window.pwned") is None
    assert page.locator("img").count() == 0
    assert evil in page.locator("#app").inner_text()


def test_settings_controls_post_validated_actions(browser):
    snap = demo_snapshot()
    page, _ = open_view(browser, "settings", build_settings_state(snap.config, snap=snap), "light")
    page.get_by_role("radio", name="1 min").click()
    page.get_by_role("button", name="Menu bar").click()
    page.get_by_role("switch", name="Time until reset").click()
    page.get_by_role("button", name="Accounts").click()
    page.get_by_role("button", name="Turn off").first.click()
    sent = [(m["action"], m.get("key"), m.get("value"), m.get("provider")) for m in outbox(page)]
    assert ("set", "refresh_interval", 60, None) in sent
    assert ("set", "bar_show_reset", True, None) in sent
    assert ("disable", None, None, "claude") in sent


def test_history_range_switch(browser):
    now = time.time()
    state = build_history_state(demo_history_rows(now), {}, now)
    page, errors = open_view(browser, "history", state)
    for label in ("7 days", "90 days", "30 days"):
        page.get_by_role("button", name=label).click()
        assert page.locator(".multiples svg").count() >= 3
    assert errors == []


def test_api_provider_extra_row(browser):
    snap = demo_snapshot()
    snap.providers.append(ProviderData("OpenAI", spent=12.4, limit=50.0))
    page, errors = open_view(browser, "panel", build_panel_state(snap))
    assert "OpenAI API" in page.locator(".extras").inner_text()
    assert errors == []
