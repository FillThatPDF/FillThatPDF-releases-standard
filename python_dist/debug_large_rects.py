#!/usr/bin/env python3
"""Debug - mimic the _detect_large_image_rectangles logic"""
import pdfplumber

pdf = pdfplumber.open('/Users/36981/Desktop/PDFTest/PDFs to test/Batch 1 Forms/53252_DTE_EEA_Field_Inspection_Report_v11.pdf')

MIN_WIDTH = 80
MIN_HEIGHT = 80
MAX_WIDTH = 600
MAX_HEIGHT = 500
MIN_ASPECT = 0.3
MAX_ASPECT = 3.5

# Simulate empty text_fields and checkboxes (we don't know what they'd be at this point)
# The real code may have fields that overlap and cause the image boxes to be skipped

# Check pages 4-8 (indices 3-7)
for pg_idx in [3, 4, 5, 6, 7]:
    page = pdf.pages[pg_idx]
    page_height = float(page.height)
    page_width = float(page.width)
    
    rects = page.rects or []
    chars = page.chars or []
    
    print(f"\nPage {pg_idx+1}: {len(rects)} rectangles")
    
    image_box_candidates = 0
    
    for rect in rects:
        x0 = float(rect['x0'])
        y_top = float(rect['top'])
        x1 = float(rect['x1'])
        y_bottom = float(rect['bottom'])
        
        width = x1 - x0
        height = y_bottom - y_top
        
        # Check size requirements
        if width < MIN_WIDTH or height < MIN_HEIGHT:
            continue
        if width > MAX_WIDTH or height > MAX_HEIGHT:
            continue
            
        aspect_ratio = width / height if height > 0 else 0
        if aspect_ratio < MIN_ASPECT or aspect_ratio > MAX_ASPECT:
            continue
        
        # Check if mostly empty (minimal text inside)
        chars_inside = [c for c in chars 
                       if c['x0'] >= x0 and c['x1'] <= x1 
                       and c['top'] >= y_top and c['bottom'] <= y_bottom]
        
        if len(chars_inside) > 5:
            # What text is inside?
            text = ''.join([c['text'] for c in chars_inside[:20]])
            print(f"  Skipped: {width:.0f}x{height:.0f} at ({x0:.0f},{y_top:.0f}) - {len(chars_inside)} chars inside: '{text[:30]}'")
            continue
        
        image_box_candidates += 1
        print(f"  ✓ Candidate: {width:.0f}x{height:.0f} at ({x0:.0f},{y_top:.0f})")
    
    print(f"  Total candidates: {image_box_candidates}")

pdf.close()
