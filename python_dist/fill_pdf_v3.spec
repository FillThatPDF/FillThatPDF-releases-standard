# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['fill_pdf_v3.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'pikepdf', 'pikepdf._qpdf',
        # v1.0.3: PIL is used for image-upload and signature test-fill.
        # Explicit hiddenimports so PyInstaller bundles the JPEG/PNG codecs.
        'PIL', 'PIL.Image', 'PIL.ImageFile',
        'PIL.JpegImagePlugin', 'PIL.PngImagePlugin',
    ],
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
    upx=True,
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
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='fill_pdf_v3',
)
