import pdfplumber

pdf_path = "../46119_RECO_HVAC_Incentive_Claim_Form_v02.pdf"

with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[2] # Page 3 (Index 2)
    print(f"Page 3 Size: {page.width}x{page.height}")
    
    lines = page.lines
    print(f"Found {len(lines)} lines")
    for l in lines:
        print(f"Line: {l['x0']:.1f},{l['top']:.1f} -> {l['x1']:.1f},{l['bottom']:.1f}")
        
    rects = page.rects
    print(f"Found {len(rects)} rects")
    for r in rects:
        print(f"Rect: {r['x0']:.1f},{r['top']:.1f} -> {r['x1']:.1f},{r['bottom']:.1f}")
        
    tables = page.extract_tables()
    print(f"Found {len(tables)} tables")
    for i, table in enumerate(tables):
        print(f"Table {i}: {len(table)} rows")
        for row in table:
            print(f"  Row: {row}")
