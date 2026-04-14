import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from smart_fillable_v21 import UniversalPDFFillable

def run_fix():
    base_path = "/Users/36981/Desktop/PDFTest/PDFs to test/Batch 1 Forms"
    print("="*50)
    print("GENERATING FIXED PDF: 53252")
    print("="*50)
    det = UniversalPDFFillable(
        os.path.join(base_path, "53252_DTE_EEA_Field_Inspection_Report_v11.pdf"),
        "Fixed_53252_ScoreCells.pdf"
    )
    det.run()
    
    # Also verify by dumping the page 3 text fields
    print("\nVERIFICATION DUMP (Page 3 - ALL Text Fields):")
    score_count = 0
    for f in det.text_fields:
        if f['page'] == 2:
             is_score = 'Score_Cell' in f.get('name', '')
             if is_score: score_count += 1
             print(f"   [TEXT] {f.get('name')} at ({f['x0']:.1f}, {f['y0']:.1f}) source={f.get('source')}")
    print(f"\nTOTAL SCORE CELLS FOUND: {score_count}")
    
    print("\nVERIFICATION DUMP (Page 3 - ALL Checkboxes):")
    for cb in det.checkboxes:
        if cb['page'] == 2:
             print(f"   [CB] {cb.get('name')} at ({cb['x']:.1f}, {cb['y']:.1f}) source={cb.get('source')}")

if __name__ == "__main__":
    run_fix()
