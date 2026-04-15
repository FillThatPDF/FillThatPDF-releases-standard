#!/usr/bin/env python3
"""Create quick PNG overlays showing detected checkbox centers for specified pages.
This is lightweight: uses pdfplumber to render pages at low DPI and draws boxes
for checkboxes taken from UniversalPDFFillable detection (no heavy text detection).
"""
import argparse, os
from smart_fillable_v21 import UniversalPDFFillable
import pdfplumber
from PIL import Image, ImageDraw

parser = argparse.ArgumentParser(description='Quick overlays for checkbox positions')
parser.add_argument('--pdf', required=True)
parser.add_argument('--pages', required=True, help='1-indexed pages like 17-19 or 17,18')
parser.add_argument('--out', default='overlays/test_57643_quick')
parser.add_argument('--dpi', type=int, default=72)
args = parser.parse_args()

PDF = args.pdf
OUT_DIR = args.out
os.makedirs(OUT_DIR, exist_ok=True)

# Parse pages
def parse_pages(s):
    parts = s.split(',')
    pages = set()
    for p in parts:
        if '-' in p:
            a,b = p.split('-',1)
            pages.update(range(int(a), int(b)+1))
        else:
            pages.add(int(p))
    return sorted([p-1 for p in pages])

pages = parse_pages(args.pages)

print('Opening PDF:', PDF)
pl = pdfplumber.open(PDF)

# Use detector to get checkbox positions
det = UniversalPDFFillable(PDF, settings={'disable_checkboxes_in_table_cells': False})
det.pdf = pl
print('Preloading structural data...')
det._preload_structural_data()
print('Running lightweight detection for positions...')
# We'll reuse _build_visual_grid and _cell_has_visual_checkbox logic to re-derive positions on requested pages
all_boxes = {p: [] for p in pages}
for p in pages:
    if p < 0 or p >= det.page_count: continue
    cells = det._build_visual_grid(p)
    for c in cells:
        x0,y0,x1,y1 = c['x0'], c['y0'], c['x1'], c['y1']
        w,h = x1-x0, y1-y0
        if not (6 <= w <= 60 and 6 <= h <= 60):
            continue
        # check for visual cue
        if det._cell_has_visual_checkbox(p, x0, y0, x1, y1):
            size = min(w,h) * 0.85
            cb_x = x0 + (w-size)/2
            cb_y = y0 + (h-size)/2
            all_boxes[p].append({'x': cb_x, 'y': cb_y, 'w': size, 'h': size})

for p in pages:
    page = pl.pages[p]
    im = page.to_image(resolution=args.dpi)
    pil = im.original
    draw = ImageDraw.Draw(pil)

    for cb in all_boxes.get(p, []):
        # Transform coordinates: pdfplumber image uses top-left origin with scaling
        # pdfplumber's to_image already maps coordinates; the cell coords are in visual points
        # We need to scale by (dpi / 72)
        scale = args.dpi / 72.0
        x = cb['x'] * scale
        y = cb['y'] * scale
        w = cb['w'] * scale
        h = cb['h'] * scale
        # Draw outer rect and center small rect
        draw.rectangle([x, y, x+w, y+h], outline='red', width=2)
        draw.rectangle([x+2, y+2, x+w-2, y+h-2], outline='yellow', width=1)

    out_fn = os.path.join(OUT_DIR, f'overlay_page_{p+1:02d}.png')
    pil.save(out_fn)
    print('Wrote', out_fn)

pl.close()
print('Done')