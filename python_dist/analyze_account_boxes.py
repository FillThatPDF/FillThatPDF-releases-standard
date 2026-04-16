#!/usr/bin/env python3
"""Analyze account number boxes in 57643 PDF."""

import pdfplumber

pdf_path = '/Users/36981/Desktop/PDFTest/PDFs to test/Batch 1 Forms/57643_DTE_MF_Measures_and_Specs_Income_Qualified_Catalog_v2.pdf'

with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[6]  # Page 7 (0-indexed)
    
    # Find ALL lines that could form boxes
    lines = page.lines
    
    # Look for vertical lines around y=479-497 (account number boxes)
    vlines = [l for l in lines if abs(l['x0'] - l['x1']) < 1 and 470 < l['top'] < 510]
    
    print(f'Found {len(vlines)} vertical lines in account number area')
    
    # Get unique x positions
    x_positions = sorted(set(round(l['x0'], 1) for l in vlines))
    print(f'X positions ({len(x_positions)}): {x_positions}')
    
    if len(x_positions) >= 2:
        # Calculate box widths
        widths = [x_positions[i+1] - x_positions[i] for i in range(len(x_positions)-1)]
        print(f'Box widths: {[round(w, 1) for w in widths]}')
        
        # Identify gaps (likely separating the two account number groups)
        for i, w in enumerate(widths):
            if w > 30:
                print(f'  Gap at index {i}: {w:.1f}pt (between x={x_positions[i]:.1f} and x={x_positions[i+1]:.1f})')
    
    print()
    print('=== Looking at rects as well ===')
    rects = page.rects
    # Filter for small rects near y=479
    box_rects = [r for r in rects if 470 < r['top'] < 510 and r['x1'] - r['x0'] < 30]
    print(f'Small rects near account area: {len(box_rects)}')
    for r in sorted(box_rects, key=lambda x: x['x0'])[:24]:
        w = r['x1'] - r['x0']
        h = r['bottom'] - r['top']
        print(f'  x={r["x0"]:.1f}, w={w:.1f}, h={h:.1f}')
