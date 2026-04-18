import pdfplumber

def debug_layout(pdf_path):
    print(f"Analyzing {pdf_path}...")
    with pdfplumber.open(pdf_path) as pdf:
        # Page 1 Analysis - Utility Section
        p1 = pdf.pages[0] # 0-indexed
        print("\n--- Page 1 Layout Analysis (Utility Section: y=500-600) ---")
        
        print("Words:")
        words = p1.extract_words()
        for w in words:
            if 500 < w['top'] < 600:
                print(f"  Word: '{w['text']}' x0={w['x0']:.1f} top={w['top']:.1f} bottom={w['bottom']:.1f} x1={w['x1']:.1f}")

        print("\nLines:")
        # pdfplumber lines can be in .lines or .rects depending on how they are drawn
        # We'll check both for horizontal-ish lines
        for l in p1.lines:
            if 500 < l['top'] < 600:
                 print(f"  Line: x0={l['x0']:.1f} x1={l['x1']:.1f} top={l['top']:.1f} bottom={l['bottom']:.1f}")

        # Page 3 Analysis - PSEG Table
        p3 = pdf.pages[2]
        print("\n--- Page 3 Layout Analysis (Top Table: y=100-300) ---")
        print("Words:")
        words_p3 = p3.extract_words()
        for w in words_p3:
            if 100 < w['top'] < 300:
                 print(f"  Word: '{w['text']}' x0={w['x0']:.1f} top={w['top']:.1f} bottom={w['bottom']:.1f} x1={w['x1']:.1f}")

if __name__ == "__main__":
    debug_layout("46119_FINAL.pdf")
