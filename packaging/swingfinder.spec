# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Swing Finder — builds a one-file desktop executable.

Used identically on Windows, macOS and Linux:

    pyinstaller packaging/swingfinder.spec

Output lands in ``dist/`` (a single binary, plus a .app bundle on macOS).
Run from the project root so the relative data paths below resolve.
"""
import os
import sys

from PyInstaller.utils.hooks import collect_all

# Run from project root: spec dir is packaging/, project is its parent.
ROOT = os.path.abspath(os.path.join(os.getcwd()))

block_cipher = None

# Bundle read-only assets the app reads at runtime. data.py / app.py locate
# these via sys._MEIPASS when frozen, so the layout here must mirror the repo.
datas = [
    (os.path.join(ROOT, "static"), "static"),
    (os.path.join(ROOT, "universe.csv"), "."),
    (os.path.join(ROOT, "seed_snapshot.csv"), "."),
]
binaries = []
hiddenimports = []

# yfinance and friends pull in data files / dynamic submodules that PyInstaller
# misses without an explicit collect. Tolerate any not installed in a slim env.
for pkg in ("yfinance", "curl_cffi", "peewee", "multitasking", "frozendict",
            "pandas", "numpy"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

a = Analysis(
    [os.path.join(ROOT, "desktop.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "gunicorn", "tkinter"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="SwingFinder",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # keep a console window; closing it quits the app
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ROOT, "packaging", "icon.ico")
        if os.path.exists(os.path.join(ROOT, "packaging", "icon.ico")) else None,
)

# macOS: also wrap the binary in a .app bundle so it installs like a Mac app.
if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name="SwingFinder.app",
        icon=os.path.join(ROOT, "packaging", "icon.icns")
            if os.path.exists(os.path.join(ROOT, "packaging", "icon.icns")) else None,
        bundle_identifier="com.swingfinder.desktop",
        info_plist={
            "CFBundleName": "Swing Finder",
            "CFBundleDisplayName": "Swing Finder",
            "CFBundleShortVersionString": "1.0.0",
            "NSHighResolutionCapable": True,
            "LSBackgroundOnly": False,
        },
    )
