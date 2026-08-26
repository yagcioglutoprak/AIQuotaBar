"""Data models and API fetch functions for all providers."""

import json
import math
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

from curl_cffi import requests
from curl_cffi.requests.exceptions import HTTPError as CurlHTTPError

try:
    import browser_cookie3
    _BROWSER_COOKIE3_OK = True
except ImportError:
    _BROWSER_COOKIE3_OK = False

from aiquotabar.config import log


# ── data models ───────────────────────────────────────────────────────────────

@dataclass
class LimitRow:
    label: str
    pct: int          # 0–100
    reset_str: str    # e.g. "resets in 1h 23m" or "resets Thu 00:00"


@dataclass
class UsageData:
    session: LimitRow | None = None
    weekly_all: LimitRow | None = None
    weekly_sonnet: LimitRow | None = None
    overages_enabled: bool | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class ProviderData:
    """Usage/billing data for a third-party API provider."""
    name: str
    spent: float | None = None    # current period spend
    limit: float | None = None    # hard/soft limit
    balance: float | None = None  # prepaid balance (for credit-based providers)
    currency: str = "USD"
    period: str = "this month"
    error: str | None = None
    _rows: list = field(default_factory=list, repr=False)

    @property
    def pct(self) -> int | None:
        if self.spent is not None and self.limit and self.limit > 0:
            return min(100, round(self.spent / self.limit * 100))
        return None


# ── claude.ai API ─────────────────────────────────────────────────────────────

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://claude.ai/settings/usage",
    "Origin": "https://claude.ai",
}
# Cloudflare fingerprint-checks Chrome aggressively; Safari passes cleanly.
_IMPERSONATE = "safari184"
# Cloudflare-bound cookies are tied to the real browser fingerprint —
# sending them from a different TLS stack causes a mismatch → 403.
CF_COOKIE_KEYS = frozenset({"cf_clearance", "__cf_bm", "_cfuvid"})


def parse_cookie_string(raw: str) -> dict:
    """Parse 'key=val; key2=val2' or just a bare sessionKey value."""
    raw = raw.strip()
    if "=" not in raw:
        return {"sessionKey": raw}
    cookies = {}
    for part in raw.split(";"):
        part = part.strip()
        if "=" in part:
            k, _, v = part.partition("=")
            cookies[k.strip()] = v.strip()
    return cookies


def _strip_cf_cookies(cookies: dict) -> dict:
    return {k: v for k, v in cookies.items() if k not in CF_COOKIE_KEYS}


def _get(url: str, cookies: dict) -> dict | list:
    r = requests.get(
        url, cookies=_strip_cf_cookies(cookies), headers=HEADERS, timeout=15,
        impersonate=_IMPERSONATE,
    )
    log.debug("GET %s  status=%s  body=%s", url, r.status_code, r.text[:800])
    r.raise_for_status()
    return r.json()


def _org_id_from_cookies(cookies: dict) -> str | None:
    return cookies.get("lastActiveOrg") or cookies.get("routingHint")


def _org_id_from_api(cookies: dict) -> str | None:
    for path in (
        "/api/organizations",
        "/api/bootstrap",
        "/api/auth/current_account",
        "/api/account",
    ):
        try:
            data = _get(f"https://claude.ai{path}", cookies)
            if isinstance(data, list) and data:
                return data[0].get("id") or data[0].get("uuid")
            if isinstance(data, dict):
                for candidate in (
                    data.get("organization_id"),
                    data.get("org_id"),
                    (data.get("organizations") or [{}])[0].get("id"),
                    (data.get("account", {}).get("memberships") or [{}])[0]
                        .get("organization", {}).get("id"),
                ):
                    if candidate:
                        return candidate
        except Exception as e:
            log.debug("endpoint %s failed: %s", path, e)
    return None



def fetch_raw(cookie_str: str) -> dict:
    cookies = parse_cookie_string(cookie_str)
    log.debug("using cookies keys: %s", list(cookies.keys()))

    org_id = _org_id_from_cookies(cookies)
    log.debug("org_id from cookie: %s", org_id)

    if not org_id:
        org_id = _org_id_from_api(cookies)
        log.debug("org_id from api: %s", org_id)

    if not org_id:
        raise ValueError(
            "Could not find organization id.\n"
            "Make sure you copied ALL cookies (including lastActiveOrg)."
        )

    usage = _get(
        f"https://claude.ai/api/organizations/{org_id}/usage", cookies
    )
    log.debug("usage full response: %s", json.dumps(usage, indent=2))
    return {"usage": usage, "org_id": org_id}


# ── time helpers ──────────────────────────────────────────────────────────────

_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _fmt_reset_after_seconds(secs: int) -> str:
    """Format remaining window time when only reset_after_seconds is available."""
    try:
        secs = int(secs)
    except (TypeError, ValueError):
        return ""
    if secs <= 0:
        return "resets soon"
    if secs < 3600 * 20:
        h, rem = divmod(secs, 3600)
        m = rem // 60
        if h > 0:
            return f"resets in {h}h {m}m"
        return f"resets in {m}m"
    dt = datetime.now(timezone.utc) + timedelta(seconds=secs)
    day = _DAYS[dt.weekday()]
    return f"resets {day} {dt.strftime('%H:%M')}"


def _reset_str_from_window(window: dict) -> str:
    """Reset label from wham window: reset_at first, then reset_after_seconds."""
    if not window or not isinstance(window, dict):
        return ""
    reset_at = window.get("reset_at")
    if reset_at:
        reset = _fmt_reset(reset_at)
        if reset:
            return reset
    reset_after = window.get("reset_after_seconds")
    if reset_after is not None:
        return _fmt_reset_after_seconds(reset_after)
    return ""


def _fmt_reset(val) -> str:
    if val is None:
        return ""
    try:
        if isinstance(val, (int, float)):
            dt = datetime.fromtimestamp(val, tz=timezone.utc)
        else:
            s = str(val).rstrip("Z")
            if "+" not in s[10:] and s[-6] != "+":
                s += "+00:00"
            dt = datetime.fromisoformat(s)
        now = datetime.now(timezone.utc)
        delta = dt - now
        secs = delta.total_seconds()
        if secs <= 0:
            return "resets soon"
        if secs < 3600 * 20:
            h, rem = divmod(int(secs), 3600)
            m = rem // 60
            if h > 0:
                return f"resets in {h}h {m}m"
            return f"resets in {m}m"
        day = _DAYS[dt.weekday()]
        return f"resets {day} {dt.strftime('%H:%M')}"
    except Exception:
        log.debug("_fmt_reset failed for %r", val, exc_info=True)
        return str(val)[:20]


# ── parser ────────────────────────────────────────────────────────────────────

_CLAUDE_LIMIT_KINDS = {
    "five_hour": "session",
    "seven_day": "weekly_all",
    "seven_day_sonnet": "weekly_sonnet",
}


def _row(data: dict, key: str, label: str, limits: list | None = None) -> LimitRow | None:
    bucket = data.get(key)
    if not bucket or not isinstance(bucket, dict):
        return None
    raw = float(bucket.get("utilization", 0))
    # API returns 0-100 percentage for all fields (five_hour, seven_day, etc.)
    pct = min(100, round(raw))
    reset = _fmt_reset(bucket.get("resets_at"))
    if not reset and limits:
        kind = _CLAUDE_LIMIT_KINDS.get(key)
        if kind:
            for lim in limits:
                if isinstance(lim, dict) and lim.get("kind") == kind:
                    reset = _fmt_reset(lim.get("resets_at"))
                    break
    return LimitRow(label, pct, reset)


def parse_usage(raw: dict) -> UsageData:
    """
    API response shape (confirmed):
      five_hour        -> Plan usage limits / Current session
      seven_day        -> Weekly limits / All models
      seven_day_sonnet -> Weekly limits / Sonnet only
      extra_usage      -> Extra usage toggle (null = off)
    """
    u = raw.get("usage", {})
    extra = u.get("extra_usage")
    overages = bool(extra) if extra is not None else None
    limits = u.get("limits") or []

    return UsageData(
        session=_row(u, "five_hour", "Current Session", limits),
        weekly_all=_row(u, "seven_day", "All Models", limits),
        weekly_sonnet=_row(u, "seven_day_sonnet", "Sonnet Only", limits),
        overages_enabled=overages,
        raw=raw,
    )


# ── third-party provider APIs ────────────────────────────────────────────────

def _api_get(url: str, headers: dict, cookies: dict | None = None) -> dict:
    clean = _strip_cf_cookies(cookies) if cookies else None
    r = requests.get(url, headers=headers, cookies=clean, timeout=10, impersonate=_IMPERSONATE)
    r.raise_for_status()
    return r.json()


_CHATGPT_HEADERS = {
    "Accept": "application/json",
    "Referer": "https://chatgpt.com/codex/settings/usage",
}


def _chatgpt_access_token(cookies: dict) -> str | None:
    """Exchange session cookie for a short-lived Bearer token."""
    data = _api_get("https://chatgpt.com/api/auth/session", _CHATGPT_HEADERS, cookies)
    return data.get("accessToken")


def _chatgpt_session(cookies: dict) -> tuple[str | None, str | None]:
    """Return (access_token, account_id) from the ChatGPT web session."""
    data = _api_get("https://chatgpt.com/api/auth/session", _CHATGPT_HEADERS, cookies)
    token = data.get("accessToken")
    account_id = (data.get("account") or {}).get("id")
    return token, account_id


def _pct_from_wham_window(window: dict) -> int:
    """Best-effort usage % from a wham window object.

    OpenAI sometimes returns used_percent=0 while reset_after_seconds and
    limit_window_seconds still reflect partial consumption. Derive a fallback
    from the remaining window time when that happens.
    """
    reported = window.get("used_percent")
    try:
        reported_pct = min(100, max(0, int(round(float(reported or 0)))))
    except (TypeError, ValueError):
        reported_pct = 0

    limit_secs = window.get("limit_window_seconds") or 0
    reset_after = window.get("reset_after_seconds")
    derived_pct = 0
    try:
        if limit_secs > 0 and reset_after is not None:
            remaining = float(reset_after) / float(limit_secs)
            derived_pct = min(100, max(0, round((1 - remaining) * 100)))
    except (TypeError, ValueError, ZeroDivisionError):
        derived_pct = 0

    return max(reported_pct, derived_pct)


def _limit_row_from_window(window: dict, label: str) -> LimitRow | None:
    """Build one LimitRow from a wham primary/secondary window dict."""
    if not window or not isinstance(window, dict):
        return None
    pct = _pct_from_wham_window(window)
    reset_str = _reset_str_from_window(window)
    return LimitRow(label, pct, reset_str)


def _parse_wham_rate_block(
    block: dict | None,
    primary_label: str,
    secondary_label: str | None = None,
) -> list[LimitRow]:
    """Parse primary + secondary windows from a wham rate_limit block."""
    rows: list[LimitRow] = []
    if not block or not isinstance(block, dict):
        return rows
    primary = _limit_row_from_window(block.get("primary_window") or {}, primary_label)
    if primary:
        rows.append(primary)
    if secondary_label:
        secondary = _limit_row_from_window(block.get("secondary_window") or {}, secondary_label)
        if secondary:
            rows.append(secondary)
    return rows


def _parse_wham_window(window: dict, label: str) -> LimitRow | None:
    """Parse only the primary window (legacy helper)."""
    rows = _parse_wham_rate_block(window, label)
    return rows[0] if rows else None


def _parse_wham_usage(data: dict) -> ProviderData:
    """Parse /backend-api/wham/usage response.

    Confirmed shape (2026-02):
      rate_limit.primary_window.used_percent  (0-100)  ~5h bucket
      rate_limit.secondary_window.used_percent         ~7d bucket
      rate_limit.primary_window.reset_at      (Unix timestamp)
      code_review_rate_limit  -- same structure
    """
    log.debug("wham/usage raw: %s", json.dumps(data, indent=2))

    rows: list[LimitRow] = []

    bucket_labels = {
        "rate_limit": ("Codex Tasks", "Codex Weekly"),
        "code_review_rate_limit": ("Code Review", "Code Review Weekly"),
    }
    for key, (primary_label, secondary_label) in bucket_labels.items():
        rows.extend(_parse_wham_rate_block(data.get(key), primary_label, secondary_label))

    # additional_rate_limits may be a list of extra buckets
    for extra in (data.get("additional_rate_limits") or []):
        if isinstance(extra, dict):
            name = (
                extra.get("limit_name")
                or extra.get("metered_feature")
                or extra.get("name")
                or extra.get("type")
                or "Extra"
            )
            base = str(name).replace("_", " ").title()
            block = extra.get("rate_limit") if isinstance(extra.get("rate_limit"), dict) else extra
            rows.extend(_parse_wham_rate_block(block, base, f"{base} Weekly"))

    if not rows:
        return ProviderData("ChatGPT", error="No rate limit data in response")

    worst = max(rows, key=lambda r: r.pct)
    pd = ProviderData("ChatGPT", spent=float(worst.pct), limit=100.0, currency="")
    pd._rows = rows
    return pd


def fetch_chatgpt(cookie_str: str) -> ProviderData:
    """Fetch ChatGPT / Codex usage via /backend-api/wham/usage."""
    cookies = parse_cookie_string(cookie_str)
    try:
        token, account_id = _chatgpt_session(cookies)
        if not token:
            return ProviderData("ChatGPT", error="Not logged in")
        h = {**_CHATGPT_HEADERS, "Authorization": f"Bearer {token}"}
        if account_id:
            h["ChatGPT-Account-Id"] = str(account_id)
        data = _api_get("https://chatgpt.com/backend-api/wham/usage", h, cookies)
        return _parse_wham_usage(data)
    except Exception as e:
        log.debug("fetch_chatgpt failed: %s", e)
        return ProviderData("ChatGPT", error=str(e)[:80])


def fetch_openai(api_key: str) -> ProviderData:
    h = {"Authorization": f"Bearer {api_key}"}
    try:
        sub = _api_get(
            "https://api.openai.com/v1/dashboard/billing/subscription", h
        )
        hard_limit = float(
            sub.get("hard_limit_usd") or sub.get("system_hard_limit_usd") or 0
        )
        now = datetime.now()
        start = now.replace(day=1).strftime("%Y-%m-%d")
        end = (now + timedelta(days=1)).strftime("%Y-%m-%d")
        usage = _api_get(
            f"https://api.openai.com/v1/dashboard/billing/usage"
            f"?start_date={start}&end_date={end}", h
        )
        spent = float(usage.get("total_usage", 0)) / 100  # cents -> dollars
        return ProviderData(
            "OpenAI", spent=spent,
            limit=hard_limit or None, currency="USD", period="this month",
        )
    except Exception as e:
        log.debug("fetch_openai failed: %s", e)
        return ProviderData("OpenAI", error=str(e)[:80])


def fetch_minimax(api_key: str) -> ProviderData:
    h = {"Authorization": f"Bearer {api_key}"}
    try:
        data = _api_get("https://api.minimax.chat/v1/account_information", h)
        balance = float(
            data.get("available_balance") or data.get("balance") or 0
        )
        return ProviderData("MiniMax", balance=balance, currency="CNY")
    except Exception as e:
        log.debug("fetch_minimax failed: %s", e)
        return ProviderData("MiniMax", error=str(e)[:80])


def fetch_glm(api_key: str) -> ProviderData:
    h = {"Authorization": f"Bearer {api_key}"}
    try:
        data = _api_get(
            "https://open.bigmodel.cn/api/paas/v4/account/balance", h
        )
        balance = float(
            data.get("total_balance") or data.get("balance") or 0
        )
        return ProviderData("GLM (Zhipu)", balance=balance, currency="CNY")
    except Exception as e:
        log.debug("fetch_glm failed: %s", e)
        return ProviderData("GLM (Zhipu)", error=str(e)[:80])


def fetch_copilot(cookie_str: str) -> ProviderData:
    """Fetch GitHub Copilot premium request usage via browser cookies."""
    cookies = parse_cookie_string(cookie_str)
    try:
        r = requests.get(
            "https://github.com/settings/billing/copilot_usage_card",
            cookies=_strip_cf_cookies(cookies),
            headers={
                "Accept": "application/json",
                "Referer": "https://github.com/settings/billing/premium_requests_usage",
            },
            timeout=10,
            impersonate=_IMPERSONATE,
        )
        r.raise_for_status()
        data = r.json()
        log.debug("copilot_usage_card: %s", json.dumps(data, indent=2))
        used = float(data.get("discountQuantity", 0))
        limit = float(data.get("userPremiumRequestEntitlement", 0))
        return ProviderData(
            "Copilot", spent=used, limit=limit or None,
            currency="", period="this month",
        )
    except Exception as e:
        log.debug("fetch_copilot failed: %s", e)
        return ProviderData("Copilot", error=str(e)[:80])


def fetch_cursor(cookie_str: str) -> ProviderData:
    """Fetch Cursor IDE usage via browser cookies (WorkOS session)."""
    cookies = parse_cookie_string(cookie_str)
    try:
        r = requests.get(
            "https://cursor.com/api/usage-summary",
            cookies=_strip_cf_cookies(cookies),
            headers={
                "Accept": "application/json",
                "Referer": "https://cursor.com/dashboard?tab=usage",
            },
            timeout=10,
            impersonate=_IMPERSONATE,
        )
        r.raise_for_status()
        data = r.json()
        log.debug("cursor usage-summary: %s", json.dumps(data, indent=2))
        plan = (data.get("individualUsage") or {}).get("plan") or {}
        auto_pct = int(round(float(plan.get("autoPercentUsed", 0))))
        api_pct = int(round(float(plan.get("apiPercentUsed", 0))))
        total_pct = int(round(float(plan.get("totalPercentUsed", 0))))
        # Build reset string from billingCycleEnd
        reset_str = ""
        cycle_end = data.get("billingCycleEnd")
        if cycle_end:
            try:
                end_dt = datetime.fromisoformat(cycle_end.replace("Z", "+00:00"))
                delta = end_dt - datetime.now(timezone.utc)
                if delta.total_seconds() > 0:
                    days = delta.days
                    hours = delta.seconds // 3600
                    if days > 0:
                        reset_str = f"resets in {days}d {hours}h"
                    else:
                        reset_str = f"resets in {hours}h"
            except (ValueError, TypeError):
                pass
        rows = [
            LimitRow(label="Auto", pct=auto_pct, reset_str=reset_str),
            LimitRow(label="API", pct=api_pct, reset_str=reset_str),
        ]
        pd = ProviderData("Cursor", spent=float(total_pct), limit=100.0, currency="")
        pd._rows = rows
        return pd
    except Exception as e:
        log.debug("fetch_cursor failed: %s", e)
        return ProviderData("Cursor", error=str(e)[:80])


# Registry: config_key -> (display_name, fetch_fn)
# chatgpt_cookies / copilot_cookies are cookie-based (auto-detected);
# others are API key-based.
PROVIDER_REGISTRY: dict[str, tuple[str, callable]] = {
    "chatgpt_cookies": ("ChatGPT",     fetch_chatgpt),
    "copilot_cookies": ("Copilot",     fetch_copilot),
    "cursor_cookies":  ("Cursor",      fetch_cursor),
    "openai_key":      ("OpenAI",      fetch_openai),
    "minimax_key":     ("MiniMax",     fetch_minimax),
    "glm_key":         ("GLM (Zhipu)", fetch_glm),
}

# Cookie-based providers (auto-detected from browser, not manually entered)
COOKIE_PROVIDERS = {"chatgpt_cookies", "copilot_cookies", "cursor_cookies"}


# ── Claude Code local stats ───────────────────────────────────────────────────

CC_STATS_FILE = os.path.expanduser("~/.claude/stats-cache.json")


def fetch_claude_code_stats() -> dict | None:
    """Read Claude Code usage from ~/.claude/stats-cache.json (no network needed).

    Returns dict with today_messages, today_sessions, week_messages,
    week_sessions, week_tool_calls -- or None if the file doesn't exist.
    """
    if not os.path.exists(CC_STATS_FILE):
        return None
    try:
        with open(CC_STATS_FILE) as f:
            data = json.load(f)
        today = datetime.now().strftime("%Y-%m-%d")
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        entries = data.get("dailyActivity", [])
        today_e = next((e for e in entries if e["date"] == today), None)
        week_e  = [e for e in entries if e["date"] >= week_ago]
        return {
            "today_messages":   today_e["messageCount"]  if today_e else 0,
            "today_sessions":   today_e["sessionCount"]  if today_e else 0,
            "week_messages":    sum(e["messageCount"]  for e in week_e),
            "week_sessions":    sum(e["sessionCount"]  for e in week_e),
            "week_tool_calls":  sum(e["toolCallCount"] for e in week_e),
            "last_date": max((e["date"] for e in entries), default=None),
        }
    except Exception as e:
        log.debug("fetch_claude_code_stats failed: %s", e)
        return None


# ── Cookie detection ──────────────────────────────────────────────────────────

_keychain_warned = False  # show the dialog at most once per session


def _warn_keychain_once():
    """Show a one-time dialog before the macOS Keychain prompt appears."""
    global _keychain_warned
    if _keychain_warned:
        return
    _keychain_warned = True
    subprocess.run(
        ["osascript", "-e",
         'display dialog "Claude Usage Bar needs one-time access to your '
         'browser cookies to read your Claude usage.\\n\\n'
         'macOS will show a security prompt — click \\"Always Allow\\" '
         'and it will never ask again." '
         'with title "Claude Usage Bar — One-time Setup" '
         'buttons {"OK"} default button "OK" '
         'with icon note'],
        capture_output=True, timeout=60,
    )


# Script run in a child process — isolates browser_cookie3 C-library crashes
# (libcrypto / sqlite segfaults on Chromium decryption don't kill the main app).
_DETECT_SCRIPT = r"""
import sys, json

domain  = sys.argv[1]
target  = sys.argv[2]

BROWSERS = [
    'firefox', 'librewolf', 'chrome', 'arc', 'brave',
    'edge', 'chromium', 'opera', 'vivaldi', 'safari',
]

# Collect candidates from every browser that has the target cookie.
# Rank by expiry as a hint, but the caller VALIDATES each candidate and
# uses the first that actually authenticates -- a stale session in one
# browser must never mask a valid one in another.
candidates = []  # list of (expires_seconds, cookie_str)

try:
    import browser_cookie3
    for name in BROWSERS:
        fn = getattr(browser_cookie3, name, None)
        if fn is None:
            continue
        try:
            jar = fn(domain_name=domain)
            cookies = {x.name: x for x in jar}
            if target in cookies:
                expiry_key = target
            elif target + '.0' in cookies:
                # NextAuth/Auth.js splits large session JWTs into chunked
                # cookies (target.0, target.1, ...) when the token exceeds
                # the ~4KB per-cookie browser limit (common once an account
                # belongs to multiple orgs/workspaces). The chunks are still
                # present in `cookies` and get joined into cookie_str below,
                # exactly as the browser would send them to the real site --
                # we just need to stop skipping this browser because the
                # unsuffixed name doesn't exist.
                expiry_key = target + '.0'
            else:
                continue
            expires = cookies[expiry_key].expires or 0
            # Normalize expiry to seconds. Firefox can report the value in
            # milliseconds (or an overflowed scale), which made a stale
            # session always out-rank a valid Chromium one. Anything past
            # year ~5138 in seconds (1e11) is treated as milliseconds.
            try:
                expires = float(expires)
            except (TypeError, ValueError):
                expires = 0.0
            while expires > 1e11:
                expires /= 1000.0
            cookie_str = '; '.join(f'{k}={c.value}' for k, c in cookies.items())
            candidates.append((expires, cookie_str))
        except Exception:
            pass
except Exception:
    pass

# Rank best-first: latest (normalized) expiry, tie-break by richest jar.
candidates.sort(key=lambda x: (x[0], len(x[1])), reverse=True)
result = [c[1] for c in candidates]

print(json.dumps(result))
"""


def _run_cookie_detection(domain: str, target_cookie: str) -> list[str]:
    """Run browser_cookie3 in an isolated child process (crash-safe).

    Returns a best-first ranked list of candidate cookie strings (one per
    browser that has the target cookie). Empty list if none are found.
    """
    try:
        r = subprocess.run(
            [sys.executable, "-c", _DETECT_SCRIPT, domain, target_cookie],
            capture_output=True, text=True, timeout=60,
        )
        log.debug("cookie-detect rc=%d out=%r err=%r",
                  r.returncode, r.stdout[:200], r.stderr[:200])
        if r.stdout.strip():
            data = json.loads(r.stdout.strip())
            if isinstance(data, list):
                return [c for c in data if c]
            if isinstance(data, str):   # backward-compat with old single result
                return [data]
    except Exception as e:
        log.debug("_run_cookie_detection failed: %s", e)
    return []


def _claude_cookie_is_valid(cookie_str: str) -> bool:
    """True if this cookie string authenticates against claude.ai.

    Probes /api/organizations (session-level, no org id needed). A stale or
    logged-out sessionKey returns 403 account_session_invalid here, so this
    cleanly rejects dead sessions that still carry a far-future expiry.
    """
    try:
        _get("https://claude.ai/api/organizations", parse_cookie_string(cookie_str))
        return True
    except Exception as e:
        log.debug("claude cookie candidate rejected: %s", e)
        return False


def _auto_detect_cookies() -> str | None:
    """Detect a *valid* claude.ai session cookie from the browser.

    Returns the first candidate (across all logged-in browsers) that actually
    authenticates, instead of blindly trusting the latest-expiry one. This
    stops a stale session in one browser (e.g. an old Firefox login whose
    cookie still has a far-future expiry) from masking a valid session in
    another (e.g. a fresh Chrome login).
    """
    if not _BROWSER_COOKIE3_OK:
        return None
    _warn_keychain_once()
    candidates = _run_cookie_detection("claude.ai", "sessionKey")
    if not candidates:
        return None
    for cookie_str in candidates:
        if _claude_cookie_is_valid(cookie_str):
            return cookie_str
    # Nothing validated (all logged out / expired). Fall back to the
    # best-ranked candidate so the existing 401/403 handling can surface a
    # "session expired" prompt to the user.
    log.debug("no claude cookie candidate validated; using best-ranked")
    return candidates[0]


def _auto_detect_chatgpt_cookies() -> str | None:
    """Detect chatgpt.com session cookies from the browser (crash-safe subprocess)."""
    if not _BROWSER_COOKIE3_OK:
        return None
    cands = _run_cookie_detection("chatgpt.com", "__Secure-next-auth.session-token")
    return cands[0] if cands else None


def _auto_detect_copilot_cookies() -> str | None:
    """Detect github.com session cookies from the browser (crash-safe subprocess)."""
    if not _BROWSER_COOKIE3_OK:
        return None
    cands = _run_cookie_detection("github.com", "user_session")
    return cands[0] if cands else None


def _auto_detect_cursor_cookies() -> str | None:
    """Detect cursor.com session cookies from the browser (crash-safe subprocess)."""
    if not _BROWSER_COOKIE3_OK:
        return None
    cands = _run_cookie_detection("cursor.com", "WorkosCursorSessionToken")
    return cands[0] if cands else None
