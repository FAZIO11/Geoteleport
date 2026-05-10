#!/usr/bin/env bash
# build.sh — package Location Spoofer into dist/LocationSpoofer.app via PyInstaller.
#
# Usage:
#   ./build/build.sh
#
# Requirements:
#   - macOS 12 or newer
#   - Python 3.10–3.12 on PATH (we recommend `brew install python@3.12`)
#   - Internet (first run pulls deps from PyPI)
#
# Output:
#   dist/LocationSpoofer.app

set -euo pipefail

# ---- Paths ------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

BACKEND_DIR="$REPO_ROOT/backend"
FRONTEND_HTML="$REPO_ROOT/frontend/index.html"
ASSETS_DIR="$REPO_ROOT/assets"
DIST_DIR="$REPO_ROOT/dist"
WORK_DIR="$SCRIPT_DIR/build"
SPEC_DIR="$SCRIPT_DIR"

PYTHON_BIN="${PYTHON:-python3}"

echo "==> Using Python: $("$PYTHON_BIN" --version)"
echo "==> Repo root:    $REPO_ROOT"

# ---- Sanity checks ----------------------------------------------------------
if [[ "$(uname)" != "Darwin" ]]; then
  echo "✗ build.sh only runs on macOS." >&2
  exit 1
fi

if [[ ! -f "$FRONTEND_HTML" ]]; then
  echo "✗ Missing $FRONTEND_HTML" >&2
  exit 1
fi

if [[ ! -f "$BACKEND_DIR/main.py" ]]; then
  echo "✗ Missing $BACKEND_DIR/main.py" >&2
  exit 1
fi

# ---- Clean previous output --------------------------------------------------
echo "==> Cleaning previous build artifacts..."
rm -rf "$DIST_DIR" "$WORK_DIR" "$SPEC_DIR/LocationSpoofer.spec"

# ---- Install deps -----------------------------------------------------------
echo "==> Installing dependencies..."
"$PYTHON_BIN" -m pip install --upgrade pip wheel >/dev/null
"$PYTHON_BIN" -m pip install -r "$BACKEND_DIR/requirements.txt"
"$PYTHON_BIN" -m pip install "pyinstaller>=6.15.0"

# ---- Optional icon ----------------------------------------------------------
ICON_FLAG=()
if [[ -f "$ASSETS_DIR/icon.icns" ]]; then
  echo "==> Using icon: $ASSETS_DIR/icon.icns"
  ICON_FLAG=(--icon "$ASSETS_DIR/icon.icns")
else
  echo "==> No assets/icon.icns found — using default PyInstaller icon."
fi

# ---- Build ------------------------------------------------------------------
echo "==> Running PyInstaller..."
"$PYTHON_BIN" -m PyInstaller \
  --name LocationSpoofer \
  --windowed \
  --noconfirm \
  --clean \
  --distpath "$DIST_DIR" \
  --workpath "$WORK_DIR" \
  --specpath "$SPEC_DIR" \
  --osx-bundle-identifier "com.locationspoofer.app" \
  --add-data "$FRONTEND_HTML:frontend" \
  --hidden-import tunnel_manager \
  --hidden-import spoofer \
  --collect-submodules pymobiledevice3 \
  --collect-data pymobiledevice3 \
  --collect-submodules webview \
  --collect-data webview \
  --hidden-import webview \
  --hidden-import webview.platforms.cocoa \
  --hidden-import objc \
  --hidden-import Foundation \
  --hidden-import AppKit \
  --hidden-import WebKit \
  --hidden-import uvicorn \
  --hidden-import uvicorn.lifespan.on \
  --hidden-import uvicorn.lifespan.off \
  --hidden-import uvicorn.loops \
  --hidden-import uvicorn.loops.auto \
  --hidden-import uvicorn.loops.asyncio \
  --hidden-import uvicorn.protocols \
  --hidden-import uvicorn.protocols.http \
  --hidden-import uvicorn.protocols.http.auto \
  --hidden-import uvicorn.protocols.http.h11_impl \
  --hidden-import uvicorn.protocols.websockets \
  --hidden-import uvicorn.protocols.websockets.auto \
  --hidden-import uvicorn.protocols.websockets.wsproto_impl \
  "${ICON_FLAG[@]}" \
  "$BACKEND_DIR/main.py"

APP_PATH="$DIST_DIR/LocationSpoofer.app"

if [[ ! -d "$APP_PATH" ]]; then
  echo "✗ Build failed — $APP_PATH not produced." >&2
  exit 1
fi

# ---- Ad-hoc code signing ----------------------------------------------------
# Without a paid Apple Developer ID we can't notarize, but ad-hoc signing
# (sign with "-") gives the bundle a stable identity so macOS doesn't
# permanently quarantine it after the first right-click → Open.
echo "==> Ad-hoc code signing..."
codesign --force --deep --sign - "$APP_PATH"
codesign --verify --verbose=1 "$APP_PATH" || {
  echo "⚠ Code signature verification failed — the .app may still launch with a warning."
}

# ---- Create DMG -------------------------------------------------------------
DMG_PATH="$DIST_DIR/LocationSpoofer.dmg"
DMG_BACKGROUND="$ASSETS_DIR/dmg-background.png"
echo "==> Creating DMG..."

# Prefer create-dmg for the polished installer (custom background, sized
# window, positioned icons, drop arrow). Fall back to plain hdiutil if it
# isn't installed — that path still ships a working DMG, just less pretty.
if command -v create-dmg >/dev/null 2>&1 && [[ -f "$DMG_BACKGROUND" ]]; then
  echo "    Using create-dmg with background image."
  rm -f "$DMG_PATH"
  create-dmg \
    --volname "Location Spoofer" \
    --background "$DMG_BACKGROUND" \
    --window-pos 200 120 \
    --window-size 600 400 \
    --icon-size 128 \
    --icon "LocationSpoofer.app" 150 230 \
    --hide-extension "LocationSpoofer.app" \
    --app-drop-link 450 230 \
    --no-internet-enable \
    "$DMG_PATH" \
    "$APP_PATH" || {
      echo "⚠ create-dmg failed — falling back to plain hdiutil."
      DMG_FALLBACK=1
    }
else
  echo "    create-dmg not installed — using plain hdiutil."
  echo "    (brew install create-dmg for a polished installer.)"
  DMG_FALLBACK=1
fi

if [[ "${DMG_FALLBACK:-0}" == "1" ]]; then
  DMG_STAGE="$WORK_DIR/dmg-staging"
  rm -rf "$DMG_STAGE"
  mkdir -p "$DMG_STAGE"
  cp -R "$APP_PATH" "$DMG_STAGE/"
  ln -s /Applications "$DMG_STAGE/Applications"
  hdiutil create \
    -volname "LocationSpoofer" \
    -srcfolder "$DMG_STAGE" \
    -ov \
    -format UDZO \
    -fs HFS+ \
    "$DMG_PATH"
  rm -rf "$DMG_STAGE"
fi

echo
echo "✓ Build complete."
echo "   App: $APP_PATH"
echo "   DMG: $DMG_PATH"
echo
echo "------------------------------------------------------------------"
echo "  GATEKEEPER NOTE (ad-hoc signed, not notarized)"
echo "------------------------------------------------------------------"
echo "  This .app is ad-hoc signed but NOT notarized by Apple, so the"
echo "  first time anyone opens it macOS will say:"
echo
echo '      "LocationSpoofer.app cannot be opened because Apple cannot'
echo '       check it for malicious software."'
echo
echo "  To bypass on the first launch:"
echo "    1. Right-click LocationSpoofer.app → Open"
echo "    2. Click 'Open' in the dialog that appears"
echo "  OR"
echo "    System Settings → Privacy & Security → scroll down →"
echo "    click 'Open Anyway' next to LocationSpoofer."
echo
echo "  After that one-time approval the app launches normally."
echo "------------------------------------------------------------------"
