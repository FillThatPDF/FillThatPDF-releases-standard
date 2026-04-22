#!/usr/bin/env python3
"""Check if FP patterns also exist in TPs - find SAFE rules"""

import fitz
import os
from collections import defaultdict
import re

IOU_THRESHOLD = 0.3

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

def get_fields(pdf_path):
    doc = fitz.open(pdf_path)
    fields = []
    for page_idx, page in enumerate(doc):
        for widget in page.widgets():
            fields.append({
                'name': widget.field_name or '',
                'rect': widget.rect,
                'page': page_idx,
                'type': widget.field_type_string,
                'type_code': widget.field_type,
                'area': widget.rect.width * widget.rect.height,
                'width': widget.rect.width,
                'height': widget.rect.height
            })
    doc.close()
    return fields

def match_fields(pred_fields, gt_fields):
    matched_gt = set()
    matched_pred = set()
    
    for i, pred in enumerate(pred_fields):
        best_iou = 0
        best_j = -1
        for j, gt in enumerate(gt_fields):
            if j in matched_gt:
                continue
            iou = calc_iou(pred['rect'], gt['rect'])
            if iou > best_iou:
                best_iou = iou
                best_j = j
        
        if best_iou >= IOU_THRESHOLD:
            matched_pred.add(i)
            matched_gt.add(best_j)
    
    tps = [pred_fields[i] for i in matched_pred]
    fps = [pred_fields[i] for i in range(len(pred_fields)) if i not in matched_pred]
    return tps, fps

# Test pairs
test_pairs = [
    ('11691', '11691_ConEd_Distributor_Application_Form_v10_fillable_RELEASE.pdf', '11691_v22_output.pdf'),
    ('14792', '14792_ConEd_Gas_HVAC_Tune-Up_Contractor_Application_v03_FINAL_RELEASE_Web_Fillable.pdf', '14792_v22_output.pdf'),
    ('32775', '32775_DTE_2022_HVAC_Customer_SelfSubmission_Application_v01_Web_Release_Fillable.pdf', '32775_v22_output.pdf'),
    ('53252', '53252_DTE_EEA_Field_Inspection_Report_v14_Web_Release_Fillable.pdf', '53252_v22_output.pdf'),
    ('55570', '55570_DTE_SEEL_Contractor_Onboarding _Packet_v26_fillable.pdf', '55570_v22_output.pdf'),
    ('57618', '57618_NGRID_New_York_Ack_Form_Fillable_v07_Release_Web_Fillable.pdf', '57618_v22_output.pdf'),
    ('57769', '57769_CH_Res_NYS_Clean_Heat_Completion_Acknowledgement_Form_v3_Web_Release_fillable.pdf', '57769_v22_output.pdf'),
    ('9787', '9787_ConEd_Res_HVAC_Gas_Rebate_Appl_v01_FINAL_RELEASE_Fillable_Locked.pdf', '9787_v22_output.pdf'),
]

gt_dir = '/Users/36981/Desktop/PDFTest/PDFs to test/Fillable PDFs'
v22_dir = '/tmp/v22_output'

all_tps = []
all_fps = []

for pdf_id, gt_name, v22_name in test_pairs:
    gt_path = os.path.join(gt_dir, gt_name)
    v22_path = os.path.join(v22_dir, v22_name)
    
    if not os.path.exists(gt_path) or not os.path.exists(v22_path):
        continue
    
    gt_fields = get_fields(gt_path)
    v22_fields = get_fields(v22_path)
    
    tps, fps = match_fields(v22_fields, gt_fields)
    
    for f in tps:
        f['source'] = pdf_id
        all_tps.append(f)
    
    for f in fps:
        f['source'] = pdf_id
        all_fps.append(f)

print(f"Total TPs: {len(all_tps)}")
print(f"Total FPs: {len(all_fps)}\n")

# Define filter rules and check safety
def apply_rule(field, rule_name):
    name = field['name']
    ftype = field['type']
    area = field['area']
    width = field['width']
    
    if rule_name == 'check_box_numbered':
        return bool(re.match(r'^Check Box \d+', name))
    
    if rule_name == 'colon_text':
        return ':' in name and ftype == 'Text'
    
    if rule_name == 'stack_field':
        return '_Stack' in name
    
    if rule_name == 'large_text':
        return ftype == 'Text' and area > 3000
    
    if rule_name == 'very_large_text':
        return ftype == 'Text' and area > 5000
    
    if rule_name == 'huge_text':
        return ftype == 'Text' and area > 7000
    
    if rule_name == 'wide_text':
        return ftype == 'Text' and width > 200
    
    if rule_name == 'very_wide_text':
        return ftype == 'Text' and width > 300
    
    if rule_name == 'extremely_wide_text':
        return ftype == 'Text' and width > 400
    
    return False

# Test each rule
rules = [
    'check_box_numbered',
    'colon_text',
    'stack_field',
    'large_text',
    'very_large_text',
    'huge_text',
    'wide_text',
    'very_wide_text',
    'extremely_wide_text',
]

print("="*70)
print("RULE ANALYSIS - FPs caught vs TPs lost")
print("="*70)

for rule in rules:
    fp_caught = sum(1 for f in all_fps if apply_rule(f, rule))
    tp_lost = sum(1 for f in all_tps if apply_rule(f, rule))
    
    # Net benefit = FPs removed - TPs lost
    net = fp_caught - tp_lost
    safety = "SAFE" if tp_lost == 0 else f"DANGEROUS ({tp_lost} TPs lost)"
    
    print(f"\n{rule}:")
    print(f"  FPs caught: {fp_caught}")
    print(f"  TPs lost: {tp_lost}")
    print(f"  Net benefit: {net:+d}")
    print(f"  Status: {safety}")
    
    if tp_lost > 0 and tp_lost <= 5:
        print(f"  TP examples that would be lost:")
        count = 0
        for tp in all_tps:
            if apply_rule(tp, rule):
                print(f"    - '{tp['name']}' from {tp['source']}")
                count += 1
                if count >= 3:
                    break

# Combined safe rules
print("\n" + "="*70)
print("COMBINED SAFE RULES ANALYSIS")
print("="*70)

def is_safe_fp(field):
    name = field['name']
    ftype = field['type']
    area = field['area']
    
    # Rule 1: Check Box N (seems safe)
    if re.match(r'^Check Box \d+', name):
        return 'check_box_numbered'
    
    # Rule 2: Stack fields
    if '_Stack' in name:
        return 'stack_field'
    
    # Rule 3: Colon in text field name
    if ':' in name and ftype == 'Text':
        return 'colon_text'
    
    # Rule 4: Huge text fields
    if ftype == 'Text' and area > 7000:
        return 'huge_text'
    
    return None

fp_filtered = 0
tp_lost = 0
rule_counts = defaultdict(int)

for fp in all_fps:
    rule = is_safe_fp(fp)
    if rule:
        fp_filtered += 1
        rule_counts[rule] += 1

for tp in all_tps:
    rule = is_safe_fp(tp)
    if rule:
        tp_lost += 1
        rule_counts[f'{rule}_tp_loss'] += 1

print(f"\nWith combined rules:")
print(f"  FPs filtered: {fp_filtered} / {len(all_fps)} ({100*fp_filtered/len(all_fps):.1f}%)")
print(f"  TPs lost: {tp_lost}")
print(f"\nBy rule:")
for rule, count in sorted(rule_counts.items()):
    print(f"  {rule}: {count}")

# Calculate new metrics
new_tp = len(all_tps) - tp_lost
new_fp = len(all_fps) - fp_filtered
new_fn = 262 + tp_lost  # Original FN count + lost TPs

precision = new_tp / (new_tp + new_fp) if (new_tp + new_fp) > 0 else 0
recall = new_tp / (new_tp + new_fn) if (new_tp + new_fn) > 0 else 0
f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

print(f"\nPROJECTED NEW METRICS:")
print(f"  TPs: {len(all_tps)} → {new_tp}")
print(f"  FPs: {len(all_fps)} → {new_fp}")
print(f"  FNs: 262 → {new_fn}")
print(f"  Precision: {100*precision:.1f}%")
print(f"  Recall: {100*recall:.1f}%")
print(f"  F1: {100*f1:.1f}%")
