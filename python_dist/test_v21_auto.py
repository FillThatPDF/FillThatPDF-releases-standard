from smart_fillable_v21 import UniversalPDFFillable
import sys

pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/Batch 1 Forms/57643_DTE_MF_Measures_and_Specs_Income_Qualified_Catalog_v2.pdf"
output_path = "test_57643_v21_auto_v2.pdf"

print(f"Running smart_fillable_v21 (AUTO) on {pdf_path}...")
engine = UniversalPDFFillable(pdf_path, output_path, auto_calibrate=True)
engine.run()
print(f"Done. Output saved to {output_path}")
