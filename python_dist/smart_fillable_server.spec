# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for smart_fillable_server (arm64 native)
from PyInstaller.utils.hooks import collect_all, collect_submodules

# Collect all pikepdf components including native extensions
pikepdf_datas, pikepdf_binaries, pikepdf_hiddenimports = collect_all('pikepdf')

# Collect pypdfium2 (needed by extract_fields for page rendering)
pypdfium2_datas, pypdfium2_binaries, pypdfium2_hiddenimports = collect_all('pypdfium2')

a = Analysis(
    ['smart_fillable_server.py'],
    pathex=['.'],
    binaries=pikepdf_binaries + pypdfium2_binaries,
    datas=pikepdf_datas + pypdfium2_datas,
    hiddenimports=[
        # Core PDF libraries
        'pikepdf',
        'pikepdf._core',
        'pikepdf._qpdf',
        'pdfplumber',
        'pdfminer',
        'pdfminer.six',
        'pdfminer.high_level',
        'pdfminer.layout',
        'pdfminer.pdfpage',
        'pdfminer.pdfdocument',
        'pdfminer.pdfparser',
        'pdf2image',
        # Image/array processing
        'PIL',
        'PIL.Image',
        'numpy',
        # Page rendering (for extract_fields)
        'pypdfium2',
        'pypdfium2._helpers',
        'pypdfium2.raw',
        # The extract_fields module
        'extract_fields',
        # System
        'psutil',
        # XML
        'lxml',
        'lxml.etree',
        # Crypto (needed by pikepdf for encrypted PDFs)
        'cryptography',
        'cffi',
        'cffi.backend_ctypes',
        # The v24 modular engine — collect ALL submodules
        'fillable_engine',
        'fillable_engine.pipeline',
        'fillable_engine.page_analyzer',
        'fillable_engine.resolver',
        'fillable_engine.writer',
        'fillable_engine.helpers',
        'fillable_engine.models',
        # Detectors (actual module names)
        'fillable_engine.detectors',
        'fillable_engine.detectors.base',
        'fillable_engine.detectors.checkboxes',
        'fillable_engine.detectors.horizontal_line_tables',
        'fillable_engine.detectors.table_cells',
        'fillable_engine.detectors.form_lines',
        'fillable_engine.detectors.signatures',
        'fillable_engine.detectors.empty_boxes',
        'fillable_engine.detectors.underscore_fields',
        'fillable_engine.detectors.special_labels',
        'fillable_engine.detectors.inline_labels',
        'fillable_engine.detectors.box_entry',
        'fillable_engine.detectors.image_rectangles',
        'fillable_engine.detectors.inline_underlines',
        'fillable_engine.detectors.discovery',
        'fillable_engine.detectors.shaded_fields',
        # Post-processors (actual module names)
        'fillable_engine.postprocess',
        'fillable_engine.postprocess.radio_groups',
        'fillable_engine.postprocess.label_enrichment',
        'fillable_engine.postprocess.label_below_split',
        'fillable_engine.postprocess.height_standardization',
        'fillable_engine.postprocess.line_snapping',
        'fillable_engine.postprocess.adjacent_merge',
        'fillable_engine.postprocess.grid_gap_fill',
        'fillable_engine.postprocess.address_recovery',
        'fillable_engine.postprocess.text_column_filter',
        'fillable_engine.postprocess.cross_page_propagation',
        'fillable_engine.postprocess.label_trimmer',
    ] + pikepdf_hiddenimports + pypdfium2_hiddenimports + collect_submodules('fillable_engine'),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'pandas',
        'matplotlib',
        'scipy',
        'torch',
        'torchvision',
        'paddle',
        'paddleocr',
        'cv2',
        'tensorflow',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# --onedir build: the EXE only contains scripts (no binaries/datas).
# Binaries and datas are placed alongside the executable by COLLECT.
# This eliminates the 5-10 second temp-extraction overhead of --onefile.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='smart_fillable_server',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
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
    upx=True,
    upx_exclude=[],
    name='smart_fillable_server',
)
