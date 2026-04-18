#!/usr/bin/env python3
import sys
import pdfplumber

pdf = pdfplumber.open('/Users/36981/Desktop/PDFTest/PDFs to test/Batch 1 Forms/57643_DTE_MF_Measures_and_Specs_Income_Qualified_Catalog_v2.pdf')

# Check P11 for header colors
page = pdf.pages[10]
print('=== P11 Header Area (y < 100) ===')
header_objs = page.within_bbox((0, 0, page.width, 100)).objects
for r in header_objs.get('rect', []):
    color = r.get('non_stroking_color', [])
    print(f"Rect ({r['x0']:.1f},{r['y0']:.1f}) Color: {color} (len={len(color) if color else 0})")

# Check logo area (bottom right, typically y > 700, x > 600)
print('\n=== P11 Bottom-Right (Logo Area) ===')
for img in page.images:
    print(f"Image: ({img['x0']:.1f},{img['y0']:.1f})-({img['x1']:.1f},{img['y1']:.1f})")

# Check text in bottom area
bottom_text = page.within_bbox((600, 700, page.width, page.height)).extract_text()
if bottom_text:
    print(f"Text in bottom-right: {bottom_text[:100]}")
