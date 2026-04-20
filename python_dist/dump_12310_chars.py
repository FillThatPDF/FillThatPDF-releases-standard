import os
import pdfplumber
import sys

def dump_chars(pdf_path, page_num):
    print(f"\n--- Dumping all characters on Page {page_num+1} of {os.path.basename(pdf_path)} ---")
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_num]
        # Sort by top then x0
        chars = sorted(page.chars, key=lambda c: (c['top'], c['x0']))
        
        for i, char in enumerate(chars):
            text = char.get('text', '')
            font = char.get('fontname', '')
            x0 = char['x0']
            top = char['top']
            w = char['x1'] - x0
            h = char['bottom'] - top
            
            # Print everything that isn't simple alphanumeric or common punctuation
            # or anything in an interesting font
            is_interesting = False
            if len(text) == 1 and (ord(text) < 32 or ord(text) > 126):
                is_interesting = True
            if 'wing' in font.lower() or 'zapf' in font.lower() or 'ding' in font.lower() or 'cid' in text.lower():
                is_interesting = True
            # Also print if it's a small square-ish box
            if 5 < w < 20 and 5 < h < 20:
                is_interesting = True
                
            if is_interesting:
                print(f"[{i}] '{text}' (ord {ord(text) if len(text)==1 else 'N/A'}) font='{font}' at ({x0:.1f}, {top:.1f}) size {w:.1f}x{h:.1f}")

if __name__ == "__main__":
    pdf_12310 = "/Users/36981/Desktop/PDFTest/PDFs to test/Batch 2 Forms/12310_ConEd_Res_Appliance_Application_v03_FINAL_RELEASE_print.pdf"
    dump_chars(pdf_12310, 0)
    dump_chars(pdf_12310, 1)
