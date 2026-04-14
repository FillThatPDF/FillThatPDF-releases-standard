import os
import pdfplumber
import sys

def diag_box_search(pdf_path, page_num):
    print(f"\n--- Searching for boxes on Page {page_num+1} of {os.path.basename(pdf_path)} ---")
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_num]
        
        # Look for "Not Applicable" to center our search
        words = page.extract_words()
        target = [w for w in words if "Applicable" in w['text']]
        if not target:
            print("Could not find 'Applicable'")
            return
        
        t = target[0]
        y_center = (t['top'] + t['bottom']) / 2
        x_start = t['x0']
        
        print(f"Target 'Applicable' at y={y_center:.1f}, x0={x_start:.1f}")
        
        # Look at EVERYTHING in this line
        area = (0, y_center - 10, page.width, y_center + 10)
        crop = page.within_bbox(area)
        
        print("\nObjects in the line bbox:")
        print(f"Chars: {[c['text'] for c in crop.chars]}")
        print(f"Rects: {len(crop.rects)}")
        for r in crop.rects:
            print(f"  RECT: {r['x0']:.1f}, {r['top']:.1f}, {r['width']:.1f}x{r['height']:.1f}")
        print(f"Lines: {len(crop.lines)}")
        for l in crop.lines:
            print(f"  LINE: {l['x0']:.1f}, {l['top']:.1f} -> {l['x1']:.1f}, {l['bottom']:.1f} width={l['width']}")
        print(f"Curves: {len(crop.curves)}")
        for c in crop.curves:
             # Sample segments
             print(f"  CURVE at {c['x0']:.1f}, {c['top']:.1f} {c['width']:.1f}x{c['height']:.1f}")

if __name__ == "__main__":
    pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/Batch 1 Forms/32987_OPRE_C_v05.pdf"
    diag_box_search(pdf_path, 1)
