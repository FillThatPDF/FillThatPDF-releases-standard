#!/usr/bin/env python3
"""
Smart Fillable PDF - Accuracy Analysis Tool
============================================

Compares detected fields against ground truth to measure accuracy.
Identifies false positives and false negatives.

Usage:
    python analyze_accuracy.py ground_truth.pdf detected.pdf
"""

import sys
import argparse
import json
from pathlib import Path
from collections import defaultdict, Counter
from typing import List, Dict, Tuple, Set
from dataclasses import dataclass
import pikepdf


@dataclass
class Field:
    x0: float
    y0: float
    x1: float
    y1: float
    page: int
    field_type: str
    name: str = ""
    
    @property
    def center(self) -> Tuple[float, float]:
        return ((self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2)
    
    @property
    def area(self) -> float:
        return abs(self.x1 - self.x0) * abs(self.y1 - self.y0)
    
    def iou(self, other: 'Field') -> float:
        """Calculate Intersection over Union."""
        if self.page != other.page:
            return 0.0
        
        ix0 = max(self.x0, other.x0)
        iy0 = max(self.y0, other.y0)
        ix1 = min(self.x1, other.x1)
        iy1 = min(self.y1, other.y1)
        
        if ix1 <= ix0 or iy1 <= iy0:
            return 0.0
        
        intersection = (ix1 - ix0) * (iy1 - iy0)
        union = self.area + other.area - intersection
        
        return intersection / union if union > 0 else 0.0


def extract_fields(pdf_path: str) -> List[Field]:
    """Extract fields from a PDF."""
    fields = []
    
    with pikepdf.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            if "/Annots" not in page:
                continue
            
            for annot in page.Annots:
                if annot.get("/Subtype") != "/Widget":
                    continue
                
                rect = [float(x) for x in annot.get("/Rect", [0, 0, 0, 0])]
                x0, y0, x1, y1 = rect
                
                # Normalize order
                if x0 > x1:
                    x0, x1 = x1, x0
                if y0 > y1:
                    y0, y1 = y1, y0
                
                ft = str(annot.get("/FT", "")) if "/FT" in annot else ""
                ff = int(annot.get("/Ff", 0)) if "/Ff" in annot else 0
                
                if ft == "/Btn":
                    field_type = "radio" if (ff & 32768) else "checkbox"
                elif ft == "/Ch":
                    field_type = "dropdown"
                else:
                    field_type = "text"
                
                name = str(annot.get("/T", "")) if "/T" in annot else ""
                
                fields.append(Field(
                    x0=x0, y0=y0, x1=x1, y1=y1,
                    page=page_num,
                    field_type=field_type,
                    name=name
                ))
    
    return fields


def match_fields(ground_truth: List[Field], detected: List[Field], 
                 iou_threshold: float = 0.3) -> Tuple[List, List, List]:
    """
    Match detected fields to ground truth.
    
    Returns: (true_positives, false_positives, false_negatives)
    """
    true_positives = []  # (gt, det) pairs
    false_positives = []  # detected but no match
    false_negatives = []  # gt but no detection
    
    gt_matched = set()
    det_matched = set()
    
    # Group by page for efficiency
    gt_by_page = defaultdict(list)
    det_by_page = defaultdict(list)
    
    for i, f in enumerate(ground_truth):
        gt_by_page[f.page].append((i, f))
    for i, f in enumerate(detected):
        det_by_page[f.page].append((i, f))
    
    # Match on each page
    for page_num in set(gt_by_page.keys()) | set(det_by_page.keys()):
        gt_fields = gt_by_page[page_num]
        det_fields = det_by_page[page_num]
        
        # Calculate all IoU pairs
        matches = []
        for gi, gf in gt_fields:
            for di, df in det_fields:
                iou = gf.iou(df)
                if iou >= iou_threshold:
                    matches.append((iou, gi, di, gf, df))
        
        # Greedy matching (highest IoU first)
        matches.sort(reverse=True)
        
        for iou, gi, di, gf, df in matches:
            if gi not in gt_matched and di not in det_matched:
                true_positives.append((gf, df, iou))
                gt_matched.add(gi)
                det_matched.add(di)
    
    # False positives: detected but not matched
    for i, f in enumerate(detected):
        if i not in det_matched:
            false_positives.append(f)
    
    # False negatives: ground truth but not matched
    for i, f in enumerate(ground_truth):
        if i not in gt_matched:
            false_negatives.append(f)
    
    return true_positives, false_positives, false_negatives


def analyze_accuracy(gt_path: str, det_path: str, iou_threshold: float = 0.3):
    """Full accuracy analysis."""
    print(f"\n{'='*70}")
    print("📊 ACCURACY ANALYSIS")
    print(f"{'='*70}")
    print(f"Ground Truth: {Path(gt_path).name}")
    print(f"Detected:     {Path(det_path).name}")
    print(f"IoU Threshold: {iou_threshold}")
    
    gt_fields = extract_fields(gt_path)
    det_fields = extract_fields(det_path)
    
    print(f"\n📦 FIELD COUNTS:")
    print(f"   Ground Truth: {len(gt_fields)}")
    print(f"   Detected:     {len(det_fields)}")
    
    tp, fp, fn = match_fields(gt_fields, det_fields, iou_threshold)
    
    precision = len(tp) / len(det_fields) if det_fields else 0
    recall = len(tp) / len(gt_fields) if gt_fields else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
    
    print(f"\n📈 METRICS:")
    print(f"   True Positives:  {len(tp)}")
    print(f"   False Positives: {len(fp)} (over-detection)")
    print(f"   False Negatives: {len(fn)} (missed fields)")
    print(f"\n   Precision: {precision:.1%}")
    print(f"   Recall:    {recall:.1%}")
    print(f"   F1 Score:  {f1:.1%}")
    
    # Type breakdown
    print(f"\n🏷️  TYPE ANALYSIS:")
    gt_types = Counter(f.field_type for f in gt_fields)
    det_types = Counter(f.field_type for f in det_fields)
    tp_types = Counter(gt.field_type for gt, det, iou in tp)
    
    print(f"   {'Type':<12} {'GT':>8} {'Det':>8} {'Matched':>8} {'Recall':>8}")
    print(f"   {'-'*44}")
    for ft in sorted(set(gt_types.keys()) | set(det_types.keys())):
        gt_c = gt_types.get(ft, 0)
        det_c = det_types.get(ft, 0)
        tp_c = tp_types.get(ft, 0)
        rec = tp_c / gt_c if gt_c > 0 else 0
        print(f"   {ft:<12} {gt_c:>8} {det_c:>8} {tp_c:>8} {rec:>7.1%}")
    
    # Page breakdown for false negatives
    if fn:
        print(f"\n❌ FALSE NEGATIVES BY PAGE (missed fields):")
        fn_by_page = Counter(f.page for f in fn)
        for page, count in sorted(fn_by_page.items()):
            print(f"   Page {page + 1}: {count} missed")
    
    # Page breakdown for false positives
    if fp:
        print(f"\n⚠️  FALSE POSITIVES BY PAGE (over-detection):")
        fp_by_page = Counter(f.page for f in fp)
        for page, count in sorted(fp_by_page.items())[:10]:  # Top 10
            print(f"   Page {page + 1}: {count} extra")
        if len(fp_by_page) > 10:
            print(f"   ... and {len(fp_by_page) - 10} more pages")
    
    # Save detailed report
    report = {
        "summary": {
            "ground_truth_count": len(gt_fields),
            "detected_count": len(det_fields),
            "true_positives": len(tp),
            "false_positives": len(fp),
            "false_negatives": len(fn),
            "precision": precision,
            "recall": recall,
            "f1_score": f1,
        },
        "false_negatives": [
            {"page": f.page + 1, "type": f.field_type, "rect": [f.x0, f.y0, f.x1, f.y1], "name": f.name}
            for f in fn
        ],
        "false_positives_sample": [
            {"page": f.page + 1, "type": f.field_type, "rect": [f.x0, f.y0, f.x1, f.y1]}
            for f in fp[:50]  # Sample
        ]
    }
    
    report_path = Path(det_path).parent / f"{Path(det_path).stem}_accuracy_report.json"
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2)
    print(f"\n📝 Detailed report saved: {report_path}")
    
    return precision, recall, f1


def main():
    parser = argparse.ArgumentParser(description="Analyze fillable field detection accuracy")
    parser.add_argument("ground_truth", help="Path to ground truth fillable PDF")
    parser.add_argument("detected", help="Path to detected/generated PDF")
    parser.add_argument("--iou", type=float, default=0.3, help="IoU threshold (default: 0.3)")
    
    args = parser.parse_args()
    analyze_accuracy(args.ground_truth, args.detected, args.iou)


if __name__ == "__main__":
    main()
