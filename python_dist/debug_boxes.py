#!/usr/bin/env python3
"""Debug the box grouping logic - full simulation of code."""
import pdfplumber
from collections import defaultdict

pdf = pdfplumber.open('/Users/36981/Desktop/PDFTest/PDFs to test/Batch 1 Forms/57643_DTE_MF_Measures_and_Specs_Income_Qualified_Catalog_v2.pdf')
page = pdf.pages[6]  # Page 7

# Extract from ALL sources like the code does
source_lines = list(page.lines)

# Add thin rects
for r in page.rects:
    if abs(r['x0'] - r['x1']) < 3:
        source_lines.append(r)

# Add edges
edges = getattr(page, 'edges', [])
for e in edges:
    if e.get('orientation') == 'v':
        source_lines.append(e)

print(f'Total source lines: {len(source_lines)} (lines={len(page.lines)}, rects={len(page.rects)}, edges={len(edges)})')

# Extract vertical lines
v_lines = []
for l in source_lines:
    x0, x1 = l['x0'], l['x1']
    y0, y1 = l['top'], l['bottom']
    
    if abs(x0 - x1) < 2:
        length = abs(y1 - y0)
        if 10 < length < 35:
            v_lines.append({
                'x': (x0 + x1) / 2,
                'y0': min(y0, y1),
                'y1': max(y0, y1),
                'length': length
            })

print(f'v_lines before dedup: {len(v_lines)}')

# Show lines around x=36 before dedup
print('\nLines around x=36 BEFORE dedup:')
for v in v_lines:
    if 34 < v['x'] < 58:
        print(f"  x={v['x']:.1f}, y0={v['y0']:.1f}, y1={v['y1']:.1f}, len={v['length']:.1f}")

# Deduplicate
v_lines.sort(key=lambda l: (l['x'], l['y0']))
dedup_v = []
if v_lines:
    curr = v_lines[0]
    for nex in v_lines[1:]:
        if abs(nex['x'] - curr['x']) < 1.0 and nex['y0'] < curr['y1'] + 2:
            # MERGED!
            if 34 < curr['x'] < 58:
                print(f"MERGING x={curr['x']:.1f} (y0={curr['y0']:.1f},y1={curr['y1']:.1f}) with x={nex['x']:.1f} (y0={nex['y0']:.1f},y1={nex['y1']:.1f})")
            curr['y1'] = max(curr['y1'], nex['y1'])
        else:
            dedup_v.append(curr)
            curr = nex
    dedup_v.append(curr)
v_lines = dedup_v

print(f'\nv_lines after dedup: {len(v_lines)}')

# Show lines around x=36 after dedup
print('\nLines around x=36 AFTER dedup:')
for v in v_lines:
    if 34 < v['x'] < 58:
        print(f"  x={v['x']:.1f}, y0={v['y0']:.1f}, y1={v['y1']:.1f}, len={v['length']:.1f}")
