#!/usr/bin/env python3
"""Check RadioButton patterns in 53252."""

import fitz
import os

# Check radio buttons in 53252
v22_path = '/tmp/v22_output/53252_v22_output.pdf'
gt_path = '/Users/36981/Desktop/PDFTest/PDFs to test/Fillable PDFs/53252_DTE_EEA_Field_Inspection_Report_v14_Web_Release_Fillable.pdf'

v22_doc = fitz.open(v22_path)
gt_doc = fitz.open(gt_path)

# Count RadioButton fields
v22_rb = [w for page in v22_doc for w in page.widgets() if w.field_type_string == 'RadioButton']
gt_rb = [w for page in gt_doc for w in page.widgets() if w.field_type_string == 'RadioButton']

print(f'V22 RadioButtons: {len(v22_rb)}')
print(f'GT RadioButtons: {len(gt_rb)}')

# Check naming patterns
v22_names = {}
for w in v22_rb:
    name = w.field_name
    v22_names[name] = v22_names.get(name, 0) + 1

print(f'\nV22 RadioButton name patterns (top 10):')
for name, count in sorted(v22_names.items(), key=lambda x: -x[1])[:10]:
    print(f'  {name}: {count}')

gt_names = {}
for w in gt_rb:
    name = w.field_name
    gt_names[name] = gt_names.get(name, 0) + 1

print(f'\nGT RadioButton name patterns (top 10):')
for name, count in sorted(gt_names.items(), key=lambda x: -x[1])[:10]:
    print(f'  {name}: {count}')
