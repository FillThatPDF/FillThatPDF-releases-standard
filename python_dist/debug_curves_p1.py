import pdfplumber

pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/New Test Forms/11691_ConEd_Distributor_Application_Form_v10_FILLABLE.pdf"

with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[0]
    curves = page.curves
    print(f"--- Page 1 Curves (y=600 to 750) ---")
    relevant = [c for c in curves if 600 < c['top'] < 750]
    relevant.sort(key=lambda c: c['top'])
    for c in relevant:
        print(f"Curve: y={c['top']:.1f} to {c['bottom']:.1f}, x={c['x0']:.1f} to {c['x1']:.1f} w={c['width']:.1f} h={c['height']:.1f}")
