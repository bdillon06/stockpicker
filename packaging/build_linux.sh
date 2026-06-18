#!/usr/bin/env bash
# Build the Linux desktop binary + a shareable tarball.
# Run from the project root:  bash packaging/build_linux.sh
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

echo "==> Packaging tarball"
VERSION="$(date +%Y.%m.%d)"
PKG="SwingFinder-linux-x86_64-${VERSION}"
mkdir -p "dist/${PKG}"
cp dist/SwingFinder "dist/${PKG}/SwingFinder"
cp README.md "dist/${PKG}/README.md" 2>/dev/null || true
cat > "dist/${PKG}/RUN.txt" <<'EOF'
Swing Finder — Linux

To run:  ./SwingFinder    (or double-click in your file manager)
It opens http://127.0.0.1:5057 in your browser. Close the terminal to quit.
No Python install required. Data is stored in ~/.local/share/SwingFinder.
EOF
chmod +x "dist/${PKG}/SwingFinder"
tar -C dist -czf "dist/${PKG}.tar.gz" "${PKG}"

echo "==> Done"
echo "    Binary : dist/SwingFinder"
echo "    Share  : dist/${PKG}.tar.gz"
