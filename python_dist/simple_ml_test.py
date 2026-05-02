#!/usr/bin/env python3
"""
Simple ML Refinement Test
==========================

Tests if ML filtering improves precision on the 9 well-performing PDFs.
Takes batch_test_accuracy.py results and shows potential improvement.

Usage:
    python simple_ml_test.py --report /tmp/batch_test_output/batch_test_report_*.json

Author: FillThatPDF Team
Date: February 2026
"""

import json
import sys
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

# PDFs scoring 70%+ F1 that we want to focus on
GOOD_PERFORMERS = [
    '55570_DTE_SEEL_Contractor_Onboarding _Packet_v26',
    '57618_NGRID_New_York_Ack_Form_Fillable_v07',
    '11691_ConEd_Distributor_Application_Form_v10',
    '57769_CH_Res_NYS_Clean_Heat_Completion_Acknowledgment_Form_v2',
    '56380_DTE_2026_PartnerGuidelines_v18',
    '53252_DTE_EEA_Field_Inspection_Report_v11',
    '32775_DTE_2022_HVAC_Customer_SelfSubmission_Application_v01',
    '14792_ConEd_Gas_HVAC_Tune-Up_Contractor_Application_v03_FINAL_RELEASE_Web',
    '9787_ConEd_Res_HVAC_Gas_Rebate_Appl_v01_FINAL_RELEASE'
]


def analyze_report(report_path: Path):
    """Analyze batch test report and show ML refinement potential."""
    
    with open(report_path) as f:
        data = json.load(f)
    
    results = data.get('results', [])
    
    logger.info("=" * 70)
    logger.info("ML REFINEMENT OPPORTUNITY ANALYSIS")
    logger.info("=" * 70)
    logger.info("")
    logger.info("Analyzing 9 well-performing PDFs (70-80% F1)...")
    logger.info("")
    
    good_results = []
    for r in results:
        pdf_name = r.get('pdf', r.get('pdf_name', ''))
        # Match by starting chars or ID
        for good_pdf in GOOD_PERFORMERS:
            # Extract ID from good_pdf (first part before _)
            good_id = good_pdf.split('_')[0]
            if pdf_name.startswith(good_id + '_'):
                good_results.append(r)
                break
    
    logger.info(f"Found {len(good_results)} matching PDFs:")
    logger.info("")
    
    # Show current performance
    total_detected = 0
    total_ground_truth = 0
    total_tp = 0
    
    logger.info("Current Performance:")
    logger.info("-" * 70)
    for r in sorted(good_results, key=lambda x: x['f1'], reverse=True):
        pdf_name = r.get('pdf', r.get('pdf_name', 'Unknown'))[:50]
        precision = r.get('precision', 0)
        recall = r.get('recall', 0)
        f1 = r.get('f1', 0)
        detected = r.get('detected_fields', r.get('detected', 0))
        ground_truth = r.get('gt_fields', r.get('ground_truth', 0))
        
        # Calculate true positives from precision
        tp = int(precision / 100 * detected)
        
        total_detected += detected
        total_ground_truth += ground_truth
        total_tp += tp
        
        logger.info(f"{pdf_name:50s} F1={f1:5.1f}% P={precision:5.1f}% R={recall:5.1f}%  Det={detected:4d} GT={ground_truth:4d}")
    
    logger.info("")
    logger.info(f"Totals: Detected={total_detected:4d} Ground Truth={total_ground_truth:4d} True Positives={total_tp:4d}")
    
    # Calculate current metrics
    current_precision = total_tp / total_detected * 100 if total_detected > 0 else 0
    current_recall = total_tp / total_ground_truth * 100 if total_ground_truth > 0 else 0
    current_f1 = 2 * current_precision * current_recall / (current_precision + current_recall) if (current_precision + current_recall) > 0 else 0
    
    logger.info(f"Combined: P={current_precision:.1f}% R={current_recall:.1f}% F1={current_f1:.1f}%")
    logger.info("")
    
    # Simulate ML filtering improvement
    # Conservative estimate: ML can remove 30-50% of false positives while keeping 95%+ true positives
    false_positives = total_detected - total_tp
    
    logger.info("ML Refinement Potential:")
    logger.info("-" * 70)
    logger.info(f"Current false positives: {false_positives}")
    logger.info("")
    
    for fp_reduction in [0.2, 0.3, 0.4, 0.5]:
        # Assume we keep 97% of true positives
        tp_retained = int(total_tp * 0.97)
        fp_removed = int(false_positives * fp_reduction)
        new_detected = tp_retained + (false_positives - fp_removed)
        
        new_precision = tp_retained / new_detected * 100 if new_detected > 0 else 0
        new_recall = tp_retained / total_ground_truth * 100
        new_f1 = 2 * new_precision * new_recall / (new_precision + new_recall) if (new_precision + new_recall) > 0 else 0
        
        improvement = new_f1 - current_f1
        
        logger.info(f"If ML removes {int(fp_reduction*100)}% of false positives (keeping 97% TPs):")
        logger.info(f"  New metrics: P={new_precision:.1f}% R={new_recall:.1f}% F1={new_f1:.1f}%")
        logger.info(f"  Improvement: +{improvement:.1f}% F1")
        logger.info("")
    
    logger.info("=" * 70)
    logger.info("RECOMMENDATION:")
    logger.info("=" * 70)
    logger.info("")
    logger.info("The 9 well-matched PDFs have high recall but lower precision.")
    logger.info(f"Combined: {total_tp} true positives, {false_positives} false positives")
    logger.info("")
    logger.info("If ML classifier can filter out 30-40% of false positives while")
    logger.info("preserving ~97% of true positives, we could achieve:")
    logger.info("")
    logger.info(f"  Target F1: ~{current_f1 + 3:.1f}% to {current_f1 + 5:.1f}%")
    logger.info(f"  Pushing most PDFs from 70-80% → 75-85% range")
    logger.info("")
    logger.info("Next step: Implement actual ML filtering pipeline on these 9 PDFs")


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Analyze ML refinement potential')
    parser.add_argument('--report', required=True, help='Path to batch_test_report JSON')
    
    args = parser.parse_args()
    
    report_path = Path(args.report)
    if not report_path.exists():
        logger.error(f"❌ Report not found: {report_path}")
        sys.exit(1)
    
    analyze_report(report_path)


if __name__ == "__main__":
    main()
