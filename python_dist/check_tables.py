import pdfplumber

pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/Batch 1 Forms/57643_DTE_MF_Measures_and_Specs_Income_Qualified_Catalog_v2.pdf"
pages_to_check = [6, 10, 12] # P7, P11, P13

print(f"Checking tables on pages {[p+1 for p in pages_to_check]}...")

with pdfplumber.open(pdf_path) as pdf:
    for page_num in pages_to_check:
        page = pdf.pages[page_num]
        tables = page.find_tables()
        print(f"\nPage {page_num + 1}: Found {len(tables)} tables")
        for i, table in enumerate(tables):
            bbox = table.bbox
            print(f"  Table {i+1}: {bbox} Rows: {len(table.rows)}")
