import pdfplumber
import sys

def debug_area(path, page_num, x0, y0, x1, y1):
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[page_num]
        print(f"--- Debugging P{page_num+1} ({x0}, {y0}) to ({x1}, {y1}) ---")
        
        # Rects
        rects = page.within_bbox((x0, y0, x1, y1)).rects
        for r in rects:
            print(f"Rect: {r['x0']:.1f}, {r['top']:.1f}, {r['x1']:.1f}, {r['bottom']:.1f} fill={r.get('fill')} stroke={r.get('stroke')} non_stroking_color={r.get('non_stroking_color')}")
        
        # Words
        words = page.within_bbox((x0, y0, x1, y1)).extract_words()
        for w in words:
            print(f"Word: '{w['text']}' at {w['x0']:.1f}, {w['top']:.1f}")

if __name__ == "__main__":
    # Page 2 (idx 1), area around y=251
    debug_area("9787_ConEd_Res HVAC Electric Rebate Appl_v01_FINAL_RELEASE_NOEDIT.pdf", 1, 250, 200, 400, 300)
