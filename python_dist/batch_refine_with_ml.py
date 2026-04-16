#!/usr/bin/env python3
"""
Batch ML Refinement - Push 70-80% → 85-90%
==========================================

Applies ML-based refinement to v22 output for well-matched PDFs.
Reuses the proven batch_test_accuracy infrastructure but adds ML filtering.

Strategy:
1. Run v22 on static PDF
2. Extract all field boxes
3. ML classifier votes on each: keep or reject  
4. Create refined PDF with filtered fields
5. Compare against ground truth

Author: FillThatPDF Team  
Date: February 2026
"""

import sys
import os
import json
import argparse
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from datetime import datetime

import pikepdf
import pdfplumber
import cv2
import numpy as np

# Optional ML
try:
    import torch
    import torch.nn as nn
    from torchvision import transforms, models
    from pdf2image import convert_from_path
    ML_AVAILABLE = True
except ImportError:
    ML_AVAILABLE = False

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


class FieldClassifier:
    """Loads and runs the trained field classifier."""
    
    def __init__(self, model_path: str):
        self.device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
        
        # Load metadata
        meta_path = Path(model_path).with_suffix('.json')
        if meta_path.exists():
            with open(meta_path) as f:
                self.metadata = json.load(f)
        else:
            self.metadata = {'class_names': ['text', 'checkbox', 'not_a_field']}
        
        self.classes = self.metadata.get('class_names', self.metadata.get('classes', ['text', 'checkbox', 'not_a_field']))
        self.num_classes = len(self.classes)
        
        # Load model
        self.model = models.mobilenet_v3_small(weights=None)
        self.model.classifier[3] = nn.Linear(self.model.classifier[3].in_features, self.num_classes)
        self.model.load_state_dict(torch.load(model_path, map_location=self.device))
        self.model.to(self.device)
        self.model.eval()
        
        # Transforms
        self.transform = transforms.Compose([
            transforms.ToPILImage(),
            transforms.Resize((224, 224)),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
        ])
        
        logger.info(f"   ✓ Loaded classifier: {self.num_classes} classes")
    
    def classify(self, img_crop: np.ndarray) -> Tuple[str, float]:
        """Classify a field crop. Returns (class_name, confidence)."""
        if img_crop.size == 0:
            return 'not_a_field', 1.0
        
        # Convert grayscale to RGB if needed
        if len(img_crop.shape) == 2:
            img_crop = cv2.cvtColor(img_crop, cv2.COLOR_GRAY2RGB)
        elif img_crop.shape[2] == 1:
            img_crop = cv2.cvtColor(img_crop, cv2.COLOR_GRAY2RGB)
        
        # Transform and predict
        img_tensor = self.transform(img_crop).unsqueeze(0).to(self.device)
        
        with torch.no_grad():
            outputs = self.model(img_tensor)
            probs = torch.softmax(outputs, dim=1)
            confidence, predicted = torch.max(probs, 1)
            
        class_name = self.classes[predicted.item()]
        conf_value = confidence.item()
        
        return class_name, conf_value


def extract_fields_from_pdf(pdf_path: Path) -> List[Dict]:
    """Extract all form fields with their properties."""
    fields = []
    
    try:
        with pikepdf.open(pdf_path) as pdf:
            if '/AcroForm' not in pdf.Root:
                return fields
            
            acroform = pdf.Root.AcroForm
            if '/Fields' not in acroform:
                return fields
            
            for field_obj in acroform.Fields:
                try:
                    field_dict = {}
                    
                    # Name
                    if '/T' in field_obj:
                        field_dict['name'] = str(field_obj.T)
                    else:
                        continue
                    
                    # Type
                    ft = str(field_obj.get('/FT', ''))
                    field_dict['type'] = ft
                    
                    # Bounding box
                    if '/Kids' in field_obj and len(field_obj.Kids) > 0:
                        widget = field_obj.Kids[0]
                    else:
                        widget = field_obj
                    
                    if '/Rect' in widget:
                        rect = widget.Rect
                        field_dict['bbox'] = [float(rect[0]), float(rect[1]), 
                                            float(rect[2]), float(rect[3])]
                    
                    # Page number
                    if '/P' in widget:
                        page = widget.P
                        for i, p in enumerate(pdf.pages):
                            if p == page:
                                field_dict['page'] = i
                                break
                    
                    if 'bbox' in field_dict and 'page' in field_dict:
                        fields.append(field_dict)
                    
                except Exception as e:
                    continue
    
    except Exception as e:
        logger.error(f"Error extracting fields: {e}")
    
    return fields


def get_page_dimensions(pdf_path: Path, page_num: int) -> Tuple[float, float]:
    """Get page dimensions."""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            page = pdf.pages[page_num]
            return page.width, page.height
    except:
        return 612, 792


def refine_pdf_with_ml(static_pdf: Path, v22_pdf: Path, output_pdf: Path, 
                       classifier: FieldClassifier, threshold: float = 0.7) -> Dict:
    """Refine v22 output using ML classifier."""
    
    # Extract fields from v22 output
    logger.info(f"   Extracting fields from v22 output...")
    fields = extract_fields_from_pdf(v22_pdf)
    logger.info(f"   Found {len(fields)} fields to evaluate")
    
    if len(fields) == 0:
        # Just copy input to output
        with pikepdf.open(v22_pdf) as pdf:
            pdf.save(output_pdf)
        return {'total': 0, 'kept': 0, 'rejected': 0}
    
    # Convert static PDF to images
    logger.info(f"   Converting static PDF to images...")
    images = convert_from_path(str(static_pdf), dpi=150)
    page_images = {i: np.array(img) for i, img in enumerate(images)}
    
    # Classify each field
    logger.info(f"   Classifying fields with ML...")
    fields_to_remove = []
    kept_count = 0
    rejected_count = 0
    
    for i, field in enumerate(fields):
        try:
            page_num = field['page']
            bbox = field['bbox']
            
            if page_num not in page_images:
                kept_count += 1
                continue
            
            # Get page image
            page_img = page_images[page_num]
            page_h, page_w = page_img.shape[:2]
            
            # Get page dimensions
            pdf_w, pdf_h = get_page_dimensions(static_pdf, page_num)
            
            # Convert PDF coords to image coords
            x1, y1, x2, y2 = bbox
            y1_img = int((pdf_h - y2) / pdf_h * page_h)
            y2_img = int((pdf_h - y1) / pdf_h * page_h)
            x1_img = int(x1 / pdf_w * page_w)
            x2_img = int(x2 / pdf_w * page_w)
            
            # Crop field
            crop = page_img[max(0, y1_img):min(page_h, y2_img), 
                          max(0, x1_img):min(page_w, x2_img)]
            
            if crop.size == 0:
                kept_count += 1
                continue
            
            # Classify
            class_name, confidence = classifier.classify(crop)
            
            # Reject if classified as "not_a_field" with high confidence
            if class_name == 'not_a_field' and confidence >= threshold:
                fields_to_remove.append(field['name'])
                rejected_count += 1
            else:
                kept_count += 1
                
        except Exception as e:
            # Keep field if error
            kept_count += 1
    
    logger.info(f"   Keeping {kept_count}, rejecting {rejected_count}")
    
    # Create output PDF with filtered fields
    with pikepdf.open(v22_pdf) as pdf:
        if rejected_count > 0 and '/AcroForm' in pdf.Root:
            acroform = pdf.Root.AcroForm
            fields_array = acroform.get('/Fields', [])
            
            # Build new fields array
            new_fields = []
            for field_obj in fields_array:
                try:
                    field_name = str(field_obj.get('/T', ''))
                    if field_name not in fields_to_remove:
                        new_fields.append(field_obj)
                except:
                    new_fields.append(field_obj)
            
            acroform.Fields = pikepdf.Array(new_fields)
        
        pdf.save(output_pdf)
    
    return {
        'total': len(fields),
        'kept': kept_count,
        'rejected': rejected_count
    }


def calculate_accuracy(detected_fields: List[Dict], ground_truth_fields: List[Dict], 
                      iou_threshold: float = 0.5) -> Dict:
    """Calculate precision, recall, F1."""
    
    def calc_iou(box1, box2):
        x1 = max(box1[0], box2[0])
        y1 = max(box1[1], box2[1])
        x2 = min(box1[2], box2[2])
        y2 = min(box1[3], box2[3])
        
        if x2 < x1 or y2 < y1:
            return 0.0
        
        inter = (x2 - x1) * (y2 - y1)
        area1 = (box1[2] - box1[0]) * (box1[3] - box1[1])
        area2 = (box2[2] - box2[0]) * (box2[3] - box2[1])
        union = area1 + area2 - inter
        
        return inter / union if union > 0 else 0.0
    
    # Match detected to ground truth
    true_positives = 0
    matched_gt = set()
    
    for det_field in detected_fields:
        det_bbox = det_field['bbox']
        det_page = det_field['page']
        
        best_iou = 0.0
        best_gt_idx = None
        
        for gt_idx, gt_field in enumerate(ground_truth_fields):
            if gt_idx in matched_gt:
                continue
            
            if gt_field['page'] != det_page:
                continue
            
            iou = calc_iou(det_bbox, gt_field['bbox'])
            if iou > best_iou:
                best_iou = iou
                best_gt_idx = gt_idx
        
        if best_iou >= iou_threshold and best_gt_idx is not None:
            true_positives += 1
            matched_gt.add(best_gt_idx)
    
    precision = true_positives / len(detected_fields) if detected_fields else 0.0
    recall = true_positives / len(ground_truth_fields) if ground_truth_fields else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    
    return {
        'precision': precision * 100,
        'recall': recall * 100,
        'f1': f1 * 100,
        'true_positives': true_positives,
        'detected': len(detected_fields),
        'ground_truth': len(ground_truth_fields)
    }


def test_single_pdf(static_pdf: Path, fillable_pdf: Path, classifier: FieldClassifier,
                   v22_script: Path, threshold: float = 0.7) -> Optional[Dict]:
    """Test refinement on a single PDF pair."""
    
    pdf_name = static_pdf.stem
    logger.info(f"\n{'='*70}")
    logger.info(f"Testing: {pdf_name}")
    logger.info(f"{'='*70}")
    
    # Ground truth
    logger.info(f"📊 Loading ground truth...")
    ground_truth = extract_fields_from_pdf(fillable_pdf)
    logger.info(f"   Ground truth: {len(ground_truth)} fields")
    
    if len(ground_truth) == 0:
        logger.warning(f"   ⚠️  No ground truth fields!")
        return None
    
    # Run v22
    logger.info(f"\n🔧 Running v22...")
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
        v22_output = Path(tmp.name)
    
    try:
        cmd = ["python3", str(v22_script), str(static_pdf), str(v22_output)]
        result = subprocess.run(cmd, capture_output=True, timeout=300)
        
        if result.returncode != 0 or not v22_output.exists():
            logger.error("   ❌ v22 failed")
            return None
        
        v22_fields = extract_fields_from_pdf(v22_output)
        logger.info(f"   v22 detected: {len(v22_fields)} fields")
        
        # Calculate v22 accuracy
        v22_accuracy = calculate_accuracy(v22_fields, ground_truth)
        logger.info(f"   v22 baseline: P={v22_accuracy['precision']:.1f}% R={v22_accuracy['recall']:.1f}% F1={v22_accuracy['f1']:.1f}%")
        
        # Run ML refinement
        logger.info(f"\n🧠 Applying ML refinement...")
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
            refined_output = Path(tmp.name)
        
        stats = refine_pdf_with_ml(static_pdf, v22_output, refined_output, classifier, threshold)
        
        # Calculate refined accuracy
        refined_fields = extract_fields_from_pdf(refined_output)
        refined_accuracy = calculate_accuracy(refined_fields, ground_truth)
        
        logger.info(f"\n📈 Results:")
        logger.info(f"   v22:     P={v22_accuracy['precision']:5.1f}% R={v22_accuracy['recall']:5.1f}% F1={v22_accuracy['f1']:5.1f}%")
        logger.info(f"   Refined: P={refined_accuracy['precision']:5.1f}% R={refined_accuracy['recall']:5.1f}% F1={refined_accuracy['f1']:5.1f}%")
        
        improvement = refined_accuracy['f1'] - v22_accuracy['f1']
        if improvement > 0:
            logger.info(f"   🎯 Improvement: +{improvement:.1f}% F1")
        elif improvement < 0:
            logger.info(f"   ⚠️  Regression: {improvement:.1f}% F1")
        else:
            logger.info(f"   ➡️  No change")
        
        return {
            'pdf_name': pdf_name,
            'ground_truth_count': len(ground_truth),
            'v22': v22_accuracy,
            'refined': refined_accuracy,
            'improvement': improvement,
            'ml_stats': stats
        }
    
    finally:
        if v22_output.exists():
            v22_output.unlink()
        if 'refined_output' in locals() and refined_output.exists():
            refined_output.unlink()


def main():
    parser = argparse.ArgumentParser(description='Batch ML refinement testing')
    parser.add_argument('--classifier', '-c', required=True, help='Path to classifier model')
    parser.add_argument('--threshold', '-t', type=float, default=0.7, 
                       help='Confidence threshold for rejection (default: 0.7)')
    parser.add_argument('--static-dir', default='/Users/36981/Desktop/PDFTest/PDFs to test/Static PDFs')
    parser.add_argument('--fillable-dir', default='/Users/36981/Desktop/PDFTest/PDFs to test/Fillable PDFs')
    parser.add_argument('--v22-script', default='smart_fillable_v22.py')
    parser.add_argument('--output-dir', default='/tmp/batch_refine_output')
    
    args = parser.parse_args()
    
    if not ML_AVAILABLE:
        logger.error("❌ PyTorch required! Install: pip install torch torchvision")
        sys.exit(1)
    
    # Load classifier
    logger.info("🧠 Loading ML classifier...")
    classifier = FieldClassifier(args.classifier)
    
    # Find PDF pairs
    static_dir = Path(args.static_dir)
    fillable_dir = Path(args.fillable_dir)
    v22_script = Path(args.v22_script)
    
    static_pdfs = {f.stem.replace('_v01', '').replace('_v02', '').replace('_v03', '').replace('_v04', '').replace('_v05', '').replace('_v06', '').replace('_v07', '').replace('_v08', '').replace('_v09', '').replace('_v10', '').replace('_v11', '').replace('_v12', '').replace('_v13', '').replace('_v14', '').replace('_v15', '').replace('_v16', '').replace('_v17', '').replace('_v18', '').replace('_v19', '').replace('_v20', '').replace('_v21', '').replace('_v22', '').replace('_v23', '').replace('_v24', '').replace('_v25', '').replace('_v26', '').replace('_v27', '').replace('_v28', '').replace('_v29', '').replace('_v30', ''): f for f in static_dir.glob('*.pdf')}
    
    pairs = []
    for fillable_pdf in fillable_dir.glob('*.pdf'):
        base_name = fillable_pdf.stem.replace('_fillable', '').replace('_Fillable', '').replace('_Web_Release_Fillable', '').replace('_Release_Fillable', '').replace('_v01', '').replace('_v02', '').replace('_v03', '').replace('_v04', '').replace('_v05', '').replace('_v06', '').replace('_v07', '').replace('_v08', '').replace('_v09', '').replace('_v10', '').replace('_v11', '').replace('_v12', '').replace('_v13', '').replace('_v14', '').replace('_v15', '').replace('_v16', '').replace('_v17', '').replace('_v18', '').replace('_v19', '').replace('_v20', '').replace('_v21', '').replace('_v22', '').replace('_v23', '').replace('_v24', '').replace('_v25', '').replace('_v26', '').replace('_v27', '').replace('_v28', '').replace('_v29', '').replace('_v30', '')
        
        if base_name in static_pdfs:
            pairs.append((static_pdfs[base_name], fillable_pdf))
    
    logger.info(f"\n📁 Found {len(pairs)} PDF pairs\n")
    
    # Test each pair
    results = []
    for i, (static_pdf, fillable_pdf) in enumerate(pairs, 1):
        logger.info(f"\n[{i}/{len(pairs)}]")
        result = test_single_pdf(static_pdf, fillable_pdf, classifier, v22_script, args.threshold)
        if result:
            results.append(result)
    
    # Summary
    logger.info(f"\n\n{'='*70}")
    logger.info(f"SUMMARY ({len(results)} PDFs tested)")
    logger.info(f"{'='*70}\n")
    
    avg_v22_f1 = sum(r['v22']['f1'] for r in results) / len(results)
    avg_refined_f1 = sum(r['refined']['f1'] for r in results) / len(results)
    avg_improvement = avg_refined_f1 - avg_v22_f1
    
    logger.info(f"Average v22 F1:     {avg_v22_f1:.1f}%")
    logger.info(f"Average refined F1: {avg_refined_f1:.1f}%")
    logger.info(f"Average improvement: {avg_improvement:+.1f}%")
    
    # Top improvements
    logger.info(f"\n🎯 Top improvements:")
    sorted_results = sorted(results, key=lambda r: r['improvement'], reverse=True)
    for r in sorted_results[:5]:
        logger.info(f"   {r['pdf_name'][:50]:50s} {r['improvement']:+5.1f}% (v22={r['v22']['f1']:.1f}% → refined={r['refined']['f1']:.1f}%)")
    
    # Save report
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    report_path = output_dir / f'batch_refine_report_{timestamp}.json'
    
    with open(report_path, 'w') as f:
        json.dump({
            'timestamp': timestamp,
            'threshold': args.threshold,
            'summary': {
                'total_tested': len(results),
                'avg_v22_f1': avg_v22_f1,
                'avg_refined_f1': avg_refined_f1,
                'avg_improvement': avg_improvement
            },
            'results': results
        }, f, indent=2)
    
    logger.info(f"\n💾 Full report: {report_path}")


if __name__ == "__main__":
    main()
