"""Silent auto-update via git pull."""

import os
import subprocess
import sys

from aiquotabar.config import log


def _check_and_apply_update() -> bool:
    """Silently check for updates via git and apply if available. Returns True if updated."""
    from aiquotabar.config import load_config
    if not load_config().get("auto_update", True):
        return False
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
        run(["git", "stash", "--quiet"])
        r = run(["git", "merge", "--ff-only", "origin/main", "--quiet"])
        if r.returncode != 0:
            log.warning("auto-update merge failed: %s", r.stderr)
            return False
        venv_pip = os.path.join(install_dir, ".venv", "bin", "pip")
        if os.path.exists(venv_pip):
            run([venv_pip, "install", "--quiet", "-r",
                 os.path.join(install_dir, "requirements.txt")])
        log.info("auto-update applied: %s → %s", local[:8], remote[:8])
        return True
    except Exception:
        log.debug("auto-update check failed", exc_info=True)
        return False


def _restart_app():
    """Restart the app in-place by re-exec'ing the current process."""
    log.info("restarting after auto-update")
    os.execv(sys.executable, [sys.executable] + sys.argv)
