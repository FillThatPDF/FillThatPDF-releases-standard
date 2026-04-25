#!/usr/bin/env python3
"""Analyze 9787 FPs to find more safe filtering rules"""

import fitz
import os

v22_dir = '/tmp/v22_output'
gt_dir = '/Users/36981/Desktop/PDFTest/PDFs to test/Fillable PDFs'

# Check 9787 checkbox FPs
print("=== 9787 Checkbox Analysis ===")
v22_path = os.path.join(v22_dir, '9787_v22_output.pdf')
doc = fitz.open(v22_path)

checkbox_fields = []
for page in doc:
    for w in page.widgets():
        if w.field_type == 2:  # CheckBox
            checkbox_fields.append({
                'name': w.field_name or '',
                'rect': w.rect,
                'area': w.rect.width * w.rect.height
            })

print(f"V22 checkboxes: {len(checkbox_fields)}")
for f in checkbox_fields[:20]:
    print(f"  '{f['name']}' area={f['area']:.0f}")
doc.close()

# Check GT
gt_path = os.path.join(gt_dir, '9787_ConEd_Res_HVAC_Gas_Rebate_Appl_v01_FINAL_RELEASE_Fillable_Locked.pdf')
doc = fitz.open(gt_path)
gt_cb = []
for page in doc:
    for w in page.widgets():
        if w.field_type == 2:
            gt_cb.append({'name': w.field_name or '', 'area': w.rect.width * w.rect.height})
print(f"\nGT checkboxes: {len(gt_cb)}")
for f in gt_cb[:20]:
    print(f"  '{f['name']}' area={f['area']:.0f}")
doc.close()

# Compare - which v22 checkboxes are NOT in GT?
gt_names = set(f['name'] for f in gt_cb)
v22_names = set(f['name'] for f in checkbox_fields)

extra_in_v22 = v22_names - gt_names
print(f"\nV22 checkbox names NOT in GT ({len(extra_in_v22)}):")
for n in sorted(extra_in_v22)[:20]:
    print(f"  '{n}'")
