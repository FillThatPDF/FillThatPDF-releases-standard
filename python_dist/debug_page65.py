#!/usr/bin/env python3
"""Debug why image boxes aren't being detected on page 65"""
import pdfplumber

pdf_path = '/Users/36981/Desktop/PDFTest/PDFs to test/Batch 1 Forms/56380_DTE_2026_PartnerGuidelines_v18.pdf'

with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[64]  # Page 65 (0-indexed)
    
    print(f"Page 65 dimensions: {page.width} x {page.height}")
    
    # Get all lines and rects
    lines = page.lines
    rects = page.rects
    curves = page.curves
    
    print(f"\nLines: {len(lines)}")
    print(f"Rects: {len(rects)}")
    print(f"Curves: {len(curves)}")
    
    # Look for large rectangles (potential image boxes)
    print("\n=== Large Rectangles (w>100, h>100) ===")
    for rect in rects:
        x0, y0, x1, y1 = float(rect['x0']), float(rect['top']), float(rect['x1']), float(rect['bottom'])
        w, h = x1 - x0, y1 - y0
        if w > 100 and h > 100:
            print(f"  Rect: ({x0:.1f}, {y0:.1f}) to ({x1:.1f}, {y1:.1f}) - {w:.1f}x{h:.1f}")
            
            # Check for images inside
            try:
                box_objs = page.within_bbox((x0, y0, x1, y1)).objects
                images = box_objs.get('image', [])
                print(f"    Images inside: {len(images)}")
                for img in images:
                    print(f"      {img}")
            except Exception as e:
                print(f"    Error checking: {e}")
    
    # Check for images on the page
    print("\n=== All Images on Page 65 ===")
    page_images = page.images
    print(f"Total images: {len(page_images)}")
    for img in page_images:
        print(f"  {img}")
    
    # Check objects
    print("\n=== Page Objects Summary ===")
    objs = page.objects
    for key in objs.keys():
        print(f"  {key}: {len(objs[key])}")
