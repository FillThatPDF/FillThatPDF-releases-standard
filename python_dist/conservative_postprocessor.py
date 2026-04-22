#!/usr/bin/env python3
"""
Conservative Post-Processor v2
Only removes the MOST obvious false positives to improve precision without hurting recall much.
"""
import fitz
import os
import re
from collections import Counter

STATIC_DIR = "/Users/36981/Desktop/PDFTest/PDFs to test/Static PDFs"
FILLABLE_DIR = "/Users/36981/Desktop/PDFTest/PDFs to test/Fillable PDFs"
V22_OUTPUT_DIR = "/tmp/v22_output"
OUTPUT_DIR = "/tmp/v22_postprocessed"
IOU_THRESHOLD = 0.3

GOOD_PAIRS = [
    ("55570", "55570_DTE_SEEL_Contractor_Onboarding _Packet_v26.pdf", 
              "55570_DTE_SEEL_Contractor_Onboarding _Packet_v26_fillable.pdf"),
    ("57618", "57618_NGRID_New_York_Ack_Form_Fillable_v07.pdf", 
              "57618_NGRID_New_York_Ack_Form_Fillable_v07_Release_Web_Fillable.pdf"),
    ("11691", "11691_ConEd_Distributor_Application_Form_v10.pdf", 
              "11691_ConEd_Distributor_Application_Form_v10_fillable_RELEASE.pdf"),
    ("14792", "14792_ConEd_Gas_HVAC_Tune-Up_Contractor_Application_v03_FINAL_RELEASE_Web.pdf", 
              "14792_ConEd_Gas_HVAC_Tune-Up_Contractor_Application_v03_FINAL_RELEASE_Web_Fillable.pdf"),
    ("32775", "32775_DTE_2022_HVAC_Customer_SelfSubmission_Application_v01.pdf", 
              "32775_DTE_2022_HVAC_Customer_SelfSubmission_Application_v01_Web_Release_Fillable.pdf"),
    ("9787", "9787_ConEd_Res_HVAC_Gas_Rebate_Appl_v01_FINAL_RELEASE.pdf", 
             "9787_ConEd_Res_HVAC_Gas_Rebate_Appl_v01_FINAL_RELEASE_Fillable_Locked.pdf"),
    ("53252", "53252_DTE_EEA_Field_Inspection_Report_v11.pdf", 
              "53252_DTE_EEA_Field_Inspection_Report_v14_Web_Release_Fillable.pdf"),
    ("57769", "57769_CH_Res_NYS_Clean_Heat_Completion_Acknowledgement_Form_v2.pdf", 
              "57769_CH_Res_NYS_Clean_Heat_Completion_Acknowledgement_Form_v3_Web_Release_fillable.pdf"),
]

def get_field_type(field):
    ft = str(field.field_type)
    if 'text' in ft.lower():
        return 'text'
    elif 'check' in ft.lower():
        return 'checkbox'
    elif 'radio' in ft.lower():
        return 'radio'
    elif 'combo' in ft.lower() or 'list' in ft.lower():
        return 'dropdown'
    return 'text'

def extract_fields(pdf_path):
    if not os.path.exists(pdf_path):
        return []
    
    doc = fitz.open(pdf_path)
    fields = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        for widget in page.widgets():
            rect = widget.rect
            if rect.width < 2 or rect.height < 2:
                continue
            fields.append({
                'name': widget.field_name or '',
                'type': get_field_type(widget),
                'page': page_num + 1,
                'x0': rect.x0, 'y0': rect.y0,
                'x1': rect.x1, 'y1': rect.y1,
                'width': rect.width,
                'height': rect.height,
                'area': rect.width * rect.height
            })
    doc.close()
    return fields

def calc_iou(f1, f2):
    if f1['page'] != f2['page']:
        return 0
    
    x0 = max(f1['x0'], f2['x0'])
    y0 = max(f1['y0'], f2['y0'])
    x1 = min(f1['x1'], f2['x1'])
    y1 = min(f1['y1'], f2['y1'])
    
    if x1 <= x0 or y1 <= y0:
        return 0
    
    inter = (x1 - x0) * (y1 - y0)
    area1 = (f1['x1'] - f1['x0']) * (f1['y1'] - f1['y0'])
    area2 = (f2['x1'] - f2['x0']) * (f2['y1'] - f2['y0'])
    union = area1 + area2 - inter
    
    return inter / union if union > 0 else 0

def match_fields(pred_fields, gt_fields, threshold=IOU_THRESHOLD):
    matched_pred = set()
    matched_gt = set()
    
    for i, pred in enumerate(pred_fields):
        best_iou = 0
        best_j = -1
        for j, gt in enumerate(gt_fields):
            if j in matched_gt:
                continue
            iou = calc_iou(pred, gt)
            if iou > best_iou:
                best_iou = iou
                best_j = j
        
        if best_iou >= threshold:
            matched_pred.add(i)
            matched_gt.add(best_j)
    
    return len(matched_pred), matched_pred, matched_gt

def should_remove_field(field):
    """
    Conservative rules - only remove fields that are VERY likely false positives.
    Returns (should_remove, reason)
    """
    name = field.get('name', '')
    area = field.get('area', 0)
    width = field.get('width', 0)
    height = field.get('height', 0)
    ftype = field.get('type', '')
    
    # Strip common suffixes for duplicate names
    base_name = re.sub(r'_\d+$', '', name)
    
    # RULE 1: Pure numeric values (like "250", "4,000", "9,000")
    # These are data values, not input fields
    name_cleaned = name.replace(',', '').replace('_', '').replace(' ', '')
    if re.match(r'^\d{2,}$', name_cleaned):  # 2+ digits only
        return True, 'pure_numeric'
    
    # RULE 2: Dollar amounts or numbers with commas
    if re.match(r'^[\$]?[\d,]+$', name.replace('_', '').replace(' ', '')):
        return True, 'dollar_amount'
    
    # RULE 3: Specific header patterns that are NEVER fields
    # These are section headers/category labels
    explicit_headers = [
        'ENERGY STAR',  # Product categories
        'Natural Gas Furnace', 'Natural Gas Boiler',
        'Split System AC', 'Ductless Mini-Split',
        'Heat Pump', 'Air Source Heat Pump',
        'Refrigerator Replacement', 'Dehumidifier',
        'AboveGround Wall Insulation', 'AtticCeiling Insulation',
        'Basement Wall Insulation', 'Duct Insulation',
        'Information Required',  # Section headers
        'Customer Information',
        'Installation Point Value',  # Column headers
    ]
    for header in explicit_headers:
        if header.lower() in name.lower():
            return True, 'explicit_header'
    
    # RULE 4: R-value labels (R10+, R6+, etc.)
    if re.match(r'^R\d+\+?', name) and len(name) < 20:
        return True, 'rvalue_label'
    
    # DON'T remove based on:
    # - Size (checkboxes can be any size)
    # - Auto-generated names (Cell_xxx) - some are real
    # - Multi-word names (many real fields have long names)
    
    return False, None

def postprocess_pdf(v22_path, output_path, gt_fields):
    """Apply conservative post-processing to v22 output"""
    doc = fitz.open(v22_path)
    removed_reasons = Counter()
    
    # First pass: identify fields to remove (store by name and coordinates)
    fields_to_remove = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        for widget in page.widgets():
            field = {
                'name': widget.field_name or '',
                'type': get_field_type(widget),
                'page': page_num + 1,
                'x0': widget.rect.x0, 'y0': widget.rect.y0,
                'x1': widget.rect.x1, 'y1': widget.rect.y1,
                'width': widget.rect.width,
                'height': widget.rect.height,
                'area': widget.rect.width * widget.rect.height
            }
            
            should_remove, reason = should_remove_field(field)
            if should_remove:
                fields_to_remove.append((page_num, field['name'], field['x0'], field['y0'], reason))
                removed_reasons[reason] += 1
    
    # Second pass: remove fields (iterate fresh each time)
    for page_num, name, x0, y0, reason in fields_to_remove:
        page = doc[page_num]
        for widget in list(page.widgets()):
            if widget.field_name == name:
                rect = widget.rect
                if abs(rect.x0 - x0) < 1 and abs(rect.y0 - y0) < 1:
                    page.delete_widget(widget)
                    break
    
    doc.save(output_path)
    doc.close()
    
    return len(fields_to_remove), removed_reasons

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    print("=" * 70)
    print("CONSERVATIVE POST-PROCESSOR V2")
    print("=" * 70)
    
    results = []
    all_removed_reasons = Counter()
    
    for pdf_id, static, fillable in GOOD_PAIRS:
        print(f"\n📄 Processing {pdf_id}...")
        
        fillable_path = os.path.join(FILLABLE_DIR, fillable)
        v22_path = os.path.join(V22_OUTPUT_DIR, f"{pdf_id}_v22_output.pdf")
        output_path = os.path.join(OUTPUT_DIR, f"{pdf_id}_postprocessed.pdf")
        
        if not os.path.exists(v22_path):
            print(f"  ⚠️ Missing v22 output")
            continue
        
        # Get ground truth
        gt_fields = extract_fields(fillable_path)
        
        # Get original v22 fields and metrics
        orig_fields = extract_fields(v22_path)
        orig_tp, _, _ = match_fields(orig_fields, gt_fields)
        orig_prec = orig_tp / len(orig_fields) if orig_fields else 0
        orig_recall = orig_tp / len(gt_fields) if gt_fields else 0
        
        # Apply post-processing
        removed, reasons = postprocess_pdf(v22_path, output_path, gt_fields)
        all_removed_reasons.update(reasons)
        
        # Get new metrics
        new_fields = extract_fields(output_path)
        new_tp, _, _ = match_fields(new_fields, gt_fields)
        new_prec = new_tp / len(new_fields) if new_fields else 0
        new_recall = new_tp / len(gt_fields) if gt_fields else 0
        
        print(f"   Original: {len(orig_fields)} fields, TP={orig_tp}, P={orig_prec:.1%}, R={orig_recall:.1%}")
        print(f"   Removed:  {removed} ({', '.join(f'{r}:{c}' for r,c in reasons.most_common())})")
        print(f"   After:    {len(new_fields)} fields, TP={new_tp}, P={new_prec:.1%}, R={new_recall:.1%}")
        
        results.append({
            'pdf_id': pdf_id,
            'orig_count': len(orig_fields),
            'new_count': len(new_fields),
            'orig_tp': orig_tp,
            'new_tp': new_tp,
            'gt_count': len(gt_fields)
        })
    
    # Aggregate
    print("\n" + "=" * 70)
    print("AGGREGATE RESULTS")
    print("=" * 70)
    
    total_orig = sum(r['orig_count'] for r in results)
    total_new = sum(r['new_count'] for r in results)
    total_orig_tp = sum(r['orig_tp'] for r in results)
    total_new_tp = sum(r['new_tp'] for r in results)
    total_gt = sum(r['gt_count'] for r in results)
    
    orig_prec = total_orig_tp / total_orig if total_orig else 0
    new_prec = total_new_tp / total_new if total_new else 0
    orig_recall = total_orig_tp / total_gt if total_gt else 0
    new_recall = total_new_tp / total_gt if total_gt else 0
    
    orig_f1 = 2 * orig_prec * orig_recall / (orig_prec + orig_recall) if (orig_prec + orig_recall) else 0
    new_f1 = 2 * new_prec * new_recall / (new_prec + new_recall) if (new_prec + new_recall) else 0
    
    print(f"\nOriginal v22:")
    print(f"   Total fields: {total_orig}")
    print(f"   True Positives: {total_orig_tp}")
    print(f"   Precision: {orig_prec:.1%}")
    print(f"   Recall: {orig_recall:.1%}")
    print(f"   F1: {orig_f1:.1%}")
    
    print(f"\nAfter Conservative Post-Processing:")
    print(f"   Total fields: {total_new}")
    print(f"   Removed: {total_orig - total_new} ({100*(total_orig-total_new)/total_orig:.1f}%)")
    print(f"   True Positives: {total_new_tp}")
    print(f"   Precision: {new_prec:.1%}")
    print(f"   Recall: {new_recall:.1%}")
    print(f"   F1: {new_f1:.1%}")
    
    print(f"\n📈 F1 Change: {orig_f1:.1%} → {new_f1:.1%} ({new_f1-orig_f1:+.1%})")
    
    print(f"\nRemoval reasons breakdown:")
    for reason, count in all_removed_reasons.most_common():
        print(f"   {reason}: {count}")

if __name__ == '__main__':
    main()
