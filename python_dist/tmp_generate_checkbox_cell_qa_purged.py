#!/usr/bin/env python3
import os, json
from smart_fillable_v21 import UniversalPDFFillable
import pdfplumber

PDF_PATH = 'test_57643_v21_ROUND6_FINAL_purged.pdf'
OUT_DIR = 'overlays/test_57643_QA_purged'
os.makedirs(OUT_DIR, exist_ok=True)

print('Loading PDF:', PDF_PATH)
d = UniversalPDFFillable(PDF_PATH, settings={'disable_checkboxes_in_table_cells': True})
pl = pdfplumber.open(PDF_PATH)
d.pdf = pl

print('Preloading structural data...')
d._preload_structural_data()
print('Detecting checkboxes...')
d._detect_all_checkboxes()
print('Detecting tables...')
d._detect_table_cells()

converted_cells = [f for f in d.text_fields if f.get('source') == 'removed_checkbox_cell']
converted_count = len(converted_cells)

remaining_overlaps = []
for cb in d.checkboxes:
    cb_cx = cb['x'] + cb['width']/2
    cb_cy = cb['y'] + cb['height']/2
    for cell in d.table_cells:
        if cell['page'] != cb['page']: continue
        if cell['x0'] <= cb_cx <= cell['x1'] and cell['y0'] <= cb_cy <= cell['y1']:
            remaining_overlaps.append({'page': cb['page'], 'cb': cb, 'cell': cell})
            break

per_page_report = {}
for item in remaining_overlaps:
    p = item['page']
    per_page_report[p] = per_page_report.get(p, 0) + 1

report = {
    'pdf': PDF_PATH,
    'total_existing_checkboxes': len(d.existing_checkboxes) or 0,
    'detected_checkboxes': len(d.checkboxes),
    'converted_checkboxes_into_textfields': converted_count,
    'remaining_checkboxes_inside_table_cells': len(remaining_overlaps),
    'per_page_remaining_overlaps': {str(p+1): n for p,n in sorted(per_page_report.items())},
    'total_table_cells': len(d.table_cells)
}
with open(os.path.join(OUT_DIR, 'checkbox_cell_report.json'), 'w') as fh:
    json.dump(report, fh, indent=2)

print('Report saved to', os.path.join(OUT_DIR, 'checkbox_cell_report.json'))
print('Summary:', json.dumps(report, indent=2))
pl.close()