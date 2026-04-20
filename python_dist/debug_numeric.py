#!/usr/bin/env python3
"""Debug why numeric-named fields are matching GT"""
import fitz
import re

GT_PATH = '/Users/36981/Desktop/PDFTest/PDFs to test/Fillable PDFs/57618_NGRID_New_York_Ack_Form_Fillable_v07_Release_Web_Fillable.pdf'
V22_PATH = '/tmp/v22_output/57618_v22_output.pdf'

gt_doc = fitz.open(GT_PATH)
gt_fields = []
for i, page in enumerate(gt_doc):
    for w in page.widgets():
        gt_fields.append({
            'name': w.field_name,
            'page': i,
            'x0': w.rect.x0, 'y0': w.rect.y0,
            'x1': w.rect.x1, 'y1': w.rect.y1
        })
gt_doc.close()

v22_doc = fitz.open(V22_PATH)
v22_fields = []
for i, page in enumerate(v22_doc):
    for w in page.widgets():
        v22_fields.append({
            'name': w.field_name,
            'page': i,
            'x0': w.rect.x0, 'y0': w.rect.y0,
            'x1': w.rect.x1, 'y1': w.rect.y1
        })
v22_doc.close()

print(f'GT fields: {len(gt_fields)}')
print(f'V22 fields: {len(v22_fields)}')

# Check for IoU matches between numeric v22 fields and GT
for v in v22_fields:
    name_cleaned = v['name'].replace(',', '').replace('_', '').replace(' ', '')
    if not re.match(r'^\d{2,}$', name_cleaned):
        continue
    
    # Find best IoU with GT
    best_iou = 0
    best_gt = None
    for gt in gt_fields:
        if gt['page'] != v['page']:
            continue
        x0 = max(v['x0'], gt['x0'])
        y0 = max(v['y0'], gt['y0'])
        x1 = min(v['x1'], gt['x1'])
        y1 = min(v['y1'], gt['y1'])
        if x1 <= x0 or y1 <= y0:
            continue
        inter = (x1-x0) * (y1-y0)
        a1 = (v['x1']-v['x0']) * (v['y1']-v['y0'])
        a2 = (gt['x1']-gt['x0']) * (gt['y1']-gt['y0'])
        iou = inter / (a1 + a2 - inter)
        if iou > best_iou:
            best_iou = iou
            best_gt = gt
    
    if best_iou >= 0.3:
        print(f'MATCH: v22="{v["name"]}" ↔ gt="{best_gt["name"]}" IoU={best_iou:.2f}')
    else:
        print(f'NO MATCH: v22="{v["name"]}" best_iou={best_iou:.2f}')
