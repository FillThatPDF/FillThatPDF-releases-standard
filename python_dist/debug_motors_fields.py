from smart_fillable_v21 import UniversalPDFFillable
import pdfplumber

pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/Motors 02-04-10kt.pdf"

detector = UniversalPDFFillable(pdf_path)
with pdfplumber.open(pdf_path) as pdf:
    detector.pdf = pdf
    detector.page_count = len(pdf.pages)
    detector._cache_page_info()
    detector._preload_structural_data()
    detector.run() # Run full pipeline
    
    print(f"\nFields on Page 1 ({len(detector.text_fields)} total):")
    for f in detector.text_fields:
        if f['page'] == 0:
            fmt = f.get('format')
            print(f"  Field: {f['name']:40} | Format: {str(fmt):10} | Source: {f.get('source'):20} | x0={f['x0']:5.1f}, x1={f['x1']:5.1f}")
