
import pdfplumber
import sys

pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/Static PDFs/55570_DTE_SEEL_Contractor_Onboarding _Packet_v26.pdf"

with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[12] # Page 13
    print(f"Page 13 Table detection:")
    tables = page.find_tables()
    print(f"  Found {len(tables)} tables")
    for i, t in enumerate(tables):
        print(f"  Table {i} bounds: {t.bbox}")
        print(f"  Cells: {len(t.cells)}")
        # Check first cell
        if t.cells:
            print(f"  First cell: {t.cells[0]}")
