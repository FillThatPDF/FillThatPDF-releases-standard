import sys
import os
import pikepdf
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from smart_fillable_v21 import UniversalPDFFillable

def analyze_output():
    pdf_path = "Fixed_53252_ScoreCells.pdf"
    if not os.path.exists(pdf_path):
        print(f"File {pdf_path} not found!")
        return

    with pikepdf.open(pdf_path) as pdf:
        page = pdf.pages[2] # Page 3
        print(f"===== FIELDS ON PAGE 3 =====")
        count = 0
        if '/Annots' in page:
            for annot in page.Annots:
                if annot.get('/Subtype') == '/Widget':
                    t = str(annot.get('/T'))
                    rect = [float(v) for v in annot.get('/Rect')]
                    print(f"   Field: {t} at Rect: {rect}")
                    count += 1
        print(f"TOTAL WIDGETS FOUND ON PAGE 3: {count}")

if __name__ == "__main__":
    analyze_output()
