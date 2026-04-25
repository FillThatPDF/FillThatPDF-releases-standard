import pdfplumber

pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/New Test Forms/11691_ConEd_Distributor_Application_Form_v10_FILLABLE.pdf"

with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[0]
    lines = page.lines
    print(f"--- Page 1 Lines (y=600 to 750) ---")
    relevant = [l for l in lines if 600 < l['top'] < 750]
    relevant.sort(key=lambda l: l['top'])
    for l in relevant:
        print(f"Line: y={l['top']:.1f}, x={l['x0']:.1f} to {l['x1']:.1f} w={l['width']:.1f}")
