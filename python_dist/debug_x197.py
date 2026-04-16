#!/usr/bin/env python3
"""Debug why checkboxes at x=197-198 exist"""
import pdfplumber

pdf_path = '/Users/36981/Desktop/PDFTest/PDFs to test/Batch 1 Forms/53252_DTE_EEA_Field_Inspection_Report_v11.pdf'
with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[2]  # Page 3
    page_height = page.height
    
    print("Looking for visual elements around x=197-200...")
    
    # Check rects
    rects = page.rects
    for rect in rects:
        x0, x1 = rect['x0'], rect['x1']
        if 195 <= x0 <= 200 or 195 <= x1 <= 200:
            w = x1 - rect['x0']
            h = rect['bottom'] - rect['top']
            print(f"Rect: ({rect['x0']:.1f}, {rect['top']:.1f}) to ({x1:.1f}, {rect['bottom']:.1f}) - {w:.1f}x{h:.1f}")
    
    # Check curves/paths
    curves = page.curves if hasattr(page, 'curves') else []
    for curve in curves:
        if 'x0' in curve and 195 <= curve['x0'] <= 200:
            print(f"Curve at x0={curve['x0']:.1f}")
    
    # Check chars around that area
    print("\n\nChars around x=195-205:")
    chars = page.chars
    for char in chars:
        if 195 <= char['x0'] <= 205 and 160 <= char['top'] <= 250:
            print(f"  '{char['text']}' at ({char['x0']:.1f}, {char['top']:.1f})")
    
    # Check if there are any small boxes (potential checkbox symbols)
    print("\n\nSmall boxes (4-15pt) around x=190-210:")
    for rect in rects:
        w = rect['x1'] - rect['x0']
        h = rect['bottom'] - rect['top']
        if 4 <= w <= 15 and 4 <= h <= 15:
            if 190 <= rect['x0'] <= 210:
                print(f"  Small box: ({rect['x0']:.1f}, {rect['top']:.1f}) {w:.1f}x{h:.1f}")
