
import pdfplumber

pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/Static PDFs/55570_DTE_SEEL_Contractor_Onboarding _Packet_v26.pdf"

with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[12]
    print(f"Page 13 Geometry:")
    print(f"  Width: {page.width}, Height: {page.height}")
    print(f"  Bbox: {page.bbox}")
    print(f"  CropBox: {page.page_obj.get('/CropBox')}")
    print(f"  MediaBox: {page.page_obj.get('/MediaBox')}")
