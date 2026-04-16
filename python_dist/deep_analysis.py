#!/usr/bin/env python3
"""Deep analysis of FPs and FNs to understand patterns"""

import fitz
import os
from collections import defaultdict

IOU_THRESHOLD = 0.3

def calc_iou(r1, r2):
    """Calculate IoU between two rects"""
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
    """Extract all fields with their metadata"""
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
                'area': widget.rect.width * widget.rect.height
            })
    doc.close()
    return fields

def match_fields(pred_fields, gt_fields):
    """Match predicted to GT fields, return TPs, FPs, FNs"""
    matched_gt = set()
    matched_pred = set()
    tp_pairs = []
    
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
            tp_pairs.append((pred, gt_fields[best_j], best_iou))
    
    fps = [pred_fields[i] for i in range(len(pred_fields)) if i not in matched_pred]
    fns = [gt_fields[j] for j in range(len(gt_fields)) if j not in matched_gt]
    
    return tp_pairs, fps, fns

# Define test pairs
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

all_fps = []
all_fns = []
all_tps = []

for pdf_id, gt_name, v22_name in test_pairs:
    gt_path = os.path.join(gt_dir, gt_name)
    v22_path = os.path.join(v22_dir, v22_name)
    
    if not os.path.exists(gt_path) or not os.path.exists(v22_path):
        print(f"Skipping {pdf_id}: files not found")
        continue
    
    gt_fields = get_fields(gt_path)
    v22_fields = get_fields(v22_path)
    
    tps, fps, fns = match_fields(v22_fields, gt_fields)
    
    for f in fps:
        f['source'] = pdf_id
        all_fps.append(f)
    
    for f in fns:
        f['source'] = pdf_id
        all_fns.append(f)
    
    for tp in tps:
        all_tps.append({'pred': tp[0], 'gt': tp[1], 'iou': tp[2], 'source': pdf_id})

print(f"\n=== SUMMARY ===")
print(f"Total TPs: {len(all_tps)}")
print(f"Total FPs: {len(all_fps)}")
print(f"Total FNs: {len(all_fns)}")

precision = len(all_tps) / (len(all_tps) + len(all_fps)) if (len(all_tps) + len(all_fps)) > 0 else 0
recall = len(all_tps) / (len(all_tps) + len(all_fns)) if (len(all_tps) + len(all_fns)) > 0 else 0
f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

print(f"Precision: {precision*100:.1f}%")
print(f"Recall: {recall*100:.1f}%")
print(f"F1: {f1*100:.1f}%")

# Analyze FPs
print(f"\n=== FALSE POSITIVES ANALYSIS ===")

# By field type
fp_by_type = defaultdict(list)
for fp in all_fps:
    fp_by_type[fp['type']].append(fp)

print(f"\nBy field type:")
for ft, fields in sorted(fp_by_type.items(), key=lambda x: -len(x[1])):
    print(f"  {ft}: {len(fields)}")

# By area
print(f"\nBy area (sq pts):")
tiny = [f for f in all_fps if f['area'] < 50]
small = [f for f in all_fps if 50 <= f['area'] < 200]
medium = [f for f in all_fps if 200 <= f['area'] < 1000]
large = [f for f in all_fps if f['area'] >= 1000]
print(f"  Tiny (<50): {len(tiny)}")
print(f"  Small (50-200): {len(small)}")
print(f"  Medium (200-1000): {len(medium)}")
print(f"  Large (>1000): {len(large)}")

# By name pattern
print(f"\nBy name pattern:")
check_box_pattern = [f for f in all_fps if 'Check Box' in f['name']]
fill_field_pattern = [f for f in all_fps if 'Fill_Field' in f['name'] or 'TextField' in f['name']]
auto_generated = [f for f in all_fps if f['name'].startswith('field_') or f['name'].startswith('text_') or f['name'].startswith('cb_')]
other = [f for f in all_fps if f not in check_box_pattern and f not in fill_field_pattern and f not in auto_generated]

print(f"  'Check Box N' pattern: {len(check_box_pattern)}")
print(f"  'Fill_Field/TextField' pattern: {len(fill_field_pattern)}")
print(f"  Auto-generated (field_/text_/cb_): {len(auto_generated)}")
print(f"  Other: {len(other)}")

# Sample FPs
print(f"\n=== SAMPLE FALSE POSITIVES ===")
for i, fp in enumerate(all_fps[:20]):
    print(f"  {fp['source']}: name='{fp['name']}' type={fp['type']} area={fp['area']:.0f}")

# Analyze FNs
print(f"\n=== FALSE NEGATIVES ANALYSIS ===")

# By field type
fn_by_type = defaultdict(list)
for fn in all_fns:
    fn_by_type[fn['type']].append(fn)

print(f"\nBy field type:")
for ft, fields in sorted(fn_by_type.items(), key=lambda x: -len(x[1])):
    print(f"  {ft}: {len(fields)}")

# By area
print(f"\nBy area (sq pts):")
tiny = [f for f in all_fns if f['area'] < 50]
small = [f for f in all_fns if 50 <= f['area'] < 200]
medium = [f for f in all_fns if 200 <= f['area'] < 1000]
large = [f for f in all_fns if f['area'] >= 1000]
print(f"  Tiny (<50): {len(tiny)}")
print(f"  Small (50-200): {len(small)}")
print(f"  Medium (200-1000): {len(medium)}")
print(f"  Large (>1000): {len(large)}")

# Sample FNs
print(f"\n=== SAMPLE FALSE NEGATIVES ===")
for i, fn in enumerate(all_fns[:20]):
    r = fn['rect']
    print(f"  {fn['source']}: name='{fn['name']}' type={fn['type']} area={fn['area']:.0f} rect=({r.x0:.0f},{r.y0:.0f},{r.x1:.0f},{r.y1:.0f})")

# By source PDF
print(f"\n=== BY SOURCE PDF ===")
fp_by_source = defaultdict(int)
fn_by_source = defaultdict(int)
tp_by_source = defaultdict(int)

for fp in all_fps:
    fp_by_source[fp['source']] += 1
for fn in all_fns:
    fn_by_source[fn['source']] += 1
for tp in all_tps:
    tp_by_source[tp['source']] += 1

for pdf_id, _, _ in test_pairs:
    tp = tp_by_source[pdf_id]
    fp = fp_by_source[pdf_id]
    fn = fn_by_source[pdf_id]
    p = tp / (tp + fp) if (tp + fp) > 0 else 0
    r = tp / (tp + fn) if (tp + fn) > 0 else 0
    f = 2*p*r/(p+r) if (p+r) > 0 else 0
    print(f"  {pdf_id}: TP={tp}, FP={fp}, FN={fn}, P={p*100:.1f}%, R={r*100:.1f}%, F1={f*100:.1f}%")
