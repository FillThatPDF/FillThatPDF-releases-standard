import pdfplumber
import sys
from pathlib import Path

pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/Blank/55570_DTE_SEEL_Contractor_Onboarding _Packet_v18_FILLABLE.pdf"

with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[12] # P13
    
    print(f"Page 13 Analysis: {page.width}x{page.height}")
    
    # Target area: Y between 500 and 750 (Customer Info Table)
    y_min, y_max = 500, 800
    
    print("\n--- VERTICAL LINES / THIN RECTS (X between 30 and 580) ---")
    raw_objs = page.lines + page.rects
    for o in raw_objs:
        lx0 = float(o.get('x0', o.get('x', 0)))
        lx1 = float(o.get('x1', o.get('x', lx0) + o.get('width', 0)))
        ly0 = float(o.get('top', o.get('y0', 0)))
        ly1 = float(o.get('bottom', o.get('y1', 0)))
        
        w = abs(lx1 - lx0)
        h = abs(ly1 - ly0)
        
        # We care about vertical dividers
        if w < 5 and h > 10 and ly0 < y_max and ly1 > y_min:
            print(f"V-OBJ: x={lx0:.2f}-{lx1:.2f} y={ly0:.2f}-{ly1:.2f} h={h:.2f} type={o.get('object_type', 'unknown')}")

    print("\n--- TABLE CELLS ---")
    tables = page.find_tables()
    for i, t in enumerate(tables):
        if t.bbox[1] > y_min:
            print(f"Table {i+1} bbox: {t.bbox}")
            for r_idx, row in enumerate(t.rows):
                # row.cells is a list of [x0, y0, x1, y1] or similar objects? 
                # Actually t.cells is [ (x0, y0, x1, y1), ... ]
                pass
            
            # Print all cells in Table 3 (Customer Info)
            for c in t.cells:
                if c[1] > y_min:
                    # Extract text
                    txt = page.within_bbox(c).extract_text() or ""
                    print(f"Cell: {c} text='{txt.strip()}'")

    print("\n--- WORDS in target area ---")
    words = page.within_bbox((0, y_min, page.width, page.height)).extract_words()
    for w in words:
        print(f"Word: x={w['x0']:.2f}-{w['x1']:.2f} y={w['top']:.2f} text='{w['text']}'")

    print("\n--- ALL VERTICAL EDGES ---")
    edges = page.edges
    for e in edges:
        if abs(e['x0'] - e['x1']) < 2 and e['top'] < y_max and e['bottom'] > y_min:
            print(f"EDGE: x={e['x0']:.2f} y={e['top']:.2f}-{e['bottom']:.2f} h={e['height']:.2f} orientation={e.get('orientation', '?')}")
