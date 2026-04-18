#!/usr/bin/env python3
"""
Analyze FPs in high-FP PDFs to find patterns.
"""

import fitz
import os
import re

OUTPUT_DIR = '/tmp/v22_batch_output'
GT_DIR = '/Users/36981/Desktop/PDFTest/PDFs to test/Fillable PDFs'

FAILING = {
    '56380': '56380_DTE_2026_PartnerGuidelines_v23_Web_Release_Fillable.pdf',
    '53252': '53252_DTE_EEA_Field_Inspection_Report_v14_Web_Release_Fillable.pdf',
    '57561_WS': '57561_DTE_INSUL_HP_Rebate_Worksheet_v1_Web_Release_Fillable.pdf',
    '29174': '29174_DTE_INWIN_Field_Inspection_Report_Form_v3_WEB_RELEASE_fillable.pdf',
}

def calc_iou(r1, r2):
    x1, y1 = max(r1.x0, r2.x0), max(r1.y0, r2.y0)
    x2, y2 = min(r1.x1, r2.x1), min(r1.y1, r2.y1)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2-x1)*(y2-y1)
    a1 = (r1.x1-r1.x0)*(r1.y1-r1.y0)
    a2 = (r2.x1-r2.x0)*(r2.y1-r2.y0)
    return inter/(a1+a2-inter) if (a1+a2-inter) > 0 else 0

def center_dist(r1, r2):
    cx1, cy1 = (r1.x0+r1.x1)/2, (r1.y0+r1.y1)/2
    cx2, cy2 = (r2.x0+r2.x1)/2, (r2.y0+r2.y1)/2
    return ((cx1-cx2)**2+(cy1-cy2)**2)**0.5

def get_fields(path):
    doc = fitz.open(path)
    fields = []
    for pi, pg in enumerate(doc):
        for w in pg.widgets():
            fields.append({
                'name': w.field_name or '',
                'rect': w.rect,
                'page': pi,
                'type': w.field_type_string,
                'area': w.rect.width * w.rect.height,
                'width': w.rect.width,
                'height': w.rect.height
            })
    doc.close()
    return fields

for pdf_id, gt_name in FAILING.items():
    v22_path = os.path.join(OUTPUT_DIR, f'{pdf_id}_v22_output.pdf')
    gt_path = os.path.join(GT_DIR, gt_name)
    
    if not os.path.exists(v22_path):
        print(f"{pdf_id}: v22 output not found")
        continue
    if not os.path.exists(gt_path):
        print(f"{pdf_id}: GT not found")
        continue
    
    v22_fields = get_fields(v22_path)
    gt_fields = get_fields(gt_path)
    
    # Match
    matched_gt = set()
    matched_v22 = set()
    
    for i, pred in enumerate(v22_fields):
        best_j = -1
        best_score = -1
        for j, gt in enumerate(gt_fields):
            if j in matched_gt:
                continue
            gt_area = gt['area']
            match = False
            if gt_area < 100:
                if center_dist(pred['rect'], gt['rect']) <= 40:
                    match = True
            else:
                iou = calc_iou(pred['rect'], gt['rect'])
                if iou >= 0.3:
                    match = True
                    if iou > best_score:
                        best_score = iou
                        best_j = j
            if match and best_j == -1:
                best_j = j
        
        if best_j >= 0:
            matched_v22.add(i)
            matched_gt.add(best_j)
    
    fps = [f for i, f in enumerate(v22_fields) if i not in matched_v22]
    
    print(f"\n{'='*60}")
    print(f"{pdf_id}: {len(fps)} FPs (of {len(v22_fields)} v22 fields)")
    print(f"{'='*60}")
    
    cb_fps = [f for f in fps if f['type'] == 'CheckBox']
    text_fps = [f for f in fps if f['type'] == 'Text']
    
    print(f"CheckBox FPs: {len(cb_fps)}")
    print(f"Text FPs: {len(text_fps)}")
    
    # Check Box N pattern
    cb_numbered = [f for f in cb_fps if re.match(r'^Check Box \d+$', f['name'])]
    print(f"  - 'Check Box N' pattern: {len(cb_numbered)}")
    
    # _Stack pattern  
    stack_pattern = [f for f in fps if '_Stack' in f['name']]
    print(f"  - '_Stack' pattern: {len(stack_pattern)}")
    
    # Cell pattern
    cell_pattern = [f for f in fps if f['name'].startswith('Cell_')]
    print(f"  - 'Cell_' pattern: {len(cell_pattern)}")
    
    # Multi-colon
    multi_colon = [f for f in text_fps if f['name'].count(':') >= 2]
    print(f"  - Multi-colon text: {len(multi_colon)}")
    
    print("\nSample CheckBox FPs:")
    for f in cb_fps[:8]:
        print(f"  '{f['name'][:45]}' {f['width']:.0f}x{f['height']:.0f}")
    
    print("\nSample Text FPs:")
    for f in text_fps[:8]:
        print(f"  '{f['name'][:45]}' {f['width']:.0f}x{f['height']:.0f}")
