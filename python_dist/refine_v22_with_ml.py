#!/usr/bin/env python3
"""
V22 + ML Refiner
================

Uses the ML classifier to FILTER v22's output, removing false positives
while keeping v22's excellent recall.

Strategy:
1. Run v22 (high recall, some false positives)
2. For each detected field, crop the image
3. ML classifier votes: keep or reject
4. Remove fields classified as "not_a_field" with high confidence

Usage:
    python refine_v22_with_ml.py input.pdf output.pdf --classifier ./classifier_model.pt

Author: FillThatPDF Team
Date: February 2026
"""

import sys
import os
import argparse
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import List, Dict, Tuple
import shutil

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
    """ML field classifier for filtering."""
    
    CLASS_NAMES = ["text", "checkbox", "radio", "dropdown", "not_a_field"]
    
    def __init__(self, model_path: str):
        self.model = None
        self.transform = None
        self.device = "cpu"
        
        if ML_AVAILABLE:
            self._load_model(model_path)
    
    def _load_model(self, model_path: str):
        try:
            self.device = "mps" if torch.backends.mps.is_available() else "cpu"
            
            self.model = models.mobilenet_v3_small(weights=None)
            self.model.classifier[-1] = nn.Linear(
                self.model.classifier[-1].in_features,
                len(self.CLASS_NAMES)
            )
            
            state_dict = torch.load(model_path, map_location=self.device)
            self.model.load_state_dict(state_dict)
            self.model.to(self.device)
            self.model.eval()
            
            self.transform = transforms.Compose([
                transforms.ToPILImage(),
                transforms.Resize((64, 128)),
                transforms.ToTensor(),
                transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
            ])
            
            logger.info(f"   ✓ Loaded ML classifier")
        except Exception as e:
            logger.warning(f"   ⚠️ Could not load classifier: {e}")
            self.model = None
    
    def should_keep_field(self, image: np.ndarray, threshold: float = 0.7) -> Tuple[bool, float]:
        """
        Classify if field should be kept.
        Returns: (should_keep, confidence)
        """
        if self.model is None:
            return True, 1.0
        
        try:
            # Ensure RGB
            if len(image.shape) == 2:
                image = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
            elif image.shape[2] == 4:
                image = cv2.cvtColor(image, cv2.COLOR_BGRA2RGB)
            elif image.shape[2] == 3:
                image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            
            tensor = self.transform(image).unsqueeze(0).to(self.device)
            
            with torch.no_grad():
                outputs = self.model(tensor)
                probs = torch.softmax(outputs, dim=1)
                conf, pred_idx = probs.max(1)
            
            class_name = self.CLASS_NAMES[pred_idx.item()]
            confidence = conf.item()
            
            # Reject if classified as "not_a_field" with high confidence
            if class_name == "not_a_field" and confidence >= threshold:
                return False, confidence
            
            return True, confidence
            
        except Exception as e:
            return True, 0.0


def extract_fields(pdf_path: Path) -> List[Dict]:
    """Extract all fields from a PDF."""
    fields = []
    
    try:
        with pikepdf.open(pdf_path) as pdf:
            if '/AcroForm' not in pdf.Root:
                return fields
            
            acroform = pdf.Root.AcroForm
            annots_by_page = {}
            
            for page_num, page in enumerate(pdf.pages):
                if '/Annots' in page:
                    annots_by_page[page_num] = list(page.Annots)
            
            field_objs = acroform.get('/Fields', [])
            
            for field_obj in field_objs:
                try:
                    field = pdf.get_object(field_obj) if not isinstance(field_obj, dict) else field_obj
                    
                    if '/Rect' in field:
                        rect = field['/Rect']
                        name = str(field.get('/T', ''))
                        
                        # Find which page this field is on
                        page_num = 0
                        for pnum, annots in annots_by_page.items():
                            if field_obj in annots:
                                page_num = pnum
                                break
                        
                        fields.append({
                            'name': name,
                            'rect': [float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])],
                            'page': page_num,
                            'obj': field_obj
                        })
                except:
                    continue
                    
    except Exception as e:
        logger.error(f"Error extracting fields: {e}")
    
    return fields


def crop_field_image(page_image: np.ndarray, field: Dict, page_dims: Tuple[float, float], 
                     padding: int = 5) -> np.ndarray:
    """Crop field region from page image."""
    page_w, page_h = page_dims
    img_h, img_w = page_image.shape[:2]
    
    scale_x = img_w / page_w
    scale_y = img_h / page_h
    
    x0, y0, x1, y1 = field['rect']
    
    # PDF coords are bottom-up, image is top-down
    ix0 = int(x0 * scale_x) - padding
    ix1 = int(x1 * scale_x) + padding
    iy0 = int((page_h - y1) * scale_y) - padding
    iy1 = int((page_h - y0) * scale_y) + padding
    
    # Clamp
    ix0 = max(0, ix0)
    iy0 = max(0, iy0)
    ix1 = min(img_w, ix1)
    iy1 = min(img_h, iy1)
    
    if ix1 <= ix0 or iy1 <= iy0:
        return None
    
    return page_image[iy0:iy1, ix0:ix1]


def refine_v22_output(v22_pdf: Path, output_pdf: Path, classifier: FieldClassifier,
                      rejection_threshold: float = 0.7, dpi: int = 150) -> Dict:
    """
    Refine v22 output using ML classifier.
    """
    logger.info("🔬 Refining v22 output with ML classifier...")
    
    # Extract fields
    fields = extract_fields(v22_pdf)
    logger.info(f"   v22 detected {len(fields)} fields")
    
    if not fields:
        shutil.copy(v22_pdf, output_pdf)
        return {'kept': 0, 'rejected': 0}
    
    # Render PDF pages
    logger.info("   Rendering pages...")
    try:
        images = convert_from_path(str(v22_pdf), dpi=dpi)
    except Exception as e:
        logger.error(f"   ❌ Could not render PDF: {e}")
        shutil.copy(v22_pdf, output_pdf)
        return {'kept': len(fields), 'rejected': 0}
    
    # Get page dimensions
    with pdfplumber.open(v22_pdf) as pdf:
        page_dims = [(float(p.width), float(p.height)) for p in pdf.pages]
    
    # Classify each field
    logger.info("   Classifying fields...")
    fields_to_remove = []
    kept_count = 0
    rejected_count = 0
    
    for field in fields:
        page_num = field['page']
        
        if page_num >= len(images):
            kept_count += 1
            continue
        
        page_img = np.array(images[page_num])
        page_w, page_h = page_dims[page_num]
        
        crop = crop_field_image(page_img, field, (page_w, page_h))
        
        if crop is None or crop.size == 0:
            kept_count += 1
            continue
        
        should_keep, confidence = classifier.should_keep_field(crop, rejection_threshold)
        
        if not should_keep:
            fields_to_remove.append(field['obj'])
            rejected_count += 1
        else:
            kept_count += 1
    
    logger.info(f"   Keeping {kept_count} fields, rejecting {rejected_count}")
    
    # Create output PDF with filtered fields
    with pikepdf.open(v22_pdf) as pdf:
        if rejected_count > 0 and '/AcroForm' in pdf.Root:
            acroform = pdf.Root.AcroForm
            fields_array = acroform.get('/Fields', [])
            
            # Remove rejected fields
            new_fields = [f for f in fields_array if f not in fields_to_remove]
            acroform.Fields = pikepdf.Array(new_fields)
            
            # Also remove from page annotations
            for page in pdf.pages:
                if '/Annots' in page:
                    annots = list(page.Annots)
                    new_annots = [a for a in annots if a not in fields_to_remove]
                    page.Annots = pikepdf.Array(new_annots)
        
        pdf.save(output_pdf)
    
    return {'kept': kept_count, 'rejected': rejected_count}


def main():
    parser = argparse.ArgumentParser(
        description="Refine v22 output using ML classifier",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("input_pdf", help="Input static PDF")
    parser.add_argument("output_pdf", help="Output refined fillable PDF")
    parser.add_argument("--classifier", "-c", required=True,
                        help="Path to trained classifier model (.pt)")
    parser.add_argument("--threshold", "-t", type=float, default=0.7,
                        help="Rejection confidence threshold (default: 0.7)")
    parser.add_argument("--v22-script", default="./smart_fillable_v22.py",
                        help="Path to v22 script")
    
    args = parser.parse_args()
    
    if not ML_AVAILABLE:
        logger.error("❌ PyTorch required! Install: pip install torch torchvision")
        sys.exit(1)
    
    input_path = Path(args.input_pdf)
    output_path = Path(args.output_pdf)
    v22_script = Path(args.v22_script)
    
    if not input_path.exists():
        logger.error(f"❌ Input PDF not found: {input_path}")
        sys.exit(1)
    
    logger.info(f"🚀 V22 + ML Refiner")
    logger.info("=" * 60)
    
    # Step 1: Run v22
    logger.info(f"\n📋 Step 1: Running v22...")
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
        v22_output = Path(tmp.name)
    
    try:
        cmd = ["python3", str(v22_script), str(input_path), str(v22_output)]
        result = subprocess.run(cmd, capture_output=True, timeout=300)
        
        if result.returncode != 0 or not v22_output.exists():
            logger.error("❌ v22 failed!")
            sys.exit(1)
        
        logger.info("   ✓ v22 complete")
        
        # Step 2: Load classifier
        logger.info(f"\n🧠 Step 2: Loading ML classifier...")
        classifier = FieldClassifier(args.classifier)
        
        # Step 3: Refine
        logger.info(f"\n🔍 Step 3: Refining output...")
        stats = refine_v22_output(v22_output, output_path, classifier, args.threshold)
        
        # Summary
        logger.info(f"\n✅ Complete!")
        logger.info(f"   Original: {stats['kept'] + stats['rejected']} fields")
        logger.info(f"   Kept: {stats['kept']} fields")
        logger.info(f"   Rejected: {stats['rejected']} fields")
        logger.info(f"   Output: {output_path}")
        
    finally:
        if v22_output.exists():
            v22_output.unlink()


if __name__ == "__main__":
    main()
