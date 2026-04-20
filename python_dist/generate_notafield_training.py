#!/usr/bin/env python3
"""
Generate training data for "not_a_field" class using known false positives.
This will create crops of areas that v22 incorrectly detected as fields
so we can train the classifier to reject them.
"""
import fitz
import os
import sys
import subprocess
from PIL import Image
import io

STATIC_DIR = "/Users/36981/Desktop/PDFTest/PDFs to test/Static PDFs"
FILLABLE_DIR = "/Users/36981/Desktop/PDFTest/PDFs to test/Fillable PDFs"
V22_OUTPUT_DIR = "/tmp/v22_output"
OUTPUT_DIR = "/tmp/not_a_field_training"
IOU_THRESHOLD = 0.3
DPI = 150  # Resolution for rendering

# Focus on well-matched pairs
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
    
    fps = [pred_fields[i] for i in range(len(pred_fields)) if i not in matched_pred]
    return fps

def extract_field_crop(pdf_path, field, dpi=DPI, padding=5):
    """Extract a cropped image of a field from the PDF"""
    doc = fitz.open(pdf_path)
    page_num = field['page'] - 1  # 0-indexed
    
    if page_num >= len(doc):
        doc.close()
        return None
    
    page = doc[page_num]
    
    # Create rect with padding
    x0 = max(0, field['x0'] - padding)
    y0 = max(0, field['y0'] - padding)
    x1 = min(page.rect.width, field['x1'] + padding)
    y1 = min(page.rect.height, field['y1'] + padding)
    
    clip_rect = fitz.Rect(x0, y0, x1, y1)
    
    # Render to image
    mat = fitz.Matrix(dpi/72, dpi/72)
    pix = page.get_pixmap(matrix=mat, clip=clip_rect)
    
    # Convert to PIL
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    
    doc.close()
    return img

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    print("=" * 70)
    print("GENERATING NOT_A_FIELD TRAINING DATA")
    print("=" * 70)
    
    total_fps = 0
    saved_count = 0
    
    for pdf_id, static_pdf, fillable_pdf in GOOD_PAIRS:
        print(f"\n📄 Processing {pdf_id}...")
        
        static_path = os.path.join(STATIC_DIR, static_pdf)
        fillable_path = os.path.join(FILLABLE_DIR, fillable_pdf)
        v22_output_path = os.path.join(V22_OUTPUT_DIR, f"{pdf_id}_v22_output.pdf")
        
        if not os.path.exists(static_path):
            print(f"  ⚠️ Missing static: {static_path}")
            continue
        if not os.path.exists(fillable_path):
            print(f"  ⚠️ Missing fillable: {fillable_path}")
            continue
        if not os.path.exists(v22_output_path):
            print(f"  ⚠️ Missing v22 output: {v22_output_path}")
            continue
        
        # Get ground truth from fillable PDF
        gt_fields = extract_fields(fillable_path)
        # Get predictions from v22 output
        pred_fields = extract_fields(v22_output_path)
        
        # Find false positives
        fps = match_fields(pred_fields, gt_fields)
        total_fps += len(fps)
        
        print(f"   Found {len(fps)} false positives")
        
        # Extract crops from the STATIC PDF (source)
        for i, fp in enumerate(fps):
            # Skip very tiny ones
            if fp['area'] < 20:
                continue
            
            img = extract_field_crop(static_path, fp, padding=3)
            if img is None:
                continue
            
            # Skip if too small after cropping
            if img.width < 10 or img.height < 5:
                continue
            
            # Save image
            safe_name = fp['name'][:30].replace('/', '_').replace(' ', '_').replace(':', '_')
            filename = f"{pdf_id}_fp_{i:03d}_{safe_name}.png"
            filepath = os.path.join(OUTPUT_DIR, filename)
            img.save(filepath)
            saved_count += 1
    
    print("\n" + "=" * 70)
    print(f"DONE!")
    print(f"Total false positives found: {total_fps}")
    print(f"Saved {saved_count} training images to: {OUTPUT_DIR}")
    print("=" * 70)
    
    # Print instructions
    print("\nNEXT STEPS:")
    print("1. Review the images in the output directory")
    print("2. Move them to the training data folder as 'not_a_field' class")
    print("3. Retrain the classifier with the new class")

if __name__ == '__main__':
    main()
