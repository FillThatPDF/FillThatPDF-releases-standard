#!/usr/bin/env python3
"""Debug the matching PROPERLY - trace each step"""
import fitz
import re

FILLABLE_DIR = '/Users/36981/Desktop/PDFTest/PDFs to test/Fillable PDFs'
V22_DIR = '/tmp/v22_output'
IOU_THRESHOLD = 0.3

GT_PATH = f'{FILLABLE_DIR}/57618_NGRID_New_York_Ack_Form_Fillable_v07_Release_Web_Fillable.pdf'
V22_PATH = f'{V22_DIR}/57618_v22_output.pdf'

def get_fields(pdf_path):
    doc = fitz.open(pdf_path)
    fields = []
    for i, page in enumerate(doc):
        for w in page.widgets():
            fields.append({
                'name': w.field_name,
                'page': i,
                'x0': w.rect.x0, 'y0': w.rect.y0,
                'x1': w.rect.x1, 'y1': w.rect.y1
            })
    doc.close()
    return fields

def calc_iou(f1, f2):
    if f1['page'] != f2['page']:
        return 0
    
    x0 = max(f1['x0'], f2['x0'])
    y0 = max(f1['y0'], f2['y0'])
    x1 = min(f1['x1'], f2['x1'])
    y1 = min(f1['y1'], f2['y1'])
    
    if x1 <= x0 or y1 <= y0:
        return 0
    
    inter = (x1-x0) * (y1-y0)
    a1 = (f1['x1']-f1['x0']) * (f1['y1']-f1['y0'])
    a2 = (f2['x1']-f2['x0']) * (f2['y1']-f2['y0'])
    
    return inter / (a1 + a2 - inter) if (a1 + a2 - inter) > 0 else 0

gt_fields = get_fields(GT_PATH)
v22_fields = get_fields(V22_PATH)

print(f"GT fields: {len(gt_fields)}")
print(f"V22 fields: {len(v22_fields)}")

# The matching algorithm from conservative_postprocessor
matched_pred = set()
matched_gt = set()

for i, pred in enumerate(v22_fields):
    best_iou = 0
    best_j = -1
    for j, gt in enumerate(gt_fields):
        if j in matched_gt:  # Skip already-matched GT fields
            continue
        iou = calc_iou(pred, gt)
        if iou > best_iou:
            best_iou = iou
            best_j = j
    
    if best_iou >= IOU_THRESHOLD:
        matched_pred.add(i)
        matched_gt.add(best_j)
        
        # Check if this is a numeric-named field
        name_cleaned = pred['name'].replace(',', '').replace('_', '').replace(' ', '')
        if re.match(r'^\d{2,}$', name_cleaned):
            print(f"NUMERIC TP: v22[{i}]='{pred['name']}' matched gt[{best_j}]='{gt_fields[best_j]['name']}' IoU={best_iou:.2f}")

print(f"\nTotal TPs: {len(matched_pred)}")
print(f"Matched GT fields: {len(matched_gt)}")

# Now check: how many numeric-named fields are in the 34 FPs?
fp_indices = [i for i in range(len(v22_fields)) if i not in matched_pred]
print(f"\nFalse Positives ({len(fp_indices)}):")
numeric_fps = []
for i in fp_indices:
    name = v22_fields[i]['name']
    name_cleaned = name.replace(',', '').replace('_', '').replace(' ', '')
    if re.match(r'^\d{2,}$', name_cleaned):
        numeric_fps.append(name)
        print(f"  NUMERIC FP: '{name}'")

print(f"\nNumeric FPs: {len(numeric_fps)}")
