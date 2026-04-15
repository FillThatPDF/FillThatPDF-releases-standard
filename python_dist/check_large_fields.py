#!/usr/bin/env python3
"""Check if large v22 Text fields are TPs or FPs."""

import fitz
import os

def calc_iou(r1, r2):
    x1 = max(r1.x0, r2.x0)
    y1 = max(r1.y0, r2.y0)
    x2 = min(r1.x1, r2.x1)
    y2 = min(r1.y1, r2.y1)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    intersection = (x2 - x1) * (y2 - y1)
    area1 = (r1.x1 - r1.x0) * (r1.y1 - r1.y0)
    area2 = (r2.x1 - r2.x0) * (r2.y1 - r2.y0)
    union = area1 + area2 - intersection
    return intersection / union if union > 0 else 0.0

gt_dir = '/Users/36981/Desktop/PDFTest/PDFs to test/Fillable PDFs'
v22_dir = '/tmp/v22_output'

gt_file = os.path.join(gt_dir, '55570_DTE_SEEL_Contractor_Onboarding _Packet_v26_fillable.pdf')
v22_file = os.path.join(v22_dir, '55570_v22_output.pdf')

gt_doc = fitz.open(gt_file)
v22_doc = fitz.open(v22_file)

# Get all GT fields
gt_fields = []
for page_num, page in enumerate(gt_doc):
    for w in page.widgets():
        gt_fields.append((page_num, w.rect, w.field_name))

# Check large v22 Text fields (area > 10000)
print('Large v22 Text fields and whether they match GT:')
unmatched_large = []
for page_num, page in enumerate(v22_doc):
    for w in page.widgets():
        area = w.rect.width * w.rect.height
        if area > 10000 and w.field_type_string == 'Text':
            best_iou = 0
            best_gt = None
            for gt_page, gt_rect, gt_name in gt_fields:
                if gt_page == page_num:
                    iou = calc_iou(w.rect, gt_rect)
                    if iou > best_iou:
                        best_iou = iou
                        best_gt = gt_name
            
            if best_iou >= 0.3:
                print(f'  MATCHED: v22="{w.field_name[:40]}" area={area:.0f} -> GT="{best_gt}" IoU={best_iou:.2f}')
            else:
                print(f'  FP: v22="{w.field_name[:40]}" area={area:.0f} best_IoU={best_iou:.2f}')
                unmatched_large.append(w.field_name)

print(f'\nTotal large unmatched Text fields (FPs): {len(unmatched_large)}')
