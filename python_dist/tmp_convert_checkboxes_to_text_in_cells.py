#!/usr/bin/env python3
"""Convert existing checkbox widgets that fall inside table cells to text fields
for a given page range. Creates a new PDF with changes and a QA JSON report.

Usage:
  python3 tmp_convert_checkboxes_to_text_in_cells.py --pdf INPUT.pdf --pages 17-19 --confirm
"""
import argparse, os, json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from smart_fillable_v21 import UniversalPDFFillable
import pdfplumber


def parse_pages(s: str):
    pages = set()
    for part in s.split(','):
        part = part.strip()
        if not part: continue
        if '-' in part:
            a, b = [int(x) for x in part.split('-', 1)]
            pages.update(range(a, b+1))
        else:
            pages.add(int(part))
    return sorted([p-1 for p in pages if p >= 1])


def main():
    parser = argparse.ArgumentParser(description='Convert checkbox widgets inside table cells to text fields')
    parser.add_argument('--pdf', required=True)
    parser.add_argument('--pages', required=True)
    parser.add_argument('--size-threshold', type=float, default=12.0, help='Keep checkboxes with size <= threshold (pt)')
    parser.add_argument('--out', help='Output PDF path (default: <input>_tables_as_text.pdf)')
    parser.add_argument('--confirm', action='store_true')
    args = parser.parse_args()

    inp = Path(args.pdf)
    if not inp.exists():
        print('ERROR: input not found:', inp); return
    pages = parse_pages(args.pages)
    if not pages:
        print('ERROR: invalid pages'); return

    out = Path(args.out) if args.out else inp.with_name(f"{inp.stem}_tables_as_text{inp.suffix}")

    # Load detector
    sf = UniversalPDFFillable(str(inp), settings={'disable_checkboxes_in_table_cells': True})
    pl = pdfplumber.open(str(inp))
    sf.pdf = pl

    print('Preloading structural data...')
    sf._preload_structural_data()
    print('Detecting table cells...')
    sf._detect_table_cells()

    # Ensure existing checkboxes are loaded
    # UniversalPDFFillable loads existing_checkboxes in __init__, but validate
    if not getattr(sf, 'existing_checkboxes', None):
        sf._detect_existing_form_fields()

    to_remove = []
    conversions = []

    for cb in sf.existing_checkboxes:
        p = cb['page']
        if p not in pages: continue
        cx = cb['x'] + cb['width'] / 2
        cy = cb['y'] + cb['height'] / 2
        w = cb['width']
        h = cb['height']
        # Keep very small independent checkboxes: if both dims <= threshold and checkbox NOT inside a table cell
        inside_cell = None
        for cell in sf.table_cells:
            if cell.get('page') != p: continue
            if cell['x0'] <= cx <= cell['x1'] and cell['y0'] <= cy <= cell['y1']:
                inside_cell = cell
                break
        if inside_cell:
            # Flag for conversion to text field (fill the cell)
            conversions.append({'page': p, 'cb': cb, 'cell': inside_cell})
        else:
            # Not in a cell; keep if small
            if max(w, h) <= args.size_threshold:
                # Keep as checkbox
                continue
            else:
                # Large checkbox outside cell (unlikely) - do not touch
                continue

    print(f'Found {len(conversions)} checkbox widgets inside table cells to convert')

    # Dry-run report
    report = {
        'input_pdf': str(inp),
        'pages_targeted': [p+1 for p in pages],
        'conversions_count': len(conversions),
        'conversions_sample': [
            {'page': c['page']+1, 'cb_center': (round(c['cb']['x'] + c['cb']['width']/2,1), round(c['cb']['y'] + c['cb']['height']/2,1)), 'cell': {'x0': round(c['cell']['x0'],1),'y0': round(c['cell']['y0'],1),'x1': round(c['cell']['x1'],1),'y1': round(c['cell']['y1'],1)}}
            for c in conversions[:20]
        ]
    }

    out_json = out.with_suffix('.checkbox_to_text_report.json')
    with open(out_json, 'w') as fh:
        json.dump(report, fh, indent=2)

    print('Dry-run report written to', out_json)

    if not args.confirm:
        print('\nDry-run complete. Re-run with --confirm to apply conversions and write output PDF at', out)
        return

    # Apply conversions: add text_fields and prepare existing checkbox removal list
    for c in conversions:
        cell = c['cell']
        name = sf._generate_unique_name(f"Cell_{int(cell['x0'])}_{int(cell['y0'])}", cell['page'])
        sf.text_fields.append({
            'page': cell['page'],
            'x0': cell['x0'] + 2, 'y0': cell['y0'] + 2, 'x1': cell['x1'] - 2, 'y1': cell['y1'] - 2,
            'name': name,
            'source': 'converted_checkbox_cell'
        })
        to_remove.append(c['cb'])

    sf._existing_checkboxes_to_remove = to_remove

    # Purge flagged checkbox widgets -> writes intermediate PDF
    print('Purging flagged widgets and writing intermediate PDF...')
    purged_pdf = sf._purge_existing_checkbox_annotations()

    # Now set input_pdf to the purged PDF and create final fillable PDF from our text_fields
    sf.input_pdf = Path(purged_pdf)
    sf.output_pdf = out

    print('Creating final fillable PDF with converted text fields...')
    sf._create_fillable_pdf()

    # QA report: run detection on output to confirm no checkboxes in target table cells
    print('Running QA detection on final PDF...')
    qa = UniversalPDFFillable(str(out), settings={'disable_checkboxes_in_table_cells': True})
    pl2 = pdfplumber.open(str(out))
    qa.pdf = pl2
    qa._preload_structural_data()
    qa._detect_all_checkboxes()
    qa._detect_table_cells()

    remaining_overlaps = []
    for cb in qa.checkboxes:
        p = cb['page']
        if p not in pages: continue
        cx = cb['x'] + cb['width']/2
        cy = cb['y'] + cb['height']/2
        for cell in qa.table_cells:
            if cell.get('page') != p: continue
            if cell['x0'] <= cx <= cell['x1'] and cell['y0'] <= cy <= cell['y1']:
                remaining_overlaps.append({'page': p, 'cb': cb, 'cell': cell})
                break

    final_report = {
        'output_pdf': str(out),
        'conversions_requested': len(conversions),
        'remaining_checkboxes_inside_table_cells': len(remaining_overlaps),
        'samples_remaining': remaining_overlaps[:20]
    }

    out_report = out.with_suffix('.checkbox_to_text_final_report.json')
    with open(out_report, 'w') as fh:
        json.dump(final_report, fh, indent=2)

    print('Final QA report saved to', out_report)
    print('Done')

if __name__ == '__main__':
    main()
