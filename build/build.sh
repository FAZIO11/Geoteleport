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
  --collect-submodules pymobiledevice3 \
  --collect-data pymobiledevice3 \
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

# ---- Create DMG -------------------------------------------------------------
DMG_PATH="$DIST_DIR/LocationSpoofer.dmg"
echo "==> Creating DMG..."

# Staging folder for the DMG contents
DMG_STAGE="$WORK_DIR/dmg-staging"
rm -rf "$DMG_STAGE"
mkdir -p "$DMG_STAGE"

# Copy the .app and add a symlink to /Applications for drag-install UX
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

echo
echo "✓ Build complete."
echo "   App: $APP_PATH"
echo "   DMG: $DMG_PATH"
echo
echo "------------------------------------------------------------------"
echo "  CODE SIGNING NOTE"
echo "------------------------------------------------------------------"
echo "  This .app is NOT signed by a paid Apple Developer account, so"
echo "  the first time anyone opens it macOS will say:"
echo
echo '      "LocationSpoofer.app cannot be opened because the developer'
echo '       cannot be verified."'
echo
echo "  To bypass:"
echo "    1. Right-click LocationSpoofer.app → Open"
echo "    2. Click 'Open' in the dialog that appears"
echo "  OR"
echo "    System Settings → Privacy & Security → scroll down →"
echo "    click 'Open Anyway' next to LocationSpoofer."
echo "------------------------------------------------------------------"
