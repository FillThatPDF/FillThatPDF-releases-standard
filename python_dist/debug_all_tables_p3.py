#!/usr/bin/env python3
"""Check ALL tables on page 3"""
import pdfplumber

pdf_path = '/Users/36981/Desktop/PDFTest/PDFs to test/Batch 1 Forms/53252_DTE_EEA_Field_Inspection_Report_v11.pdf'
with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[2]  # Page 3
    page_height = page.height
    
    tables = page.find_tables()
    print(f"Found {len(tables)} tables on page 3")
    
    for i, table in enumerate(tables):
        print(f"\n=== Table {i}: bbox={table.bbox} ===")
        bbox = table.bbox
        if not bbox:
            continue
        
        table_x0, table_y0, table_x1, table_y1 = bbox
        table_pdf_y0 = page_height - table_y1
        table_pdf_y1 = page_height - table_y0
        print(f"  Table X: {table_x0:.1f} to {table_x1:.1f}")
        print(f"  Table PDF Y: {table_pdf_y0:.1f} to {table_pdf_y1:.1f}")
        
        rows = table.rows
        if rows and rows[0]:
            header_row = rows[0]
            if hasattr(header_row, 'cells') and header_row.cells:
                print("  Header cells:")
                checkbox_columns = []
                for cell in header_row.cells:
                    if cell:
                        cx0, cy0, cx1, cy1 = cell
                        try:
                            crop = page.within_bbox((cx0+1, cy0+1, cx1-1, cy1-1))
                            text = (crop.extract_text() or "").strip().lower()
                            keywords = ['pass', 'fail', 'yes', 'no', 'n/a', 'na', 'check', 'y', 'n']
                            is_cb = text in keywords
                            print(f"    Cell x=({cx0:.1f}-{cx1:.1f}): '{text}' {'✓ CB COLUMN' if is_cb else ''}")
                            if is_cb:
                                checkbox_columns.append((cx0, cx1))
                        except:
                            pass
                print(f"  Checkbox column ranges: {checkbox_columns}")
        
        # Check what checkboxes would be in this table
        print(f"\n  Checkboxes in table (by rect detection):")
        rects = page.rects
        cbs_in_table = []
        for rect in rects:
            w = rect['x1'] - rect['x0']
            h = rect['bottom'] - rect['top']
            if 4 <= w <= 15 and 4 <= h <= 15 and abs(w - h) <= 2:
                x = rect['x0']
                y_top = rect['top']
                pdf_y = page_height - rect['bottom']
                
                if table_x0 <= x <= table_x1:
                    if table_pdf_y0 <= pdf_y <= table_pdf_y1:
                        cbs_in_table.append({'x': x, 'y': pdf_y, 'y_top': y_top})
        
        print(f"    Found {len(cbs_in_table)} checkbox rects in table")
        
        # Also check what's detected ABOVE the table
        above_cbs = []
        for rect in rects:
            w = rect['x1'] - rect['x0']
            h = rect['bottom'] - rect['top']
            if 4 <= w <= 15 and 4 <= h <= 15 and abs(w - h) <= 2:
                x = rect['x0']
                y_top = rect['top']
                pdf_y = page_height - rect['bottom']
                
                if table_x0 <= x <= table_x1:
                    if pdf_y > table_pdf_y1:  # ABOVE table
                        above_cbs.append({'x': x, 'y': pdf_y, 'y_top': y_top})
        
        if above_cbs:
            print(f"\n  Checkboxes ABOVE table ({len(above_cbs)}):")
            for cb in above_cbs:
                print(f"    x={cb['x']:.1f}, y_top={cb['y_top']:.1f}")
