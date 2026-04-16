#!/usr/bin/env python3
"""Debug colon detection on Page 9"""
import pdfplumber

pdf = pdfplumber.open('/Users/36981/Desktop/PDFTest/PDFs to test/Batch 1 Forms/53252_DTE_EEA_Field_Inspection_Report_v11.pdf')
page9 = pdf.pages[8]  # 0-indexed

# Check words ending with colon
words = page9.extract_words()
colon_words = []
for i, w in enumerate(words):
    text = w['text']
    if text.endswith(':'):
        colon_words.append({
            'text': text,
            'x0': w['x0'],
            'x1': w['x1'],
            'top': w['top'],
            'bottom': w['bottom'],
            'index': i
        })
        print(f'{i:3d}. "{text:30s}" x0={w["x0"]:.1f}-{w["x1"]:.1f} top={w["top"]:.1f}')

# Group by Y
rows = {}
for cw in colon_words:
    y = round(cw['top'], 0)
    if y not in rows:
        rows[y] = []
    rows[y].append(cw)

print('\n--- Grouped by Row ---')
for y, labels in sorted(rows.items()):
    print(f'Y={y}: {len(labels)} labels: {[l["text"] for l in labels]}')
