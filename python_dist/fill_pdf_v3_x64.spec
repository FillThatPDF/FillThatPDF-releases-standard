# -*- mode: python ; coding: utf-8 -*-
# Onedir build: ~3-5s faster cold start vs onefile (no /tmp extraction).


a = Analysis(
    ['fill_pdf_v3.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['pikepdf', 'pikepdf._qpdf'],
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
    [],
    exclude_binaries=True,
    name='fill_pdf_v3',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='fill_pdf_v3',
)
