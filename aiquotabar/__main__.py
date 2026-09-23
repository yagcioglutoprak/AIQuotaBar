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
    from aiquotabar.ui import ClaudeBar
    ClaudeBar(demo=demo).run()


if __name__ == "__main__":
    main()
