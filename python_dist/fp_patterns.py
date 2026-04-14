#!/usr/bin/env python3
"""Analyze FP patterns to find safe filtering rules"""

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
    
    fps = [pred_fields[i] for i in range(len(pred_fields)) if i not in matched_pred]
    return fps

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

all_fps = []

for pdf_id, gt_name, v22_name in test_pairs:
    gt_path = os.path.join(gt_dir, gt_name)
    v22_path = os.path.join(v22_dir, v22_name)
    
    if not os.path.exists(gt_path) or not os.path.exists(v22_path):
        continue
    
    gt_fields = get_fields(gt_path)
    v22_fields = get_fields(v22_path)
    
    fps = match_fields(v22_fields, gt_fields)
    
    for f in fps:
        f['source'] = pdf_id
        all_fps.append(f)

print(f"Total FPs: {len(all_fps)}\n")

# Pattern analysis
def categorize_fp(fp):
    name = fp['name']
    ftype = fp['type']
    area = fp['area']
    width = fp['width']
    height = fp['height']
    
    categories = []
    
    # Check Box N pattern
    if re.match(r'^Check Box \d+', name):
        categories.append('check_box_numbered')
    
    # Colon patterns (merged labels)
    if ':' in name and ftype == 'Text':
        colon_count = name.count(':')
        categories.append(f'colon_{colon_count}')
    
    # Stack pattern
    if '_Stack' in name:
        categories.append('stack_field')
    
    # Header-like patterns
    header_words = ['Proposed', 'HVAC', 'Efficiency', 'Measure', 'Rating', 
                    'Projections', 'Sales', 'Pipe', 'Insulation', 'Thermostat',
                    'TuneUp', 'Tune-Up', 'Program', 'Agreement', 'service territory',
                    'consumer promotions']
    if any(hw.lower() in name.lower() for hw in header_words) and ftype == 'Text':
        categories.append('header_like')
    
    # Per unit pattern
    if re.search(r'\d+ per unit', name, re.I):
        categories.append('per_unit')
    
    # Very large text fields (likely labels/headers)
    if ftype == 'Text' and area > 3000:
        categories.append('large_text')
    
    # Wide text fields (likely section headers)
    if ftype == 'Text' and width > 200:
        categories.append('wide_text')
    
    # Tiny checkbox (possibly FP)
    if ftype == 'CheckBox' and area < 50:
        categories.append('tiny_checkbox')
    
    # Numbered suffix pattern (_2, _3, etc.)
    if re.search(r'_\d+$', name):
        categories.append('numbered_suffix')
    
    if not categories:
        categories.append('other')
    
    return categories

# Categorize all FPs
category_counts = defaultdict(int)
category_examples = defaultdict(list)

for fp in all_fps:
    cats = categorize_fp(fp)
    for cat in cats:
        category_counts[cat] += 1
        if len(category_examples[cat]) < 5:
            category_examples[cat].append(fp)

print("FP Categories (count):")
for cat, count in sorted(category_counts.items(), key=lambda x: -x[1]):
    print(f"  {cat}: {count}")

print("\n" + "="*60)
print("Category Details with Examples:")
print("="*60)

for cat, count in sorted(category_counts.items(), key=lambda x: -x[1]):
    print(f"\n{cat}: {count} FPs")
    for ex in category_examples[cat][:3]:
        print(f"  - '{ex['name']}' type={ex['type']} area={ex['area']:.0f}")

# Find patterns that are EXCLUSIVELY FPs (not TPs)
print("\n" + "="*60)
print("Potential Safe Filtering Rules:")
print("="*60)

# 1. Check colon patterns
print(f"\n1. Colon patterns (merged labels): {category_counts.get('colon_1', 0) + category_counts.get('colon_2', 0) + category_counts.get('colon_3', 0)}")

# 2. Stack fields
print(f"2. Stack fields (_Stack): {category_counts.get('stack_field', 0)}")

# 3. Large text (>3000 sq pts)
print(f"3. Large text fields (>3000): {category_counts.get('large_text', 0)}")

# 4. Per unit patterns
print(f"4. 'per unit' patterns: {category_counts.get('per_unit', 0)}")

# 5. Wide text (>200 width)
print(f"5. Very wide text (>200): {category_counts.get('wide_text', 0)}")

# 6. Check Box N
print(f"6. 'Check Box N' pattern: {category_counts.get('check_box_numbered', 0)}")
