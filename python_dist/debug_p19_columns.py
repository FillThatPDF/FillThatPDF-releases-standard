#!/usr/bin/env python3
"""Debug column analysis for Page 19"""
import pdfplumber

pdf_path = '/Users/36981/Desktop/PDFTest/PDFs to test/Batch 1 Forms/56380_DTE_2026_PartnerGuidelines_v18.pdf'

with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[18]  # Page 19 (0-indexed)
    tables = page.find_tables()
    
    print(f"Page 19: Found {len(tables)} tables")
    
    for ti, table in enumerate(tables):
        cells = table.cells
        if not cells:
            continue
        
        print(f"\n=== Table {ti}: {len(cells)} cells ===")
        
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
        
        # Analyze each column
        for col_x, col_cells in sorted(columns.items()):
            if len(col_cells) < 2:
                continue
            
            col_x0 = min(c[0] for c in col_cells)
            col_x1 = max(c[2] for c in col_cells)
            
            print(f"\n  Column x={col_x:.0f} ({col_x0:.0f}-{col_x1:.0f}): {len(col_cells)} cells")
            
            # Analyze cells in this column
            cells_with_dense_text = 0
            cells_with_fill_indicators = 0
            total_checked = 0
            
            for cell in col_cells[:6]:  # Sample first 6
                cx0, cy0, cx1, cy1 = cell
                cell_w = cx1 - cx0
                cell_h = cy1 - cy0
                
                if cell_w < 20 or cell_h < 10:
                    continue
                
                try:
                    cell_text = (page.within_bbox((cx0, cy0, cx1, cy1)).extract_text() or "").strip()
                    words = page.within_bbox((cx0, cy0, cx1, cy1)).extract_words()
                except:
                    continue
                
                total_checked += 1
                
                has_fill = False
                if '___' in cell_text or not cell_text.strip() or len(cell_text) < 3:
                    has_fill = True
                if ':' in cell_text and words:
                    for w in words:
                        if w['text'].endswith(':'):
                            space_after = cx1 - float(w['x1'])
                            if space_after > 30:
                                has_fill = True
                
                if has_fill:
                    cells_with_fill_indicators += 1
                    print(f"    Cell {cy0:.0f}: FILL '{cell_text[:40]}...'")
                    continue
                
                # Check density
                if words:
                    text_x0 = min(float(w['x0']) for w in words)
                    text_x1 = max(float(w['x1']) for w in words)
                    text_width = text_x1 - text_x0
                    fill_ratio = text_width / cell_w if cell_w > 0 else 0
                    word_count = len(cell_text.split())
                    
                    if fill_ratio > 0.50 and word_count >= 2:
                        cells_with_dense_text += 1
                        print(f"    Cell {cy0:.0f}: DENSE ({fill_ratio:.0%}) '{cell_text[:40]}...'")
                    else:
                        print(f"    Cell {cy0:.0f}: LOW ({fill_ratio:.0%}) '{cell_text[:40]}...'")
            
            if total_checked >= 2:
                dense_ratio = cells_with_dense_text / total_checked
                fill_ratio = cells_with_fill_indicators / total_checked
                is_dense = dense_ratio > 0.60 and fill_ratio < 0.30
                print(f"  -> RESULT: dense={dense_ratio:.0%}, fill={fill_ratio:.0%}, SKIP={is_dense}")
