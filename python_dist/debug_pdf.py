#!/usr/bin/env python3
"""Debug: analyze annotations in a specific PDF."""
import pikepdf
import sys

pdf_path = sys.argv[1] if len(sys.argv) > 1 else '/Users/36981/Desktop/PDFTest/PDFs to test/Static PDFs/1/9787_ConEd_Res HVAC Electric Rebate Appl_v01_FINAL_RELEASE_NOEDIT.pdf'

pdf = pikepdf.open(pdf_path)
for pi, page in enumerate(pdf.pages):
    if '/Annots' not in page:
        print(f'Page {pi}: no annotations')
        continue
    print(f'Page {pi}: {len(page.Annots)} annotations')
    for ai, annot in enumerate(page.Annots):
        sub = str(annot.get('/Subtype', 'none'))
        ft = str(annot.get('/FT', 'none'))
        t = str(annot.get('/T', 'none'))
        a_type = ''
        uri = ''
        if '/A' in annot:
            a_type = str(annot['/A'].get('/S', ''))
            if '/URI' in annot['/A']:
                uri = str(annot['/A']['/URI'])
        parent_ft = ''
        if '/Parent' in annot:
            parent_ft = str(annot['/Parent'].get('/FT', 'none'))
        rect = [float(c) for c in annot.get('/Rect', [0, 0, 0, 0])]
        
        is_widget = sub == '/Widget'
        marker = ' <<< WIDGET (counted as field)' if is_widget else ''
        print(f'  annot#{ai}: Subtype={sub:10s} FT={ft:8s} ParentFT={parent_ft:8s} T={t[:35]:35s} Action={a_type:10s} URI={uri[:60]}{marker}')
pdf.close()
