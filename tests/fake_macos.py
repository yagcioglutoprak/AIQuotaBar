"""Minimal stand-ins for rumps / AppKit / Foundation / WebKit / objc.

They let the native shell (ui.py, webview.py) be imported and driven on any
OS, so tests can exercise its Python logic: fetch loop, bridge actions,
settings, history, alerts. They do not emulate Cocoa behaviour — only enough
surface for the code paths to run and for calls to be recorded.
"""

from __future__ import annotations

import builtins
import sys
import types

CALLS: list[tuple] = []          # (object repr, method, args) — for assertions


class Rect:
    def __init__(self, x=0.0, y=0.0, w=0.0, h=0.0):
        self.origin = types.SimpleNamespace(x=x, y=y)
        self.size = types.SimpleNamespace(width=w, height=h)

    def __repr__(self):
        return f"Rect({self.origin.x}, {self.origin.y}, {self.size.width}, {self.size.height})"


RETURNS = {
    "visibleFrame": lambda *a: Rect(0, 0, 1440, 875),
    "frame": lambda *a: Rect(0, 0, 80, 24),
    "bounds": lambda *a: Rect(0, 0, 360, 460),
    "convertRectToScreen_": lambda *a: Rect(1200, 875, 80, 24),
    "currentEvent": lambda *a: None,
    "length": lambda *a: 0,
    "type": lambda *a: 1,
    "modifierFlags": lambda *a: 0,
}


class Fake:
    """Accepts any call; returns another Fake (or a canned value)."""

    def __init__(self, name="fake"):
        object.__setattr__(self, "_name", name)

    def __getattr__(self, item):
        if item.startswith("__"):
            raise AttributeError(item)

        def method(*args, **kwargs):
            CALLS.append((self._name, item, args))
            if item in RETURNS:
                return RETURNS[item](*args)
            return Fake(f"{self._name}.{item}()")
        return method

    def __call__(self, *a, **k):
        return Fake(self._name + "()")

    def __bool__(self):
        return True

    def __or__(self, other):
        return 0

    __ror__ = __or__


class _ObjCMeta(type):
    def __new__(mcls, name, bases, ns, protocols=None, **kw):
        return super().__new__(mcls, name, bases, ns)

    def __init__(cls, name, bases, ns, protocols=None, **kw):
        super().__init__(name, bases, ns)

    def __getattr__(cls, item):
        if item.startswith("__"):
            raise AttributeError(item)
        return lambda *a, **k: Fake(f"{cls.__name__}.{item}()")


class NSObject(metaclass=_ObjCMeta):
    @classmethod
    def alloc(cls):
        return cls.__new__(cls)

    def init(self):
        return self

    def __getattr__(self, item):
        if item.startswith("__") or item.startswith("py_"):
            raise AttributeError(item)

        def method(*args, **kwargs):
            CALLS.append((type(self).__name__, item, args))
            if item in RETURNS:
                return RETURNS[item](*args)
            if item.startswith("initWith"):
                return self
            return Fake(f"{type(self).__name__}.{item}()")
        return method


def _module(name, **attrs):
    mod = types.ModuleType(name)

    def __getattr__(item):
        if item.startswith("__"):
            raise AttributeError(item)
        if item[:1].isupper():
            # Unknown Cocoa class/constant: a fresh NSObject subclass works for both.
            cls = _ObjCMeta(item, (NSObject,), {})
            setattr(mod, item, cls)
            return cls
        return Fake(item)
    mod.__getattr__ = __getattr__
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


# ── rumps ────────────────────────────────────────────────────────────────────

class MenuItem:
    def __init__(self, title, callback=None, key=None, **kw):
        self.title, self.callback, self.key = title, callback, key
        self.state = 0
        self.children = []

    def set_callback(self, cb):
        self.callback = cb

    def add(self, item):
        self.children.append(item)


class _Menu(list):
    def __init__(self):
        super().__init__()
        self._menu = Fake("NSMenu")

    def clear(self):
        del self[:]

    def update(self, items):
        self.extend(items)


class Timer:
    instances: list = []

    def __init__(self, callback, interval):
        self.callback, self.interval, self.running = callback, interval, False
        Timer.instances.append(self)

    def start(self):
        self.running = True

    def stop(self):
        self.running = False


class App:
    def __init__(self, name, quit_button=None, **kw):
        self.title = name
        self._menu = _Menu()
        self._nsapp = types.SimpleNamespace(nsstatusitem=Fake("NSStatusItem"))

    @property
    def menu(self):
        return self._menu

    @menu.setter
    def menu(self, items):
        self._menu.update(items)

    def run(self):
        raise RuntimeError("fake rumps cannot run a Cocoa event loop")


NOTIFICATIONS: list[tuple] = []
ALERTS: list[dict] = []


def install():
    """Register the fakes in sys.modules (idempotent)."""
    if "rumps" in sys.modules and getattr(sys.modules["rumps"], "IS_FAKE", False):
        return
    objc = _module("objc", super=builtins.super, protocolNamed=lambda name: name,
                   python_method=lambda f: f)
    appkit = _module("AppKit", NSObject=NSObject, NSMakeRect=lambda *a: Rect(*a),
                     NSBackingStoreBuffered=2, NSWindowStyleMaskTitled=1,
                     NSWindowStyleMaskClosable=2, NSWindowStyleMaskMiniaturizable=4,
                     NSWindowStyleMaskResizable=8, NSFontAttributeName="font",
                     NSForegroundColorAttributeName="color")
    foundation = _module("Foundation", NSObject=NSObject, NSMakeRect=lambda *a: Rect(*a))
    webkit = _module("WebKit")
    rumps = _module("rumps", App=App, MenuItem=MenuItem, Timer=Timer, IS_FAKE=True,
                    notification=lambda *a: NOTIFICATIONS.append(a),
                    alert=lambda **k: ALERTS.append(k),
                    quit_application=lambda *a: CALLS.append(("rumps", "quit", a)))
    sys.modules.update({"objc": objc, "AppKit": appkit, "Foundation": foundation,
                        "WebKit": webkit, "rumps": rumps, "Quartz": _module("Quartz")})
