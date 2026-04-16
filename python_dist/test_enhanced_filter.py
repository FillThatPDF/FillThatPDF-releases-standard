#!/usr/bin/env python3
"""
Test new filtering rules to see impact on F1 for each PDF.
Goal: Find rules that improve FPs without losing TPs.
"""

import fitz
import os
import re
from collections import defaultdict

OUTPUT_DIR = '/tmp/v22_all_output'
GT_DIR = '/Users/36981/Desktop/PDFTest/PDFs to test/Fillable PDFs'

# All test PDFs with GT mappings
PDFS = [
    ('53252_DTE_EEA_Field_Inspection_Report_v11_v22_output.pdf', '53252_DTE_EEA_Field_Inspection_Report_v14_Web_Release_Fillable.pdf'),
    ('55570_DTE_SEEL_Contractor_Onboarding _Packet_v26_v22_output.pdf', '55570_DTE_SEEL_Contractor_Onboarding _Packet_v26_fillable.pdf'),
    ('57618_NGRID_New_York_Ack_Form_Fillable_v07_v22_output.pdf', '57618_NGRID_New_York_Ack_Form_Fillable_v07_Release_Web_Fillable.pdf'),
    ('57769_CH_Res_NYS_Clean_Heat_Completion_Acknowledgement_Form_v2_v22_output.pdf', '57769_CH_Res_NYS_Clean_Heat_Completion_Acknowledgement_Form_v3_Web_Release_fillable.pdf'),
    ('56380_DTE_2026_PartnerGuidelines_v18_v22_output.pdf', '56380_DTE_2026_PartnerGuidelines_v23_Web_Release_Fillable.pdf'),
    ('9787_ConEd_Res_HVAC_Gas_Rebate_Appl_v01_FINAL_RELEASE_v22_output.pdf', '9787_ConEd_Res_HVAC_Gas_Rebate_Appl_v01_FINAL_RELEASE_Fillable_Locked.pdf'),
    ('32775_DTE_2022_HVAC_Customer_SelfSubmission_Application_v01_v22_output.pdf', '32775_DTE_2022_HVAC_Customer_SelfSubmission_Application_v01_Web_Release_Fillable.pdf'),
    ('14792_ConEd_Gas_HVAC_Tune-Up_Contractor_Application_v03_FINAL_RELEASE_Web_v22_output.pdf', '14792_ConEd_Gas_HVAC_Tune-Up_Contractor_Application_v03_FINAL_RELEASE_Web_Fillable.pdf'),
    ('57561_DTE_INSUL_HP_Rebate_Worksheet_v1_v22_output.pdf', '57561_DTE_INSUL_HP_Rebate_Worksheet_v1_Web_Release_Fillable.pdf'),
    ('57561_DTE_INSUL_HP_Program_TC_v1_v22_output.pdf', '57561_DTE_INSUL_HP_Program_TC_v1_Web_Release_Fillable.pdf'),
    ('29174_DTE_INWIN_Field_Inspection_Report_Form_v2_v22_output.pdf', '29174_DTE_INWIN_Field_Inspection_Report_Form_v3_WEB_RELEASE_fillable.pdf'),
    ('11691_ConEd_Distributor_Application_Form_v10_v22_output.pdf', '11691_ConEd_Distributor_Application_Form_v10_fillable_RELEASE.pdf'),
]

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

def should_filter_baseline(name, ftype, area, overlap, width, height):
    """Current baseline filtering."""
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

def should_filter_enhanced(name, ftype, area, overlap, width, height):
    """Enhanced filtering with additional rules."""
    # All baseline rules
    if should_filter_baseline(name, ftype, area, overlap, width, height):
        return True
    
    # TEST RULE: Only overlap > 70%
    if ftype == 'Text' and overlap > 0.7:
        return True
    
    return False

def get_fields(pdf_path, filter_fn=None):
    doc = fitz.open(pdf_path)
    fields = []
    for page_idx, page in enumerate(doc):
        text_blocks = get_text_blocks(page) if filter_fn else []
        for widget in page.widgets():
            name = widget.field_name or ''
            ftype = widget.field_type_string
            area = widget.rect.width * widget.rect.height
            overlap = field_text_overlap_ratio(widget.rect, text_blocks) if filter_fn else 0
            width = widget.rect.width
            height = widget.rect.height
            
            if filter_fn and filter_fn(name, ftype, area, overlap, width, height):
                continue
            
            fields.append({
                'name': name, 'rect': widget.rect, 'page': page_idx,
                'type': ftype, 'area': area
            })
    doc.close()
    return fields

def match_fields(pred_fields, gt_fields):
    matched_gt = set()
    matched_pred = set()
    
    for i, pred in enumerate(pred_fields):
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
    
    tp = len(matched_pred)
    fp = len(pred_fields) - tp
    fn = len(gt_fields) - len(matched_gt)
    return tp, fp, fn

def calc_f1(tp, fp, fn):
    p = tp/(tp+fp) if (tp+fp) > 0 else 0
    r = tp/(tp+fn) if (tp+fn) > 0 else 0
    return 2*p*r/(p+r) if (p+r) > 0 else 0

print("="*90)
print("COMPARING BASELINE vs ENHANCED FILTERING")
print("="*90)
print()
print(f"{'PDF':<45} {'Baseline F1':>12} {'Enhanced F1':>12} {'Change':>10}")
print("-"*90)

total_base = {'tp': 0, 'fp': 0, 'fn': 0}
total_enh = {'tp': 0, 'fp': 0, 'fn': 0}

for v22_name, gt_name in PDFS:
    v22_path = os.path.join(OUTPUT_DIR, v22_name)
    gt_path = os.path.join(GT_DIR, gt_name)
    
    if not os.path.exists(v22_path) or not os.path.exists(gt_path):
        continue
    
    gt_fields = get_fields(gt_path, filter_fn=None)
    
    # Baseline
    v22_base = get_fields(v22_path, filter_fn=should_filter_baseline)
    tp_b, fp_b, fn_b = match_fields(v22_base, gt_fields)
    f1_b = calc_f1(tp_b, fp_b, fn_b)
    
    # Enhanced
    v22_enh = get_fields(v22_path, filter_fn=should_filter_enhanced)
    tp_e, fp_e, fn_e = match_fields(v22_enh, gt_fields)
    f1_e = calc_f1(tp_e, fp_e, fn_e)
    
    total_base['tp'] += tp_b
    total_base['fp'] += fp_b
    total_base['fn'] += fn_b
    total_enh['tp'] += tp_e
    total_enh['fp'] += fp_e
    total_enh['fn'] += fn_e
    
    change = f1_e - f1_b
    status = "✓" if f1_e >= 0.86 else ""
    pdf_short = v22_name.split('_')[0]
    print(f"{pdf_short:<45} {f1_b*100:>11.1f}% {f1_e*100:>11.1f}% {change*100:>+9.1f}% {status}")

print("-"*90)

f1_b_total = calc_f1(total_base['tp'], total_base['fp'], total_base['fn'])
f1_e_total = calc_f1(total_enh['tp'], total_enh['fp'], total_enh['fn'])

print(f"{'TOTAL':<45} {f1_b_total*100:>11.1f}% {f1_e_total*100:>11.1f}% {(f1_e_total-f1_b_total)*100:>+9.1f}%")
print()
print(f"Baseline: TP={total_base['tp']}, FP={total_base['fp']}, FN={total_base['fn']}")
print(f"Enhanced: TP={total_enh['tp']}, FP={total_enh['fp']}, FN={total_enh['fn']}")
print(f"FP reduction: {total_base['fp'] - total_enh['fp']}")
print(f"TP change:    {total_enh['tp'] - total_base['tp']}")
