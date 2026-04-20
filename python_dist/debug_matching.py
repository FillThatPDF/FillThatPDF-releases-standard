#!/usr/bin/env python3
"""Debug the matching to understand what happens when we remove numeric fields"""
import fitz
import re

FILLABLE_DIR = '/Users/36981/Desktop/PDFTest/PDFs to test/Fillable PDFs'
V22_DIR = '/tmp/v22_output'
POSTPROCESSED_DIR = '/tmp/v22_postprocessed'
IOU_THRESHOLD = 0.3

GT_PATH = f'{FILLABLE_DIR}/57618_NGRID_New_York_Ack_Form_Fillable_v07_Release_Web_Fillable.pdf'
V22_PATH = f'{V22_DIR}/57618_v22_output.pdf'
POST_PATH = f'{POSTPROCESSED_DIR}/57618_postprocessed.pdf'

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

def match_fields(pred_fields, gt_fields):
    matched_pred = set()
    matched_gt = set()
    matches = []
    
    for i, pred in enumerate(pred_fields):
        best_iou = 0
        best_j = -1
        for j, gt in enumerate(gt_fields):
            if j in matched_gt:
                continue
            iou = calc_iou(pred, gt)
            if iou > best_iou:
                best_iou = iou
                best_j = j
        
        if best_iou >= IOU_THRESHOLD:
            matched_pred.add(i)
            matched_gt.add(best_j)
            matches.append((pred['name'], gt_fields[best_j]['name'], best_iou))
    
    return len(matched_pred), matches

gt_fields = get_fields(GT_PATH)
v22_fields = get_fields(V22_PATH)
post_fields = get_fields(POST_PATH)

print(f"GT fields: {len(gt_fields)}")
print(f"V22 fields: {len(v22_fields)}")
print(f"Postprocessed fields: {len(post_fields)}")

print("\n=== V22 MATCHING ===")
v22_tp, v22_matches = match_fields(v22_fields, gt_fields)
print(f"True Positives: {v22_tp}")
# Show matches for fields that got removed
removed_names = set(f['name'] for f in v22_fields) - set(f['name'] for f in post_fields)
print(f"\nFields that were removed ({len(removed_names)}):")
for vn, gn, iou in v22_matches:
    if vn in removed_names:
        print(f"  {vn} → {gn} (IoU={iou:.2f})")

print("\n=== POSTPROCESSED MATCHING ===")
post_tp, post_matches = match_fields(post_fields, gt_fields)
print(f"True Positives: {post_tp}")

# Find GT fields that were matched in V22 but not in POST
v22_matched_gt = set(gn for vn, gn, iou in v22_matches)
post_matched_gt = set(gn for vn, gn, iou in post_matches)

lost_gt = v22_matched_gt - post_matched_gt
print(f"\nGT fields that lost their match ({len(lost_gt)}):")
for gn in lost_gt:
    print(f"  {gn}")
