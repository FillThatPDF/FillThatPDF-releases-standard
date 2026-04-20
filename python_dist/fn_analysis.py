#!/usr/bin/env python3
"""Analyze FNs to understand what v22 is missing"""

import fitz
import os
from collections import defaultdict

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
                'area': widget.rect.width * widget.rect.height,
                'width': widget.rect.width,
                'height': widget.rect.height
            })
    doc.close()
    return fields

def match_fields(pred_fields, gt_fields):
    matched_gt = set()
    
    for pred in pred_fields:
        best_iou = 0
        best_j = -1
        for j, gt in enumerate(gt_fields):
            if j in matched_gt:
                continue
            iou = calc_iou(pred['rect'], gt['rect'])
            if iou > best_iou:
                best_iou = iou
                best_j = j
        
        if best_iou >= IOU_THRESHOLD and best_j >= 0:
            matched_gt.add(best_j)
    
    fns = [gt_fields[j] for j in range(len(gt_fields)) if j not in matched_gt]
    return fns

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

all_fns = []

for pdf_id, gt_name, v22_name in test_pairs:
    gt_path = os.path.join(gt_dir, gt_name)
    v22_path = os.path.join(v22_dir, v22_name)
    
    if not os.path.exists(gt_path) or not os.path.exists(v22_path):
        continue
    
    gt_fields = get_fields(gt_path)
    v22_fields = get_fields(v22_path)
    
    fns = match_fields(v22_fields, gt_fields)
    
    for f in fns:
        f['source'] = pdf_id
        all_fns.append(f)

print(f"Total FNs: {len(all_fns)}\n")

# Analyze by type
print("="*70)
print("FN ANALYSIS BY TYPE")
print("="*70)

by_type = defaultdict(list)
for fn in all_fns:
    by_type[fn['type']].append(fn)

for ftype, fields in sorted(by_type.items(), key=lambda x: -len(x[1])):
    print(f"\n{ftype}: {len(fields)} FNs ({100*len(fields)/len(all_fns):.1f}%)")
    
    # Area distribution
    tiny = [f for f in fields if f['area'] < 50]
    small = [f for f in fields if 50 <= f['area'] < 200]
    medium = [f for f in fields if 200 <= f['area'] < 1000]
    large = [f for f in fields if f['area'] >= 1000]
    
    print(f"  Area: tiny={len(tiny)}, small={len(small)}, medium={len(medium)}, large={len(large)}")
    
    # Sample
    print(f"  Samples:")
    for f in fields[:5]:
        r = f['rect']
        print(f"    - '{f['name'][:50]}' area={f['area']:.0f} rect=({r.x0:.0f},{r.y0:.0f},{r.x1:.0f},{r.y1:.0f}) src={f['source']}")

# Analyze by source PDF
print("\n" + "="*70)
print("FN ANALYSIS BY SOURCE PDF")
print("="*70)

by_source = defaultdict(list)
for fn in all_fns:
    by_source[fn['source']].append(fn)

for src in ['11691', '14792', '32775', '53252', '55570', '57618', '57769', '9787']:
    fields = by_source.get(src, [])
    if not fields:
        print(f"\n{src}: 0 FNs")
        continue
    
    print(f"\n{src}: {len(fields)} FNs")
    
    # By type
    types = defaultdict(int)
    for f in fields:
        types[f['type']] += 1
    
    type_str = ', '.join(f"{t}:{c}" for t, c in sorted(types.items(), key=lambda x: -x[1]))
    print(f"  Types: {type_str}")
    
    # Samples
    for f in fields[:3]:
        r = f['rect']
        print(f"  - '{f['name'][:50]}' type={f['type']} area={f['area']:.0f}")

# Special focus on RadioButtons (biggest FN category)
print("\n" + "="*70)
print("RADIOBUTTON FNs - DETAILED ANALYSIS")
print("="*70)

radio_fns = [f for f in all_fns if f['type'] == 'RadioButton']
print(f"\nTotal RadioButton FNs: {len(radio_fns)}")

# Check if they're all tiny
tiny_radio = [f for f in radio_fns if f['area'] < 50]
print(f"Tiny (<50 sq pts): {len(tiny_radio)} ({100*len(tiny_radio)/len(radio_fns):.1f}%)")

# Check dimension patterns
print("\nDimension patterns:")
dim_patterns = defaultdict(int)
for f in radio_fns:
    w = round(f['width'])
    h = round(f['height'])
    dim_patterns[(w, h)] += 1

for (w, h), count in sorted(dim_patterns.items(), key=lambda x: -x[1])[:10]:
    print(f"  {w}x{h}: {count}")

# By source
print("\nBy source PDF:")
radio_by_source = defaultdict(int)
for f in radio_fns:
    radio_by_source[f['source']] += 1

for src, count in sorted(radio_by_source.items(), key=lambda x: -x[1]):
    print(f"  {src}: {count}")
