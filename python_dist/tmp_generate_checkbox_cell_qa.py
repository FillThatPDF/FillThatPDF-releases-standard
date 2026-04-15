#!/usr/bin/env python3
import os
import json
from collections import Counter
from smart_fillable_v21 import UniversalPDFFillable
import pdfplumber
from PIL import Image, ImageDraw, ImageFont

PDF_PATH = 'test_57643_v21_ROUND6_FINAL.pdf'
OUT_DIR = 'overlays/test_57643_QA'
RESOLUTION = 150  # DPI for page render

os.makedirs(OUT_DIR, exist_ok=True)

print(f"Loading PDF: {PDF_PATH}")
d = UniversalPDFFillable(PDF_PATH, settings={'disable_checkboxes_in_table_cells': True})
# Use pdfplumber handle for rendering
pl = pdfplumber.open(PDF_PATH)
d.pdf = pl

print('Preloading structural data...')
d._preload_structural_data()
print('Detecting checkboxes...')
d._detect_all_checkboxes()
print('Detecting tables...')
d._detect_table_cells()

# Count conversions recorded in text_fields
converted_cells = [f for f in d.text_fields if f.get('source') == 'removed_checkbox_cell']
converted_count = len(converted_cells)

# Compute remaining checkboxes that lie in table cells
remaining_overlaps = []
for cb in d.checkboxes:
    cb_cx = cb['x'] + cb['width']/2
    cb_cy = cb['y'] + cb['height']/2
    for cell in d.table_cells:
        if cell['page'] != cb['page']: continue
        if cell['x0'] <= cb_cx <= cell['x1'] and cell['y0'] <= cb_cy <= cell['y1']:
            remaining_overlaps.append({'page': cb['page'], 'cb': cb, 'cell': cell})
            break

# Per-page counts
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

# Save JSON report
with open(os.path.join(OUT_DIR, 'checkbox_cell_report.json'), 'w') as fh:
    json.dump(report, fh, indent=2)

print('Generating overlay images...')
for page_num in range(d.page_count):
    plpage = pl.pages[page_num]
    imobj = plpage.to_image(resolution=RESOLUTION)
    pil = imobj.original.convert('RGBA')
    draw = ImageDraw.Draw(pil)
    w, h = pil.size
    scale = pil.width / plpage.width

    # Draw table cells (green, 2px)
    for cell in d.table_cells:
        if cell['page'] != page_num: continue
        x0 = cell['x0'] * scale
        y0 = (plpage.height - cell['y1']) * scale
        x1 = cell['x1'] * scale
        y1 = (plpage.height - cell['y0']) * scale
        draw.rectangle([x0, y0, x1, y1], outline=(0, 200, 0, 200), width=2)

    # Draw converted (blue)
    for f in converted_cells:
        if f['page'] != page_num: continue
        x0 = f['x0'] * scale
        y0 = (plpage.height - f['y1']) * scale
        x1 = f['x1'] * scale
        y1 = (plpage.height - f['y0']) * scale
        draw.rectangle([x0, y0, x1, y1], outline=(0, 120, 255, 200), width=3)
        # Fill a slight transparent background
        draw.rectangle([x0+2, y0+2, x1-2, y1-2], outline=None)

    # Draw remaining checkboxes (red)
    for cb in d.checkboxes:
        if cb['page'] != page_num: continue
        x0 = cb['x'] * scale
        y0 = (plpage.height - (cb['y'] + cb['height'])) * scale
        x1 = (cb['x'] + cb['width']) * scale
        y1 = (plpage.height - cb['y']) * scale
        draw.rectangle([x0, y0, x1, y1], outline=(255, 40, 40, 220), width=2)

    # Annotate counts on the page top-left
    text = f"Page {page_num+1}: table_cells={sum(1 for c in d.table_cells if c['page']==page_num)}\nconverted={sum(1 for c in converted_cells if c['page']==page_num)}\nremaining_cb_in_cells={per_page_report.get(page_num,0)}"
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 12)
    except Exception:
        font = ImageFont.load_default()
    draw.rectangle([5,5,260,60], fill=(255,255,255,200))
    draw.text((8,8), text, fill=(0,0,0), font=font)

    out_fn = os.path.join(OUT_DIR, f'overlay_page_{page_num+1:02d}.png')
    pil.save(out_fn)
    print('Saved', out_fn)

pl.close()
print('Report saved to', os.path.join(OUT_DIR, 'checkbox_cell_report.json'))
print('Done.')
