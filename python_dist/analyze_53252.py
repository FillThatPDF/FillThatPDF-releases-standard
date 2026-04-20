#!/usr/bin/env python3
"""
Detailed FP analysis for 53252 to understand what's being filtered wrong.
"""

import fitz
import os
import re

OUTPUT_DIR = '/tmp/v22_all_output'
GT_DIR = '/Users/36981/Desktop/PDFTest/PDFs to test/Fillable PDFs'

V22_PATH = os.path.join(OUTPUT_DIR, '53252_DTE_EEA_Field_Inspection_Report_v11_v22_output.pdf')
GT_PATH = os.path.join(GT_DIR, '53252_DTE_EEA_Field_Inspection_Report_v14_Web_Release_Fillable.pdf')

def calc_iou(r1, r2):
    x1, y1 = max(r1.x0, r2.x0), max(r1.y0, r2.y0)
    x2, y2 = min(r1.x1, r2.x1), min(r1.y1, r2.y1)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2-x1)*(y2-y1)
    a1 = (r1.x1-r1.x0)*(r1.y1-r1.y0)
    a2 = (r2.x1-r2.x0)*(r2.y1-r2.y0)
    return inter/(a1+a2-inter) if (a1+a2-inter) > 0 else 0

def center_distance(r1, r2):
    return (((r1.x0+r1.x1)/2-(r2.x0+r2.x1)/2)**2+((r1.y0+r1.y1)/2-(r2.y0+r2.y1)/2)**2)**0.5

def adaptive_match(pred, gt):
    if gt['area'] < 100:
        if center_distance(pred['rect'], gt['rect']) <= 40:
            return True
    return calc_iou(pred['rect'], gt['rect']) >= 0.3

def get_text_blocks(page):
    blocks = []
    text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    for block in text_dict.get("blocks", []):
        if block.get("type") == 0:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "").strip()
                    if text:
                        non_underscore = text.replace('_', '').replace('-', '').strip()
                        if len(non_underscore) < len(text) * 0.3:
                            continue
                        blocks.append({'text': text, 'rect': fitz.Rect(span["bbox"])})
    return blocks

def field_text_overlap_ratio(field_rect, text_blocks):
    field_area = field_rect.width * field_rect.height
    if field_area == 0:
        return 0
    total_overlap = 0
    for block in text_blocks:
        x1, y1 = max(field_rect.x0, block['rect'].x0), max(field_rect.y0, block['rect'].y0)
        x2, y2 = min(field_rect.x1, block['rect'].x1), min(field_rect.y1, block['rect'].y1)
        if x2 > x1 and y2 > y1:
            total_overlap += (x2-x1)*(y2-y1)
    return min(1.0, total_overlap / field_area)

def should_filter(name, ftype, area, overlap, width, height):
    if re.match(r'^Check Box \d+$', name):
        return True
    if '_Stack' in name:
        return True
    if name.count(':') >= 2 and ftype == 'Text' and overlap > 0.2:
        return True
    if ftype == 'Text' and overlap > 0.85 and area > 500:
        return True
    if ftype == 'Text' and area > 7000 and overlap > 0.4:
        return True
    if '?' in name and ftype == 'Text':
        return True
    if ftype == 'Text' and area > 10000 and len(name) > 15 and ' ' in name and overlap < 0.1:
        return True
    return False

# Get GT fields
gt_doc = fitz.open(GT_PATH)
gt_fields = []
for page_idx, page in enumerate(gt_doc):
    for widget in page.widgets():
        area = widget.rect.width * widget.rect.height
        gt_fields.append({
            'name': widget.field_name or '', 'rect': widget.rect, 
            'page': page_idx, 'type': widget.field_type_string, 'area': area
        })
gt_doc.close()

# Get v22 fields after filtering
v22_doc = fitz.open(V22_PATH)
v22_fields = []
v22_filtered = []
for page_idx, page in enumerate(v22_doc):
    text_blocks = get_text_blocks(page)
    for widget in page.widgets():
        name = widget.field_name or ''
        ftype = widget.field_type_string
        area = widget.rect.width * widget.rect.height
        overlap = field_text_overlap_ratio(widget.rect, text_blocks)
        width = widget.rect.width
        height = widget.rect.height
        
        field = {
            'name': name, 'rect': widget.rect, 'page': page_idx,
            'type': ftype, 'area': area, 'overlap': overlap, 
            'width': width, 'height': height
        }
        
        if should_filter(name, ftype, area, overlap, width, height):
            v22_filtered.append(field)
        else:
            v22_fields.append(field)
v22_doc.close()

# Match and find FPs
matched_pred = set()
matched_gt = set()

for i, pred in enumerate(v22_fields):
    best_score = -1
    best_j = -1
    for j, gt in enumerate(gt_fields):
        if j in matched_gt:
            continue
        if adaptive_match(pred, gt):
            iou = calc_iou(pred['rect'], gt['rect'])
            if iou > best_score:
                best_score = iou
                best_j = j
    if best_j >= 0:
        matched_pred.add(i)
        matched_gt.add(best_j)

fp_fields = [v22_fields[i] for i in range(len(v22_fields)) if i not in matched_pred]
fn_fields = [gt_fields[j] for j in range(len(gt_fields)) if j not in matched_gt]

print(f"53252 Analysis")
print(f"GT fields: {len(gt_fields)}")
print(f"v22 after filter: {len(v22_fields)}")
print(f"Filtered out: {len(v22_filtered)}")
print(f"TP: {len(matched_pred)}, FP: {len(fp_fields)}, FN: {len(fn_fields)}")
print()

print("="*100)
print("FALSE POSITIVES (v22 fields not matching GT) - by type:")
print("="*100)

# Group by type
by_type = {}
for fp in fp_fields:
    by_type.setdefault(fp['type'], []).append(fp)

for ftype, fields in sorted(by_type.items()):
    print(f"\n{ftype}: {len(fields)} FPs")
    print("-"*80)
    for f in sorted(fields, key=lambda x: (x['page'], x['rect'].y0))[:10]:
        print(f"  Page {f['page']+1}: '{f['name'][:50]:<50}' area={f['area']:>7.0f} overlap={f['overlap']:.2f} width={f['width']:.0f}")
    if len(fields) > 10:
        print(f"  ... and {len(fields)-10} more")

print("\n" + "="*100)
print("FALSE NEGATIVES (GT fields not found by v22):")
print("="*100)
for fn in sorted(fn_fields, key=lambda x: (x['page'], x['rect'].y0))[:20]:
    print(f"  Page {fn['page']+1}: '{fn['name'][:50]:<50}' area={fn['area']:>7.0f}")
