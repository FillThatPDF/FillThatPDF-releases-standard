from smart_fillable_v21 import UniversalPDFFillable
import sys

pdf_path = "9787_ConEd_Res HVAC Electric Rebate Appl_v01_FINAL_RELEASE_NOEDIT.pdf"
output_path = "coned_test_v21_in_v4.pdf"

print(f"Running smart_fillable_v21 on {pdf_path}...")
engine = UniversalPDFFillable(pdf_path, output_path)
engine.run()
print(f"Done. Output saved to {output_path}")
