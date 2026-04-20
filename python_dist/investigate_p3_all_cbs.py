import sys
from pathlib import Path
import pdfplumber
import os
from smart_fillable_v21 import UniversalPDFFillable

def investigate_p3_all_cbs():
    input_pdf = "/Users/36981/Desktop/PDFTest/PDFs to test/Batch 1 Forms/53252_DTE_EEA_Field_Inspection_Report_v11.pdf"
    det = UniversalPDFFillable(input_pdf)
    det.pdf = pdfplumber.open(input_pdf)
    page_num = 2  # Page 3
    
    det._preload_structural_data()
    det._detect_all_checkboxes()
    
    print(f"\n--- All Detected Checkboxes on Page {page_num+1} ---")
    p3_cbs = [cb for cb in det.checkboxes if cb['page'] == page_num]
    print(f"Total: {len(p3_cbs)}")
    
    # Sort by Y then X
    p3_cbs.sort(key=lambda c: (c['y'], c['x']))
    
    for cb in p3_cbs:
        print(f"  CB at x={cb['x']:.1f}, y={cb['y']:.1f} source={cb.get('source')}")

    det.pdf.close()

if __name__ == "__main__":
    investigate_p3_all_cbs()
