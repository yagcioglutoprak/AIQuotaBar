# Post Plan — AIQuotaBar

## Schedule

| Day | Platform | Action |
|-----|----------|--------|
| Today | Hacker News | Submit Show HN |
| Today | Discord (Anthropic) | #community-projects post |
| Day 2 | r/ClaudeAI (527k) | Full post |
| Day 3 | r/ChatGPT (2.5M) | Full post |
| Day 3 | Twitter/X | 5-tweet thread |
| Day 4 | r/ClaudeCode (96k) | Full post |
| Day 5 | r/MacApps (209k) | Full post |
| Day 6 | r/OpenAI (800k) | Full post |
| Later | Hacker News v2 | When major feature ships |

---

## Hacker News — news.ycombinator.com/submit
**Best time:** Tuesday–Thursday 8–10 AM EST

**Title:**
```
Show HN: AIQuotaBar — macOS menu bar app that shows Claude + ChatGPT usage limits in real-time
```
**URL:**
```
https://github.com/yagcioglutoprak/AIQuotaBar
```

---

## Discord (Anthropic) — #community-projects or #showcase
**URL:** discord.com/invite/prcdpx7qMm

```
I built AIQuotaBar — shows your Claude session and weekly limits live in your macOS menu bar, plus ChatGPT usage in the same indicator. Zero setup, reads browser cookies locally, one-command install. MIT, ~900 lines Python, no Electron.

curl -fsSL https://raw.githubusercontent.com/yagcioglutoprak/AIQuotaBar/main/install.sh | bash

https://github.com/yagcioglutoprak/AIQuotaBar
```

---

## r/ClaudeAI (527k members)
**URL:** reddit.com/r/ClaudeAI
**Best time:** Tuesday–Thursday 9–11 AM EST

**Title:**
```
I built a macOS menu bar app that shows your Claude session and weekly usage in real-time — also tracks ChatGPT now
```

**Body:**
```
If you're on Claude Pro or Team, you've probably been blindsided mid-conversation when Claude just stops and you realize you hit the 5-hour limit. There's no visible indicator until you're already at the wall.

So I built AIQuotaBar. It sits in your menu bar showing your current session usage, weekly All Models cap, and Sonnet-only cap as a color-coded percentage. Notifies you at 80% and 95% so you can pace yourself or wrap up before getting cut off. I recently added ChatGPT tracking too, so now it shows both in one place.

How it works: reads your existing browser cookies locally from Chrome, Arc, Firefox, Safari, Brave, or Edge. Calls the same API that claude.ai/settings/usage uses. Nothing leaves your machine except the request to claude.ai itself.

Install in one command:
curl -fsSL https://raw.githubusercontent.com/yagcioglutoprak/AIQuotaBar/main/install.sh | bash

No Electron, no browser extension, no accounts. ~900 lines of Python, MIT licensed.

https://github.com/yagcioglutoprak/AIQuotaBar
```

---

## r/ChatGPT (2.5M members)
**URL:** reddit.com/r/ChatGPT
**Best time:** Tuesday–Thursday 9–11 AM EST — 1 day after r/ClaudeAI

**Title:**
```
Built a free macOS menu bar app that shows your ChatGPT usage alongside Claude — no extensions needed
```

**Body:**
```
I use both Claude and ChatGPT daily and got tired of getting blindsided by usage limits on both. There's no built-in way to see how close you are without manually checking the settings page on each.

So I built AIQuotaBar — a lightweight macOS menu bar app that shows both Claude and ChatGPT usage limits as a color-coded percentage. Green when you're fine, yellow when you're getting close, red when you need to slow down. Notifications at 80% and 95%.

How it works: reads your existing browser session cookies locally from Chrome, Arc, Firefox, Safari, Brave, or Edge. No API keys, no accounts, no setup. The only network requests go to claude.ai and chatgpt.com directly.

One-command install:
curl -fsSL https://raw.githubusercontent.com/yagcioglutoprak/AIQuotaBar/main/install.sh | bash

Requires macOS 12+ and Python 3.10+. No Electron, no background services — ~900 lines of Python total. MIT licensed.

https://github.com/yagcioglutoprak/AIQuotaBar
```

---

## r/ClaudeCode (96k members)
**URL:** reddit.com/r/ClaudeCode
**Best time:** 1–2 days after r/ClaudeAI

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

## r/MacApps (209k members)
**URL:** reddit.com/r/MacApps
**Best time:** Day 5–6

**Title:**
```
AIQuotaBar — free menu bar app that tracks Claude + ChatGPT usage limits locally (~900 lines Python, no Electron)
```

**Body:**
```
Sharing a small utility I built: if you use Claude.ai or ChatGPT on paid plans, the usage caps are easy to hit without realizing it.

AIQuotaBar is a native-feeling macOS menu bar app that monitors both Claude and ChatGPT usage limits and shows them as a color-coded indicator (green/yellow/red). Notifies you at 80% and 95%.

Privacy first: reads your existing browser cookies locally — the only network requests go to claude.ai and chatgpt.com themselves. No analytics, no telemetry, no server.

~900 lines Python, rumps for the menu bar, curl_cffi for TLS fingerprinting. No Electron, no bundled Chromium. Auto-detects cookies from Chrome, Arc, Brave, Edge, Firefox, Safari.

Install:
curl -fsSL https://raw.githubusercontent.com/yagcioglutoprak/AIQuotaBar/main/install.sh | bash

macOS 12+, Python 3.10+, MIT licensed:
https://github.com/yagcioglutoprak/AIQuotaBar
```

---

## r/OpenAI (800k members)
**URL:** reddit.com/r/OpenAI
**Best time:** Day 6 (same day as r/MacApps)

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
**Tag in last tweet:** @AnthropicAI @OpenAI @swyx @levelsio @nutlope

**Tweet 1:**
```
The worst part about Claude Pro and ChatGPT Plus isn't the usage limits.

It's getting blindsided mid-session with zero warning because there's no visible indicator of how close you are.

I built something to fix that.
```

**Tweet 2:**
```
AIQuotaBar — a macOS menu bar app that shows your Claude and ChatGPT usage limits in real-time.

Color-coded: green/yellow/red. Notifications at 80% and 95% so you can pace yourself instead of getting cut off.

Both AI assistants. One menu bar indicator.
```

**Tweet 3:**
```
How it works:
• Reads your existing browser cookies locally (Chrome, Arc, Firefox, Safari, Brave, Edge)
• Calls the same APIs that claude.ai/settings/usage and chatgpt.com use
• ~900 lines of Python, no Electron
• Your data never leaves your machine
```

**Tweet 4:**
```
One command to install:

curl -fsSL https://raw.githubusercontent.com/yagcioglutoprak/AIQuotaBar/main/install.sh | bash

No accounts. No API keys. No setup. It finds your browser session automatically.
```

**Tweet 5:**
```
Open source, MIT licensed.

If you've ever been surprised by hitting AI limits mid-thought, give it a try:

https://github.com/yagcioglutoprak/AIQuotaBar

@AnthropicAI @OpenAI @swyx @levelsio @nutlope
```

---

## Hacker News v2 — when major feature ships
**DO NOT post until v2 has a meaningful new feature (e.g. Homebrew)**
**Best time:** Tuesday–Wednesday 8:00–8:30 AM EST

**Title:**
```
Show HN: AIQuotaBar v2 — Claude + ChatGPT usage tracker for macOS menu bar (Homebrew, open source)
```
