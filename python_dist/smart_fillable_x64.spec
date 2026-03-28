# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for smart_fillable v24 (x64 via Rosetta)
from PyInstaller.utils.hooks import collect_all, collect_submodules

# Collect all pikepdf components including native extensions
pikepdf_datas, pikepdf_binaries, pikepdf_hiddenimports = collect_all('pikepdf')

a = Analysis(
    ['smart_fillable_v24.py'],
    pathex=['.'],
    binaries=pikepdf_binaries,
    datas=pikepdf_datas,
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
    ] + pikepdf_hiddenimports + collect_submodules('fillable_engine'),
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

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='smart_fillable_x64',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch='x86_64',
    codesign_identity=None,
    entitlements_file=None,
)
