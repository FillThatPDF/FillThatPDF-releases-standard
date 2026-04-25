from smart_fillable_v21 import UniversalPDFFillable
import sys

pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/Batch 1 Forms/32987_OPRE_C_v05_FILLABLE.pdf"
output_path = "test_32987_v21.pdf"

print(f"Running smart_fillable_v21 on {pdf_path}...")
# Use auto_calibrate=True as enabled in main app now
engine = UniversalPDFFillable(pdf_path, output_path, auto_calibrate=True)
engine.run()
print(f"Done. Output saved to {output_path}")
