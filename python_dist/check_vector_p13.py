
import pdfplumber
import sys

pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/Static PDFs/55570_DTE_SEEL_Contractor_Onboarding _Packet_v26.pdf"

with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[12] # Page 13
    print(f"Page 13 Statistics:")
    print(f"  Rects: {len(page.rects)}")
    print(f"  Lines: {len(page.lines)}")
    print(f"  Curves: {len(page.curves)}")
    print(f"  Edges: {len(page.edges)}")
    
    # Check for target area [135, 645, 303, 657] (Bottom-Up)
    # Target in Top-Down: [135, 792-657, 303, 792-645] = [135, 135, 303, 147]
    target = [135, 135, 303, 147]
    found = False
    for r in page.rects:
        rx0, ry0, rx1, ry1 = r['x0'], r['top'], r['x1'], r['bottom']
        if abs(rx0 - 135) < 5 and abs(ry0 - 135) < 5:
            print(f"  FOUND RECT at target! {r}")
            found = True
    if not found:
        print("  Target rect NOT FOUND in page.rects")
