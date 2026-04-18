import pdfplumber
import sys

pdf_path = "/Users/36981/Desktop/Studio_Time_Sheet.pdf"
with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[0]
    lines = page.lines
    print(f"Total lines on Page 1: {len(lines)}")
    for i, line in enumerate(sorted(lines, key=lambda l: l['top'])):
        if line['top'] > 500: # Focus on the bottom half
            print(f"Line {i}: x0={line['x0']:.2f}, x1={line['x1']:.2f}, y={line['top']:.2f}, width={line['width']:.2f}")
