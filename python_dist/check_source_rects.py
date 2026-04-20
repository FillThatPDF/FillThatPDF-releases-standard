#!/usr/bin/env python3
"""Debug - check what rectangles exist on pages 4-8 in the source PDF"""
import pdfplumber

pdf = pdfplumber.open('/Users/36981/Desktop/PDFTest/PDFs to test/Batch 1 Forms/53252_DTE_EEA_Field_Inspection_Report_v11.pdf')

# Check pages 4-8 (indices 3-7) for large rectangles
for pg_idx in [3, 4, 5, 6, 7]:
    page = pdf.pages[pg_idx]
    page_height = float(page.height)
    
    # Get rectangles
    rects = page.rects or []
    
    print(f"\nPage {pg_idx+1}: {len(rects)} total rectangles")
    
    # Find large rectangles (>80x80pt)
    large_rects = []
    for rect in rects:
        w = float(rect['width'])
        h = float(rect['height'])
        if w > 80 and h > 80:
            large_rects.append(rect)
    
    print(f"  {len(large_rects)} large rectangles (>80x80pt)")
    for rect in large_rects[:5]:
        x0 = rect['x0']
        y_top = rect['top']
        w = rect['width']
        h = rect['height']
        print(f"    - {w:.0f}x{h:.0f}pt at ({x0:.0f}, {y_top:.0f})")
    
    # Also check for tables with large cells
    tables = page.find_tables()
    print(f"  {len(tables)} tables found")
    for tidx, table in enumerate(tables):
        # Check for large cells
        for cell in (table.cells or []):
            if cell:
                cx0, cy0, cx1, cy1 = cell
                cw = cx1 - cx0
                ch = cy1 - cy0
                if cw > 100 and ch > 80:
                    print(f"    Table {tidx}: Large cell {cw:.0f}x{ch:.0f}pt at ({cx0:.0f}, {cy0:.0f})")

pdf.close()
