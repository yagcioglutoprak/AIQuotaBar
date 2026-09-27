## What this changes

<!-- One or two sentences. Link the issue it addresses. -->

Closes #

## How I tested it

- [ ] `python3 -m pytest` passes
- [ ] Ran the app with `python3 claude_bar.py --demo` (UI changes)
- [ ] Ran the app with my real accounts (provider changes). Say which services:

<!-- For UI changes, add a before/after screenshot (hide account details). -->

## Checklist

- [ ] No cookies, session tokens, API keys or personal data anywhere in the diff, tests or screenshots
- [ ] New provider rows set `resets_at` / `window_secs` (so the pace marker works)
- [ ] Untrusted text in `aiquotabar/web/` is rendered with `textContent`, not `innerHTML`, and there are no remote scripts, fonts or images
- [ ] README changes are one line or less (longer docs go in `docs/`)
- [ ] If I used AI tools, I've reviewed every line and can explain the change
