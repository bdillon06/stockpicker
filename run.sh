#!/usr/bin/env bash
# Create the venv (first run), install deps, and launch the app.
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "Creating virtualenv…"
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
. .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt

echo "Starting Swing Finder on http://127.0.0.1:5057"
exec python app.py
