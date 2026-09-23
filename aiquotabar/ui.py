"""The menu bar app: status item, fetch loop, alerts, and the bridge between
the web UI (panel, settings, history, welcome) and the rest of the app.

Rendering lives in aiquotabar/web/ and state shaping in viewmodel.py; this
module owns side effects — network, config, notifications, windows.
"""

import atexit
import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from datetime import datetime, timezone, timedelta

import rumps

from aiquotabar import __version__, theme
from aiquotabar import webview
from aiquotabar.config import (
    log, LOG_FILE, load_config, save_config, notif_enabled, thresholds,
    REFRESH_INTERVALS, DEFAULT_REFRESH, PACING_ALERT_MINUTES, UPDATE_CHECK_INTERVAL,
)
from aiquotabar.providers import (
    UsageData, ProviderData, parse_usage, fetch_raw, fetch_claude_code_stats,
    PROVIDER_REGISTRY, COOKIE_PROVIDERS, CurlHTTPError,
    _auto_detect_cookies, _auto_detect_chatgpt_cookies,
    _auto_detect_copilot_cookies, _auto_detect_cursor_cookies,
    _BROWSER_COOKIE3_OK,
)
from aiquotabar.history import (
    _load_history, _save_history, _append_history, _calc_eta_minutes,
    _init_history_db, _record_sample, _rollup_daily_stats,
    _get_week_limit_hits, _get_today_stats, get_trend,
)
from aiquotabar.viewmodel import (
    Snapshot, COOKIE_KEYS, REPO_URL, SIGN_IN_DOMAINS,
    build_panel_state, build_settings_state, build_history_state,
    bar_segments, menu_lines, apply_setting, fmt_count, fmt_duration, history_key,
)
from aiquotabar.widget import _write_widget_cache, _is_widget_installed
from aiquotabar.update import _check_and_apply_update, _restart_app


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ICON_DIR = os.path.join(ROOT_DIR, "assets")
_ICON_SIZE = 14   # points — matches the menu bar font
_icon_cache: dict = {}

DETECTORS = {
    "claude":  _auto_detect_cookies,
    "chatgpt": _auto_detect_chatgpt_cookies,
    "copilot": _auto_detect_copilot_cookies,
    "cursor":  _auto_detect_cursor_cookies,
}
REDETECT_EVERY = 30 * 60      # retry browser detection for missing / signed-out providers
OPEN_URL_HOSTS = {"claude.ai", "chatgpt.com", "cursor.com", "github.com", "x.com", "twitter.com"}
LAUNCH_AGENT = os.path.expanduser("~/Library/LaunchAgents/com.claudebar.plist")


# ── status bar icons ─────────────────────────────────────────────────────────

def _bar_icon(filename: str, tint_hex: str | None = None):
    """Lazy-load and cache a 14×14 pt menu bar icon, optionally tinted."""
    key = (filename, tint_hex)
    if key in _icon_cache:
        return _icon_cache[key]
    img = None
    try:
        from AppKit import NSImage, NSColor
        raw = NSImage.alloc().initWithContentsOfFile_(os.path.join(_ICON_DIR, filename))
        if raw:
            img = raw.copy()
            img.setSize_((_ICON_SIZE, _ICON_SIZE))
            if tint_hex:
                r, g, b = (int(tint_hex[i:i + 2], 16) / 255 for i in (1, 3, 5))
                img.setTemplate_(True)
                if hasattr(img, "imageWithTintColor_"):
                    img = img.imageWithTintColor_(
                        NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 1.0))
    except Exception as e:
        log.debug("_bar_icon %s: %s", filename, e)
    _icon_cache[key] = img
    return img


def _icon_astr(img, base_attrs: dict):
    """Wrap an NSImage in an attributed string via NSTextAttachment."""
    from AppKit import NSTextAttachment, NSAttributedString
    from Foundation import NSMakeRect, NSMutableAttributedString
    att = NSTextAttachment.alloc().init()
    att.setImage_(img)
    att.setBounds_(NSMakeRect(0, -3, _ICON_SIZE, _ICON_SIZE))
    m = NSMutableAttributedString.alloc().initWithAttributedString_(
        NSAttributedString.attributedStringWithAttachment_(att))
    for k, v in base_attrs.items():
        m.addAttribute_value_range_(k, v, (0, m.length()))
    return m


# ── launch at login ──────────────────────────────────────────────────────────

def _script_path() -> str:
    # The LaunchAgent must start the package entry shim (claude_bar.py at the
    # repo root), which puts the root on sys.path so `aiquotabar` imports.
    shim = os.path.join(ROOT_DIR, "claude_bar.py")
    return shim if os.path.exists(shim) else os.path.abspath(__file__)


def _is_login_item() -> bool:
    return os.path.exists(LAUNCH_AGENT)


def _add_login_item():
    """Write the LaunchAgent. It takes effect at the next login; we don't
    `launchctl load` it now because RunAtLoad would start a second copy."""
    import plistlib
    os.makedirs(os.path.dirname(LAUNCH_AGENT), exist_ok=True)
    with open(LAUNCH_AGENT, "wb") as f:
        plistlib.dump({
            "Label": "com.claudebar",
            "ProgramArguments": [sys.executable, _script_path()],
            "RunAtLoad": True,
            # Respawn only after a crash; a clean Quit (exit 0) stays quit.
            "KeepAlive": {"SuccessfulExit": False},
            "StandardOutPath": LOG_FILE,
            "StandardErrorPath": LOG_FILE,
        }, f)


def _remove_login_item():
    """Delete the LaunchAgent. Not `launchctl unload`: that would kill this
    very process when launchd started it."""
    try:
        os.remove(LAUNCH_AGENT)
    except FileNotFoundError:
        pass


# ── small platform helpers ───────────────────────────────────────────────────

def _notify(title: str, subtitle: str, message: str = ""):
    """rumps.notification wrapper — swallows the missing-Info.plist crash in dev."""
    try:
        rumps.notification(title, subtitle, message)
    except Exception as e:
        log.debug("notification suppressed: %s", e)


def _ask_text(title: str, prompt: str, default: str = "") -> str | None:
    def _esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')
    script = (f'display dialog "{_esc(prompt)}" default answer "{_esc(default)}" '
              f'with title "{_esc(title)}" buttons {{"Cancel", "Save"}} default button "Save"')
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=120)
        if r.returncode == 0 and "text returned:" in r.stdout:
            return r.stdout.split("text returned:")[-1].strip()
    except Exception:
        log.exception("_ask_text failed")
    return None


def _show_text(text: str):
    """Open `text` in TextEdit via a temp file that is removed a minute later."""
    try:
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False,
                                          prefix="aiquotabar_raw_")
        tmp.write(text)
        tmp.close()
        subprocess.Popen(["open", "-a", "TextEdit", tmp.name])

        def _cleanup():
            time.sleep(60)
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
        threading.Thread(target=_cleanup, daemon=True).start()
    except Exception:
        log.exception("_show_text failed")


def _safe_url(url) -> str | None:
    """Only https links to the services we talk to may be opened from the UI."""
    if not isinstance(url, str):
        return None
    p = urllib.parse.urlparse(url)
    host = (p.hostname or "").lower()
    if p.scheme == "https" and any(host == h or host.endswith("." + h) for h in OPEN_URL_HOSTS):
        return url
    return None


def _open(url: str):
    subprocess.Popen(["open", url])


def _sentence(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


# ── app ──────────────────────────────────────────────────────────────────────

class ClaudeBar(rumps.App):
    def __init__(self, demo: bool = False):
        super().__init__("◆", quit_button=None)
        self._demo = demo
        if demo:
            from aiquotabar.demo import demo_snapshot
            self.config = dict(demo_snapshot().config)
        else:
            self.config = load_config()

        # -- data (written by the fetch thread, read on the main thread) --
        self._last_raw: dict = {}
        self._last_data: UsageData | None = None
        self._claude_error: dict | None = None
        self._provider_data: list[ProviderData] = []
        self._cc_stats: dict | None = None
        self._trends: dict = {}
        self._week_hits: dict = {}
        self._last_updated: float | None = None
        self._history = {} if demo else _load_history()

        # -- alert bookkeeping --
        self._warned_pcts: set[str] = set()
        self._prev_pcts: dict[str, int] = {}
        self._pacing_alerted: set[str] = set()
        self._auth_fail_count = 0
        self._detect_attempts: dict[str, float] = {}

        # -- threading --
        self._fetching = False
        self._refetch = False
        self._detecting: set[str] = set()
        self._state_lock = threading.Lock()
        self._config_lock = threading.Lock()
        self._db_lock = threading.Lock()
        self._ui_dirty = False
        self._last_title_tick = 0.0

        self._refresh_interval = self.config.get("refresh_interval", DEFAULT_REFRESH)
        self._last_update_check = self.config.get("last_update_check", 0)

        self._history_db = _init_history_db() if not demo else None
        self._last_rollup = 0.0
        if self._history_db is not None:
            try:
                _rollup_daily_stats(self._history_db)
                self._last_rollup = time.time()
            except Exception:
                log.exception("startup rollup failed")

        if not demo and self.config.get("launch_at_login", True) and not _is_login_item():
            _add_login_item()

        # -- UI --
        self._panel: webview.Panel | None = None
        self._windows: dict[str, webview.Window] = {}
        self._web_ok = False
        self._rebuild_menu()

        self._timer = rumps.Timer(self._on_timer, self._refresh_interval)
        self._timer.start()
        # Main-thread ticker: applies updates queued by background threads.
        self._ui_ticker = rumps.Timer(self._flush_ui, 0.25)
        self._ui_ticker.start()
        # Finish setup once the run loop is live (status button exists then).
        self._startup_timer = rumps.Timer(self._deferred_startup, 0.5)
        self._startup_timer.start()

        atexit.register(self._shutdown)
        self._schedule_fetch()

    # ── lifecycle ────────────────────────────────────────────────────────────

    def _save(self):
        if not self._demo:
            save_config(self.config)

    def _shutdown(self):
        try:
            if self._history_db is not None:
                self._history_db.close()
        except Exception:
            pass

    def _deferred_startup(self, timer):
        timer.stop()
        try:
            webview.install_edit_menu()
        except Exception:
            log.debug("edit menu install failed", exc_info=True)
        self._web_ok = webview.available()
        if self._web_ok:
            try:
                self._panel = webview.Panel(self._status_button, self._on_web_message)
                self._hook_status_button()
            except Exception:
                log.exception("panel setup failed — using the text menu")
                self._web_ok = False
                self._panel = None
                try:
                    self._nsapp.nsstatusitem.setMenu_(self._menu._menu)
                except Exception:
                    pass
        self._rebuild_menu()
        self._push_all()
        if self.config.get("seen_welcome") and not self.config.get("seen_v2") and not self._demo:
            self.config["seen_v2"] = True
            self._save()
            _notify("AIQuotaBar 2.0", "A redesigned panel, pace tracking and usage history",
                    "Click the menu bar icon to take a look.")
        if not self.config.get("seen_welcome") or self._demo:
            self.config["seen_welcome"] = True
            self.config["seen_v2"] = True
            self._save()
            if self._web_ok:
                self._open_window("welcome")
            else:
                _notify("Welcome to AIQuotaBar", "Your AI limits live in the menu bar",
                        "Click the ◆ icon to see them.")

    def _status_button(self):
        return self._nsapp.nsstatusitem.button()

    def _hook_status_button(self):
        """Left click toggles the panel; right click shows the small context menu."""
        self._clicker = webview.status_click_target(
            left=lambda: self._panel.toggle(), right=self._show_context_menu)
        self._nsapp.nsstatusitem.setMenu_(None)
        btn = self._status_button()
        btn.setTarget_(self._clicker)
        btn.setAction_(b"clicked:")
        btn.sendActionOn_(4 | 16)    # left mouse up | right mouse up
        log.info("status button hooked to the web panel")

    def _show_context_menu(self):
        if self._panel:
            self._panel.dismiss()
        try:
            self._nsapp.nsstatusitem.popUpStatusItemMenu_(self._menu._menu)
        except Exception:
            log.debug("context menu failed", exc_info=True)

    # ── state → UI ───────────────────────────────────────────────────────────

    def _snapshot(self) -> Snapshot:
        with self._state_lock:
            return Snapshot(
                config=self.config,
                claude=self._last_data,
                claude_error=self._claude_error,
                providers=list(self._provider_data),
                cc_stats=self._cc_stats,
                history=self._history,
                trends=dict(self._trends),
                week_hits=dict(self._week_hits),
                updated_at=self._last_updated,
                fetching=self._fetching,
                detecting=set(self._detecting),
            )

    def _post_update(self):
        """Ask the main thread to refresh the UI (safe from any thread)."""
        with self._state_lock:
            self._ui_dirty = True

    def _flush_ui(self, _timer):
        with self._state_lock:
            dirty, self._ui_dirty = self._ui_dirty, False
        if dirty:
            self._apply()
        elif self.config.get("bar_show_reset") and time.time() - self._last_title_tick > 30:
            self._update_title(self._snapshot())    # keep the countdown current
        panel = self._panel
        if panel and panel.show_when_ready and not panel.host.ready \
                and time.time() - panel.host.created_at > 5:
            # The page never loaded — don't leave the user with nothing.
            log.error("web panel did not load; falling back to the text menu")
            panel.show_when_ready = False
            self._web_ok = False
            self._nsapp.nsstatusitem.setMenu_(self._menu._menu)
            self._rebuild_menu()
            self._show_context_menu()

    def _apply(self):
        snap = self._snapshot()
        self._update_title(snap)
        self._rebuild_menu(snap)
        self._push_all(snap)

    def _push_all(self, snap: Snapshot | None = None):
        snap = snap or self._snapshot()
        if self._panel:
            self._panel.host.push(build_panel_state(snap))
        for view, win in list(self._windows.items()):
            if view in ("settings", "welcome"):
                win.host.push(self._settings_state(snap, view))

    def _settings_state(self, snap: Snapshot, view: str = "settings", tab: str | None = None) -> dict:
        st = build_settings_state(self.config, snap=snap, launch_at_login=_is_login_item(),
                                  widget_installed=_is_widget_installed(),
                                  version=__version__, tab=tab)
        st["view"] = view
        return st

    # ── status bar title ─────────────────────────────────────────────────────

    def _update_title(self, snap: Snapshot):
        self._last_title_tick = time.time()
        segments = bar_segments(snap)
        cc = None
        if snap.cc_stats and self.config.get("bar_show_cc", True):
            cc = snap.cc_stats.get("week_messages") or None
        if not segments and not cc:
            has_error = snap.claude_error and snap.claude_error.get("kind") == "auth"
            self._set_plain_title("◆ !" if has_error else "◆")
            return
        self._set_bar_title(segments, cc, bool(self.config.get("bar_show_reset")))

    def _set_plain_title(self, text: str):
        try:
            self._nsapp.nsstatusitem.setAttributedTitle_(None)
        except Exception:
            pass
        self.title = text

    def _set_bar_title(self, segments: list[dict], cc_msgs: int | None, show_reset: bool):
        """Brand icon + percentage per provider. Percentages turn orange/red at
        the alert thresholds so trouble shows without opening anything."""
        try:
            from AppKit import (NSColor, NSFont, NSForegroundColorAttributeName,
                                NSFontAttributeName)
            from Foundation import NSAttributedString, NSMutableAttributedString

            font = NSFont.menuBarFontOfSize_(0)
            base = {NSFontAttributeName: font} if font else {}
            sev_color = {"warn": NSColor.systemOrangeColor(), "crit": NSColor.systemRedColor()}

            def text(s: str, color=None):
                attrs = dict(base)
                if color is not None:
                    attrs[NSForegroundColorAttributeName] = color
                return NSAttributedString.alloc().initWithString_attributes_(s, attrs)

            out = NSMutableAttributedString.alloc().initWithString_("")
            for i, seg in enumerate(segments):
                p = theme.PROVIDERS[seg["id"]]
                if i:
                    out.appendAttributedString_(text("   "))
                img = _bar_icon(p["icon"], p["tint"])
                if img:
                    out.appendAttributedString_(_icon_astr(img, base))
                else:
                    out.appendAttributedString_(text("●"))
                out.appendAttributedString_(text(f" {seg['pct']}%", sev_color.get(seg["severity"])))
                if seg.get("weekly_maxed"):
                    out.appendAttributedString_(text(" ·"))
                if show_reset and i == 0 and seg.get("reset_ts"):
                    out.appendAttributedString_(text(" · " + fmt_duration(seg["reset_ts"] - time.time())))
            if cc_msgs:
                r, g, b = (int(theme.brand_color("claude")[i:i + 2], 16) / 255 for i in (1, 3, 5))
                if segments:
                    out.appendAttributedString_(text("   "))
                out.appendAttributedString_(text("◆", NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 1)))
                out.appendAttributedString_(text(f" {fmt_count(cc_msgs)}"))
            self._nsapp.nsstatusitem.setAttributedTitle_(out)
        except Exception:
            log.debug("attributed title failed", exc_info=True)
            parts = [f"{s['name']} {s['pct']}%" for s in segments]
            if cc_msgs:
                parts.append(f"◆ {fmt_count(cc_msgs)}")
            self.title = "  ".join(parts)

    # ── native menu (right-click, or everything when WebKit is missing) ──────

    def _rebuild_menu(self, snap: Snapshot | None = None):
        items: list = []
        if self._web_ok:
            items += [
                rumps.MenuItem("Show Usage", callback=lambda _: self._open_panel()),
                rumps.MenuItem("Refresh Now", callback=lambda _: self._schedule_fetch(), key="r"),
                None,
                rumps.MenuItem("Usage History…", callback=lambda _: self._open_window("history")),
                rumps.MenuItem("Settings…", callback=lambda _: self._open_window("settings"), key=","),
                None,
                rumps.MenuItem("⭐ Star on GitHub", callback=lambda _: _open(REPO_URL)),
                None,
            ]
        else:
            snap = snap or self._snapshot()
            for line in menu_lines(build_panel_state(snap)):
                items.append(None if line is None else self._info_item(line))
            if not self._web_ok and self._panel is None:
                items.append(self._info_item("Install pyobjc-framework-WebKit for the full panel"))
                items.append(None)
            items += [
                rumps.MenuItem("Refresh Now", callback=lambda _: self._schedule_fetch()),
                rumps.MenuItem("Auto-detect from Browser", callback=lambda _: self._detect_async(list(DETECTORS))),
                rumps.MenuItem("Set Claude Cookie…", callback=self._set_cookie_dialog),
                self._interval_menu(),
                self._login_menu_item(),
                None,
                rumps.MenuItem("⭐ Star on GitHub", callback=lambda _: _open(REPO_URL)),
                None,
            ]
        items.append(rumps.MenuItem("Quit AIQuotaBar", callback=rumps.quit_application))
        self.menu.clear()
        self.menu = items
        try:
            # Keep display-only rows readable instead of greyed out.
            self._menu._menu.setAutoenablesItems_(False)
        except Exception:
            pass

    @staticmethod
    def _info_item(title: str) -> rumps.MenuItem:
        item = rumps.MenuItem(title)
        item.set_callback(None)
        try:
            item._menuitem.setEnabled_(True)
        except Exception:
            pass
        return item

    def _interval_menu(self) -> rumps.MenuItem:
        menu = rumps.MenuItem("Refresh Interval")
        for label, secs in REFRESH_INTERVALS.items():
            item = rumps.MenuItem(label, callback=lambda _, s=secs: self._set_setting("refresh_interval", s))
            item.state = 1 if secs == self._refresh_interval else 0
            menu.add(item)
        return menu

    def _login_menu_item(self) -> rumps.MenuItem:
        item = rumps.MenuItem("Launch at Login",
                              callback=lambda _: self._set_login(not _is_login_item()))
        item.state = 1 if _is_login_item() else 0
        return item

    def _set_cookie_dialog(self, _sender):
        value = _ask_text("AIQuotaBar — Claude cookie",
                          "Paste the full cookie header from claude.ai (DevTools → Network).",
                          self.config.get("cookie_str", ""))
        if value:
            self._set_cookie(value)

    # ── windows & panel ──────────────────────────────────────────────────────

    def _open_panel(self):
        if self._panel and not self._panel.visible:
            self._panel.show()

    _WINDOW_SPECS = {
        "settings": ("AIQuotaBar Settings", (760, 560), False),
        "history":  ("Usage History", (780, 760), True),
        "welcome":  ("", (560, 700), False),
    }

    def _open_window(self, view: str, tab: str | None = None):
        if not self._web_ok:
            return
        if self._panel:
            self._panel.dismiss()
        win = self._windows.get(view)
        if win is None:
            title, size, resizable = self._WINDOW_SPECS[view]
            win = webview.Window(view, title, size, self._on_web_message,
                                 self._window_closed, resizable=resizable)
            self._windows[view] = win
        self._push_window(win, tab)
        win.show()

    def _push_window(self, win: "webview.Window", tab: str | None = None):
        snap = self._snapshot()
        if win.view == "history":
            win.host.push(build_history_state(self._history_rows(), dict(snap.trends)))
        else:
            win.host.push(self._settings_state(snap, win.view, tab))

    def _window_closed(self, win):
        if self._windows.get(win.view) is win:
            del self._windows[win.view]

    def _history_rows(self) -> list[tuple]:
        if self._demo:
            from aiquotabar.demo import demo_history_rows
            return demo_history_rows()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d")
        try:
            with self._db_lock:
                rows = self._history_db.execute(
                    "SELECT date, key, peak_pct, avg_pct, limit_hits FROM daily_stats "
                    "WHERE date >= ? ORDER BY date", (cutoff,)).fetchall()
                today = _get_today_stats(self._history_db)
        except Exception:
            log.exception("history query failed")
            return []
        rows = [tuple(r) for r in rows]
        for key, st in today.items():
            rows.append((st["date"], key, st["peak_pct"], st["avg_pct"], st["limit_hits"]))
        return rows

    # ── messages from the web UI ─────────────────────────────────────────────

    def _on_web_message(self, host, msg: dict):
        action = msg.get("action")
        try:
            handler = getattr(self, "_act_" + action, None) if isinstance(action, str) else None
            if handler is None:
                log.debug("ignored web action %r", action)
                return
            handler(host, msg)
        except Exception:
            log.exception("web action %r failed", action)

    def _act_ready(self, host, msg):
        if self._panel and host is self._panel.host:
            self._panel.host.push(build_panel_state(self._snapshot()))
            if self._panel.show_when_ready:
                self._panel.show()
        else:
            win = next((w for w in self._windows.values() if w.host is host), None)
            if win:
                self._push_window(win)

    def _act_resize(self, host, msg):
        h = msg.get("height")
        if self._panel and host is self._panel.host and isinstance(h, (int, float)) and 0 < h < 4000:
            self._panel.set_content_height(h)

    def _act_close(self, host, msg):
        if self._panel and host is self._panel.host:
            self._panel.dismiss()
            return
        for win in list(self._windows.values()):
            if win.host is host:
                win.close()

    def _act_refresh(self, host, msg):
        self._schedule_fetch()

    def _act_open_panel(self, host, msg):
        self._act_close(host, msg)
        self._open_panel()

    def _act_open_settings(self, host, msg):
        tab = msg.get("tab") if msg.get("tab") in ("general", "menubar", "accounts",
                                                   "notifications", "about") else None
        self._open_window("settings", tab)

    def _act_open_history(self, host, msg):
        self._open_window("history")

    def _act_open_url(self, host, msg):
        url = _safe_url(msg.get("url"))
        if url:
            _open(url)

    def _act_star(self, host, msg):
        _open(REPO_URL)

    def _act_set(self, host, msg):
        self._set_setting(msg.get("key"), msg.get("value"))

    def _act_set_login(self, host, msg):
        if isinstance(msg.get("value"), bool):
            self._set_login(msg["value"])

    def _act_detect(self, host, msg):
        pid = msg.get("provider")
        if pid == "all":
            self._detect_async(list(DETECTORS), manual=True)
        elif pid in DETECTORS:
            self._detect_async([pid], manual=True)

    def _act_disable(self, host, msg):
        pid = msg.get("provider")
        if pid in DETECTORS:
            with self._config_lock:
                disabled = self.config.setdefault("disabled_providers", [])
                if pid not in disabled:
                    disabled.append(pid)
                self._save()
            with self._state_lock:
                if pid == "claude":
                    self._last_data, self._claude_error = None, None
                else:
                    name = theme.PROVIDERS[pid]["name"]
                    self._provider_data = [p for p in self._provider_data if p.name != name]
            self._post_update()

    def _act_enable(self, host, msg):
        pid = msg.get("provider")
        if pid in DETECTORS:
            with self._config_lock:
                disabled = self.config.get("disabled_providers", [])
                if pid in disabled:
                    disabled.remove(pid)
                self._save()
            if not self.config.get(COOKIE_KEYS[pid]):
                self._detect_async([pid], manual=True)
            else:
                self._schedule_fetch()
            self._post_update()

    def _act_set_cookie(self, host, msg):
        if msg.get("provider") == "claude" and isinstance(msg.get("value"), str):
            self._set_cookie(msg["value"])

    def _act_set_api_key(self, host, msg):
        key, value = msg.get("key"), msg.get("value")
        if key not in PROVIDER_REGISTRY or key in COOKIE_PROVIDERS or not isinstance(value, str):
            return
        with self._config_lock:
            if value.strip():
                self.config[key] = value.strip()[:500]
            else:
                self.config.pop(key, None)
            self._save()
        self._schedule_fetch()
        self._post_update()

    def _act_share_image(self, host, msg):
        png = msg.get("png")
        prefix = "data:image/png;base64,"
        if not isinstance(png, str) or not png.startswith(prefix) or len(png) > 12_000_000:
            return
        data = base64.b64decode(png[len(prefix):], validate=True)
        mode = msg.get("mode")
        if mode in ("copy", "x"):
            from AppKit import NSPasteboard
            from Foundation import NSData
            pb = NSPasteboard.generalPasteboard()
            pb.clearContents()
            pb.setData_forType_(NSData.dataWithBytes_length_(data, len(data)), "public.png")
        if mode == "save":
            folder = os.path.expanduser("~/Downloads")
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, datetime.now().strftime("AIQuotaBar %Y-%m-%d at %H.%M.%S.png"))
            with open(path, "wb") as f:
                f.write(data)
            subprocess.Popen(["open", "-R", path])
        if mode == "x":
            share = build_panel_state(self._snapshot())["share"]
            _open("https://x.com/intent/post?text=" + urllib.parse.quote(share["text"])
                  + "&url=" + urllib.parse.quote(share["url"]))

    def _act_test_notification(self, host, msg):
        _notify("AIQuotaBar", "Notifications are on ✓",
                "You'll get a heads-up before you hit a limit.")

    def _act_show_raw(self, host, msg):
        _show_text(json.dumps(self._last_raw.get("usage", self._last_raw), indent=2))

    def _act_open_logs(self, host, msg):
        subprocess.Popen(["open", "-a", "Console", LOG_FILE])

    def _act_open_widget(self, host, msg):
        subprocess.Popen(["open", "-a", "AIQuotaBarHost"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _act_install_widget(self, host, msg):
        widget_dir = os.path.join(ROOT_DIR, "AIQuotaBarWidget")
        if os.path.isfile(os.path.join(widget_dir, "build_widget.sh")):
            rumps.alert(title="Install the desktop widget",
                        message=("Run this in Terminal (needs Xcode):\n\n"
                                 f"cd {widget_dir}\n./build_widget.sh\n\n"
                                 "Then right-click the desktop → Edit Widgets → search “AI Quota”."))
        else:
            _open(REPO_URL + "#desktop-widget-new")

    def _act_quit(self, host, msg):
        rumps.quit_application()

    # ── settings mutations ───────────────────────────────────────────────────

    def _set_setting(self, key, value):
        if not isinstance(key, str):
            return
        with self._config_lock:
            changed = apply_setting(self.config, key, value)
            if changed:
                self._save()
        if not changed:
            log.debug("rejected setting %r=%r", key, value)
            self._post_update()     # re-sync the UI with what's actually stored
            return
        if key == "refresh_interval" and value != self._refresh_interval:
            self._refresh_interval = value
            self._timer.stop()
            self._timer = rumps.Timer(self._on_timer, value)
            self._timer.start()
        if key in ("warn_threshold", "crit_threshold"):
            self._warned_pcts.clear()
        self._post_update()

    def _set_login(self, on: bool):
        if self._demo:
            return
        if on:
            _add_login_item()
        else:
            _remove_login_item()
        with self._config_lock:
            self.config["launch_at_login"] = on
            self._save()
        self._post_update()

    def _set_cookie(self, value: str):
        value = value.strip()
        if "=" not in value and len(value) < 20:
            return
        with self._config_lock:
            self.config["cookie_str"] = value
            disabled = self.config.get("disabled_providers", [])
            if "claude" in disabled:
                disabled.remove("claude")
            self._save()
        self._warned_pcts.clear()
        self._auth_fail_count = 0
        self._schedule_fetch()

    # ── browser detection ────────────────────────────────────────────────────

    def _detect_async(self, pids: list[str], manual: bool = False):
        if self._demo:
            return
        if not _BROWSER_COOKIE3_OK:
            _notify("AIQuotaBar", "browser-cookie3 is not installed",
                    "Run: pip install browser-cookie3")
            return
        with self._state_lock:
            pids = [p for p in pids if p not in self._detecting]
            self._detecting.update(pids)
        if not pids:
            return
        self._post_update()
        threading.Thread(target=self._detect_worker, args=(pids, manual), daemon=True).start()

    def _detect_worker(self, pids: list[str], manual: bool):
        found = []
        for pid in pids:
            cookie = None
            try:
                cookie = DETECTORS[pid]()
            except Exception:
                log.exception("cookie detection failed for %s", pid)
            self._detect_attempts[pid] = time.time()
            if cookie:
                with self._config_lock:
                    self.config[COOKIE_KEYS[pid]] = cookie
                    disabled = self.config.get("disabled_providers", [])
                    if pid in disabled:
                        disabled.remove(pid)
                    self._save()
                found.append(pid)
            elif manual:
                name = theme.PROVIDERS[pid]["name"]
                _notify("AIQuotaBar", f"No {name} session found",
                        f"Sign in to {SIGN_IN_DOMAINS[pid]} in your browser, then try again.")
            with self._state_lock:
                self._detecting.discard(pid)
            self._post_update()
        if found:
            if "claude" in found:
                self._auth_fail_count = 0
                self._warned_pcts.clear()
            self._schedule_fetch()

    # ── fetch ────────────────────────────────────────────────────────────────

    def _on_timer(self, _timer):
        self._schedule_fetch()

    def _schedule_fetch(self):
        with self._state_lock:
            if self._fetching:
                self._refetch = True
                return
            self._fetching = True
        self._post_update()
        threading.Thread(target=self._fetch_and_update, daemon=True).start()

    def _fetch_and_update(self):
        try:
            if self._demo:
                self._fetch_demo()
            else:
                self._fetch_claude()
                self._fetch_providers()
                self._cc_stats = fetch_claude_code_stats()
                self._record_history()
                self._check_provider_warnings(self._provider_data)
                self._check_pacing_alerts()
            self._last_updated = time.time()
            if not self._demo:
                _write_widget_cache(self._last_data or UsageData(), self._provider_data,
                                    self._cc_stats, self.config)
                self._maybe_auto_update()
        except Exception:
            log.exception("fetch failed")
        finally:
            with self._state_lock:
                self._fetching = False
                again, self._refetch = self._refetch, False
            self._post_update()
            if again:
                self._schedule_fetch()

    def _fetch_demo(self):
        from aiquotabar.demo import demo_snapshot
        time.sleep(0.6)
        snap = demo_snapshot()
        with self._state_lock:
            self._last_data, self._claude_error = snap.claude, snap.claude_error
            self._provider_data, self._cc_stats = snap.providers, snap.cc_stats
            self._history, self._trends, self._week_hits = snap.history, snap.trends, snap.week_hits

    def _fetch_claude(self):
        if "claude" in self.config.get("disabled_providers", []):
            with self._state_lock:
                self._last_data, self._claude_error = None, None
            return
        with self._config_lock:
            cookie = self.config.get("cookie_str")
        if not cookie and self._should_detect("claude"):
            with self._state_lock:
                self._detecting.add("claude")
            self._post_update()
            try:
                cookie = _auto_detect_cookies()
            finally:
                self._detect_attempts["claude"] = time.time()
                with self._state_lock:
                    self._detecting.discard("claude")
            if cookie:
                with self._config_lock:
                    self.config["cookie_str"] = cookie
                    self._save()
        if not cookie:
            with self._state_lock:
                self._last_data = None
                self._claude_error = {"kind": "missing", "message": "No claude.ai session found"}
            return

        try:
            raw = fetch_raw(cookie)
        except CurlHTTPError as e:
            code = getattr(getattr(e, "response", None), "status_code", 0) or 0
            log.error("Claude HTTP error %s: %s", code, e)
            if code in (401, 403):
                self._on_claude_auth_failure(code)
            else:
                with self._state_lock:
                    self._claude_error = {"kind": "network", "message": f"HTTP {code}"}
            return
        except ValueError as e:        # no organization id: cookie is incomplete / stale
            self._on_claude_auth_failure(0, str(e))
            return
        except Exception as e:
            log.exception("Claude fetch failed")
            with self._state_lock:
                self._claude_error = {"kind": "network", "message": str(e)[:120]}
            return

        data = parse_usage(raw)
        with self._state_lock:
            self._last_raw = raw
            self._last_data = data
            self._claude_error = None
        self._auth_fail_count = 0
        self._check_warnings(data)

    def _on_claude_auth_failure(self, code: int, message: str = ""):
        self._auth_fail_count += 1
        with self._state_lock:
            self._claude_error = {"kind": "auth", "message": message or f"{code} session expired"}
            if self._auth_fail_count >= 2:
                self._last_data = None
        if self._auth_fail_count >= 2 and self._should_detect("claude", force_after=5 * 60):
            self._detect_attempts["claude"] = time.time()
            fresh = _auto_detect_cookies()
            if fresh and fresh != self.config.get("cookie_str"):
                with self._config_lock:
                    self.config["cookie_str"] = fresh
                    self._save()
                self._auth_fail_count = 0
                self._warned_pcts.clear()
                log.info("Claude auth failed — picked up a fresh browser session")
                with self._state_lock:
                    self._refetch = True
            elif self._auth_fail_count == 2:
                _notify("AIQuotaBar", "Claude session expired",
                        "Sign in to claude.ai in your browser — AIQuotaBar will pick it up.")

    def _should_detect(self, pid: str, force_after: float = REDETECT_EVERY) -> bool:
        return time.time() - self._detect_attempts.get(pid, 0) > force_after

    def _fetch_providers(self):
        """Fetch every configured provider in parallel — independent of Claude."""
        disabled = set(self.config.get("disabled_providers", []))
        for cfg_key in COOKIE_PROVIDERS:
            pid = next(p for p, k in COOKIE_KEYS.items() if k == cfg_key)
            if pid in disabled or self.config.get(cfg_key) or not self._should_detect(pid):
                continue
            with self._state_lock:
                self._detecting.add(pid)
            self._post_update()
            try:
                cookie = DETECTORS[pid]()
            except Exception:
                cookie = None
            self._detect_attempts[pid] = time.time()
            with self._state_lock:
                self._detecting.discard(pid)
            if cookie:
                with self._config_lock:
                    self.config[cfg_key] = cookie
                    self._save()

        with self._config_lock:
            keys = {k: self.config.get(k) for k in PROVIDER_REGISTRY}
        tasks = []
        for cfg_key, (name, fn) in PROVIDER_REGISTRY.items():
            pid = theme.NAME_TO_ID.get(name)
            if keys.get(cfg_key) and pid not in disabled:
                tasks.append((cfg_key, fn, keys[cfg_key]))
        results: list[ProviderData] = []
        if tasks:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
                futures = [(cfg_key, pool.submit(fn, k)) for cfg_key, fn, k in tasks]
                for cfg_key, fut in futures:
                    try:
                        results.append(fut.result())
                    except Exception:
                        log.exception("provider fetch failed: %s", cfg_key)

        # A signed-out cookie provider: look for a fresher browser session.
        for pd in results:
            pid = theme.NAME_TO_ID.get(pd.name)
            if pid in COOKIE_KEYS and pid != "claude" and pd.error and \
                    any(s in pd.error.lower() for s in ("401", "403", "not logged in")) and \
                    self._should_detect(pid):
                self._detect_attempts[pid] = time.time()
                fresh = DETECTORS[pid]()
                if fresh and fresh != self.config.get(COOKIE_KEYS[pid]):
                    with self._config_lock:
                        self.config[COOKIE_KEYS[pid]] = fresh
                        self._save()
                    with self._state_lock:
                        self._refetch = True
        with self._state_lock:
            self._provider_data = results

    def _history_points(self) -> list[tuple[str, int]]:
        """(history key, pct) pairs recorded each cycle."""
        pts = []
        if self._last_data and self._last_data.session and self._claude_error is None:
            pts.append(("claude", self._last_data.session.pct))
        for pd in self._provider_data:
            pid = theme.NAME_TO_ID.get(pd.name)
            if pd.error or pid is None:
                continue
            rows = getattr(pd, "_rows", None) or []
            if rows:
                pts += [(history_key(pid, r.label), r.pct) for r in rows]
            elif pd.pct is not None:
                pts.append((pid, pd.pct))
        return pts

    def _record_history(self):
        points = self._history_points()
        for key, pct in points:
            _append_history(self._history, key, pct)
        try:
            _save_history(self._history)
        except OSError:
            log.exception("saving short-term history failed")
        try:
            with self._db_lock:
                for key, pct in points:
                    _record_sample(self._history_db, key, pct)
                self._history_db.commit()
                if time.time() - self._last_rollup > 3600:
                    _rollup_daily_stats(self._history_db)
                    self._last_rollup = time.time()
                keys = [k for k, _ in points]
                trends = {k: get_trend(self._history_db, k) for k in keys}
                hits = {k: _get_week_limit_hits(self._history_db, k) for k in keys}
            with self._state_lock:
                self._trends, self._week_hits = trends, hits
        except Exception:
            log.exception("SQLite history recording failed")

    def _maybe_auto_update(self):
        if time.time() - self._last_update_check <= UPDATE_CHECK_INTERVAL:
            return
        self._last_update_check = time.time()
        with self._config_lock:
            self.config["last_update_check"] = self._last_update_check
            self._save()
        if _check_and_apply_update():
            _restart_app()

    # ── alerts ───────────────────────────────────────────────────────────────

    def _threshold_alerts(self, rows: list[tuple], who: str, warn_on: bool, reset_on: bool):
        """rows: (LimitRow, key). Notify on crossing warn/crit and on resets."""
        warn, crit = thresholds(self.config)
        for row, key in rows:
            warn_key, crit_key = f"{key}_warn", f"{key}_crit"
            prev = self._prev_pcts.get(key)
            label = f"{who} {row.label}".strip()
            if (reset_on and prev is not None and prev >= warn and row.pct < warn
                    and prev - row.pct >= 10):
                self._warned_pcts.discard(warn_key)
                self._warned_pcts.discard(crit_key)
                _notify("AIQuotaBar ✅", f"{label} has reset",
                        f"Now at {row.pct}% — you're good to go.")
            if warn_on:
                if row.pct >= crit and crit_key not in self._warned_pcts:
                    self._warned_pcts.update((warn_key, crit_key))
                    _notify("AIQuotaBar \U0001f534", f"{label} is at {row.pct}%",
                            _sentence(row.reset_str) or "Limit almost reached")
                elif warn <= row.pct < crit and warn_key not in self._warned_pcts:
                    self._warned_pcts.add(warn_key)
                    _notify("AIQuotaBar \U0001f7e1", f"{label} is at {row.pct}%",
                            _sentence(row.reset_str) or "Approaching the limit")
                elif row.pct < warn:
                    self._warned_pcts.discard(warn_key)
                    self._warned_pcts.discard(crit_key)
            self._prev_pcts[key] = row.pct

    def _check_warnings(self, data: UsageData):
        rows = [(r, k) for r, k in ((data.session, "session"), (data.weekly_all, "weekly_all"),
                                    (data.weekly_sonnet, "weekly_sonnet"),
                                    (data.weekly_opus, "weekly_opus")) if r]
        self._threshold_alerts(rows, "Claude", notif_enabled(self.config, "claude_warning"),
                               notif_enabled(self.config, "claude_reset"))

    def _check_provider_warnings(self, provider_data: list):
        for pname, prefix, warn_key, reset_key in (("ChatGPT", "chatgpt", "chatgpt_warning", "chatgpt_reset"),
                                                    ("Cursor", "cursor", "cursor_warning", None)):
            pd = next((p for p in provider_data if p.name == pname), None)
            if pd is None or pd.error:
                continue
            rows = [(r, f"{prefix}_{r.label}") for r in (getattr(pd, "_rows", None) or [])]
            self._threshold_alerts(rows, pname, notif_enabled(self.config, warn_key),
                                   bool(reset_key) and notif_enabled(self.config, reset_key))

    def _check_pacing_alerts(self):
        """Predictive alert when the burn rate says you'll hit a limit before it resets."""
        checks = []
        if self._last_data and self._last_data.session and self._claude_error is None:
            checks.append(("claude", "claude_pacing", "Claude session", self._last_data.session.resets_at))
        for pd in self._provider_data:
            pid = theme.NAME_TO_ID.get(pd.name)
            if pd.error or pid not in ("chatgpt", "cursor", "copilot"):
                continue
            rows = getattr(pd, "_rows", None) or []
            if rows:
                checks += [(history_key(pid, r.label), f"{pid}_pacing", f"{pd.name} {r.label}", r.resets_at)
                           for r in rows]
            else:
                checks.append(("copilot", "copilot_pacing", "Copilot", pd.resets_at))
        for hkey, nkey, label, resets_at in checks:
            if not notif_enabled(self.config, nkey):
                continue
            eta = _calc_eta_minutes(self._history, hkey)
            before_reset = resets_at is None or (eta is not None and time.time() + eta * 60 < resets_at)
            if eta is not None and eta <= PACING_ALERT_MINUTES and before_reset:
                if hkey not in self._pacing_alerted:
                    self._pacing_alerted.add(hkey)
                    _notify("AIQuotaBar ⏱", f"{label}: limit in ~{fmt_duration(eta * 60)}",
                            "At your current pace you'll hit the cap before it resets.")
            else:
                self._pacing_alerted.discard(hkey)
