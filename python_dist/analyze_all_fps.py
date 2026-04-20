#!/usr/bin/env python3
"""
Analyze FP patterns across all failing PDFs to find safe filtering rules.
"""

import fitz
import os
import re
from collections import defaultdict

OUTPUT_DIR = '/tmp/v22_all_output'
GT_DIR = '/Users/36981/Desktop/PDFTest/PDFs to test/Fillable PDFs'

# PDFs with FP issues (too many false positives)
FP_PDFS = [
    ('53252_DTE_EEA_Field_Inspection_Report_v11_v22_output.pdf', '53252_DTE_EEA_Field_Inspection_Report_v14_Web_Release_Fillable.pdf'),
    ('57769_CH_Res_NYS_Clean_Heat_Completion_Acknowledgement_Form_v2_v22_output.pdf', '57769_CH_Res_NYS_Clean_Heat_Completion_Acknowledgement_Form_v3_Web_Release_fillable.pdf'),
    ('56380_DTE_2026_PartnerGuidelines_v18_v22_output.pdf', '56380_DTE_2026_PartnerGuidelines_v23_Web_Release_Fillable.pdf'),
    ('9787_ConEd_Res_HVAC_Gas_Rebate_Appl_v01_FINAL_RELEASE_v22_output.pdf', '9787_ConEd_Res_HVAC_Gas_Rebate_Appl_v01_FINAL_RELEASE_Fillable_Locked.pdf'),
    ('32775_DTE_2022_HVAC_Customer_SelfSubmission_Application_v01_v22_output.pdf', '32775_DTE_2022_HVAC_Customer_SelfSubmission_Application_v01_Web_Release_Fillable.pdf'),
    ('14792_ConEd_Gas_HVAC_Tune-Up_Contractor_Application_v03_FINAL_RELEASE_Web_v22_output.pdf', '14792_ConEd_Gas_HVAC_Tune-Up_Contractor_Application_v03_FINAL_RELEASE_Web_Fillable.pdf'),
    ('57561_DTE_INSUL_HP_Rebate_Worksheet_v1_v22_output.pdf', '57561_DTE_INSUL_HP_Rebate_Worksheet_v1_Web_Release_Fillable.pdf'),
    ('29174_DTE_INWIN_Field_Inspection_Report_Form_v2_v22_output.pdf', '29174_DTE_INWIN_Field_Inspection_Report_Form_v3_WEB_RELEASE_fillable.pdf'),
]

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
    gt_area = gt['area']
    if gt_area < 100:
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
                        bbox = fitz.Rect(span["bbox"])
                        blocks.append({'text': text, 'rect': bbox})
    return blocks

def rect_overlap_area(r1, r2):
    x1 = max(r1.x0, r2.x0)
    y1 = max(r1.y0, r2.y0)
    x2 = min(r1.x1, r2.x1)
    y2 = min(r1.y1, r2.y1)
    if x2 <= x1 or y2 <= y1:
        return 0
    return (x2 - x1) * (y2 - y1)

def field_text_overlap_ratio(field_rect, text_blocks):
    field_area = field_rect.width * field_rect.height
    if field_area == 0:
        return 0
    total_overlap = 0
    for block in text_blocks:
        overlap = rect_overlap_area(field_rect, block['rect'])
        total_overlap += overlap
    return min(1.0, total_overlap / field_area)

def get_fields_with_details(pdf_path):
    doc = fitz.open(pdf_path)
    fields = []
    for page_idx, page in enumerate(doc):
        text_blocks = get_text_blocks(page)
        for widget in page.widgets():
            name = widget.field_name or ''
            ftype = widget.field_type_string
            area = widget.rect.width * widget.rect.height
            overlap_ratio = field_text_overlap_ratio(widget.rect, text_blocks)
            fields.append({
                'name': name,
                'rect': widget.rect,
                'page': page_idx,
                'type': ftype,
                'area': area,
                'overlap': overlap_ratio,
                'width': widget.rect.width,
                'height': widget.rect.height
            })
    doc.close()
    return fields

# Collect all FPs across PDFs
all_fps = []

for v22_name, gt_name in FP_PDFS:
    v22_path = os.path.join(OUTPUT_DIR, v22_name)
    gt_path = os.path.join(GT_DIR, gt_name)
    
    if not os.path.exists(v22_path) or not os.path.exists(gt_path):
        print(f"Missing: {v22_name}")
        continue
    
    v22_fields = get_fields_with_details(v22_path)
    gt_fields = get_fields_with_details(gt_path)
    
    # Find unmatched v22 fields (FPs)
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
    
    for i, f in enumerate(v22_fields):
        if i not in matched_v22:
            f['pdf'] = v22_name.split('_')[0]
            all_fps.append(f)

print(f"Total FPs across all PDFs: {len(all_fps)}")
print()

# Group by type
by_type = defaultdict(list)
for fp in all_fps:
    by_type[fp['type']].append(fp)

print("By field type:")
for ftype, fields in sorted(by_type.items(), key=lambda x: -len(x[1])):
    print(f"  {ftype}: {len(fields)}")

print("\n" + "="*70)
print("ANALYZING PATTERNS IN FPs")
print("="*70)

# Analyze Text FPs
text_fps = by_type.get('Text', [])
print(f"\n--- TEXT FPs ({len(text_fps)}) ---")

# Check overlap distribution
high_overlap = [f for f in text_fps if f['overlap'] > 0.5]
print(f"High text overlap (>50%): {len(high_overlap)}")

# Check colon patterns
with_colon = [f for f in text_fps if ':' in f['name']]
print(f"Name contains colon: {len(with_colon)}")

multi_colon = [f for f in text_fps if f['name'].count(':') >= 2]
print(f"Name contains 2+ colons: {len(multi_colon)}")

# Check area
large_area = [f for f in text_fps if f['area'] > 5000]
print(f"Large area (>5000): {len(large_area)}")

small_area = [f for f in text_fps if f['area'] < 200]
print(f"Small area (<200): {len(small_area)}")

# Check aspect ratio
wide = [f for f in text_fps if f['width'] > 200]
print(f"Wide (>200px): {len(wide)}")

tall = [f for f in text_fps if f['height'] > 50]
print(f"Tall (>50px): {len(tall)}")

# Sample some patterns
print("\n--- SAMPLE TEXT FPs (high overlap) ---")
for fp in sorted(high_overlap, key=lambda x: -x['overlap'])[:10]:
    print(f"  {fp['pdf']}: '{fp['name'][:40]}' overlap={fp['overlap']:.0%} area={fp['area']:.0f}")

# Analyze Checkbox FPs
cb_fps = by_type.get('CheckBox', [])
print(f"\n--- CHECKBOX FPs ({len(cb_fps)}) ---")

# Check patterns
check_box_n = [f for f in cb_fps if re.match(r'^Check Box \d+$', f['name'])]
print(f"'Check Box N' pattern: {len(check_box_n)}")

numeric_only = [f for f in cb_fps if re.match(r'^[\d,.\s]+$', f['name'])]
print(f"Numeric-only names: {len(numeric_only)}")

long_names = [f for f in cb_fps if len(f['name']) > 30]
print(f"Long names (>30 chars): {len(long_names)}")

# Sample
print("\n--- SAMPLE CHECKBOX FPs ---")
for fp in cb_fps[:15]:
    print(f"  {fp['pdf']}: '{fp['name'][:50]}' area={fp['area']:.0f}")

# Look for patterns that are unique to FPs
print("\n" + "="*70)
print("POTENTIAL NEW RULES")
print("="*70)

# Rule candidates
rules = {
    'text_high_overlap_50': [f for f in text_fps if f['overlap'] > 0.5],
    'text_high_overlap_60': [f for f in text_fps if f['overlap'] > 0.6],
    'text_high_overlap_70': [f for f in text_fps if f['overlap'] > 0.7],
    'text_multi_colon': [f for f in text_fps if f['name'].count(':') >= 2],
    'text_single_colon_overlap': [f for f in text_fps if ':' in f['name'] and f['overlap'] > 0.3],
    'text_wide_200': [f for f in text_fps if f['width'] > 200],
    'text_wide_300': [f for f in text_fps if f['width'] > 300],
    'cb_check_box_n': check_box_n,
    'cb_numeric_only': numeric_only,
    'text_very_large': [f for f in text_fps if f['area'] > 10000],
    'text_contains_question': [f for f in text_fps if '?' in f['name']],
}

print("\nRule candidates (count of FPs that would be filtered):")
for rule, fields in sorted(rules.items(), key=lambda x: -len(x[1])):
    if len(fields) > 0:
        print(f"  {rule}: {len(fields)}")
