"""More than one Claude or ChatGPT account, side by side.

The main account of each provider keeps its original config keys
(`cookie_str`, `chatgpt_cookies`), so the menu bar, widget and alerts work
exactly as before. Every other account is an entry in
config["extra_accounts"]:

    {"key": "claude@3f9a1c2b", "provider": "claude", "label": "jo@work.com",
     "cookie": "...", "org_id": "<organization uuid>", "source": "Chrome · Work"}
    {"key": "chatgpt@91be04d7", "provider": "chatgpt", "label": "jo@home.com",
     "cookie": "...", "account_id": "...", "source": "Firefox · default-release"}
    {"key": "chatgpt@5c0e77aa", "provider": "chatgpt", "label": "jo@home.com",
     "account_id": "...", "source": "codex"}          # read from ~/.codex/auth.json

Accounts are found, not typed in: discovery reads every browser *profile*
(a second Chrome profile signed in to another account is the usual setup),
every organization a Claude sign-in belongs to (personal + Team plan), and
Codex CLI's sign-in for ChatGPT. The key is a short hash of the provider's
own account id, so it survives cookie refreshes and history / alert keys
built from it never contain raw ids.

Pure Python: the network calls are passed in, so this runs in tests anywhere.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from aiquotabar.providers import ProviderData, UsageData, claude_default_org

MULTI_PROVIDERS = ("claude", "chatgpt")
CONFIG_KEY = "extra_accounts"
IGNORED_KEY = "ignored_accounts"     # removed by the user: discovery won't re-add them
MAX_EXTRA = 8
PRIMARY_KEYS = {"claude": "cookie_str", "chatgpt": "chatgpt_cookies"}


@dataclass
class AccountUsage:
    """The latest fetch for one extra account."""
    key: str
    provider: str                       # "claude" | "chatgpt"
    label: str
    claude: UsageData | None = None     # Claude accounts
    data: ProviderData | None = None    # ChatGPT accounts
    error: dict | None = None           # {"kind": "auth"|"network", "message": str}
    source: str = ""


def make_key(provider: str, ident: str) -> str:
    return f"{provider}@{hashlib.sha1(ident.encode()).hexdigest()[:8]}"


def is_account_key(key) -> bool:
    if not isinstance(key, str) or "@" not in key:
        return False
    pid, _, h = key.partition("@")
    return pid in MULTI_PROVIDERS and len(h) == 8 and all(c in "0123456789abcdef" for c in h)


def extra_accounts(cfg: dict, provider: str | None = None) -> list[dict]:
    """The well-formed extra accounts in config, optionally for one provider."""
    out = []
    for a in cfg.get(CONFIG_KEY) or []:
        if (isinstance(a, dict) and a.get("provider") in MULTI_PROVIDERS
                and is_account_key(a.get("key"))
                and (provider is None or a["provider"] == provider)):
            out.append(a)
    return out


def find_account(cfg: dict, key: str) -> dict | None:
    return next((a for a in extra_accounts(cfg) if a["key"] == key), None)


def account_labels(cfg: dict) -> dict:
    return {a["key"]: a.get("label") or "" for a in extra_accounts(cfg)}


_WEBMAIL = {"gmail", "googlemail", "outlook", "hotmail", "live", "msn", "icloud", "me", "mac",
            "yahoo", "proton", "protonmail", "pm", "hey", "fastmail", "aol", "gmx", "yandex",
            "zoho", "mail", "qq", "163", "naver"}


def short_tag(label: str, others=(), limit: int = 10) -> str:
    """A short menu bar tag for an extra account.

    'jo.smith@work.com' -> 'jo.smith'; when that name is shared with another
    of the person's accounts (`others`), the company instead: 'jo@acme.com'
    next to 'jo@gmail.com' -> 'acme'.
    """
    local, _, domain = (label or "").partition("@")
    tag = local.strip()
    org = domain.split(".")[0].strip()
    if org and org.lower() not in _WEBMAIL and any(
            o and o != label and o.partition("@")[0].strip().lower() == tag.lower() for o in others):
        tag = org
    return tag[:limit] if tag else "#2"


def remove_account(cfg: dict, key: str) -> bool:
    """Drop an extra account and remember not to re-add it on the next scan."""
    before = len(cfg.get(CONFIG_KEY) or [])
    cfg[CONFIG_KEY] = [a for a in cfg.get(CONFIG_KEY) or []
                       if not (isinstance(a, dict) and a.get("key") == key)]
    if len(cfg[CONFIG_KEY]) == before:
        return False
    ignored = cfg.setdefault(IGNORED_KEY, [])
    if key not in ignored:
        ignored.append(key)
    bar = cfg.get("bar_providers")
    if isinstance(bar, list) and key in bar:
        bar.remove(key)
        if not bar:
            cfg.pop("bar_providers", None)
    return True


def hidden_count(cfg: dict, provider: str) -> int:
    return sum(1 for k in cfg.get(IGNORED_KEY) or []
               if isinstance(k, str) and k.startswith(provider + "@"))


def unhide(cfg: dict, provider: str) -> None:
    cfg[IGNORED_KEY] = [k for k in cfg.get(IGNORED_KEY) or []
                        if not (isinstance(k, str) and k.startswith(provider + "@"))]


def _add(cfg: dict, acct: dict) -> bool:
    if acct["key"] in (cfg.get(IGNORED_KEY) or []):
        return False
    if len(extra_accounts(cfg)) >= MAX_EXTRA:
        return False
    cfg.setdefault(CONFIG_KEY, []).append(acct)
    return True


def _summary() -> dict:
    return {"added": 0, "refreshed": 0, "primary": False}


# ── discovery ────────────────────────────────────────────────────────────────

def find_claude_accounts(cfg: dict, sessions: list[dict], orgs_fn) -> dict:
    """Add every Claude organization reachable from `sessions` (mutates cfg).

    sessions: [{"cookie", "source"}] best first (providers.discover_sessions).
    orgs_fn(cookie) -> providers.claude_orgs(cookie); raises for a dead session.

    The main account's organization is never duplicated. Known accounts get
    the freshest working cookie. With no main account yet, the first working
    session becomes it.
    """
    out = _summary()
    primary_org, primary_dead = None, False
    if cfg.get("cookie_str"):
        try:
            primary_org = claude_default_org(cfg["cookie_str"], orgs_fn(cfg["cookie_str"]))
        except Exception:
            # Dead main session: it still owns the org its cookie names.
            primary_org = claude_default_org(cfg["cookie_str"], [])
            primary_dead = True
    known = {a.get("org_id"): a for a in extra_accounts(cfg, "claude")}
    seen = set()
    for s in sessions:
        try:
            orgs = orgs_fn(s["cookie"])
        except Exception:
            continue
        if not orgs:
            continue
        uids = {o["uuid"] for o in orgs}
        if not cfg.get("cookie_str") or (primary_dead and primary_org is None):
            # No (identifiable) main account: this session becomes it.
            cfg["cookie_str"] = s["cookie"]
            primary_org, primary_dead = claude_default_org(s["cookie"], orgs), False
            out["primary"] = True
        elif primary_dead and primary_org in uids:
            cfg["cookie_str"] = s["cookie"]       # a fresh session for the main account
            primary_dead = False
            out["refreshed"] += 1
        for org in orgs:
            uid = org["uuid"]
            if uid == primary_org or uid in seen or not org.get("chat", True):
                continue
            seen.add(uid)
            if uid in known:
                if known[uid].get("cookie") != s["cookie"]:
                    known[uid]["cookie"] = s["cookie"]
                    known[uid]["source"] = s.get("source", "")
                    out["refreshed"] += 1
                continue
            acct = {"key": make_key("claude", uid), "provider": "claude",
                    "label": org.get("label") or "Claude account", "cookie": s["cookie"],
                    "org_id": uid, "source": s.get("source", "")}
            if _add(cfg, acct):
                known[uid] = acct
                out["added"] += 1
    return out


def find_chatgpt_accounts(cfg: dict, sessions: list[dict], codex: dict | None,
                          identity_fn) -> dict:
    """Add every ChatGPT account from browser `sessions` and Codex CLI (mutates cfg).

    identity_fn(cookie) -> (account id, email); raises for a dead session.
    codex: providers.read_codex_auth() or None.

    Without a browser session the main account already falls back to Codex,
    so Codex is only added as an extra when it is a different account.
    """
    out = _summary()
    primary_id, primary_known = None, True
    if cfg.get("chatgpt_cookies"):
        try:
            primary_id = identity_fn(cfg["chatgpt_cookies"])[0] or None
        except Exception:
            # A dead main session can't say whose it was; the best fresh
            # session replaces it, as the regular re-detect would.
            primary_known = False
    if primary_id is None and codex and not cfg.get("chatgpt_cookies"):
        primary_id = codex.get("account_id") or None
    known = {a.get("account_id"): a for a in extra_accounts(cfg, "chatgpt")}
    seen = set()
    for s in sessions:
        try:
            acct_id, email = identity_fn(s["cookie"])
        except Exception:
            continue
        if not acct_id or acct_id in seen:
            continue
        seen.add(acct_id)
        if (not cfg.get("chatgpt_cookies") or not primary_known) and primary_id in (None, acct_id):
            if cfg.get("chatgpt_cookies"):
                out["refreshed"] += 1
            else:
                out["primary"] = True
            cfg["chatgpt_cookies"] = s["cookie"]
            primary_id, primary_known = acct_id, True
            continue
        if acct_id == primary_id:
            continue
        if acct_id in known:
            k = known[acct_id]
            if k.get("source") != "codex" and k.get("cookie") != s["cookie"]:
                k["cookie"], k["source"] = s["cookie"], s.get("source", "")
                out["refreshed"] += 1
            continue
        acct = {"key": make_key("chatgpt", acct_id), "provider": "chatgpt",
                "label": email or "ChatGPT account", "cookie": s["cookie"],
                "account_id": acct_id, "source": s.get("source", "")}
        if _add(cfg, acct):
            known[acct_id] = acct
            out["added"] += 1
    cid = (codex or {}).get("account_id")
    if cid and cid != primary_id and cid not in known:
        acct = {"key": make_key("chatgpt", cid), "provider": "chatgpt",
                "label": codex.get("email") or "Codex CLI", "account_id": cid,
                "source": "codex"}
        if _add(cfg, acct):
            out["added"] += 1
    return out


def codex_is_extra(cfg: dict) -> bool:
    """True when Codex CLI's sign-in is tracked as its own (extra) account."""
    return any(a.get("source") == "codex" for a in extra_accounts(cfg, "chatgpt"))
