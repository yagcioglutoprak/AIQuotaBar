#!/bin/bash
# AIQuotaBar — one-line installer
# Usage: curl -fsSL https://raw.githubusercontent.com/yagcioglutoprak/AIQuotaBar/main/install.sh | bash

set -e

REPO="https://github.com/yagcioglutoprak/AIQuotaBar"
INSTALL_DIR="$HOME/.ai-quota-bar"
VENV_DIR="$INSTALL_DIR/.venv"
PLIST="$HOME/Library/LaunchAgents/com.claudebar.plist"

echo ""
echo "  AIQuotaBar — installer"
echo "  ──────────────────────"
echo ""

# ── 1. Python 3.10+ ───────────────────────────────────────────────────────────
BASE_PYTHON=""
for candidate in python3 python3.13 python3.12 python3.11 python3.10; do
  if command -v "$candidate" &>/dev/null; then
    VER=$("$candidate" -c 'import sys; print(sys.version_info >= (3,10))' 2>/dev/null)
    if [ "$VER" = "True" ]; then
      BASE_PYTHON="$candidate"
      break
    fi
  fi
done

if [ -z "$BASE_PYTHON" ]; then
  echo "  ✗  Python 3.10+ not found."
  echo "     Install it from https://www.python.org/downloads/ and re-run."
  exit 1
fi
echo "  ✓  Python: $($BASE_PYTHON --version)"

# ── 2. Git check ──────────────────────────────────────────────────────────────
if ! command -v git &>/dev/null; then
  echo "  ✗  Git not found. Install Xcode Command Line Tools first:"
  echo "     xcode-select --install"
  exit 1
fi

# ── 3. Clone / update ─────────────────────────────────────────────────────────
if [ -d "$INSTALL_DIR/.git" ]; then
  echo "  ↻  Updating existing install…"
  git -C "$INSTALL_DIR" fetch --quiet origin
  git -C "$INSTALL_DIR" stash --quiet 2>/dev/null || true
  git -C "$INSTALL_DIR" checkout main --quiet 2>/dev/null || true
  git -C "$INSTALL_DIR" merge --ff-only origin/main --quiet
else
  echo "  ↓  Cloning repository…"
  git clone --quiet --depth 1 "$REPO" "$INSTALL_DIR"
fi

# ── 4. Virtual environment + dependencies ─────────────────────────────────────
echo "  ↓  Setting up virtual environment…"
if [ ! -d "$VENV_DIR" ]; then
  "$BASE_PYTHON" -m venv "$VENV_DIR"
fi
PYTHON="$VENV_DIR/bin/python3"
echo "  ↓  Installing Python dependencies…"
"$PYTHON" -m pip install --quiet --upgrade pip
"$PYTHON" -m pip install --quiet --upgrade -r "$INSTALL_DIR/requirements.txt"
echo "  ✓  Dependencies installed"

# ── 5. LaunchAgent (run at login) ─────────────────────────────────────────────
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.claudebar</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>$INSTALL_DIR/claude_bar.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>StandardOutPath</key>
    <string>$HOME/.claude_bar.log</string>
    <key>StandardErrorPath</key>
    <string>$HOME/.claude_bar.log</string>
</dict>
</plist>
PLIST_EOF

launchctl bootout gui/$(id -u) "$PLIST" 2>/dev/null || true
sleep 1
launchctl bootstrap gui/$(id -u) "$PLIST"
echo "  ✓  Added to Login Items (runs at every login)"

# ── 5b. Desktop Widget ───────────────────────────────────────────────────────
WIDGET_APP="/Applications/AIQuotaBarHost.app"
WIDGET_INSTALLED=false

if [ -d "$WIDGET_APP" ]; then
    echo "  ✓  Desktop widget already installed"
    WIDGET_INSTALLED=true
elif command -v xcodebuild &>/dev/null && [ -d "$INSTALL_DIR/AIQuotaBarWidget/AIQuotaBarWidget.xcodeproj" ]; then
    echo "  ↓  Building desktop widget (Xcode found)…"
    if bash "$INSTALL_DIR/AIQuotaBarWidget/build_widget.sh"; then
        WIDGET_INSTALLED=true
    else
        echo "  ⚠  Widget build failed (non-fatal)"
    fi
else
    echo "  ↓  Installing pre-built desktop widget…"
    WIDGET_URL="https://github.com/yagcioglutoprak/AIQuotaBar/releases/latest/download/AIQuotaBarWidget.zip"
    WIDGET_TMP="/tmp/AIQuotaBarWidget_$$.zip"
    if curl -fsSL -o "$WIDGET_TMP" "$WIDGET_URL" 2>/dev/null; then
        rm -rf "$WIDGET_APP"
        ditto -x -k "$WIDGET_TMP" /Applications/
        xattr -dr com.apple.quarantine "$WIDGET_APP" 2>/dev/null || true
        rm -f "$WIDGET_TMP"
        # Launch once to register widget with the system
        open "$WIDGET_APP"
        sleep 2
        osascript -e 'quit app "AIQuotaBarHost"' 2>/dev/null || true
        echo "  ✓  Desktop widget installed"
        WIDGET_INSTALLED=true
    else
        echo "  ⊘  Widget: download failed (non-fatal, skipping)"
        rm -f "$WIDGET_TMP"
    fi
fi

if [ "$WIDGET_INSTALLED" = true ]; then
    echo "     Right-click desktop → Edit Widgets → search \"AI Quota\""
fi
echo ""

# ── 6. Launch now ─────────────────────────────────────────────────────────────
pkill -f "$INSTALL_DIR/claude_bar.py" 2>/dev/null || true
sleep 1
"$PYTHON" "$INSTALL_DIR/claude_bar.py" &>/dev/null &
echo "  ✓  Launched!"
echo ""
echo "  Look for the ◆ icon in your menu bar."
echo "  It will auto-detect your Claude session from your browser."
echo ""
echo "  ─────────────────────────────────────────────────"
echo "  ⭐ If you find this useful, star the repo!"
echo "     https://github.com/yagcioglutoprak/AIQuotaBar"
echo "  ─────────────────────────────────────────────────"
echo ""
