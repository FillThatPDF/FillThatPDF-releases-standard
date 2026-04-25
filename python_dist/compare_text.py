
import pdfplumber

v21_path = "/Users/36981/Desktop/PDFTest/PDFs to test/Fillable PDFs/55570_DTE_SEEL_Contractor_Onboarding_Packet_v21_Web_Release_Fillable.pdf"
v26_path = "/Users/36981/Desktop/PDFTest/PDFs to test/Static PDFs/55570_DTE_SEEL_Contractor_Onboarding _Packet_v26.pdf"

def get_text(path):
    with pdfplumber.open(path) as pdf:
        return " ".join(p.extract_text() or "" for p in pdf.pages)

t21 = get_text(v21_path)
t26 = get_text(v26_path)

print(f"v21 Length: {len(t21)}")
print(f"v26 Length: {len(t26)}")
print(f"Overlap: {len(set(t21.split()) & set(t26.split()))} words")
