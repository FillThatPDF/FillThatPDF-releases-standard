import pikepdf
import sys

pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/Batch 1 Forms/55570_DTE_SEEL_Contractor_Onboarding _Packet_v26_fillable.pdf"
with pikepdf.open(pdf_path) as pdf:
    page = pdf.pages[13] # Page 14
    print(f"Fields on Page 32:")
    if '/Annots' in page:
        for annot in page.Annots:
            if annot.get('/Subtype') == '/Widget':
                name = str(annot.get('/T', 'No Name'))
                rect = [float(x) for x in annot.get('/Rect')]
                print(f" - {name} at {rect}")
