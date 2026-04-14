import sys
from pathlib import Path
import pdfplumber
import os
from smart_fillable_v21 import UniversalPDFFillable

def investigate_p3_headers():
    input_pdf = "/Users/36981/Desktop/PDFTest/PDFs to test/Batch 1 Forms/53252_DTE_EEA_Field_Inspection_Report_v11.pdf"
    det = UniversalPDFFillable(input_pdf)
    det.pdf = pdfplumber.open(input_pdf)
    page_num = 2  # Page 3
    page = det.pdf.pages[page_num]
    
    det._preload_structural_data()
    
    print(f"\n--- Investigating Headers on Page {page_num+1} ---")
    
    tables = det.page_tables.get(page_num, [])
    for t_idx, table in enumerate(tables):
        print(f"Table {t_idx} bbox: {table.bbox}")
        for r_idx, row in enumerate(table.rows[:2]):
            print(f"  Row {r_idx}:")
            for c_idx, cell in enumerate(row.cells):
                if not cell: continue
                cx0, cy0, cx1, cy1 = cell
                crop = page.within_bbox((cx0+1, cy0+1, cx1-1, cy1-1))
                text = (crop.extract_text() or "").strip()
                print(f"    Cell {c_idx}: x0={cx0:.1f}, x1={cx1:.1f}, text='{text}'")

    det.pdf.close()

if __name__ == "__main__":
    investigate_p3_headers()
