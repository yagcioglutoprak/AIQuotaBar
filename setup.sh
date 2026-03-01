#!/bin/bash
# Legacy manual setup for Claude Usage Bar
# Recommended: use install.sh instead

set -e

echo "Installing dependencies…"

if [ -d ".venv" ]; then
    .venv/bin/python3 -m pip install -r requirements.txt
elif command -v python3 &>/dev/null; then
    python3 -m venv .venv
    .venv/bin/python3 -m pip install -r requirements.txt
else
    echo "Python 3 not found. Install it first."
    exit 1
fi

echo ""
echo "Done. To run:"
echo "  .venv/bin/python3 claude_bar.py"
echo ""
echo "To run at login, add it to System Settings → General → Login Items."
