#!/usr/bin/env python3
"""Diagnostic script to analyze which checkboxes are being purged and why.

This will help identify if checkboxes are being incorrectly flagged for removal.
"""
import argparse
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from smart_fillable_v21 import UniversalPDFFillable as SmartFillable


def count_all_button_fields(pdf_path):
    """Count all button fields in PDF using pikepdf directly."""
    import pikepdf
    
    doc_level = 0
    page_level = 0
    
    with pikepdf.open(str(pdf_path)) as pdf:
        # Document-level fields
        if '/AcroForm' in pdf.Root:
            acroform = pdf.Root.AcroForm
            if '/Fields' in acroform:
                fields = acroform.Fields
                for field in fields:
                    ft = field.get('/FT')
                    if ft == '/Btn':
                        ff = int(field.get('/Ff', 0))
                        is_radio = bool(ff & (1 << 15))
                        if not is_radio:
                            doc_level += 1
        
        # Page-level annotations
        for page_num, page in enumerate(pdf.pages):
            if '/Annots' in page:
                for annot in page.Annots:
                    if annot.get('/Subtype') == '/Widget':
                        ft = annot.get('/FT')
                        parent = annot.get('/Parent')
                        if not ft and parent:
                            ft = parent.get('/FT')
                        if ft == '/Btn':
                            ff = int(annot.get('/Ff', 0))
                            if not ff and parent:
                                ff = int(parent.get('/Ff', 0))
                            is_radio = bool(ff & (1 << 15))
                            if not is_radio:
                                page_level += 1
    
    return doc_level, page_level


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pdf', required=True)
    args = parser.parse_args()

    inp = Path(args.pdf)
    if not inp.exists():
        print('ERROR: input not found'); return

    # Count all button fields directly
    doc_btns, page_btns = count_all_button_fields(inp)
    total_btns = doc_btns + page_btns
    print(f"Direct PDF analysis: {total_btns} total button fields ({doc_btns} doc-level, {page_btns} page-level)")

    settings = {'disable_checkboxes_in_table_cells': True, 'debug': False}
    sf = SmartFillable(str(inp), output_pdf=str(inp.parent / (inp.stem + '_diag.pdf')), settings=settings)

    import pdfplumber
    sf.pdf = pdfplumber.open(str(inp))
    try:
        sf._preload_structural_data()
        sf._detect_existing_form_fields()  # This populates existing_checkboxes
        sf._detect_all_checkboxes()
        sf._detect_table_cells()
    finally:
        pass

    # Analyze existing checkboxes
    detected_existing = len(getattr(sf, 'existing_checkboxes', []))
    print(f"Engine detected existing checkboxes: {detected_existing}")
    print(f"Detection gap: {total_btns - detected_existing} missing")
    print(f"Total table cells detected: {len(getattr(sf, 'table_cells', []))}")

    # Simulate the purging logic
    removed_existing = 0
    kept_existing = 0
    purge_details = []

    for ecb in getattr(sf, 'existing_checkboxes', []):
        cb_cx = ecb['x'] + ecb['width'] / 2
        cb_cy = ecb['y'] + ecb['height'] / 2
        contained = False
        matched_cell = None
        for cell in getattr(sf, 'table_cells', []):
            if cell.get('page') != ecb.get('page'):
                continue
            if cell['x0'] <= cb_cx <= cell['x1'] and cell['y0'] <= cb_cy <= cell['y1']:
                contained = True
                matched_cell = cell
                break

        if contained and sf.settings.get('disable_checkboxes_in_table_cells', True):
            purge_details.append({
                'page': ecb.get('page') + 1,
                'center': [cb_cx, cb_cy],
                'size': [ecb['width'], ecb['height']],
                'cell_bbox': [matched_cell['x0'], matched_cell['y0'], matched_cell['x1'], matched_cell['y1']] if matched_cell else None,
                'reason': 'inside_table_cell'
            })
            removed_existing += 1
        else:
            kept_existing += 1

    print(f"Checkboxes to be purged: {removed_existing}")
    print(f"Checkboxes to be kept: {kept_existing}")

    # Save details
    report = {
        'input_pdf': str(inp),
        'direct_pdf_analysis': {
            'total_button_fields': total_btns,
            'doc_level_buttons': doc_btns,
            'page_level_buttons': page_btns
        },
        'engine_detection': {
            'existing_checkboxes_total': detected_existing,
            'detection_gap': total_btns - detected_existing
        },
        'table_cells_total': len(getattr(sf, 'table_cells', [])),
        'to_purge': removed_existing,
        'to_keep': kept_existing,
        'purge_details': purge_details[:50]  # First 50 for readability
    }

    report_path = inp.parent / (inp.stem + '.purge_analysis.json')
    with open(report_path, 'w') as fh:
        json.dump(report, fh, indent=2)

    print(f"Purge analysis saved to: {report_path}")


if __name__ == '__main__':
    main()