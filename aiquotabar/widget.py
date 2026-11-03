"""Widget cache writer for WidgetKit desktop widget."""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone

from aiquotabar.config import log, WIDGET_HOST_APP, WIDGET_CACHE_DIR, WIDGET_CACHE_FILE
from aiquotabar.providers import LimitRow, UsageData, ProviderData


def _write_widget_cache(
    data: UsageData,
    providers: list[ProviderData],
    cc_stats: dict | None,
    config: dict | None = None,
) -> None:
    """Write current usage snapshot for the WidgetKit widget.

    Writes to ~/Library/Application Support/AIQuotaBar/usage.json
    using atomic replace so the widget never reads a partial file.
    Failures are logged but never crash the main app. Does nothing when the
    user has turned the widget off (Settings → About → Desktop widget).
    """
    if not widget_enabled(config or {}):
        return
    try:
        def _row_dict(row: LimitRow | None) -> dict | None:
            if row is None:
                return None
            return {"label": row.label, "pct": row.pct, "reset_str": row.reset_str,
                    "resets_at": row.resets_at}

        def _active_providers(cfg: dict) -> list[str]:
            """Return list of provider IDs the user has configured."""
            active = []
            if cfg.get("cookie_str"):
                active.append("claude")
            _key_map = {
                "chatgpt_cookies": "chatgpt",
                "copilot_cookies": "copilot",
                "cursor_cookies":  "cursor",
            }
            for cfg_key, prov_id in _key_map.items():
                if cfg.get(cfg_key):
                    active.append(prov_id)
            # Fallback: always show at least Claude
            return active or ["claude"]

        def _bar_providers(cfg: dict) -> list[str] | None:
            """User's explicit bar provider choices (lowercase IDs), or None for auto."""
            chosen = cfg.get("bar_providers")
            if not chosen:
                return None
            return [n.lower() for n in chosen]

        def _copilot_block(provs: list[ProviderData]) -> dict:
            pd = next((p for p in provs if p.name == "Copilot"), None)
            if not pd:
                return {"spent": None, "limit": None, "pct": None, "error": None}
            if pd.error:
                return {"spent": None, "limit": None, "pct": None, "error": pd.error}
            pct = int(round(pd.spent / pd.limit * 100)) if pd.limit else 0
            return {
                "spent": pd.spent,
                "limit": pd.limit,
                "pct": pct,
                "error": None,
            }

        # ChatGPT rows
        chatgpt_pd = next((p for p in providers if p.name == "ChatGPT"), None)
        chatgpt_rows = None
        chatgpt_error = None
        if chatgpt_pd:
            if chatgpt_pd.error:
                chatgpt_error = chatgpt_pd.error
            else:
                raw_rows = getattr(chatgpt_pd, "_rows", None) or []
                chatgpt_rows = [_row_dict(r) for r in raw_rows]

        # Cursor rows
        cursor_pd = next((p for p in providers if p.name == "Cursor"), None)
        cursor_rows = None
        cursor_error = None
        if cursor_pd:
            if cursor_pd.error:
                cursor_error = cursor_pd.error
            else:
                raw_rows = getattr(cursor_pd, "_rows", None) or []
                cursor_rows = [_row_dict(r) for r in raw_rows]

        payload = {
            "version": 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "claude": {
                "session": _row_dict(data.session),
                "weekly_all": _row_dict(data.weekly_all),
                "weekly_sonnet": _row_dict(data.weekly_sonnet),
                "overages_enabled": data.overages_enabled,
            },
            "chatgpt": {
                "rows": chatgpt_rows,
                "error": chatgpt_error,
            },
            "cursor": {
                "rows": cursor_rows,
                "error": cursor_error,
            },
            "copilot": _copilot_block(providers),
            "claude_code": {
                "today_messages": (cc_stats or {}).get("today_messages", 0),
                "week_messages": (cc_stats or {}).get("week_messages", 0),
            },
            "active_providers": _active_providers(config or {}),
            "bar_providers": _bar_providers(config or {}),
        }

        os.makedirs(WIDGET_CACHE_DIR, exist_ok=True)
        tmp = os.path.join(WIDGET_CACHE_DIR, ".usage.json.tmp")
        with open(tmp, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, WIDGET_CACHE_FILE)
        log.debug("widget cache written: %s", WIDGET_CACHE_FILE)

        # Nudge WidgetKit to reload (non-blocking, best-effort). This launches
        # the host app, so only do it when the widget is actually installed.
        if _is_widget_installed():
            subprocess.Popen(
                ["open", "-g", "-a", "AIQuotaBarHost", "--args", "--reload-widget"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
    except Exception:
        log.debug("_write_widget_cache failed", exc_info=True)


def _cli_limit(row: dict) -> dict:
    return {
        "label": row.get("label"),
        "pct": row.get("pct"),
        "reset": row.get("reset_str"),
        "resets_at": row.get("resets_at"),
    }


def usage_for_cli(snapshot: dict) -> dict:
    """Reshape the widget snapshot into `{updated_at, providers: {id: {limits, error}}}`.

    Only providers with data or an error appear. Older snapshots without
    `resets_at` still work (it comes out as null).
    """
    providers = {}

    claude = snapshot.get("claude") or {}
    rows = [claude.get(k) for k in ("session", "weekly_all", "weekly_sonnet")]
    limits = [_cli_limit(r) for r in rows if r]
    if limits:
        providers["claude"] = {"limits": limits, "error": None}

    for pid in ("chatgpt", "cursor"):
        block = snapshot.get(pid) or {}
        limits = [_cli_limit(r) for r in (block.get("rows") or []) if r]
        if limits or block.get("error"):
            providers[pid] = {"limits": limits, "error": block.get("error")}

    copilot = snapshot.get("copilot") or {}
    if copilot.get("pct") is not None:
        providers["copilot"] = {"limits": [{
            "label": "Premium requests",
            "pct": copilot.get("pct"),
            "spent": copilot.get("spent"),
            "limit": copilot.get("limit"),
        }], "error": None}
    elif copilot.get("error"):
        providers["copilot"] = {"limits": [], "error": copilot.get("error")}

    return {"updated_at": snapshot.get("updated_at"), "providers": providers}


def cli_json() -> int:
    """`--json`: print the latest snapshot for scripts. Returns the exit code."""
    try:
        with open(WIDGET_CACHE_FILE) as f:
            snapshot = json.load(f)
        if not isinstance(snapshot, dict):
            raise ValueError("snapshot is not an object")
    except FileNotFoundError:
        print("No usage data yet. Start AIQuotaBar and wait for the first refresh "
              "(the snapshot is only written while Settings → About → Desktop widget is on).",
              file=sys.stderr)
        return 1
    except (OSError, ValueError) as e:
        print(f"Could not read {WIDGET_CACHE_FILE}: {e}", file=sys.stderr)
        return 1
    print(json.dumps(usage_for_cli(snapshot), indent=2))
    return 0


def _is_widget_installed() -> bool:
    """Check if the AIQuotaBarHost widget app is installed."""
    return os.path.isdir(WIDGET_HOST_APP)


def widget_enabled(config: dict) -> bool:
    return config.get("widget_enabled", True) is not False


def _quit_widget_host() -> None:
    """Quit the widget host app (and its window) if it is running."""
    try:
        subprocess.Popen(
            ["osascript", "-e",
             'if application "AIQuotaBarHost" is running then quit application "AIQuotaBarHost"'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        log.debug("_quit_widget_host failed", exc_info=True)
