import pdfplumber

pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/New Test Forms/11691_ConEd_Distributor_Application_Form_v10_FILLABLE.pdf"

with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[0]
    rects = page.rects
    print(f"--- Page 1 Rects (y=600 to 750) ---")
    relevant = [r for r in rects if 600 < r['top'] < 750]
    relevant.sort(key=lambda r: r['top'])
    for r in relevant:
        print(f"Rect: y={r['top']:.1f} to {r['bottom']:.1f}, x={r['x0']:.1f} to {r['x1']:.1f} w={r['width']:.1f} h={r['height']:.1f} fill={r.get('fill')} stroke={r.get('stroke')}")
