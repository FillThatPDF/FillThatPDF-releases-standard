#!/usr/bin/env python3
"""
Apply ML Refinement to Batch Test Results
==========================================

Takes v22 outputs from batch_test_accuracy.py and applies ML filtering.
Tests on the 9 well-performing PDFs to push 75% → 80% F1.

Usage:
    python apply_ml_refinement.py --batch-report /tmp/batch_test_output/batch_test_report_*.json

Author: FillThatPDF Team
Date: February 2026
"""

import os
import sys
import json
import logging
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from datetime import datetime

import pikepdf
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

# PDFs to refine (those scoring 70%+ F1)
TARGET_IDS = ['55570', '57618', '11691', '57769', '56380', '53252', '32775', '14792', '9787']


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
        self.model.load_state_dict(torch.load(model_path, map_location=self.device, weights_only=True))
        self.model.to(self.device)
        self.model.eval()
        
        # Transforms
        self.transform = transforms.Compose([
            transforms.ToPILImage(),
            transforms.Resize((224, 224)),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
        ])
        
        logger.info(f"   ✓ Loaded ML classifier ({self.num_classes} classes)")
    
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


def get_field_boxes_from_pdf(pdf_path: Path) -> List[Dict]:
    """Extract field bounding boxes from PDF using page annotations."""
    fields = []
    
    try:
        with pikepdf.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages):
                if "/Annots" not in page:
                    continue
                
                for annot in page.Annots:
                    try:
                        if annot.get("/Subtype") != "/Widget":
                            continue
                        
                        rect = annot.get("/Rect", None)
                        if rect is None:
                            continue
                        
                        bbox = [float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])]
                        
                        # Normalize bbox order
                        if bbox[0] > bbox[2]:
                            bbox[0], bbox[2] = bbox[2], bbox[0]
                        if bbox[1] > bbox[3]:
                            bbox[1], bbox[3] = bbox[3], bbox[1]
                        
                        field_name = str(annot.get("/T", f"Field_{len(fields)}"))
                        
                        fields.append({
                            'name': field_name,
                            'bbox': bbox,
                            'page': page_num
                        })
                    
                    except Exception as e:
                        continue
    
    except Exception as e:
        logger.error(f"   ❌ Error reading PDF: {e}")
    
    return fields


def refine_pdf_with_ml(static_pdf: Path, v22_pdf: Path, output_pdf: Path, 
                       classifier: FieldClassifier, threshold: float = 0.7) -> Dict:
    """Apply ML filtering to v22 output."""
    
    # Extract fields from v22
    fields = get_field_boxes_from_pdf(v22_pdf)
    
    if len(fields) == 0:
        # No fields, just copy
        with pikepdf.open(v22_pdf) as pdf:
            pdf.save(output_pdf)
        return {'total': 0, 'kept': 0, 'rejected': 0}
    
    logger.info(f"   📋 Found {len(fields)} fields in v22 output")
    
    # Convert static PDF to images
    logger.info(f"   🖼️  Converting static PDF to images...")
    images = convert_from_path(str(static_pdf), dpi=150)
    page_images = {}
    for i, img in enumerate(images):
        page_images[i] = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    
    # Classify each field
    logger.info(f"   🧠 Classifying {len(fields)} fields...")
    fields_to_remove = set()
    kept = 0
    rejected = 0
    
    for field in fields:
        try:
            page_num = field['page']
            bbox = field['bbox']
            field_name = field['name']
            
            if page_num not in page_images:
                kept += 1
                continue
            
            page_img = page_images[page_num]
            page_h, page_w = page_img.shape[:2]
            
            # Get PDF page dimensions from first page (assume consistent)
            with pikepdf.open(v22_pdf) as pdf:
                page = pdf.pages[page_num]
                mediabox = page.MediaBox
                pdf_w = float(mediabox[2] - mediabox[0])
                pdf_h = float(mediabox[3] - mediabox[1])
            
            # Convert PDF coords to image coords
            # PDF: origin bottom-left, bbox is [x1, y1, x2, y2]
            # Image: origin top-left
            x1, y1, x2, y2 = bbox
            x1_img = int(x1 / pdf_w * page_w)
            x2_img = int(x2 / pdf_w * page_w)
            y1_img = int((pdf_h - y2) / pdf_h * page_h)
            y2_img = int((pdf_h - y1) / pdf_h * page_h)
            
            # Ensure valid bounds
            x1_img = max(0, min(x1_img, page_w - 1))
            x2_img = max(0, min(x2_img, page_w))
            y1_img = max(0, min(y1_img, page_h - 1))
            y2_img = max(0, min(y2_img, page_h))
            
            if x2_img <= x1_img or y2_img <= y1_img:
                kept += 1
                continue
            
            # Crop field region
            crop = page_img[y1_img:y2_img, x1_img:x2_img]
            
            if crop.size == 0:
                kept += 1
                continue
            
            # Classify
            class_name, confidence = classifier.classify(crop)
            
            # Reject if "not_a_field" with high confidence
            if class_name == 'not_a_field' and confidence >= threshold:
                fields_to_remove.add(field_name)
                rejected += 1
            else:
                kept += 1
        
        except Exception as e:
            # Keep field on error
            kept += 1
    
    logger.info(f"   ✂️  Keeping {kept}, rejecting {rejected}")
    
    # Create refined PDF by removing rejected fields
    with pikepdf.open(v22_pdf) as pdf:
        if rejected > 0 and '/AcroForm' in pdf.Root:
            acroform = pdf.Root.AcroForm
            
            if '/Fields' in acroform:
                old_fields = list(acroform.Fields)
                new_fields = []
                
                for field_ref in old_fields:
                    try:
                        field_name = str(field_ref.get('/T', ''))
                        if field_name not in fields_to_remove:
                            new_fields.append(field_ref)
                    except:
                        new_fields.append(field_ref)
                
                acroform.Fields = pikepdf.Array(new_fields)
        
        pdf.save(output_pdf)
    
    return {'total': len(fields), 'kept': kept, 'rejected': rejected}


def count_pdf_fields(pdf_path: Path) -> int:
    """Count fields in PDF."""
    try:
        with pikepdf.open(pdf_path) as pdf:
            if '/AcroForm' not in pdf.Root:
                return 0
            acroform = pdf.Root.AcroForm
            fields = acroform.get('/Fields', [])
            return len(fields) if fields else 0
    except:
        return 0


def calculate_iou(box1: List[float], box2: List[float]) -> float:
    """Calculate IoU between two bounding boxes."""
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


def calculate_accuracy(detected_pdf: Path, ground_truth_pdf: Path, iou_threshold: float = 0.5) -> Dict:
    """Calculate precision, recall, F1."""
    
    detected_fields = get_field_boxes_from_pdf(detected_pdf)
    gt_fields = get_field_boxes_from_pdf(ground_truth_pdf)
    
    if len(detected_fields) == 0 or len(gt_fields) == 0:
        return {
            'precision': 0.0,
            'recall': 0.0,
            'f1': 0.0,
            'detected': len(detected_fields),
            'ground_truth': len(gt_fields)
        }
    
    # Match detected to ground truth
    true_positives = 0
    matched_gt = set()
    
    for det_field in detected_fields:
        det_bbox = det_field['bbox']
        det_page = det_field['page']
        
        best_iou = 0.0
        best_gt_idx = None
        
        for gt_idx, gt_field in enumerate(gt_fields):
            if gt_idx in matched_gt:
                continue
            
            if gt_field['page'] != det_page:
                continue
            
            iou = calculate_iou(det_bbox, gt_field['bbox'])
            if iou > best_iou:
                best_iou = iou
                best_gt_idx = gt_idx
        
        if best_iou >= iou_threshold and best_gt_idx is not None:
            true_positives += 1
            matched_gt.add(best_gt_idx)
    
    precision = true_positives / len(detected_fields) if detected_fields else 0.0
    recall = true_positives / len(gt_fields) if gt_fields else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    
    return {
        'precision': precision * 100,
        'recall': recall * 100,
        'f1': f1 * 100,
        'detected': len(detected_fields),
        'ground_truth': len(gt_fields)
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Apply ML refinement to batch test results')
    parser.add_argument('--batch-report', required=True, help='Path to batch_test_report JSON')
    parser.add_argument('--classifier', default='classifier_model.pt', help='Path to classifier model')
    parser.add_argument('--threshold', type=float, default=0.7, help='Confidence threshold')
    parser.add_argument('--output-dir', default='/tmp/batch_refine_output', help='Output directory')
    
    args = parser.parse_args()
    
    if not ML_AVAILABLE:
        logger.error("❌ PyTorch required! Install: pip install torch torchvision pdf2image")
        sys.exit(1)
    
    # Load batch report
    report_path = Path(args.batch_report)
    if not report_path.exists():
        logger.error(f"❌ Report not found: {report_path}")
        sys.exit(1)
    
    with open(report_path) as f:
        batch_data = json.load(f)
    
    # Load classifier
    logger.info("🧠 Loading ML classifier...")
    classifier_path = Path(args.classifier)
    if not classifier_path.is_absolute():
        classifier_path = Path(__file__).parent / classifier_path
    
    classifier = FieldClassifier(str(classifier_path))
    
    # Create output directory
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Filter results to target PDFs (70%+ F1)
    results = batch_data.get('results', [])
    target_results = []
    for r in results:
        pdf_name = r.get('pdf', '')
        pdf_id = pdf_name.split('_')[0]
        if pdf_id in TARGET_IDS and r.get('status') == 'success' and r.get('f1', 0) >= 70:
            target_results.append(r)
    
    logger.info(f"📁 Processing {len(target_results)} PDFs (70%+ F1)\n")
    
    # Process each PDF
    refined_results = []
    
    for i, result in enumerate(target_results, 1):
        pdf_name = result['pdf']
        logger.info(f"\n[{i}/{len(target_results)}] {pdf_name[:60]}")
        logger.info("=" * 70)
        
        static_path = Path(result['static_path'])
        fillable_path = Path(result['fillable_path'])
        v22_output = Path(result['output_path'])
        
        if not v22_output.exists():
            logger.warning(f"   ⚠️  v22 output not found: {v22_output}")
            continue
        
        # Original v22 metrics
        v22_metrics = {
            'precision': result['precision'],
            'recall': result['recall'],
            'f1': result['f1'],
            'detected': result['detected_fields']
        }
        
        logger.info(f"   v22 baseline: P={v22_metrics['precision']:.1f}% R={v22_metrics['recall']:.1f}% F1={v22_metrics['f1']:.1f}%")
        
        # Apply ML refinement
        refined_path = output_dir / f"{Path(pdf_name).stem}_refined.pdf"
        
        logger.info(f"   🔧 Applying ML refinement...")
        ml_stats = refine_pdf_with_ml(static_path, v22_output, refined_path, classifier, args.threshold)
        
        # Calculate refined metrics
        logger.info(f"   📊 Calculating refined accuracy...")
        refined_metrics = calculate_accuracy(refined_path, fillable_path)
        
        # Show results
        logger.info(f"\n   📈 Results:")
        logger.info(f"      v22:     P={v22_metrics['precision']:5.1f}% R={v22_metrics['recall']:5.1f}% F1={v22_metrics['f1']:5.1f}%")
        logger.info(f"      Refined: P={refined_metrics['precision']:5.1f}% R={refined_metrics['recall']:5.1f}% F1={refined_metrics['f1']:5.1f}%")
        
        improvement = refined_metrics['f1'] - v22_metrics['f1']
        if improvement > 0:
            logger.info(f"      🎯 Improvement: +{improvement:.1f}% F1")
        elif improvement < 0:
            logger.info(f"      ⚠️  Regression: {improvement:.1f}% F1")
        else:
            logger.info(f"      ➡️  No change")
        
        refined_results.append({
            'pdf': pdf_name,
            'v22': v22_metrics,
            'refined': refined_metrics,
            'improvement': improvement,
            'ml_stats': ml_stats,
            'refined_path': str(refined_path)
        })
    
    # Summary
    logger.info(f"\n\n{'='*70}")
    logger.info(f"SUMMARY ({len(refined_results)} PDFs)")
    logger.info(f"{'='*70}\n")
    
    if refined_results:
        avg_v22_f1 = sum(r['v22']['f1'] for r in refined_results) / len(refined_results)
        avg_refined_f1 = sum(r['refined']['f1'] for r in refined_results) / len(refined_results)
        avg_improvement = avg_refined_f1 - avg_v22_f1
        
        logger.info(f"Average v22 F1:     {avg_v22_f1:.1f}%")
        logger.info(f"Average refined F1: {avg_refined_f1:.1f}%")
        logger.info(f"Average improvement: {avg_improvement:+.1f}%\n")
        
        # Show top improvements
        logger.info("🎯 Top improvements:")
        sorted_results = sorted(refined_results, key=lambda r: r['improvement'], reverse=True)
        for r in sorted_results[:5]:
            logger.info(f"   {Path(r['pdf']).stem[:45]:45s} {r['improvement']:+5.1f}% (v22={r['v22']['f1']:.1f}% → {r['refined']['f1']:.1f}%)")
        
        # Save report
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        report_file = output_dir / f'refinement_report_{timestamp}.json'
        
        with open(report_file, 'w') as f:
            json.dump({
                'timestamp': timestamp,
                'threshold': args.threshold,
                'summary': {
                    'total_pdfs': len(refined_results),
                    'avg_v22_f1': avg_v22_f1,
                    'avg_refined_f1': avg_refined_f1,
                    'avg_improvement': avg_improvement
                },
                'results': refined_results
            }, f, indent=2)
        
        logger.info(f"\n💾 Report saved: {report_file}")
    
    logger.info(f"💾 Refined PDFs saved to: {output_dir}")


if __name__ == "__main__":
    main()
