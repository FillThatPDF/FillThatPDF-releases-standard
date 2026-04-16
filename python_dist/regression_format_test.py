#!/usr/bin/env python3
"""
Formatting Regression Test
==========================
Processes all 29 static source PDFs through the v24 pipeline, then inspects
the output PDFs for JavaScript formatting actions (AA dictionaries).

For each PDF it reports:
  - total fields
  - JS-formatted field count
  - each formatted field name + detected format type

It also flags suspicious false positives:
  - phone format on fields NOT named phone/fax/tel/mobile
  - currency format on fields named Signature/Print_Name/Date/Address/Email
  - date format on fields not plausibly date-related
  - state format on fields not plausibly state/ST-related
  - zip format on fields not plausibly zip-related

Produces a summary at the end.
"""

import os
import sys
import re
import time
import tempfile
import shutil
import signal
from pathlib import Path
from contextlib import contextmanager

# ----- paths ----------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
PDF_SOURCE_DIR = Path("/Users/36981/Desktop/PDFTest/PDFs to test/Static PDFs/1")
PYENV_PYTHON = SCRIPT_DIR / "../../pyenv/bin/python"

# Add script dir so fillable_engine is importable
sys.path.insert(0, str(SCRIPT_DIR))

import logging
logging.basicConfig(filename='/tmp/regression_format_test_debug.log',
                    level=logging.DEBUG, filemode='w')
logging.getLogger('pdfminer').setLevel(logging.WARNING)
logging.getLogger('pdfplumber').setLevel(logging.WARNING)

from fillable_engine import Pipeline
from fillable_engine.detectors import ALL_DETECTORS
from fillable_engine.postprocess import ALL_POSTPROCESSORS

import pikepdf

# ----- timeout helper -------------------------------------------------------
class TimeoutError(Exception):
    pass

@contextmanager
def timeout(seconds):
    def handler(signum, frame):
        raise TimeoutError(f"Timed out after {seconds}s")
    old = signal.signal(signal.SIGALRM, handler)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)

# ----- JS format classification ---------------------------------------------
FORMAT_PATTERNS = {
    'date':     re.compile(r'AFDate_Format', re.IGNORECASE),
    'phone':    re.compile(r'AFSpecial_Format\(2\)', re.IGNORECASE),
    'zip':      re.compile(r'AFSpecial_Format\(0\)', re.IGNORECASE),
    'state':    re.compile(r'event\.change\s*=\s*event\.change\.toUpperCase', re.IGNORECASE),
    'ssn':      re.compile(r'AFSpecial_Format\(3\)', re.IGNORECASE),
    'currency_with_$': re.compile(r'AFNumber_Format\(2.*?"\$"', re.IGNORECASE),
    'currency_no_$':   re.compile(r'AFNumber_Format\(2.*?""', re.IGNORECASE),
    'number':   re.compile(r'AFNumber_Format\(0', re.IGNORECASE),
}

def classify_js(js_string: str) -> str:
    """Classify a JS formatting action string."""
    for name, pat in FORMAT_PATTERNS.items():
        if pat.search(js_string):
            return name
    return 'unknown'

# ----- false-positive detection ---------------------------------------------
PHONE_OK_RE = re.compile(r'(phone|fax|tel|mobile|cell|contact.*num)', re.I)
CURRENCY_BAD_RE = re.compile(
    r'(signature|print.*name|printed.*name|date|address|email|city|state|zip|'
    r'account.*name|company.*name|customer.*name|first.*name|last.*name|'
    r'applicant.*name|contact.*name|title|county)', re.I)
DATE_OK_RE = re.compile(r'(date|dob|birth|expir|start|end|install|complet)', re.I)
STATE_OK_RE = re.compile(r'(state|st\b|province)', re.I)
ZIP_OK_RE = re.compile(r'(zip|postal|post.*code)', re.I)
SSN_OK_RE = re.compile(r'(ssn|social|ss.*num|tax.*id|ein|fein|tin)', re.I)

def check_false_positive(field_name: str, fmt: str) -> str:
    """Return warning string if this looks like a false positive, else ''."""
    fn = field_name.replace('_', ' ')
    if fmt == 'phone' and not PHONE_OK_RE.search(fn):
        return f"  ** FALSE POSITIVE? phone format on '{field_name}'"
    if fmt in ('currency_with_$', 'currency_no_$') and CURRENCY_BAD_RE.search(fn):
        return f"  ** FALSE POSITIVE? currency format on '{field_name}'"
    if fmt == 'date' and not DATE_OK_RE.search(fn):
        return f"  ** FALSE POSITIVE? date format on '{field_name}'"
    if fmt == 'state' and not STATE_OK_RE.search(fn):
        return f"  ** FALSE POSITIVE? state format on '{field_name}'"
    if fmt == 'zip' and not ZIP_OK_RE.search(fn):
        return f"  ** FALSE POSITIVE? zip format on '{field_name}'"
    if fmt == 'ssn' and not SSN_OK_RE.search(fn):
        return f"  ** FALSE POSITIVE? ssn format on '{field_name}'"
    return ''

# ----- inspect output PDF for JS actions ------------------------------------
def inspect_js_formats(pdf_path: str):
    """Open a fillable PDF and extract all fields with JS formatting.

    Returns:
        (total_fields, formatted_fields_list)
        where formatted_fields_list = [(field_name, format_type), ...]
    """
    total = 0
    formatted = []

    try:
        pdf = pikepdf.open(pdf_path)
    except Exception as e:
        return 0, [], f"Could not open output PDF: {e}"

    root = pdf.Root
    acroform = root.get('/AcroForm')
    if not acroform:
        pdf.close()
        return 0, [], None

    fields = acroform.get('/Fields')
    if not fields:
        pdf.close()
        return 0, [], None

    for field_ref in fields:
        try:
            field = field_ref
            if isinstance(field, pikepdf.Object):
                # dereference if indirect
                pass
        except Exception:
            continue

        total += 1
        name = ''
        try:
            t_val = field.get('/T')
            if t_val is not None:
                name = str(t_val)
        except Exception:
            pass

        # Check for /AA (additional actions)
        aa = field.get('/AA')
        if aa is None:
            continue
        f_action = aa.get('/F')
        if f_action is None:
            continue
        js_obj = f_action.get('/JS')
        if js_obj is None:
            continue

        js_str = str(js_obj)
        fmt = classify_js(js_str)
        formatted.append((name, fmt))

    pdf.close()
    return total, formatted, None

# ----- source PDF filter -----------------------------------------------------
def is_source_pdf(filename: str) -> bool:
    """True if filename is a source PDF (not _FILLABLE or _FILLED)."""
    base = filename.replace('.pdf', '').replace('.PDF', '')
    if base.endswith('_FILLABLE') or base.endswith('_FILLED'):
        return False
    if '_FILLABLE_FILLED' in base:
        return False
    return True

# ----- main ------------------------------------------------------------------
def main():
    # Collect source PDFs
    all_files = sorted(PDF_SOURCE_DIR.glob('*.pdf'))
    source_pdfs = [f for f in all_files if is_source_pdf(f.name)]

    print(f"\n{'='*70}")
    print(f"  FORMATTING REGRESSION TEST")
    print(f"  Source directory: {PDF_SOURCE_DIR}")
    print(f"  Source PDFs found: {len(source_pdfs)}")
    print(f"{'='*70}\n")

    # Create a temporary output directory
    tmp_dir = Path(tempfile.mkdtemp(prefix='fmt_regression_'))
    print(f"  Temp output dir: {tmp_dir}\n")

    results = []       # (pdf_name, status, total_fields, formatted_list, warnings, elapsed)
    all_warnings = []   # global warnings collection
    per_pdf_timeout = 120  # seconds per PDF

    for idx, src_pdf in enumerate(source_pdfs, 1):
        pdf_name = src_pdf.name
        short = pdf_name[:55]
        out_pdf = tmp_dir / f"{src_pdf.stem}_FILLABLE.pdf"

        print(f"[{idx:2d}/{len(source_pdfs)}] {short}")
        t0 = time.time()

        try:
            with timeout(per_pdf_timeout):
                # Run the pipeline
                pipeline = Pipeline(
                    input_pdf=str(src_pdf),
                    output_pdf=str(out_pdf),
                    settings={},
                )
                pipeline.register_detectors(ALL_DETECTORS)
                pipeline.register_postprocessors(ALL_POSTPROCESSORS)

                # Suppress stdout noise
                import io
                old_stdout = sys.stdout
                sys.stdout = io.StringIO()
                try:
                    pipeline.run()
                finally:
                    sys.stdout = old_stdout

        except TimeoutError:
            elapsed = time.time() - t0
            print(f"       TIMEOUT after {elapsed:.1f}s")
            results.append((pdf_name, 'TIMEOUT', 0, [], [], elapsed))
            continue
        except Exception as e:
            elapsed = time.time() - t0
            err_msg = str(e)[:80]
            print(f"       ERROR: {err_msg} ({elapsed:.1f}s)")
            results.append((pdf_name, f'ERROR: {err_msg}', 0, [], [], elapsed))
            continue

        elapsed = time.time() - t0

        # Now inspect the output
        if not out_pdf.exists():
            print(f"       No output file generated ({elapsed:.1f}s)")
            results.append((pdf_name, 'NO OUTPUT', 0, [], [], elapsed))
            continue

        total, formatted, err = inspect_js_formats(str(out_pdf))
        if err:
            print(f"       Inspect error: {err} ({elapsed:.1f}s)")
            results.append((pdf_name, f'INSPECT ERR: {err}', 0, [], [], elapsed))
            continue

        # Check for false positives
        warnings = []
        for fname, fmt in formatted:
            w = check_false_positive(fname, fmt)
            if w:
                warnings.append(w)
                all_warnings.append((pdf_name, w))

        js_count = len(formatted)
        status = 'OK'
        fp_tag = f"  [{len(warnings)} FP warnings]" if warnings else ""
        print(f"       {total} fields, {js_count} JS-formatted{fp_tag} ({elapsed:.1f}s)")

        results.append((pdf_name, status, total, formatted, warnings, elapsed))

    # -------------------------------------------------------------------------
    # Detailed report
    # -------------------------------------------------------------------------
    print(f"\n\n{'='*70}")
    print(f"  DETAILED RESULTS")
    print(f"{'='*70}")

    total_pdfs = len(results)
    total_fields_all = 0
    total_js_all = 0
    total_fp = 0

    format_type_counts = {}

    for pdf_name, status, total, formatted, warnings, elapsed in results:
        print(f"\n--- {pdf_name} ---")
        print(f"    Status: {status} | Fields: {total} | JS-formatted: {len(formatted)} | Time: {elapsed:.1f}s")

        total_fields_all += total
        total_js_all += len(formatted)

        if formatted:
            # Group by format type
            by_type = {}
            for fname, fmt in formatted:
                by_type.setdefault(fmt, []).append(fname)
                format_type_counts[fmt] = format_type_counts.get(fmt, 0) + 1

            for fmt in sorted(by_type.keys()):
                names = by_type[fmt]
                print(f"    [{fmt}] ({len(names)} fields):")
                for n in names:
                    print(f"        - {n}")

        if warnings:
            total_fp += len(warnings)
            print(f"    WARNINGS ({len(warnings)}):")
            for w in warnings:
                print(f"      {w}")

    # -------------------------------------------------------------------------
    # Summary
    # -------------------------------------------------------------------------
    print(f"\n\n{'='*70}")
    print(f"  SUMMARY")
    print(f"{'='*70}")
    print(f"  PDFs processed:       {total_pdfs}")
    ok_count = sum(1 for r in results if r[1] == 'OK')
    err_count = sum(1 for r in results if r[1].startswith('ERROR'))
    to_count = sum(1 for r in results if r[1] == 'TIMEOUT')
    print(f"  Successful:           {ok_count}")
    print(f"  Errors:               {err_count}")
    print(f"  Timeouts:             {to_count}")
    print(f"  Total fields across all PDFs: {total_fields_all}")
    print(f"  Total JS-formatted fields:    {total_js_all}")
    print()

    if format_type_counts:
        print(f"  Format type breakdown:")
        for fmt in sorted(format_type_counts.keys()):
            print(f"    {fmt:25s}: {format_type_counts[fmt]}")
    print()

    print(f"  Total false-positive warnings: {total_fp}")
    if all_warnings:
        print(f"\n  All false-positive warnings:")
        for pdf_name, w in all_warnings:
            print(f"    [{pdf_name[:40]}] {w}")

    print(f"\n{'='*70}")

    # Clean up temp directory
    try:
        shutil.rmtree(tmp_dir)
        print(f"  Cleaned up temp dir: {tmp_dir}")
    except Exception:
        print(f"  Note: temp dir remains at {tmp_dir}")

    print(f"{'='*70}\n")


if __name__ == '__main__':
    main()
