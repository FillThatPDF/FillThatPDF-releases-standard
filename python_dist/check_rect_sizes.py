#!/usr/bin/env python3
import sys
sys.path.insert(0, '/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/FillThatPDF_Optimized_v4/python_dist')
import pdfplumber

pdf = pdfplumber.open('/Users/36981/Desktop/PDFTest/PDFs to test/Batch 1 Forms/57643_DTE_MF_Measures_and_Specs_Income_Qualified_Catalog_v2.pdf')
page = pdf.pages[10]  # P11

print("=== ALL Colored Rectangles on P11 ===")
for r in page.rects:
    color = r.get('non_stroking_color', [])
    if color and color != [1, 1, 1] and color != 1:  # Not white
        print(f"Rect: ({r['x0']:.1f},{r['y0']:.1f})-({r['x1']:.1f},{r['y1']:.1f}), color={color}")
        
        # Check width
        width = r['x1'] - r['x0']
        height = r['y1'] - r['y0']
        print(f"  Size: {width:.1f} x {height:.1f}")
