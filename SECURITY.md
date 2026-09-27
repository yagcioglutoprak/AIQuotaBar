# Security policy

AIQuotaBar reads session cookies from your browser to call each service's usage endpoint, so security reports are taken seriously.

## Reporting a vulnerability

**Please don't open a public issue.** Report it privately through GitHub:
[**Report a vulnerability**](https://github.com/yagcioglutoprak/AIQuotaBar/security/advisories/new) (Security tab → *Report a vulnerability*).

Please include what an attacker could do, the steps to reproduce, and the AIQuotaBar version (`python3 claude_bar.py --version`). You'll get a reply within a few days, and credit in the release notes if you'd like it.

## Supported versions

Only the latest release gets fixes. The one-line installer keeps installs up to date automatically.

## What's in scope

- Anything that could expose cookies, tokens or API keys: logs, files, the widget cache, crash output, network requests to anywhere other than the service itself.
- The panel (`aiquotabar/web/`) and the JS↔Python bridge: script injection, unvalidated actions, opening arbitrary URLs.
- The installer and auto-updater (`install.sh`, `aiquotabar/update.py`).

## How AIQuotaBar handles your data

- Cookies are read locally and stored only in `~/.claude_bar_config.json`.
- Requests go straight from your Mac to each service. There's no AIQuotaBar server, no analytics and no telemetry.
- The panel's content security policy blocks remote scripts, fonts and images, and every action from the page is validated in Python against an allow-list.
