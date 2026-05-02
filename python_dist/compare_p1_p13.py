
import pdfplumber

pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/Static PDFs/55570_DTE_SEEL_Contractor_Onboarding _Packet_v26.pdf"

with pdfplumber.open(pdf_path) as pdf:
    for page_idx in [0, 12]: # Page 1 and 13
        page = pdf.pages[page_idx]
        print(f"Page {page_idx + 1} Statistics:")
        print(f"  Rects: {len(page.rects)}")
        print(f"  Lines: {len(page.lines)}")
        print(f"  Curves: {len(page.curves)}")
        print(f"  Edges: {len(page.edges)}")
