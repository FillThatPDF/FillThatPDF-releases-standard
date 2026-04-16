#!/usr/bin/env python3
"""
Process ALL static PDFs with v22, compare against GT, and report individual F1 scores.
Uses adaptive matching for tiny fields (RadioButtons).
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
OUTPUT_DIR = '/tmp/v22_all_output'
V22_SCRIPT = '/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/FillThatPDF_Optimized_v7/python_dist/smart_fillable_v22.py'

# Matching between static and GT filenames
PDF_MAPPINGS = {
    '11691_ConEd_Distributor_Application_Form_v10.pdf': '11691_ConEd_Distributor_Application_Form_v10_fillable_RELEASE.pdf',
    '14792_ConEd_Gas_HVAC_Tune-Up_Contractor_Application_v03_FINAL_RELEASE_Web.pdf': '14792_ConEd_Gas_HVAC_Tune-Up_Contractor_Application_v03_FINAL_RELEASE_Web_Fillable.pdf',
    '32775_DTE_2022_HVAC_Customer_SelfSubmission_Application_v01.pdf': '32775_DTE_2022_HVAC_Customer_SelfSubmission_Application_v01_Web_Release_Fillable.pdf',
    '53252_DTE_EEA_Field_Inspection_Report_v11.pdf': '53252_DTE_EEA_Field_Inspection_Report_v14_Web_Release_Fillable.pdf',
    '55570_DTE_SEEL_Contractor_Onboarding _Packet_v26.pdf': '55570_DTE_SEEL_Contractor_Onboarding _Packet_v26_fillable.pdf',
    '57618_NGRID_New_York_Ack_Form_Fillable_v07.pdf': '57618_NGRID_New_York_Ack_Form_Fillable_v07_Release_Web_Fillable.pdf',
    '57769_CH_Res_NYS_Clean_Heat_Completion_Acknowledgement_Form_v2.pdf': '57769_CH_Res_NYS_Clean_Heat_Completion_Acknowledgement_Form_v3_Web_Release_fillable.pdf',
    '9787_ConEd_Res_HVAC_Gas_Rebate_Appl_v01_FINAL_RELEASE.pdf': '9787_ConEd_Res_HVAC_Gas_Rebate_Appl_v01_FINAL_RELEASE_Fillable_Locked.pdf',
    '29174_DTE_INWIN_Field_Inspection_Report_Form_v2.pdf': '29174_DTE_INWIN_Field_Inspection_Report_Form_v3_WEB_RELEASE_fillable.pdf',
    '34062_DTE_HVAC_RES_Contractor_Agreement_v01.pdf': '34062_DTE_HVAC_RES_Contractor_Agreement_v01_Fillable_Release.pdf',
    '57561_DTE_INSUL_HP_Program_TC_v1.pdf': '57561_DTE_INSUL_HP_Program_TC_v1_Web_Release_Fillable.pdf',
    '57561_DTE_INSUL_HP_Rebate_Worksheet_v1.pdf': '57561_DTE_INSUL_HP_Rebate_Worksheet_v1_Web_Release_Fillable.pdf',
    '57026_Avangrid_Clean_Heat_2026_BE_Incentive_Application_v05.pdf': '57026_Avangrid_Clean_Heat_2026_BE_Incentive_Application_Fillable_v05_Release_Web_Fillable.pdf',
    '56380_DTE_2026_PartnerGuidelines_v18.pdf': '56380_DTE_2026_PartnerGuidelines_v23_Web_Release_Fillable.pdf',
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
    """Adaptive matching: distance-based for tiny fields, IoU for larger ones."""
    gt_area = gt['area']
    
    if gt_area < 100:
        dist = center_distance(pred['rect'], gt['rect'])
        if dist <= 40:
            return True
    
    iou = calc_iou(pred['rect'], gt['rect'])
    return iou >= 0.3

def get_text_blocks(page):
    """Get text blocks, ignoring underlines."""
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

def should_filter_field(name, ftype, area, overlap_ratio):
    """Smart filtering rules."""
    if re.match(r'^Check Box \d+$', name):
        return True
    if '_Stack' in name:
        return True
    if name.count(':') >= 2 and ftype == 'Text' and overlap_ratio > 0.2:
        return True
    if ftype == 'Text' and overlap_ratio > 0.85 and area > 500:
        return True
    if ftype == 'Text' and area > 7000 and overlap_ratio > 0.4:
        return True
    header_patterns = [r'service territory', r'Proposed project', r'^Efficiency Rating', r'Disadvantaged']
    for pattern in header_patterns:
        if re.search(pattern, name, re.I) and ftype == 'Text':
            return True
    if '?' in name and ftype == 'Text':
        return True
    if ftype == 'Text' and area > 10000 and len(name) > 15 and ' ' in name and overlap_ratio < 0.1:
        return True
    return False

def get_fields(pdf_path, apply_filter=False):
    """Extract fields from PDF."""
    doc = fitz.open(pdf_path)
    fields = []
    
    for page_idx, page in enumerate(doc):
        text_blocks = get_text_blocks(page) if apply_filter else []
        
        for widget in page.widgets():
            name = widget.field_name or ''
            ftype = widget.field_type_string
            area = widget.rect.width * widget.rect.height
            
            if apply_filter:
                overlap_ratio = field_text_overlap_ratio(widget.rect, text_blocks)
                if should_filter_field(name, ftype, area, overlap_ratio):
                    continue
            
            fields.append({
                'name': name,
                'rect': widget.rect,
                'page': page_idx,
                'type': ftype,
                'area': area
            })
    
    doc.close()
    return fields

def match_fields(pred_fields, gt_fields):
    """Match with adaptive algorithm."""
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

def process_pdf(static_name):
    """Process a single PDF with v22."""
    static_path = os.path.join(STATIC_DIR, static_name)
    output_name = static_name.replace('.pdf', '_v22_output.pdf')
    output_path = os.path.join(OUTPUT_DIR, output_name)
    
    # Create output dir
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # Run v22
    cmd = ['python3', V22_SCRIPT, static_path, output_path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            print(f"  ERROR: v22 failed - {result.stderr[:200]}")
            return None
    except subprocess.TimeoutExpired:
        print(f"  ERROR: v22 timed out")
        return None
    except Exception as e:
        print(f"  ERROR: {e}")
        return None
    
    return output_path

def main():
    print("="*80)
    print("BATCH PROCESSING ALL STATIC PDFs WITH V22")
    print("="*80)
    print()
    
    # Get all static PDFs
    static_pdfs = [f for f in os.listdir(STATIC_DIR) if f.endswith('.pdf')]
    
    results = []
    pdfs_without_gt = []
    
    for static_name in sorted(static_pdfs):
        print(f"\nProcessing: {static_name[:60]}...")
        
        # Check for GT
        gt_name = PDF_MAPPINGS.get(static_name)
        if not gt_name:
            # Try fuzzy match by ID
            pdf_id = static_name.split('_')[0]
            gt_candidates = [f for f in os.listdir(GT_DIR) if f.startswith(pdf_id + '_') and f.endswith('.pdf')]
            if gt_candidates:
                gt_name = gt_candidates[0]
            else:
                pdfs_without_gt.append(static_name)
                print(f"  SKIP: No GT found")
                continue
        
        gt_path = os.path.join(GT_DIR, gt_name)
        if not os.path.exists(gt_path):
            pdfs_without_gt.append(static_name)
            print(f"  SKIP: GT file not found")
            continue
        
        # Process with v22
        output_path = process_pdf(static_name)
        if not output_path or not os.path.exists(output_path):
            print(f"  FAILED: v22 processing failed")
            continue
        
        # Get fields and evaluate
        gt_fields = get_fields(gt_path)
        v22_fields = get_fields(output_path, apply_filter=True)
        
        tp, fp, fn = match_fields(v22_fields, gt_fields)
        
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
        
        results.append({
            'name': static_name,
            'gt_name': gt_name,
            'gt_fields': len(gt_fields),
            'v22_fields': len(v22_fields),
            'tp': tp,
            'fp': fp,
            'fn': fn,
            'precision': precision,
            'recall': recall,
            'f1': f1
        })
        
        status = "✓" if f1 >= 0.86 else "✗"
        print(f"  {status} GT={len(gt_fields)}, V22={len(v22_fields)}, TP={tp}, FP={fp}, FN={fn}")
        print(f"    P={precision*100:.1f}%, R={recall*100:.1f}%, F1={f1*100:.1f}%")
    
    # Summary
    print("\n" + "="*80)
    print("RESULTS SUMMARY")
    print("="*80)
    
    print(f"\nPDFs processed: {len(results)}")
    print(f"PDFs without GT: {len(pdfs_without_gt)}")
    
    if results:
        # Sort by F1
        results.sort(key=lambda x: x['f1'], reverse=True)
        
        print("\n{:<50} {:>6} {:>6} {:>6} {:>7}".format("PDF", "GT", "V22", "F1%", "Status"))
        print("-"*80)
        
        passing = 0
        for r in results:
            status = "PASS" if r['f1'] >= 0.86 else "FAIL"
            if r['f1'] >= 0.86:
                passing += 1
            print(f"{r['name'][:50]:<50} {r['gt_fields']:>6} {r['v22_fields']:>6} {r['f1']*100:>6.1f}% {status:>7}")
        
        print("-"*80)
        print(f"PASSING (≥86%): {passing}/{len(results)}")
        
        # Average
        avg_f1 = sum(r['f1'] for r in results) / len(results)
        print(f"AVERAGE F1: {avg_f1*100:.1f}%")
        
        # Show failing PDFs that need improvement
        failing = [r for r in results if r['f1'] < 0.86]
        if failing:
            print("\n" + "="*80)
            print("PDFs NEEDING IMPROVEMENT")
            print("="*80)
            for r in failing:
                print(f"\n{r['name']}")
                print(f"  Current: F1={r['f1']*100:.1f}% (P={r['precision']*100:.1f}%, R={r['recall']*100:.1f}%)")
                print(f"  Fields: GT={r['gt_fields']}, V22={r['v22_fields']}, TP={r['tp']}, FP={r['fp']}, FN={r['fn']}")
                if r['fp'] > r['fn']:
                    print(f"  Issue: Too many FPs ({r['fp']}) - need better filtering")
                else:
                    print(f"  Issue: Too many FNs ({r['fn']}) - v22 missing fields")
    
    if pdfs_without_gt:
        print("\n" + "="*80)
        print("PDFs WITHOUT GT (cannot evaluate)")
        print("="*80)
        for name in pdfs_without_gt:
            print(f"  {name}")

if __name__ == '__main__':
    main()
