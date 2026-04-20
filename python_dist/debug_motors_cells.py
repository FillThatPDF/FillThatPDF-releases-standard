import pdfplumber

pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/Motors 02-04-10kt.pdf"

with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[0]
    tables = page.find_tables()
    print(f"Found {len(tables)} tables")
    for i, table in enumerate(tables):
        print(f"\nTable {i} (bbox={table.bbox}):")
        for row in table.cells:
             # cell is (x0, y0, x1, y1)
             print(f"  Cell: {row}")
             cell_text = page.within_bbox(row).extract_text()
             print(f"    Text: {cell_text}")
