#!/usr/bin/env python3
"""Debug P3 - find ALL checkboxes including outside table"""
import pdfplumber

pdf_path = '/Users/36981/Desktop/PDFTest/PDFs to test/Batch 1 Forms/53252_DTE_EEA_Field_Inspection_Report_v11.pdf'
with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[2]  # Page 3 (0-indexed)
    page_height = page.height
    
    tables = page.find_tables()
    table_bbox = tables[0].bbox if tables else None
    print(f"Table bbox: {table_bbox}")
    
    # Find ALL checkbox-like rects on page
    rects = page.rects
    all_checkboxes = []
    for rect in rects:
        w = rect['x1'] - rect['x0']
        h = rect['bottom'] - rect['top']
        if 4 <= w <= 15 and 4 <= h <= 15 and abs(w - h) <= 2:
            all_checkboxes.append({
                'x': rect['x0'],
                'top': rect['top'],
                'bottom': rect['bottom'],
                'w': w,
                'h': h
            })
    
    print(f"\nAll checkbox-like rects on page 3: {len(all_checkboxes)}")
    
    # Group by x position
    by_x = {}
    for cb in all_checkboxes:
        x_key = round(cb['x'] / 5) * 5
        by_x.setdefault(x_key, []).append(cb)
    
    print("\nGrouped by X position:")
    for x_key in sorted(by_x.keys()):
        cbs = by_x[x_key]
        in_table = "in table" if table_bbox and table_bbox[0] <= x_key <= table_bbox[2] else "OUTSIDE table"
        print(f"  x~{x_key}: {len(cbs)} checkboxes ({in_table})")
        if len(cbs) <= 3:
            for cb in cbs:
                print(f"    at ({cb['x']:.1f}, {cb['top']:.1f})")
    
    # Also check for character-based checkboxes (☐, ☑, etc.)
    print("\n\nSearching for checkbox characters...")
    chars = page.chars
    checkbox_chars = []
    for char in chars:
        t = char.get('text', '')
        # Unicode ballot box characters
        if t in ['☐', '☑', '☒', '□', '■', '▢', '▣', '⬜', '⬛']:
            checkbox_chars.append(char)
    
    print(f"Found {len(checkbox_chars)} checkbox characters")
    if checkbox_chars:
        for cc in checkbox_chars[:10]:
            print(f"  '{cc['text']}' at x={cc['x0']:.1f}, y={cc['top']:.1f}")
