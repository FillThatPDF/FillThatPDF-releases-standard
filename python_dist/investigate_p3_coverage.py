import sys
from pathlib import Path
import pdfplumber
import os
from smart_fillable_v21 import UniversalPDFFillable

def investigate_p3():
    input_pdf = "/Users/36981/Desktop/PDFTest/PDFs to test/Batch 1 Forms/53252_DTE_EEA_Field_Inspection_Report_v11.pdf"
    if not os.path.exists(input_pdf):
        print(f"Error: File not found: {input_pdf}")
        return
        
    det = UniversalPDFFillable(input_pdf)
    det.pdf = pdfplumber.open(input_pdf)
    page_num = 2  # Page 3
    page = det.pdf.pages[page_num]
    
    det._preload_structural_data()
    det._detect_all_checkboxes()
    
    print(f"\n--- Investigating Page {page_num+1} ---")
    
    # 1. Check Detected Checkboxes
    p3_cbs = [cb for cb in det.checkboxes if cb['page'] == page_num]
    print(f"Detected {len(p3_cbs)} checkboxes on P3.")
    for i, cb in enumerate(p3_cbs[:10]):
        print(f"  CB {i}: x={cb['x']:.1f}, y={cb['y']:.1f} source={cb.get('source')}")

    # 2. Check Visual Grid
    grid = det._build_visual_grid(page_num)
    print(f"\nDetected {len(grid)} grid cells on P3.")
    
    # Analyze row 5 (roughly y=236 based on my trace)
    target_y = 236
    row_cells = [c for c in grid if abs(c['y0'] - target_y) < 5]
    print(f"\nCells near y={target_y}:")
    for c in sorted(row_cells, key=lambda x: x['x0']):
        cw = c['x1'] - c['x0']
        print(f"  Cell: x0={c['x0']:.1f}, x1={c['x1']:.1f}, width={cw:.1f}")
        
        # Check for checkboxes inside
        cbs_in = [cb for cb in p3_cbs if c['x0']-1 <= cb['x'] <= c['x1']+1 and c['y0']-1 <= cb['y'] <= c['y1']+1]
        print(f"    Checkboxes inside: {len(cbs_in)}")
        for cb in cbs_in:
            print(f"      - CB at x={cb['x']:.1f}")
            
        # Test my heuristic
        has_cb = det._cell_has_visual_checkbox(page_num, c['x0'], c['y0'], c['x1'], c['y1'])
        print(f"    _cell_has_visual_checkbox: {has_cb}")

    det.pdf.close()

if __name__ == "__main__":
    investigate_p3()
