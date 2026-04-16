#!/usr/bin/env python3
"""
Expand Training Dataset
=======================

Takes the existing training data and expands it with negative examples
(false positive crops from v22 output) to help the classifier learn what
NOT to classify as a field.

This addresses the over-detection problem by teaching the ML model to
reject decorative elements, table borders, etc.

Usage:
    python expand_training_with_negatives.py --v22-outputs /tmp/batch_test_output --classifier-data ./dataset_v3/classifier

Author: FillThatPDF Team
Date: February 2026
"""

import sys
import os
import argparse
import logging
import shutil
from pathlib import Path
from typing import List, Dict, Tuple
from collections import defaultdict
import random

import pikepdf
import pdfplumber
import cv2
import numpy as np
from pdf2image import convert_from_path

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


def extract_fields_with_coords(pdf_path: Path) -> List[Dict]:
    """Extract fields with their coordinates."""
    fields = []
    
    try:
        with pikepdf.open(pdf_path) as pdf:
            if '/AcroForm' not in pdf.Root:
                return fields
            
            acroform = pdf.Root.AcroForm
            field_objs = acroform.get('/Fields', [])
            
            for field_obj in field_objs:
                try:
                    field = pdf.get_object(field_obj) if not isinstance(field_obj, dict) else field_obj
                    
                    if '/Rect' in field:
                        rect = field['/Rect']
                        
                        # Find page
                        page_num = 0
                        for pnum, page in enumerate(pdf.pages):
                            if '/Annots' in page:
                                if field_obj in list(page.Annots):
                                    page_num = pnum
                                    break
                        
                        fields.append({
                            'page': page_num,
                            'rect': [float(r) for r in rect],
                            'x0': float(rect[0]),
                            'y0': float(rect[1]),
                            'x1': float(rect[2]),
                            'y1': float(rect[3]),
                        })
                except:
                    continue
                    
    except Exception as e:
        logger.debug(f"Error extracting fields: {e}")
    
    return fields


def fields_overlap(f1: Dict, f2: Dict, threshold: float = 0.3) -> bool:
    """Check if two fields overlap."""
    if f1['page'] != f2['page']:
        return False
    
    ix0 = max(f1['x0'], f2['x0'])
    iy0 = max(f1['y0'], f2['y0'])
    ix1 = min(f1['x1'], f2['x1'])
    iy1 = min(f1['y1'], f2['y1'])
    
    if ix1 <= ix0 or iy1 <= iy0:
        return False
    
    intersection = (ix1 - ix0) * (iy1 - iy0)
    area1 = (f1['x1'] - f1['x0']) * (f1['y1'] - f1['y0'])
    area2 = (f2['x1'] - f2['x0']) * (f2['y1'] - f2['y0'])
    union = area1 + area2 - intersection
    
    return (intersection / union) >= threshold if union > 0 else False


def find_false_positives(v22_pdf: Path, ground_truth_pdf: Path, 
                         iou_threshold: float = 0.3) -> List[Dict]:
    """Find fields in v22 output that don't match ground truth (false positives)."""
    v22_fields = extract_fields_with_coords(v22_pdf)
    gt_fields = extract_fields_with_coords(ground_truth_pdf)
    
    false_positives = []
    
    for v22_field in v22_fields:
        # Check if this field matches any ground truth field
        is_match = False
        for gt_field in gt_fields:
            if fields_overlap(v22_field, gt_field, iou_threshold):
                is_match = True
                break
        
        if not is_match:
            false_positives.append(v22_field)
    
    return false_positives


def crop_field(page_image: np.ndarray, field: Dict, page_dims: Tuple[float, float],
               padding: int = 5) -> np.ndarray:
    """Crop field region from page image."""
    page_w, page_h = page_dims
    img_h, img_w = page_image.shape[:2]
    
    scale_x = img_w / page_w
    scale_y = img_h / page_h
    
    # PDF coords are bottom-up
    ix0 = int(field['x0'] * scale_x) - padding
    ix1 = int(field['x1'] * scale_x) + padding
    iy0 = int((page_h - field['y1']) * scale_y) - padding
    iy1 = int((page_h - field['y0']) * scale_y) + padding
    
    ix0 = max(0, ix0)
    iy0 = max(0, iy0)
    ix1 = min(img_w, ix1)
    iy1 = min(img_h, iy1)
    
    if ix1 <= ix0 or iy1 <= iy0:
        return None
    
    return page_image[iy0:iy1, ix0:ix1]


def main():
    parser = argparse.ArgumentParser(description="Expand training dataset with negative examples")
    parser.add_argument("--v22-outputs", required=True,
                        help="Directory with v22 output PDFs from batch_test")
    parser.add_argument("--ground-truth-dir", default="/Users/36981/Desktop/PDFTest/PDFs to test/Fillable PDFs",
                        help="Directory with ground truth fillable PDFs")
    parser.add_argument("--static-dir", default="/Users/36981/Desktop/PDFTest/PDFs to test/Static PDFs",
                        help="Directory with static PDFs (for rendering)")
    parser.add_argument("--classifier-data", required=True,
                        help="Classifier data directory (will add to not_a_field folder)")
    parser.add_argument("--max-per-pdf", type=int, default=20,
                        help="Max negative examples per PDF")
    parser.add_argument("--dpi", type=int, default=150,
                        help="DPI for rendering")
    
    args = parser.parse_args()
    
    v22_dir = Path(args.v22_outputs)
    gt_dir = Path(args.ground_truth_dir)
    static_dir = Path(args.static_dir)
    classifier_dir = Path(args.classifier_data)
    
    if not v22_dir.exists():
        logger.error(f"❌ V22 outputs directory not found: {v22_dir}")
        sys.exit(1)
    
    # Create not_a_field folders
    train_neg_dir = classifier_dir / "train" / "not_a_field"
    val_neg_dir = classifier_dir / "val" / "not_a_field"
    train_neg_dir.mkdir(parents=True, exist_ok=True)
    val_neg_dir.mkdir(parents=True, exist_ok=True)
    
    logger.info("🔍 Finding false positives from v22 outputs...")
    
    v22_pdfs = list(v22_dir.glob("*_v22_output.pdf"))
    logger.info(f"   Found {len(v22_pdfs)} v22 output PDFs")
    
    total_negatives = 0
    train_count = 0
    val_count = 0
    
    for v22_pdf in v22_pdfs:
        # Find matching ground truth and static PDF
        # Extract base name (e.g., "55570_DTE_..." from "55570_DTE_..._v22_output.pdf")
        base_name = v22_pdf.stem.replace("_v22_output", "")
        
        # Find ground truth by ID
        pdf_id = base_name.split('_')[0]
        gt_pdfs = list(gt_dir.glob(f"{pdf_id}_*.pdf"))
        
        if not gt_pdfs:
            logger.warning(f"   ⚠️ No ground truth found for {base_name}")
            continue
        
        gt_pdf = gt_pdfs[0]
        
        # Find static PDF
        static_pdf = static_dir / f"{base_name}.pdf"
        if not static_pdf.exists():
            logger.warning(f"   ⚠️ Static PDF not found: {static_pdf.name}")
            continue
        
        logger.info(f"\n   Processing: {base_name}")
        
        # Find false positives
        false_positives = find_false_positives(v22_pdf, gt_pdf)
        
        if not false_positives:
            logger.info(f"      No false positives (perfect detection!)")
            continue
        
        logger.info(f"      Found {len(false_positives)} false positives")
        
        # Limit to max_per_pdf
        if len(false_positives) > args.max_per_pdf:
            false_positives = random.sample(false_positives, args.max_per_pdf)
        
        # Render static PDF
        try:
            images = convert_from_path(str(static_pdf), dpi=args.dpi)
        except Exception as e:
            logger.warning(f"      ⚠️ Could not render: {e}")
            continue
        
        # Get page dimensions
        with pdfplumber.open(static_pdf) as pdf:
            page_dims = [(float(p.width), float(p.height)) for p in pdf.pages]
        
        # Crop false positives
        saved = 0
        for i, fp in enumerate(false_positives):
            page_num = fp['page']
            
            if page_num >= len(images):
                continue
            
            page_img = np.array(images[page_num])
            crop = crop_field(page_img, fp, page_dims[page_num])
            
            if crop is None or crop.size == 0:
                continue
            
            # 80/20 train/val split
            if random.random() < 0.8:
                output_dir = train_neg_dir
                train_count += 1
            else:
                output_dir = val_neg_dir
                val_count += 1
            
            output_path = output_dir / f"{base_name}_fp_{i}.png"
            cv2.imwrite(str(output_path), cv2.cvtColor(crop, cv2.COLOR_RGB2BGR))
            saved += 1
        
        logger.info(f"      Saved {saved} negative examples")
        total_negatives += saved
    
    logger.info(f"\n✅ Complete!")
    logger.info(f"   Total negative examples: {total_negatives}")
    logger.info(f"   Train: {train_count}")
    logger.info(f"   Val: {val_count}")
    logger.info(f"\n   Added to:")
    logger.info(f"      {train_neg_dir}")
    logger.info(f"      {val_neg_dir}")


if __name__ == "__main__":
    main()
