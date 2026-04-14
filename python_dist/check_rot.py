import sys
from pathlib import Path
import pikepdf

# Add the directory to sys.path
sys.path.append("/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/FillThatPDF_Optimized/python_dist")

pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/Blank/57643_DTE_MF_Measures_and_Specs_Income_Qualified_Catalog_v2_FILLABLE.pdf"

def check_rotations():
    with pikepdf.open(pdf_path) as pdf:
        for i in [6, 8]:
            page = pdf.pages[i]
            rotate = int(page.get('/Rotate', 0))
            print(f"Page {i+1} Rotation: {rotate}")

if __name__ == "__main__":
    check_rotations()
