#!/usr/bin/env python3
"""
Batch PDF Accuracy Tester
==========================

Runs smart_fillable_v22 on all static PDFs and compares to ground truth fillables.
Identifies which PDFs need the most improvement.

Usage:
    python batch_test_accuracy.py

Author: FillThatPDF Team
Date: February 2026
"""

import os
import sys
import json
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple
import logging

import pikepdf

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

# Paths
BASE_DIR = Path(__file__).parent.parent.parent.parent
STATIC_DIR = BASE_DIR / "PDFs to test" / "Static PDFs"
FILLABLE_DIR = BASE_DIR / "PDFs to test" / "Fillable PDFs"
V22_SCRIPT = Path(__file__).parent / "smart_fillable_v22.py"
ANALYZE_SCRIPT = Path(__file__).parent / "analyze_accuracy.py"
OUTPUT_DIR = Path("/tmp/batch_test_output")


def find_matching_pairs() -> List[Tuple[Path, Path]]:
    """Find matching static/fillable PDF pairs."""
    if not STATIC_DIR.exists() or not FILLABLE_DIR.exists():
        logger.error(f"❌ Directories not found!")
        return []
    
    static_pdfs = list(STATIC_DIR.glob("*.pdf"))
    fillable_pdfs = list(FILLABLE_DIR.glob("*.pdf"))
    
    # Build a lookup of fillable PDFs by their base ID (the number at start)
    fillable_lookup = {}
    for fillable in fillable_pdfs:
        # Extract ID from filename (e.g., "11691_..." -> "11691")
        parts = fillable.stem.split('_')
        if parts and parts[0].isdigit():
            pdf_id = parts[0]
            fillable_lookup[pdf_id] = fillable
    
    pairs = []
    for static in static_pdfs:
        # Extract ID from static PDF
        parts = static.stem.split('_')
        if not parts or not parts[0].isdigit():
            continue
        
        pdf_id = parts[0]
        
        # Look for matching fillable by ID
        if pdf_id in fillable_lookup:
            pairs.append((static, fillable_lookup[pdf_id]))
    
    return pairs


def get_field_count(pdf_path: Path) -> int:
    """Count fields in a PDF."""
    try:
        with pikepdf.open(pdf_path) as pdf:
            if '/AcroForm' not in pdf.Root:
                return 0
            acroform = pdf.Root.AcroForm
            fields = acroform.get('/Fields', [])
            return len(fields) if fields else 0
    except:
        return 0


def run_v22(static_pdf: Path, output_pdf: Path, timeout: int = 180) -> bool:
    """Run smart_fillable_v22 on a PDF."""
    try:
        cmd = [
            "python3", str(V22_SCRIPT),
            str(static_pdf),
            str(output_pdf)
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        
        return result.returncode == 0 and output_pdf.exists()
    except subprocess.TimeoutExpired:
        logger.warning(f"   ⏱️ Timeout after {timeout}s")
        return False
    except Exception as e:
        logger.warning(f"   ❌ Error: {e}")
        return False


def analyze_accuracy(ground_truth: Path, detected: Path) -> Dict:
    """Run accuracy analysis."""
    try:
        cmd = [
            "python3", str(ANALYZE_SCRIPT),
            str(ground_truth),
            str(detected)
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60
        )
        
        # Parse output
        output = result.stdout
        metrics = {}
        
        for line in output.split('\n'):
            if 'Precision:' in line:
                metrics['precision'] = float(line.split(':')[1].strip().rstrip('%'))
            elif 'Recall:' in line:
                metrics['recall'] = float(line.split(':')[1].strip().rstrip('%'))
            elif 'F1 Score:' in line:
                metrics['f1'] = float(line.split(':')[1].strip().rstrip('%'))
            elif 'Ground Truth:' in line and 'FIELD COUNTS' in output:
                # Find the counts section
                for l in output.split('\n'):
                    if 'Ground Truth:' in l and l.strip().startswith('Ground Truth:'):
                        parts = l.split()
                        if len(parts) >= 3:
                            try:
                                metrics['gt_fields'] = int(parts[2])
                            except:
                                pass
                    elif 'Detected:' in l and l.strip().startswith('Detected:'):
                        parts = l.split()
                        if len(parts) >= 2:
                            try:
                                metrics['detected_fields'] = int(parts[1])
                            except:
                                pass
        
        return metrics
    except Exception as e:
        logger.warning(f"   ❌ Analysis error: {e}")
        return {}


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    
    logger.info("=" * 70)
    logger.info("🧪 BATCH PDF ACCURACY TESTING")
    logger.info("=" * 70)
    
    # Find pairs
    logger.info("\n📁 Finding matching PDF pairs...")
    pairs = find_matching_pairs()
    
    if not pairs:
        logger.error("❌ No matching pairs found!")
        return
    
    logger.info(f"   Found {len(pairs)} pairs")
    
    # Test each pair
    results = []
    
    for i, (static, fillable) in enumerate(pairs, 1):
        logger.info(f"\n[{i}/{len(pairs)}] Testing: {static.name}")
        
        # Get ground truth field count
        gt_count = get_field_count(fillable)
        logger.info(f"   Ground truth: {gt_count} fields")
        
        # Run v22
        output_pdf = OUTPUT_DIR / f"{static.stem}_v22_output.pdf"
        logger.info(f"   Running v22...")
        
        success = run_v22(static, output_pdf)
        
        if not success:
            logger.warning(f"   ⚠️ v22 failed")
            results.append({
                'pdf': static.name,
                'status': 'failed',
                'gt_fields': gt_count
            })
            continue
        
        detected_count = get_field_count(output_pdf)
        logger.info(f"   Detected: {detected_count} fields")
        
        # Analyze accuracy
        logger.info(f"   Analyzing accuracy...")
        metrics = analyze_accuracy(fillable, output_pdf)
        
        if metrics:
            logger.info(f"   ✅ Precision: {metrics.get('precision', 0):.1f}% | "
                       f"Recall: {metrics.get('recall', 0):.1f}% | "
                       f"F1: {metrics.get('f1', 0):.1f}%")
        
        results.append({
            'pdf': static.name,
            'static_path': str(static),
            'fillable_path': str(fillable),
            'output_path': str(output_pdf),
            'status': 'success',
            'gt_fields': gt_count,
            'detected_fields': detected_count,
            **metrics
        })
    
    # Summary
    logger.info("\n" + "=" * 70)
    logger.info("📊 SUMMARY REPORT")
    logger.info("=" * 70)
    
    successful = [r for r in results if r['status'] == 'success']
    failed = [r for r in results if r['status'] == 'failed']
    
    logger.info(f"\n✅ Successful: {len(successful)}/{len(pairs)}")
    logger.info(f"❌ Failed: {len(failed)}/{len(pairs)}")
    
    if successful:
        avg_f1 = sum(r.get('f1', 0) for r in successful) / len(successful)
        avg_precision = sum(r.get('precision', 0) for r in successful) / len(successful)
        avg_recall = sum(r.get('recall', 0) for r in successful) / len(successful)
        
        logger.info(f"\n📈 AVERAGE METRICS:")
        logger.info(f"   Precision: {avg_precision:.1f}%")
        logger.info(f"   Recall: {avg_recall:.1f}%")
        logger.info(f"   F1 Score: {avg_f1:.1f}%")
        
        # Worst performers
        logger.info(f"\n⚠️  NEEDS MOST IMPROVEMENT (by F1 score):")
        sorted_results = sorted(successful, key=lambda x: x.get('f1', 0))
        
        for i, r in enumerate(sorted_results[:10], 1):
            name = r['pdf']
            if len(name) > 50:
                name = name[:47] + "..."
            f1 = r.get('f1', 0)
            precision = r.get('precision', 0)
            recall = r.get('recall', 0)
            logger.info(f"   {i:2}. F1={f1:5.1f}% P={precision:5.1f}% R={recall:5.1f}% | {name}")
        
        # Best performers
        logger.info(f"\n🏆 BEST PERFORMERS:")
        for i, r in enumerate(reversed(sorted_results[-5:]), 1):
            name = r['pdf']
            if len(name) > 50:
                name = name[:47] + "..."
            f1 = r.get('f1', 0)
            logger.info(f"   {i}. F1={f1:.1f}% | {name}")
    
    # Save report
    report_path = OUTPUT_DIR / f"batch_test_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(report_path, 'w') as f:
        json.dump({
            'test_date': datetime.now().isoformat(),
            'total_pairs': len(pairs),
            'successful': len(successful),
            'failed': len(failed),
            'results': results
        }, f, indent=2)
    
    logger.info(f"\n💾 Full report: {report_path}")
    
    return results


if __name__ == "__main__":
    main()
