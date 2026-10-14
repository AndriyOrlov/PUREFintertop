# -*- mode: python ; coding: utf-8 -*-
# Build: python -m PyInstaller --noconfirm PureRef-FrameRef-Converter.spec
from PyInstaller.utils.hooks import collect_data_files

datas = [
    ("NOTICE", "."),
    ("LICENSE", "."),
    ("packaging/converter.ico", "."),
    ("packaging/converter.png", "."),
]
datas += collect_data_files("pur_adapter")
datas += collect_data_files("sv_ttk")  # Windows 11 style theme for the window

a = Analysis(
    ["packaging/converter_entry.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=["sv_ttk"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="PureRef-FrameRef-Converter",
    icon="packaging/converter.ico",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    # A window application: no black console flashes up. Command-line use
    # still prints by attaching to the console it was started from.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
