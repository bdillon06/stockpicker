# Swing Finder — Desktop builds

Package Swing Finder as a double-click desktop app for **Windows, macOS and
Linux**. Each build is a self-contained executable (bundled Python + Flask +
yfinance) — end users need **nothing installed**. Launching it starts the local
server and opens the app in their browser; closing the window quits it.

User data (price cache + watchlist) is stored **outside** the app bundle so it
survives upgrades and writes succeed even from a read-only install location:

| OS      | Data location                                  |
|---------|------------------------------------------------|
| Windows | `%LOCALAPPDATA%\SwingFinder\stockpicker.db`    |
| macOS   | `~/Library/Application Support/SwingFinder/`    |
| Linux   | `~/.local/share/SwingFinder/`                  |

---

## Important: builds are per-OS

PyInstaller **cannot cross-compile** — a Windows `.exe` must be built on
Windows, a Mac `.app` on macOS, a Linux binary on Linux. Two ways to get all
three:

### Option A — Automatic, all three at once (recommended)

The GitHub Actions workflow `.github/workflows/build-desktop.yml` builds all
three on native runners and attaches them to a Release.

```bash
git tag v1.0.0
git push origin v1.0.0
```

Watch the **Actions** tab; when it finishes, the installers are on the matching
**Release** (and as run artifacts). You can also trigger it manually from the
Actions tab ("Run workflow") without a tag.

### Option B — Build locally on each machine

Run the script for the OS you're on, from the project root:

| OS      | Command                                                                 | Produces                                |
|---------|-------------------------------------------------------------------------|-----------------------------------------|
| Linux   | `bash packaging/build_linux.sh`                                         | `dist/SwingFinder-linux-x86_64-*.tar.gz`|
| macOS   | `bash packaging/build_macos.sh`                                        | `dist/SwingFinder-macOS-*.dmg`          |
| Windows | `powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1` | `dist/SwingFinder-Setup-*.exe`          |

Each script creates an isolated `.venv-build`, installs deps, runs PyInstaller
against `packaging/swingfinder.spec`, then packages a shareable installer.

---

## Per-platform notes

**Windows** — the `.exe` runs as-is. For a real installer (Start-menu +
desktop shortcut + uninstaller) install [Inno Setup 6](https://jrsoftware.org/isdl.php);
the build script then compiles `packaging/installer.iss` into
`SwingFinder-Setup-<version>.exe`. The app is unsigned, so SmartScreen shows a
"more info → Run anyway" prompt; buy a code-signing certificate to remove it.

**macOS** — produces `SwingFinder.app` inside a drag-to-Applications `.dmg`.
Unsigned, so first launch is **right-click → Open** (or System Settings →
Privacy & Security → Open Anyway). To ship without the prompt, codesign +
notarize with an Apple Developer ID.

**Linux** — a single executable plus a `.tar.gz`. Recipients extract and run
`./SwingFinder`. Built on Ubuntu for broad glibc compatibility.

## Icons (optional)

Drop `packaging/icon.ico` (Windows) and `packaging/icon.icns` (macOS) and the
spec picks them up automatically. Without them the OS default icon is used.

## Customising the port

The app defaults to `http://127.0.0.1:5057`, falling back to a random free port
if 5057 is busy. Set `PORT` in the environment to force a specific port.
