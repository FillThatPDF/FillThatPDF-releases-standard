#!/usr/bin/env python3
"""
FillThatPDF Training Diagnostic Tool
=====================================

This script analyzes why your YOLO training isn't learning effectively.

KEY FINDINGS:
1. YOLO is designed for NATURAL IMAGES (photos), not documents
2. PDF form fields are GEOMETRIC patterns, not visual features
3. Your 675 training images aren't enough for document ML
4. Class imbalance: Text fields dominate, checkboxes/radios are rare
"""

import os
import json
from pathlib import Path
from collections import Counter, defaultdict
import numpy as np

def analyze_dataset(dataset_dir):
    """Analyze training dataset for issues."""
    labels_dir = Path(dataset_dir) / "train" / "labels"
    
    if not labels_dir.exists():
        labels_dir = Path(dataset_dir) / "labels"
    
    stats = {
        "total_images": 0,
        "total_labels": 0,
        "images_with_no_labels": 0,
        "class_distribution": Counter(),
        "boxes_per_image": [],
        "box_sizes": {"tiny": 0, "small": 0, "medium": 0, "large": 0},
        "aspect_ratios": [],
    }
    
    label_files = list(labels_dir.glob("*.txt"))
    print(f"\n📊 Analyzing {len(label_files)} label files in {labels_dir}...")
    
    for lf in label_files:
        stats["total_images"] += 1
        with open(lf) as f:
            lines = f.readlines()
        
        if not lines:
            stats["images_with_no_labels"] += 1
            stats["boxes_per_image"].append(0)
            continue
        
        stats["boxes_per_image"].append(len(lines))
        
        for line in lines:
            parts = line.strip().split()
            if len(parts) >= 5:
                cls_id, x_center, y_center, width, height = map(float, parts[:5])
                stats["total_labels"] += 1
                stats["class_distribution"][int(cls_id)] += 1
                
                # Analyze box sizes (normalized 0-1)
                area = width * height
                if area < 0.001:  # < 0.1% of image
                    stats["box_sizes"]["tiny"] += 1
                elif area < 0.01:  # < 1%
                    stats["box_sizes"]["small"] += 1
                elif area < 0.05:  # < 5%
                    stats["box_sizes"]["medium"] += 1
                else:
                    stats["box_sizes"]["large"] += 1
                
                if height > 0:
                    stats["aspect_ratios"].append(width / height)
    
    return stats

def print_diagnosis(stats):
    """Print diagnosis with actionable insights."""
    print("\n" + "="*70)
    print("🔍 TRAINING DATA DIAGNOSIS")
    print("="*70)
    
    print(f"\n📦 DATASET SIZE:")
    print(f"   Total images: {stats['total_images']}")
    print(f"   Total labels: {stats['total_labels']}")
    print(f"   Empty images: {stats['images_with_no_labels']}")
    
    avg_boxes = np.mean(stats['boxes_per_image']) if stats['boxes_per_image'] else 0
    print(f"   Avg boxes/image: {avg_boxes:.1f}")
    
    # ISSUE #1: Dataset Size
    if stats['total_images'] < 1000:
        print(f"\n   ⚠️  CRITICAL: {stats['total_images']} images is TOO FEW!")
        print(f"      YOLO typically needs 5,000-50,000 images to learn patterns.")
        print(f"      For document understanding, you need 10,000+ images.")
    
    print(f"\n📊 CLASS DISTRIBUTION:")
    class_names = {0: "Text Field", 1: "Checkbox", 2: "Radio Button", 3: "Dropdown"}
    total = sum(stats['class_distribution'].values())
    for cls_id, count in sorted(stats['class_distribution'].items()):
        pct = (count / total * 100) if total > 0 else 0
        name = class_names.get(cls_id, f"Class {cls_id}")
        print(f"   {name}: {count} ({pct:.1f}%)")
        
        # ISSUE #2: Class Imbalance
        if pct > 90:
            print(f"      ⚠️  SEVERE IMBALANCE: {name} dominates training!")
            print(f"         Model will be biased toward this class.")
    
    print(f"\n📐 BOX SIZE DISTRIBUTION:")
    for size_cat, count in stats['box_sizes'].items():
        pct = (count / stats['total_labels'] * 100) if stats['total_labels'] > 0 else 0
        print(f"   {size_cat.capitalize()}: {count} ({pct:.1f}%)")
    
    # ISSUE #3: Tiny Boxes
    tiny_pct = (stats['box_sizes']['tiny'] / stats['total_labels'] * 100) if stats['total_labels'] > 0 else 0
    if tiny_pct > 50:
        print(f"\n   ⚠️  ISSUE: {tiny_pct:.1f}% of boxes are TINY (<0.1% image area)")
        print(f"      YOLO struggles to detect objects this small.")
        print(f"      Form fields are often just 12-15 pixels tall at 200 DPI.")
    
    print(f"\n📏 ASPECT RATIOS (width/height):")
    if stats['aspect_ratios']:
        arr = np.array(stats['aspect_ratios'])
        print(f"   Mean: {np.mean(arr):.2f}")
        print(f"   Median: {np.median(arr):.2f}")
        print(f"   Min: {np.min(arr):.2f}, Max: {np.max(arr):.2f}")
        
        # ISSUE #4: Extreme Aspect Ratios
        extreme = sum(1 for r in arr if r > 20 or r < 0.05)
        if extreme > 0.1 * len(arr):
            print(f"\n   ⚠️  ISSUE: Many boxes have EXTREME aspect ratios")
            print(f"      Form fields are often very wide & thin (signature lines)")
            print(f"      YOLO anchors aren't designed for this shape.")

def analyze_why_yolo_fails():
    """Explain fundamental issues with YOLO for PDF forms."""
    print("\n" + "="*70)
    print("🚨 WHY YOLO ISN'T WORKING FOR PDF FORM DETECTION")
    print("="*70)
    
    issues = [
        ("1. YOLO is designed for NATURAL IMAGES, not documents",
         "   YOLO learns visual features like edges, textures, colors.\n"
         "   PDF forms are GEOMETRIC patterns with fixed rules.\n"
         "   A checkbox is always a square. A text field is always a rectangle.\n"
         "   These are rules, not learned patterns."),
        
        ("2. Form fields are CONTEXT-DEPENDENT",
         "   You need to understand 'this box follows a colon label'\n"
         "   YOLO only sees pixel patterns, not semantic relationships.\n"
         "   A table cell looks identical to a text field visually."),
        
        ("3. Training data doesn't match inference data",
         "   Training: Static PDF rendered to image\n"
         "   Labels: From fillable PDF's actual AcroForm fields\n"
         "   Problem: Fillable PDFs have VISUAL differences (blue boxes, borders)\n"
         "   The model learns to detect these visual artifacts, not field locations."),
        
        ("4. Resolution mismatch destroys small objects",
         "   Form fields are often 12pt tall = ~17 pixels at 200 DPI\n"
         "   YOLO downsamples to 640x640 internally\n"
         "   A 17px field becomes ~5px after downsampling = undetectable"),
        
        ("5. Checkboxes/Radios are visually identical to decorative squares",
         "   YOLO can't distinguish 'fillable checkbox' from 'decorative border'\n"
         "   Only PDF structure (AcroForm) knows the difference."),
    ]
    
    for title, explanation in issues:
        print(f"\n❌ {title}")
        print(explanation)

def recommend_solutions():
    """Provide actionable solutions."""
    print("\n" + "="*70)
    print("✅ RECOMMENDED SOLUTIONS")
    print("="*70)
    
    solutions = [
        ("SOLUTION A: Hybrid Rule-Based + ML Refinement",
         "   1. Use your existing smart_fillable_v22 as the PRIMARY detector\n"
         "      (It already has 15,000+ lines of hand-tuned rules!)\n"
         "   2. Use ML only for REFINEMENT:\n"
         "      - False positive filtering (is this really a field?)\n"
         "      - Field type classification (text vs checkbox)\n"
         "      - Confidence scoring\n"
         "   3. Train a CLASSIFIER, not a DETECTOR\n"
         "      - Input: Cropped image around detected field\n"
         "      - Output: text/checkbox/radio/dropdown/not_a_field\n"
         "   ✅ This leverages your PDF parsing strength + ML classification"),
        
        ("SOLUTION B: Document Layout Analysis Model (BETTER)",
         "   Use a model DESIGNED for documents, not general objects:\n"
         "   - LayoutLM / LayoutLMv3 (Microsoft)\n"
         "   - Donut (Naver)\n"
         "   - PaddleOCR with layout detection\n"
         "   - DocTR\n"
         "   These understand document structure, not just pixel patterns."),
        
        ("SOLUTION C: Ensemble Voting System (RECOMMENDED)",
         "   Combine MULTIPLE detection strategies:\n"
         "   1. Rule-based vector detection (checkboxes, lines)\n"
         "   2. Table structure analysis (pdfplumber tables)\n"
         "   3. Text/colon pattern matching (label: [field])\n"
         "   4. Visual grid detection (OpenCV)\n"
         "   5. ML classifier for verification\n"
         "   \n"
         "   VOTING: A field is confirmed if 2+ strategies agree.\n"
         "   ✅ Much more robust than any single approach!"),
        
        ("SOLUTION D: Synthetic Data Augmentation",
         "   If you want to keep YOLO, you need 10,000+ images.\n"
         "   Generate synthetic training data:\n"
         "   1. Take blank PDF templates\n"
         "   2. Programmatically add fields at known locations\n"
         "   3. Apply augmentations (rotation, noise, scaling)\n"
         "   4. Create 100+ variations per page\n"
         "   \n"
         "   This bootstraps your dataset from 675 → 67,500 images."),
    ]
    
    for title, explanation in solutions:
        print(f"\n🔧 {title}")
        print(explanation)

def main():
    print("="*70)
    print("🔬 FILLTHATPDF TRAINING DIAGNOSTIC")
    print("="*70)
    
    # Analyze all datasets
    datasets = [
        "/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/dataset_gt_v1",
        "/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/dataset_combined",
        "/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/dataset_yolo",
    ]
    
    for ds in datasets:
        if Path(ds).exists():
            print(f"\n\n{'='*70}")
            print(f"📁 DATASET: {Path(ds).name}")
            print('='*70)
            stats = analyze_dataset(ds)
            print_diagnosis(stats)
    
    analyze_why_yolo_fails()
    recommend_solutions()
    
    print("\n" + "="*70)
    print("📋 EXECUTIVE SUMMARY")
    print("="*70)
    print("""
Your smart_fillable_v22.py (15,000 lines of rules) is actually MORE
VALUABLE than the YOLO model. It encodes domain knowledge that would
take millions of images for YOLO to learn.

RECOMMENDED PATH FORWARD:
1. KEEP smart_fillable_v22 as your primary detection engine
2. ADD a lightweight classifier for field-type refinement
3. BUILD an ensemble voting system for robustness
4. ABANDON pure YOLO detection for PDF forms

The forms you're processing follow RULES, not learned patterns.
Embrace the rules-based approach and augment with ML, not replace.
""")

if __name__ == "__main__":
    main()
