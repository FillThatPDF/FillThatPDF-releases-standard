
import pdfplumber

def debug_section_b():
    pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/New Test Forms/11691_ConEd_Distributor_Application_Form_v10_FILLABLE.pdf"
    
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        words = page.extract_words()
        
        # Look for "B. General Program Design"
        section_b_word = None
        for w in words:
            if "General" in w['text'] and float(w['top']) > 300 and float(w['top']) < 400:
                if "Program" in words[words.index(w)+1]['text']:
                    section_b_word = w
                    break
        
        if not section_b_word:
            print("Could not find Section B header")
            return
            
        print(f"Section B Start: y={section_b_word['top']}")
        
        # Analyze area from y=320 to y=600
        interest_area = page.within_bbox((36, float(section_b_word['top']), 576, 650))
        
        print("\n--- Words in Section B Area ---")
        lines = {}
        for w in interest_area.extract_words():
            y = round(float(w['top']), 1)
            lines.setdefault(y, []).append(w)
            
        for y in sorted(lines.keys()):
            line_text = " ".join([w['text'] for w in lines[y]])
            x1 = max(w['x1'] for w in lines[y])
            print(f"y={y}: '{line_text}' (end_x={x1:.1f})")

        print("\n--- Rects in Section B Area ---")
        for r in interest_area.rects:
            print(f"Rect: {r['x0']:.1f}, {r['top']:.1f}, {r['x1']:.1f}, {r['bottom']:.1f}")

if __name__ == "__main__":
    debug_section_b()
