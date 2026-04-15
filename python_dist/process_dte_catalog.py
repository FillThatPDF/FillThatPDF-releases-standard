from smart_fillable_v21 import UniversalPDFFillable
import os

pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/57643_DTE_MF_Measures_and_Specs_Income_Qualified_Catalog_v2.pdf"
output_path = "DTE_Catalog_FIXED.pdf"

print(f"Processing {pdf_path}...")
# Note: UniversalPDFFillable(input_pdf, output_pdf=None, settings=None)
detector = UniversalPDFFillable(pdf_path, output_pdf=output_path)
detector.run()
print(f"Saved to {output_path}")
