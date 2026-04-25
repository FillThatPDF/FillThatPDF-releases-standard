#!/usr/bin/env python3
import pikepdf
import sys

pdf_path = sys.argv[1] if len(sys.argv) > 1 else '/Users/36981/Desktop/PDFTest/PDFs to test/Batch 1 Forms/56380_DTE_2026_PartnerGuidelines_v18_fillable.pdf'

pdf = pikepdf.open(pdf_path)

# Check via page annotations
print("Checking via page annotations...")
all_annots = 0
btn_annots = 0
pushbutton_annots = 0
for page_num, page in enumerate(pdf.pages):
    if '/Annots' not in page:
        continue
    for annot in page.Annots:
        all_annots += 1
        ft = annot.get('/FT')
        ff = int(annot.get('/Ff', 0))
        if str(ft) == '/Btn':
            btn_annots += 1
            if ff & 65536:
                pushbutton_annots += 1
                if pushbutton_annots <= 5:
                    name = str(annot.get('/T', 'Unknown'))
                    rect = annot.get('/Rect')
                    print(f"  Page {page_num+1}: {name} Ff={ff}")

print(f"\nTotal annotations: {all_annots}")
print(f"Total Btn annotations: {btn_annots}")
print(f"Total pushbutton annotations: {pushbutton_annots}")

# Also check via AcroForm Fields
print("\nChecking via AcroForm/Fields...")
acroform = pdf.Root.get('/AcroForm')
if acroform:
    fields = acroform.get('/Fields', [])
    print(f"Total fields in AcroForm: {len(fields)}")
