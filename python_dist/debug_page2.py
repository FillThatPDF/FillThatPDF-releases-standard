import pdfplumber

def debug_page3(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[2] # Page 3
        words = page.extract_words()
        lines = page.lines
        
        print("WORDS ON PAGE 3:")
        for w in sorted(words, key=lambda x: (x['top'], x['x0'])):
            print(f"  '{w['text']}' | top={w['top']:.2f}, bot={w['bottom']:.2f}, x0={w['x0']:.2f}, x1={w['x1']:.2f}")

        print("\nHORIZONTAL LINES ON PAGE 3:")
        for line in lines:
            if abs(line['top'] - line['bottom']) < 2:
                y = (line['top'] + line['bottom']) / 2
                print(f"  Line at y={y:.2f}, x0={line['x0']:.2f}, x1={line['x1']:.2f}")

if __name__ == "__main__":
    pdf = "/Users/36981/Desktop/PDFs to test/57026_Avangrid_Clean_Heat_2026_BE_Incentive_Application_v05_Release_Print.pdf"
    debug_page3(pdf)
