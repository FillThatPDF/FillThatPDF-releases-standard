import pdfplumber

pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/Blank/57643_DTE_MF_Measures_and_Specs_Income_Qualified_Catalog_v2_FILLABLE.pdf"

with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[6] # Page 7
    print(f"Default: {len(page.find_tables())}")
    print(f"Tol 5: {len(page.find_tables(table_settings={'intersection_tolerance': 5}))}")
    print(f"Tol 10: {len(page.find_tables(table_settings={'intersection_tolerance': 10}))}")
    
    for i, t in enumerate(page.find_tables(table_settings={'intersection_tolerance': 5})):
        print(f"Table {i} bbox: {t.bbox}")
