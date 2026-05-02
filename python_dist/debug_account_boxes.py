import pdfplumber
pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/57643_DTE_MF_Measures_and_Specs_Income_Qualified_Catalog_v2.pdf"
with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[6]  # Page 7
    rects = page.rects
    print(f"Page 7 Rects ({len(rects)}):")
    for r in rects:
        # Print all small rects, not just those meeting a strict 25pt limit
        w = r['width']
        h = r['height']
        if w < 100 and h < 50:
             print(f"  Rect: {r['x0']:.1f}, {r['top']:.1f}, {r['x1']:.1f}, {r['bottom']:.1f} (size {w:.1f}x{h:.1f})")

    # Also check lines
    lines = page.lines
    print(f"\nPage 7 Lines ({len(lines)}):")
    for l in lines:
        if l['width'] < 5: # Vertical
             print(f"  V-Line: x={l['x0']:.1f}, y0={l['top']:.1f}, y1={l['bottom']:.0f} (h={l['height']:.1f})")
