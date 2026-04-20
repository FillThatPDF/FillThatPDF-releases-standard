
import pdfplumber

pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/Static PDFs/55570_DTE_SEEL_Contractor_Onboarding _Packet_v26.pdf"

with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[12]
    print("Page 13 Text-Based Table Detection:")
    
    settings = {
        "vertical_strategy": "text",
        "horizontal_strategy": "text",
        "snap_tolerance": 3,
        "join_tolerance": 3,
    }
    
    tables = page.find_tables(table_settings=settings)
    print(f"  Found {len(tables)} tables with 'text' strategy")
    for i, t in enumerate(tables):
        print(f"  Table {i} bbox: {t.bbox}, Cells: {len(t.cells)}")
        if t.cells and len(t.cells) > 0:
            print(f"  First cell: {t.cells[0]}")
