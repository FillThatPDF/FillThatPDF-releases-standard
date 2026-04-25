import sys
from pathlib import Path
import pdfplumber

# Add the directory to sys.path
sys.path.append("/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/FillThatPDF_Optimized/python_dist")

pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/Blank/57643_DTE_MF_Measures_and_Specs_Income_Qualified_Catalog_v2_FILLABLE.pdf"

def find_applicant_p7():
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[6] # Page 7
        for w in page.extract_words():
            if 'Applicant' in w['text']:
                print(f"Word: '{w['text']}', x0={w['x0']:.1f}, x1={w['x1']:.1f}, top={w['top']:.1f}, bottom={w['bottom']:.1f}")

if __name__ == "__main__":
    find_applicant_p7()
