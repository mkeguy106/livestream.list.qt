# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Livestream List Qt (Windows --onedir build)."""

import os
import sys

block_cipher = None

# Paths — SPECPATH is the directory containing the .spec file
SPEC_DIR = SPECPATH
SRC_DIR = os.path.join(SPEC_DIR, "src", "livestream_list")
DATA_DIR = os.path.join(SPEC_DIR, "data")

# Collect data files to bundle
datas = [
    # Spellcheck word list
    (os.path.join(SRC_DIR, "chat", "spellcheck", "data"), "livestream_list/chat/spellcheck/data"),
    # App icon + desktop metadata
    (DATA_DIR, "data"),
]

# Bundled yt-dlp.exe (downloaded during CI)
ytdlp_exe = os.path.join(SPEC_DIR, "yt-dlp.exe")
if os.path.isfile(ytdlp_exe):
    datas.append((ytdlp_exe, "."))

a = Analysis(
    [os.path.join(SRC_DIR, "main.py")],
    pathex=[os.path.join(SPEC_DIR, "src")],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "keyring.backends.Windows",
        "desktop_notifier",
        "desktop_notifier.main",
        "desktop_notifier.resources",
        "pydantic",
        "pydantic_settings",
        "pytchat",
        "aiohttp",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LivestreamListQt",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # --windowed: no console window
    disable_windowed_traceback=False,
    icon=os.path.join(DATA_DIR, "icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="LivestreamListQt",
)
