import pdfplumber
import sys

def dump_text(pdf_path, page_num, x0, y0, x1, y1):
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_num]
        bbox = (x0, y0, x1, y1)
        crop = page.crop(bbox)
        text = crop.extract_text()
        print(f"--- Text in {bbox} ---")
        print(text)
        print("--- Words ---")
        words = crop.extract_words()
        for w in words:
            print(f"{w['text']} at ({w['x0']:.1f}, {w['top']:.1f})")

if __name__ == "__main__":
    pdf_path = sys.argv[1]
    page_num = int(sys.argv[2])
    x0 = float(sys.argv[3])
    y0 = float(sys.argv[4])
    x1 = float(sys.argv[5])
    y1 = float(sys.argv[6])
    dump_text(pdf_path, page_num, x0, y0, x1, y1)
