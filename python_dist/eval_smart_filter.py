#!/usr/bin/env python3
"""
Evaluate v22 with smart text-overlap-aware post-processing.
"""

import fitz
import os
import re
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

def get_text_blocks(page, include_underlines=False):
    """Get all text blocks on a page.
    
    Args:
        page: PyMuPDF page object
        include_underlines: If False, ignore text that is mostly underscores
                           (these are form fill lines, not labels)
    """
    blocks = []
    text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    
    for block in text_dict.get("blocks", []):
        if block.get("type") == 0:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "").strip()
                    if text:
                        # Skip underline-only text (form fill lines)
                        if not include_underlines:
                            non_underscore = text.replace('_', '').replace('-', '').strip()
                            # If text is mostly underscores (>70%), skip it
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
    """Calculate what fraction of field area overlaps with text."""
    field_area = field_rect.width * field_rect.height
    if field_area == 0:
        return 0
    
    total_overlap = 0
    for block in text_blocks:
        overlap = rect_overlap_area(field_rect, block['rect'])
        total_overlap += overlap
    
    return min(1.0, total_overlap / field_area)

def should_filter_field(name, ftype, area, overlap_ratio):
    """
    Determine if a field should be filtered based on rules.
    Returns (should_filter, reason)
    """
    
    # Rule 1: Check Box N pattern (always filter)
    if re.match(r'^Check Box \d+$', name):
        return True, 'check_box_numbered'
    
    # Rule 2: _Stack fields (aggregated labels)
    if '_Stack' in name:
        return True, 'stack_field'
    
    # Rule 3: Multiple colons + text overlap (merged labels)
    colon_count = name.count(':')
    if colon_count >= 2 and ftype == 'Text' and overlap_ratio > 0.2:
        return True, 'multi_colon_overlap'
    
    # Rule 4: Very high text overlap for text fields (>85%)
    # Be more conservative to avoid TP loss
    if ftype == 'Text' and overlap_ratio > 0.85 and area > 500:
        return True, 'high_text_overlap'
    
    # Rule 5: Large text field with very high overlap (>7000 area, >40% overlap)
    if ftype == 'Text' and area > 7000 and overlap_ratio > 0.4:
        return True, 'large_text_overlap'
    
    # Rule 6: Known header patterns
    header_patterns = [
        r'service territory',
        r'Proposed project',
        r'^Efficiency Rating',
        r'Disadvantaged',
    ]
    for pattern in header_patterns:
        if re.search(pattern, name, re.I) and ftype == 'Text':
            return True, 'header_pattern'
    
    # Rule 7: Question text (contains ?)
    if '?' in name and ftype == 'Text':
        return True, 'question_text'
    
    # Rule 8: Very large Text field with long multi-word name and 0% overlap
    # These are "ghost" fields in empty cells that v22 incorrectly names after nearby text
    if ftype == 'Text' and area > 10000 and len(name) > 15 and ' ' in name and overlap_ratio < 0.1:
        return True, 'large_descriptive_no_overlap'
    
    return False, ''

def get_fields_with_filtering(pdf_path, apply_filter=False):
    """Get fields from PDF with optional smart filtering."""
    doc = fitz.open(pdf_path)
    fields = []
    removed = []
    
    for page_idx, page in enumerate(doc):
        text_blocks = get_text_blocks(page) if apply_filter else []
        
        for widget in page.widgets():
            name = widget.field_name or ''
            ftype = widget.field_type_string
            area = widget.rect.width * widget.rect.height
            
            if apply_filter:
                overlap_ratio = field_text_overlap_ratio(widget.rect, text_blocks)
                should_remove, reason = should_filter_field(name, ftype, area, overlap_ratio)
                
                if should_remove:
                    removed.append({'name': name, 'reason': reason})
                    continue
            
            fields.append({
                'name': name,
                'rect': widget.rect,
                'page': page_idx,
                'type': ftype,
                'area': area
            })
    
    doc.close()
    return fields, removed

def get_gt_fields(pdf_path):
    """Get GT fields."""
    doc = fitz.open(pdf_path)
    fields = []
    for page_idx, page in enumerate(doc):
        for widget in page.widgets():
            fields.append({
                'name': widget.field_name or '',
                'rect': widget.rect,
                'page': page_idx,
                'type': widget.field_type_string
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
        
        if best_iou >= IOU_THRESHOLD and best_j >= 0:
            matched_pred.add(i)
            matched_gt.add(best_j)
    
    tp = len(matched_pred)
    fp = len(pred_fields) - tp
    fn = len(gt_fields) - len(matched_gt)
    
    return tp, fp, fn

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

# Evaluate BASELINE
print("="*70)
print("V22 BASELINE (no filtering)")
print("="*70)

total_tp = total_fp = total_fn = 0
for pdf_id, gt_name, v22_name in test_pairs:
    gt_path = os.path.join(gt_dir, gt_name)
    v22_path = os.path.join(v22_dir, v22_name)
    
    if not os.path.exists(gt_path) or not os.path.exists(v22_path):
        continue
    
    gt_fields = get_gt_fields(gt_path)
    v22_fields, _ = get_fields_with_filtering(v22_path, apply_filter=False)
    
    tp, fp, fn = match_fields(v22_fields, gt_fields)
    total_tp += tp
    total_fp += fp
    total_fn += fn
    
    p = tp/(tp+fp) if (tp+fp) > 0 else 0
    r = tp/(tp+fn) if (tp+fn) > 0 else 0
    f1 = 2*p*r/(p+r) if (p+r) > 0 else 0
    print(f"{pdf_id}: TP={tp}, FP={fp}, FN={fn}, P={100*p:.1f}%, R={100*r:.1f}%, F1={100*f1:.1f}%")

baseline_p = total_tp/(total_tp+total_fp) if (total_tp+total_fp) > 0 else 0
baseline_r = total_tp/(total_tp+total_fn) if (total_tp+total_fn) > 0 else 0
baseline_f1 = 2*baseline_p*baseline_r/(baseline_p+baseline_r) if (baseline_p+baseline_r) > 0 else 0

print(f"\nTOTAL: TP={total_tp}, FP={total_fp}, FN={total_fn}")
print(f"Precision: {100*baseline_p:.1f}%")
print(f"Recall: {100*baseline_r:.1f}%")
print(f"F1: {100*baseline_f1:.1f}%")

# Evaluate WITH SMART FILTERING
print("\n" + "="*70)
print("V22 WITH SMART TEXT-OVERLAP FILTERING")
print("="*70)

total_tp = total_fp = total_fn = 0
total_removed = 0
removal_reasons = defaultdict(int)

for pdf_id, gt_name, v22_name in test_pairs:
    gt_path = os.path.join(gt_dir, gt_name)
    v22_path = os.path.join(v22_dir, v22_name)
    
    if not os.path.exists(gt_path) or not os.path.exists(v22_path):
        continue
    
    gt_fields = get_gt_fields(gt_path)
    v22_fields, removed = get_fields_with_filtering(v22_path, apply_filter=True)
    
    total_removed += len(removed)
    for r in removed:
        removal_reasons[r['reason']] += 1
    
    tp, fp, fn = match_fields(v22_fields, gt_fields)
    total_tp += tp
    total_fp += fp
    total_fn += fn
    
    p = tp/(tp+fp) if (tp+fp) > 0 else 0
    r = tp/(tp+fn) if (tp+fn) > 0 else 0
    f1 = 2*p*r/(p+r) if (p+r) > 0 else 0
    print(f"{pdf_id}: TP={tp}, FP={fp}, FN={fn}, removed={len(removed)}, P={100*p:.1f}%, R={100*r:.1f}%, F1={100*f1:.1f}%")

new_p = total_tp/(total_tp+total_fp) if (total_tp+total_fp) > 0 else 0
new_r = total_tp/(total_tp+total_fn) if (total_tp+total_fn) > 0 else 0
new_f1 = 2*new_p*new_r/(new_p+new_r) if (new_p+new_r) > 0 else 0

print(f"\nTOTAL: TP={total_tp}, FP={total_fp}, FN={total_fn}")
print(f"Total removed: {total_removed}")
print(f"Precision: {100*new_p:.1f}%")
print(f"Recall: {100*new_r:.1f}%")
print(f"F1: {100*new_f1:.1f}%")

print(f"\nRemoval reasons:")
for reason, count in sorted(removal_reasons.items(), key=lambda x: -x[1]):
    print(f"  {reason}: {count}")

print(f"\n{'='*70}")
print(f"IMPROVEMENT SUMMARY")
print(f"{'='*70}")
print(f"Precision: {100*baseline_p:.1f}% → {100*new_p:.1f}% ({100*(new_p-baseline_p):+.1f}%)")
print(f"Recall:    {100*baseline_r:.1f}% → {100*new_r:.1f}% ({100*(new_r-baseline_r):+.1f}%)")
print(f"F1:        {100*baseline_f1:.1f}% → {100*new_f1:.1f}% ({100*(new_f1-baseline_f1):+.1f}%)")
