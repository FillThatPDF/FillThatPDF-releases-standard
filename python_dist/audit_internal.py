from smart_fillable_v21 import UniversalPDFFillable
import pdfplumber
import sys

pdf_path = "9787_ConEd_Res HVAC Electric Rebate Appl_v01_FINAL_RELEASE_NOEDIT.pdf"
output_path = "coned_debug.pdf"

engine = UniversalPDFFillable(pdf_path, output_path)
engine._cache_page_info()

engine._detect_checkboxes_character()
engine._detect_checkboxes_vector()
engine._detect_checkboxes_curve()
engine._detect_checkboxes_line_box()

print(f"Total CB in list: {len(engine.checkboxes)}")
p2_cbs = [c for c in engine.checkboxes if c['page']==1]
print(f"P2 CB count: {len(p2_cbs)}")
if p2_cbs:
    print(f"Smallest Y on P2: {min(c['y'] for c in p2_cbs)}")
    print(f"Largest Y on P2: {max(c['y'] for c in p2_cbs)}")
