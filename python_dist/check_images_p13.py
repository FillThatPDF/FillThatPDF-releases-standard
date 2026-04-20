
import pdfplumber

pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/Static PDFs/55570_DTE_SEEL_Contractor_Onboarding _Packet_v26.pdf"

with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[12]
    print("Page 13 Image Statistics:")
    print(f"  Images: {len(page.images)}")
    for i, img in enumerate(page.images):
        print(f"  Image {i}: {img['x0'], img['top'], img['x1'], img['bottom']} ({img['width']}x{img['height']})")
