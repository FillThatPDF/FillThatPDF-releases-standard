import pdfplumber

pdf_path = "/Users/36981/Desktop/Studio_Time_Sheet.pdf"
with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[0]
    words = page.extract_words()
    print(f"Words in bottom half of Page 1:")
    for w in sorted(words, key=lambda l: l['top']):
        if w['top'] > 700:
            print(f"'{w['text']}' at top={w['top']:.2f}, bottom={w['bottom']:.2f}, x0={w['x0']:.2f}, x1={w['x1']:.2f}")
