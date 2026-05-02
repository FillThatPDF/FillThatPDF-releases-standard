import pdfplumber

pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/New Test Forms/11691_ConEd_Distributor_Application_Form_v10_FILLABLE.pdf"

with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[0]
    edges = page.edges
    print(f"--- Page 1 Edges (y=600 to 750) ---")
    relevant = [e for e in edges if 600 < e['top'] < 750]
    relevant.sort(key=lambda e: e['top'])
    for e in relevant:
        print(f"Edge: y={e['top']:.1f} to {e['bottom']:.1f}, x={e['x0']:.1f} to {e['x1']:.1f} w={e['width']:.1f} h={e['height']:.1f}")
