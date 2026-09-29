"""AIQuotaBar entry point.

    python3 claude_bar.py            run the menu bar app
    python3 claude_bar.py --demo     run with sample data (no accounts, nothing saved)
    python3 claude_bar.py --history  print a 7-day usage chart in the terminal
"""

import os
import sys

_lock_fd = None


def _single_instance() -> bool:
    """Hold an exclusive lock for the app's lifetime so only one copy runs.

    The fd is non-inheritable, so the auto-updater's os.execv releases it
    and the re-executed process simply takes it again.
    """
    global _lock_fd
    import fcntl
    path = os.path.expanduser("~/.claude_bar.lock")
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return False
    _lock_fd = fd
    return True


def _set_app_name(name: str = "AIQuotaBar") -> None:
    """Show up as AIQuotaBar instead of "Python" (Activity Monitor, Force Quit).

    The app runs inside Python's own app bundle, and macOS names the process
    after that bundle's CFBundleName when it registers with the window server.
    Renaming it in the in-memory info dictionary before NSApplication starts
    changes the name it registers under. The command line is left alone, so
    `pkill -f claude_bar.py` keeps working.
    """
    try:
        from Foundation import NSBundle
        bundle = NSBundle.mainBundle()
    except Exception:
        return
    for get_info in ("infoDictionary", "localizedInfoDictionary"):
        try:
            info = getattr(bundle, get_info)()
            current = info.get("CFBundleName") if info is not None else None
            # Only rename the interpreter's bundle, never a real app bundle.
            if isinstance(current, str) and current.lower().startswith("python"):
                info["CFBundleName"] = name
        except Exception:
            pass


def main():
    args = sys.argv[1:]
    if args and args[0] in ("--history", "-H"):
        from aiquotabar.history import cli_history
        cli_history()
        return
    if args and args[0] in ("--version", "-V"):
        from aiquotabar import __version__
        print(f"AIQuotaBar {__version__}")
        return
    demo = "--demo" in args
    if not demo and not _single_instance():
        print("AIQuotaBar is already running.")
        # Exit 0 so launchd's KeepAlive (crash-only) doesn't respawn us.
        sys.exit(0)
    _set_app_name()
    from aiquotabar.ui import ClaudeBar
    ClaudeBar(demo=demo).run()


if __name__ == "__main__":
    main()
