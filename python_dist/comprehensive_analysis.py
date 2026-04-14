#!/usr/bin/env python3
"""
Comprehensive FP/FN Analysis Tool
Runs v22 on static PDFs, compares to fillable PDFs, categorizes errors.
"""
import fitz
import os
import sys
import subprocess
import re
from collections import Counter, defaultdict

STATIC_DIR = "/Users/36981/Desktop/PDFTest/PDFs to test/Static PDFs"
FILLABLE_DIR = "/Users/36981/Desktop/PDFTest/PDFs to test/Fillable PDFs"
V22_OUTPUT_DIR = "/tmp/v22_output"
V22_SCRIPT = "/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/FillThatPDF_Optimized_v7/python_dist/smart_fillable_v22.py"
IOU_THRESHOLD = 0.3

# Focus on these well-matched pairs (same version numbers or known good matches)
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
    # These have version differences but should still be mostly comparable
    ("53252", "53252_DTE_EEA_Field_Inspection_Report_v11.pdf", 
              "53252_DTE_EEA_Field_Inspection_Report_v14_Web_Release_Fillable.pdf"),
    ("57769", "57769_CH_Res_NYS_Clean_Heat_Completion_Acknowledgement_Form_v2.pdf", 
              "57769_CH_Res_NYS_Clean_Heat_Completion_Acknowledgement_Form_v3_Web_Release_fillable.pdf"),
]

os.makedirs(V22_OUTPUT_DIR, exist_ok=True)

def get_field_type(field):
    """Determine field type from PDF field object"""
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
    """Extract all form fields from a PDF"""
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
    """Calculate IoU between two field rects"""
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
    """Match predicted fields to ground truth"""
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
    
    # False positives: predicted but not matched
    fps = [pred_fields[i] for i in range(len(pred_fields)) if i not in matched_pred]
    # False negatives: GT not matched
    fns = [gt_fields[j] for j in range(len(gt_fields)) if j not in matched_gt]
    
    return fps, fns, len(matched_pred)

def categorize_fp(fp):
    """Categorize a false positive by its likely cause"""
    name = fp.get('name', '')
    area = fp.get('area', 0)
    width = fp.get('width', 0)
    height = fp.get('height', 0)
    ftype = fp.get('type', '')
    
    # Check for header patterns
    header_keywords = [
        'ENERGY', 'STAR', 'Furnace', 'Boiler', 'Refrigerator', 
        'Insulation', 'Duct', 'Wall', 'Attic', 'Ceiling',
        'Basement', 'Natural Gas', 'Split System', 'Heat Pump',
        'Dehumidifier', 'Dryer', 'Washer', 'Dishwasher', 'Program',
        'Measure', 'Incentive', 'Rebate', 'Application'
    ]
    if any(kw.lower() in name.lower() for kw in header_keywords):
        return 'header_label'
    
    # R-value patterns
    if name.startswith('R') and '+' in name:
        return 'rvalue_label'
    
    # Auto-generated names (likely table cells detected incorrectly)
    if 'Cell_' in name:
        return 'auto_cell'
    
    # Very thin/line-like
    if width > 100 and height < 5:
        return 'horizontal_line'
    if height > 100 and width < 5:
        return 'vertical_line'
    
    # Tiny fields
    if area < 50:
        return 'tiny'
    
    # Check for pure numeric names (like "250", "4,000", "9,000")
    name_stripped = name.replace('_', '').replace('-', '').replace('.', '').replace(',', '').replace(' ', '')
    if name_stripped.isdigit():
        return 'pure_numeric'
    
    # Dollar amounts or numbers with commas
    if re.match(r'^[\$]?[\d,]+(\.\d+)?$', name.replace('_', '').replace(' ', '')):
        return 'numeric_value'
    
    # Status words
    if any(w in name for w in ['Rated', 'Certified', 'following', 'Required', 'Information']):
        return 'status_label'
    
    # "Or" / "or" patterns often indicate instruction text
    if name.strip().lower() in ['or', 'and', 'n/a', 'na', 'yes', 'no']:
        return 'connector_word'
    
    # Point/score values
    if re.match(r'^\d+\s*(pts?|points?)$', name.strip().lower()):
        return 'point_value'
    
    # AC/HVAC related values that aren't fields
    if any(x in name for x in ['AC + ', 'GFCI', 'BTU', 'SEER', 'EER', 'HSPF', 'CFM']):
        return 'hvac_spec'
    
    # Names that look like instruction text (contain multiple words)
    words = name.split()
    if len(words) >= 4:
        return 'instruction_text'
    
    return 'other'

def run_v22_on_pdf(input_path, output_path):
    """Run v22 on a PDF"""
    if os.path.exists(output_path):
        # Already exists, skip
        return True
    
    cmd = ['python3', V22_SCRIPT, input_path, output_path]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    return result.returncode == 0

def analyze_pdf_pair(pdf_id, static_pdf, fillable_pdf):
    """Analyze a single PDF pair"""
    static_path = os.path.join(STATIC_DIR, static_pdf)
    fillable_path = os.path.join(FILLABLE_DIR, fillable_pdf)
    v22_output_path = os.path.join(V22_OUTPUT_DIR, f"{pdf_id}_v22_output.pdf")
    
    if not os.path.exists(static_path):
        print(f"  ⚠️ Missing static: {static_path}")
        return None
    if not os.path.exists(fillable_path):
        print(f"  ⚠️ Missing fillable: {fillable_path}")
        return None
    
    # Run v22
    print(f"  Running v22...")
    if not run_v22_on_pdf(static_path, v22_output_path):
        print(f"    ❌ v22 failed")
        return None
    
    if not os.path.exists(v22_output_path):
        print(f"    ❌ No output generated")
        return None
    
    # Get ground truth from fillable PDF
    gt_fields = extract_fields(fillable_path)
    # Get predictions from v22 output
    pred_fields = extract_fields(v22_output_path)
    
    # Match
    fps, fns, tp = match_fields(pred_fields, gt_fields)
    
    # Calculate metrics
    precision = tp / len(pred_fields) if pred_fields else 0
    recall = tp / len(gt_fields) if gt_fields else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
    
    return {
        'pdf_id': pdf_id,
        'gt_count': len(gt_fields),
        'pred_count': len(pred_fields),
        'tp': tp,
        'fp_count': len(fps),
        'fn_count': len(fns),
        'precision': precision,
        'recall': recall,
        'f1': f1,
        'fps': fps,
        'fns': fns
    }

def main():
    print("=" * 70)
    print("COMPREHENSIVE FP/FN ANALYSIS")
    print("=" * 70)
    
    all_fps = []
    all_fns = []
    all_results = []
    
    for pdf_id, static, fillable in GOOD_PAIRS:
        print(f"\n📄 Analyzing {pdf_id}...")
        result = analyze_pdf_pair(pdf_id, static, fillable)
        if result:
            print(f"   GT: {result['gt_count']}, Pred: {result['pred_count']}")
            print(f"   TP: {result['tp']}, FP: {result['fp_count']}, FN: {result['fn_count']}")
            print(f"   P: {result['precision']:.1%}, R: {result['recall']:.1%}, F1: {result['f1']:.1%}")
            all_fps.extend(result['fps'])
            all_fns.extend(result['fns'])
            all_results.append(result)
    
    print("\n" + "=" * 70)
    print("AGGREGATE ANALYSIS")
    print("=" * 70)
    
    total_tp = sum(r['tp'] for r in all_results)
    total_pred = sum(r['pred_count'] for r in all_results)
    total_gt = sum(r['gt_count'] for r in all_results)
    
    agg_precision = total_tp / total_pred if total_pred else 0
    agg_recall = total_tp / total_gt if total_gt else 0
    agg_f1 = 2 * agg_precision * agg_recall / (agg_precision + agg_recall) if (agg_precision + agg_recall) else 0
    
    print(f"\nTotal GT fields: {total_gt}")
    print(f"Total Predicted fields: {total_pred}")
    print(f"Total True Positives: {total_tp}")
    print(f"Total False Positives: {len(all_fps)}")
    print(f"Total False Negatives: {len(all_fns)}")
    print(f"\nAggregate Precision: {agg_precision:.1%}")
    print(f"Aggregate Recall: {agg_recall:.1%}")
    print(f"Aggregate F1: {agg_f1:.1%}")
    
    # Categorize FPs
    fp_categories = Counter()
    for fp in all_fps:
        cat = categorize_fp(fp)
        fp_categories[cat] += 1
    
    print("\n" + "=" * 70)
    print("📊 FALSE POSITIVE CATEGORIES")
    print("=" * 70)
    for cat, count in fp_categories.most_common():
        pct = 100 * count / len(all_fps) if all_fps else 0
        print(f"   {cat}: {count} ({pct:.1f}%)")
    
    # Show examples for each category
    print("\n" + "=" * 70)
    print("📝 EXAMPLES OF EACH FP CATEGORY (top 5 each)")
    print("=" * 70)
    fp_by_cat = defaultdict(list)
    for fp in all_fps:
        cat = categorize_fp(fp)
        fp_by_cat[cat].append(fp)
    
    for cat in sorted(fp_categories.keys(), key=lambda x: -fp_categories[x]):
        print(f"\n   === {cat.upper()} ({fp_categories[cat]} total) ===")
        for fp in fp_by_cat[cat][:5]:
            print(f"      • {fp['type']:8} area={fp['area']:>6.0f} name='{fp['name'][:40]}'")
    
    # FN analysis
    print("\n" + "=" * 70)
    print("📊 FALSE NEGATIVE BREAKDOWN")
    print("=" * 70)
    fn_types = Counter(fn['type'] for fn in all_fns)
    print("\n   By type:")
    for t, count in fn_types.most_common():
        print(f"   {t}: {count}")
    
    fn_sizes = Counter()
    for fn in all_fns:
        area = fn['area']
        if area < 50:
            fn_sizes['tiny (<50)'] += 1
        elif area < 200:
            fn_sizes['small (50-200)'] += 1
        elif area < 500:
            fn_sizes['medium (200-500)'] += 1
        else:
            fn_sizes['large (>500)'] += 1
    
    print("\n   By size:")
    for size, count in fn_sizes.most_common():
        print(f"   {size}: {count}")
    
    # Show some FN examples
    print("\n   Example FNs (first 20):")
    for fn in all_fns[:20]:
        print(f"      • {fn['type']:8} area={fn['area']:>6.0f} name='{fn['name'][:40]}'")

if __name__ == '__main__':
    main()
