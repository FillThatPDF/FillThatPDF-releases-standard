#!/usr/bin/env python3
"""
Batch process all static PDFs and evaluate against GT with adaptive matching.
"""

import fitz
import os
import re
import subprocess
import sys
from collections import defaultdict

# Paths
STATIC_DIR = '/Users/36981/Desktop/PDFTest/PDFs to test/Static PDFs'
GT_DIR = '/Users/36981/Desktop/PDFTest/PDFs to test/Fillable PDFs'
OUTPUT_DIR = '/tmp/v22_batch_output'
V22_SCRIPT = '/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/FillThatPDF_Optimized_v7/python_dist/smart_fillable_v22.py'

# Mapping of static PDFs to their GT counterparts (by ID prefix)
PDF_MAPPINGS = {
    '11691': ('11691_ConEd_Distributor_Application_Form_v10.pdf', 
              '11691_ConEd_Distributor_Application_Form_v10_fillable_RELEASE.pdf'),
    '14792': ('14792_ConEd_Gas_HVAC_Tune-Up_Contractor_Application_v03_FINAL_RELEASE_Web.pdf',
              '14792_ConEd_Gas_HVAC_Tune-Up_Contractor_Application_v03_FINAL_RELEASE_Web_Fillable.pdf'),
    '29174': ('29174_DTE_INWIN_Field_Inspection_Report_Form_v2.pdf',
              '29174_DTE_INWIN_Field_Inspection_Report_Form_v3_WEB_RELEASE_fillable.pdf'),
    '32775': ('32775_DTE_2022_HVAC_Customer_SelfSubmission_Application_v01.pdf',
              '32775_DTE_2022_HVAC_Customer_SelfSubmission_Application_v01_Web_Release_Fillable.pdf'),
    '34062': ('34062_DTE_HVAC_RES_Contractor_Agreement_v01.pdf',
              '34062_DTE_HVAC_RES_Contractor_Agreement_v01_Fillable_Release.pdf'),
    '53252': ('53252_DTE_EEA_Field_Inspection_Report_v11.pdf',
              '53252_DTE_EEA_Field_Inspection_Report_v14_Web_Release_Fillable.pdf'),
    '55570': ('55570_DTE_SEEL_Contractor_Onboarding _Packet_v26.pdf',
              '55570_DTE_SEEL_Contractor_Onboarding _Packet_v26_fillable.pdf'),
    '56380': ('56380_DTE_2026_PartnerGuidelines_v18.pdf',
              '56380_DTE_2026_PartnerGuidelines_v23_Web_Release_Fillable.pdf'),
    '57026': ('57026_Avangrid_Clean_Heat_2026_BE_Incentive_Application_v05.pdf',
              '57026_Avangrid_Clean_Heat_2026_BE_Incentive_Application_Fillable_v05_Release_Web_Fillable.pdf'),
    '57561_TC': ('57561_DTE_INSUL_HP_Program_TC_v1.pdf',
                 '57561_DTE_INSUL_HP_Program_TC_v1_Web_Release_Fillable.pdf'),
    '57561_WS': ('57561_DTE_INSUL_HP_Rebate_Worksheet_v1.pdf',
                 '57561_DTE_INSUL_HP_Rebate_Worksheet_v1_Web_Release_Fillable.pdf'),
    '57618': ('57618_NGRID_New_York_Ack_Form_Fillable_v07.pdf',
              '57618_NGRID_New_York_Ack_Form_Fillable_v07_Release_Web_Fillable.pdf'),
    '57769': ('57769_CH_Res_NYS_Clean_Heat_Completion_Acknowledgement_Form_v2.pdf',
              '57769_CH_Res_NYS_Clean_Heat_Completion_Acknowledgement_Form_v3_Web_Release_fillable.pdf'),
    '9787': ('9787_ConEd_Res_HVAC_Gas_Rebate_Appl_v01_FINAL_RELEASE.pdf',
             '9787_ConEd_Res_HVAC_Gas_Rebate_Appl_v01_FINAL_RELEASE_Fillable_Locked.pdf'),
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
    """Adaptive matching - use distance for tiny fields, IoU for larger ones."""
    gt_area = gt['area']
    if gt_area < 100:
        dist = center_distance(pred['rect'], gt['rect'])
        if dist <= 40:
            return True
    iou = calc_iou(pred['rect'], gt['rect'])
    return iou >= 0.3

def get_text_blocks(page):
    """Get text blocks, excluding underlines."""
    blocks = []
    text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    for block in text_dict.get("blocks", []):
        if block.get("type") == 0:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "").strip()
                    if text:
                        non_underscore = text.replace('_', '').replace('-', '').strip()
                        if len(non_underscore) >= len(text) * 0.3:
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

def should_filter_field(name, ftype, area, overlap_ratio):
    """Determine if field should be filtered out."""
    # Rule 1: Check Box N pattern
    if re.match(r'^Check Box \d+$', name):
        return True, 'check_box_numbered'
    
    # Rule 2: _Stack fields
    if '_Stack' in name:
        return True, 'stack_field'
    
    # Rule 3: Multiple colons + text overlap
    if name.count(':') >= 2 and ftype == 'Text' and overlap_ratio > 0.2:
        return True, 'multi_colon_overlap'
    
    # Rule 4: Very high text overlap
    if ftype == 'Text' and overlap_ratio > 0.85 and area > 500:
        return True, 'high_text_overlap'
    
    # Rule 5: Large text with high overlap
    if ftype == 'Text' and area > 7000 and overlap_ratio > 0.4:
        return True, 'large_text_overlap'
    
    # Rule 6: Known header patterns
    header_patterns = [r'service territory', r'Proposed project', r'^Efficiency Rating', r'Disadvantaged']
    for pattern in header_patterns:
        if re.search(pattern, name, re.I) and ftype == 'Text':
            return True, 'header_pattern'
    
    # Rule 7: Question text
    if '?' in name and ftype == 'Text':
        return True, 'question_text'
    
    # Rule 8: Large descriptive with no overlap
    if ftype == 'Text' and area > 10000 and len(name) > 15 and ' ' in name and overlap_ratio < 0.1:
        return True, 'large_descriptive_no_overlap'
    
    return False, ''

def get_fields_filtered(pdf_path):
    """Get fields from PDF with filtering applied."""
    doc = fitz.open(pdf_path)
    fields = []
    filtered_count = defaultdict(int)
    
    for page_idx, page in enumerate(doc):
        text_blocks = get_text_blocks(page)
        
        for widget in page.widgets():
            name = widget.field_name or ''
            ftype = widget.field_type_string
            area = widget.rect.width * widget.rect.height
            overlap_ratio = field_text_overlap_ratio(widget.rect, text_blocks)
            
            should_remove, reason = should_filter_field(name, ftype, area, overlap_ratio)
            if should_remove:
                filtered_count[reason] += 1
                continue
            
            fields.append({
                'name': name,
                'rect': widget.rect,
                'page': page_idx,
                'type': ftype,
                'area': area
            })
    
    doc.close()
    return fields, filtered_count

def get_gt_fields(pdf_path):
    """Get GT fields (no filtering)."""
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

def match_fields(pred_fields, gt_fields):
    """Match predictions to GT using adaptive matching."""
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

def process_pdf(static_name, gt_name, pdf_id):
    """Process a single PDF and return metrics."""
    static_path = os.path.join(STATIC_DIR, static_name)
    gt_path = os.path.join(GT_DIR, gt_name)
    output_path = os.path.join(OUTPUT_DIR, f'{pdf_id}_v22_output.pdf')
    
    if not os.path.exists(static_path):
        return None, f"Static PDF not found: {static_name}"
    if not os.path.exists(gt_path):
        return None, f"GT PDF not found: {gt_name}"
    
    # Run v22
    try:
        result = subprocess.run(
            ['python3', V22_SCRIPT, static_path, output_path],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            return None, f"v22 failed: {result.stderr[:200]}"
    except subprocess.TimeoutExpired:
        return None, "v22 timeout"
    except Exception as e:
        return None, f"v22 error: {str(e)}"
    
    if not os.path.exists(output_path):
        return None, "v22 output not created"
    
    # Get fields
    pred_fields, filtered = get_fields_filtered(output_path)
    gt_fields = get_gt_fields(gt_path)
    
    # Match
    tp, fp, fn = match_fields(pred_fields, gt_fields)
    
    p = tp / (tp + fp) if (tp + fp) > 0 else 0
    r = tp / (tp + fn) if (tp + fn) > 0 else 0
    f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0
    
    return {
        'pdf_id': pdf_id,
        'tp': tp, 'fp': fp, 'fn': fn,
        'precision': p, 'recall': r, 'f1': f1,
        'pred_count': len(pred_fields),
        'gt_count': len(gt_fields),
        'filtered': dict(filtered)
    }, None

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    results = []
    print("=" * 80)
    print("BATCH EVALUATION: Static PDFs → v22 → Compare with GT")
    print("=" * 80)
    print()
    
    for pdf_id, (static_name, gt_name) in sorted(PDF_MAPPINGS.items()):
        print(f"Processing {pdf_id}...", end=" ", flush=True)
        result, error = process_pdf(static_name, gt_name, pdf_id)
        
        if error:
            print(f"ERROR: {error}")
            continue
        
        results.append(result)
        status = "✅ PASS" if result['f1'] >= 0.86 else "❌ FAIL"
        print(f"{status} F1={result['f1']*100:.1f}% (TP={result['tp']}, FP={result['fp']}, FN={result['fn']})")
    
    print()
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    
    # Sort by F1
    results.sort(key=lambda x: x['f1'], reverse=True)
    
    passing = [r for r in results if r['f1'] >= 0.86]
    failing = [r for r in results if r['f1'] < 0.86]
    
    print(f"\nPASSING (≥86%): {len(passing)}/{len(results)}")
    for r in passing:
        print(f"  {r['pdf_id']}: F1={r['f1']*100:.1f}% P={r['precision']*100:.1f}% R={r['recall']*100:.1f}%")
    
    print(f"\nFAILING (<86%): {len(failing)}/{len(results)}")
    for r in failing:
        print(f"  {r['pdf_id']}: F1={r['f1']*100:.1f}% P={r['precision']*100:.1f}% R={r['recall']*100:.1f}% (TP={r['tp']}, FP={r['fp']}, FN={r['fn']})")
    
    # Overall stats
    total_tp = sum(r['tp'] for r in results)
    total_fp = sum(r['fp'] for r in results)
    total_fn = sum(r['fn'] for r in results)
    overall_p = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0
    overall_r = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0
    overall_f1 = 2 * overall_p * overall_r / (overall_p + overall_r) if (overall_p + overall_r) > 0 else 0
    
    print(f"\nOVERALL: TP={total_tp}, FP={total_fp}, FN={total_fn}")
    print(f"         P={overall_p*100:.1f}%, R={overall_r*100:.1f}%, F1={overall_f1*100:.1f}%")
    
    return results

if __name__ == '__main__':
    main()
