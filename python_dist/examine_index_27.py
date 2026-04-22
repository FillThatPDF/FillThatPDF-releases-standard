import pdfplumber

v21_path = "/Users/36981/Desktop/PDFTest/PDFs to test/Fillable PDFs/55570_DTE_SEEL_Contractor_Onboarding_Packet_v21_Web_Release_Fillable.pdf"

with pdfplumber.open(v21_path) as pdf:
    page = pdf.pages[32] # Index 32
    print(f"v21 Page 33 (Index 32) Text:\n{page.extract_text()}")
