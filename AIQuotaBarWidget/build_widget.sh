#!/bin/bash
# Build the AIQuotaBar WidgetKit widget
# Requires: Xcode 15+, macOS 14+

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR"
BUILD_DIR="$PROJECT_DIR/build"
APP_NAME="AIQuotaBarHost.app"

echo ""
echo "  AIQuotaBar Widget — builder"
echo "  ───────────────────────────"
echo ""

# ── Check Xcode ──────────────────────────────────────────────────────────────
if ! command -v xcodebuild &>/dev/null; then
    echo "  ✗  Xcode not found. Install from the App Store."
    echo "     The widget is optional — the menu bar app works without it."
    exit 1
fi

XCODE_VER=$(xcodebuild -version 2>/dev/null | head -1 | awk '{print $2}')
echo "  ✓  Xcode: $XCODE_VER"

# ── Check for project ────────────────────────────────────────────────────────
if [ ! -d "$PROJECT_DIR/AIQuotaBarWidget.xcodeproj" ]; then
    echo ""
    echo "  ✗  No Xcode project found."
    echo ""
    echo "  To create the project:"
    echo "    1. Open Xcode → File → New → Project"
    echo "    2. Choose macOS → App"
    echo "    3. Product Name: AIQuotaBarHost"
    echo "    4. Add Widget Extension target: AIQuotaBarWidgetExtension"
    echo "    5. Set App Group: group.com.aiquotabar on both targets"
    echo "    6. Drag the existing Swift files into the project"
    echo ""
    echo "  Alternatively, open this directory in Xcode and it will"
    echo "  detect the source files automatically."
    echo ""
    echo "  For detailed instructions, see the README."
    exit 1
fi

# ── Build ────────────────────────────────────────────────────────────────────
echo "  ↓  Building widget…"
xcodebuild \
    -project "$PROJECT_DIR/AIQuotaBarWidget.xcodeproj" \
    -scheme AIQuotaBarHost \
    -configuration Release \
    -derivedDataPath "$BUILD_DIR" \
    CODE_SIGN_IDENTITY="-" \
    CODE_SIGNING_REQUIRED=NO \
    CODE_SIGNING_ALLOWED=NO \
    DEVELOPMENT_TEAM="" \
    2>&1 | tail -5

# ── Sign ─────────────────────────────────────────────────────────────────────
# The build runs with CODE_SIGNING_ALLOWED=NO, which leaves a linker-signed
# bundle with no entitlements and a placeholder identifier. macOS will not
# register a widget extension in that state, so the widget never appears in
# the picker. Ad-hoc sign both bundles with their real entitlements; the
# extension needs its sandbox exception to read usage.json at all.
BUILT_APP=$(find "$BUILD_DIR" -name "$APP_NAME" -type d | head -1)
if [ -z "$BUILT_APP" ]; then
    echo "  ✗  Build failed — app bundle not found."
    exit 1
fi

echo "  ↓  Signing…"
BUILT_EXT="$BUILT_APP/Contents/PlugIns/AIQuotaBarWidgetExtension.appex"
codesign --force --sign - --timestamp=none \
    --entitlements "$PROJECT_DIR/AIQuotaBarWidgetExtension/AIQuotaBarWidgetExtension.entitlements" \
    "$BUILT_EXT" >/dev/null 2>&1
codesign --force --sign - --timestamp=none \
    --entitlements "$PROJECT_DIR/AIQuotaBarHost/AIQuotaBarHost.entitlements" \
    "$BUILT_APP" >/dev/null 2>&1
if ! codesign --verify --deep --strict "$BUILT_APP" 2>/dev/null; then
    echo "  ✗  Signing failed — macOS will not register an unsigned widget."
    exit 1
fi
echo "  ✓  Signed (ad-hoc, with entitlements)"

INSTALL_PATH="/Applications/$APP_NAME"
echo "  ↓  Installing to $INSTALL_PATH…"
rm -rf "$INSTALL_PATH"
# ditto, not cp -R: preserves extended attributes so the signature stays intact.
ditto "$BUILT_APP" "$INSTALL_PATH"

# Drop the build-directory copy from LaunchServices. Both copies share the
# bundle id, and if the build copy stays registered the system can host the
# widget from there instead of /Applications - silently serving stale code.
LSREGISTER=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
"$LSREGISTER" -u "$BUILT_APP" 2>/dev/null || true
"$LSREGISTER" -f "$INSTALL_PATH" 2>/dev/null || true

# Launch once to register the widget with the system
open "$INSTALL_PATH"
sleep 2
osascript -e 'quit app "AIQuotaBarHost"' 2>/dev/null || true

echo "  ✓  Widget installed!"
echo ""
echo "  Right-click your desktop → Edit Widgets → search \"AI Quota\""
echo ""
