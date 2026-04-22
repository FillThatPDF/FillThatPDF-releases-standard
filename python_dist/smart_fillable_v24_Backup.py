#!/usr/bin/env python3
"""
FillThatPDF v24 — Modular Field Detection Engine
=================================================

Architecture: Analyze → Detect Candidates → Resolve → Write
No field is ever "created then deleted" — candidates are scored and committed once.

This replaces v23's monolithic 23,000-line single class with a modular pipeline.

Usage:
    python smart_fillable_v24.py input.pdf [output.pdf] [--settings JSON] [--pages "1,3-5"] [--auto-calibrate]
"""

import sys
import os
import json
import time
import logging
import argparse

# Reduce verbose parsing logs from pdfminer/pdfplumber
logging.basicConfig(filename='/tmp/smart_fillable_debug.log', level=logging.DEBUG, filemode='w')
logging.getLogger('pdfminer').setLevel(logging.WARNING)
logging.getLogger('pdfplumber').setLevel(logging.WARNING)

from fillable_engine import Pipeline
from fillable_engine.detectors import ALL_DETECTORS
from fillable_engine.postprocess import ALL_POSTPROCESSORS


def main():
    parser = argparse.ArgumentParser(
        description='FillThatPDF v24 — Modular fillable PDF generator',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python smart_fillable_v24.py form.pdf
    python smart_fillable_v24.py form.pdf output.pdf --settings '{"table_cell_padding": 2}'
    python smart_fillable_v24.py form.pdf --pages "1,3-5"
    python smart_fillable_v24.py form.pdf --auto-calibrate
        """
    )
    parser.add_argument('input_pdf', help='Input PDF file')
    parser.add_argument('output_pdf', nargs='?', help='Output PDF file (optional)')
    parser.add_argument('--settings', type=str, metavar='JSON',
                        help='JSON string or file path containing settings overrides')
    parser.add_argument('--pages', type=str,
                        help='Process specific pages (e.g. "1,3-5")')
    parser.add_argument('--auto-calibrate', action='store_true',
                        help='Automatically analyze PDF structure and optimize settings')

    # v23-compatible args (accepted but some may be no-ops in v24)
    parser.add_argument('--enhanced', choices=['off', 'conservative', 'aggressive'],
                        default='off', help='Enhanced detection mode (reserved for future)')
    parser.add_argument('--inspect', action='store_true',
                        help='Run inspection only (no PDF generated)')
    parser.add_argument('--skip-inspection', action='store_true',
                        help='Skip inspection (default in v24)')
    parser.add_argument('--qa-overlay', type=str, metavar='FILE',
                        help='Write QA overlay PDF (reserved for future)')
    parser.add_argument('--candidate-json', type=str, metavar='FILE',
                        help='Write JSON with field candidates (reserved for future)')
    parser.add_argument('--fix', type=str, metavar='IDS',
                        help='Fix IDs (reserved for future)')

    args = parser.parse_args()

    # Validate input
    if not os.path.exists(args.input_pdf):
        print(f"Error: Input file not found: {args.input_pdf}", file=sys.stderr)
        sys.exit(1)

    # Parse settings
    settings = {}
    if args.settings:
        try:
            if os.path.exists(args.settings):
                with open(args.settings, 'r', encoding='utf-8') as f:
                    settings = json.load(f)
            else:
                settings = json.loads(args.settings)
            print(f"   Loaded {len(settings)} settings from app")
        except json.JSONDecodeError as e:
            print(f"   Warning: Could not parse settings JSON: {e}")

    # Auto-calibrate (placeholder — will be implemented with page analysis)
    if args.auto_calibrate:
        settings['auto_calibrate'] = True

    # Create pipeline
    pipeline = Pipeline(
        input_pdf=args.input_pdf,
        output_pdf=args.output_pdf,
        settings=settings,
        pages=args.pages,
    )

    # Register all 13 detectors
    pipeline.register_detectors(ALL_DETECTORS)

    # Register all 4 post-processors (label enrichment, radio groups, height, snapping)
    pipeline.register_postprocessors(ALL_POSTPROCESSORS)

    # Run the pipeline
    try:
        output_path = pipeline.run()
        print(f"\nOutput: {output_path}")
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
