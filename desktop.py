"""Desktop launcher for Swing Finder.

Starts the bundled Flask server on a local port and opens the app in the user's
default browser, so a double-click behaves like a native desktop app. Runs both
as a PyInstaller-frozen executable (Windows / macOS / Linux) and directly via
``python desktop.py`` during development.

The SQLite cache + watchlist live in a per-user data directory (not inside the
read-only app bundle) so writes succeed and your data survives app upgrades.
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

APP_NAME = "SwingFinder"
PREFERRED_PORT = 5057


def user_data_dir() -> str:
    """Platform-appropriate writable directory for the DB."""
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    path = os.path.join(base, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def pick_port(preferred: int = PREFERRED_PORT) -> int:
    """Use the preferred port if free, else an OS-assigned ephemeral one."""
    for candidate in (preferred, 0):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(("127.0.0.1", candidate))
            port = s.getsockname()[1]
            s.close()
            return port
        except OSError:
            continue
    return preferred


def wait_until_up(url: str, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=0.5)
            return True
        except Exception:
            time.sleep(0.2)
    return False


def main() -> None:
    data_dir = user_data_dir()
    # Must be set BEFORE importing app/db so db.DB_PATH picks it up at import.
    os.environ.setdefault("STOCKPICKER_DB", os.path.join(data_dir, "stockpicker.db"))

    import app as flask_app  # noqa: E402  (deferred until env is configured)

    port = pick_port(int(os.environ.get("PORT", PREFERRED_PORT)))
    url = f"http://127.0.0.1:{port}"

    def serve() -> None:
        # Threaded so a long /api/refresh doesn't block the UI; no reloader in a
        # frozen build (the reloader re-execs the interpreter).
        flask_app.app.run(host="127.0.0.1", port=port, debug=False,
                          use_reloader=False, threaded=True)

    threading.Thread(target=serve, daemon=True).start()

    up = wait_until_up(url)
    banner = (
        "\n  ====================================================\n"
        "    📈  Swing Finder is running\n"
        f"        Open:  {url}\n"
        f"        Data:  {data_dir}\n"
        "        Close this window to quit.\n"
        "  ====================================================\n"
    )
    print(banner, flush=True)
    if up:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    else:
        print("  (Server slow to start — open the URL above manually.)", flush=True)

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\n  Swing Finder stopped.", flush=True)


if __name__ == "__main__":
    main()
