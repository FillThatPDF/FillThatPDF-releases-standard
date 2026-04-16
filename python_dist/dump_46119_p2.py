import pdfplumber

def dump_p2():
    pdf_path = "../46119_RECO_HVAC_Incentive_Claim_Form_v02.pdf"
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[1] # Page 2
        print(f"--- Page 2 (index 1) Size: {page.width}x{page.height} ---")
        
        print("\n--- All Horizontal Lines ---")
        lines = page.lines
        rect_lines = [r for r in page.rects if r['height'] < 5]
        all_h_lines = []
        for l in lines:
            if abs(l['top'] - l['bottom']) < 2:
                all_h_lines.append(l)
        for r in rect_lines:
            all_h_lines.append({'top': r['top'], 'x0': r['x0'], 'x1': r['x1'], 'width': r['width'], 'height': r['height'], 'source': 'rect'})
        all_h_lines.sort(key=lambda x: x['top'])
        for l in all_h_lines:
            src = l.get('source', 'line')
            print(f"Y= {l['top']:5.1f} X= {l['x0']:5.1f}-{l['x1']:5.1f} W={l['x1']-l['x0']:5.1f} [{src}]")

        print("\n--- All Words ---")
        words = page.extract_words()
        words.sort(key=lambda w: (w['top'], w['x0']))
        for w in words:
            print(f"Y= {w['top']:5.1f} X= {w['x0']:5.1f}-{w['x1']:5.1f} '{w['text']}'")

if __name__ == "__main__":
    dump_p2()
