# CLAUDE.md — AIQuotaBar

## Project goal

**Get maximum GitHub stars and widespread adoption.**
North star: **225 stars** (threshold to resubmit homebrew-core PR).
Every change must either (a) convert more visitors to stars, (b) bring new visitors, or (c) make the app so good people share it organically.

### Current stats (2026-03-01)
- 6 stars, 0 forks
- Early stage — need to build real traction from scratch

### Growth priorities (in order)
1. **Fix star conversion** — README must convince in 5 seconds (hook → GIF → install)
2. **Social proof loop** — HN/PH badges, testimonials, star count badge visible
3. **Distribution** — awesome-lists, Reddit, dev.to, Twitter/X, YouTube demos
4. **Shareability features** — screenshot/share menu item, referral nudges in-app
5. **Cross-platform** — Linux tray port opens 70% more developers
6. **SEO** — GitHub Pages landing page, proper meta tags, backlinks

### Channel status
| Channel | Status | Next action |
|---------|--------|-------------|
| HN | Show HN posted | Repost if no traction |
| Product Hunt | Not submitted | Submit on a Tuesday 12:01 AM PST |
| awesome-mac PR #1833 | Open | Follow up if stale >7d |
| open-source-mac-os-apps PR #1041 | Open | Follow up if stale >7d |
| awesome-claude PR #60 | Open | Follow up if stale >7d |
| awesome-claude-code #888 | Auto-closed | Resubmit after 2026-03-05 |
| Reddit r/ClaudeAI | Rejected once | Repost with value-first copy |
| Reddit r/ChatGPT, r/macapps, r/commandline | Not posted | Post with screenshots |
| AlternativeTo | Listed | Done |
| dev.to | Published | Done |
| Twitter/X | Not posted | Post demo GIF + install command |
| GitHub Pages | Live | Improve SEO meta tags |
| Homebrew core | Needs 225 stars | Blocked on stars |
| Indie Hackers | Blocked (new account) | Build karma via comments |

## What this is

A native macOS menu bar app (Python + rumps) that shows live Claude, ChatGPT, Cursor, and GitHub Copilot usage limits. It reads cookies from the user's browser (no manual copy-paste), calls provider APIs, and displays the result as a status bar icon (`🟢 4%`, `🟡 83%`, `🔴 100%`).

## Architecture

Single file: `claude_bar.py` (~900 lines). No build step. No framework.

```
claude_bar.py
├── Config         load_config / save_config  (~/.claude_bar_config.json)
├── Claude API     fetch_raw → _get / _org_id_from_api
├── Provider APIs  fetch_openai / fetch_minimax / fetch_glm → ProviderData
│                  PROVIDER_REGISTRY: cfg_key → (name, fetch_fn)
├── Parser         parse_usage → UsageData(session, weekly_all, weekly_sonnet)
├── Display        _bar / _status_icon / _row_lines / _provider_lines
├── Cookie mgmt    _auto_detect_cookies → browser-cookie3 (Firefox first, then Chromium)
└── App            ClaudeBar(rumps.App) — timer, menu rebuild, callbacks
```

## Widget (optional)

A native macOS WidgetKit widget in `AIQuotaBarWidget/` shows usage on the desktop.

**Data flow:** `claude_bar.py` → `~/Library/Application Support/AIQuotaBar/usage.json` → WidgetKit reads it.

- `_write_widget_cache()` runs after every fetch cycle (atomic write, never crashes main app)
- Widget refreshes every 15 min via `TimelineProvider`
- Shows stale indicator if data is >30 min old
- Small widget: circular gauge (Claude session %). Medium: side-by-side bars
- Requires Xcode 15+ to build; entirely optional — menu bar app works without it

## Adding a new provider

1. Write `fetch_myprovider(api_key: str) -> ProviderData` — return `ProviderData` with `spent`/`limit` or `balance`
2. Add one entry to `PROVIDER_REGISTRY`: `"myprovider_key": ("MyProvider", fetch_myprovider)`
3. That's it — the menu item, key dialog, and display are all automatic.

## Key decisions to preserve

- **Session (5-hour) drives the status bar icon**, not the max of all limits.
  Weekly limits appear in the menu only. Rationale: session determines immediate access.
- **Firefox/LibreWolf first** in browser detection order — no Keychain prompt, zero friction.
  Chromium browsers (Arc, Chrome, Brave) come after; they need one-time "Always Allow".
- **API utilization scale is now consistent**: all fields (`five_hour`, `seven_day`,
  `seven_day_sonnet`) return 0–100 percentage. No conversion needed.
- **`rumps.notification` crashes** in dev (missing Info.plist CFBundleIdentifier).
  All notifications go through `_notify()` which swallows the exception silently.
- **Cookies are cached** in `~/.claude_bar_config.json`. Auto-detect runs on first launch
  and on repeated 401/403 failures to silently refresh the session.

## API behaviour (confirmed)

```
GET https://claude.ai/api/organizations/{org_id}/usage
```
Requires Cloudflare bypass — use `curl_cffi` with `impersonate="chrome131"`.

Response fields:
| Field              | Meaning                        | Utilization scale |
|--------------------|--------------------------------|-------------------|
| `five_hour`        | Current session (5-hr limit)   | 0–100 percentage  |
| `seven_day`        | Weekly all-models              | 0–100 percentage  |
| `seven_day_sonnet` | Weekly Sonnet-only             | 0–100 percentage  |
| `extra_usage`      | Overage toggle (null = off)    | —                 |

## Files

| File            | Purpose                                              |
|-----------------|------------------------------------------------------|
| `claude_bar.py` | Entire application                                   |
| `install.sh`    | One-line curl installer (detects Python, LaunchAgent)|
| `requirements.txt` | `rumps`, `curl_cffi`, `browser-cookie3`           |
| `setup.sh`      | Legacy manual installer (kept for reference)         |
| `assets/`       | demo.gif and screenshots for README                  |
| `AIQuotaBarWidget/` | Optional WidgetKit desktop widget (Xcode project)   |

## Growth / virality rules

- **README is a landing page, not docs.** Hook → GIF → install command must be above the fold.
  A visitor should understand value and install in under 10 seconds.
- **The demo GIF is the #1 driver of stars.** `assets/demo.gif` must be short, polished, and show
  the "aha moment": menu bar icon → click → full usage breakdown with colors.
- **Zero-friction install is non-negotiable.**
  `curl -fsSL .../install.sh | bash` must work end-to-end without manual steps.
  If it breaks, fix it before anything else.
- **Social proof converts.** Star count badge, download count, real testimonials — keep them visible.
  Never fake stats. Only show real numbers.
- **Every touchpoint should nudge stars.** Post-install terminal message, in-app menu item,
  GitHub Pages landing page — all link back to the repo.
- **Keep the README concise.** One install command, one GIF, short feature list.
  Long docs belong in a wiki, not the README.
- **GitHub topics to maintain** (set via repo Settings → About):
  `claude`, `anthropic`, `macos`, `menu-bar`, `usage-monitor`, `menubar-app`, `claude-ai`,
  `chatgpt`, `cursor`, `copilot`, `rate-limit`, `ai-tools`

## High-impact features to build (star drivers)

These features would make the app significantly more shareable:

1. **Gemini support** — Google's API has usage limits too; huge user base
2. **Linux system tray** — opens the app to 70% more developers (use pystray)
3. **Usage history chart** — "see your AI usage over time" is a compelling screenshot
4. **Share/export** — one-click screenshot of usage to clipboard (instant Twitter content)
5. **Claude Code / CLI tracking** — devs using Claude Code want to see limits too
6. **Notification Center widget** — already have WidgetKit, expose in NC for more visibility

## Dev workflow

```bash
# Run locally
python3 claude_bar.py

# Check logs
tail -f ~/.claude_bar.log

# Quick syntax check
python3 -m py_compile claude_bar.py

# Kill and restart
pkill -f claude_bar.py; sleep 1; python3 claude_bar.py &
```

## Do not

- Do not add a `session_key` field — the app uses full cookie strings, not just the session key.
- Do not multiply utilization values by 100 — all fields now return 0–100 percentages directly.
- Do not call `rumps.notification()` directly — always use `_notify()`.
- Do not store cookies in plaintext anywhere other than `~/.claude_bar_config.json` (which is gitignored).
- Do not add Electron, a web server, or any always-on background process beyond the menu bar app itself.
- Do not make the README longer than it already is — trim if anything.
- Do not add features that don't drive stars or retention. Every line of code should serve growth.
