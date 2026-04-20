#!/usr/bin/env python3
"""
Analyze FN RadioButtons to understand why v22 misses them.
"""

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
                'area': widget.rect.width * widget.rect.height
            })
    doc.close()
    return fields

def find_fn_radios():
    """Find all FN RadioButtons and analyze them."""
    
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
    
    fn_radios = []
    
    for pdf_id, gt_name, v22_name in test_pairs:
        gt_path = os.path.join(gt_dir, gt_name)
        v22_path = os.path.join(v22_dir, v22_name)
        
        if not os.path.exists(gt_path) or not os.path.exists(v22_path):
            continue
        
        gt_fields = get_fields(gt_path)
        v22_fields = get_fields(v22_path)
        
        # Find matched GT fields
        matched_gt = set()
        for pred in v22_fields:
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
        
        # Find unmatched GT RadioButtons
        for j, gt in enumerate(gt_fields):
            if j not in matched_gt and gt['type'] == 'RadioButton':
                fn_radios.append({
                    'pdf_id': pdf_id,
                    'name': gt['name'],
                    'rect': gt['rect'],
                    'page': gt['page'],
                    'area': gt['area'],
                    'width': gt['rect'].width,
                    'height': gt['rect'].height
                })
    
    return fn_radios

# Analyze
fn_radios = find_fn_radios()

print(f"Total FN RadioButtons: {len(fn_radios)}")
print()

# Group by PDF
by_pdf = defaultdict(list)
for r in fn_radios:
    by_pdf[r['pdf_id']].append(r)

print("By PDF:")
for pdf_id, radios in sorted(by_pdf.items()):
    print(f"  {pdf_id}: {len(radios)}")

print()

# Analyze sizes
areas = [r['area'] for r in fn_radios]
widths = [r['width'] for r in fn_radios]
heights = [r['height'] for r in fn_radios]

print("Size analysis:")
print(f"  Area: min={min(areas):.1f}, max={max(areas):.1f}, avg={sum(areas)/len(areas):.1f}")
print(f"  Width: min={min(widths):.1f}, max={max(widths):.1f}, avg={sum(widths)/len(widths):.1f}")
print(f"  Height: min={min(heights):.1f}, max={max(heights):.1f}, avg={sum(heights)/len(heights):.1f}")

# Size distribution
print()
print("Area distribution:")
buckets = [0, 50, 100, 200, 500, 1000, 10000]
for i in range(len(buckets)-1):
    count = len([a for a in areas if buckets[i] <= a < buckets[i+1]])
    print(f"  {buckets[i]}-{buckets[i+1]}: {count}")
count = len([a for a in areas if areas[-1] >= buckets[-1]])
print(f"  >{buckets[-1]}: {count}")

# Sample some FN RadioButtons
print()
print("Sample FN RadioButtons:")
for r in fn_radios[:20]:
    print(f"  {r['pdf_id']} p{r['page']}: '{r['name'][:40]}' at ({r['rect'].x0:.0f},{r['rect'].y0:.0f})-({r['rect'].x1:.0f},{r['rect'].y1:.0f}), {r['width']:.1f}x{r['height']:.1f}")

# Check how many might be partially matched (low IoU)
print()
print("\nChecking if v22 has NEARBY detections (within 50px) for FN radios...")

for pdf_id, gt_name, v22_name in [
    ('11691', '11691_ConEd_Distributor_Application_Form_v10_fillable_RELEASE.pdf', '11691_v22_output.pdf'),
    ('53252', '53252_DTE_EEA_Field_Inspection_Report_v14_Web_Release_Fillable.pdf', '53252_v22_output.pdf'),
]:
    gt_dir = '/Users/36981/Desktop/PDFTest/PDFs to test/Fillable PDFs'
    v22_dir = '/tmp/v22_output'
    
    gt_path = os.path.join(gt_dir, gt_name)
    v22_path = os.path.join(v22_dir, v22_name)
    
    if not os.path.exists(gt_path) or not os.path.exists(v22_path):
        continue
    
    gt_fields = get_fields(gt_path)
    v22_fields = get_fields(v22_path)
    
    print(f"\n{pdf_id}:")
    
    fn_for_pdf = [r for r in fn_radios if r['pdf_id'] == pdf_id][:5]
    for fn in fn_for_pdf:
        # Find nearest v22 field
        min_dist = float('inf')
        nearest = None
        
        for v22 in v22_fields:
            if v22['page'] != fn['page']:
                continue
            # Distance between centers
            fn_cx = (fn['rect'].x0 + fn['rect'].x1) / 2
            fn_cy = (fn['rect'].y0 + fn['rect'].y1) / 2
            v22_cx = (v22['rect'].x0 + v22['rect'].x1) / 2
            v22_cy = (v22['rect'].y0 + v22['rect'].y1) / 2
            dist = ((fn_cx - v22_cx)**2 + (fn_cy - v22_cy)**2) ** 0.5
            
            if dist < min_dist:
                min_dist = dist
                nearest = v22
        
        print(f"  GT: '{fn['name'][:30]}' ({fn['width']:.0f}x{fn['height']:.0f})")
        if nearest:
            iou = calc_iou(fn['rect'], nearest['rect'])
            print(f"     Nearest v22: '{nearest['name'][:30]}' ({nearest['type']}) dist={min_dist:.0f}, IoU={iou:.2f}")
        else:
            print(f"     No nearby v22 field")
