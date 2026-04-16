import sys
from pathlib import Path
import pdfplumber
import os
from smart_fillable_v21 import UniversalPDFFillable

def investigate_p3_labels():
    input_pdf = "/Users/36981/Desktop/PDFTest/PDFs to test/Batch 1 Forms/53252_DTE_EEA_Field_Inspection_Report_v11.pdf"
    det = UniversalPDFFillable(input_pdf)
    det.pdf = pdfplumber.open(input_pdf)
    page_num = 2  # Page 3
    
    det._preload_structural_data()
    det._detect_all_checkboxes()
    det._enrich_checkbox_labels_from_column_headers()
    det._enrich_checkbox_labels_from_beside_text()
    det._identify_radio_groups()
    
    print(f"\n--- All Detected Checkboxes on Page {page_num+1} ---")
    p3_cbs = [cb for cb in det.checkboxes if cb['page'] == page_num]
    p3_cbs.sort(key=lambda c: (c['y'], c['x']))
    
    for cb in p3_cbs:
        print(f"  CB at x={cb['x']:.1f}, y={cb['y']:.1f} Label='{cb.get('label')}' radio_value='{cb.get('radio_value')}' is_child={cb.get('is_radio_group_child')}")

    print(f"\n--- Radio Groups on Page {page_num+1} ---")
    p3_groups = [g for g in det.radio_groups if g['page'] == page_num]
    for g in p3_groups:
        print(f"  Group: {g['name']} with {len(g['checkboxes'])} checkboxes")
        for cb in g['checkboxes']:
             print(f"    - CB at x={cb['x']:.1f}, y={cb['y']:.1f} val={cb.get('radio_value')}")

    det.pdf.close()

if __name__ == "__main__":
    investigate_p3_labels()
