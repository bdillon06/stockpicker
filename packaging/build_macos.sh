#!/usr/bin/env bash
# Build the macOS .app and wrap it in a drag-to-install .dmg.
# Run on a Mac, from the project root:  bash packaging/build_macos.sh
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"
echo "==> Setting up build venv (.venv-build)"
[ -d .venv-build ] || "$PY" -m venv .venv-build
# shellcheck disable=SC1091
. .venv-build/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt -r packaging/requirements-build.txt

echo "==> Cleaning previous build"
rm -rf build dist

echo "==> Running PyInstaller"
pyinstaller --noconfirm --clean packaging/swingfinder.spec

VERSION="$(date +%Y.%m.%d)"
DMG="dist/SwingFinder-macOS-${VERSION}.dmg"
echo "==> Building DMG"
# Stage the .app + an Applications symlink so users drag-to-install.
STAGE="dist/dmg"
rm -rf "$STAGE" && mkdir -p "$STAGE"
cp -R "dist/SwingFinder.app" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "Swing Finder" -srcfolder "$STAGE" \
  -ov -format UDZO "$DMG"

echo "==> Done"
echo "    App   : dist/SwingFinder.app"
echo "    Share : ${DMG}"
echo
echo "NOTE: the .app is unsigned. On first launch users right-click -> Open"
echo "      (or System Settings -> Privacy & Security -> Open Anyway). To ship"
echo "      without that prompt, codesign + notarize with an Apple Developer ID."
