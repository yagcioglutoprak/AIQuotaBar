"""Extra accounts: discovery merge rules, keys, tags (pure Python, no network)."""

import pytest

from aiquotabar import accounts as acc

A, B, C, D = ("aaaaaaaa-0000-0000-0000-000000000001", "bbbbbbbb-0000-0000-0000-000000000002",
              "cccccccc-0000-0000-0000-000000000003", "dddddddd-0000-0000-0000-000000000004")


def orgs_fn(table):
    """claude_orgs stand-in: cookie -> org list; unknown cookies are dead sessions."""
    def fn(cookie):
        if cookie not in table:
            raise RuntimeError("403 session expired")
        return table[cookie]
    return fn


def org(uuid, label, chat=True):
    return {"uuid": uuid, "label": label, "chat": chat}


def test_keys_are_stable_short_hashes():
    k = acc.make_key("claude", A)
    assert k == acc.make_key("claude", A) and k.startswith("claude@") and len(k) == 15
    assert acc.is_account_key(k)
    assert not acc.is_account_key("claude") and not acc.is_account_key("cursor@12345678")
    assert not acc.is_account_key("claude@XYZ") and not acc.is_account_key(None)


def test_claude_adds_other_orgs_and_sessions_but_never_the_main_one():
    cfg = {"cookie_str": f"sessionKey=main; lastActiveOrg={A}"}
    table = {
        cfg["cookie_str"]: [org(A, "me@home.com"), org(B, "Acme Team"), org(C, "API", chat=False)],
        "sessionKey=work": [org(D, "me@acme.com")],
    }
    sessions = [{"cookie": cfg["cookie_str"], "source": "Firefox"},
                {"cookie": "sessionKey=work", "source": "Chrome · Work"},
                {"cookie": "sessionKey=dead", "source": "Safari"}]
    out = acc.find_claude_accounts(cfg, sessions, orgs_fn(table))
    assert out == {"added": 2, "refreshed": 0, "primary": False}
    extras = acc.extra_accounts(cfg, "claude")
    assert [(a["org_id"], a["label"], a["source"]) for a in extras] == [
        (B, "Acme Team", "Firefox"), (D, "me@acme.com", "Chrome · Work")]
    assert extras[1]["cookie"] == "sessionKey=work"

    # Scanning again adds nothing; a fresher cookie for a known org is picked up.
    table["sessionKey=work2"] = table.pop("sessionKey=work")
    sessions[1]["cookie"] = "sessionKey=work2"
    out = acc.find_claude_accounts(cfg, sessions, orgs_fn(table))
    assert out == {"added": 0, "refreshed": 1, "primary": False}
    assert len(acc.extra_accounts(cfg)) == 2
    assert acc.find_account(cfg, acc.make_key("claude", D))["cookie"] == "sessionKey=work2"


def test_claude_first_session_becomes_main_account_when_none():
    cfg = {}
    table = {"sessionKey=a": [org(A, "me@home.com")], "sessionKey=b": [org(B, "me@acme.com")]}
    out = acc.find_claude_accounts(cfg, [{"cookie": "sessionKey=a", "source": "Firefox"},
                                         {"cookie": "sessionKey=b", "source": "Chrome"}],
                                   orgs_fn(table))
    assert out["primary"] and out["added"] == 1
    assert cfg["cookie_str"] == "sessionKey=a"
    assert [a["org_id"] for a in acc.extra_accounts(cfg)] == [B]


def test_claude_dead_main_session_is_refreshed_not_duplicated():
    cfg = {"cookie_str": f"sessionKey=stale; lastActiveOrg={A}"}
    table = {"sessionKey=fresh": [org(A, "me@home.com")]}
    out = acc.find_claude_accounts(cfg, [{"cookie": "sessionKey=fresh", "source": "Chrome"}],
                                   orgs_fn(table))
    assert out == {"added": 0, "refreshed": 1, "primary": False}
    assert cfg["cookie_str"] == "sessionKey=fresh" and not acc.extra_accounts(cfg)


def test_removed_accounts_stay_removed_until_unhidden():
    cfg = {"cookie_str": f"sessionKey=main; lastActiveOrg={A}",
           "bar_providers": ["Claude", acc.make_key("claude", B)]}
    table = {cfg["cookie_str"]: [org(A, "me"), org(B, "Team")]}
    sessions = [{"cookie": cfg["cookie_str"], "source": "Firefox"}]
    acc.find_claude_accounts(cfg, sessions, orgs_fn(table))
    key = acc.make_key("claude", B)
    assert acc.remove_account(cfg, key)
    assert not acc.extra_accounts(cfg) and cfg["bar_providers"] == ["Claude"]
    assert acc.hidden_count(cfg, "claude") == 1 and acc.hidden_count(cfg, "chatgpt") == 0
    assert acc.find_claude_accounts(cfg, sessions, orgs_fn(table))["added"] == 0
    acc.unhide(cfg, "claude")
    assert acc.find_claude_accounts(cfg, sessions, orgs_fn(table))["added"] == 1
    assert not acc.remove_account(cfg, "claude@00000000")


def ident_fn(table):
    def fn(cookie):
        if cookie not in table:
            raise RuntimeError("401")
        return table[cookie]
    return fn


def test_chatgpt_browser_profiles_and_codex():
    cfg = {"chatgpt_cookies": "tok=main"}
    table = {"tok=main": ("acct-1", "me@home.com"), "tok=work": ("acct-2", "me@acme.com"),
             "tok=main-again": ("acct-1", "me@home.com")}
    codex = {"account_id": "acct-3", "email": "me@lab.org", "access_token": "x"}
    sessions = [{"cookie": "tok=main-again", "source": "Chrome"},
                {"cookie": "tok=work", "source": "Chrome · Work"}]
    out = acc.find_chatgpt_accounts(cfg, sessions, codex, ident_fn(table))
    assert out == {"added": 2, "refreshed": 0, "primary": False}
    extras = acc.extra_accounts(cfg, "chatgpt")
    assert [(a["account_id"], a["label"], a["source"]) for a in extras] == [
        ("acct-2", "me@acme.com", "Chrome · Work"), ("acct-3", "me@lab.org", "codex")]
    assert "cookie" not in extras[1]            # Codex tokens are read fresh, never stored
    assert acc.codex_is_extra(cfg)
    assert acc.find_chatgpt_accounts(cfg, sessions, codex, ident_fn(table))["added"] == 0


def test_chatgpt_codex_same_account_as_main_is_not_duplicated():
    cfg = {"chatgpt_cookies": "tok=main"}
    table = {"tok=main": ("acct-1", "me@home.com")}
    codex = {"account_id": "acct-1", "email": "me@home.com", "access_token": "x"}
    assert acc.find_chatgpt_accounts(cfg, [], codex, ident_fn(table))["added"] == 0
    assert not acc.codex_is_extra(cfg)


def test_chatgpt_without_browser_session_codex_backs_the_main_account():
    # Only Codex: it is the main account (via fallback), nothing extra.
    cfg = {}
    codex = {"account_id": "acct-1", "email": "me@home.com", "access_token": "x"}
    assert acc.find_chatgpt_accounts(cfg, [], codex, ident_fn({})) == \
        {"added": 0, "refreshed": 0, "primary": False}
    # A browser session for that same account becomes the main cookie.
    table = {"tok=a": ("acct-1", "me@home.com"), "tok=b": ("acct-2", "me@acme.com")}
    out = acc.find_chatgpt_accounts(cfg, [{"cookie": "tok=b", "source": "Firefox"},
                                         {"cookie": "tok=a", "source": "Chrome"}],
                                    codex, ident_fn(table))
    assert out == {"added": 1, "refreshed": 0, "primary": True}
    assert cfg["chatgpt_cookies"] == "tok=a"
    assert [a["account_id"] for a in acc.extra_accounts(cfg)] == ["acct-2"]


def test_chatgpt_dead_main_session_is_replaced_by_the_best_fresh_one():
    cfg = {"chatgpt_cookies": "tok=dead"}
    table = {"tok=fresh": ("acct-1", "me@home.com")}
    out = acc.find_chatgpt_accounts(cfg, [{"cookie": "tok=fresh", "source": "Chrome"}], None,
                                    ident_fn(table))
    assert out == {"added": 0, "refreshed": 1, "primary": False}
    assert cfg["chatgpt_cookies"] == "tok=fresh" and not acc.extra_accounts(cfg)


def test_extra_account_limit():
    cfg = {"cookie_str": f"k=main; lastActiveOrg={A}"}
    many = [org(f"{i:08x}-0000-0000-0000-000000000000", f"org {i}") for i in range(20)]
    table = {cfg["cookie_str"]: [org(A, "me")] + many}
    acc.find_claude_accounts(cfg, [{"cookie": cfg["cookie_str"], "source": "x"}], orgs_fn(table))
    assert len(acc.extra_accounts(cfg)) == acc.MAX_EXTRA


@pytest.mark.parametrize("label,others,tag", [
    ("jo.smith@acme.com", [], "jo.smith"),
    ("jo@acme.com", ["jo@gmail.com"], "acme"),
    ("jo@gmail.com", ["jo@acme.com"], "jo"),          # webmail domains never become the tag
    ("Acme Team", ["me@home.com"], "Acme Team"),
    ("a-very-long-name@x.com", [], "a-very-lon"),
    ("", [], "#2"),
])
def test_short_tag(label, others, tag):
    assert acc.short_tag(label, others) == tag


def test_extra_accounts_ignores_malformed_entries():
    cfg = {acc.CONFIG_KEY: [None, {"key": "claude@zz"}, {"key": "cursor@12345678", "provider": "cursor"},
                            {"key": acc.make_key("claude", A), "provider": "claude", "label": "ok"}]}
    assert [a["label"] for a in acc.extra_accounts(cfg)] == ["ok"]
    assert acc.account_labels(cfg) == {acc.make_key("claude", A): "ok"}
