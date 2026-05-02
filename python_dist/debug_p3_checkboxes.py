#!/usr/bin/env python3
"""Debug P3 checkboxes and image boxes"""
import pdfplumber

pdf_path = '/Users/36981/Desktop/PDFTest/PDFs to test/Batch 1 Forms/53252_DTE_EEA_Field_Inspection_Report_v11.pdf'
with pdfplumber.open(pdf_path) as pdf:
    # Check page 3 (index 2) - Quality Scores table
    page = pdf.pages[2]
    print('=== P3 Quality Scores Table ===')
    print(f'Page height: {page.height}')
    tables = page.find_tables()
    for i, t in enumerate(tables):
        print(f'\nTable {i}: bbox={t.bbox}')
        if t.rows:
            header = t.rows[0]
            if hasattr(header, 'cells') and header.cells:
                print('Header cells:')
                for cell in header.cells:
                    if cell:
                        try:
                            crop = page.within_bbox((cell[0]+1, cell[1]+1, cell[2]-1, cell[3]-1))
                            text = (crop.extract_text() or '').strip()
                            print(f'  Cell x=({cell[0]:.1f}-{cell[2]:.1f}): "{text}"')
                        except:
                            pass
    
    # Check page 6 (index 5) for image boxes
    print('\n=== P6 Water Heater - Rectangles ===')
    page6 = pdf.pages[5]
    print(f'Page 6 height: {page6.height}')
    
    # Find large rectangles
    rects = page6.rects
    print(f'Total rects on page 6: {len(rects)}')
    large_rects = [r for r in rects if (r['x1'] - r['x0']) > 80 and (r['bottom'] - r['top']) > 80]
    print(f'Large rects (>80x80): {len(large_rects)}')
    for r in large_rects[:5]:
        w = r['x1'] - r['x0']
        h = r['bottom'] - r['top']
        print(f"  Rect: {w:.1f}x{h:.1f} at ({r['x0']:.1f}, {r['top']:.1f}) to ({r['x1']:.1f}, {r['bottom']:.1f})")
    
    # Check page 2 (Summary page) for table structure
    print('\n=== P2 Summary - Table Structure ===')
    page2 = pdf.pages[1]
    print(f'Page 2 height: {page2.height}')
    tables2 = page2.find_tables()
    for i, t in enumerate(tables2):
        print(f'\nTable {i}: bbox={t.bbox}')
        if t.rows and len(t.rows) > 1:
            for row_idx, row in enumerate(t.rows[:3]):  # First 3 rows
                if hasattr(row, 'cells') and row.cells:
                    for cell in row.cells:
                        if cell:
                            w = cell[2] - cell[0]
                            h = cell[3] - cell[1]
                            print(f'  Row {row_idx} cell: {w:.1f}x{h:.1f} at x=({cell[0]:.1f}-{cell[2]:.1f}), y=({cell[1]:.1f}-{cell[3]:.1f})')
