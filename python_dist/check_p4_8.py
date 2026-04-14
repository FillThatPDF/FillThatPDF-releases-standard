#!/usr/bin/env python3
"""Check pages 4-8 for Notes and image box fields"""
import pikepdf

pdf = pikepdf.open('/Users/36981/Desktop/PDFTest/PDFs to test/Batch 1 Forms/53252_DTE_EEA_Field_Inspection_Report_v11_fillable.pdf')

# Check page 4 for Notes fields
print("Page 4 Notes/Image fields:")
page = pdf.pages[3]
annots = list(page.get('/Annots', []))
for annot in annots:
    name = str(annot.get('/T', ''))
    if 'Notes' in name or 'af_image' in name or 'Image' in name:
        ft = str(annot.get('/FT', ''))
        ff = int(annot.get('/Ff', 0))
        rect = annot.get('/Rect', [])
        w = float(rect[2]) - float(rect[0]) if rect else 0
        h = float(rect[3]) - float(rect[1]) if rect else 0
        is_pushbutton = ft == '/Btn' and (ff & (1 << 16))
        print(f'  {name}: ft={ft} ff={ff} size={w:.0f}x{h:.0f} pushbutton={is_pushbutton}')

pdf.close()
