"""Data models and API fetch functions for all providers."""

import base64
import glob
import json
import math
import os
import re
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
    resets_at: float | None = None    # unix timestamp of the next reset
    window_secs: int | None = None    # length of the limit window (5h, 7d, ...)


@dataclass
class UsageData:
    session: LimitRow | None = None
    weekly_all: LimitRow | None = None
    weekly_sonnet: LimitRow | None = None
    weekly_opus: LimitRow | None = None
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
    reset_str: str = ""
    resets_at: float | None = None
    window_secs: int | None = None
    source: str = ""          # how it signed in: "browser" or "codex" (ChatGPT)
    account_label: str = ""   # the account's email, when the provider tells us
    account_id: str = ""      # provider-side account / workspace id
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


def _org_uuid(org) -> str | None:
    """An organization's uuid, falling back to its id.

    /organizations/{id}/usage rejects the legacy numeric id with HTTP 400
    ("organization_uuid: invalid length"), so the uuid has to win.
    """
    if not isinstance(org, dict):
        return None
    return org.get("uuid") or org.get("id")


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
                return _org_uuid(data[0])
            if isinstance(data, dict):
                for candidate in (
                    data.get("organization_id"),
                    data.get("org_id"),
                    _org_uuid((data.get("organizations") or [{}])[0]),
                    _org_uuid((data.get("account", {}).get("memberships") or [{}])[0]
                              .get("organization")),
                ):
                    if candidate:
                        return candidate
        except Exception as e:
            log.debug("endpoint %s failed: %s", path, e)
    return None



def fetch_raw(cookie_str: str, org_id: str | None = None) -> dict:
    """Usage for one organization: `org_id`, else the one the cookie last used."""
    cookies = parse_cookie_string(cookie_str)
    log.debug("using cookies keys: %s", list(cookies.keys()))

    org_id = org_id or _org_id_from_cookies(cookies)
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


def claude_org_label(name) -> str:
    """"jo@example.com's Organization" -> "jo@example.com" (other names as is)."""
    name = (name or "").strip() if isinstance(name, str) else ""
    m = re.match(r"^(.+?)['’]s [Oo]rganization$", name)
    return m.group(1) if m else name


def claude_orgs(cookie_str: str) -> list[dict]:
    """Every organization a claude.ai session belongs to, in API order.

    One sign-in can see several (say a personal Pro plan and a work Team
    plan), and each has its own limits. Items: {"uuid", "label", "chat"};
    "chat" is False for API-only (Console) organizations, which have no plan
    limits. Raises on HTTP errors, so a dead session is not "no organizations".
    """
    data = _get("https://claude.ai/api/organizations", parse_cookie_string(cookie_str))
    out = []
    for org in data if isinstance(data, list) else []:
        uuid = _org_uuid(org)
        if not uuid:
            continue
        caps = org.get("capabilities")
        chat = not (isinstance(caps, list) and caps and "chat" not in caps)
        out.append({"uuid": str(uuid), "label": claude_org_label(org.get("name")), "chat": chat})
    return out


def claude_default_org(cookie_str: str, orgs: list[dict]) -> str | None:
    """The organization fetch_raw() reads for this cookie (no explicit org)."""
    return _org_id_from_cookies(parse_cookie_string(cookie_str)) or (
        orgs[0]["uuid"] if orgs else None)


# ── time helpers ──────────────────────────────────────────────────────────────

_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

HOUR = 3600
DAY = 24 * HOUR


def _parse_ts(val) -> float | None:
    """Parse a unix timestamp or ISO-8601 string into a unix timestamp."""
    if val is None or val == "":
        return None
    try:
        if isinstance(val, (int, float)):
            # Some APIs report milliseconds.
            return float(val) / 1000.0 if val > 1e11 else float(val)
        s = str(val).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError, OverflowError):
        log.debug("_parse_ts failed for %r", val)
        return None


def fmt_reset_ts(ts: float | None, now: float | None = None) -> str:
    """'resets in 2h 14m' for resets within 20h, else 'resets Thu 09:00'
    (in the user's local time zone)."""
    if ts is None:
        return ""
    now = time.time() if now is None else now
    secs = ts - now
    if secs <= 0:
        return "resets soon"
    if secs < 20 * HOUR:
        h, rem = divmod(int(secs), 3600)
        m = rem // 60
        if h > 0:
            return f"resets in {h}h {m}m"
        return f"resets in {max(1, m)}m"
    if secs < 6 * DAY:
        dt = datetime.fromtimestamp(ts).astimezone()
        return f"resets {_DAYS[dt.weekday()]} {dt.strftime('%H:%M')}"
    days = int(secs // DAY)
    return f"resets in {days}d"


def _fmt_reset(val) -> str:
    ts = _parse_ts(val)
    if ts is None:
        return "" if val is None else str(val)[:20]
    return fmt_reset_ts(ts)


# ── parser ────────────────────────────────────────────────────────────────────

_CLAUDE_WINDOWS = {
    "five_hour": 5 * HOUR,
    "seven_day": 7 * DAY,
    "seven_day_sonnet": 7 * DAY,
    "seven_day_opus": 7 * DAY,
}


def _row(data: dict, key: str, label: str) -> LimitRow | None:
    bucket = data.get(key)
    if not bucket or not isinstance(bucket, dict):
        return None
    raw = float(bucket.get("utilization") or 0)
    # API returns 0-100 percentage for all fields (five_hour, seven_day, etc.)
    pct = max(0, min(100, round(raw)))
    resets_at = _parse_ts(bucket.get("resets_at"))
    return LimitRow(label, pct, fmt_reset_ts(resets_at), resets_at,
                    _CLAUDE_WINDOWS.get(key))


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

    return UsageData(
        session=_row(u, "five_hour", "Current Session"),
        weekly_all=_row(u, "seven_day", "All Models"),
        weekly_sonnet=_row(u, "seven_day_sonnet", "Sonnet Only"),
        weekly_opus=_row(u, "seven_day_opus", "Opus Only"),
        overages_enabled=overages,
        raw=raw,
    )


# ── third-party provider APIs ────────────────────────────────────────────────

def _api_get(url: str, headers: dict, cookies: dict | None = None,
             keep_cf: bool = False) -> dict:
    clean = None
    if cookies:
        clean = cookies if keep_cf else _strip_cf_cookies(cookies)
    r = requests.get(url, headers=headers, cookies=clean, timeout=10, impersonate=_IMPERSONATE)
    r.raise_for_status()
    return r.json()


_CHATGPT_HEADERS = {
    "Accept": "application/json",
    "Referer": "https://chatgpt.com/codex/settings/usage",
}


def _chatgpt_get(url: str, headers: dict, cookies: dict) -> dict:
    """GET on chatgpt.com, without the Cloudflare cookies first.

    Those cookies are bound to the browser's TLS fingerprint, and sending
    them works for some people and breaks it for others. Some chatgpt.com
    edges answer 403 without cf_clearance, so on a 403 retry once with them.
    """
    try:
        return _api_get(url, headers, cookies)
    except CurlHTTPError as e:
        code = getattr(getattr(e, "response", None), "status_code", 0)
        if code != 403 or not CF_COOKIE_KEYS & set(cookies):
            raise
        log.debug("chatgpt 403 on %s; retrying with Cloudflare cookies", url)
        return _api_get(url, headers, cookies, keep_cf=True)


def _chatgpt_session(cookies: dict) -> dict:
    """/api/auth/session: a short-lived Bearer token plus the signed-in user."""
    data = _chatgpt_get("https://chatgpt.com/api/auth/session", _CHATGPT_HEADERS, cookies)
    return data if isinstance(data, dict) else {}


def _jwt_claims(token) -> dict:
    """A JWT's payload, unverified. Only used to label our own tokens."""
    try:
        payload = str(token).split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


_OPENAI_AUTH = "https://api.openai.com/auth"
_OPENAI_PROFILE = "https://api.openai.com/profile"
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _openai_identity(*tokens) -> tuple[str, str]:
    """(ChatGPT account id, email) from OpenAI access / id tokens."""
    acct = email = ""
    for tok in tokens:
        claims = _jwt_claims(tok) if tok else {}
        auth = claims.get(_OPENAI_AUTH) if isinstance(claims.get(_OPENAI_AUTH), dict) else {}
        prof = claims.get(_OPENAI_PROFILE) if isinstance(claims.get(_OPENAI_PROFILE), dict) else {}
        acct = acct or str(auth.get("chatgpt_account_id") or "")
        email = email or str(prof.get("email") or claims.get("email") or "")
    return acct, email


def _chatgpt_workspace(cookies: dict) -> str:
    """The workspace picked in ChatGPT's account switcher (multi-workspace sign-ins)."""
    ws = str(cookies.get("_account") or "")
    return ws if _UUID_RE.match(ws) else ""


def chatgpt_identity(cookie_str: str) -> tuple[str, str]:
    """(account id, email) of a chatgpt.com browser session. Raises when signed out."""
    cookies = parse_cookie_string(cookie_str)
    session = _chatgpt_session(cookies)
    token = session.get("accessToken")
    if not token:
        raise ValueError("Not logged in")
    acct, email = _openai_identity(token)
    user = session.get("user") if isinstance(session.get("user"), dict) else {}
    return _chatgpt_workspace(cookies) or acct, email or str(user.get("email") or "")


def _wham_get(token: str, account_id: str = "", cookies: dict | None = None) -> dict:
    """Codex limits for the account (workspace) the token, or account_id, names."""
    h = {**_CHATGPT_HEADERS, "Authorization": f"Bearer {token}"}
    if account_id:
        h["ChatGPT-Account-Id"] = account_id
    url = "https://chatgpt.com/backend-api/wham/usage"
    return _chatgpt_get(url, h, cookies) if cookies else _api_get(url, h)


def _wham_limit_row(w: dict, label: str) -> LimitRow | None:
    """Build a LimitRow from one {used_percent, reset_at, limit_window_seconds} dict."""
    if not isinstance(w, dict) or "used_percent" not in w:
        return None
    pct = max(0, min(100, int(round(float(w.get("used_percent") or 0)))))
    resets_at = _parse_ts(w.get("reset_at"))
    if resets_at is None and w.get("reset_after_seconds") is not None:
        try:
            resets_at = time.time() + float(w["reset_after_seconds"])
        except (TypeError, ValueError):
            resets_at = None
    window = w.get("limit_window_seconds")
    window = int(window) if isinstance(window, (int, float)) and window > 0 else None
    return LimitRow(label, pct, fmt_reset_ts(resets_at), resets_at, window)


def _parse_wham_window(window: dict, label: str) -> LimitRow | None:
    """Parse a single rate-limit window dict into a LimitRow (primary window)."""
    if not window or not isinstance(window, dict):
        return None
    row = _wham_limit_row(window.get("primary_window") or {}, label)
    return row or LimitRow(label, 0, "")


def _parse_wham_secondary(window: dict, label: str) -> LimitRow | None:
    """Longer (usually weekly) window that Codex reports next to the 5h one."""
    if not window or not isinstance(window, dict):
        return None
    sw = window.get("secondary_window")
    if not isinstance(sw, dict):
        return None
    secs = sw.get("limit_window_seconds") or 0
    suffix = "Weekly" if not secs or secs >= DAY else "Long"
    return _wham_limit_row(sw, f"{label} {suffix}")


def _parse_wham_usage(data: dict) -> ProviderData:
    """Parse /backend-api/wham/usage response.

    Confirmed shape (2026-02):
      rate_limit.primary_window.used_percent  (0-100)
      rate_limit.primary_window.reset_at      (Unix timestamp)
      code_review_rate_limit  -- same structure
    """
    log.debug("wham/usage raw: %s", json.dumps(data, indent=2))

    rows: list[LimitRow] = []

    label_map = {
        "rate_limit":            "Codex Tasks",
        "code_review_rate_limit": "Code Review",
    }
    for key, label in label_map.items():
        row = _parse_wham_window(data.get(key), label)
        if row is not None:
            rows.append(row)
            secondary = _parse_wham_secondary(data.get(key), label)
            if secondary is not None:
                rows.append(secondary)

    # additional_rate_limits may be a list of extra buckets
    for extra in (data.get("additional_rate_limits") or []):
        if isinstance(extra, dict):
            name = extra.get("name") or extra.get("type") or "Extra"
            label = name.replace("_", " ").title()
            row = _parse_wham_window(extra, label)
            if row:
                rows.append(row)
                secondary = _parse_wham_secondary(extra, label)
                if secondary is not None:
                    rows.append(secondary)

    if not rows:
        return ProviderData("ChatGPT", error="No rate limit data in response")

    worst = max(rows, key=lambda r: r.pct)
    pd = ProviderData("ChatGPT", spent=float(worst.pct), limit=100.0, currency="")
    pd._rows = rows
    return pd


def fetch_chatgpt(cookie_str: str) -> ProviderData:
    """Fetch ChatGPT / Codex usage via /backend-api/wham/usage (browser session)."""
    cookies = parse_cookie_string(cookie_str)
    try:
        session = _chatgpt_session(cookies)
        token = session.get("accessToken")
        if not token:
            return ProviderData("ChatGPT", error="Not logged in", source="browser")
        acct, email = _openai_identity(token)
        user = session.get("user") if isinstance(session.get("user"), dict) else {}
        workspace = _chatgpt_workspace(cookies)
        pd = _parse_wham_usage(_wham_get(token, workspace, cookies))
        pd.source, pd.account_id = "browser", workspace or acct
        pd.account_label = email or str(user.get("email") or "")
        return pd
    except Exception as e:
        log.debug("fetch_chatgpt failed: %s", e)
        return ProviderData("ChatGPT", error=str(e)[:80], source="browser")


# ── ChatGPT via Codex CLI (~/.codex/auth.json) ───────────────────────────────

def codex_auth_file() -> str:
    home = os.environ.get("CODEX_HOME") or "~/.codex"
    return os.path.join(os.path.expanduser(home), "auth.json")


def read_codex_auth() -> dict | None:
    """The ChatGPT sign-in Codex CLI keeps in ~/.codex/auth.json, or None.

    Read-only on purpose: refreshing the token here would rotate Codex's
    refresh token and sign Codex itself out. Codex refreshes it whenever it
    runs, so reading the file each cycle always gets the newest one.
    """
    try:
        with open(codex_auth_file()) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    tokens = data.get("tokens") if isinstance(data, dict) else None
    if not isinstance(tokens, dict) or not tokens.get("access_token"):
        return None      # signed out, or an API-key login (no plan limits)
    token = str(tokens["access_token"])
    acct, email = _openai_identity(token, tokens.get("id_token"))
    exp = _jwt_claims(token).get("exp")
    return {"access_token": token, "account_id": str(tokens.get("account_id") or acct),
            "email": email,
            "expires_at": float(exp) if isinstance(exp, (int, float)) else None}


def fetch_chatgpt_codex(auth: dict | None = None) -> ProviderData:
    """ChatGPT / Codex usage with the Codex CLI's token instead of a browser cookie."""
    auth = auth or read_codex_auth()
    if not auth:
        return ProviderData("ChatGPT", error="Not logged in", source="codex")
    if auth.get("expires_at") and auth["expires_at"] < time.time():
        return ProviderData("ChatGPT", error="Codex sign-in expired", source="codex",
                            account_label=auth.get("email", ""),
                            account_id=auth.get("account_id", ""))
    try:
        pd = _parse_wham_usage(_wham_get(auth["access_token"], auth.get("account_id", "")))
    except Exception as e:
        log.debug("fetch_chatgpt_codex failed: %s", e)
        pd = ProviderData("ChatGPT", error=str(e)[:80])
    pd.source, pd.account_label = "codex", auth.get("email", "")
    pd.account_id = auth.get("account_id", "")
    return pd


def fetch_chatgpt_with_fallback(cookie_str: str | None, codex_auth: dict | None = None,
                                fetch_cookie=None) -> ProviderData:
    """Browser session first; if it is missing or fails, Codex CLI's sign-in
    (`codex_auth`, from read_codex_auth(); None to skip it)."""
    fetch_cookie = fetch_cookie or fetch_chatgpt
    try:
        pd = fetch_cookie(cookie_str) if cookie_str else None
    except Exception as e:
        pd = ProviderData("ChatGPT", error=str(e)[:80], source="browser")
    if pd is not None and not pd.error:
        return pd
    auth = codex_auth
    if auth:
        cpd = fetch_chatgpt_codex(auth)
        if not cpd.error:
            log.info("ChatGPT: using ~/.codex/auth.json token (browser session %s)",
                     "stale" if pd is not None else "not found")
            return cpd
        if pd is None:
            return cpd
    return pd or ProviderData("ChatGPT", error="Not logged in")


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


def _next_month_reset(now: datetime | None = None) -> tuple[float, int]:
    """(timestamp of next 1st-of-month 00:00 UTC, length of this month in secs)."""
    now = now or datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return end.timestamp(), int((end - start).total_seconds())


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
        # Premium request counters reset on the 1st of each month, 00:00 UTC.
        resets_at, window = _next_month_reset()
        return ProviderData(
            "Copilot", spent=used, limit=limit or None,
            currency="", period="this month",
            reset_str=fmt_reset_ts(resets_at), resets_at=resets_at,
            window_secs=window,
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
        # Billing cycle: end is the reset; start (when present) gives the window.
        resets_at = _parse_ts(data.get("billingCycleEnd"))
        cycle_start = _parse_ts(data.get("billingCycleStart"))
        window = None
        if resets_at and cycle_start and resets_at > cycle_start:
            window = int(resets_at - cycle_start)
        elif resets_at:
            window = 30 * DAY   # monthly plans
        reset_str = fmt_reset_ts(resets_at)
        rows = [
            LimitRow("Auto", auto_pct, reset_str, resets_at, window),
            LimitRow("API", api_pct, reset_str, resets_at, window),
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
# Optional: [[browser, cookie_file, label], ...] to read specific profiles.
specs   = json.loads(sys.argv[3]) if len(sys.argv) > 3 else None

BROWSERS = [
    'firefox', 'librewolf', 'chrome', 'arc', 'brave',
    'edge', 'chromium', 'opera', 'vivaldi', 'safari',
]
if specs is None:
    specs = [[name, None, name] for name in BROWSERS]

# Collect candidates from every browser that has the target cookie.
# Rank by expiry as a hint, but the caller VALIDATES each candidate and
# uses the first that actually authenticates -- a stale session in one
# browser must never mask a valid one in another.
candidates = []  # list of (expires_seconds, cookie_str, label)

try:
    import browser_cookie3
    for name, cookie_file, label in specs:
        fn = getattr(browser_cookie3, name, None)
        if fn is None:
            continue
        try:
            if cookie_file:
                jar = fn(cookie_file=cookie_file, domain_name=domain)
            else:
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
            candidates.append((expires, cookie_str, label))
        except Exception:
            pass
except Exception:
    pass

# Rank best-first: latest (normalized) expiry, tie-break by richest jar.
candidates.sort(key=lambda x: (x[0], len(x[1])), reverse=True)
if len(sys.argv) > 3:
    result = [{"cookie": c[1], "source": c[2]} for c in candidates]
else:
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


# Chromium-based browsers keep one cookie database per profile under their
# user-data folder; browser_cookie3 on its own only reads the first one.
_CHROMIUM_DIRS = {
    "chrome":   ("Chrome", "Google/Chrome"),
    "arc":      ("Arc", "Arc/User Data"),
    "brave":    ("Brave", "BraveSoftware/Brave-Browser"),
    "edge":     ("Edge", "Microsoft Edge"),
    "chromium": ("Chromium", "Chromium"),
    "vivaldi":  ("Vivaldi", "Vivaldi"),
}
_FIREFOX_DIRS = {
    "firefox":   ("Firefox", "Firefox/Profiles"),
    "librewolf": ("LibreWolf", "librewolf/Profiles"),
}
_SINGLE_PROFILE = (("opera", "Opera"), ("safari", "Safari"))


def _chromium_profile_names(user_data: str) -> dict:
    """Profile folder -> the name the person gave it (from "Local State")."""
    try:
        with open(os.path.join(user_data, "Local State")) as f:
            cache = json.load(f)["profile"]["info_cache"]
        return {k: v.get("name") for k, v in cache.items() if isinstance(v, dict)}
    except Exception:
        return {}


def browser_profiles(home: str | None = None) -> list[list]:
    """[browser, cookie file, label] for every profile of every browser found.

    Lets account discovery see a second Chrome profile (or Firefox profile)
    signed in to another account. Browsers without separate profiles are
    read the normal way (cookie file None).
    """
    support = os.path.join(home or os.path.expanduser("~"), "Library", "Application Support")
    specs: list[list] = []
    for name, (title, rel) in _CHROMIUM_DIRS.items():
        base = os.path.join(support, rel)
        names = None
        for prof in sorted(glob.glob(os.path.join(base, "*", ""))):
            prof = prof.rstrip(os.sep)
            folder = os.path.basename(prof)
            if folder in ("System Profile", "Guest Profile"):
                continue
            # Chrome 96+ moved the database to Network/Cookies; an old copy can linger.
            path = next((p for p in (os.path.join(prof, "Network", "Cookies"),
                                     os.path.join(prof, "Cookies")) if os.path.isfile(p)), None)
            if path:
                if names is None:
                    names = _chromium_profile_names(base)
                specs.append([name, path, f"{title} · {names.get(folder) or folder}"])
    for name, (title, rel) in _FIREFOX_DIRS.items():
        for path in sorted(glob.glob(os.path.join(support, rel, "*", "cookies.sqlite"))):
            folder = os.path.basename(os.path.dirname(path))
            specs.append([name, path, f"{title} · {folder.split('.', 1)[-1]}"])
    specs += [[name, None, title] for name, title in _SINGLE_PROFILE]
    return specs


def discover_sessions(domain: str, target_cookie: str) -> list[dict]:
    """Every distinct session for `domain` across all browsers and profiles.

    Items: {"cookie": str, "source": "Chrome · Work"}, best first. Unlike
    _run_cookie_detection() (which feeds the main account) this looks at
    every profile, so it can find a second account.
    """
    specs = browser_profiles()
    # Also the browsers' own default lookup, in case a profile lives elsewhere.
    specs += [[name, None, title] for name, (title, _rel) in
              {**_CHROMIUM_DIRS, **_FIREFOX_DIRS}.items()]
    try:
        r = subprocess.run(
            [sys.executable, "-c", _DETECT_SCRIPT, domain, target_cookie, json.dumps(specs)],
            capture_output=True, text=True, timeout=120,
        )
        log.debug("session discovery rc=%d found=%s err=%r", r.returncode,
                  r.stdout.count('"cookie"'), r.stderr[:200])
        data = json.loads(r.stdout.strip() or "[]")
    except Exception as e:
        log.debug("discover_sessions failed: %s", e)
        return []
    seen, out = set(), []
    for item in data if isinstance(data, list) else []:
        cookie = item.get("cookie") if isinstance(item, dict) else None
        if cookie and cookie not in seen:
            seen.add(cookie)
            out.append({"cookie": cookie, "source": str(item.get("source") or "")})
    return out


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
