#!/usr/bin/env python3
import pikepdf

pdf = pikepdf.open('/Users/36981/Desktop/PDFTest/PDFs to test/Batch 1 Forms/56380_DTE_2026_PartnerGuidelines_v18_fillable.pdf')

# Page 21 is index 20
page_num = 20
page = pdf.pages[page_num]

fields = []
if '/Annots' in page:
    for annot in page.Annots:
        ft = str(annot.get('/FT', ''))
        if ft == '/Tx':  # Text fields only
            name = str(annot.get('/T', ''))
            rect = annot.get('/Rect')
            x0, y0, x1, y1 = float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])
            width = x1 - x0
            height = y1 - y0
            fields.append((x0, y0, name, width, height))

# Sort by x position to see columns
fields.sort(key=lambda f: f[0])

print(f'Text fields on page 21 (sorted by X position):')
for x0, y0, name, w, h in fields[:30]:
    print(f'  x={x0:.0f} y={y0:.0f} w={w:.0f}x{h:.0f} "{name[:40]}"')

print(f'\n... Total: {len(fields)} text fields')
