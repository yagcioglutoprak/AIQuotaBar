"""UI components -- menu bar app, floating panel, windows."""

import rumps
import atexit
import json
import os
import subprocess
import sys
import tempfile
import time
import threading
import urllib.parse
from datetime import datetime, timezone, timedelta

from aiquotabar.config import (
    log, load_config, save_config, notif_enabled, set_notif,
    REFRESH_INTERVALS, DEFAULT_REFRESH,
    WARN_THRESHOLD, CRIT_THRESHOLD, PACING_ALERT_MINUTES,
    UPDATE_CHECK_INTERVAL, HISTORY_COLORS,
    WIDGET_CACHE_DIR,
)
from aiquotabar.providers import (
    LimitRow, UsageData, ProviderData, parse_usage, fetch_raw,
    fetch_claude_code_stats, PROVIDER_REGISTRY, COOKIE_PROVIDERS,
    CurlHTTPError, parse_cookie_string,
    _auto_detect_cookies, _auto_detect_chatgpt_cookies,
    _auto_detect_copilot_cookies, _auto_detect_cursor_cookies,
    _warn_keychain_once, _fmt_reset, _BROWSER_COOKIE3_OK,
)
from aiquotabar.history import (
    _load_history, _save_history, _append_history,
    _calc_burn_rate, _calc_eta_minutes, _fmt_eta, _sparkline,
    _init_history_db, _record_sample, _rollup_daily_stats,
    _get_week_limit_hits, _get_today_stats,
    _fetch_history_data, _nscolor,
)
from aiquotabar.widget import _write_widget_cache, _is_widget_installed
from aiquotabar.update import _check_and_apply_update, _restart_app


# -- Brand icon helpers --------------------------------------------------------

_ICON_DIR   = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
_ICON_SIZE  = 14   # points -- matches menu bar font height
_icon_cache: dict = {}


def _bar_icon(filename: str, tint_hex: str | None = None):
    """Lazy-load and cache a menu bar icon (14x14 pt NSImage).

    tint_hex: e.g. '#74AA9C' -- applied to monochrome (black) icons so they
              show in the brand color. Pass None for already-coloured icons.
    """
    key = (filename, tint_hex)
    if key in _icon_cache:
        return _icon_cache[key]
    img = None
    try:
        from AppKit import NSImage, NSColor
        path = os.path.join(_ICON_DIR, filename)
        raw = NSImage.alloc().initWithContentsOfFile_(path)
        if raw:
            img = raw.copy()
            img.setSize_((_ICON_SIZE, _ICON_SIZE))
            if tint_hex:
                r = int(tint_hex[1:3], 16) / 255
                g = int(tint_hex[3:5], 16) / 255
                b = int(tint_hex[5:7], 16) / 255
                color = NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 1.0)
                img.setTemplate_(True)
                if hasattr(img, "imageWithTintColor_"):
                    img = img.imageWithTintColor_(color)
    except Exception as e:
        log.debug("_bar_icon %s: %s", filename, e)
    _icon_cache[key] = img
    return img


def _icon_astr(img, base_attrs: dict):
    """Wrap an NSImage in an NSAttributedString via NSTextAttachment."""
    from AppKit import NSTextAttachment, NSAttributedString
    from Foundation import NSMakeRect, NSMutableAttributedString
    att = NSTextAttachment.alloc().init()
    att.setImage_(img)
    att.setBounds_(NSMakeRect(0, -3, _ICON_SIZE, _ICON_SIZE))
    astr = NSAttributedString.attributedStringWithAttachment_(att)
    m = NSMutableAttributedString.alloc().initWithAttributedString_(astr)
    for k, v in base_attrs.items():
        m.addAttribute_value_range_(k, v, (0, m.length()))
    return m


# -- Sticky toggle view (menu stays open on click) ----------------------------

_HAS_TOGGLE_VIEW = False
try:
    from AppKit import NSView, NSTextField, NSFont, NSColor, NSBezierPath, NSTrackingArea
    from Foundation import NSMakeRect
    import objc

    _TRACK_FLAGS = 0x01 | 0x80   # mouseEnteredAndExited | activeInActiveApp

    class _BarToggleView(NSView):
        """Custom NSView for menu items -- clicking does NOT dismiss the menu."""

        def initWithFrame_(self, frame):
            self = objc.super(_BarToggleView, self).initWithFrame_(frame)
            if self:
                self._action = None
                self._label = None
                self._hovering = False
                area = NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
                    self.bounds(), _TRACK_FLAGS, self, None,
                )
                self.addTrackingArea_(area)
            return self

        def mouseUp_(self, event):
            if callable(self._action):
                self._action()

        def mouseEntered_(self, event):
            self._hovering = True
            self.setNeedsDisplay_(True)

        def mouseExited_(self, event):
            self._hovering = False
            self.setNeedsDisplay_(True)

        def drawRect_(self, rect):
            if self._hovering:
                NSColor.selectedMenuItemColor().set()
                NSBezierPath.fillRect_(rect)
                if self._label:
                    self._label.setTextColor_(NSColor.selectedMenuItemTextColor())
            else:
                if self._label:
                    self._label.setTextColor_(NSColor.labelColor())

    _HAS_TOGGLE_VIEW = True
except Exception:
    pass


# -- Welcome window ------------------------------------------------------------

def _show_welcome_window(gif_path: str, widget_installed: bool) -> None:
    """Show a native macOS welcome window with side-by-side GIFs."""
    from AppKit import (
        NSWindow, NSImageView, NSImage, NSTextField, NSButton, NSFont,
        NSMakeRect, NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
        NSWindowStyleMaskFullSizeContentView,
        NSBackingStoreBuffered, NSTextAlignmentCenter,
        NSColor, NSBezelStyleRounded,
        NSApplication, NSFloatingWindowLevel, NSScreen,
        NSVisualEffectView, NSView,
    )
    import Quartz

    PAD = 24
    GAP = 16

    # Load both GIFs
    assets_dir = _ICON_DIR
    demo_img = NSImage.alloc().initWithContentsOfFile_(
        os.path.join(assets_dir, "demo.gif")
    )
    widget_img = NSImage.alloc().initWithContentsOfFile_(gif_path)

    # Both GIFs at same height; widths from aspect ratios
    GIF_H = 480
    def _w_for_h(img, h):
        if not img:
            return 200
        iw, ih = img.size().width, img.size().height
        return int(h * iw / ih) if ih > 0 else 200

    # Left GIF: fixed wider width, fill-scaled (crops top/bottom)
    demo_w = 320
    widget_w = _w_for_h(widget_img, GIF_H)
    WIN_W = PAD + demo_w + GAP + widget_w + PAD
    WIN_H = 70 + GIF_H + 20 + 150 + 54  # header + gifs + labels + info + button

    # Centre on screen
    screen = NSScreen.mainScreen().frame()
    sx = (screen.size.width - WIN_W) / 2
    sy = (screen.size.height - WIN_H) / 2

    win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(sx, sy, WIN_W, WIN_H),
        (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
         | NSWindowStyleMaskFullSizeContentView),
        NSBackingStoreBuffered,
        False,
    )
    win.setTitle_("")
    win.setTitlebarAppearsTransparent_(True)
    win.setTitleVisibility_(1)
    win.setLevel_(NSFloatingWindowLevel)
    win.setMovableByWindowBackground_(True)

    content = win.contentView()
    content.setWantsLayer_(True)

    # -- Vibrancy background --
    blur = NSVisualEffectView.alloc().initWithFrame_(content.bounds())
    blur.setAutoresizingMask_(18)
    blur.setBlendingMode_(0)
    blur.setMaterial_(3)
    blur.setState_(1)
    content.addSubview_(blur)

    border_color = Quartz.CGColorCreateGenericRGB(1, 1, 1, 0.08)

    def _make_gif(img, x, y, w, h, fill=False):
        c = NSView.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
        c.setWantsLayer_(True)
        c.layer().setCornerRadius_(10)
        c.layer().setMasksToBounds_(True)
        c.layer().setBorderWidth_(0.5)
        c.layer().setBorderColor_(border_color)
        content.addSubview_(c)
        if img:
            if fill:
                # Scale to fill: size image view to cover container, clip overflow
                iw, ih = img.size().width, img.size().height
                ratio = iw / ih if ih > 0 else 1.0
                # Scale by width -> compute height needed
                iv_w = w
                iv_h = int(w / ratio)
                iv_y = h - iv_h  # align to top
                iv = NSImageView.alloc().initWithFrame_(
                    NSMakeRect(0, iv_y, iv_w, iv_h))
            else:
                iv = NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
            iv.setImage_(img)
            iv.setAnimates_(True)
            iv.setImageScaling_(3)
            iv.setImageAlignment_(0)
            iv.setWantsLayer_(True)
            iv.layer().setMagnificationFilter_(Quartz.kCAFilterTrilinear)
            iv.layer().setMinificationFilter_(Quartz.kCAFilterTrilinear)
            iv.layer().setShouldRasterize_(True)
            iv.layer().setRasterizationScale_(3.0)
            c.addSubview_(iv)

    def _label(text, x, y, w, align=NSTextAlignmentCenter, size=11, weight=0.3):
        lbl = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, w, 14))
        lbl.setStringValue_(text)
        lbl.setBezeled_(False)
        lbl.setDrawsBackground_(False)
        lbl.setEditable_(False)
        lbl.setSelectable_(False)
        lbl.setAlignment_(align)
        lbl.setFont_(NSFont.systemFontOfSize_weight_(size, weight))
        lbl.setTextColor_(NSColor.secondaryLabelColor())
        content.addSubview_(lbl)

    # -- Title + subtitle --
    y_top = WIN_H - 52
    t = NSTextField.alloc().initWithFrame_(
        NSMakeRect(PAD, y_top, WIN_W - PAD * 2, 28)
    )
    t.setStringValue_("Welcome to AIQuotaBar")
    t.setBezeled_(False)
    t.setDrawsBackground_(False)
    t.setEditable_(False)
    t.setSelectable_(False)
    t.setAlignment_(NSTextAlignmentCenter)
    t.setFont_(NSFont.systemFontOfSize_weight_(20, 0.56))
    content.addSubview_(t)

    _label("Monitor your Claude and ChatGPT usage limits in real time.",
           PAD, y_top - 22, WIN_W - PAD * 2, size=12)

    # -- Side-by-side GIFs --
    gif_y = y_top - 22 - 14 - GIF_H
    _make_gif(demo_img, PAD, gif_y, demo_w, GIF_H, fill=True)
    _label("Menu Bar", PAD, gif_y - 16, demo_w)

    widget_x = PAD + demo_w + GAP
    _make_gif(widget_img, widget_x, gif_y, widget_w, GIF_H)
    _label("Desktop Widget", widget_x, gif_y - 16, widget_w)

    # -- Info rows --
    info_y = gif_y - 40
    inner_w = WIN_W - PAD * 2
    rows = [
        ("Menu Bar",
         "Click the diamond icon to see session limits, weekly caps, and reset times."),
        ("Desktop Widget",
         "Installed and synced. Right-click desktop > Edit Widgets > 'AI Quota'."
         if widget_installed else
         "Available to install. Check 'Desktop Widget' in the menu bar."),
        ("Auto Refresh",
         "Data updates every 60 seconds. Alerts at 80% and 95% usage."),
    ]
    for heading, desc in rows:
        h = NSTextField.alloc().initWithFrame_(
            NSMakeRect(PAD, info_y, inner_w, 16)
        )
        h.setStringValue_(heading)
        h.setBezeled_(False)
        h.setDrawsBackground_(False)
        h.setEditable_(False)
        h.setSelectable_(False)
        h.setAlignment_(NSTextAlignmentCenter)
        h.setFont_(NSFont.systemFontOfSize_weight_(12, 0.4))
        content.addSubview_(h)
        info_y -= 16

        d = NSTextField.alloc().initWithFrame_(
            NSMakeRect(PAD, info_y, inner_w, 14)
        )
        d.setStringValue_(desc)
        d.setBezeled_(False)
        d.setDrawsBackground_(False)
        d.setEditable_(False)
        d.setSelectable_(False)
        d.setAlignment_(NSTextAlignmentCenter)
        d.setFont_(NSFont.systemFontOfSize_(11))
        d.setTextColor_(NSColor.secondaryLabelColor())
        content.addSubview_(d)
        info_y -= 22

    # -- "Got it" button --
    btn_w, btn_h = 120, 30
    btn = NSButton.alloc().initWithFrame_(
        NSMakeRect((WIN_W - btn_w) / 2, 14, btn_w, btn_h)
    )
    btn.setTitle_("Got it")
    btn.setBezelStyle_(NSBezelStyleRounded)
    btn.setKeyEquivalent_("\r")
    btn.setAction_(b"performClose:")
    btn.setTarget_(win)
    content.addSubview_(btn)

    win.makeKeyAndOrderFront_(None)
    NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    _show_welcome_window._active_win = win


# -- History window helpers ----------------------------------------------------

def _ensure_history_handler():
    """Lazily create an ObjC click handler for heatmap cells."""
    h = getattr(_ensure_history_handler, '_inst', None)
    if h is not None:
        return h
    try:
        from AppKit import NSObject

        class _HMapHandler(NSObject):
            def cellClicked_(self, sender):
                fn = getattr(type(self), '_on_click', None)
                if fn:
                    fn(sender.tag())
        _ensure_history_handler._inst = _HMapHandler.alloc().init()
    except Exception:
        log.debug("Failed to create history click handler", exc_info=True)
        _ensure_history_handler._inst = None
    return _ensure_history_handler._inst


def _show_history_window(conn) -> None:
    """Show a native macOS Usage History window with heatmap, stats, and charts."""
    from AppKit import (
        NSWindow, NSTextField, NSFont, NSColor, NSView, NSScrollView,
        NSButton, NSMakeRect, NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
        NSWindowStyleMaskFullSizeContentView, NSBackingStoreBuffered,
        NSTextAlignmentCenter, NSTextAlignmentLeft,
        NSApplication, NSFloatingWindowLevel, NSScreen,
        NSVisualEffectView,
    )
    import Quartz

    # Reuse existing window if open
    existing = getattr(_show_history_window, "_active_win", None)
    if existing is not None:
        try:
            existing.makeKeyAndOrderFront_(None)
            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            return
        except Exception:
            pass

    data = _fetch_history_data(conn)
    if data is None:
        return

    WIN_W = 560
    WIN_H = 680
    PAD = 28
    inner_w = WIN_W - PAD * 2

    summary = data["summary"]
    days = data["days"]
    per_day_detail = data.get("per_day_detail", {})
    today_windows = data.get("today_windows", {})
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # Only show providers that have actual usage data
    providers = [p for p in data["providers"] if p["peak"] > 0]

    # -- Helpers --------------------------------------------------------

    def _cg(hex_str, alpha=1.0):
        h = hex_str.lstrip("#")
        r, g, b = int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255
        return Quartz.CGColorCreateGenericRGB(r, g, b, alpha)

    dark_bg = _cg("#1C1C2A")
    card_bg = _cg("#232336")
    track_bg = _cg("#1C1C2A")
    dim = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.55, 0.55, 0.6, 1.0)
    dimmer = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.4, 0.4, 0.45, 1.0)

    # -- Precompute heatmap cell sizing (needed for doc_h) ----------------
    today = datetime.now(timezone.utc).date()
    _hm_start = today - timedelta(days=89)
    _hm_start -= timedelta(days=_hm_start.weekday())
    _hm_num_days = (today - _hm_start).days + 1
    _hm_num_cols = (_hm_num_days + 6) // 7
    DAY_LABEL_W = 32
    _hm_avail = inner_w - DAY_LABEL_W
    HGAP = 3
    CELL = max(10, int((_hm_avail - HGAP * (_hm_num_cols - 1)) / _hm_num_cols))
    HSTEP = CELL + HGAP

    # -- Compute total content height -------------------------------------
    HEATMAP_H = 7 * HSTEP + 14 + CELL + 8  # 7 rows + month labels + legend
    CARD_H = 60
    BAR_H = 14
    BAR_GAP = 5
    INTRADAY_BLOCK = 20 + 5 * (BAR_H + BAR_GAP) + 8

    doc_h = PAD + 16          # top padding (below titlebar)
    doc_h += 32 + 22          # title + subtitle
    doc_h += 16 + HEATMAP_H   # gap + heatmap
    doc_h += 24               # day info row below heatmap
    doc_h += 24 + CARD_H      # gap + stat cards
    PROV_HEADER = 22 + 18     # colored dot/name + summary line
    for p in providers:
        doc_h += 24 + PROV_HEADER
        if p["key"] in today_windows:
            doc_h += INTRADAY_BLOCK
        else:
            doc_h += 7 * (BAR_H + BAR_GAP) + 16
    doc_h += 24 + 20 + PAD    # gap + footer + bottom pad
    doc_h = max(doc_h, WIN_H)

    # Placement helpers: top_y is logical offset from top, converted to
    # NSView bottom-up coordinates via  real_y = parent_h - top_y - h

    def _v(parent, x, top_y, w, h, bg=None, corner=0, ph=None, tooltip=None):
        real_y = (ph or doc_h) - top_y - h
        v = NSView.alloc().initWithFrame_(NSMakeRect(x, real_y, w, h))
        v.setWantsLayer_(True)
        if bg:
            v.layer().setBackgroundColor_(bg)
        if corner:
            v.layer().setCornerRadius_(corner)
            v.layer().setMasksToBounds_(True)
        if tooltip:
            v.setToolTip_(tooltip)
        parent.addSubview_(v)
        return v

    def _lbl(parent, text, x, top_y, w, h=0, size=12, weight=0.0,
             color=None, align=NSTextAlignmentLeft, mono=False, ph=None):
        if h == 0:
            h = int(size * 1.5 + 2)
        real_y = (ph or doc_h) - top_y - h
        lbl = NSTextField.alloc().initWithFrame_(NSMakeRect(x, real_y, w, h))
        lbl.setStringValue_(text)
        lbl.setBezeled_(False)
        lbl.setDrawsBackground_(False)
        lbl.setEditable_(False)
        lbl.setSelectable_(False)
        lbl.setAlignment_(align)
        if mono:
            lbl.setFont_(NSFont.monospacedDigitSystemFontOfSize_weight_(size, weight))
        else:
            lbl.setFont_(NSFont.systemFontOfSize_weight_(size, weight))
        lbl.setTextColor_(color or NSColor.labelColor())
        parent.addSubview_(lbl)
        return lbl

    # -- Build window -----------------------------------------------------
    screen = NSScreen.mainScreen().frame()
    sx = (screen.size.width - WIN_W) / 2
    sy = (screen.size.height - WIN_H) / 2

    win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(sx, sy, WIN_W, WIN_H),
        (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
         | NSWindowStyleMaskFullSizeContentView),
        NSBackingStoreBuffered,
        False,
    )
    win.setTitle_("")
    win.setTitlebarAppearsTransparent_(True)
    win.setTitleVisibility_(1)
    win.setLevel_(NSFloatingWindowLevel)
    win.setMovableByWindowBackground_(True)

    content = win.contentView()
    content.setWantsLayer_(True)

    blur = NSVisualEffectView.alloc().initWithFrame_(content.bounds())
    blur.setAutoresizingMask_(18)
    blur.setBlendingMode_(0)
    blur.setMaterial_(3)
    blur.setState_(1)
    content.addSubview_(blur)

    scroll = NSScrollView.alloc().initWithFrame_(content.bounds())
    scroll.setAutoresizingMask_(18)
    scroll.setHasVerticalScroller_(True)
    scroll.setDrawsBackground_(False)
    scroll.setBorderType_(0)
    content.addSubview_(scroll)

    doc = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, WIN_W, doc_h))
    scroll.setDocumentView_(doc)

    # -- Layout (top-down y cursor) ---------------------------------------
    y = PAD + 16

    # Title
    _lbl(doc, "Usage History", PAD, y, inner_w, size=22, weight=0.56)
    y += 32
    _lbl(doc, f"Average daily usage across all providers  \u00b7  {summary['total_days']} days tracked",
         PAD, y, inner_w, size=12, color=dim)
    y += 30

    # -- Heatmap (hero section -- fills available width) ------------------
    HLEFT = PAD + DAY_LABEL_W
    hm_top = y
    start = _hm_start

    # Day labels
    for row, dl in enumerate(["Mon", "", "Wed", "", "Fri", "", "Sun"]):
        if dl:
            _lbl(doc, dl, PAD, hm_top + row * HSTEP + 1, DAY_LABEL_W - 4,
                 size=9, color=dimmer)

    # Cells (interactive NSButtons for click-to-inspect)
    handler = _ensure_history_handler()
    date_for_tag = {}  # tag -> date_str
    tag_counter = [0]

    current = start
    col = 0
    last_month = -1
    while current <= today:
        row = current.weekday()
        cx = HLEFT + col * HSTEP
        cy = hm_top + row * HSTEP

        if current.month != last_month and row == 0:
            _lbl(doc, current.strftime("%b"), cx, hm_top - 14, 40,
                 size=9, color=dimmer)
            last_month = current.month

        ds = current.strftime("%Y-%m-%d")
        pct = days.get(ds, -1)
        if pct < 0:
            cc = dark_bg
            tip = f"{ds}  \u2013  No data"
        else:
            t = min(pct / 100, 1.0)
            r = 0.14 + t * 0.71
            g = 0.14 + t * 0.33
            b = 0.20 + t * 0.14
            cc = Quartz.CGColorCreateGenericRGB(r, g, b, 1.0)
            tip = f"{ds}  \u2013  Peak {pct}%"

        tag = tag_counter[0]
        tag_counter[0] += 1
        date_for_tag[tag] = ds

        real_y = doc_h - cy - CELL
        btn = NSButton.alloc().initWithFrame_(NSMakeRect(cx, real_y, CELL, CELL))
        btn.setBordered_(False)
        btn.setTitle_("")
        btn.setWantsLayer_(True)
        btn.layer().setBackgroundColor_(cc)
        btn.layer().setCornerRadius_(3)
        btn.layer().setMasksToBounds_(True)
        btn.setToolTip_(tip)
        if handler:
            btn.setTarget_(handler)
            btn.setAction_(b"cellClicked:")
            btn.setTag_(tag)
        doc.addSubview_(btn)

        if row == 6:
            col += 1
        current += timedelta(days=1)

    # Legend
    legend_y = hm_top + 7 * HSTEP + 8
    lx = HLEFT
    _lbl(doc, "Less", lx - 30, legend_y + 1, 28, size=9, color=dimmer)
    for lv in [0, 25, 50, 75, 100]:
        t = lv / 100
        lr = 0.14 + t * 0.71
        lg = 0.14 + t * 0.33
        lb = 0.20 + t * 0.14
        _v(doc, lx, legend_y, CELL, CELL,
           Quartz.CGColorCreateGenericRGB(lr, lg, lb, 1.0), corner=3)
        lx += HSTEP
    _lbl(doc, "More", lx + 3, legend_y + 1, 30, size=9, color=dimmer)

    y = legend_y + CELL + 16

    # -- Selected Day info row (updatable on click) ----------------------
    _day_names_fmt = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    def _fmt_date_label(ds):
        try:
            dt = datetime.strptime(ds, "%Y-%m-%d")
            return f"{_day_names_fmt[dt.weekday()]} {dt.strftime('%b %d')}"
        except Exception:
            return ds

    today_detail = per_day_detail.get(today_str, {})
    info_parts = []
    for key, stats in sorted(today_detail.items()):
        lbl_name = (key.replace("_", " ").title()
                    .replace("Chatgpt", "ChatGPT").replace("Api", "API"))
        info_parts.append(f"{lbl_name} {stats['avg_pct']}%")
    initial_info = (f"{_fmt_date_label(today_str)}  \u2014  "
                    + "  \u00b7  ".join(info_parts)) if info_parts else "Click a cell to see day details"

    info_label = _lbl(doc, initial_info, PAD, y, inner_w, size=11, color=dim)
    y += 24

    # Wire click handler to update info label
    if handler:
        def _on_click(tag):
            ds = date_for_tag.get(tag, "")
            if not ds:
                return
            detail = per_day_detail.get(ds, {})
            parts = []
            for key, stats in sorted(detail.items()):
                name = (key.replace("_", " ").title()
                        .replace("Chatgpt", "ChatGPT").replace("Api", "API"))
                parts.append(f"{name} {stats['avg_pct']}%")
            text = f"{_fmt_date_label(ds)}  \u2014  "
            text += "  \u00b7  ".join(parts) if parts else "No data"
            info_label.setStringValue_(text)
        type(handler)._on_click = _on_click

    # -- Stats Cards ------------------------------------------------------
    CARD_GAP = 10
    card_w = (inner_w - CARD_GAP * 3) / 4
    cards = [
        (f"{summary['highest'][1]}%", "Highest Day", summary["highest"][0], "#D97757"),
        (f"{summary['lowest'][1]}%", "Lowest Day", summary["lowest"][0], "#74AA9C"),
        (f"{summary['avg']}%", "Daily Avg", "", "#6E40C9"),
        (f"{summary['total_hits']}x", "Hit Limit", "", "#00A0D1"),
    ]
    for i, (val, lbl, sub, clr) in enumerate(cards):
        cx = PAD + i * (card_w + CARD_GAP)
        card = _v(doc, cx, y, card_w, CARD_H, card_bg, corner=10)
        # Accent bar
        _v(card, 0, 8, 3, CARD_H - 16, _cg(clr), corner=1.5, ph=CARD_H)
        # Value
        _lbl(card, val, 12, 10, card_w - 16, size=17, weight=0.56, mono=True, ph=CARD_H)
        # Label
        _lbl(card, lbl, 12, 32, card_w - 16, size=10, color=dim, ph=CARD_H)
        # Sub-label
        if sub:
            _lbl(card, sub, 12, 44, card_w - 16, size=9, color=dimmer, ph=CARD_H)

    y += CARD_H + 24

    # -- Per-provider sections (only those with data) ---------------------
    _day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    BLABEL_W = 32
    BPCT_W = 38

    # Metric context: what the % means for each provider key
    _metric_hint = {
        "claude": "5-hour session window",
        "copilot": "rate limit",
    }

    for prov in providers:
        # Colored dot + name
        _v(doc, PAD, y + 5, 8, 8, _cg(prov["color"]), corner=4)
        _lbl(doc, prov["label"], PAD + 14, y, 250, size=14, weight=0.5)
        # Metric hint next to name
        hint = _metric_hint.get(prov["key"], "rate limit")
        _lbl(doc, hint, PAD + 14 + len(prov["label"]) * 9, y + 2, 200,
             size=10, color=dimmer)
        y += 22

        # Summary
        stxt = f"Avg {prov['avg']}%  \u00b7  Peak {prov['peak']}%"
        if prov["hits"] > 0:
            stxt += f"  \u00b7  Hit limit {prov['hits']}x"
        _lbl(doc, stxt, PAD, y, inner_w, size=11, color=dim)
        y += 18

        bar_area = inner_w - BLABEL_W - BPCT_W - 8
        accent = _cg(prov["color"])

        # Intraday 5h windows replace the 7-day chart when available
        prov_windows = today_windows.get(prov["key"], {})
        if prov_windows:
            _lbl(doc, "Today\u2019s Sessions", PAD, y, inner_w, size=11, weight=0.4, color=dim)
            y += 20
            window_labels = ["00\u201305h", "05\u201310h", "10\u201315h", "15\u201320h", "20\u201324h"]
            for widx in range(5):
                wpct = prov_windows.get(widx, 0)
                wy = y + widx * (BAR_H + BAR_GAP)
                _lbl(doc, window_labels[widx], PAD, wy + 1, BLABEL_W + 10,
                     h=BAR_H, size=9, color=dim)
                _v(doc, PAD + BLABEL_W + 10, wy, bar_area - 10, BAR_H, track_bg, corner=4)
                if wpct > 0:
                    bw = max(6, (bar_area - 10) * wpct / 100)
                    _v(doc, PAD + BLABEL_W + 10, wy, bw, BAR_H, accent, corner=4)
                _lbl(doc, f"{wpct}%", PAD + BLABEL_W + bar_area + 6, wy + 1, BPCT_W,
                     h=BAR_H, size=10, weight=0.3, color=dim, mono=True)
            y += 5 * (BAR_H + BAR_GAP) + 8
        else:
            # 7-day bar chart (fallback when no intraday data)
            day_data = {d["date"]: d["peak_pct"] for d in prov["weekly"]}
            for i in range(7):
                day = today - timedelta(days=6 - i)
                ds = day.strftime("%Y-%m-%d")
                pct = day_data.get(ds, 0)
                by = y + i * (BAR_H + BAR_GAP)

                _lbl(doc, _day_names[day.weekday()], PAD, by + 1, BLABEL_W,
                     h=BAR_H, size=10, color=dim)
                _v(doc, PAD + BLABEL_W, by, bar_area, BAR_H, track_bg, corner=4)
                if pct > 0:
                    bw = max(6, bar_area * pct / 100)
                    _v(doc, PAD + BLABEL_W, by, bw, BAR_H, accent, corner=4)
                _lbl(doc, f"{pct}%", PAD + BLABEL_W + bar_area + 6, by + 1, BPCT_W,
                     h=BAR_H, size=10, weight=0.3, color=dim, mono=True)
            y += 7 * (BAR_H + BAR_GAP) + 16

    # -- Footer -----------------------------------------------------------
    _lbl(doc, f"Data since {summary['earliest']}  \u00b7  {summary['total_days']} days tracked",
         PAD, y, inner_w, size=10, color=dimmer, align=NSTextAlignmentCenter)

    # -- Scroll to top ----------------------------------------------------
    visible_h = scroll.contentSize().height
    if doc_h > visible_h:
        clip = scroll.contentView()
        clip.scrollToPoint_((0, doc_h - visible_h))
        scroll.reflectScrolledClipView_(clip)

    win.makeKeyAndOrderFront_(None)
    NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    _show_history_window._active_win = win


# -- Display helpers -----------------------------------------------------------

def _fmt_count(n: int) -> str:
    """Format a message count compactly: 1234 -> '1.2k', 999 -> '999'."""
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def _bar(pct: int, width: int = 14) -> str:
    filled = round(pct / 100 * width)
    return "\u2588" * filled + "\u2591" * (width - filled)


def _status_icon(pct: int) -> str:
    if pct >= CRIT_THRESHOLD:
        return "\U0001f534"
    if pct >= WARN_THRESHOLD:
        return "\U0001f7e1"
    return "\U0001f7e2"


def _row_lines(row: LimitRow) -> list[str]:
    bar = _bar(row.pct)
    line1 = f"  {row.label}  {row.pct}%"
    line2 = f"  {bar}  {row.reset_str}" if row.reset_str else f"  {bar}"
    return [line1, line2]


def _provider_lines(pd: ProviderData) -> list[str]:
    sym = "\u00a5" if pd.currency == "CNY" else ("" if pd.currency == "" else "$")
    if pd.error:
        return [f"  \u26a0\ufe0f  {pd.error[:60]}"]
    # ChatGPT multi-row format (stored in pd._rows by _parse_wham_usage)
    rows = getattr(pd, "_rows", None)
    if rows:
        lines = []
        for row in rows:
            for line in _row_lines(row):
                if line:
                    lines.append(line)
        return lines
    # Standard spending / balance format
    lines = []
    if pd.pct is not None:
        bar = _bar(pd.pct)
        lines.append(f"  {sym}{pd.spent:.2f} / {sym}{pd.limit:.2f} {pd.period}")
        lines.append(f"  {bar}  {pd.pct}%")
    elif pd.balance is not None:
        lines.append(f"  {sym}{pd.balance:.2f} remaining")
    elif pd.spent is not None:
        lines.append(f"  {sym}{pd.spent:.2f} {pd.period}")
    return lines


def _mi(title: str) -> rumps.MenuItem:
    """Display-only menu item (non-clickable but visually active)."""
    item = rumps.MenuItem(title)
    item.set_callback(None)
    item._menuitem.setEnabled_(True)
    return item


def _colored_mi(title: str, color_hex: str) -> rumps.MenuItem:
    """Display-only menu item with brand-colored text."""
    item = rumps.MenuItem(title)
    item.set_callback(None)
    item._menuitem.setEnabled_(True)
    try:
        from AppKit import NSColor, NSForegroundColorAttributeName
        from Foundation import NSAttributedString
        r = int(color_hex[1:3], 16) / 255
        g = int(color_hex[3:5], 16) / 255
        b = int(color_hex[5:7], 16) / 255
        color = NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 0.75)
        astr = NSAttributedString.alloc().initWithString_attributes_(
            title, {NSForegroundColorAttributeName: color}
        )
        item._menuitem.setAttributedTitle_(astr)
    except Exception as e:
        log.debug("_colored_mi: %s", e)
    return item


def _menu_icon(filename: str, tint_hex: str | None = None, size: int = 16):
    """Load an NSImage for use in a menu item, optionally tinted."""
    try:
        from AppKit import NSImage, NSColor
        path = os.path.join(_ICON_DIR, filename)
        raw = NSImage.alloc().initWithContentsOfFile_(path)
        if not raw:
            return None
        img = raw.copy()
        img.setSize_((size, size))
        if tint_hex:
            r = int(tint_hex[1:3], 16) / 255
            g = int(tint_hex[3:5], 16) / 255
            b = int(tint_hex[5:7], 16) / 255
            color = NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 1.0)
            img.setTemplate_(True)
            if hasattr(img, "imageWithTintColor_"):
                img = img.imageWithTintColor_(color)
        return img
    except Exception as e:
        log.debug("_menu_icon %s: %s", filename, e)
        return None


def _section_header_mi(title: str, icon_filename: str | None,
                        color_hex: str, icon_tint: str | None = None) -> rumps.MenuItem:
    """Section header with brand icon and colored bold title."""
    item = rumps.MenuItem(title)
    item.set_callback(None)
    item._menuitem.setEnabled_(True)
    try:
        from AppKit import (NSColor, NSFont,
                            NSForegroundColorAttributeName, NSFontAttributeName)
        from Foundation import NSAttributedString
        r = int(color_hex[1:3], 16) / 255
        g = int(color_hex[3:5], 16) / 255
        b = int(color_hex[5:7], 16) / 255
        color = NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 1.0)
        font = NSFont.boldSystemFontOfSize_(13)
        attrs = {NSFontAttributeName: font, NSForegroundColorAttributeName: color}
        astr = NSAttributedString.alloc().initWithString_attributes_(title, attrs)
        item._menuitem.setAttributedTitle_(astr)
        if icon_filename:
            img = _menu_icon(icon_filename, tint_hex=icon_tint)
            if img:
                item._menuitem.setImage_(img)
    except Exception as e:
        log.debug("_section_header_mi: %s", e)
    return item


# -- login item helpers --------------------------------------------------------

def _script_path() -> str:
    return os.path.abspath(__file__)


def _is_login_item() -> bool:
    try:
        result = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to get the name of every login item'],
            capture_output=True, text=True, timeout=10,
        )
        return "claude_bar" in result.stdout.lower()
    except Exception:
        return False


def _add_login_item():
    import plistlib
    path = _script_path()
    plist = os.path.expanduser("~/Library/LaunchAgents/com.claudebar.plist")
    python_exe = sys.executable
    plist_data = {
        "Label": "com.claudebar",
        "ProgramArguments": [python_exe, path],
        "RunAtLoad": True,
        "KeepAlive": False,
    }
    with open(plist, "wb") as f:
        plistlib.dump(plist_data, f)
    result = subprocess.run(["launchctl", "load", plist], capture_output=True)
    if result.returncode != 0:
        log.warning("launchctl load failed: %s", result.stderr.decode(errors="replace"))


def _remove_login_item():
    plist = os.path.expanduser("~/Library/LaunchAgents/com.claudebar.plist")
    if os.path.exists(plist):
        subprocess.run(["launchctl", "unload", plist], capture_output=True)
        os.remove(plist)


# -- native macOS dialogs via osascript ----------------------------------------

def _ask_text(title: str, prompt: str, default: str = "") -> str | None:
    def _esc(s: str) -> str:
        """Escape a string for safe embedding in AppleScript double-quoted strings."""
        return s.replace("\\", "\\\\").replace('"', '\\"')
    script = (
        f'display dialog "{_esc(prompt)}" '
        f'default answer "{_esc(default)}" '
        f'with title "{_esc(title)}" '
        f'buttons {{"Cancel", "Save"}} default button "Save"'
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            return None
        out = result.stdout.strip()
        if "text returned:" in out:
            return out.split("text returned:")[-1].strip()
    except Exception:
        log.exception("_ask_text failed")
    return None


def _clipboard_text() -> str:
    try:
        result = subprocess.run(
            ["pbpaste"], capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip()
    except Exception:
        return ""


# -- notification helpers ------------------------------------------------------

def _notify(title: str, subtitle: str, message: str = ""):
    """rumps.notification wrapper -- silently swallows if the notification
    center is unavailable (e.g. missing Info.plist in dev environments)."""
    try:
        rumps.notification(title, subtitle, message)
    except Exception as e:
        log.debug("notification suppressed: %s", e)


def _show_text(title: str, text: str):
    try:
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False,
            prefix="claude_usage_raw_"
        )
        tmp.write(text)
        tmp.close()
        subprocess.Popen(["open", "-a", "TextEdit", tmp.name])
        # Schedule cleanup after 60 seconds (enough time for TextEdit to open)
        def _cleanup():
            time.sleep(60)
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
        threading.Thread(target=_cleanup, daemon=True).start()
    except Exception:
        log.exception("_show_text failed")


# -- floating panel (replaces NSMenu) ------------------------------------------

# Brand colors for each provider
_BRAND_COLORS = {
    "Claude": "#D97757",
    "ChatGPT": "#74AA9C",
    "Copilot": "#6E40C9",
    "Cursor": "#00A0D1",
}

# ObjC subclasses are defined lazily on first use so AppKit import
# doesn't crash in headless / test contexts.
_panel_classes_ready = False
_DismissablePanelClass = None
_ClickHandlerClass = None


def _ensure_panel_classes():
    """Create the ObjC subclasses exactly once."""
    global _panel_classes_ready, _DismissablePanelClass, _ClickHandlerClass
    if _panel_classes_ready:
        return
    try:
        from AppKit import NSPanel, NSObject
        import objc

        class _DismissablePanel(NSPanel):
            """Borderless panel that dismisses on Esc and focus loss."""

            def canBecomeKeyWindow(self):
                return True

            def resignKeyWindow(self):
                objc.super(_DismissablePanel, self).resignKeyWindow()
                try:
                    cb = getattr(self, '_dismiss_callback', None)
                    if callable(cb):
                        cb()
                except Exception:
                    pass

            def cancelOperation_(self, sender):
                try:
                    cb = getattr(self, '_dismiss_callback', None)
                    if callable(cb):
                        cb()
                except Exception:
                    pass

        class _ClickHandler(NSObject):
            """ObjC target for the NSStatusItem button click."""

            def togglePanel_(self, sender):
                try:
                    # Detect right-click: show fallback menu instead
                    from AppKit import NSApplication
                    evt = NSApplication.sharedApplication().currentEvent()
                    # NSEventTypeRightMouseDown=3, NSEventTypeRightMouseUp=4
                    if evt and evt.type() in (3, 4):
                        menu_fn = getattr(type(self), '_show_menu_fn', None)
                        if callable(menu_fn):
                            menu_fn()
                        return
                    cb = getattr(type(self), '_toggle_fn', None)
                    if callable(cb):
                        cb()
                except Exception:
                    log.debug("_ClickHandler.togglePanel_ error", exc_info=True)

            def refreshClicked_(self, sender):
                try:
                    cb = getattr(type(self), '_refresh_fn', None)
                    if callable(cb):
                        cb()
                except Exception:
                    log.debug("_ClickHandler.refreshClicked_ error", exc_info=True)

            def gearClicked_(self, sender):
                try:
                    get_menu = getattr(type(self), '_gear_menu_fn', None)
                    menu = get_menu() if callable(get_menu) else None
                    if menu:
                        from AppKit import NSMenu, NSApplication
                        NSMenu.popUpContextMenu_withEvent_forView_(
                            menu,
                            NSApplication.sharedApplication().currentEvent(),
                            sender,
                        )
                    else:
                        subprocess.Popen(["open", "https://claude.ai/settings/usage"])
                except Exception:
                    log.debug("_ClickHandler.gearClicked_ error", exc_info=True)

            def shareClicked_(self, sender):
                try:
                    cb = getattr(type(self), '_share_fn', None)
                    if callable(cb):
                        cb(sender)
                except Exception:
                    pass

            def copyImage_(self, sender):
                try:
                    cb = getattr(type(self), '_copy_image_fn', None)
                    if callable(cb):
                        cb()
                except Exception:
                    log.debug("_ClickHandler.copyImage_ error", exc_info=True)

            def shareOnX_(self, sender):
                try:
                    cb = getattr(type(self), '_share_on_x_fn', None)
                    if callable(cb):
                        cb()
                except Exception:
                    log.debug("_ClickHandler.shareOnX_ error", exc_info=True)

        _DismissablePanelClass = _DismissablePanel
        _ClickHandlerClass = _ClickHandler
        _panel_classes_ready = True
    except Exception:
        log.debug("_ensure_panel_classes failed", exc_info=True)


class _SharePopover:
    """Two-option share menu: Copy Image or Share on X."""

    def __init__(self, app: "ClaudeBar"):
        self.app = app

    def show(self, sender):
        """Show a context menu with share options near the share button."""
        try:
            from AppKit import NSMenu, NSMenuItem, NSFont, NSApplication

            menu = NSMenu.alloc().init()
            menu.setAutoenablesItems_(False)

            # -- Copy Image --
            item1 = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "  Copy Image", b"copyImage:", ""
            )
            item1.setEnabled_(True)
            handler = self.app._panel._handler
            if handler:
                item1.setTarget_(handler)
            menu.addItem_(item1)

            # -- Share on X --
            item2 = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "  Share on X", b"shareOnX:", ""
            )
            item2.setEnabled_(True)
            if handler:
                item2.setTarget_(handler)
            menu.addItem_(item2)

            # Pop up at mouse location
            evt = NSApplication.sharedApplication().currentEvent()
            panel_obj = self.app._panel._panel
            if evt and panel_obj:
                NSMenu.popUpContextMenu_withEvent_forView_(
                    menu, evt, panel_obj.contentView()
                )
            else:
                # Fallback: pop up at status item button
                btn = self.app._nsapp.nsstatusitem.button()
                if btn:
                    menu.popUpMenuPositioningItem_atLocation_inView_(
                        None, (0, 0), btn
                    )
        except Exception:
            log.debug("_SharePopover.show failed", exc_info=True)

    def copy_image(self):
        """Render the panel as PNG with watermark and copy to clipboard."""
        try:
            from AppKit import (
                NSBitmapImageRep, NSPasteboard, NSImage,
                NSGraphicsContext, NSColor, NSFont, NSBezierPath,
            )
            from Foundation import (
                NSMakeRect, NSMakeSize, NSAttributedString,
                NSFontAttributeName, NSForegroundColorAttributeName,
            )

            panel = self.app._panel
            if not panel or not panel._panel:
                return

            view = panel._panel.contentView()
            bounds = view.bounds()

            # Create bitmap from current view
            view.lockFocus()
            rep = NSBitmapImageRep.alloc().initWithFocusedViewRect_(bounds)
            view.unlockFocus()

            if not rep:
                return

            # Build composite image: panel content + watermark bar at bottom
            w = int(bounds.size.width)
            h = int(bounds.size.height)
            watermark_h = 24
            total_h = h + watermark_h

            img = NSImage.alloc().initWithSize_(NSMakeSize(w, total_h))
            img.lockFocus()

            # Draw panel content (shifted up by watermark height)
            rep.drawInRect_(NSMakeRect(0, watermark_h, w, h))

            # Draw watermark bar at the bottom
            NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.1, 0.1, 0.12, 1.0
            ).set()
            NSBezierPath.fillRect_(NSMakeRect(0, 0, w, watermark_h))

            attrs = {
                NSFontAttributeName: NSFont.systemFontOfSize_weight_(9, 0.3),
                NSForegroundColorAttributeName: NSColor.colorWithCalibratedRed_green_blue_alpha_(
                    0.5, 0.5, 0.55, 1.0
                ),
            }
            text = NSAttributedString.alloc().initWithString_attributes_(
                "AIQuotaBar  \u00b7  github.com/yagcioglutoprak/AIQuotaBar", attrs
            )
            text.drawAtPoint_((8, 6))

            # Capture the composite
            final_rep = NSBitmapImageRep.alloc().initWithFocusedViewRect_(
                NSMakeRect(0, 0, w, total_h)
            )
            img.unlockFocus()

            if not final_rep:
                return

            # Copy PNG to clipboard  (4 = NSBitmapImageFileTypePNG)
            png_data = final_rep.representationUsingType_properties_(4, {})
            if not png_data:
                return

            pb = NSPasteboard.generalPasteboard()
            pb.clearContents()
            pb.setData_forType_(png_data, "public.png")

            log.info("Panel screenshot copied to clipboard")
            _notify("AIQuotaBar", "Copied!", "Panel screenshot copied to clipboard")
        except Exception:
            log.debug("_SharePopover.copy_image failed", exc_info=True)

    def share_on_x(self):
        """Open X/Twitter with pre-filled usage stats."""
        try:
            parts = []
            data = self.app._last_data
            if data and data.session:
                parts.append(f"Claude {data.session.pct}%")
            for pd in self.app._provider_data:
                if pd.error:
                    continue
                if pd._rows:
                    best = max(r.pct for r in pd._rows if r.pct is not None) if pd._rows else None
                    if best is not None:
                        parts.append(f"{pd.name} {best}%")
                elif pd.pct is not None:
                    parts.append(f"{pd.name} {pd.pct}%")

            stats = " \u00b7 ".join(parts) if parts else "my AI usage"
            text = f"{stats} \u2014 tracking with AIQuotaBar"
            url = (
                "https://x.com/intent/post?text="
                + urllib.parse.quote(text)
                + "&url="
                + urllib.parse.quote("https://github.com/yagcioglutoprak/AIQuotaBar")
            )
            subprocess.Popen(["open", url])
        except Exception:
            log.debug("_SharePopover.share_on_x failed", exc_info=True)


class _UsagePanel:
    """Premium floating panel that replaces the default NSMenu dropdown."""

    PANEL_WIDTH = 340
    MAX_HEIGHT = 600
    PAD = 16
    PROGRESS_H = 6
    PROGRESS_RADIUS = 3
    DOT_SIZE = 8
    SECTION_GAP = 12
    ROW_GAP = 4

    def __init__(self, app: "ClaudeBar"):
        self._app = app
        self._panel = None           # NSPanel instance
        self._visible = False
        self._content_view = None    # The NSView inside the scroll view's document
        self._handler = None         # ObjC click handler instance
        self._scroll = None
        self._built = False

    # -- public API -----------------------------------------------------------

    @property
    def visible(self) -> bool:
        return self._visible

    def toggle(self):
        """Show or dismiss the panel."""
        if self._visible:
            self.dismiss()
        else:
            self.show()

    def show(self):
        """Build/refresh the panel and show it below the status item."""
        try:
            self._ensure_built()
            self.refresh()
            self._position_panel()
            # Fade-in animation
            self._panel.setAlphaValue_(0.0)
            self._panel.makeKeyAndOrderFront_(None)
            from AppKit import NSApplication, NSAnimationContext
            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            ctx = NSAnimationContext.currentContext()
            ctx.setDuration_(0.12)
            self._panel.animator().setAlphaValue_(1.0)
            self._visible = True
        except Exception:
            log.debug("_UsagePanel.show failed", exc_info=True)

    def dismiss(self):
        """Hide the panel with a quick fade-out."""
        try:
            if self._panel:
                from AppKit import NSAnimationContext
                ctx = NSAnimationContext.currentContext()
                ctx.setDuration_(0.08)
                self._panel.animator().setAlphaValue_(0.0)
                # Schedule orderOut after animation
                from Foundation import NSTimer
                def _hide(_timer):
                    try:
                        self._panel.orderOut_(None)
                        self._panel.setAlphaValue_(1.0)
                    except Exception:
                        pass
                NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.1, False, _hide)
        except Exception:
            if self._panel:
                self._panel.orderOut_(None)
        self._visible = False

    def refresh(self):
        """Rebuild the panel content with current data."""
        try:
            if not self._built:
                return
            self._rebuild_content()
        except Exception:
            log.debug("_UsagePanel.refresh failed", exc_info=True)

    # -- construction ---------------------------------------------------------

    def _ensure_built(self):
        """Lazily create the NSPanel and its chrome (vibrancy, scroll, etc.)."""
        if self._built:
            return
        _ensure_panel_classes()
        if _DismissablePanelClass is None:
            log.warning("Panel ObjC classes not available")
            return

        from AppKit import (
            NSVisualEffectView, NSScrollView, NSView,
            NSBackingStoreBuffered, NSApplication,
        )
        from Foundation import NSMakeRect

        # Panel: borderless, non-activating
        # styleMask: 0 = borderless
        panel = _DismissablePanelClass.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, self.PANEL_WIDTH, 200),
            0,  # borderless
            NSBackingStoreBuffered,
            False,
        )
        panel.setLevel_(3)         # NSFloatingWindowLevel
        panel.setHasShadow_(True)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(self._clear_color())
        panel.setMovableByWindowBackground_(False)
        panel.setWorksWhenModal_(True)
        panel.setHidesOnDeactivate_(False)
        panel._dismiss_callback = self.dismiss

        content = panel.contentView()
        content.setWantsLayer_(True)
        content.layer().setCornerRadius_(12)
        content.layer().setMasksToBounds_(True)

        # Vibrancy background
        blur = NSVisualEffectView.alloc().initWithFrame_(content.bounds())
        blur.setAutoresizingMask_(18)  # width + height
        blur.setBlendingMode_(0)       # behindWindow
        blur.setMaterial_(3)           # dark
        blur.setState_(1)              # active
        content.addSubview_(blur)

        # Scroll view fills the panel
        scroll = NSScrollView.alloc().initWithFrame_(content.bounds())
        scroll.setAutoresizingMask_(18)
        scroll.setHasVerticalScroller_(True)
        scroll.setDrawsBackground_(False)
        scroll.setBorderType_(0)
        content.addSubview_(scroll)

        doc = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, self.PANEL_WIDTH, 200))
        scroll.setDocumentView_(doc)

        self._panel = panel
        self._scroll = scroll
        self._content_view = doc
        self._blur = blur

        # ObjC handler
        if _ClickHandlerClass is not None:
            self._handler = _ClickHandlerClass.alloc().init()

        self._built = True

    def _clear_color(self):
        from AppKit import NSColor
        return NSColor.clearColor()

    # -- positioning ----------------------------------------------------------

    def _position_panel(self):
        """Place the panel below the status bar button, centered."""
        try:
            btn = self._app._nsapp.nsstatusitem.button()
            if not btn:
                return
            btn_win = btn.window()
            if not btn_win:
                return
            # Get button frame in screen coordinates
            btn_frame = btn.frame()
            screen_rect = btn_win.convertRectToScreen_(btn_frame)

            # Panel top-center aligns with button bottom-center
            panel_x = screen_rect.origin.x + screen_rect.size.width / 2 - self.PANEL_WIDTH / 2
            panel_y = screen_rect.origin.y - self._panel.frame().size.height - 4

            # Clamp to screen
            from AppKit import NSScreen
            screen = NSScreen.mainScreen()
            if screen:
                sf = screen.visibleFrame()
                panel_x = max(sf.origin.x + 4, min(panel_x, sf.origin.x + sf.size.width - self.PANEL_WIDTH - 4))
                panel_y = max(sf.origin.y + 4, panel_y)

            from Foundation import NSMakeRect
            new_frame = NSMakeRect(
                panel_x, panel_y,
                self.PANEL_WIDTH,
                self._panel.frame().size.height,
            )
            self._panel.setFrame_display_(new_frame, True)
        except Exception:
            log.debug("_position_panel failed", exc_info=True)

    # -- content rebuild ------------------------------------------------------

    def _rebuild_content(self):
        """Tear down and rebuild all subviews in the document view."""
        from AppKit import (
            NSView, NSTextField, NSFont, NSColor, NSButton,
            NSTextAlignmentLeft, NSTextAlignmentRight,
        )
        from Foundation import NSMakeRect
        import Quartz

        doc = self._content_view
        # Remove all existing subviews
        for sv in list(doc.subviews()):
            sv.removeFromSuperview()

        W = self.PANEL_WIDTH
        PAD = self.PAD
        inner = W - PAD * 2

        # We build top-down, tracking y offset from top.
        # At the end we set the doc height and convert to bottom-up coords.
        elements = []  # list of (top_y, builder_fn) -- deferred so we know total height

        # We'll accumulate content height
        y = PAD  # start below top edge

        # ── Header ──────────────────────────────────────────────────────
        header_h = 24
        elements.append(('header', y, header_h))
        y += header_h + 8

        # ── Separator ───────────────────────────────────────────────────
        elements.append(('sep', y, 1))
        y += 1 + self.SECTION_GAP

        # ── Provider sections ───────────────────────────────────────────
        data = self._app._last_data
        provider_data = self._app._provider_data
        history = self._app._history
        history_db = self._app._history_db

        has_any_data = False

        # Claude section
        if data and any([data.session, data.weekly_all, data.weekly_sonnet]):
            has_any_data = True
            # Provider header: dot + name + reset time
            elements.append(('provider_header', y, 18, 'Claude', '#D97757',
                             data.session.reset_str if data.session else ''))
            y += 18 + 6

            # Rows
            for row in [data.session, data.weekly_all, data.weekly_sonnet]:
                if row:
                    elements.append(('limit_row', y, 20, row, '#D97757'))
                    y += 20 + self.ROW_GAP

            # ETA
            eta = _calc_eta_minutes(history, "claude")
            if eta is not None:
                elements.append(('eta_line', y, 14, eta))
                y += 14 + 2

            # Sparkline
            spark = _sparkline(history, "claude")
            if spark:
                elements.append(('spark_line', y, 14, spark))
                y += 14 + 2

            # Hit limit count
            try:
                hits = _get_week_limit_hits(history_db, "claude")
            except Exception:
                hits = 0
            if hits > 0:
                elements.append(('hit_line', y, 14, hits))
                y += 14 + 2

            y += self.SECTION_GAP

        # ChatGPT section
        chatgpt_pd = next((pd for pd in provider_data if pd.name == "ChatGPT"), None)
        if chatgpt_pd and not chatgpt_pd.error:
            has_any_data = True
            rows = getattr(chatgpt_pd, "_rows", None) or []
            reset_str = rows[0].reset_str if rows else ""
            elements.append(('provider_header', y, 18, 'ChatGPT', '#74AA9C', reset_str))
            y += 18 + 6
            for row in rows:
                elements.append(('limit_row', y, 20, row, '#74AA9C'))
                y += 20 + self.ROW_GAP
                hkey = f"chatgpt_{row.label.lower().replace(' ', '_')}"
                eta = _calc_eta_minutes(history, hkey)
                if eta is not None:
                    elements.append(('eta_line', y, 14, eta))
                    y += 14 + 2
            y += self.SECTION_GAP

        # Copilot section
        copilot_pd = next((pd for pd in provider_data if pd.name == "Copilot"), None)
        if copilot_pd and not copilot_pd.error:
            has_any_data = True
            reset_str = ""
            if copilot_pd.spent is not None and copilot_pd.limit:
                summary_text = f"{int(copilot_pd.spent)} / {int(copilot_pd.limit)}"
            else:
                summary_text = ""
            elements.append(('provider_header', y, 18, 'Copilot', '#6E40C9', summary_text))
            y += 18 + 6
            if copilot_pd.pct is not None:
                fake_row = LimitRow("Premium Requests", copilot_pd.pct, "")
                elements.append(('limit_row', y, 20, fake_row, '#6E40C9'))
                y += 20 + self.ROW_GAP
            eta = _calc_eta_minutes(history, "copilot")
            if eta is not None:
                elements.append(('eta_line', y, 14, eta))
                y += 14 + 2
            y += self.SECTION_GAP

        # Cursor section
        cursor_pd = next((pd for pd in provider_data if pd.name == "Cursor"), None)
        if cursor_pd and not cursor_pd.error:
            has_any_data = True
            rows = getattr(cursor_pd, "_rows", None) or []
            reset_str = rows[0].reset_str if rows else ""
            elements.append(('provider_header', y, 18, 'Cursor', '#00A0D1', reset_str))
            y += 18 + 6
            for row in rows:
                elements.append(('limit_row', y, 20, row, '#00A0D1'))
                y += 20 + self.ROW_GAP
                hkey = f"cursor_{row.label.lower().replace(' ', '_')}"
                eta = _calc_eta_minutes(history, hkey)
                if eta is not None:
                    elements.append(('eta_line', y, 14, eta))
                    y += 14 + 2
            y += self.SECTION_GAP

        # No data placeholder
        if not has_any_data:
            elements.append(('placeholder', y, 40))
            y += 40 + self.SECTION_GAP

        # ── Footer separator ────────────────────────────────────────────
        elements.append(('sep', y, 1))
        y += 1 + 8

        # ── Footer ──────────────────────────────────────────────────────
        footer_h = 20
        elements.append(('footer', y, footer_h))
        y += footer_h + PAD

        # ── Set document height and panel height ────────────────────────
        total_h = min(y, self.MAX_HEIGHT)
        doc_h = y  # full content height (may exceed panel if scrollable)

        doc.setFrame_(NSMakeRect(0, 0, W, doc_h))

        # Resize panel
        self._panel.setContentSize_((W, total_h))

        # Now render all elements (converting top_y to bottom-up NSView coords)
        for elem in elements:
            kind = elem[0]
            top_y = elem[1]
            h = elem[2]
            real_y = doc_h - top_y - h

            if kind == 'header':
                self._render_header(doc, PAD, real_y, inner, h, NSTextField, NSFont,
                                    NSColor, NSButton, NSMakeRect, NSTextAlignmentLeft)

            elif kind == 'sep':
                sep = NSView.alloc().initWithFrame_(NSMakeRect(PAD, real_y, inner, 1))
                sep.setWantsLayer_(True)
                sep.layer().setBackgroundColor_(
                    Quartz.CGColorCreateGenericRGB(1, 1, 1, 0.08)
                )
                doc.addSubview_(sep)

            elif kind == 'provider_header':
                _, _, _, name, color_hex, right_text = elem
                self._render_provider_header(
                    doc, PAD, real_y, inner, h, name, color_hex, right_text,
                    NSView, NSTextField, NSFont, NSColor, NSMakeRect,
                    NSTextAlignmentLeft, NSTextAlignmentRight, Quartz,
                )

            elif kind == 'limit_row':
                _, _, _, row, color_hex = elem
                self._render_limit_row(
                    doc, PAD, real_y, inner, h, row, color_hex,
                    NSView, NSTextField, NSFont, NSColor, NSMakeRect,
                    NSTextAlignmentLeft, NSTextAlignmentRight, Quartz,
                )

            elif kind == 'eta_line':
                _, _, _, eta_min = elem
                self._render_small_text(
                    doc, PAD, real_y, inner, h,
                    f"\u23f1 ~{_fmt_eta(eta_min)} to limit",
                    NSTextField, NSFont, NSColor, NSMakeRect,
                )

            elif kind == 'spark_line':
                _, _, _, spark_str = elem
                self._render_small_text(
                    doc, PAD, real_y, inner, h,
                    spark_str,
                    NSTextField, NSFont, NSColor, NSMakeRect,
                )

            elif kind == 'hit_line':
                _, _, _, hit_count = elem
                self._render_small_text(
                    doc, PAD, real_y, inner, h,
                    f"Hit limit {hit_count}x this week",
                    NSTextField, NSFont, NSColor, NSMakeRect,
                )

            elif kind == 'placeholder':
                self._render_small_text(
                    doc, PAD, real_y, inner, h,
                    "Waiting for data...",
                    NSTextField, NSFont, NSColor, NSMakeRect,
                    size=13, weight=0.3,
                )

            elif kind == 'footer':
                self._render_footer(doc, PAD, real_y, inner, h,
                                    NSTextField, NSFont, NSColor, NSButton,
                                    NSMakeRect, NSTextAlignmentLeft, NSTextAlignmentRight)

        # Scroll to top
        try:
            visible_h = self._scroll.contentSize().height
            if doc_h > visible_h:
                clip = self._scroll.contentView()
                clip.scrollToPoint_((0, doc_h - visible_h))
                self._scroll.reflectScrolledClipView_(clip)
        except Exception:
            pass

    # -- render helpers -------------------------------------------------------

    def _render_header(self, parent, x, y, w, h, NSTextField, NSFont,
                       NSColor, NSButton, NSMakeRect, NSTextAlignmentLeft):
        """Render: 'AIQuotaBar' title + gear + share buttons."""
        # Title
        title = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, w - 60, h))
        title.setStringValue_("AIQuotaBar")
        title.setBezeled_(False)
        title.setDrawsBackground_(False)
        title.setEditable_(False)
        title.setSelectable_(False)
        title.setAlignment_(NSTextAlignmentLeft)
        title.setFont_(NSFont.systemFontOfSize_weight_(15, 0.56))
        title.setTextColor_(NSColor.labelColor())
        parent.addSubview_(title)

        # Share button
        share_btn = NSButton.alloc().initWithFrame_(NSMakeRect(x + w - 52, y, 24, h))
        share_btn.setTitle_("\u2197")
        share_btn.setBordered_(False)
        share_btn.setFont_(NSFont.systemFontOfSize_(14))
        if self._handler:
            share_btn.setTarget_(self._handler)
            share_btn.setAction_(b"shareClicked:")
        parent.addSubview_(share_btn)

        # Gear button
        gear_btn = NSButton.alloc().initWithFrame_(NSMakeRect(x + w - 26, y, 24, h))
        gear_btn.setTitle_("\u2699")
        gear_btn.setBordered_(False)
        gear_btn.setFont_(NSFont.systemFontOfSize_(14))
        if self._handler:
            gear_btn.setTarget_(self._handler)
            gear_btn.setAction_(b"gearClicked:")
        parent.addSubview_(gear_btn)

    def _render_provider_header(self, parent, x, y, w, h, name, color_hex,
                                right_text, NSView, NSTextField, NSFont,
                                NSColor, NSMakeRect, NSTextAlignmentLeft,
                                NSTextAlignmentRight, Quartz):
        """Render: colored dot + bold provider name + right-aligned reset text."""
        # Colored dot
        dot_y = y + (h - self.DOT_SIZE) / 2
        dot = NSView.alloc().initWithFrame_(NSMakeRect(x, dot_y, self.DOT_SIZE, self.DOT_SIZE))
        dot.setWantsLayer_(True)
        hx = color_hex.lstrip("#")
        r, g, b = int(hx[0:2], 16) / 255, int(hx[2:4], 16) / 255, int(hx[4:6], 16) / 255
        dot.layer().setBackgroundColor_(Quartz.CGColorCreateGenericRGB(r, g, b, 1.0))
        dot.layer().setCornerRadius_(self.DOT_SIZE / 2)
        dot.layer().setMasksToBounds_(True)
        parent.addSubview_(dot)

        # Provider name (bold)
        name_x = x + self.DOT_SIZE + 6
        name_w = w - self.DOT_SIZE - 6 - 120
        lbl = NSTextField.alloc().initWithFrame_(NSMakeRect(name_x, y, name_w, h))
        lbl.setStringValue_(name)
        lbl.setBezeled_(False)
        lbl.setDrawsBackground_(False)
        lbl.setEditable_(False)
        lbl.setSelectable_(False)
        lbl.setAlignment_(NSTextAlignmentLeft)
        lbl.setFont_(NSFont.systemFontOfSize_weight_(13, 0.5))
        lbl.setTextColor_(NSColor.labelColor())
        parent.addSubview_(lbl)

        # Right text (reset time or count)
        if right_text:
            rt = NSTextField.alloc().initWithFrame_(NSMakeRect(x + w - 140, y, 140, h))
            rt.setStringValue_(right_text)
            rt.setBezeled_(False)
            rt.setDrawsBackground_(False)
            rt.setEditable_(False)
            rt.setSelectable_(False)
            rt.setAlignment_(NSTextAlignmentRight)
            rt.setFont_(NSFont.systemFontOfSize_(11))
            rt.setTextColor_(NSColor.secondaryLabelColor())
            parent.addSubview_(rt)

    def _render_limit_row(self, parent, x, y, w, h, row, color_hex,
                          NSView, NSTextField, NSFont, NSColor, NSMakeRect,
                          NSTextAlignmentLeft, NSTextAlignmentRight, Quartz):
        """Render: label + progress bar + pct% text."""
        label_w = 80
        pct_w = 40
        bar_x = x + label_w + 4
        bar_w = w - label_w - pct_w - 8
        bar_y = y + (h - self.PROGRESS_H) / 2

        # Label
        lbl = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, label_w, h))
        lbl.setStringValue_(row.label)
        lbl.setBezeled_(False)
        lbl.setDrawsBackground_(False)
        lbl.setEditable_(False)
        lbl.setSelectable_(False)
        lbl.setAlignment_(NSTextAlignmentLeft)
        lbl.setFont_(NSFont.systemFontOfSize_(11))
        lbl.setTextColor_(NSColor.secondaryLabelColor())
        parent.addSubview_(lbl)

        # Track (background)
        track = NSView.alloc().initWithFrame_(NSMakeRect(bar_x, bar_y, bar_w, self.PROGRESS_H))
        track.setWantsLayer_(True)
        track.layer().setBackgroundColor_(
            Quartz.CGColorCreateGenericRGB(0.15, 0.15, 0.2, 1.0)
        )
        track.layer().setCornerRadius_(self.PROGRESS_RADIUS)
        track.layer().setMasksToBounds_(True)
        parent.addSubview_(track)

        # Fill
        fill_w = max(0, bar_w * row.pct / 100)
        if fill_w > 0:
            fill = NSView.alloc().initWithFrame_(NSMakeRect(bar_x, bar_y, fill_w, self.PROGRESS_H))
            fill.setWantsLayer_(True)
            hx = color_hex.lstrip("#")
            r, g, b = int(hx[0:2], 16) / 255, int(hx[2:4], 16) / 255, int(hx[4:6], 16) / 255
            fill.layer().setBackgroundColor_(Quartz.CGColorCreateGenericRGB(r, g, b, 1.0))
            fill.layer().setCornerRadius_(self.PROGRESS_RADIUS)
            fill.layer().setMasksToBounds_(True)
            parent.addSubview_(fill)

        # Percentage text
        pct_lbl = NSTextField.alloc().initWithFrame_(NSMakeRect(x + w - pct_w, y, pct_w, h))
        pct_lbl.setStringValue_(f"{row.pct}%")
        pct_lbl.setBezeled_(False)
        pct_lbl.setDrawsBackground_(False)
        pct_lbl.setEditable_(False)
        pct_lbl.setSelectable_(False)
        pct_lbl.setAlignment_(NSTextAlignmentRight)
        pct_lbl.setFont_(NSFont.monospacedDigitSystemFontOfSize_weight_(11, 0.3))
        pct_lbl.setTextColor_(NSColor.labelColor())
        parent.addSubview_(pct_lbl)

    def _render_small_text(self, parent, x, y, w, h, text,
                           NSTextField, NSFont, NSColor, NSMakeRect,
                           size=10, weight=0.0):
        """Render a small secondary-colored text line."""
        lbl = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
        lbl.setStringValue_(text)
        lbl.setBezeled_(False)
        lbl.setDrawsBackground_(False)
        lbl.setEditable_(False)
        lbl.setSelectable_(False)
        lbl.setFont_(NSFont.systemFontOfSize_weight_(size, weight))
        lbl.setTextColor_(NSColor.secondaryLabelColor())
        parent.addSubview_(lbl)

    def _render_footer(self, parent, x, y, w, h,
                       NSTextField, NSFont, NSColor, NSButton,
                       NSMakeRect, NSTextAlignmentLeft, NSTextAlignmentRight):
        """Render: 'Updated HH:MM' left + 'Refresh' button right."""
        updated = self._app._last_updated
        if updated:
            ts = updated.strftime("%H:%M")
        else:
            ts = "--:--"
        lbl = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, w - 80, h))
        lbl.setStringValue_(f"Updated {ts}")
        lbl.setBezeled_(False)
        lbl.setDrawsBackground_(False)
        lbl.setEditable_(False)
        lbl.setSelectable_(False)
        lbl.setAlignment_(NSTextAlignmentLeft)
        lbl.setFont_(NSFont.systemFontOfSize_(10))
        lbl.setTextColor_(NSColor.tertiaryLabelColor())
        parent.addSubview_(lbl)

        # Refresh button
        btn = NSButton.alloc().initWithFrame_(NSMakeRect(x + w - 70, y, 70, h))
        btn.setTitle_("\u21bb Refresh")
        btn.setBordered_(False)
        btn.setFont_(NSFont.systemFontOfSize_(10))
        if self._handler:
            btn.setTarget_(self._handler)
            btn.setAction_(b"refreshClicked:")
        parent.addSubview_(btn)


# -- app -----------------------------------------------------------------------

class ClaudeBar(rumps.App):
    def __init__(self):
        super().__init__("\u25c6", quit_button=None)
        self.config = load_config()
        self._last_raw: dict = {}
        self._last_data: UsageData | None = None
        self._provider_data: list[ProviderData] = []
        self._warned_pcts: set[str] = set()   # track which rows we've notified
        self._prev_pcts: dict[str, int] = {}  # previous pct per row key (reset detection)
        self._auth_fail_count = 0
        self._fetching = False
        self._last_updated: datetime | None = None

        self._refresh_interval = self.config.get("refresh_interval", DEFAULT_REFRESH)
        self._cc_stats: dict | None = None   # Claude Code local stats
        self._history = _load_history()       # usage history for burn rate / sparkline
        self._pacing_alerted: set[str] = set()  # track which providers we've pacing-alerted
        self._history_db = _init_history_db()
        self._last_rollup = 0
        try:
            _rollup_daily_stats(self._history_db)
            self._last_rollup = time.time()
        except Exception:
            log.exception("startup rollup failed")

        # Thread-safe UI update queue (background thread -> main thread)
        self._ui_pending_title: str | None = None
        self._ui_pending_data: UsageData | None = None
        self._ui_lock = threading.Lock()
        self._config_lock = threading.Lock()
        self._db_lock = threading.Lock()
        self._login_item_cached: bool | None = None
        self._last_update_check = self.config.get("last_update_check", 0)

        if not _is_login_item():
            _add_login_item()

        # Floating panel (premium UI that replaces NSMenu)
        self._panel = _UsagePanel(self)
        self._share_popover = _SharePopover(self)
        self._click_handler_inst = None   # set in _deferred_welcome

        self._rebuild_menu(None)
        self._timer = rumps.Timer(self._on_timer, self._refresh_interval)
        self._timer.start()
        # Fast ticker: drains pending UI updates on the main thread (avoids AppKit crashes)
        self._ui_ticker = rumps.Timer(self._flush_ui, 0.25)
        self._ui_ticker.start()

        # Deferred startup info (runs after the run loop is active)
        self._welcome_timer = rumps.Timer(self._deferred_welcome, 2)
        self._welcome_timer.start()

        # Always try to fetch on startup -- browser JS works even without saved cookies
        atexit.register(self._shutdown)
        self._schedule_fetch()

    def _shutdown(self):
        """Clean up resources on exit."""
        try:
            self._history_db.close()
        except Exception:
            pass

    def _is_login_item_cached(self) -> bool:
        if self._login_item_cached is None:
            self._login_item_cached = _is_login_item()
        return self._login_item_cached

    # -- menu -----------------------------------------------------------------

    def _rebuild_menu(self, data: UsageData | None):
        items: list = []

        # -- CLAUDE section ---------------------------------------------------
        items.append(_section_header_mi("  Claude", "claude_icon.png", "#D97757"))

        if data is None or not any([data.session, data.weekly_all, data.weekly_sonnet]):
            items.append(_mi("  No data \u2014 click Auto-detect from Browser"))
        else:
            if data.session:
                lines = _row_lines(data.session)
                items.append(_mi(lines[0]))
                items.append(_colored_mi(lines[1], "#D97757"))
                # ETA + sparkline for Claude session
                eta = _calc_eta_minutes(self._history, "claude")
                if eta is not None:
                    items.append(_mi(f"  \u23f1 Limit in ~{_fmt_eta(eta)}"))
                spark = _sparkline(self._history, "claude")
                if spark:
                    items.append(_mi(f"  {spark}"))
                    items.append(_mi(f"  \U0001f4c8 24h usage trend"))
                try:
                    hits = _get_week_limit_hits(self._history_db, "claude")
                except Exception:
                    hits = 0
                if hits > 0:
                    items.append(_mi(f"  Hit limit {hits}x this week"))
                items.append(None)

            for row in [data.weekly_all, data.weekly_sonnet]:
                if row:
                    lines = _row_lines(row)
                    items.append(_mi(lines[0]))
                    items.append(_colored_mi(lines[1], "#D97757"))
                    items.append(None)


        # -- CHATGPT section (if detected) ------------------------------------
        chatgpt_pd = next(
            (pd for pd in self._provider_data if pd.name == "ChatGPT"), None
        )
        if chatgpt_pd:
            items.append(_section_header_mi("  ChatGPT", "chatgpt_icon_clean.png",
                                            "#74AA9C", icon_tint="#74AA9C"))
            rows = getattr(chatgpt_pd, "_rows", None)
            if rows:
                for row in rows:
                    lines = _row_lines(row)
                    items.append(_mi(lines[0]))
                    items.append(_colored_mi(lines[1], "#74AA9C"))
                    hkey = f"chatgpt_{row.label.lower().replace(' ', '_')}"
                    eta = _calc_eta_minutes(self._history, hkey)
                    if eta is not None:
                        items.append(_mi(f"  \u23f1 Limit in ~{_fmt_eta(eta)}"))
                    spark = _sparkline(self._history, hkey)
                    if spark:
                        items.append(_mi(f"  {spark}"))
                        items.append(_mi(f"  \U0001f4c8 24h usage trend"))
                    try:
                        hits = _get_week_limit_hits(self._history_db, hkey)
                    except Exception:
                        hits = 0
                    if hits > 0:
                        items.append(_mi(f"  Hit limit {hits}x this week"))
                    items.append(None)
            else:
                for line in _provider_lines(chatgpt_pd):
                    if line:
                        items.append(_mi(line))
                items.append(None)

        # -- COPILOT section (if detected) ------------------------------------
        copilot_pd = next(
            (pd for pd in self._provider_data if pd.name == "Copilot"), None
        )
        if copilot_pd:
            items.append(_section_header_mi("  GitHub Copilot", "copilot.png", "#6E40C9", icon_tint="#9B6BFF"))
            for line in _provider_lines(copilot_pd):
                if line:
                    items.append(_mi(line))
            # ETA + sparkline for Copilot
            eta = _calc_eta_minutes(self._history, "copilot")
            if eta is not None:
                items.append(_mi(f"  \u23f1 Limit in ~{_fmt_eta(eta)}"))
            spark = _sparkline(self._history, "copilot")
            if spark:
                items.append(_mi(f"  {spark}"))
                items.append(_mi(f"  \U0001f4c8 24h usage trend"))
            try:
                hits = _get_week_limit_hits(self._history_db, "copilot")
            except Exception:
                hits = 0
            if hits > 0:
                items.append(_mi(f"  Hit limit {hits}x this week"))
            items.append(None)

        # -- CURSOR section (if detected) -------------------------------------
        cursor_pd = next(
            (pd for pd in self._provider_data if pd.name == "Cursor"), None
        )
        if cursor_pd:
            items.append(_section_header_mi("  Cursor", "cursor.png", "#00A0D1", icon_tint="#00A0D1"))
            rows = getattr(cursor_pd, "_rows", None)
            if rows:
                for row in rows:
                    lines = _row_lines(row)
                    items.append(_mi(lines[0]))
                    items.append(_colored_mi(lines[1], "#00A0D1"))
                    hkey = f"cursor_{row.label.lower().replace(' ', '_')}"
                    eta = _calc_eta_minutes(self._history, hkey)
                    if eta is not None:
                        items.append(_mi(f"  \u23f1 Limit in ~{_fmt_eta(eta)}"))
                    spark = _sparkline(self._history, hkey)
                    if spark:
                        items.append(_mi(f"  {spark}"))
                        items.append(_mi(f"  \U0001f4c8 24h usage trend"))
                    try:
                        hits = _get_week_limit_hits(self._history_db, hkey)
                    except Exception:
                        hits = 0
                    if hits > 0:
                        items.append(_mi(f"  Hit limit {hits}x this week"))
                    items.append(None)
            else:
                for line in _provider_lines(cursor_pd):
                    if line:
                        items.append(_mi(line))
                items.append(None)

        # -- CLAUDE CODE section ----------------------------------------------
        if self._cc_stats:
            cc = self._cc_stats
            items.append(_section_header_mi("  Claude Code", "claude_icon.png", "#D97757"))
            if cc["today_messages"] > 0:
                items.append(_mi(
                    f"  Today     {_fmt_count(cc['today_messages'])} msgs"
                    f"  \u00b7  {cc['today_sessions']} sessions"
                ))
            wm = cc["week_messages"]
            if wm > 0:
                items.append(_mi(
                    f"  This week  {_fmt_count(wm)} msgs"
                    f"  \u00b7  {cc['week_sessions']} sessions"
                    f"  \u00b7  {_fmt_count(cc['week_tool_calls'])} tools"
                ))
            if cc.get("last_date"):
                items.append(_mi(f"  Last active  {cc['last_date']}"))
            items.append(None)

        # -- Other API providers ----------------------------------------------
        for pd in self._provider_data:
            if pd.name in ("ChatGPT", "Copilot", "Cursor"):
                continue
            items.append(_mi(f"  {pd.name}"))
            items.append(None)
            for line in _provider_lines(pd):
                if line:
                    items.append(_mi(line))
            items.append(None)

        # -- Usage History window ---------------------------------------------
        try:
            today_stats = _get_today_stats(self._history_db)
            past_keys = {r[0] for r in self._history_db.execute(
                "SELECT DISTINCT key FROM daily_stats"
            ).fetchall()}
            has_history = bool(past_keys or today_stats)
            if has_history:
                items.append(rumps.MenuItem(
                    "Usage History\u2026", callback=self._open_history_window,
                ))
                items.append(None)
        except Exception:
            log.exception("Usage History menu item failed")

        # -- Footer -----------------------------------------------------------
        if self._last_updated:
            t = self._last_updated.strftime("%H:%M")
            items.append(_mi(f"  Updated {t}"))
            items.append(None)

        # -- Actions ----------------------------------------------------------
        items.append(rumps.MenuItem("Refresh Now", callback=self._do_refresh))
        items.append(rumps.MenuItem("Open claude.ai/settings/usage", callback=self._open_usage_page))
        items.append(rumps.MenuItem("Share on X / Twitter\u2026", callback=self._share_on_x))
        items.append(rumps.MenuItem("\u2b50 Star on GitHub", callback=self._open_github))
        items.append(None)

        # Status bar display submenu
        bar_menu = rumps.MenuItem("Status Bar")
        chosen = self.config.get("bar_providers") or []
        # In auto mode, compute which providers would be shown
        if not chosen:
            available_names = {"Claude"} if self._last_data else set()
            for pd in self._provider_data:
                if self._provider_bar_pct(pd) is not None:
                    available_names.add(pd.name)
            auto_shown = [n for n in self._BAR_PRIORITY if n in available_names][:2]
        else:
            auto_shown = []
        self._bar_toggle_views = {}
        for name in self._BAR_PRIORITY:
            is_on = name in chosen if chosen else name in auto_shown
            item = self._make_sticky_toggle(name, is_on, name)
            bar_menu.add(item)
        bar_menu.add(None)
        is_auto = not chosen
        auto_item = rumps.MenuItem(
            "\u2713 Auto (top 2 active)" if is_auto else "Reset to Auto",
            callback=self._bar_reset_auto,
        )
        bar_menu.add(auto_item)
        items.append(bar_menu)

        # Refresh interval submenu
        interval_menu = rumps.MenuItem("Refresh Interval")
        for label, secs in REFRESH_INTERVALS.items():
            item = rumps.MenuItem(label, callback=self._make_interval_cb(secs, label))
            item._menuitem.setState_(1 if secs == self._refresh_interval else 0)
            interval_menu.add(item)
        items.append(interval_menu)

        # Notifications submenu
        notif_menu = rumps.MenuItem("Notifications")
        _notif_labels = [
            ("claude_warning",  "Claude \u2014 usage warnings (80% / 95%)"),
            ("claude_reset",    "Claude \u2014 reset alerts"),
            ("claude_pacing",   "Claude \u2014 pacing alert (ETA < 30 min)"),
            ("chatgpt_warning", "ChatGPT \u2014 usage warnings (80% / 95%)"),
            ("chatgpt_reset",   "ChatGPT \u2014 reset alerts"),
            ("chatgpt_pacing",  "ChatGPT \u2014 pacing alert (ETA < 30 min)"),
            ("copilot_pacing",  "Copilot \u2014 pacing alert (ETA < 30 min)"),
            ("cursor_warning",  "Cursor \u2014 usage warnings (80% / 95%)"),
            ("cursor_pacing",   "Cursor \u2014 pacing alert (ETA < 30 min)"),
        ]
        for nkey, nlabel in _notif_labels:
            item = rumps.MenuItem(nlabel, callback=self._make_notif_toggle_cb(nkey))
            item._menuitem.setState_(1 if notif_enabled(self.config, nkey) else 0)
            notif_menu.add(item)
        items.append(notif_menu)
        items.append(None)

        # API providers submenu
        providers_menu = rumps.MenuItem("API Providers")
        for cfg_key, (name, _) in PROVIDER_REGISTRY.items():
            is_set = bool(self.config.get(cfg_key))
            if cfg_key in COOKIE_PROVIDERS:
                label = f"{'\u2713' if is_set else '+'} {name} (auto-detect)"
            else:
                label = f"{'\u2713' if is_set else '+'} {name} API Key\u2026"
            providers_menu.add(rumps.MenuItem(
                label, callback=self._make_provider_key_cb(cfg_key, name)
            ))
        items.append(providers_menu)

        items.append(None)
        items.append(rumps.MenuItem("Auto-detect from Browser", callback=self._auto_detect_menu))
        items.append(rumps.MenuItem("Set Session Cookie\u2026", callback=self._set_cookie))
        items.append(rumps.MenuItem("Paste Cookie from Clipboard", callback=self._paste_cookie))
        items.append(rumps.MenuItem("Show Raw API Data\u2026", callback=self._show_raw))
        items.append(None)

        login_item = rumps.MenuItem("Launch at Login", callback=self._toggle_login_item)
        login_item._menuitem.setState_(1 if self._is_login_item_cached() else 0)
        items.append(login_item)

        # Desktop Widget submenu: enable/disable + status/setup
        widget_menu = rumps.MenuItem("Desktop Widget")
        widget_on = self.config.get("widget_enabled", True)
        enable_item = rumps.MenuItem(
            "Enable Desktop Widget", callback=self._toggle_widget_enabled,
        )
        enable_item._menuitem.setState_(1 if widget_on else 0)
        widget_menu.add(enable_item)
        widget_menu.add(None)
        if _is_widget_installed():
            widget_menu.add(rumps.MenuItem(
                "Open Widget Setup\u2026  \u2713  Installed",
                callback=self._open_widget_settings,
            ))
        else:
            widget_menu.add(rumps.MenuItem(
                "Install Widget\u2026  \u00b7  Not Installed",
                callback=self._install_widget_prompt,
            ))
        items.append(widget_menu)

        items.append(None)
        items.append(rumps.MenuItem("Quit", callback=rumps.quit_application))

        self.menu.clear()
        self.menu = items
        # Prevent macOS from auto-disabling display-only items
        try:
            ns_menu = self._nsapp.nsstatusitem.menu()
            if ns_menu:
                ns_menu.setAutoenablesItems_(False)
        except Exception:
            pass

    # -- thread-safe UI helpers -----------------------------------------------

    def _post_title(self, title: str):
        """Queue a title update from any thread."""
        with self._ui_lock:
            self._ui_pending_title = title

    def _post_data(self, data: UsageData):
        """Queue a full UI update (title + menu) from any thread."""
        with self._ui_lock:
            self._ui_pending_data = data

    def _flush_ui(self, _timer):
        """Main-thread ticker: apply any queued updates from background threads."""
        with self._ui_lock:
            title = self._ui_pending_title
            data = self._ui_pending_data
            self._ui_pending_title = None
            self._ui_pending_data = None
        if data is not None:
            self._apply(data)
        elif title is not None:
            self.title = title

    # -- widget ---------------------------------------------------------------

    def _deferred_welcome(self, _timer):
        """Runs once after the run loop is active, then stops itself."""
        _timer.stop()
        self._hook_status_button()
        self._check_widget_status()

    def _hook_status_button(self):
        """Replace NSMenu with panel toggle on the status item button click."""
        try:
            _ensure_panel_classes()
            if _ClickHandlerClass is None:
                log.warning("Cannot hook status button: ObjC classes unavailable")
                return

            btn = self._nsapp.nsstatusitem.button()
            if not btn:
                log.warning("Cannot hook status button: button() returned None")
                return

            # Keep original menu reference so rumps internals don't break
            self._original_menu = self._nsapp.nsstatusitem.menu()

            # Build a minimal right-click menu (fallback with Quit)
            from AppKit import NSMenu, NSMenuItem
            fallback = NSMenu.alloc().init()
            fallback.setAutoenablesItems_(False)
            quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "Quit", b"terminate:", "q"
            )
            fallback.addItem_(quit_item)

            # Remove menu from status item so clicks route to button action
            self._nsapp.nsstatusitem.setMenu_(None)

            # Set up click handler
            handler = _ClickHandlerClass.alloc().init()
            type(handler)._toggle_fn = lambda: self._panel.toggle()
            type(handler)._refresh_fn = lambda: self._do_refresh(None)
            type(handler)._share_fn = lambda sender=None: self._share_popover.show(sender)
            type(handler)._copy_image_fn = lambda: self._share_popover.copy_image()
            type(handler)._share_on_x_fn = lambda: self._share_popover.share_on_x()
            type(handler)._show_menu_fn = lambda: self._show_fallback_menu()
            type(handler)._gear_menu_fn = lambda: self._get_settings_menu()
            self._click_handler_inst = handler

            btn.setTarget_(handler)
            btn.setAction_(b"togglePanel:")

            # Accept both left and right mouse up so we can detect right-click
            # NSLeftMouseUpMask=4, NSRightMouseUpMask=16
            btn.sendActionOn_(4 | 16)

            log.info("Status button hooked for panel toggle")
        except Exception:
            log.debug("_hook_status_button failed", exc_info=True)

    def _show_fallback_menu(self):
        """Show the full NSMenu on right-click (fallback for settings/quit)."""
        try:
            # Dismiss the panel first if visible
            if self._panel and self._panel.visible:
                self._panel.dismiss()

            # Use the current rumps-managed menu (always up to date from _rebuild_menu)
            ns_menu = self._menu._menu if hasattr(self._menu, '_menu') else None
            if ns_menu is None:
                ns_menu = getattr(self, '_original_menu', None)
            if ns_menu:
                self._nsapp.nsstatusitem.popUpStatusItemMenu_(ns_menu)
        except Exception:
            log.debug("_show_fallback_menu failed", exc_info=True)

    def _get_settings_menu(self):
        """Return the full NSMenu for use as a settings popover from the gear button."""
        try:
            ns_menu = self._menu._menu if hasattr(self._menu, '_menu') else None
            if ns_menu is None:
                ns_menu = getattr(self, '_original_menu', None)
            return ns_menu
        except Exception:
            log.debug("_get_settings_menu failed", exc_info=True)
            return None

    def _check_widget_status(self):
        """Show startup info about what the app is doing."""
        seen_welcome = self.config.get("seen_welcome", False)
        widget_ok = _is_widget_installed()

        if not seen_welcome:
            # First launch -- show native welcome window with GIF
            gif_path = os.path.join(_ICON_DIR, "widget_info.gif")
            if os.path.isfile(gif_path):
                _show_welcome_window(gif_path, widget_ok)
            else:
                # Fallback to notification if GIF missing
                rumps.notification(
                    title="Welcome to AIQuotaBar",
                    subtitle="Monitoring Claude + ChatGPT usage",
                    message="Click the diamond in your menu bar to get started.",
                    sound=True,
                )
            self.config["seen_welcome"] = True
            save_config(self.config)
        else:
            # Subsequent launches -- brief notification
            if widget_ok:
                rumps.notification(
                    title="AIQuotaBar",
                    subtitle="Running",
                    message="Menu bar and desktop widget are synced.",
                    sound=False,
                )
            else:
                rumps.notification(
                    title="AIQuotaBar",
                    subtitle="Running",
                    message=(
                        "Tracking usage from your menu bar. "
                        "A desktop widget is also available \u2014 check the menu."
                    ),
                    sound=False,
                )

    def _toggle_widget_enabled(self, sender):
        """Enable/disable the desktop widget integration.

        When disabled we stop nudging the host app on every refresh, so its
        onboarding window never reappears in the background. We also quit any
        running host instance so the window goes away immediately. Re-enabling
        resumes the background widget refreshes.
        """
        enabled = not self.config.get("widget_enabled", True)
        self.config["widget_enabled"] = enabled
        save_config(self.config)
        sender._menuitem.setState_(1 if enabled else 0)
        if not enabled:
            subprocess.Popen(
                ["osascript", "-e", 'quit app "AIQuotaBarHost"'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        elif self._last_data:
            # Re-enabled: push a fresh cache + reload right away.
            _write_widget_cache(
                self._last_data, self._provider_data, self._cc_stats, self.config,
            )

    def _open_widget_settings(self, _sender):
        """Open the widget host app (shows add-widget instructions)."""
        subprocess.Popen(
            ["open", "-a", "AIQuotaBarHost"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def _install_widget_prompt(self, _sender):
        """Show instructions for building/installing the widget."""
        widget_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "AIQuotaBarWidget"
        )
        if os.path.isdir(widget_dir):
            build_script = os.path.join(widget_dir, "build_widget.sh")
            if os.path.isfile(build_script):
                rumps.alert(
                    title="Install Desktop Widget",
                    message=(
                        "To install the desktop widget, run in Terminal:\n\n"
                        f"cd {widget_dir}\n"
                        "./build_widget.sh\n\n"
                        "Then right-click desktop \u2192 Edit Widgets \u2192 search 'AI Quota'."
                    ),
                )
                return
        rumps.alert(
            title="Desktop Widget Not Found",
            message=(
                "The widget project was not found.\n\n"
                "Make sure the AIQuotaBarWidget folder exists "
                "in the AIQuotaBar directory."
            ),
        )

    # -- fetch ----------------------------------------------------------------

    def _on_timer(self, _timer):
        self._schedule_fetch()

    def _schedule_fetch(self):
        with self._ui_lock:
            if self._fetching:
                return
            self._fetching = True
        threading.Thread(target=self._fetch_and_update, daemon=True).start()

    def _fetch_and_update(self):

        try:
            with self._config_lock:
                sk = self.config.get("cookie_str")
            if not sk:
                sk = _auto_detect_cookies()
                if sk:
                    with self._config_lock:
                        self.config["cookie_str"] = sk
                        save_config(self.config)
            if not sk:
                self._post_title("\u25c6")
                self._fetching = False
                return
            raw = fetch_raw(sk)
            self._last_raw = raw
            self._auth_fail_count = 0
            data = parse_usage(raw)
            self._last_data = data
            self._last_updated = datetime.now()
            log.debug("parsed UsageData: %s", data)
            self._check_warnings(data)
            self._fetch_providers()
            self._check_provider_warnings(self._provider_data)
            self._cc_stats = fetch_claude_code_stats()

            # -- record usage history --
            if data.session:
                _append_history(self._history, "claude", data.session.pct)
            # Per-row history for multi-limit providers (avoids mixing
            # different limit types which made ETAs jump around).
            for prefix, pname in [("chatgpt", "ChatGPT"), ("cursor", "Cursor")]:
                pd = next((p for p in self._provider_data if p.name == pname), None)
                if pd and not pd.error:
                    rows = getattr(pd, "_rows", None)
                    if rows:
                        for row in rows:
                            hkey = f"{prefix}_{row.label.lower().replace(' ', '_')}"
                            _append_history(self._history, hkey, row.pct)
            copilot_pd = next(
                (pd for pd in self._provider_data if pd.name == "Copilot"), None
            )
            if copilot_pd and not copilot_pd.error and copilot_pd.pct is not None:
                _append_history(self._history, "copilot", copilot_pd.pct)
            _save_history(self._history)

            # -- record to SQLite history --
            try:
                with self._db_lock:
                    if data.session:
                        _record_sample(self._history_db, "claude", data.session.pct)
                    for prefix, pname in [("chatgpt", "ChatGPT"), ("cursor", "Cursor")]:
                        pd = next((p for p in self._provider_data if p.name == pname), None)
                        if pd and not pd.error:
                            rows = getattr(pd, "_rows", None)
                            if rows:
                                for row in rows:
                                    hkey = f"{prefix}_{row.label.lower().replace(' ', '_')}"
                                    _record_sample(self._history_db, hkey, row.pct)
                    if copilot_pd and not copilot_pd.error and copilot_pd.pct is not None:
                        _record_sample(self._history_db, "copilot", copilot_pd.pct)
                    self._history_db.commit()
                    # Periodic rollup (every hour)
                    if time.time() - self._last_rollup > 3600:
                        _rollup_daily_stats(self._history_db)
                        self._last_rollup = time.time()
            except Exception:
                log.exception("SQLite history recording failed")

            self._check_pacing_alerts()

            self._post_data(data)          # <- main thread applies title + menu
            _write_widget_cache(data, self._provider_data, self._cc_stats, self.config)

            # -- silent auto-update --
            if time.time() - self._last_update_check > UPDATE_CHECK_INTERVAL:
                self._last_update_check = time.time()
                with self._config_lock:
                    self.config["last_update_check"] = self._last_update_check
                    save_config(self.config)
                if _check_and_apply_update():
                    _restart_app()
        except CurlHTTPError as e:
            resp = getattr(e, "response", None)
            code = getattr(resp, "status_code", 0) or 0
            log.error("HTTP error: %s (status=%s)", e, code, exc_info=True)
            if code in (401, 403):
                self._auth_fail_count += 1
                self._post_title("\u25c6 !")
                if self._auth_fail_count >= 2:
                    self._auth_fail_count = 0
                    cookie_str = _auto_detect_cookies()
                    if cookie_str:
                        with self._config_lock:
                            self.config["cookie_str"] = cookie_str
                            save_config(self.config)
                        self._warned_pcts.clear()
                        log.info("Auth failed \u2014 auto-detected fresh cookies from browser")
                        self._schedule_fetch()
                    else:
                        _notify(
                            "Claude Usage Bar",
                            "Session expired \u2014 please update your cookie",
                            "Click: Set Session Cookie\u2026 or Auto-detect from Browser",
                        )
            else:
                self._post_title("\u25c6 err")
        except Exception:
            log.exception("fetch failed")
            self._post_title("\u25c6 ?")
        finally:
            self._fetching = False

    def _check_warnings(self, data: UsageData):
        """Send macOS notification when a Claude limit crosses a threshold or resets."""
        rows = [
            (data.session,       "session"),
            (data.weekly_all,    "weekly_all"),
            (data.weekly_sonnet, "weekly_sonnet"),
        ]
        warn_enabled = notif_enabled(self.config, "claude_warning")
        reset_enabled = notif_enabled(self.config, "claude_reset")

        for row, key in rows:
            if row is None:
                continue
            warn_key = f"{key}_{WARN_THRESHOLD}"
            crit_key = f"{key}_{CRIT_THRESHOLD}"
            prev = self._prev_pcts.get(key)

            # Reset detection: pct dropped significantly (>=10 pp) from above-warn to below
            if (reset_enabled and prev is not None
                    and prev >= WARN_THRESHOLD and row.pct < WARN_THRESHOLD
                    and (prev - row.pct) >= 10):
                self._warned_pcts.discard(warn_key)
                self._warned_pcts.discard(crit_key)
                _notify(
                    "Claude Usage Bar \u2705",
                    f"{row.label} has reset!",
                    f"Now at {row.pct}% \u2014 you're good to go.",
                )

            if warn_enabled:
                if row.pct >= CRIT_THRESHOLD and crit_key not in self._warned_pcts:
                    self._warned_pcts.add(crit_key)
                    _notify(
                        "Claude Usage Bar \U0001f534",
                        f"{row.label} is at {row.pct}%!",
                        row.reset_str or "Limit almost reached",
                    )
                elif row.pct >= WARN_THRESHOLD and warn_key not in self._warned_pcts:
                    self._warned_pcts.add(warn_key)
                    _notify(
                        "Claude Usage Bar \U0001f7e1",
                        f"{row.label} is at {row.pct}%",
                        row.reset_str or "Approaching limit",
                    )
                elif row.pct < WARN_THRESHOLD:
                    self._warned_pcts.discard(warn_key)
                    self._warned_pcts.discard(crit_key)

            self._prev_pcts[key] = row.pct

    def _check_provider_warnings(self, provider_data: list):
        """Send macOS notification when provider rate limits cross a threshold or reset."""
        _warn_providers = [
            ("ChatGPT", "chatgpt", "chatgpt_warning", "chatgpt_reset"),
            ("Cursor",  "cursor",  "cursor_warning",  None),
        ]
        for pname, prefix, warn_nkey, reset_nkey in _warn_providers:
            pd = next((p for p in provider_data if p.name == pname), None)
            if pd is None or pd.error:
                continue

            rows = getattr(pd, "_rows", None) or []
            warn_enabled = notif_enabled(self.config, warn_nkey)
            reset_enabled = notif_enabled(self.config, reset_nkey) if reset_nkey else False

            for row in rows:
                key = f"{prefix}_{row.label}"
                warn_key = f"{key}_{WARN_THRESHOLD}"
                crit_key = f"{key}_{CRIT_THRESHOLD}"
                prev = self._prev_pcts.get(key)

                if (reset_enabled and prev is not None
                        and prev >= WARN_THRESHOLD and row.pct < WARN_THRESHOLD
                        and (prev - row.pct) >= 10):
                    self._warned_pcts.discard(warn_key)
                    self._warned_pcts.discard(crit_key)
                    _notify(
                        "Claude Usage Bar \u2705",
                        f"{pname} {row.label} has reset!",
                        f"Now at {row.pct}% \u2014 you're good to go.",
                    )

                if warn_enabled:
                    if row.pct >= CRIT_THRESHOLD and crit_key not in self._warned_pcts:
                        self._warned_pcts.add(crit_key)
                        _notify(
                            "Claude Usage Bar \U0001f534",
                            f"{pname} {row.label} is at {row.pct}%!",
                            row.reset_str or "Limit almost reached",
                        )
                    elif row.pct >= WARN_THRESHOLD and warn_key not in self._warned_pcts:
                        self._warned_pcts.add(warn_key)
                        _notify(
                            "Claude Usage Bar \U0001f7e1",
                            f"{pname} {row.label} is at {row.pct}%",
                            row.reset_str or "Approaching limit",
                        )
                    elif row.pct < WARN_THRESHOLD:
                        self._warned_pcts.discard(warn_key)
                        self._warned_pcts.discard(crit_key)

                self._prev_pcts[key] = row.pct

    def _check_pacing_alerts(self):
        """Send predictive notification when ETA drops below PACING_ALERT_MINUTES."""
        # Static entries (single history key per provider)
        checks: list[tuple[str, str, str]] = [
            ("claude",  "claude_pacing",  "Claude session"),
            ("copilot", "copilot_pacing", "Copilot"),
        ]
        # Dynamic per-row entries for multi-limit providers
        for prefix, pname, nkey in [
            ("chatgpt", "ChatGPT", "chatgpt_pacing"),
            ("cursor",  "Cursor",  "cursor_pacing"),
        ]:
            pd = next((p for p in self._provider_data if p.name == pname), None)
            if pd and not pd.error:
                rows = getattr(pd, "_rows", None) or []
                for row in rows:
                    hkey = f"{prefix}_{row.label.lower().replace(' ', '_')}"
                    checks.append((hkey, nkey, f"{pname} {row.label}"))

        for hkey, nkey, label in checks:
            if not notif_enabled(self.config, nkey):
                continue
            eta = _calc_eta_minutes(self._history, hkey)
            if eta is not None and eta <= PACING_ALERT_MINUTES:
                if hkey not in self._pacing_alerted:
                    self._pacing_alerted.add(hkey)
                    _notify(
                        "Claude Usage Bar \u23f1",
                        f"Slow down \u2014 {label} limit in ~{_fmt_eta(eta)}",
                        "At your current pace you'll hit the cap soon.",
                    )
            else:
                self._pacing_alerted.discard(hkey)

    # Bar icon/color config per provider name
    _BAR_PROVIDERS = {
        "Claude":  {"icon": "claude_icon.png",        "tint": None,      "color": "#D97757", "sym": "\u25cf"},
        "ChatGPT": {"icon": "chatgpt_icon_clean.png", "tint": "#74AA9C", "color": "#74AA9C", "sym": "\u25c7"},
        "Cursor":  {"icon": "cursor.png",             "tint": "#6699FF", "color": "#6699FF", "sym": "\u25c8"},
        "Copilot": {"icon": "copilot.png",            "tint": "#8CBFF3", "color": "#8CBFF3", "sym": "\u25c6"},
    }

    def _set_bar_title(self, provider_segments: list[tuple[str, int, str]],
                       cc_msgs: int | None = None):
        """Multi-indicator attributed title with brand logo icons.

        provider_segments: list of (provider_name, pct, extra_suffix)
          e.g. [("Claude", 36, " \u00b7"), ("ChatGPT", 12, "")]

        Falls back to colored text symbols if AppKit / icons unavailable.
        """
        try:
            from AppKit import (NSColor, NSFont,
                                NSForegroundColorAttributeName, NSFontAttributeName)
            from Foundation import NSMutableAttributedString, NSAttributedString

            def _rgb(hex_str):
                r = int(hex_str[1:3], 16) / 255
                g = int(hex_str[3:5], 16) / 255
                b = int(hex_str[5:7], 16) / 255
                return NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 1.0)

            font = NSFont.menuBarFontOfSize_(0)
            base = {NSFontAttributeName: font} if font else {}

            s = NSMutableAttributedString.alloc().initWithString_("",)

            for i, (name, pct, suffix) in enumerate(provider_segments):
                cfg = self._BAR_PROVIDERS.get(name, {})
                color_hex = cfg.get("color", "#AAAAAA")
                color = _rgb(color_hex)

                if i > 0:
                    s.appendAttributedString_(
                        NSAttributedString.alloc().initWithString_attributes_("   ", base)
                    )

                icon_file = cfg.get("icon")
                tint = cfg.get("tint")
                img = _bar_icon(icon_file, tint_hex=tint) if icon_file else None
                if img:
                    s.appendAttributedString_(_icon_astr(img, base))
                else:
                    sym = cfg.get("sym", "\u25cf")
                    seg = NSMutableAttributedString.alloc().initWithString_attributes_(f"{sym} ", base)
                    seg.addAttribute_value_range_(NSForegroundColorAttributeName, color, (0, len(sym)))
                    s.appendAttributedString_(seg)

                s.appendAttributedString_(
                    NSAttributedString.alloc().initWithString_attributes_(f" {pct}%{suffix}", base)
                )

            # -- Claude Code  diamond 3.2k --
            if cc_msgs is not None and cc_msgs > 0:
                cc_color = _rgb("#D97757")
                seg = NSMutableAttributedString.alloc().initWithString_attributes_(
                    f"   \u25c6 {_fmt_count(cc_msgs)}", base
                )
                seg.addAttribute_value_range_(NSForegroundColorAttributeName, cc_color, (3, 2))
                s.appendAttributedString_(seg)

            self._nsapp.nsstatusitem.setAttributedTitle_(s)
            return
        except Exception as e:
            log.debug("_set_bar_title failed: %s", e)
        # Plain-text fallback
        parts = []
        for name, pct, suffix in provider_segments:
            cfg = self._BAR_PROVIDERS.get(name, {})
            sym = cfg.get("sym", "\u25cf")
            parts.append(f"{sym} {pct}%{suffix}")
        if cc_msgs is not None and cc_msgs > 0:
            parts.append(f"\u25c6 {_fmt_count(cc_msgs)}")
        self.title = "  ".join(parts)

    def _provider_bar_pct(self, pd: ProviderData) -> int | None:
        """Extract a single percentage for the menu bar from a provider."""
        if pd.error:
            return None
        rows = getattr(pd, "_rows", None)
        if rows:
            return max(r.pct for r in rows)
        if pd.pct is not None:
            return pd.pct
        return None

    # Priority order for the 2 bar slots (highest first)
    _BAR_PRIORITY = ["Claude", "ChatGPT", "Cursor", "Copilot"]

    def _apply(self, data: UsageData):
        primary = data.session or data.weekly_all or data.weekly_sonnet
        if primary:
            weekly_maxed = any(
                r and r.pct >= CRIT_THRESHOLD
                for r in [data.weekly_all, data.weekly_sonnet]
            )
            extra = " \u00b7" if (weekly_maxed and primary is data.session
                             and primary.pct < CRIT_THRESHOLD) else ""

            # Collect all available segments
            available: dict[str, tuple[str, int, str]] = {}
            available["Claude"] = ("Claude", primary.pct, extra)
            for pd in self._provider_data:
                bar_pct = self._provider_bar_pct(pd)
                if bar_pct is not None:
                    available[pd.name] = (pd.name, bar_pct, "")

            # User-configured bar providers, or auto top 2 by priority
            chosen = self.config.get("bar_providers")
            if chosen:
                segments = [available[n] for n in chosen if n in available]
            else:
                segments = [available[n] for n in self._BAR_PRIORITY
                            if n in available][:2]

            # Claude Code weekly messages
            cc_msgs: int | None = None
            if self._cc_stats:
                cc_msgs = self._cc_stats.get("week_messages")

            self._set_bar_title(segments, cc_msgs=cc_msgs)
        else:
            self.title = "\u25c6"
        self._rebuild_menu(data)
        # Refresh the floating panel if it's currently visible
        try:
            if self._panel and self._panel.visible:
                self._panel.refresh()
        except Exception:
            log.debug("panel refresh in _apply failed", exc_info=True)

    def _fetch_providers(self):
        """Fetch all configured third-party API providers (sync, called from fetch thread)."""
        # Auto-detect ChatGPT cookies if not saved yet
        _cookie_detectors = {
            "chatgpt_cookies": _auto_detect_chatgpt_cookies,
            "copilot_cookies": _auto_detect_copilot_cookies,
            "cursor_cookies":  _auto_detect_cursor_cookies,
        }
        for cfg_key in COOKIE_PROVIDERS:
            with self._config_lock:
                has_key = bool(self.config.get(cfg_key))
            if not has_key:
                detect_fn = _cookie_detectors.get(cfg_key)
                if detect_fn:
                    ck = detect_fn()
                    if ck:
                        with self._config_lock:
                            self.config[cfg_key] = ck
                            save_config(self.config)

        with self._config_lock:
            keys_snapshot = {k: self.config.get(k) for k in PROVIDER_REGISTRY}
        tasks = []
        for cfg_key, (name, fetch_fn) in PROVIDER_REGISTRY.items():
            key = keys_snapshot.get(cfg_key)
            if key:
                tasks.append((fetch_fn, key))
        if tasks:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            results = []
            with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
                futures = {pool.submit(fn, k): (fn, k) for fn, k in tasks}
                for future in as_completed(futures):
                    try:
                        results.append(future.result())
                    except Exception:
                        log.exception("provider fetch failed")
            self._provider_data = results
        else:
            self._provider_data = []

    # -- callbacks ------------------------------------------------------------

    def _do_refresh(self, _sender):
        self._schedule_fetch()

    def _open_usage_page(self, _sender):
        subprocess.Popen(["open", "https://claude.ai/settings/usage"])

    def _open_history_window(self, _sender):
        try:
            _show_history_window(self._history_db)
        except Exception:
            log.exception("Failed to open history window")

    def _open_github(self, _sender):
        subprocess.Popen(["open", "https://github.com/yagcioglutoprak/AIQuotaBar"])

    def _share_on_x(self, _sender):
        data = self._last_data
        if data and data.session:
            pct = int(data.session.pct)
            icon = _status_icon(pct)
            text = (
                f"I'm at {pct}% of my Claude session limit {icon}\n"
                f"Tracking Claude + ChatGPT + Cursor usage live in my macOS menu bar "
                f"\u2014 zero setup, auto-detects from browser\n"
                f"github.com/yagcioglutoprak/AIQuotaBar"
            )
        else:
            text = (
                "Track Claude + ChatGPT + Cursor usage live in your macOS menu bar "
                "\u2014 zero setup, auto-detects from browser\n"
                "github.com/yagcioglutoprak/AIQuotaBar"
            )
        url = "https://x.com/intent/post?text=" + urllib.parse.quote(text)
        subprocess.Popen(["open", url])

    def _make_provider_key_cb(self, cfg_key: str, name: str):
        def _cb(_sender):
            if cfg_key in COOKIE_PROVIDERS:
                # Cookie-based: re-run auto-detect
                _detectors = {
                    "chatgpt_cookies": _auto_detect_chatgpt_cookies,
                    "copilot_cookies": _auto_detect_copilot_cookies,
                    "cursor_cookies":  _auto_detect_cursor_cookies,
                }
                detect_fn = _detectors.get(cfg_key)
                if detect_fn:
                    ck = detect_fn()
                    if ck:
                        self.config[cfg_key] = ck
                        save_config(self.config)
                        _notify("Claude Usage Bar", f"{name} cookies updated \u2713", "Fetching usage\u2026")
                        self._schedule_fetch()
                    else:
                        _notify("Claude Usage Bar", f"Could not find {name} session",
                                f"Make sure you are logged into {name} in your browser.")
                return
            # API key-based
            current = self.config.get(cfg_key, "")
            key = _ask_text(
                title=f"Claude Usage Bar \u2014 {name}",
                prompt=f"Paste your {name} API key.\nLeave blank to remove.",
                default=current,
            )
            if key is None:
                return
            if key.strip():
                self.config[cfg_key] = key.strip()
            else:
                self.config.pop(cfg_key, None)
            save_config(self.config)
            self._schedule_fetch()
        return _cb

    def _make_notif_toggle_cb(self, nkey: str):
        def _cb(sender):
            current = notif_enabled(self.config, nkey)
            set_notif(self.config, nkey, not current)
            sender._menuitem.setState_(0 if current else 1)
        return _cb

    def _make_interval_cb(self, secs: int, label: str):
        def _cb(_sender):
            self._refresh_interval = secs
            self.config["refresh_interval"] = secs
            save_config(self.config)
            self._timer.stop()
            self._timer = rumps.Timer(self._on_timer, secs)
            self._timer.start()
            self._rebuild_menu(self._last_data)
        return _cb

    _TOGGLE_ICONS = {
        "Claude":  ("claude_icon.png",        None),
        "ChatGPT": ("chatgpt_icon_clean.png", "#74AA9C"),
        "Cursor":  ("cursor.png",             "#6699FF"),
        "Copilot": ("copilot.png",            "#8CBFF3"),
    }

    def _make_sticky_toggle(self, display_name: str, is_on: bool, name: str):
        """Create a menu item that stays open on click (custom NSView) with a real icon."""
        item = rumps.MenuItem("")

        if _HAS_TOGGLE_VIEW:
            from AppKit import NSImageView

            view_w, view_h = 220, 22
            check_w = 22          # space for checkmark
            icon_sz = 16
            icon_pad = 4
            label_x = check_w + icon_sz + icon_pad + 4

            view = _BarToggleView.alloc().initWithFrame_(NSMakeRect(0, 0, view_w, view_h))

            # Checkmark label
            check = NSTextField.labelWithString_("\u2713" if is_on else "")
            check.setFont_(NSFont.menuFontOfSize_(14))
            check.setFrame_(NSMakeRect(6, 1, check_w - 4, view_h - 2))
            check.setBezeled_(False)
            check.setDrawsBackground_(False)
            check.setEditable_(False)
            check.setSelectable_(False)
            view.addSubview_(check)

            # Real icon
            icon_file, icon_tint = self._TOGGLE_ICONS.get(name, (None, None))
            if icon_file:
                img = _menu_icon(icon_file, tint_hex=icon_tint, size=icon_sz)
                if img:
                    iv = NSImageView.alloc().initWithFrame_(
                        NSMakeRect(check_w, (view_h - icon_sz) / 2, icon_sz, icon_sz)
                    )
                    iv.setImage_(img)
                    view.addSubview_(iv)

            # Provider name label
            label = NSTextField.labelWithString_(display_name)
            label.setFont_(NSFont.menuFontOfSize_(14))
            label.setFrame_(NSMakeRect(label_x, 1, view_w - label_x - 4, view_h - 2))
            label.setBezeled_(False)
            label.setDrawsBackground_(False)
            label.setEditable_(False)
            label.setSelectable_(False)
            view.addSubview_(label)

            view._label = label
            view._check = check
            self._bar_toggle_views[name] = (view, label, check)

            def _make_action(n):
                def _action():
                    self._do_bar_toggle(n)
                return _action

            view._action = _make_action(name)
            item._menuitem.setView_(view)
        else:
            # Fallback: standard menu item (will close on click)
            item = rumps.MenuItem(display_name, callback=lambda _: self._do_bar_toggle(name))
            item._menuitem.setState_(1 if is_on else 0)
            icon_file, icon_tint = self._TOGGLE_ICONS.get(name, (None, None))
            if icon_file:
                img = _menu_icon(icon_file, tint_hex=icon_tint, size=16)
                if img:
                    item._menuitem.setImage_(img)

        return item

    def _do_bar_toggle(self, name: str):
        """Toggle a provider in the status bar and update views in-place."""
        chosen = self.config.get("bar_providers")
        if not chosen:
            # Switching from auto -> manual: seed with current auto selection
            available_names = {"Claude"} if self._last_data else set()
            for pd in self._provider_data:
                if self._provider_bar_pct(pd) is not None:
                    available_names.add(pd.name)
            chosen = [n for n in self._BAR_PRIORITY if n in available_names][:2]
        if name in chosen:
            chosen.remove(name)
        else:
            chosen.append(name)
        # If empty after removal, go back to auto
        if not chosen:
            self.config.pop("bar_providers", None)
        else:
            self.config["bar_providers"] = chosen
        save_config(self.config)

        # Update all toggle views in-place (no menu rebuild needed)
        effective = self.config.get("bar_providers")
        if not effective:
            available_names = {"Claude"} if self._last_data else set()
            for pd in self._provider_data:
                if self._provider_bar_pct(pd) is not None:
                    available_names.add(pd.name)
            auto_shown = set(
                [n for n in self._BAR_PRIORITY if n in available_names][:2]
            )
        else:
            auto_shown = None

        for n, (view, label, check) in self._bar_toggle_views.items():
            is_on = n in effective if effective else n in auto_shown
            check.setStringValue_("\u2713" if is_on else "")

        if self._last_data:
            self._apply(self._last_data)

    def _bar_reset_auto(self, _sender):
        """Reset bar display to auto-detect (top 2 active providers)."""
        self.config.pop("bar_providers", None)
        save_config(self.config)
        self._rebuild_menu(self._last_data)
        if self._last_data:
            self._apply(self._last_data)

    def _set_cookie(self, _sender):
        key = _ask_text(
            title="Claude Usage \u2014 Set Cookies",
            prompt=(
                "Paste ALL cookies from claude.ai (needed to bypass Cloudflare)\n\n"
                "How to get them:\n"
                "  1. Open https://claude.ai/settings/usage in Chrome\n"
                "  2. F12 \u2192 Network tab \u2192 click any request to claude.ai\n"
                "  3. In Headers, find the 'cookie:' row\n"
                "  4. Right-click it \u2192 Copy value  (long string with semicolons)"
            ),
            default=self.config.get("cookie_str", ""),
        )
        if key:
            self.config["cookie_str"] = key.strip()
            save_config(self.config)
            self._warned_pcts.clear()
            self._auth_fail_count = 0
            self._schedule_fetch()

    def _paste_cookie(self, _sender):
        text = _clipboard_text()
        if not text or ("sessionKey" not in text and "=" not in text):
            _notify(
                "Claude Usage Bar",
                "Nothing useful in clipboard",
                "Copy your cookie string from Chrome DevTools first.",
            )
            return
        self.config["cookie_str"] = text
        save_config(self.config)
        self._warned_pcts.clear()
        self._auth_fail_count = 0
        self._schedule_fetch()
        _notify(
            "Claude Usage Bar",
            "Cookie updated from clipboard \u2713",
            "Fetching usage data\u2026",
        )

    def _show_raw(self, _sender):
        text = json.dumps(self._last_raw.get("usage", self._last_raw), indent=2)
        _show_text(title="Claude Usage \u2014 Raw API Response", text=text)

    def _toggle_login_item(self, sender):
        if _is_login_item():
            _remove_login_item()
            self._login_item_cached = False
            sender._menuitem.setState_(0)
            _notify("Claude Usage Bar", "Removed from Login Items", "")
        else:
            _add_login_item()
            self._login_item_cached = True
            sender._menuitem.setState_(1)
            _notify("Claude Usage Bar", "Added to Login Items", "Will launch automatically on login")

    def _try_auto_detect(self):
        """Background: silently try to grab cookies from the browser on first run."""
        cookie_str = _auto_detect_cookies()
        if cookie_str:
            self.config["cookie_str"] = cookie_str
            save_config(self.config)
            _notify(
                "Claude Usage Bar",
                "Cookies auto-detected from your browser \u2713",
                "Fetching usage data\u2026",
            )
            self._schedule_fetch()

    def _auto_detect_menu(self, _sender):
        """Menu item: manually trigger auto-detect (runs in background thread)."""
        if not _BROWSER_COOKIE3_OK:
            _notify(
                "Claude Usage Bar",
                "browser-cookie3 not installed",
                "Run: pip install browser-cookie3",
            )
            return
        # Run cookie detection in a background thread -- browser_cookie3 accesses
        # SQLite databases and Keychain which can hard-crash if called on the main thread.
        threading.Thread(target=self._do_auto_detect, daemon=True).start()

    def _do_auto_detect(self):
        """Background: detect cookies then schedule a fetch."""
        try:
            cookie_str = _auto_detect_cookies()
        except Exception:
            log.exception("_auto_detect_cookies failed")
            cookie_str = None
        if cookie_str:
            with self._config_lock:
                self.config["cookie_str"] = cookie_str
                save_config(self.config)
            self._warned_pcts.clear()
            self._auth_fail_count = 0
            _notify("Claude Usage Bar", "Cookies auto-detected \u2713", "Fetching usage data\u2026")
            self._schedule_fetch()
        else:
            _notify(
                "Claude Usage Bar",
                "Could not find claude.ai session in any browser",
                "Make sure you are logged in to claude.ai in Chrome, Firefox, or Safari.",
            )
