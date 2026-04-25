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
