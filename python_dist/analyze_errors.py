#!/usr/bin/env python3
"""
Error Pattern Analyzer
======================

Analyzes what v22 gets WRONG - identifies patterns in false positives
and false negatives so we can fix them.

Usage:
    python analyze_errors.py --batch-report /tmp/batch_test_output/batch_test_report_*.json

Author: FillThatPDF Team
Date: February 2026
"""

import os
import sys
import json
import logging
from pathlib import Path
from typing import Dict, List, Tuple, Set
from collections import defaultdict, Counter
from datetime import datetime

import pikepdf
from pdf2image import convert_from_path
import cv2
import numpy as np

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

# Focus on the 9 well-performing PDFs
TARGET_IDS = ['55570', '57618', '11691', '57769', '56380', '53252', '32775', '14792', '9787']


def get_fields_from_pdf(pdf_path: Path) -> List[Dict]:
    """Extract fields with detailed info."""
    fields = []
    
    try:
        with pikepdf.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages):
                if "/Annots" not in page:
                    continue
                
                # Get page dimensions
                mediabox = page.MediaBox if '/MediaBox' in page else [0, 0, 612, 792]
                page_width = float(mediabox[2] - mediabox[0])
                page_height = float(mediabox[3] - mediabox[1])
                
                for annot in page.Annots:
                    try:
                        if annot.get("/Subtype") != "/Widget":
                            continue
                        
                        rect = annot.get("/Rect", None)
                        if rect is None:
                            continue
                        
                        x0, y0, x1, y1 = float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])
                        
                        # Normalize
                        if x0 > x1: x0, x1 = x1, x0
                        if y0 > y1: y0, y1 = y1, y0
                        
                        width = x1 - x0
                        height = y1 - y0
                        area = width * height
                        
                        # Field type
                        ft = str(annot.get("/FT", ""))
                        ff = int(annot.get("/Ff", 0)) if "/Ff" in annot else 0
                        
                        if ft == "/Btn":
                            field_type = "radio" if (ff & 32768) else "checkbox"
                        elif ft == "/Ch":
                            field_type = "dropdown"
                        else:
                            field_type = "text"
                        
                        name = str(annot.get("/T", ""))
                        
                        # Position relative to page
                        rel_x = x0 / page_width
                        rel_y = y0 / page_height
                        
                        fields.append({
                            'name': name,
                            'bbox': [x0, y0, x1, y1],
                            'page': page_num,
                            'type': field_type,
                            'width': width,
                            'height': height,
                            'area': area,
                            'rel_x': rel_x,
                            'rel_y': rel_y,
                            'page_width': page_width,
                            'page_height': page_height
                        })
                    
                    except Exception:
                        continue
    
    except Exception as e:
        logger.error(f"Error: {e}")
    
    return fields


def calculate_iou(box1: List[float], box2: List[float]) -> float:
    """Calculate IoU."""
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


def match_fields(detected: List[Dict], ground_truth: List[Dict], 
                iou_threshold: float = 0.5) -> Tuple[List, List, List]:
    """Match detected to ground truth, return TPs, FPs, FNs."""
    
    true_positives = []  # (detected, gt, iou)
    false_positives = []  # detected with no match
    false_negatives = []  # gt with no match
    
    matched_gt = set()
    matched_det = set()
    
    # Find all matches above threshold
    matches = []
    for di, det in enumerate(detected):
        for gi, gt in enumerate(ground_truth):
            if det['page'] != gt['page']:
                continue
            iou = calculate_iou(det['bbox'], gt['bbox'])
            if iou >= iou_threshold:
                matches.append((iou, di, gi, det, gt))
    
    # Greedy matching
    matches.sort(reverse=True)
    for iou, di, gi, det, gt in matches:
        if di not in matched_det and gi not in matched_gt:
            true_positives.append((det, gt, iou))
            matched_det.add(di)
            matched_gt.add(gi)
    
    # False positives
    for di, det in enumerate(detected):
        if di not in matched_det:
            false_positives.append(det)
    
    # False negatives
    for gi, gt in enumerate(ground_truth):
        if gi not in matched_gt:
            false_negatives.append(gt)
    
    return true_positives, false_positives, false_negatives


def analyze_field_characteristics(fields: List[Dict], label: str) -> Dict:
    """Analyze characteristics of a set of fields."""
    if not fields:
        return {}
    
    # Size analysis
    widths = [f['width'] for f in fields]
    heights = [f['height'] for f in fields]
    areas = [f['area'] for f in fields]
    
    # Position analysis
    rel_xs = [f['rel_x'] for f in fields]
    rel_ys = [f['rel_y'] for f in fields]
    
    # Type analysis
    type_counts = Counter(f['type'] for f in fields)
    
    # Page distribution
    page_counts = Counter(f['page'] for f in fields)
    
    # Size categories
    tiny = sum(1 for a in areas if a < 100)
    small = sum(1 for a in areas if 100 <= a < 500)
    medium = sum(1 for a in areas if 500 <= a < 2000)
    large = sum(1 for a in areas if a >= 2000)
    
    # Position categories
    left_margin = sum(1 for x in rel_xs if x < 0.1)
    right_margin = sum(1 for x in rel_xs if x > 0.85)
    top_area = sum(1 for y in rel_ys if y > 0.85)  # PDF y is bottom-up
    bottom_area = sum(1 for y in rel_ys if y < 0.15)
    
    return {
        'count': len(fields),
        'width': {'min': min(widths), 'max': max(widths), 'avg': sum(widths)/len(widths)},
        'height': {'min': min(heights), 'max': max(heights), 'avg': sum(heights)/len(heights)},
        'area': {'min': min(areas), 'max': max(areas), 'avg': sum(areas)/len(areas)},
        'size_distribution': {'tiny(<100)': tiny, 'small(100-500)': small, 'medium(500-2000)': medium, 'large(>2000)': large},
        'position_distribution': {'left_margin': left_margin, 'right_margin': right_margin, 'top': top_area, 'bottom': bottom_area},
        'type_distribution': dict(type_counts),
        'pages': len(page_counts)
    }


def save_error_crops(fields: List[Dict], static_pdf: Path, output_dir: Path, prefix: str, max_crops: int = 50):
    """Save image crops of error fields for visual inspection."""
    if not fields:
        return
    
    # Convert PDF to images
    images = convert_from_path(str(static_pdf), dpi=150)
    page_images = {i: np.array(img) for i, img in enumerate(images)}
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    for i, field in enumerate(fields[:max_crops]):
        try:
            page_num = field['page']
            if page_num not in page_images:
                continue
            
            page_img = page_images[page_num]
            page_h, page_w = page_img.shape[:2]
            
            bbox = field['bbox']
            pdf_w = field['page_width']
            pdf_h = field['page_height']
            
            # Convert coords
            x0, y0, x1, y1 = bbox
            x1_img = int(x0 / pdf_w * page_w)
            x2_img = int(x1 / pdf_w * page_w)
            y1_img = int((pdf_h - y1) / pdf_h * page_h)
            y2_img = int((pdf_h - y0) / pdf_h * page_h)
            
            # Add padding
            pad = 10
            x1_img = max(0, x1_img - pad)
            x2_img = min(page_w, x2_img + pad)
            y1_img = max(0, y1_img - pad)
            y2_img = min(page_h, y2_img + pad)
            
            crop = page_img[y1_img:y2_img, x1_img:x2_img]
            
            if crop.size > 0:
                # Add red border
                crop_bgr = cv2.cvtColor(crop, cv2.COLOR_RGB2BGR)
                cv2.rectangle(crop_bgr, (pad-2, pad-2), (crop_bgr.shape[1]-pad+2, crop_bgr.shape[0]-pad+2), (0, 0, 255), 2)
                
                name = field['name'][:30].replace('/', '_').replace('\\', '_') or f"field_{i}"
                filename = f"{prefix}_{i:03d}_p{page_num}_{field['type']}_{name}.png"
                cv2.imwrite(str(output_dir / filename), crop_bgr)
        
        except Exception as e:
            continue


def analyze_single_pdf(result: Dict, output_dir: Path) -> Dict:
    """Analyze errors for a single PDF."""
    
    pdf_name = result['pdf']
    pdf_id = pdf_name.split('_')[0]
    
    logger.info(f"\n{'='*70}")
    logger.info(f"Analyzing: {pdf_name[:60]}")
    logger.info(f"{'='*70}")
    
    v22_path = Path(result['output_path'])
    gt_path = Path(result['fillable_path'])
    static_path = Path(result['static_path'])
    
    if not v22_path.exists() or not gt_path.exists():
        logger.warning("   Missing files!")
        return None
    
    # Extract fields
    v22_fields = get_fields_from_pdf(v22_path)
    gt_fields = get_fields_from_pdf(gt_path)
    
    logger.info(f"   v22 detected: {len(v22_fields)} fields")
    logger.info(f"   Ground truth: {len(gt_fields)} fields")
    
    # Match
    tps, fps, fns = match_fields(v22_fields, gt_fields, iou_threshold=0.5)
    
    precision = len(tps) / len(v22_fields) * 100 if v22_fields else 0
    recall = len(tps) / len(gt_fields) * 100 if gt_fields else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
    
    logger.info(f"   True Positives: {len(tps)}")
    logger.info(f"   False Positives: {len(fps)} (v22 detected, but not in GT)")
    logger.info(f"   False Negatives: {len(fns)} (in GT, but v22 missed)")
    logger.info(f"   P={precision:.1f}% R={recall:.1f}% F1={f1:.1f}%")
    
    # Analyze FP characteristics
    if fps:
        logger.info(f"\n   📊 FALSE POSITIVE analysis ({len(fps)} fields):")
        fp_stats = analyze_field_characteristics(fps, "FP")
        logger.info(f"      Types: {fp_stats['type_distribution']}")
        logger.info(f"      Sizes: {fp_stats['size_distribution']}")
        logger.info(f"      Positions: {fp_stats['position_distribution']}")
        logger.info(f"      Avg size: {fp_stats['area']['avg']:.1f} sq pts")
    
    # Analyze FN characteristics
    if fns:
        logger.info(f"\n   📊 FALSE NEGATIVE analysis ({len(fns)} fields):")
        fn_stats = analyze_field_characteristics(fns, "FN")
        logger.info(f"      Types: {fn_stats['type_distribution']}")
        logger.info(f"      Sizes: {fn_stats['size_distribution']}")
        logger.info(f"      Positions: {fn_stats['position_distribution']}")
        logger.info(f"      Avg size: {fn_stats['area']['avg']:.1f} sq pts")
    
    # Save crops for visual inspection
    pdf_output_dir = output_dir / pdf_id
    if fps:
        logger.info(f"\n   💾 Saving FP crops to {pdf_output_dir}/false_positives/")
        save_error_crops(fps, static_path, pdf_output_dir / "false_positives", "FP", max_crops=30)
    
    if fns:
        logger.info(f"   💾 Saving FN crops to {pdf_output_dir}/false_negatives/")
        save_error_crops(fns, static_path, pdf_output_dir / "false_negatives", "FN", max_crops=30)
    
    return {
        'pdf': pdf_name,
        'v22_count': len(v22_fields),
        'gt_count': len(gt_fields),
        'tp': len(tps),
        'fp': len(fps),
        'fn': len(fns),
        'precision': precision,
        'recall': recall,
        'f1': f1,
        'fp_stats': analyze_field_characteristics(fps, "FP") if fps else {},
        'fn_stats': analyze_field_characteristics(fns, "FN") if fns else {}
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Analyze v22 error patterns')
    parser.add_argument('--batch-report', required=True, help='Path to batch_test_report JSON')
    parser.add_argument('--output-dir', default='/tmp/error_analysis', help='Output directory')
    
    args = parser.parse_args()
    
    # Load batch report
    report_path = Path(args.batch_report)
    with open(report_path) as f:
        batch_data = json.load(f)
    
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    logger.info("🔍 ERROR PATTERN ANALYZER")
    logger.info("=" * 70)
    
    # Filter to target PDFs
    results = batch_data.get('results', [])
    target_results = []
    for r in results:
        pdf_id = r.get('pdf', '').split('_')[0]
        if pdf_id in TARGET_IDS and r.get('status') == 'success' and r.get('f1', 0) >= 70:
            target_results.append(r)
    
    logger.info(f"Analyzing {len(target_results)} PDFs")
    
    # Analyze each
    all_results = []
    total_fp = 0
    total_fn = 0
    all_fps = []
    all_fns = []
    
    for result in target_results:
        analysis = analyze_single_pdf(result, output_dir)
        if analysis:
            all_results.append(analysis)
            total_fp += analysis['fp']
            total_fn += analysis['fn']
    
    # Aggregate analysis
    logger.info(f"\n\n{'='*70}")
    logger.info("AGGREGATE ERROR ANALYSIS")
    logger.info(f"{'='*70}\n")
    
    logger.info(f"Total False Positives: {total_fp}")
    logger.info(f"Total False Negatives: {total_fn}")
    
    # Aggregate FP/FN characteristics
    all_fp_types = Counter()
    all_fp_sizes = Counter()
    all_fn_types = Counter()
    all_fn_sizes = Counter()
    
    for r in all_results:
        if r['fp_stats']:
            for t, c in r['fp_stats'].get('type_distribution', {}).items():
                all_fp_types[t] += c
            for s, c in r['fp_stats'].get('size_distribution', {}).items():
                all_fp_sizes[s] += c
        if r['fn_stats']:
            for t, c in r['fn_stats'].get('type_distribution', {}).items():
                all_fn_types[t] += c
            for s, c in r['fn_stats'].get('size_distribution', {}).items():
                all_fn_sizes[s] += c
    
    logger.info(f"\n📊 FALSE POSITIVES breakdown:")
    logger.info(f"   By type: {dict(all_fp_types)}")
    logger.info(f"   By size: {dict(all_fp_sizes)}")
    
    logger.info(f"\n📊 FALSE NEGATIVES breakdown:")
    logger.info(f"   By type: {dict(all_fn_types)}")
    logger.info(f"   By size: {dict(all_fn_sizes)}")
    
    # Recommendations
    logger.info(f"\n\n{'='*70}")
    logger.info("🎯 RECOMMENDATIONS")
    logger.info(f"{'='*70}\n")
    
    if total_fp > total_fn:
        logger.info("⚠️  More FALSE POSITIVES than FALSE NEGATIVES")
        logger.info("   → v22 is detecting too many fields (over-detecting)")
        logger.info("   → Solutions:")
        logger.info("      1. Increase detection thresholds")
        logger.info("      2. Add minimum size requirements")
        logger.info("      3. Filter fields in problematic areas")
        
        # Specific recommendations based on FP characteristics
        if all_fp_sizes.get('tiny(<100)', 0) > total_fp * 0.3:
            logger.info(f"\n   🔧 SPECIFIC: {all_fp_sizes['tiny(<100)']} FPs are TINY (<100 sq pts)")
            logger.info("      → Add minimum area filter: reject fields < 100 sq pts")
        
        if all_fp_types.get('checkbox', 0) > total_fp * 0.4:
            logger.info(f"\n   🔧 SPECIFIC: {all_fp_types['checkbox']} FPs are CHECKBOXES")
            logger.info("      → Tighten checkbox detection criteria")
    
    elif total_fn > total_fp:
        logger.info("⚠️  More FALSE NEGATIVES than FALSE POSITIVES")
        logger.info("   → v22 is missing fields (under-detecting)")
        logger.info("   → Solutions:")
        logger.info("      1. Lower detection thresholds")
        logger.info("      2. Add more detection strategies")
        logger.info("      3. Check specific field types being missed")
    
    # Save report
    report_file = output_dir / f'error_analysis_report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
    with open(report_file, 'w') as f:
        json.dump({
            'total_fp': total_fp,
            'total_fn': total_fn,
            'fp_types': dict(all_fp_types),
            'fp_sizes': dict(all_fp_sizes),
            'fn_types': dict(all_fn_types),
            'fn_sizes': dict(all_fn_sizes),
            'results': all_results
        }, f, indent=2)
    
    logger.info(f"\n💾 Full report: {report_file}")
    logger.info(f"💾 Error crops saved to: {output_dir}")


if __name__ == "__main__":
    main()
