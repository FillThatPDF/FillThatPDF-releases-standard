#!/usr/bin/env python3
"""
FillThatPDF v24 — Persistent Server Mode
==========================================

Keeps the Python runtime and all imports alive between PDF jobs.
The Electron app spawns this process ONCE at startup; subsequent
PDF processing requests are sent as JSON lines via stdin.

Protocol
--------
→ stdin  (one JSON object per line):
    {"cmd": "make_fillable", "id": "req-1",
     "input": "/path/to.pdf", "output": "/out.pdf",
     "settings": {...}, "auto_calibrate": true,
     "pages": null}

← stdout (free-form progress lines, then one RESULT JSON):
    Phase 1: Analyzing page structure...
    Phase 2: Running 13 detectors...
    ...
    RESULT:{"id":"req-1","success":true,"output":"/out.pdf"}

  or on error:
    RESULT:{"id":"req-1","success":false,"error":"..."}

Additional commands:
    {"cmd": "garbage_cleanup", "id": "req-2",
     "input": "/path/to.pdf", "output": "/out.pdf",
     "sensitivity": "standard"}

    {"cmd": "auto_rename", "id": "req-3",
     "input": "/path/to.pdf"}

Special commands:
    {"cmd": "ping"}                → RESULT:{"pong":true}
    {"cmd": "quit"}                → clean exit
"""

import sys
import os
import json
import time
import traceback

# -----------------------------------------------------------------------
# Eager import: pay the cost ONCE at server startup, not per PDF.
# -----------------------------------------------------------------------
# Redirect stdout during imports so stray print()s don't confuse the protocol.
_real_stdout = sys.stdout
_real_stderr = sys.stderr


def _import_engine():
    """Import the full pipeline + detectors + postprocessors once."""
    from fillable_engine import Pipeline
    from fillable_engine.detectors import ALL_DETECTORS
    from fillable_engine.postprocess import ALL_POSTPROCESSORS
    return Pipeline, ALL_DETECTORS, ALL_POSTPROCESSORS


def _import_extract_fields():
    """Import extract_fields module once — avoids per-call startup cost."""
    from extract_fields import extract_fields_and_images
    return extract_fields_and_images


def _import_garbage_cleanup():
    """Import garbage_field_cleanup module once."""
    from garbage_field_cleanup import cleanup_garbage_fields
    return cleanup_garbage_fields


def _import_auto_rename():
    """Import auto_rename_all module once."""
    from auto_rename_all import auto_rename_all_fields
    return auto_rename_all_fields


try:
    Pipeline, ALL_DETECTORS, ALL_POSTPROCESSORS = _import_engine()
except Exception as e:
    # If imports fail, report and exit — the Electron app will fall back
    print(f"RESULT:{{\"success\":false,\"error\":\"Import failed: {e}\"}}", flush=True)
    sys.exit(1)

try:
    extract_fields_and_images = _import_extract_fields()
except Exception as e:
    # Non-fatal — extract_fields can fall back to spawning the binary
    extract_fields_and_images = None
    print(f"Warning: extract_fields import failed: {e}", file=sys.stderr, flush=True)

try:
    cleanup_garbage_fields = _import_garbage_cleanup()
except Exception as e:
    cleanup_garbage_fields = None
    print(f"Warning: garbage_field_cleanup import failed: {e}", file=sys.stderr, flush=True)

try:
    auto_rename_all_fields = _import_auto_rename()
except Exception as e:
    auto_rename_all_fields = None
    print(f"Warning: auto_rename_all import failed: {e}", file=sys.stderr, flush=True)


# -----------------------------------------------------------------------
# Command handlers
# -----------------------------------------------------------------------

def handle_make_fillable(req: dict) -> dict:
    """
    Run the fillable-PDF pipeline.

    Required keys: input, output
    Optional keys: settings (dict), auto_calibrate (bool), pages (str)
    """
    input_pdf = req.get('input')
    output_pdf = req.get('output')
    settings = req.get('settings', {})
    pages = req.get('pages')

    if not input_pdf or not os.path.exists(input_pdf):
        return {'success': False, 'error': f'Input file not found: {input_pdf}'}

    if req.get('auto_calibrate'):
        settings['auto_calibrate'] = True

    pipeline = Pipeline(
        input_pdf=input_pdf,
        output_pdf=output_pdf,
        settings=settings,
        pages=pages,
    )
    pipeline.register_detectors(ALL_DETECTORS)
    pipeline.register_postprocessors(ALL_POSTPROCESSORS)

    output_path = pipeline.run()
    return {'success': True, 'output': str(output_path)}


def handle_garbage_cleanup(req: dict) -> dict:
    """
    Run garbage field cleanup on a PDF.

    Required keys: input
    Optional keys: output, sensitivity
    """
    if cleanup_garbage_fields is None:
        return {'success': False, 'error': 'garbage_field_cleanup module not available'}

    input_pdf = req.get('input')
    output_pdf = req.get('output', input_pdf)
    sensitivity = req.get('sensitivity', 'standard')

    if not input_pdf or not os.path.exists(input_pdf):
        return {'success': False, 'error': f'Input file not found: {input_pdf}'}

    result = cleanup_garbage_fields(input_pdf, output_pdf, verbose=False, sensitivity=sensitivity)
    return result


def handle_auto_rename(req: dict) -> dict:
    """
    Auto-rename all fields in a PDF based on nearby text.

    Required keys: input
    """
    if auto_rename_all_fields is None:
        return {'success': False, 'error': 'auto_rename_all module not available'}

    input_pdf = req.get('input')

    if not input_pdf or not os.path.exists(input_pdf):
        return {'success': False, 'error': f'Input file not found: {input_pdf}'}

    ok = auto_rename_all_fields(input_pdf)
    return {'success': ok}


def handle_extract_fields(req: dict) -> dict:
    """
    Extract form fields and page images from a PDF.

    Required keys: input
    Optional keys: output_dir
    """
    if extract_fields_and_images is None:
        return {'success': False, 'error': 'extract_fields module not available'}

    input_pdf = req.get('input')
    output_dir = req.get('output_dir')

    if not input_pdf or not os.path.exists(input_pdf):
        return {'success': False, 'error': f'Input file not found: {input_pdf}'}

    data = extract_fields_and_images(input_pdf, output_dir)
    return {'success': True, 'data': data}


# -----------------------------------------------------------------------
# Main server loop
# -----------------------------------------------------------------------

def main():
    # Signal readiness
    print("RESULT:{\"ready\":true}", flush=True)

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue

        # Parse the JSON command
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            print(f"RESULT:{{\"success\":false,\"error\":\"Bad JSON: {e}\"}}", flush=True)
            continue

        req_id = req.get('id', '')
        cmd = req.get('cmd', 'make_fillable')

        # ---- Ping / Quit ----
        if cmd == 'ping':
            print(f'RESULT:{{"id":"{req_id}","pong":true}}', flush=True)
            continue
        if cmd == 'quit':
            print(f'RESULT:{{"id":"{req_id}","quit":true}}', flush=True)
            break

        # ---- Make fillable ----
        if cmd == 'make_fillable':
            t0 = time.time()
            try:
                result = handle_make_fillable(req)
                result['id'] = req_id
                result['elapsed'] = round(time.time() - t0, 2)
            except Exception as e:
                tb = traceback.format_exc()
                # Print traceback as progress so it appears in the UI log
                print(tb, flush=True)
                result = {
                    'id': req_id,
                    'success': False,
                    'error': str(e),
                }

            print(f"RESULT:{json.dumps(result)}", flush=True)
            continue

        # ---- Garbage field cleanup ----
        if cmd == 'garbage_cleanup':
            t0 = time.time()
            try:
                result = handle_garbage_cleanup(req)
                result['id'] = req_id
                result['elapsed'] = round(time.time() - t0, 2)
            except Exception as e:
                tb = traceback.format_exc()
                print(tb, flush=True)
                result = {
                    'id': req_id,
                    'success': False,
                    'error': str(e),
                }

            print(f"RESULT:{json.dumps(result)}", flush=True)
            continue

        # ---- Auto rename all ----
        if cmd == 'auto_rename':
            t0 = time.time()
            try:
                result = handle_auto_rename(req)
                result['id'] = req_id
                result['elapsed'] = round(time.time() - t0, 2)
            except Exception as e:
                tb = traceback.format_exc()
                print(tb, flush=True)
                result = {
                    'id': req_id,
                    'success': False,
                    'error': str(e),
                }

            print(f"RESULT:{json.dumps(result)}", flush=True)
            continue

        # ---- Extract fields ----
        if cmd == 'extract_fields':
            t0 = time.time()
            try:
                result = handle_extract_fields(req)
                result['id'] = req_id
                result['elapsed'] = round(time.time() - t0, 2)
            except Exception as e:
                tb = traceback.format_exc()
                print(tb, flush=True)
                result = {
                    'id': req_id,
                    'success': False,
                    'error': str(e),
                }

            print(f"RESULT:{json.dumps(result)}", flush=True)
            continue

        # ---- Unknown command ----
        print(f'RESULT:{{"id":"{req_id}","success":false,"error":"Unknown cmd: {cmd}"}}', flush=True)


if __name__ == '__main__':
    main()
