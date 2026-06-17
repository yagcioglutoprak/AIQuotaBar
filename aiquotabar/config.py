"""Configuration constants and persistence."""

import json
import os
import logging
import logging.handlers

# ── logging ──────────────────────────────────────────────────────────────────

LOG_FILE = os.path.expanduser("~/.claude_bar.log")
_log_handler = logging.handlers.RotatingFileHandler(
    LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3,
)
_log_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logging.basicConfig(handlers=[_log_handler], level=logging.DEBUG)
log = logging.getLogger("aiquotabar")

# ── paths & thresholds ───────────────────────────────────────────────────────

CONFIG_FILE = os.path.expanduser("~/.claude_bar_config.json")

REFRESH_INTERVALS = {
    "1 min":  60,
    "5 min":  300,
    "15 min": 900,
}
DEFAULT_REFRESH = 300

# ── Claude menu-bar metrics ───────────────────────────────────────────────────
# Which Claude limit(s) are shown as % segments in the menu bar. Stored under
# config["claude_bar_metrics"] as a list of these keys. Each entry maps to a
# (menu label, compact bar tag) pair. Tags only render when two or more limits
# are shown (to tell them apart); a lone limit shows a bare percentage, so the
# default (session-only) display stays bare, matching older versions.
CLAUDE_BAR_METRICS = {
    "session":       ("Current Session (5h)", "5h"),
    "weekly":        ("Weekly · All Models",  "7d"),
    "weekly_sonnet": ("Weekly · Sonnet",      "7d·S"),
}
DEFAULT_CLAUDE_BAR_METRICS = ["session"]

WARN_THRESHOLD = 80   # notify when any limit crosses this %
CRIT_THRESHOLD = 95   # title turns red emoji above this %

WIDGET_HOST_APP = "/Applications/AIQuotaBarHost.app"
WIDGET_CACHE_DIR = os.path.expanduser(
    "~/Library/Application Support/AIQuotaBar"
)
WIDGET_CACHE_FILE = os.path.join(WIDGET_CACHE_DIR, "usage.json")

# ── notification defaults ─────────────────────────────────────────────────────
# Keys stored in config under "notifications": { key: bool }
NOTIF_DEFAULTS = {
    "claude_reset":   True,   # notify when Claude session/weekly resets
    "chatgpt_reset":  True,   # notify when ChatGPT rate-limit resets
    "claude_warning": True,   # notify when Claude usage crosses WARN/CRIT
    "chatgpt_warning":True,   # notify when ChatGPT usage crosses WARN/CRIT
    "claude_pacing":  True,   # predictive alert when Claude ETA < 30 min
    "chatgpt_pacing": True,   # predictive alert when ChatGPT ETA < 30 min
    "copilot_pacing": True,   # predictive alert when Copilot ETA < 30 min
    "cursor_warning": True,   # notify when Cursor usage crosses WARN/CRIT
    "cursor_pacing":  True,   # predictive alert when Cursor ETA < 30 min
}

# ── usage history + burn rate ────────────────────────────────────────────────

HISTORY_FILE = os.path.expanduser("~/.claude_bar_history.json")
HISTORY_MAX_AGE = 24 * 3600  # prune entries older than 24 h
PACING_ALERT_MINUTES = 30    # alert when ETA drops below this

# ── SQLite long-term history ─────────────────────────────────────────────────
HISTORY_DB = os.path.join(os.path.expanduser("~/Library/Application Support/AIQuotaBar"), "history.db")
SAMPLES_MAX_DAYS = 7
DAILY_MAX_DAYS = 90
LIMIT_HIT_PCT = 95
BURN_WINDOW = 30 * 60       # regression window: 30 minutes
MIN_SPAN_SECS = 5 * 60      # need >=5 min of data before showing ETA
RESET_DROP_PCT = 30          # pct drop that signals a reset
UPDATE_CHECK_INTERVAL = 4 * 3600   # check for updates every 4 hours

HISTORY_COLORS = {
    "claude": "#D97757", "chatgpt": "#74AA9C",
    "copilot": "#6E40C9", "cursor": "#00A0D1",
}


# ── config persistence ───────────────────────────────────────────────────────

def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            corrupt = CONFIG_FILE + ".bak"
            log.warning("Config file corrupt (%s), resetting. Backup at %s", e, corrupt)
            try:
                os.replace(CONFIG_FILE, corrupt)
            except OSError:
                pass
    return {}


def save_config(cfg: dict):
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_FILE)


def notif_enabled(cfg: dict, key: str) -> bool:
    """Return True if the named notification is enabled (defaults to True)."""
    return cfg.get("notifications", {}).get(key, NOTIF_DEFAULTS.get(key, True))


def set_notif(cfg: dict, key: str, value: bool):
    """Persist a single notification toggle."""
    cfg.setdefault("notifications", {})[key] = value
    save_config(cfg)
