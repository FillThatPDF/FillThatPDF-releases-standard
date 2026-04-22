import pdfplumber

v26_path = "/Users/36981/Desktop/PDFTest/PDFs to test/Static PDFs/55570_DTE_SEEL_Contractor_Onboarding _Packet_v26.pdf"

with pdfplumber.open(v26_path) as pdf:
    for i, page in enumerate(pdf.pages):
        text = page.extract_text()
        if "No air leaks coming from furnace cabinet" in text:
            print(f"Found anchor on Index {i} (Page {i+1})")
