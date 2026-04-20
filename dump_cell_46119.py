
import pdfplumber
import re

def dump_cell_details(pdf_path, page_idx, bbox):
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_idx]
        cell_crop = page.within_bbox(bbox)
        words = cell_crop.extract_words()
        
        print(f"--- Cell Words (Bbox: {bbox}) ---")
        for w in words:
            print(f"WORD: '{w['text']}' Bbox: [{w['x0']:.1f}, {w['top']:.1f}, {w['x1']:.1f}, {w['bottom']:.1f}]")
        
        lines = page.lines
        print("\n--- Lines in Cell ---")
        for l in lines:
            if bbox[0] <= l['x0'] <= bbox[2] and bbox[1] <= l['top'] <= bbox[3]:
                 print(f"LINE: x0={l['x0']:.1f} top={l['top']:.1f} x1={l['x1']:.1f} bottom={l['bottom']:.1f}")

if __name__ == "__main__":
    pdf_path = "46119_RECO_HVAC_Incentive_Claim_Form_v02.pdf"
    # Gas Cell Bbox from previous dump: (36.125, 498.88815, 306.125, 561.7561999999999)
    dump_cell_details(pdf_path, 0, (36.125, 498.8, 310.0, 562.0))
