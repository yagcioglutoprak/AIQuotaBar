#!/usr/bin/env python3
"""Render README / marketing screenshots of the real UI with demo data.

The app's panel, settings, history and welcome screens are the HTML/CSS/JS in
aiquotabar/web/, fed by aiquotabar/viewmodel.py. This script feeds the same
view model with deterministic demo data and captures each view in headless
Chromium, placed on a macOS-style backdrop. On a Mac the app renders these
views in WebKit with SF Pro; here we fall back to Inter if you pass --font.

    pip install playwright && python3 tools/screenshots.py --out assets/screens

Only the menu bar strip above the panel is a mock; everything else is the
app's own UI code.
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from aiquotabar.demo import demo_history_rows, demo_snapshot  # noqa: E402
from aiquotabar.viewmodel import (  # noqa: E402
    WEB_DIR, boot_assets, build_history_state, build_panel_state, build_settings_state,
)

SHOWCASE_CSS = """
html, body { background: transparent !important; }
.scene { position: relative; width: %(w)dpx; height: %(h)dpx; overflow: hidden; font-family: var(--font);
         background: %(wall)s; }
.scene .mb { position: absolute; left: 0; right: 0; top: 0; height: 30px; display: flex; align-items: center;
             gap: 16px; padding: 0 14px; font-size: 13px; font-weight: 500; color: %(mbfg)s;
             background: %(mbbg)s; -webkit-backdrop-filter: blur(30px); backdrop-filter: blur(30px); }
.scene .mb .sp { flex: 1; }
.scene .mb .item { display: inline-flex; align-items: center; gap: 5px; padding: 3px 8px; border-radius: 5px; }
.scene .mb .item.hl { background: %(mbhl)s; }
.scene .mb .picon { width: 15px; height: 15px; }
.scene .mb .picon.mask { background-color: %(mbfg)s !important; }
.scene .mb .w { color: %(mbwarn)s; }
.scene .anchor { position: absolute; top: 36px; }
.scene .win { position: absolute; border-radius: 12px; overflow: hidden; box-shadow: var(--shadow); }
.scene .titlebar { height: 30px; display: flex; align-items: center; gap: 8px; padding: 0 12px;
                   background: var(--sidebar-bg); border-bottom: 0.5px solid var(--hairline); }
.scene .titlebar i { width: 12px; height: 12px; border-radius: 50%%; display: inline-block; }
.scene .titlebar .t { flex: 1; text-align: center; font-size: 13px; font-weight: 600; color: var(--text-2); margin-right: 52px; }
"""

WALLS = {
    "dark": "radial-gradient(120%% 90%% at 80%% 0%%, #3b2d5c 0%%, transparent 60%%),"
            "radial-gradient(100%% 80%% at 0%% 100%%, #1d4a5a 0%%, transparent 60%%),"
            "linear-gradient(160deg, #161428 0%%, #0e1020 100%%)",
    "light": "radial-gradient(120%% 90%% at 80%% 0%%, #f6c9a8 0%%, transparent 60%%),"
             "radial-gradient(100%% 80%% at 0%% 100%%, #a9d4ec 0%%, transparent 60%%),"
             "linear-gradient(160deg, #eadff5 0%%, #d6e6f5 100%%)",
}


def font_css(font_dir: str | None) -> str:
    if not font_dir:
        return ""
    out = []
    for fn, rng in (("latin.woff2", "U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, "
                                    "U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, "
                                    "U+2212, U+2215, U+FEFF, U+FFFD"),
                    ("latin-ext.woff2", "U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7, U+02DD-02FF, "
                                        "U+0304, U+0308, U+0329, U+1D00-1DBF, U+1E00-1E9F, U+1EF2-1EFF, U+2020, "
                                        "U+20A0-20AB, U+20AD-20C0, U+2113, U+2C60-2C7F, U+A720-A7FF")):
        path = os.path.join(font_dir, fn)
        if os.path.exists(path):
            data = base64.b64encode(open(path, "rb").read()).decode()
            out.append("@font-face{font-family:'Inter';font-style:normal;font-weight:100 900;"
                       f"src:url(data:font/woff2;base64,{data}) format('woff2');unicode-range:{rng};}}")
    return "\n".join(out) + "\n:root{--font:'Inter',sans-serif;font-feature-settings:'cv11','ss01';}"


def menubar_html(theme: str) -> str:
    # A mock of the native status item: icon + % per provider, severity-coloured.
    return (
        '<div class="mb"><span style="font-weight:700"></span><span>Finder</span><span>File</span>'
        '<span>Edit</span><span>View</span><span class="sp"></span>'
        '<span class="item hl"><span class="picon" data-icon="claude"></span>78%'
        '<span style="width:8px"></span><span class="picon mask" data-icon="chatgpt"></span>64%'
        '<span style="width:8px"></span><span style="opacity:.8">◆ 1.2k</span></span>'
        '<span>Wed 23 Sep  14:32</span></div>'
    )


def run(out_dir: str, font_dir: str | None, only: list[str] | None) -> list[str]:
    from playwright.sync_api import sync_playwright

    os.makedirs(out_dir, exist_ok=True)
    now = time.time()
    assets = boot_assets()
    index = "file://" + os.path.join(WEB_DIR, "index.html")
    fonts = font_css(font_dir)
    written = []

    snap = demo_snapshot("default", now)
    panel = build_panel_state(snap)
    panel_states = build_panel_state(demo_snapshot("states", now))
    panel_empty = build_panel_state(demo_snapshot("empty", now))
    settings = build_settings_state(snap.config, snap=snap, launch_at_login=True,
                                    widget_installed=True, version="2.0.0")
    history = build_history_state(demo_history_rows(now), {k: v for k, v in snap.trends.items()}, now)
    welcome = build_settings_state(snap.config, snap=demo_snapshot("states", now))
    welcome["view"] = "welcome"

    shots = [
        # name, view, theme, state, scene size, placement
        ("panel-dark", "panel", "dark", panel, (760, 920), "panel"),
        ("panel-light", "panel", "light", panel, (760, 920), "panel"),
        ("panel-states-dark", "panel", "dark", panel_states, (760, 760), "panel"),
        ("panel-empty-light", "panel", "light", panel_empty, (760, 520), "panel"),
        ("settings-menubar-dark", "settings", "dark", {**settings, "tab": "menubar"}, (900, 640), "window"),
        ("settings-accounts-light", "settings", "light", {**settings, "tab": "accounts"}, (900, 640), "window"),
        ("settings-notifications-dark", "settings", "dark", {**settings, "tab": "notifications"}, (900, 560), "window"),
        ("history-dark", "history", "dark", history, (900, 1240), "window"),
        ("welcome-light", "welcome", "light", welcome, (720, 760), "window"),
    ]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, view, theme, state, (w, hgt), placement in shots:
            if only and name not in only:
                continue
            page = browser.new_page(viewport={"width": w, "height": hgt}, device_scale_factor=2,
                                    color_scheme=theme)
            page.goto(f"{index}?view={view}&theme={theme}")
            css = SHOWCASE_CSS % {
                "w": w, "h": hgt, "wall": WALLS[theme] % (),
                "mbfg": "#fff" if theme == "dark" else "#1d1d1f",
                "mbbg": "rgba(20,20,30,0.45)" if theme == "dark" else "rgba(255,255,255,0.45)",
                "mbhl": "rgba(255,255,255,0.22)" if theme == "dark" else "rgba(0,0,0,0.10)",
                "mbwarn": "#FFB84D",
            }
            page.add_style_tag(content=fonts + css)
            page.evaluate("([a, s]) => { AIQ.boot(a); AIQ.render(s); }", [assets, state])
            page.evaluate(
                """([placement, mb, w, h, view, icons]) => {
                  const app = document.getElementById('app');
                  const scene = document.createElement('div');
                  scene.className = 'scene';
                  document.body.prepend(scene);
                  if (placement === 'panel') {
                    scene.insertAdjacentHTML('beforeend', mb);
                    const a = document.createElement('div');
                    a.className = 'anchor';
                    a.appendChild(app);
                    scene.appendChild(a);
                    a.style.left = (w - 360) / 2 + 'px';
                  } else {
                    const win = document.createElement('div');
                    win.className = 'win';
                    const W = view === 'welcome' ? 560 : 780;
                    const H = h - 80;
                    win.style.width = W + 'px'; win.style.height = H + 'px';
                    win.style.left = (w - W) / 2 + 'px'; win.style.top = '40px';
                    const titles = {settings: 'AIQuotaBar Settings', history: 'Usage History', welcome: ''};
                    win.insertAdjacentHTML('beforeend',
                      '<div class="titlebar"><i style="background:#FF5F57"></i><i style="background:#FEBC2E"></i>' +
                      '<i style="background:#28C840"></i><span class="t"></span></div>');
                    win.querySelector('.t').textContent = titles[view] || '';
                    const body = document.createElement('div');
                    body.style.cssText = 'height:' + (H - 30) + 'px;overflow:hidden;position:relative;background:var(--window-bg)';
                    app.style.cssText = 'height:100%';
                    body.appendChild(app);
                    win.appendChild(body);
                    scene.appendChild(win);
                    document.querySelectorAll('.settings').forEach(e => e.style.height = '100%');
                  }
                  scene.querySelectorAll('[data-icon]').forEach(el => {
                    const src = icons[el.getAttribute('data-icon')];
                    if (el.classList.contains('mask')) {
                      el.style.webkitMaskImage = 'url(' + src + ')'; el.style.maskImage = 'url(' + src + ')';
                    } else { el.style.backgroundImage = 'url(' + src + ')'; }
                  });
                }""",
                [placement, menubar_html(theme), w, hgt, view, assets["icons"]],
            )
            page.wait_for_timeout(350)
            path = os.path.join(out_dir, f"{name}.png")
            page.locator(".scene").screenshot(path=path)
            written.append(path)
            page.close()

        # The share card is drawn on a <canvas>; save exactly what "Copy image" produces.
        if not only or "share-card" in only:
            page = browser.new_page(viewport={"width": 400, "height": 300})
            page.goto(f"{index}?view=panel")
            page.add_style_tag(content=fonts)
            page.evaluate("([a, s]) => { AIQ.boot(a); AIQ.render(s); }", [assets, panel])
            page.wait_for_timeout(200)
            data = page.evaluate("s => AIQ.shareCard(s).then(c => c.toDataURL('image/png'))", panel["share"])
            path = os.path.join(out_dir, "share-card.png")
            with open(path, "wb") as f:
                f.write(base64.b64decode(data.split(",", 1)[1]))
            written.append(path)
            page.close()
        browser.close()
    return written


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.path.join(ROOT, "assets", "screens"))
    ap.add_argument("--font", help="directory with Inter latin.woff2 / latin-ext.woff2 (optional)")
    ap.add_argument("--only", nargs="*", help="render only these shot names")
    args = ap.parse_args()
    for path in run(args.out, args.font, args.only):
        print(path)


if __name__ == "__main__":
    main()
