from smart_fillable_v21 import UniversalPDFFillable
import pdfplumber
import sys

pdf_path = "9787_ConEd_Res HVAC Electric Rebate Appl_v01_FINAL_RELEASE_NOEDIT.pdf"
output_path = "coned_debug.pdf"

engine = UniversalPDFFillable(pdf_path, output_path)
# Hook into the same plumbing as engine.run()
engine._cache_page_info()

print("--- PASS 1: Checkboxes ---")
engine._detect_checkboxes_character()
engine._detect_checkboxes_vector()
print(f"P2 CB after P1: {len([c for c in engine.checkboxes if c['page']==1])}")

print("\n--- PASS 2-4: Tables & Boxes ---")
engine._detect_table_cells()
engine._detect_empty_boxes()
print(f"P2 CB after P4: {len([c for c in engine.checkboxes if c['page']==1])}")

print("\n--- PASS 6: Conflict Resolution ---")
engine._resolve_all_conflicts()
print(f"P2 CB after P6: {len([c for c in engine.checkboxes if c['page']==1])}")
