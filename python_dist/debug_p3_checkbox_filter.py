#!/usr/bin/env python3
"""Debug P3 checkbox filtering"""
import pdfplumber

pdf_path = '/Users/36981/Desktop/PDFTest/PDFs to test/Batch 1 Forms/53252_DTE_EEA_Field_Inspection_Report_v11.pdf'
with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[2]  # Page 3 (0-indexed)
    page_height = page.height
    print(f"Page 3 height: {page_height}")
    
    tables = page.find_tables()
    for t_idx, table in enumerate(tables):
        bbox = table.bbox
        if not bbox:
            continue
        
        print(f"\n=== Table {t_idx}: bbox={bbox} ===")
        table_x0, table_y0, table_x1, table_y1 = bbox
        table_pdf_y0 = page_height - table_y1
        table_pdf_y1 = page_height - table_y0
        print(f"Table PDF Y range: {table_pdf_y0:.1f} - {table_pdf_y1:.1f}")
        
        rows = table.rows
        if not rows:
            continue
        
        header_row = rows[0]
        if not hasattr(header_row, 'cells') or not header_row.cells:
            continue
        
        # Find checkbox columns
        checkbox_column_ranges = []
        for cell in header_row.cells:
            if not cell:
                continue
            cx0, cy0, cx1, cy1 = cell
            try:
                crop = page.within_bbox((cx0+1, cy0+1, cx1-1, cy1-1))
                text = (crop.extract_text() or "").strip().lower()
                
                if any(kw in text for kw in ['pass', 'fail', 'yes', 'no', 'n/a', 'na', 'check']):
                    checkbox_column_ranges.append((cx0, cx1))
                    print(f"  WOULD BE Checkbox column (old logic): '{text}' at x=({cx0:.1f}-{cx1:.1f})")
                    
                # v22.89: NEW exact match logic  
                checkbox_keywords = ['pass', 'fail', 'yes', 'no', 'n/a', 'na', 'check', 'y', 'n']
                if text in checkbox_keywords:
                    print(f"  ✓ IS Checkbox column (new logic): '{text}' at x=({cx0:.1f}-{cx1:.1f})")
            except:
                pass
        
        print(f"\nCheckbox column X ranges: {checkbox_column_ranges}")
        
        # Now check the small rects that would be detected as checkboxes
        rects = page.rects
        checkbox_candidates = []
        for rect in rects:
            w = rect['x1'] - rect['x0']
            h = rect['bottom'] - rect['top']
            if 4 <= w <= 15 and 4 <= h <= 15 and abs(w - h) <= 2:
                # This is a checkbox candidate
                x = rect['x0']
                # Convert y to PDF coords
                pdf_y = page_height - rect['bottom']
                
                # Check if inside table
                if table_x0 <= x <= table_x1 and table_pdf_y0 <= pdf_y <= table_pdf_y1:
                    checkbox_candidates.append({
                        'x': x,
                        'y': pdf_y,
                        'w': w,
                        'h': h
                    })
        
        print(f"\nFound {len(checkbox_candidates)} checkbox candidates inside table")
        
        # Check which would be kept/removed
        kept = []
        removed = []
        for cb in checkbox_candidates:
            cb_x = cb['x']
            in_checkbox_column = False
            for col_x0, col_x1 in checkbox_column_ranges:
                if col_x0 - 5 <= cb_x <= col_x1 + 5:
                    in_checkbox_column = True
                    break
            
            if in_checkbox_column:
                kept.append(cb)
            else:
                removed.append(cb)
        
        print(f"\nWould KEEP: {len(kept)} checkboxes")
        for cb in kept[:5]:
            print(f"  x={cb['x']:.1f}, y={cb['y']:.1f}")
        
        print(f"\nWould REMOVE: {len(removed)} checkboxes")
        for cb in removed[:10]:
            print(f"  x={cb['x']:.1f}, y={cb['y']:.1f}")
            # Find which column this is in
            for cell in header_row.cells:
                if cell and cell[0] <= cb['x'] <= cell[2]:
                    try:
                        crop = page.within_bbox((cell[0]+1, cell[1]+1, cell[2]-1, cell[3]-1))
                        text = (crop.extract_text() or "").strip()
                        print(f"    -> In column: '{text}'")
                    except:
                        pass
                    break
