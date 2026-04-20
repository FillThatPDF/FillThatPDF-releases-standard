#!/usr/bin/env python3
"""
Analyze FP patterns across failing PDFs to find safe filtering rules.
"""

import fitz
import os
import re
from collections import defaultdict

OUTPUT_DIR = '/tmp/v22_batch_output'
GT_DIR = '/Users/36981/Desktop/PDFTest/PDFs to test/Fillable PDFs'

# Failing PDFs with their GT
FAILING = {
    '53252': '53252_DTE_EEA_Field_Inspection_Report_v14_Web_Release_Fillable.pdf',
    '56380': '56380_DTE_2026_PartnerGuidelines_v23_Web_Release_Fillable.pdf',
    '57561_WS': '57561_DTE_INSUL_HP_Rebate_Worksheet_v1_Web_Release_Fillable.pdf',
    '29174': '29174_DTE_INWIN_Field_Inspection_Report_Form_v3_WEB_RELEASE_fillable.pdf',
    '32775': '32775_DTE_2022_HVAC_Customer_SelfSubmission_Application_v01_Web_Release_Fillable.pdf',
    '9787': '9787_ConEd_Res_HVAC_Gas_Rebate_Appl_v01_FINAL_RELEASE_Fillable_Locked.pdf',
    '14792': '14792_ConEd_Gas_HVAC_Tune-Up_Contractor_Application_v03_FINAL_RELEASE_Web_Fillable.pdf',
}

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

def center_distance(r1, r2):
    cx1 = (r1.x0 + r1.x1) / 2
    cy1 = (r1.y0 + r1.y1) / 2
    cx2 = (r2.x0 + r2.x1) / 2
    cy2 = (r2.y0 + r2.y1) / 2
    return ((cx1-cx2)**2 + (cy1-cy2)**2) ** 0.5

def adaptive_match(pred, gt):
    if gt['area'] < 100:
        if center_distance(pred['rect'], gt['rect']) <= 40:
            return True
    return calc_iou(pred['rect'], gt['rect']) >= 0.3

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

# Collect all FPs
all_fps = []
all_tps = []

for pdf_id, gt_name in FAILING.items():
    v22_path = os.path.join(OUTPUT_DIR, f'{pdf_id}_v22_output.pdf')
    gt_path = os.path.join(GT_DIR, gt_name)
    
    if not os.path.exists(v22_path) or not os.path.exists(gt_path):
        continue
    
    v22_fields = get_fields(v22_path)
    gt_fields = get_fields(gt_path)
    
    # Match
    matched_gt = set()
    matched_v22 = set()
    
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
            matched_v22.add(i)
            matched_gt.add(best_j)
    
    # Collect FPs and TPs
    for i, f in enumerate(v22_fields):
        f['pdf_id'] = pdf_id
        if i in matched_v22:
            all_tps.append(f)
        else:
            all_fps.append(f)

print(f"Total TPs: {len(all_tps)}")
print(f"Total FPs: {len(all_fps)}")
print()

# Analyze FP patterns
print("=" * 60)
print("FP PATTERN ANALYSIS")
print("=" * 60)

# By type
by_type = defaultdict(list)
for fp in all_fps:
    by_type[fp['type']].append(fp)

print("\nBy Type:")
for t, fps in sorted(by_type.items(), key=lambda x: -len(x[1])):
    print(f"  {t}: {len(fps)}")

# Check Box N pattern (should already be filtered)
cb_numbered = [f for f in all_fps if re.match(r'^Check Box \d+$', f['name'])]
print(f"\n'Check Box N' pattern: {len(cb_numbered)} FPs")

# Wide text fields
wide_text = [f for f in all_fps if f['type'] == 'Text' and f['width'] > 200]
print(f"Wide Text (>200px): {len(wide_text)} FPs")

# Tall text fields 
tall_text = [f for f in all_fps if f['type'] == 'Text' and f['height'] > 30]
print(f"Tall Text (>30px): {len(tall_text)} FPs")

# Large area
large_area = [f for f in all_fps if f['area'] > 5000]
print(f"Large area (>5000): {len(large_area)} FPs")

# Multi-word names
multi_word = [f for f in all_fps if len(f['name'].split()) >= 3]
print(f"3+ word names: {len(multi_word)} FPs")

# Names with numbers
has_numbers = [f for f in all_fps if any(c.isdigit() for c in f['name'])]
print(f"Names with numbers: {len(has_numbers)} FPs")

# Names with colons
has_colon = [f for f in all_fps if ':' in f['name']]
print(f"Names with colon: {len(has_colon)} FPs")

print("\n" + "=" * 60)
print("CHECKING RULE SAFETY (would these also remove TPs?)")
print("=" * 60)

# Check each potential rule
rules = [
    ('Check Box N', lambda f: re.match(r'^Check Box \d+$', f['name'])),
    ('Width > 300 (Text)', lambda f: f['type'] == 'Text' and f['width'] > 300),
    ('Width > 400 (Text)', lambda f: f['type'] == 'Text' and f['width'] > 400),
    ('Height > 40 (Text)', lambda f: f['type'] == 'Text' and f['height'] > 40),
    ('Area > 10000 (Text)', lambda f: f['type'] == 'Text' and f['area'] > 10000),
    ('3+ words (Text)', lambda f: f['type'] == 'Text' and len(f['name'].split()) >= 3),
    ('4+ words (Text)', lambda f: f['type'] == 'Text' and len(f['name'].split()) >= 4),
    ('5+ words (Text)', lambda f: f['type'] == 'Text' and len(f['name'].split()) >= 5),
    ('Contains colon (Text)', lambda f: f['type'] == 'Text' and ':' in f['name']),
]

for rule_name, rule_fn in rules:
    fp_match = len([f for f in all_fps if rule_fn(f)])
    tp_match = len([f for f in all_tps if rule_fn(f)])
    safe = "✅ SAFE" if tp_match == 0 else f"⚠️  LOSES {tp_match} TPs"
    print(f"  {rule_name}: removes {fp_match} FPs, {safe}")

# Sample FPs
print("\n" + "=" * 60)
print("SAMPLE FPs (Text type)")
print("=" * 60)
text_fps = [f for f in all_fps if f['type'] == 'Text']
for fp in text_fps[:30]:
    print(f"{fp['pdf_id']}: '{fp['name'][:50]}' {fp['width']:.0f}x{fp['height']:.0f} area={fp['area']:.0f}")
