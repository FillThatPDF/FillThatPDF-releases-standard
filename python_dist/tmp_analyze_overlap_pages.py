#!/usr/bin/env python3
from smart_fillable_v21 import UniversalPDFFillable
import pdfplumber
from collections import defaultdict

PDF='test_57643_v21_ROUND6_FINAL.pdf'
PAGES = [16,17,18]  # 0-indexed pages 17-19

def format_cb(cb):
    return f"size={cb['width']:.1f}x{cb['height']:.1f} at ({cb['x']:.1f},{cb['y']:.1f}) src={cb.get('source')}"

if __name__ == '__main__':
    d = UniversalPDFFillable(PDF, settings={'disable_checkboxes_in_table_cells': True})
    pl = pdfplumber.open(PDF)
    d.pdf = pl
    print('Preloading structural data...')
    d._preload_structural_data()
    print('Detecting checkboxes...')
    d._detect_all_checkboxes()
    print('Detecting tables...')
    d._detect_table_cells()

    for page in PAGES:
        page_cbs = [cb for cb in d.checkboxes if cb['page']==page]
        page_cells = [c for c in d.table_cells if c['page']==page]
        print(f"\nPage {page+1}: checkboxes={len(page_cbs)}, table_cells={len(page_cells)}")

        overlaps = []
        for cb in page_cbs:
            cb_x0 = cb['x']; cb_y0 = cb['y']; cb_x1 = cb['x']+cb['width']; cb_y1 = cb['y']+cb['height']
            cb_area = cb['width']*cb['height']
            max_inter = 0
            max_cell = None
            for c in page_cells:
                ix0 = max(cb_x0, c['x0']); iy0 = max(cb_y0, c['y0']); ix1 = min(cb_x1, c['x1']); iy1 = min(cb_y1, c['y1'])
                if ix1 <= ix0 or iy1 <= iy0: continue
                inter = (ix1-ix0)*(iy1-iy0)
                if inter > max_inter:
                    max_inter = inter; max_cell = c
            if max_inter > 0:
                overlaps.append((cb, max_cell, max_inter/cb_area))
        print(f"Checkboxes overlapping any cell: {len(overlaps)}")
        # Show some samples
        for i,(cb,cell,ratio) in enumerate(overlaps[:30], start=1):
            print(f" {i}: {format_cb(cb)} overlap_ratio_of_checkbox={ratio:.3f} cell_size={cell['x1']-cell['x0']:.1f}x{cell['y1']-cell['y0']:.1f}")

        non_overlapping = [cb for cb in page_cbs if not any(cb['x'] < c['x1'] and cb['x']+cb['width'] > c['x0'] and cb['y'] < c['y1'] and cb['y']+cb['height'] > c['y0'] for c in page_cells)]
        print(f"Checkboxes NOT overlapping any detected cell: {len(non_overlapping)} (sample up to 10)")
        for cb in non_overlapping[:10]:
            print('  -', format_cb(cb))

    pl.close()
