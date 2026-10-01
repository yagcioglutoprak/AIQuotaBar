# Troubleshooting

Most problems are one of these. The log (`~/.claude_bar.log`, or **Settings → About → Log file**) usually says which:

```bash
tail -50 ~/.claude_bar.log
```

Remove cookies and tokens before you paste a log into an issue.

## An account isn't found

AIQuotaBar reuses the session already in your browser. Sign in to the service in a supported browser, then open **Settings → Accounts → Detect** (or click **Reconnect** on the card).

| Service | Sign in at | Also works with |
|---|---|---|
| Claude | claude.ai | pasting a cookie (Settings → Accounts → *Paste a Claude cookie manually…*) |
| ChatGPT / Codex | chatgpt.com | Codex CLI (`codex login`), see below |
| Cursor | cursor.com | |
| GitHub Copilot | github.com | |

In the log, `cookie-detect rc=0 out='[]'` means no browser had a session for that service.

### Per browser

- **Firefox, LibreWolf:** no prompt. Read first.
- **Chrome, Arc, Brave, Edge, Vivaldi, Chromium:** macOS asks once for Keychain access ("*Chrome Safe Storage*"). Choose **Always Allow**. If you clicked *Deny*, click **Detect** again and macOS asks again.
- **Safari:** needs Full Disk Access, see below.

### Safari and Full Disk Access

Safari's cookies are only readable by an app with **Full Disk Access**. macOS checks the app the Python interpreter runs as, not the `python3` in the AIQuotaBar folder (a symlink), so adding that one has no effect.

1. Open **Settings → About → Safari sessions**. It shows the exact app (usually `…/Python.framework/Versions/3.x/Resources/Python.app`). The same path is written to the log at startup (`Full Disk Access (for Safari) goes to: …`).
2. Click **Open Privacy**, then add that app under **Full Disk Access** (click **Show app** to find it in Finder, and drag it in).
3. Quit and restart AIQuotaBar.

## ChatGPT: browser session or Codex CLI

ChatGPT's usage can come from either:

- **your browser session** on chatgpt.com, or
- **Codex CLI**'s sign-in in `~/.codex/auth.json` (or `$CODEX_HOME/auth.json`). Run `codex login` once and choose *Sign in with ChatGPT*.

The browser is tried first. If there's no browser session, or it has gone stale, AIQuotaBar uses Codex CLI's sign-in instead, and the log says `ChatGPT: using ~/.codex/auth.json token`. Settings → Accounts then shows *via Codex CLI*.

AIQuotaBar only reads that file and never refreshes the token itself (that would sign Codex out). Codex refreshes it whenever you use it. If the card says **Codex sign-in expired**, run any `codex` command, or sign in to chatgpt.com in your browser.

Codex CLI set to keep its credentials in the system keychain (instead of `auth.json`) isn't supported; use the browser session then.

**Several workspaces on one ChatGPT sign-in** (say Plus and a Team workspace): AIQuotaBar shows the workspace you last picked in ChatGPT's account switcher.

## More than one account

Claude and ChatGPT can show several accounts side by side, e.g. personal and work.

1. Sign in to the other account in **another browser or browser profile** (a second Chrome profile works). For ChatGPT, Codex CLI signed in to another account works too.
2. Open **Settings → Accounts** and click **Add another Claude account** (or ChatGPT).

AIQuotaBar looks through every browser profile and adds each account it finds. A Claude sign-in that belongs to more than one organization (a personal plan and a Team plan) shows each one. You can also paste a Claude cookie and choose **Add as another account**.

Every account gets its own card, alerts and history. To show one in the menu bar, pick it in **Settings → Menu bar**. **Remove** stops tracking an account; *Show N removed* brings removed ones back.

## The panel doesn't open when an app is full screen

The panel is made to open on top of full-screen apps. If it doesn't for you:

- Check the log for `web panel did not load` or `web content process … ended`.
- As a workaround, turn on **Settings → General → Classic menu**: a click then opens a plain text menu, which always works.
- Please open an issue with your macOS version.

## Start fresh

Everything AIQuotaBar stores is in your home folder:

| File | What it is |
|---|---|
| `~/.claude_bar_config.json` | settings, saved sessions, extra accounts |
| `~/.claude_bar_history.json` | the last 24 hours (for pace) |
| `~/Library/Application Support/AIQuotaBar/` | long-term history, widget data |
| `~/.claude_bar.log` | the log |

Quit AIQuotaBar, delete `~/.claude_bar_config.json`, and start it again. It detects your accounts from scratch.
