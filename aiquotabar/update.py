"""Silent auto-update via git pull."""

import os
import subprocess
import sys
import time

from aiquotabar.config import log


def _check_and_apply_update() -> bool:
    """Silently check for updates via git and apply if available. Returns True if updated."""
    install_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if not os.path.isdir(os.path.join(install_dir, ".git")):
        return False  # Not a git install (Homebrew, dev, etc.)
    try:
        run = lambda cmd: subprocess.run(
            cmd, cwd=install_dir, capture_output=True, text=True, timeout=30
        )
        r = run(["git", "fetch", "--quiet", "origin"])
        if r.returncode != 0:
            return False
        local = run(["git", "rev-parse", "HEAD"]).stdout.strip()
        remote = run(["git", "rev-parse", "origin/main"]).stdout.strip()
        if local == remote:
            return False  # Already up to date
        # We usually run local commits AHEAD of upstream. In that case
        # origin/main is an ANCESTOR of HEAD, so `merge --ff-only` reports
        # "Already up to date" and exits 0 WITHOUT moving HEAD. Treating that
        # exit 0 as "updated" makes the app re-exec itself every interval for
        # no reason -- the spurious restart that was killing the menu bar item.
        # Bail out before stashing/merging if origin/main is already contained.
        if run(["git", "merge-base", "--is-ancestor", remote, "HEAD"]).returncode == 0:
            return False
        run(["git", "stash", "--quiet"])
        r = run(["git", "merge", "--ff-only", "origin/main", "--quiet"])
        if r.returncode != 0:
            log.warning("auto-update merge failed: %s", r.stderr)
            return False
        new_head = run(["git", "rev-parse", "HEAD"]).stdout.strip()
        if new_head == local:
            return False  # Merge was a no-op -- nothing changed, don't restart.
        venv_pip = os.path.join(install_dir, ".venv", "bin", "pip")
        if os.path.exists(venv_pip):
            run([venv_pip, "install", "--quiet", "-r",
                 os.path.join(install_dir, "requirements.txt")])
        log.info("auto-update applied: %s → %s", local[:8], new_head[:8])
        return True
    except Exception:
        log.debug("auto-update check failed", exc_info=True)
        return False


def _restart_app():
    """Restart the app cleanly so the menu-bar item reliably comes back.

    A menu-bar (agent) app must be relaunched by launchd to get a fresh
    GUI / WindowServer session. Re-exec'ing in place with os.execv keeps the
    PID but inherits the old, now-stale GUI session, which intermittently
    leaves the new NSStatusItem alive-but-undrawn -- the "menu bar item
    vanished after a restart" bug. Prefer a launchd kickstart of our
    LaunchAgent; fall back to os.execv only when this process isn't managed by
    that agent (Homebrew / dev / manual launch).
    """
    label = "com.claudebar"
    try:
        uid = os.getuid()
        managed = subprocess.run(
            ["launchctl", "print", f"gui/{uid}/{label}"],
            capture_output=True, text=True, timeout=10,
        ).returncode == 0
        if managed:
            log.info("restarting via launchd kickstart (clean GUI session)")
            subprocess.Popen(
                ["launchctl", "kickstart", "-k", f"gui/{uid}/{label}"])
            time.sleep(3)            # launchd SIGKILLs us; bail if it lags
            os._exit(0)
    except Exception:
        log.debug("launchd kickstart restart failed; falling back to execv",
                  exc_info=True)
    log.info("restarting via in-place execv (fallback)")
    os.execv(sys.executable, [sys.executable] + sys.argv)
