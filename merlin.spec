# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for MERLIN (one-folder, windowed build).
# Build:  pyinstaller merlin.spec  (from the project root)

import os

block_cipher = None

datas = [
    ("merlin/prompts/system_prompt.txt", "merlin/prompts"),
]

a = Analysis(
    ["main.py"],
    pathex=[os.getcwd()],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter.test", "test"],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="Merlin",
    debug=False,
    strip=False,
    upx=False,
    console=False,          # GUI app: no console window
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="Merlin",
)
