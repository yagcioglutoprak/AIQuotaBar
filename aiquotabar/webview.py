"""Native containers for the web UI: a WKWebView host, the menu bar panel and
regular windows (settings, history, welcome).

This is the only module that touches WebKit. It is deliberately thin: the
page (aiquotabar/web/) draws everything and posts JSON messages back; the
app decides what they mean. If WebKit can't be imported, `available()`
returns False and the app falls back to a classic text menu.
"""

from __future__ import annotations

import json
import os
import time

from aiquotabar.config import log
from aiquotabar.viewmodel import WEB_DIR, boot_assets

_classes: dict = {}
_boot_cache: dict = {}


def available() -> bool:
    """True when WebKit (pyobjc-framework-WebKit) and AppKit are importable."""
    if "ok" not in _classes:
        try:
            import WebKit  # noqa: F401
            import AppKit  # noqa: F401
            _classes["ok"] = _define_classes()
        except Exception:
            log.warning("WebKit UI unavailable — falling back to the text menu", exc_info=True)
            _classes["ok"] = False
    return _classes["ok"]


def _define_classes() -> bool:
    """Create the Objective-C subclasses exactly once per process."""
    import objc
    from AppKit import NSObject, NSPanel

    try:
        protocols = [objc.protocolNamed("WKScriptMessageHandler")]
    except Exception:
        protocols = []

    class AIQScriptBridge(NSObject, protocols=protocols):
        """Receives window.webkit.messageHandlers.aiq.postMessage(json)."""

        def userContentController_didReceiveScriptMessage_(self, controller, message):
            cb = getattr(self, "py_callback", None)
            if cb is None:
                return
            try:
                cb(message.body())
            except Exception:
                log.exception("web message handler failed")

    class AIQPanel(NSPanel):
        """Borderless panel that closes on Esc and when it loses focus."""

        def canBecomeKeyWindow(self):
            return True

        def resignKeyWindow(self):
            objc.super(AIQPanel, self).resignKeyWindow()
            cb = getattr(self, "py_dismiss", None)
            if cb:
                try:
                    cb()
                except Exception:
                    log.debug("panel dismiss failed", exc_info=True)

        def cancelOperation_(self, sender):
            cb = getattr(self, "py_dismiss", None)
            if cb:
                cb()

    class AIQWindowDelegate(NSObject):
        def windowWillClose_(self, notification):
            cb = getattr(self, "py_close", None)
            if cb:
                try:
                    cb()
                except Exception:
                    log.debug("window close callback failed", exc_info=True)

    class AIQStatusClick(NSObject):
        """Target for the status item button: left vs right (or ⌃) click."""

        def clicked_(self, sender):
            from AppKit import NSApplication
            evt = NSApplication.sharedApplication().currentEvent()
            # 3/4 = right mouse down/up; control-click counts as a right click
            right = evt is not None and (evt.type() in (3, 4) or bool(evt.modifierFlags() & (1 << 18)))
            cb = getattr(self, "py_right" if right else "py_left", None)
            if cb:
                try:
                    cb()
                except Exception:
                    log.exception("status click handler failed")

    _classes.update(bridge=AIQScriptBridge, panel=AIQPanel, delegate=AIQWindowDelegate,
                    click=AIQStatusClick)
    return True


def status_click_target(left, right):
    target = _classes["click"].alloc().init()
    target.py_left, target.py_right = left, right
    return target


def _boot_js() -> str:
    if "js" not in _boot_cache:
        _boot_cache["js"] = "AIQ.boot(%s);" % json.dumps(boot_assets())
    return _boot_cache["js"]


class WebHost:
    """One WKWebView showing one view of web/index.html plus its message bridge."""

    def __init__(self, view: str, frame, on_message):
        from Foundation import NSURL
        from WebKit import WKUserScript, WKWebView, WKWebViewConfiguration

        self.view = view
        self.ready = False
        self.created_at = time.time()
        self._pending: dict | None = None
        self._on_message = on_message

        self.bridge = _classes["bridge"].alloc().init()
        self.bridge.py_callback = self._receive

        cfg = WKWebViewConfiguration.alloc().init()
        self._ucc = cfg.userContentController()
        boot = "window.AIQ_BOOT = %s;" % json.dumps({"view": view, "native": True})
        # 0 = WKUserScriptInjectionTimeAtDocumentStart
        self._ucc.addUserScript_(
            WKUserScript.alloc().initWithSource_injectionTime_forMainFrameOnly_(boot, 0, True))
        self._ucc.addScriptMessageHandler_name_(self.bridge, "aiq")

        wv = WKWebView.alloc().initWithFrame_configuration_(frame, cfg)
        try:
            wv.setValue_forKey_(False, "drawsBackground")   # let vibrancy show through
        except Exception:
            log.debug("drawsBackground not supported", exc_info=True)
        wv.setAutoresizingMask_(18)   # width + height
        index = NSURL.fileURLWithPath_(os.path.join(WEB_DIR, "index.html"))
        wv.loadFileURL_allowingReadAccessToURL_(index, NSURL.fileURLWithPath_isDirectory_(WEB_DIR, True))
        self.webview = wv

    def _receive(self, body):
        try:
            msg = json.loads(str(body))
        except (TypeError, ValueError):
            return
        if not isinstance(msg, dict) or not isinstance(msg.get("action"), str):
            return
        if msg["action"] == "ready":
            self.ready = True
            self.eval(_boot_js())
            if self._pending is not None:
                self.push(self._pending)
        self._on_message(self, msg)

    def push(self, state: dict):
        """Render `state` (queued until the page reports ready)."""
        if not self.ready:
            self._pending = state
            return
        self._pending = None
        self.eval("AIQ.render(%s);" % json.dumps(state, ensure_ascii=True, default=str))

    def eval(self, js: str):
        try:
            self.webview.evaluateJavaScript_completionHandler_(js, None)
        except Exception:
            log.debug("evaluateJavaScript failed", exc_info=True)

    def toast(self, text: str):
        self.eval("AIQ.toast(%s);" % json.dumps(text))

    def teardown(self):
        """Break the WebKit → bridge → Python reference cycle."""
        try:
            self._ucc.removeScriptMessageHandlerForName_("aiq")
        except Exception:
            pass
        self.bridge.py_callback = None


class Panel:
    """The floating usage panel anchored under the status item."""

    WIDTH = 360
    MIN_HEIGHT = 160

    def __init__(self, status_button_fn, on_message):
        from AppKit import NSBackingStoreBuffered, NSColor, NSVisualEffectView
        from Foundation import NSMakeRect

        self._status_button = status_button_fn
        self._height = 460
        self.visible = False
        self._last_dismiss = 0.0
        self.show_when_ready = False

        panel = _classes["panel"].alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, self.WIDTH, self._height), 0, NSBackingStoreBuffered, False)
        panel.setLevel_(3)                      # floating
        panel.setHasShadow_(True)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setHidesOnDeactivate_(False)
        panel.setWorksWhenModal_(True)
        # canJoinAllSpaces | fullScreenAuxiliary: open over full-screen apps too
        panel.setCollectionBehavior_((1 << 0) | (1 << 8))
        panel.py_dismiss = self.dismiss

        content = panel.contentView()
        content.setWantsLayer_(True)
        content.layer().setCornerRadius_(12)
        content.layer().setMasksToBounds_(True)

        blur = NSVisualEffectView.alloc().initWithFrame_(content.bounds())
        blur.setAutoresizingMask_(18)
        blur.setBlendingMode_(0)                # behind window
        blur.setMaterial_(6)                    # popover: adapts to light / dark
        blur.setState_(1)                       # always active
        content.addSubview_(blur)

        self.host = WebHost("panel", content.bounds(), on_message)
        content.addSubview_(self.host.webview)
        self.window = panel

    # -- visibility -----------------------------------------------------------

    def toggle(self):
        if self.visible:
            self.dismiss()
        elif time.time() - self._last_dismiss > 0.3:
            # A click on the status item first resigns the panel (dismissing
            # it); don't let the same click reopen it.
            self.show()

    def show(self):
        if not self.host.ready:
            self.show_when_ready = True
            return
        self.show_when_ready = False
        from AppKit import NSAnimationContext, NSApplication
        self._place()
        self.window.setAlphaValue_(0.0)
        self.window.makeKeyAndOrderFront_(None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        NSAnimationContext.currentContext().setDuration_(0.12)
        self.window.animator().setAlphaValue_(1.0)
        self.visible = True

    def dismiss(self):
        if not self.visible:
            return
        self.visible = False
        self._last_dismiss = time.time()
        try:
            from AppKit import NSAnimationContext
            from Foundation import NSTimer
            NSAnimationContext.currentContext().setDuration_(0.08)
            self.window.animator().setAlphaValue_(0.0)

            def _hide(_timer):
                if not self.visible:
                    self.window.orderOut_(None)
                    self.window.setAlphaValue_(1.0)
            NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.1, False, _hide)
        except Exception:
            self.window.orderOut_(None)

    # -- geometry -------------------------------------------------------------

    def set_content_height(self, height: float):
        self._height = max(self.MIN_HEIGHT, float(height))
        if self.visible:
            self._place()

    def _place(self):
        """Top-centre the panel under the status item, clamped to the screen."""
        from AppKit import NSScreen
        from Foundation import NSMakeRect
        try:
            btn = self._status_button()
            screen_rect = btn.window().convertRectToScreen_(btn.frame())
            screen = btn.window().screen() or NSScreen.mainScreen()
            vf = screen.visibleFrame()
            height = min(self._height, vf.size.height - 16)
            x = screen_rect.origin.x + screen_rect.size.width / 2 - self.WIDTH / 2
            x = max(vf.origin.x + 6, min(x, vf.origin.x + vf.size.width - self.WIDTH - 6))
            y = screen_rect.origin.y - height - 5
            y = max(vf.origin.y + 6, y)
            self.window.setFrame_display_(NSMakeRect(x, y, self.WIDTH, height), True)
            self.window.invalidateShadow()
        except Exception:
            log.debug("panel placement failed", exc_info=True)


class Window:
    """A normal titled window hosting one web view (settings, history, welcome)."""

    def __init__(self, view: str, title: str, size: tuple[int, int], on_message, on_close,
                 resizable: bool = False):
        from AppKit import (
            NSBackingStoreBuffered, NSColor, NSWindow, NSWindowStyleMaskClosable,
            NSWindowStyleMaskMiniaturizable, NSWindowStyleMaskResizable, NSWindowStyleMaskTitled,
        )
        from Foundation import NSMakeRect

        style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable
        if resizable:
            style |= NSWindowStyleMaskResizable
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, size[0], size[1]), style, NSBackingStoreBuffered, False)
        win.setTitle_(title)
        win.setTitlebarAppearsTransparent_(True)
        win.setReleasedWhenClosed_(False)
        win.setBackgroundColor_(NSColor.windowBackgroundColor())
        if resizable:
            win.setMinSize_((size[0] - 120, 420))
        self.host = WebHost(view, win.contentView().bounds(), on_message)
        win.contentView().addSubview_(self.host.webview)
        self._delegate = _classes["delegate"].alloc().init()
        self._delegate.py_close = self._closed
        win.setDelegate_(self._delegate)
        win.center()
        self.window = win
        self.view = view
        self._on_close = on_close

    def show(self):
        from AppKit import NSApplication
        self.window.makeKeyAndOrderFront_(None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    def close(self):
        self.window.close()

    def _closed(self):
        self.host.teardown()
        self._delegate.py_close = None
        self.window.setDelegate_(None)      # the delegate may be freed after this
        self._on_close(self)


def install_edit_menu():
    """Give the app a (hidden) main menu so ⌘C / ⌘V / ⌘A work in text fields.

    Menu bar apps have no main menu by default, and AppKit routes the
    clipboard shortcuts through it — without this, pasting a key fails.
    """
    from AppKit import NSApplication, NSMenu, NSMenuItem
    app = NSApplication.sharedApplication()
    main = NSMenu.alloc().init()

    app_item = NSMenuItem.alloc().init()
    app_menu = NSMenu.alloc().initWithTitle_("AIQuotaBar")
    app_menu.addItemWithTitle_action_keyEquivalent_("Quit AIQuotaBar", "terminate:", "q")
    app_item.setSubmenu_(app_menu)
    main.addItem_(app_item)

    edit_item = NSMenuItem.alloc().init()
    edit = NSMenu.alloc().initWithTitle_("Edit")
    for title, action, key in (("Undo", "undo:", "z"), ("Redo", "redo:", "Z"), (None, None, None),
                               ("Cut", "cut:", "x"), ("Copy", "copy:", "c"),
                               ("Paste", "paste:", "v"), ("Select All", "selectAll:", "a")):
        if title is None:
            edit.addItem_(NSMenuItem.separatorItem())
        else:
            edit.addItemWithTitle_action_keyEquivalent_(title, action, key)
    edit_item.setSubmenu_(edit)
    main.addItem_(edit_item)

    window_item = NSMenuItem.alloc().init()
    window_menu = NSMenu.alloc().initWithTitle_("Window")
    window_menu.addItemWithTitle_action_keyEquivalent_("Close", "performClose:", "w")
    window_item.setSubmenu_(window_menu)
    main.addItem_(window_item)

    app.setMainMenu_(main)
