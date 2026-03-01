# Growth Actions — AIQuotaBar

Ordered by impact. Each action is self-contained with exact copy ready to paste.

---

## Action 1 — Add HN + PH links to README
**Time:** 2 min
**File:** `README.md` line 17

Replace the current social proof line:
```
**Featured on Hacker News (161 points, 49 comments) · Product Hunt (382 upvotes)**
```
With (add your actual URLs):
```
**Featured on [Hacker News](YOUR_HN_LINK) (161 points, 49 comments) · [Product Hunt](YOUR_PH_LINK) (382 upvotes)**
```

Then commit and push:
```bash
cd ~/AIQuotaBar
git add README.md
git commit -m "docs: add HN and PH links to social proof"
git push
```

---

## Action 2 — Awesome list PR: awesome-claude-code (21.6k stars)
**Time:** 5 min
**URL:** https://github.com/hesreallyhim/awesome-claude-code

1. Open the repo, click the pencil icon on README.md
2. Find the "Utilities" or "Tools" section
3. Add in alphabetical order:
```
- [AIQuotaBar](https://github.com/yagcioglutoprak/AIQuotaBar) - macOS menu bar app showing Claude.ai session and weekly usage limits in real-time. Zero setup, reads local browser cookies.
```
4. PR title: `Add AIQuotaBar — macOS menu bar usage tracker`

---

## Action 3 — Awesome list PR: open-source-mac-os-apps (43k stars)
**Time:** 5 min
**URL:** https://github.com/serhii-londar/open-source-mac-os-apps

1. Open the repo, click pencil icon on README.md
2. Find "Menu Bar" section
3. Add:
```
- [AIQuotaBar](https://github.com/yagcioglutoprak/AIQuotaBar) - See your Claude.ai and ChatGPT usage limits live in your macOS menu bar. ![python_lang](https://github.com/serhii-londar/open-source-mac-os-apps/blob/master/./icons/python-16.png?raw=true)
```
4. PR title: `Add AIQuotaBar — Claude.ai + ChatGPT usage tracker menu bar app`

---

## Action 4 — Awesome list PR: awesome-mac (76k stars)
**Time:** 5 min
**URL:** https://github.com/jaywcjlove/awesome-mac

1. Find "Developer Tools > Utilities"
2. Add:
```
- [AIQuotaBar](https://github.com/yagcioglutoprak/AIQuotaBar) - See your Claude.ai and ChatGPT usage limits live in your macOS menu bar. [![Open-Source Software](https://jaywcjlove.github.io/sb/ico/min-oss.svg)](https://github.com/yagcioglutoprak/AIQuotaBar) ![Freeware](https://jaywcjlove.github.io/sb/ico/min-free.svg)
```
3. PR title: `Add AIQuotaBar — Claude + ChatGPT usage tracker for macOS menu bar`

---

## Action 5 — MacMenuBar.com submission
**Time:** 5 min
**URL:** https://macmenubar.com/submit/

Fill in:
- **Name:** AIQuotaBar
- **Category:** Developer Tools
- **Description:** See your Claude.ai and ChatGPT usage limits live in your macOS menu bar. Zero setup — reads browser cookies locally. No Electron. One command install.
- **URL:** https://github.com/yagcioglutoprak/AIQuotaBar

---

## Action 6 — Anthropic Discord post
**Time:** 5 min
**URL:** discord.com/invite/prcdpx7qMm
**Channel:** #community-projects or #showcase

Paste:
```
I built AIQuotaBar — shows your Claude session and weekly limits live in your macOS menu bar, plus ChatGPT usage in the same indicator. Zero setup, reads browser cookies locally, one-command install. MIT, ~900 lines Python, no Electron.

curl -fsSL https://raw.githubusercontent.com/yagcioglutoprak/AIQuotaBar/main/install.sh | bash

https://github.com/yagcioglutoprak/AIQuotaBar
```

---

## Action 7 — r/ClaudeAI post (527k members)
**Time:** 10 min
**URL:** https://reddit.com/r/ClaudeAI
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

## Action 8 — r/ChatGPT post (2.5M members)
**Time:** 10 min
**URL:** https://reddit.com/r/ChatGPT
**Best time:** Tuesday–Thursday 9–11 AM EST — post 1 day after r/ClaudeAI

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

## Action 9 — r/ClaudeCode post (96k members)
**Time:** 5 min
**URL:** https://reddit.com/r/ClaudeCode
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

## Action 10 — r/MacApps post (209k members)
**Time:** 10 min
**URL:** https://reddit.com/r/MacApps
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

## Action 11 — r/OpenAI post (800k members)
**Time:** 10 min
**URL:** https://reddit.com/r/OpenAI
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

## Action 12 — Twitter/X thread
**Time:** 15 min
**Best time:** Tuesday–Thursday, 9 AM–12 PM EST
**Accounts to tag in last tweet:** @AnthropicAI @OpenAI @swyx @levelsio @nutlope

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
Featured on Hacker News (161 points) and Product Hunt (382 upvotes).

If you've ever been surprised by hitting AI limits mid-thought, give it a try:

https://github.com/yagcioglutoprak/AIQuotaBar

@AnthropicAI @OpenAI @swyx @levelsio @nutlope
```

---

## Action 13 — HN v2 (when Homebrew / major feature ships)
**DO NOT post until v2 has a meaningful new feature.**
**Best time:** Tuesday–Wednesday, 8:00–8:30 AM EST

**Title:**
```
Show HN: AIQuotaBar v2 — Claude + ChatGPT usage tracker for macOS menu bar (Homebrew, open source)
```

---

## Schedule

| Day | Actions |
|-----|---------|
| Today | 1 (README links), 2 (awesome-claude-code PR), 3 (open-source-mac-os-apps PR), 4 (awesome-mac PR), 5 (MacMenuBar.com), 6 (Discord) |
| Day 2 | 7 (r/ClaudeAI) |
| Day 3 | 8 (r/ChatGPT), 12 (Twitter/X) |
| Day 4 | 9 (r/ClaudeCode) |
| Day 5 | 10 (r/MacApps) |
| Day 6 | 11 (r/OpenAI) |
| Later | 13 (HN v2 when ready) |
