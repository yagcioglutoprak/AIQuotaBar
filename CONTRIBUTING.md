# Contributing to AIQuotaBar

Thanks for helping! AIQuotaBar is a small, plain-Python codebase with no build step, and most contributions touch only one or two files.

**Looking for something to work on?** Try the [`good first issue`](https://github.com/yagcioglutoprak/AIQuotaBar/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) and [`help wanted`](https://github.com/yagcioglutoprak/AIQuotaBar/issues?q=is%3Aissue+is%3Aopen+label%3A%22help+wanted%22) issues, or the [roadmap](https://github.com/yagcioglutoprak/AIQuotaBar/issues/28). Comment on an issue before you start so two people don't build the same thing. For bigger changes, open an issue first.

## Set up

```bash
git clone https://github.com/yagcioglutoprak/AIQuotaBar.git && cd AIQuotaBar

# Run the tests (works on Linux and macOS; macOS APIs are faked)
pip install -r requirements-dev.txt
python3 -m pytest

# Run the app (macOS only)
pip install -r requirements.txt
python3 claude_bar.py --demo     # sample data, no accounts needed, nothing saved
python3 claude_bar.py            # your real accounts; logs go to ~/.claude_bar.log
```

The web UI tests run the real panel in headless Chromium. They're skipped unless Playwright has a browser (`python3 -m playwright install chromium`).

## How the code is organised

`claude_bar.py` is a thin shim. The app lives in `aiquotabar/`:

| File | What it does |
|---|---|
| `providers.py` | Fetchers for each service → `UsageData` / `ProviderData`. Browser cookie detection. |
| `viewmodel.py` | Pure Python: turns app state into the JSON the UI renders. Settings allow-list. |
| `web/` | The panel, settings, history and share card (HTML/CSS/JS, no build step, no remote assets). |
| `webview.py`, `ui.py` | The macOS shell: WKWebView host, menu bar title, fetch loop, alerts. |
| `theme.py` | Provider names, brand colours and icons (single source of truth). |
| `history.py` | Burn rate, SQLite history, 24h trends. |
| `demo.py` | Deterministic sample data for `--demo`, screenshots and tests. |

More detail, and the design decisions to preserve, are in [CLAUDE.md](CLAUDE.md).

## Add a provider

### API-key providers (spend or balance): 3 steps

1. Write `fetch_myprovider(api_key: str) -> ProviderData` in `aiquotabar/providers.py`. Return a `ProviderData` with `spent`/`limit` or `balance`, and `error=` on failure (never raise). `fetch_minimax()` and `fetch_openai()` are good templates.
2. Add one entry to `PROVIDER_REGISTRY`: `"myprovider_key": ("MyProvider", fetch_myprovider)`.
3. That's it. Settings → Accounts and the panel's extra rows pick it up automatically.

Add a unit test that monkeypatches the HTTP call. No real keys, no network.

### Quota providers with their own card (plan limits, browser session)

Do the 3 steps above with a cookie-based fetcher, then also add:

- an entry in `theme.PROVIDERS` and `PROVIDER_ORDER` (plus an icon in `assets/`)
- the config key in `viewmodel.COOKIE_KEYS` (and the usage page in `viewmodel.USAGE_PAGES`)
- a detector in `ui.DETECTORS` using `_run_cookie_detection(domain, cookie_name)`
- sample data in `demo.py`

Set `resets_at` / `window_secs` on every `LimitRow` so the pace marker and "runs out at this pace" warning work.

## Guidelines

- **Never commit cookies, tokens, API keys or real account data.** That includes test fixtures, logs and screenshots.
- In `aiquotabar/web/`, render untrusted text with `textContent`, never `innerHTML`, and don't load remote scripts, fonts or images.
- Every action the page sends is validated in Python (`viewmodel.apply_setting`, `ui._safe_url`). Keep it that way.
- Don't call `rumps.notification()` directly. Use `_notify()`.
- Keep PRs focused, and keep the README short (longer docs go in `docs/`).
- UI changes: include a before/after screenshot (`python3 claude_bar.py --demo` gives you clean sample data).
- AI-assisted contributions are fine, but review every line and make sure you can explain and test the change yourself.

## Reporting bugs and security issues

- Bugs: use the **Bug report** form, and include `tail -50 ~/.claude_bar.log` with cookies removed.
- Security vulnerabilities: please report privately. See [SECURITY.md](SECURITY.md).

By contributing, you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md) and that your contributions are licensed under the [MIT License](LICENSE).
