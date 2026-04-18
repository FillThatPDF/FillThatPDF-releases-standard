#!/usr/bin/env python3
"""
Robust Training Data Harvester v2.0
====================================

Creates properly matched training data for FillThatPDF AI training.

KEY IMPROVEMENTS over original harvester:
1. Validates Static/Fillable pairs have matching page counts
2. Uses Static PDF images (no visual field artifacts)
3. Extracts precise field coordinates from Fillable PDF
4. Supports data augmentation for larger datasets
5. Creates classifier training data (cropped field images)

Output Structure:
  dataset/
    images/train/          # Full page images
    images/val/
    labels/train/          # YOLO format labels
    labels/val/
    classifier/train/      # Cropped field images for classifier
      text/
      checkbox/
      radio/
      dropdown/
    classifier/val/
    data.yaml              # YOLO config
    pairs.json             # Metadata about matched pairs

Usage:
    python harvest_training_v2.py --output ./dataset_v2
    python harvest_training_v2.py --output ./dataset_v2 --augment 5
"""

import os
import sys
import json
import random
import argparse
from pathlib import Path
from collections import defaultdict
from typing import List, Dict, Tuple, Optional
import shutil

import pikepdf
import cv2
import numpy as np
from pdf2image import convert_from_path

# Directories
STATIC_DIR = Path("/Users/36981/Desktop/PDFTest/PDFs to test/Static PDFs")
FILLABLE_DIR = Path("/Users/36981/Desktop/PDFTest/PDFs to test/Fillable PDFs")


def find_matching_fillable(static_path: Path) -> Optional[Path]:
    """Find the best matching fillable PDF for a static PDF."""
    import re
    
    static_name = static_path.name
    form_id_match = re.match(r'^(\d+)', static_name)
    if not form_id_match:
        return None
    
    form_id = form_id_match.group(1)
    
    # Get all fillable files with same form ID
    candidates = list(FILLABLE_DIR.glob(f"{form_id}*.pdf"))
    
    if not candidates:
        return None
    
    if len(candidates) == 1:
        return candidates[0]
    
    # Try to match version
    version_match = re.search(r'_v(\d+)', static_name.lower())
    if version_match:
        version = version_match.group(1)
        for cand in candidates:
            if f"_v{version}" in cand.name.lower():
                return cand
    
    return candidates[0]


def extract_fields_from_fillable(fillable_path: Path) -> Dict[int, List[Dict]]:
    """Extract field information from fillable PDF, organized by page."""
    fields_by_page = defaultdict(list)
    
    with pikepdf.open(fillable_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            # Get page dimensions
            mb = page.MediaBox
            page_width = float(mb[2])
            page_height = float(mb[3])
            
            if "/Annots" not in page:
                continue
            
            for annot in page.Annots:
                if annot.get("/Subtype") != "/Widget":
                    continue
                
                rect = [float(x) for x in annot.get("/Rect", [0, 0, 0, 0])]
                x0, y0, x1, y1 = rect
                
                # Normalize order
                x0, x1 = min(x0, x1), max(x0, x1)
                y0, y1 = min(y0, y1), max(y0, y1)
                
                # Determine field type
                ft = str(annot.get("/FT", "")) if "/FT" in annot else ""
                ff = int(annot.get("/Ff", 0)) if "/Ff" in annot else 0
                
                if ft == "/Btn":
                    field_type = "radio" if (ff & 32768) else "checkbox"
                    class_id = 2 if (ff & 32768) else 1
                elif ft == "/Ch":
                    field_type = "dropdown"
                    class_id = 3
                else:
                    field_type = "text"
                    class_id = 0
                
                name = str(annot.get("/T", "")) if "/T" in annot else ""
                
                fields_by_page[page_num].append({
                    "rect_pdf": [x0, y0, x1, y1],  # PDF coords (bottom-up)
                    "page_width": page_width,
                    "page_height": page_height,
                    "field_type": field_type,
                    "class_id": class_id,
                    "name": name
                })
    
    return dict(fields_by_page)


def pdf_to_yolo_coords(rect_pdf: List[float], page_width: float, page_height: float) -> Tuple[float, float, float, float]:
    """
    Convert PDF coordinates to YOLO normalized format.
    
    PDF: (0,0) at bottom-left, y increases upward
    YOLO: normalized (0-1), (0,0) at top-left, y increases downward
    """
    x0, y0, x1, y1 = rect_pdf
    
    # Convert Y from bottom-up to top-down
    y0_td = page_height - y1  # Top in top-down coords
    y1_td = page_height - y0  # Bottom in top-down coords
    
    # Calculate center and size
    cx = (x0 + x1) / 2
    cy = (y0_td + y1_td) / 2
    w = x1 - x0
    h = y1_td - y0_td
    
    # Normalize to 0-1
    cx_norm = cx / page_width
    cy_norm = cy / page_height
    w_norm = w / page_width
    h_norm = h / page_height
    
    # Clamp to valid range
    cx_norm = max(0, min(1, cx_norm))
    cy_norm = max(0, min(1, cy_norm))
    w_norm = max(0.001, min(1, w_norm))
    h_norm = max(0.001, min(1, h_norm))
    
    return cx_norm, cy_norm, w_norm, h_norm


def apply_augmentation(image: np.ndarray, aug_type: str) -> np.ndarray:
    """Apply augmentation to image."""
    if aug_type == "original":
        return image
    elif aug_type == "brightness_up":
        return cv2.convertScaleAbs(image, alpha=1.2, beta=20)
    elif aug_type == "brightness_down":
        return cv2.convertScaleAbs(image, alpha=0.8, beta=-20)
    elif aug_type == "blur":
        return cv2.GaussianBlur(image, (3, 3), 0)
    elif aug_type == "noise":
        noise = np.random.normal(0, 10, image.shape).astype(np.uint8)
        return cv2.add(image, noise)
    elif aug_type == "contrast":
        lab = cv2.cvtColor(image, cv2.COLOR_RGB2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        l = clahe.apply(l)
        return cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2RGB)
    return image


def crop_field_image(page_image: np.ndarray, rect_pdf: List[float], 
                     page_width: float, page_height: float, 
                     padding: int = 5) -> Optional[np.ndarray]:
    """Crop a field region from page image for classifier training."""
    img_h, img_w = page_image.shape[:2]
    scale_x = img_w / page_width
    scale_y = img_h / page_height
    
    x0, y0, x1, y1 = rect_pdf
    
    # Convert to image coordinates (top-down)
    ix0 = int(x0 * scale_x) - padding
    ix1 = int(x1 * scale_x) + padding
    iy0 = int((page_height - y1) * scale_y) - padding
    iy1 = int((page_height - y0) * scale_y) + padding
    
    # Clamp to image bounds
    ix0 = max(0, ix0)
    iy0 = max(0, iy0)
    ix1 = min(img_w, ix1)
    iy1 = min(img_h, iy1)
    
    if ix1 <= ix0 or iy1 <= iy0:
        return None
    
    return page_image[iy0:iy1, ix0:ix1]


def process_pair(static_path: Path, fillable_path: Path, 
                 output_dir: Path, split: str,
                 augmentations: List[str],
                 dpi: int = 200) -> Dict:
    """Process a single Static/Fillable pair."""
    
    # Extract fields from fillable
    fields_by_page = extract_fields_from_fillable(fillable_path)
    
    if not fields_by_page:
        print(f"   ⚠️ No fields found in {fillable_path.name}")
        return {"pages": 0, "fields": 0}
    
    # Render static PDF to images
    try:
        images = convert_from_path(str(static_path), dpi=dpi)
    except Exception as e:
        print(f"   ❌ Error rendering {static_path.name}: {e}")
        return {"pages": 0, "fields": 0}
    
    stats = {"pages": 0, "fields": 0, "classifier_crops": 0}
    stem = static_path.stem
    
    for page_num, page_fields in fields_by_page.items():
        if page_num >= len(images):
            print(f"   ⚠️ Page {page_num + 1} out of range for {static_path.name}")
            continue
        
        page_image = np.array(images[page_num])
        page_width = page_fields[0]["page_width"]
        page_height = page_fields[0]["page_height"]
        
        for aug_idx, aug_type in enumerate(augmentations):
            aug_suffix = f"_aug{aug_idx}" if aug_type != "original" else ""
            
            # Apply augmentation
            aug_image = apply_augmentation(page_image.copy(), aug_type)
            
            # Save page image
            image_name = f"{stem}_p{page_num + 1}{aug_suffix}.jpg"
            image_path = output_dir / "images" / split / image_name
            cv2.imwrite(str(image_path), cv2.cvtColor(aug_image, cv2.COLOR_RGB2BGR))
            
            # Create YOLO label
            labels = []
            for field in page_fields:
                cx, cy, w, h = pdf_to_yolo_coords(
                    field["rect_pdf"], page_width, page_height
                )
                class_id = field["class_id"]
                labels.append(f"{class_id} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")
            
            # Save label file
            label_name = f"{stem}_p{page_num + 1}{aug_suffix}.txt"
            label_path = output_dir / "labels" / split / label_name
            with open(label_path, "w") as f:
                f.write("\n".join(labels))
            
            stats["pages"] += 1
            stats["fields"] += len(labels)
        
        # Save classifier crops (only for original, not augmented)
        for field in page_fields:
            crop = crop_field_image(page_image, field["rect_pdf"], page_width, page_height)
            if crop is not None and crop.size > 0:
                field_type = field["field_type"]
                crop_dir = output_dir / "classifier" / split / field_type
                crop_dir.mkdir(parents=True, exist_ok=True)
                
                crop_name = f"{stem}_p{page_num + 1}_{stats['classifier_crops']}.jpg"
                crop_path = crop_dir / crop_name
                cv2.imwrite(str(crop_path), cv2.cvtColor(crop, cv2.COLOR_RGB2BGR))
                stats["classifier_crops"] += 1
    
    return stats


def main():
    parser = argparse.ArgumentParser(description="Harvest training data from matched PDF pairs")
    parser.add_argument("--output", "-o", type=str, default="./dataset_v2",
                        help="Output directory for dataset")
    parser.add_argument("--augment", "-a", type=int, default=1,
                        help="Augmentation multiplier (1=no augmentation, 5=5x data)")
    parser.add_argument("--dpi", type=int, default=200,
                        help="DPI for rendering (default: 200)")
    parser.add_argument("--val-split", type=float, default=0.2,
                        help="Validation split ratio (default: 0.2)")
    
    args = parser.parse_args()
    
    output_dir = Path(args.output)
    
    print("="*70)
    print("🌾 TRAINING DATA HARVESTER v2.0")
    print("="*70)
    
    # Create directory structure
    for split in ["train", "val"]:
        (output_dir / "images" / split).mkdir(parents=True, exist_ok=True)
        (output_dir / "labels" / split).mkdir(parents=True, exist_ok=True)
        for field_type in ["text", "checkbox", "radio", "dropdown"]:
            (output_dir / "classifier" / split / field_type).mkdir(parents=True, exist_ok=True)
    
    # Define augmentations
    if args.augment > 1:
        all_augs = ["original", "brightness_up", "brightness_down", "blur", "noise", "contrast"]
        augmentations = all_augs[:args.augment]
    else:
        augmentations = ["original"]
    
    print(f"\nAugmentations: {augmentations}")
    print(f"DPI: {args.dpi}")
    print(f"Val Split: {args.val_split}")
    
    # Find all matching pairs
    static_files = sorted(STATIC_DIR.glob("*.pdf"))
    pairs = []
    
    print(f"\n📁 Finding matching pairs...")
    for static_path in static_files:
        fillable_path = find_matching_fillable(static_path)
        if fillable_path:
            # Verify page count match
            try:
                with pikepdf.open(static_path) as s_pdf, pikepdf.open(fillable_path) as f_pdf:
                    if len(s_pdf.pages) == len(f_pdf.pages):
                        pairs.append((static_path, fillable_path))
                    else:
                        print(f"   ⚠️ Page mismatch: {static_path.name} ({len(s_pdf.pages)}) vs {fillable_path.name} ({len(f_pdf.pages)})")
            except Exception as e:
                print(f"   ❌ Error checking {static_path.name}: {e}")
    
    print(f"\n✅ Found {len(pairs)} valid pairs")
    
    # Split into train/val
    random.shuffle(pairs)
    split_idx = int(len(pairs) * (1 - args.val_split))
    train_pairs = pairs[:split_idx]
    val_pairs = pairs[split_idx:]
    
    print(f"   Training pairs: {len(train_pairs)}")
    print(f"   Validation pairs: {len(val_pairs)}")
    
    # Process pairs
    total_stats = {"pages": 0, "fields": 0, "classifier_crops": 0}
    
    print(f"\n🔄 Processing training pairs...")
    for static_path, fillable_path in train_pairs:
        print(f"   Processing: {static_path.name}")
        stats = process_pair(static_path, fillable_path, output_dir, "train", augmentations, args.dpi)
        for k in total_stats:
            total_stats[k] += stats.get(k, 0)
    
    print(f"\n🔄 Processing validation pairs...")
    for static_path, fillable_path in val_pairs:
        print(f"   Processing: {static_path.name}")
        stats = process_pair(static_path, fillable_path, output_dir, "val", ["original"], args.dpi)
        for k in total_stats:
            total_stats[k] += stats.get(k, 0)
    
    # Create data.yaml for YOLO
    data_yaml = {
        "path": str(output_dir.absolute()),
        "train": "images/train",
        "val": "images/val",
        "nc": 4,
        "names": ["Text Field", "Checkbox", "Radio Button", "Dropdown"]
    }
    
    import yaml
    with open(output_dir / "data.yaml", "w") as f:
        yaml.dump(data_yaml, f)
    
    # Save pairs metadata
    pairs_meta = {
        "train": [(str(s), str(f)) for s, f in train_pairs],
        "val": [(str(s), str(f)) for s, f in val_pairs],
        "augmentations": augmentations,
        "dpi": args.dpi
    }
    with open(output_dir / "pairs.json", "w") as f:
        json.dump(pairs_meta, f, indent=2)
    
    # Summary
    print("\n" + "="*70)
    print("✅ HARVEST COMPLETE")
    print("="*70)
    print(f"📁 Output directory: {output_dir.absolute()}")
    print(f"📊 Statistics:")
    print(f"   Total page images:    {total_stats['pages']}")
    print(f"   Total field labels:   {total_stats['fields']}")
    print(f"   Classifier crops:     {total_stats['classifier_crops']}")
    print(f"\n📄 Files created:")
    print(f"   {output_dir}/data.yaml - YOLO config")
    print(f"   {output_dir}/pairs.json - Metadata")
    print(f"   {output_dir}/images/ - Page images")
    print(f"   {output_dir}/labels/ - YOLO labels")
    print(f"   {output_dir}/classifier/ - Cropped field images")


if __name__ == "__main__":
    main()
