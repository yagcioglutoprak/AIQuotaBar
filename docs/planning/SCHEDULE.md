# Posting Schedule — AIQuotaBar

**Started:** 2026-02-26
**Goal:** 225 stars → resubmit homebrew-core PR

---

## Schedule

| Date | Platform | Status | Notes |
|------|----------|--------|-------|
| 2026-02-26 | Hacker News | ⚠️ Live (1pt, DEAD) | Missing "Show HN:" prefix — can't fix. 1 self-comment only. |
| 2026-02-26 | r/ClaudeAI | ❌ Rejected | Repost with fixed version below (wait 1h) |
| 2026-02-26 | awesome-mac PR #1833 | 🔄 Open | jaywcjlove/awesome-mac (99k⭐) — waiting on maintainer |
| 2026-02-26 | open-source-mac-os-apps PR #1041 | 🔄 Open | serhii-londar (47k⭐) — waiting on maintainer |
| 2026-02-26 | awesome-claude PR #60 | 🔄 Open | tonysurfly/awesome-claude (1.1k⭐) — waiting on maintainer |
| 2026-02-26 | jqueryscript/awesome-claude-code PR #51 | 🔄 Open | Waiting on maintainer |
| 2026-02-26 | GitHub Pages | ✅ Live | https://yagcioglutoprak.github.io/AIQuotaBar/ |
| 2026-02-27 | r/MacApps | ⏳ Pending | YOU must post — Reddit blocked in automation |
| 2026-02-27 | r/ChatGPT | ⏳ Pending | YOU must post — a few hours after r/MacApps |
| 2026-02-27 | r/ClaudeAI retry | ⏳ Pending | YOU must post — fixed copy in this doc |
| 2026-02-28 | awesome-claude-code | ⚠️ Manual only | Cooldown expires — submit via browser (see section below) |
| 2026-02-28 | r/Python | ⏳ Pending | Technical angle |
| 2026-02-28 | r/macOS | ⏳ Pending | |
| 2026-02-28 | Product Hunt | ⏳ NOT submitted | YOU must submit — not in system, huge opportunity (see section below) |
| 2026-02-26 | AlternativeTo | ✅ Listed | https://alternativeto.net/software/aiquotabar/about/ |
| 2026-03-01 | r/ClaudeCode | ⏳ Pending | |
| 2026-03-01 | r/OpenAI | ⏳ Pending | |
| 2026-02-26 | dev.to post | ✅ Published | https://dev.to/yagcioglutoprak/i-built-a-macos-menu-bar-app-in-900-lines-of-python-that-tracks-claude-chatgpt-limits-heres-5he7 |
| 2026-03-01 | Indie Hackers | ❌ Blocked | New accounts can't post — need comment karma first. Earn by commenting on posts. |
| 2026-03-02 | Twitter/X thread | ⏳ Pending | Tag @AnthropicAI @swyx @levelsio @nutlope |
| 2026-03-02 | Anthropic Discord | ⏳ NOT done | #community-projects or #showcase — copy in POST_PLAN.md |
| When 225 ⭐ | homebrew-core PR | ⏳ Waiting | Formula is ready, just resubmit |

**Best posting time for all platforms:** Tuesday–Thursday, 9–11 AM EST

---

## 🚨 Product Hunt — NOT SUBMITTED (submit 2026-02-28, Tuesday 12:01 AM PST)

**Product Hunt launches reset at 12:01 AM PST daily. Submit Tuesday for max exposure.**
**Must be logged in — go to producthunt.com/posts/new**

| Field | Value |
|-------|-------|
| Name | `AIQuotaBar` |
| Tagline | `Claude + ChatGPT usage limits in your macOS menu bar` |
| Description | `Stop getting blindsided by AI rate limits. AIQuotaBar shows live Claude session/weekly usage and ChatGPT limits as a color-coded indicator in your macOS menu bar. Zero setup — reads your existing browser cookies from Chrome, Arc, Firefox, or Safari. One-command install. MIT licensed.` |
| Website | `https://github.com/yagcioglutoprak/AIQuotaBar` |
| Topics | `Developer Tools`, `Productivity`, `Artificial Intelligence`, `Mac` |
| Maker comment | `I built this after Claude Pro cut me off mid-session three times in one week with zero warning. The settings page exists but nobody checks it proactively. This auto-detects your browser session so there's nothing to configure — just install and it shows up in your menu bar. Happy to answer questions!` |

---

## ✅ AlternativeTo — LISTED

**URL:** https://alternativeto.net/software/aiquotabar/about/
Listed 2026-02-26.

---

## 🆕 dev.to — Post (2026-03-01)

**Title:**
```
Show HN: I built a macOS menu bar app in ~900 lines of Python that tracks Claude + ChatGPT limits — here's how
```

**Tags:** `python`, `macos`, `claude`, `openai`

**Body (technical angle):**
```markdown
I got tired of getting blindsided by Claude Pro rate limits mid-session. The solution: a menu bar app that reads the same private API that claude.ai/settings/usage calls.

Here's what made it interesting to build:

## The Cloudflare problem
Claude's API is behind Cloudflare. Regular `requests` or `httpx` get 403'd immediately. The fix: `curl_cffi` with `impersonate="chrome131"` to spoof the TLS fingerprint. This was the single biggest hurdle.

## Browser cookie detection
Rather than making users copy-paste cookies, I used `browser-cookie3` to read them directly from Chrome, Arc, Firefox, Safari, or Brave. Key insight: **try Firefox first** — it uses a plain SQLite database with no Keychain prompt. Chromium browsers need a one-time "Always Allow" dialog.

## AppKit threading rule
`rumps` runs on the main thread. All UI updates (menu rebuilds) must happen there. My background fetch thread puts results in a `queue.Queue`, and a 0.25-second timer on the main thread drains it. Violate this and you get intermittent crashes.

## Inconsistent API scale
The API returns `five_hour` (session) as a 0–1 fraction, but `seven_day` (weekly) as a 0–100 percentage. The fix: `if raw > 1.0: already_pct else multiply_by_100`. Spent embarrassing time debugging this.

## One-line install

```bash
curl -fsSL https://raw.githubusercontent.com/yagcioglutoprak/AIQuotaBar/main/install.sh | bash
```

MIT licensed, ~900 lines Python: https://github.com/yagcioglutoprak/AIQuotaBar

Happy to go deep on any of these implementation details in the comments.
```

---

## 🆕 Indie Hackers — Post (2026-03-01)

**URL:** https://www.indiehackers.com/post/new
**Channel:** "What are you building?"

**Body:**
```
I built AIQuotaBar — a free macOS menu bar app that shows live Claude and ChatGPT usage limits.

The problem: both AI services have usage caps that aren't surfaced until you hit them. I kept getting cut off mid-Claude-session with zero warning.

The solution: a ~900-line Python app that reads your browser's existing session cookies, calls the same internal APIs that the settings pages use, and shows a live color-coded indicator in your menu bar. 🟢🟡🔴

Zero setup. One command to install. Auto-detects cookies from Chrome, Arc, Firefox, Brave, Safari.

curl -fsSL https://raw.githubusercontent.com/yagcioglutoprak/AIQuotaBar/main/install.sh | bash

GitHub: https://github.com/yagcioglutoprak/AIQuotaBar — MIT, open source

Would love feedback from other developers who've hit this problem.
```

---

## ⚠️ awesome-claude-code — MANUAL SUBMISSION REQUIRED (2026-02-28)

**DO NOT use gh CLI or any automation — the maintainer has bot detection and will extend the ban.**
**Cooldown expires: 2026-02-28. Submit via browser only.**

**URL:** https://github.com/hesreallyhim/awesome-claude-code/issues/new?template=recommend-resource.yml

| Field | Value |
|-------|-------|
| Display Name | `AIQuotaBar` |
| Category | `Tooling` |
| Sub-Category | `Tooling: Usage Monitors` |
| Primary Link | `https://github.com/yagcioglutoprak/AIQuotaBar` |
| Author Name | `yagcioglutoprak` |
| Author Link | `https://github.com/yagcioglutoprak` |
| License | `MIT` |
| Description | `macOS menu bar app that shows live Claude.ai session and weekly usage limits as a color-coded percentage. Reads browser cookies locally from Chrome, Arc, Firefox, or Brave — no setup required. Also tracks ChatGPT usage in the same indicator.` |
| Validate Claims | `Install with one command, then check your menu bar — the Claude usage % updates every 5 minutes automatically.` |
| Specific Task | `Install AIQuotaBar and watch the menu bar update with your current Claude session usage in real time.` |
| Specific Prompt | `curl -fsSL https://raw.githubusercontent.com/yagcioglutoprak/AIQuotaBar/main/install.sh \| bash` |

Check all boxes including "I am primarily composed of human-y stuff and not electrical circuits."

---

## r/ClaudeAI — FIXED (repost after 1h from rejection)

**URL:** reddit.com/r/ClaudeAI
**Title:**
```
I built a free macOS menu bar app to track your Claude usage limits in real time — made with Claude Code
```

**Body:**
```
I built this specifically because Claude Pro kept cutting me off mid-session with zero warning. Built the whole thing with Claude Code.

What I made: AIQuotaBar — a macOS menu bar app that shows your Claude session and weekly limits live, so you can see 🟢 12% or 🟡 83% at a glance without going to Settings → Usage.

The menu shows:

  CLAUDE
    🟡 Current Session    83%   resets in 1h 12m
    🟢 All Models (week)  22%   resets Wed 23:00
    🟢 Sonnet Only        18%   resets Wed 23:00

How Claude Code helped: Wrote the Cloudflare bypass logic (curl_cffi with Chrome TLS fingerprinting), the browser cookie auto-detection across Chrome/Firefox/Arc/Brave, and the thread-safe AppKit menu update queue. Would have taken me days alone — took hours.

It's completely free and open source (MIT). One command to install:

  curl -fsSL https://raw.githubusercontent.com/yagcioglutoprak/AIQuotaBar/main/install.sh | bash

GitHub: https://github.com/yagcioglutoprak/AIQuotaBar

No browser extension, no API keys, nothing to configure — reads your existing browser session automatically.
```

---

## r/MacApps

**URL:** reddit.com/r/MacApps
**Title:**
```
I built a menu bar app that shows your Claude + ChatGPT usage limits in real time — zero setup, reads from your browser automatically
```

**Body:**
```
I kept getting cut off mid-session on Claude Pro with no warning. The usage page only shows limits if you remember to check it. So I built AIQuotaBar — a tiny menu bar app that shows live usage for both Claude and ChatGPT.

What it does:
- 🟢 / 🟡 / 🔴 icon in your menu bar showing session usage %
- Tracks Claude session (5h), weekly all-models, and weekly Sonnet-only limits
- ChatGPT usage alongside it in the same menu
- Auto-detects cookies from Chrome, Arc, Firefox, Brave — nothing to configure
- macOS notifications at 80% and 95%
- Runs at login via LaunchAgent

One-line install:
  curl -fsSL https://raw.githubusercontent.com/yagcioglutoprak/AIQuotaBar/main/install.sh | bash

Or via Homebrew:
  brew tap yagcioglutoprak/aiquotabar && brew install aiquotabar

~900 lines of Python, no Electron, no background services.
GitHub: https://github.com/yagcioglutoprak/AIQuotaBar
```

---

## r/ChatGPT

**URL:** reddit.com/r/ChatGPT
**Title:**
```
Built a macOS menu bar app that shows ChatGPT + Claude usage limits side by side — no extensions, no setup
```

**Body:**
```
If you use both ChatGPT and Claude, you know the pain: both have usage limits, neither tells you how close you are until you hit the wall.

I built AIQuotaBar — a menu bar app for macOS that shows both in one place:

  CHATGPT
    🟢 Codex Tasks      0%    resets Thu 05:38
    🟢 Code Review      0%    resets Thu 05:38

  CLAUDE
    🟡 Current Session  83%   resets in 1h 12m
    🟢 All Models       22%   resets Wed 23:00

- No browser extension
- No API keys to manage
- Auto-reads cookies from Chrome, Arc, Firefox, Brave
- Sends a macOS notification at 80% and 95%

One command to install:
  curl -fsSL https://raw.githubusercontent.com/yagcioglutoprak/AIQuotaBar/main/install.sh | bash

https://github.com/yagcioglutoprak/AIQuotaBar — MIT, open source
```

---

## r/Python

**URL:** reddit.com/r/Python
**Title:**
```
Show r/Python: I built a macOS menu bar app in ~900 lines of Python that tracks Claude + ChatGPT usage limits
```

**Body:**
```
What it does: Reads Claude.ai and ChatGPT usage from their private APIs and shows the result as a live menu bar icon (🟢 / 🟡 / 🔴 with a percentage).

The interesting technical bits:

- Uses curl_cffi with impersonate="chrome131" to bypass Cloudflare on claude.ai — regular requests/httpx get 403'd
- Reads browser cookies via browser_cookie3 (tries Firefox first — no Keychain prompt, then Chromium browsers)
- AppKit/rumps for the menu bar UI, with a thread-safe update queue (background thread fetches, 0.25s ticker drains to main thread — AppKit won't let you update UI off main thread)
- The API returns session usage as a 0–1 fraction but weekly usage as 0–100 — fun edge case to handle

Stack: rumps + curl_cffi + browser-cookie3, single file, no build step

Install:
  curl -fsSL https://raw.githubusercontent.com/yagcioglutoprak/AIQuotaBar/main/install.sh | bash

Code: https://github.com/yagcioglutoprak/AIQuotaBar

Happy to answer questions about any of the implementation details.
```

---

## r/macOS

**URL:** reddit.com/r/macOS
**Title:**
```
I made a free menu bar app that shows your Claude and ChatGPT usage limits so you stop getting cut off mid-conversation
```

**Body:**
```
If you're on Claude Pro or ChatGPT Plus, both services have usage limits that they don't surface prominently. You find out when you're suddenly rate-limited mid-task.

I built a tiny menu bar app called AIQuotaBar that solves this:

- Lives in your menu bar, always visible
- Shows Claude session usage + weekly limits
- Shows ChatGPT limits alongside it
- Auto-detects your browser session — nothing to configure
- Sends a notification when you hit 80% and 95%
- Runs at login

Install with one command:
  curl -fsSL https://raw.githubusercontent.com/yagcioglutoprak/AIQuotaBar/main/install.sh | bash

Free, open source, MIT licensed: https://github.com/yagcioglutoprak/AIQuotaBar
```

---

## r/ClaudeCode

**URL:** reddit.com/r/ClaudeCode
**Title:**
```
Never get surprised by Claude Code limits again — free menu bar tracker (also tracks ChatGPT, open source)
```

**Body:**
```
If you use Claude Code heavily, you've probably hit the session or weekly limit mid-task. AIQuotaBar shows your Claude usage in your macOS menu bar so you always know where you stand before you get cut off.

One-command install:
  curl -fsSL https://raw.githubusercontent.com/yagcioglutoprak/AIQuotaBar/main/install.sh | bash

Also tracks ChatGPT if you use both. MIT licensed, ~900 lines Python, no Electron:
https://github.com/yagcioglutoprak/AIQuotaBar
```

---

## r/OpenAI

**URL:** reddit.com/r/OpenAI
**Title:**
```
Free macOS app that shows your ChatGPT usage limits in the menu bar — also works for Claude
```

**Body:**
```
I kept getting blindsided by ChatGPT usage limits mid-session with no warning. The usage page exists but nobody checks it proactively.

Built AIQuotaBar: a lightweight macOS menu bar app showing ChatGPT (and Claude) usage as a live color-coded indicator. Green = fine, yellow = getting close, red = throttled. Notifies at 80% and 95%.

Zero setup: reads your browser session cookies locally. No API keys, no accounts. The only requests go to chatgpt.com (and optionally claude.ai). Nothing transmitted elsewhere.

  curl -fsSL https://raw.githubusercontent.com/yagcioglutoprak/AIQuotaBar/main/install.sh | bash

No Electron. ~900 lines Python, MIT licensed:
https://github.com/yagcioglutoprak/AIQuotaBar
```

---

## Twitter/X — 5-tweet thread

**Best time:** Tuesday–Thursday 9 AM–12 PM EST
**Tag:** @AnthropicAI @swyx @levelsio @nutlope

**Tweet 1:**
```
The worst part about Claude Pro and ChatGPT Plus isn't the usage limits.

It's getting blindsided mid-session with zero warning because there's no visible indicator of how close you are.

I built something to fix that.
```

**Tweet 2:**
```
AIQuotaBar — a macOS menu bar app that shows your Claude and ChatGPT usage limits in real-time.

Color-coded: 🟢🟡🔴. Notifications at 80% and 95% so you can pace yourself instead of getting cut off.

Both AI assistants. One menu bar indicator.
```

**Tweet 3:**
```
How it works:
• Reads your existing browser cookies locally (Chrome, Arc, Firefox, Brave)
• Calls the same APIs that claude.ai/settings/usage uses
• ~900 lines of Python, no Electron
• Your data never leaves your machine
```

**Tweet 4:**
```
One command to install:

curl -fsSL https://raw.githubusercontent.com/yagcioglutoprak/AIQuotaBar/main/install.sh | bash

No accounts. No API keys. No setup. Finds your browser session automatically.
```

**Tweet 5:**
```
Open source, MIT licensed.

If you've ever been surprised by hitting AI limits mid-thought, give it a try:

https://github.com/yagcioglutoprak/AIQuotaBar

@AnthropicAI @swyx @levelsio @nutlope
```

---

## Hacker News v2 — repost when homebrew-core is merged

**DO NOT post until homebrew-core PR is accepted**
**Best time:** Tuesday–Wednesday 8:00–8:30 AM EST

**Title:**
```
Show HN: AIQuotaBar – macOS menu bar app tracking Claude + ChatGPT limits (now on Homebrew)
```
