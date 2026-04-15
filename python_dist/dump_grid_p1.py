
import sys
import os
import pdfplumber

# Add parent dir to path to import smart_fillable
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from smart_fillable_v21 import UniversalPDFFillable

def dump_grid():
    pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/New Test Forms/11691_ConEd_Distributor_Application_Form_v10_FILLABLE.pdf"
    detector = UniversalPDFFillable(pdf_path)
    
    # Pass 0 & 1 to populate basic data
    detector._preload_structural_data()
    # Open handle for header detection
    detector.pdf = pdfplumber.open(pdf_path)
    header_bars = detector._detect_header_bars()
    
    page_num = 0 # Page 1
    grid_cells = detector._build_visual_grid(page_num)
    bars = header_bars.get(page_num, [])
    
    print(f"--- Page {page_num+1} Header Bars ---")
    for hb in bars:
        print(f"Header: {hb['x0']:.1f}, {hb['y0']:.1f}, {hb['x1']:.1f}, {hb['y1']:.1f} font={hb.get('font_size')} text='{hb.get('text', '')[:30]}'")

    print(f"\n--- Page {page_num+1} Grid Cells ---")
    for i, cell in enumerate(grid_cells):
        cx0, cy0, cx1, cy1 = cell['x0'], cell['y0'], cell['x1'], cell['y1']
        
        # Simulating overlap check
        is_header = detector._is_in_header_bar(cx0, cy0, cx1, cy1, bars)
        
        # Word extraction
        page = detector.pdf.pages[page_num]
        cell_crop = page.within_bbox((cx0, cy0, cx1, cy1))
        words = cell_crop.extract_words()
        text = " ".join([w['text'] for w in words])
        
        status = "HEADER" if is_header else "CELL"
        print(f"[{i:02}] {status}: {cx0:.1f}, {cy0:.1f} -> {cx1:.1f}, {cy1:.1f} | Text: '{text[:50]}'")

if __name__ == "__main__":
    dump_grid()
