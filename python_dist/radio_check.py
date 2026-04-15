#!/usr/bin/env python3
"""Check radio button distribution between GT and v22"""

import fitz

# GT
gt_path = '/Users/36981/Desktop/PDFTest/PDFs to test/Fillable PDFs/53252_DTE_EEA_Field_Inspection_Report_v14_Web_Release_Fillable.pdf'
doc = fitz.open(gt_path)

print('GT RadioButtons per page:')
gt_total = 0
for page_idx, page in enumerate(doc):
    count = sum(1 for w in page.widgets() if w.field_type == 5)
    gt_total += count
    if count > 0:
        print(f'  Page {page_idx+1}: {count}')
print(f'  Total: {gt_total}')
doc.close()

# V22
v22_path = '/tmp/v22_output/53252_v22_output.pdf'
doc2 = fitz.open(v22_path)

print('\nV22 RadioButtons per page:')
v22_total = 0
for page_idx, page in enumerate(doc2):
    count = sum(1 for w in page.widgets() if w.field_type == 5)
    v22_total += count
    if count > 0:
        print(f'  Page {page_idx+1}: {count}')
print(f'  Total: {v22_total}')
doc2.close()

# Now check specific FN locations
print('\n--- Checking specific FN radio buttons ---')
doc_gt = fitz.open(gt_path)
doc_v22 = fitz.open(v22_path)

# FN example: page 2, Radio_Group_Page2_186_YesNo at (233,187,239,193)
fn_page = 1  # 0-indexed
fn_rect = fitz.Rect(233, 187, 239, 193)

# Find GT radio at this location
print(f'\nLooking for GT radio near {fn_rect}:')
for widget in doc_gt[fn_page].widgets():
    if widget.field_type == 5:  # Radio
        r = widget.rect
        if abs(r.x0 - fn_rect.x0) < 10 and abs(r.y0 - fn_rect.y0) < 10:
            print(f'  Found: {widget.field_name} at {r}')

# Find v22 radio at this location
print(f'\nLooking for V22 radio near {fn_rect}:')
for widget in doc_v22[fn_page].widgets():
    if widget.field_type == 5:  # Radio
        r = widget.rect
        if abs(r.x0 - fn_rect.x0) < 10 and abs(r.y0 - fn_rect.y0) < 10:
            print(f'  Found: {widget.field_name} at {r}')

# List ALL v22 page 2 radios
print(f'\nAll V22 page 2 radios:')
count = 0
for widget in doc_v22[fn_page].widgets():
    if widget.field_type == 5:
        r = widget.rect
        print(f'  {widget.field_name} at ({r.x0:.0f},{r.y0:.0f},{r.x1:.0f},{r.y1:.0f})')
        count += 1
        if count >= 10:
            print('  ...')
            break

doc_gt.close()
doc_v22.close()
