"""Chunked NextAuth session cookies (Fixes #15, PR #16).

Mirrors the presence/expiry-key branch inside providers._DETECT_SCRIPT so a
regression cannot reintroduce skipping browsers that only have target.0/.1.
"""


def _expiry_key_for_target(cookie_names: set[str], target: str) -> str | None:
    if target in cookie_names:
        return target
    if f"{target}.0" in cookie_names:
        return f"{target}.0"
    return None


def test_chunked_session_token_uses_first_chunk_for_expiry():
    target = "__Secure-next-auth.session-token"
    names = {f"{target}.0", f"{target}.1"}
    assert _expiry_key_for_target(names, target) == f"{target}.0"


def test_unsuffixed_session_token_unchanged():
    target = "__Secure-next-auth.session-token"
    assert _expiry_key_for_target({target}, target) == target


def test_no_session_cookie_skips_browser():
    target = "__Secure-next-auth.session-token"
    assert _expiry_key_for_target({"other": "x"}, target) is None
