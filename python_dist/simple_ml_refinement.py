#!/usr/bin/env python3
"""
Simple ML refinement: Use the classifier to filter out false positives from v22 output.
"""
import fitz
import os
import sys
import torch
import torch.nn as nn
from torchvision import transforms, models
from PIL import Image
import io

# Configuration
CLASSIFIER_MODEL = "/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/FillThatPDF_Optimized_v7/python_dist/classifier_model.pt"
STATIC_DIR = "/Users/36981/Desktop/PDFTest/PDFs to test/Static PDFs"
FILLABLE_DIR = "/Users/36981/Desktop/PDFTest/PDFs to test/Fillable PDFs"
V22_OUTPUT_DIR = "/tmp/v22_output"
OUTPUT_DIR = "/tmp/v22_refined"
DPI = 150
IOU_THRESHOLD = 0.3
NOT_A_FIELD_THRESHOLD = 0.98  # Only remove if confidence > 98%

# Same pairs as before
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

CLASS_NAMES = ["text", "checkbox", "radio", "dropdown", "not_a_field"]

def load_classifier():
    """Load the trained classifier"""
    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    
    # Create model
    model = models.mobilenet_v3_small(weights=None)
    model.classifier[-1] = nn.Linear(model.classifier[-1].in_features, len(CLASS_NAMES))
    
    # Load weights
    checkpoint = torch.load(CLASSIFIER_MODEL, map_location=device)
    if 'model_state_dict' in checkpoint:
        model.load_state_dict(checkpoint['model_state_dict'])
    else:
        model.load_state_dict(checkpoint)
    
    model = model.to(device)
    model.eval()
    
    # Transform
    transform = transforms.Compose([
        transforms.Resize((64, 128)),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
    ])
    
    return model, transform, device

def extract_field_crop(pdf_path, field, dpi=DPI, padding=3):
    """Extract a cropped image of a field from the PDF"""
    doc = fitz.open(pdf_path)
    page_num = field['page'] - 1
    
    if page_num >= len(doc):
        doc.close()
        return None
    
    page = doc[page_num]
    
    x0 = max(0, field['x0'] - padding)
    y0 = max(0, field['y0'] - padding)
    x1 = min(page.rect.width, field['x1'] + padding)
    y1 = min(page.rect.height, field['y1'] + padding)
    
    clip_rect = fitz.Rect(x0, y0, x1, y1)
    mat = fitz.Matrix(dpi/72, dpi/72)
    pix = page.get_pixmap(matrix=mat, clip=clip_rect)
    
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    doc.close()
    return img

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
                'area': rect.width * rect.height,
                'widget': widget
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
    
    fps = [i for i in range(len(pred_fields)) if i not in matched_pred]
    fns = [j for j in range(len(gt_fields)) if j not in matched_gt]
    
    return len(matched_pred), fps, fns

def classify_field(model, transform, device, static_pdf, field):
    """Classify a single field"""
    img = extract_field_crop(static_pdf, field)
    if img is None or img.width < 5 or img.height < 3:
        return None, 0
    
    # Transform and predict
    img_tensor = transform(img).unsqueeze(0).to(device)
    
    with torch.no_grad():
        outputs = model(img_tensor)
        probs = torch.softmax(outputs, dim=1)
        pred_class = probs.argmax(dim=1).item()
        confidence = probs[0, pred_class].item()
    
    return CLASS_NAMES[pred_class], confidence

def refine_pdf(model, transform, device, pdf_id, static_pdf, v22_output, fillable_pdf, output_path):
    """Apply ML refinement to a v22 output PDF"""
    static_path = os.path.join(STATIC_DIR, static_pdf)
    fillable_path = os.path.join(FILLABLE_DIR, fillable_pdf)
    v22_path = os.path.join(V22_OUTPUT_DIR, v22_output)
    
    if not os.path.exists(v22_path):
        return None
    
    # Get fields
    pred_fields = extract_fields(v22_path)
    gt_fields = extract_fields(fillable_path)
    
    # Original metrics
    orig_tp, orig_fp_idxs, orig_fn_idxs = match_fields(pred_fields, gt_fields)
    orig_precision = orig_tp / len(pred_fields) if pred_fields else 0
    orig_recall = orig_tp / len(gt_fields) if gt_fields else 0
    
    # Classify each field and decide whether to keep
    fields_to_remove = []
    not_a_field_count = 0
    
    for i, field in enumerate(pred_fields):
        pred_class, confidence = classify_field(model, transform, device, static_path, field)
        
        if pred_class == 'not_a_field' and confidence >= NOT_A_FIELD_THRESHOLD:
            fields_to_remove.append(i)
            not_a_field_count += 1
    
    # Open source PDF and remove fields
    doc = fitz.open(v22_path)
    removed = 0
    
    # Get widgets to remove (by name and page)
    widgets_to_remove = []
    for i in fields_to_remove:
        f = pred_fields[i]
        widgets_to_remove.append((f['page'], f['name'], f['x0'], f['y0']))
    
    for page_num in range(len(doc)):
        page = doc[page_num]
        for widget in list(page.widgets()):
            # Check if this widget should be removed
            for pg, name, x0, y0 in widgets_to_remove:
                if pg == page_num + 1 and widget.field_name == name:
                    rect = widget.rect
                    if abs(rect.x0 - x0) < 1 and abs(rect.y0 - y0) < 1:
                        page.delete_widget(widget)
                        removed += 1
                        break
    
    # Save to new file
    doc.save(output_path)
    doc.close()
    
    # Calculate new metrics
    refined_fields = extract_fields(output_path)
    new_tp, new_fp_idxs, new_fn_idxs = match_fields(refined_fields, gt_fields)
    new_precision = new_tp / len(refined_fields) if refined_fields else 0
    new_recall = new_tp / len(gt_fields) if gt_fields else 0
    
    return {
        'pdf_id': pdf_id,
        'orig_count': len(pred_fields),
        'refined_count': len(refined_fields),
        'removed': removed,
        'orig_tp': orig_tp,
        'new_tp': new_tp,
        'orig_precision': orig_precision,
        'new_precision': new_precision,
        'orig_recall': orig_recall,
        'new_recall': new_recall,
        'gt_count': len(gt_fields)
    }

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    print("=" * 70)
    print("ML REFINEMENT: Filter False Positives with Classifier")
    print("=" * 70)
    
    print("\n📦 Loading classifier...")
    model, transform, device = load_classifier()
    print(f"   Device: {device}")
    
    results = []
    
    for pdf_id, static, fillable in GOOD_PAIRS:
        print(f"\n📄 Processing {pdf_id}...")
        v22_output = f"{pdf_id}_v22_output.pdf"
        output_path = os.path.join(OUTPUT_DIR, f"{pdf_id}_refined.pdf")
        
        result = refine_pdf(model, transform, device, pdf_id, static, v22_output, fillable, output_path)
        
        if result:
            print(f"   Original: {result['orig_count']} fields, TP={result['orig_tp']}")
            print(f"   Refined:  {result['refined_count']} fields, TP={result['new_tp']}")
            print(f"   Removed:  {result['removed']} fields")
            print(f"   Precision: {result['orig_precision']:.1%} → {result['new_precision']:.1%}")
            print(f"   Recall:    {result['orig_recall']:.1%} → {result['new_recall']:.1%}")
            results.append(result)
    
    # Aggregate
    print("\n" + "=" * 70)
    print("AGGREGATE RESULTS")
    print("=" * 70)
    
    total_orig = sum(r['orig_count'] for r in results)
    total_refined = sum(r['refined_count'] for r in results)
    total_removed = sum(r['removed'] for r in results)
    total_orig_tp = sum(r['orig_tp'] for r in results)
    total_new_tp = sum(r['new_tp'] for r in results)
    total_gt = sum(r['gt_count'] for r in results)
    
    orig_prec = total_orig_tp / total_orig if total_orig else 0
    new_prec = total_new_tp / total_refined if total_refined else 0
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
    
    print(f"\nAfter ML Refinement:")
    print(f"   Total fields: {total_refined}")
    print(f"   Removed: {total_removed} ({100*total_removed/total_orig:.1f}%)")
    print(f"   True Positives: {total_new_tp}")
    print(f"   Precision: {new_prec:.1%}")
    print(f"   Recall: {new_recall:.1%}")
    print(f"   F1: {new_f1:.1%}")
    
    print(f"\n📈 F1 Change: {orig_f1:.1%} → {new_f1:.1%} ({new_f1-orig_f1:+.1%})")

if __name__ == '__main__':
    main()
