#!/usr/bin/env python3
"""Debug column analysis for Page 20"""
import pdfplumber

pdf_path = '/Users/36981/Desktop/PDFTest/PDFs to test/Batch 1 Forms/56380_DTE_2026_PartnerGuidelines_v18.pdf'

with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[19]  # Page 20 (0-indexed)
    tables = page.find_tables()
    
    print(f"Page 20: Found {len(tables)} tables")
    
    for ti, table in enumerate(tables):
        cells = table.cells
        if not cells:
            continue
        
        print(f"\n=== Table {ti}: {len(cells)} cells ===")
        bbox = table.bbox
        print(f"  Table bbox: {bbox}")
        
        # Group cells by column (X position)
        col_tolerance = 10
        columns = {}
        
        for cell in cells:
            cx0, cy0, cx1, cy1 = cell
            col_key = None
            for existing_x in columns.keys():
                if abs(cx0 - existing_x) < col_tolerance:
                    col_key = existing_x
                    break
            if col_key is None:
                col_key = cx0
            if col_key not in columns:
                columns[col_key] = []
            columns[col_key].append(cell)
        
        print(f"  Found {len(columns)} columns at x positions: {sorted(columns.keys())}")
        
        # Analyze columns that might be "Quality Standards"
        for col_x, col_cells in sorted(columns.items()):
            if len(col_cells) < 2:
                continue
            
            col_x0 = min(c[0] for c in col_cells)
            col_x1 = max(c[2] for c in col_cells)
            
            print(f"\n  Column x={col_x:.0f} ({col_x0:.0f}-{col_x1:.0f}, width={col_x1-col_x0:.0f}): {len(col_cells)} cells")
            
            # Sample cells
            for cell in col_cells[:4]:
                cx0, cy0, cx1, cy1 = cell
                try:
                    cell_text = (page.within_bbox((cx0, cy0, cx1, cy1)).extract_text() or "").strip()
                    print(f"    Cell y={cy0:.0f}: '{cell_text[:60]}...'")
                except:
                    pass
