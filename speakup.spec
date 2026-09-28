# -*- mode: python ; coding: utf-8 -*-
"""
SpeakUp — PyInstaller spec for Windows executable.

Build with:
    python -m PyInstaller speakup.spec

Output:
    dist/SpeakUp/SpeakUp.exe   (one-folder, recommended)
or
    dist/SpeakUp.exe            (one-file, slower startup)
"""

import sys
from pathlib import Path

ROOT = Path(SPEC).resolve().parent   # noqa: F821 — PyInstaller injects SPEC

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
a = Analysis(
    [str(ROOT / "app.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        # Include bundled word topics and assets
        (str(ROOT / "speakup" / "data" / "words" / "topics.json"),
         "speakup/data/words"),
        (str(ROOT / "speakup" / "assets"),
         "speakup/assets"),
        # Include .env.example as reference (NOT .env — never bundle secrets)
        (str(ROOT / ".env.example"), "."),
    ],
    hiddenimports=[
        # PySide6 plugins needed at runtime
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "PySide6.QtMultimedia",
        # matplotlib backend
        "matplotlib.backends.backend_qtagg",
        # SQLAlchemy dialects
        "sqlalchemy.dialects.sqlite",
        # faster-whisper / ctranslate2
        "ctranslate2",
        "faster_whisper",
        # sounddevice
        "sounddevice",
        "_sounddevice_data",
        # OpenAI (optional, may not be installed)
        "openai",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude things we definitely don't use in production
        "tkinter",
        "unittest",
        "test",
        "doctest",
        "pdb",
        "profile",
        "cProfile",
        "IPython",
        "jupyter",
        "notebook",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

# ---------------------------------------------------------------------------
# PYZ archive
# ---------------------------------------------------------------------------
pyz = PYZ(a.pure, a.zipped_data, cipher=None)  # noqa: F821

# ---------------------------------------------------------------------------
# EXE
# ---------------------------------------------------------------------------
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SpeakUp",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,                           # GUI app — no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "speakup" / "assets" / "icon.ico"),
    version_file=None,
)

# ---------------------------------------------------------------------------
# COLLECT (one-folder distribution)
# ---------------------------------------------------------------------------
coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="SpeakUp",
)
