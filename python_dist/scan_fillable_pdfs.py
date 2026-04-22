#!/usr/bin/env python3
"""
Fillable PDF Scanner
====================

Scans a directory recursively to find all PDFs that contain form fields.
Outputs a report of found fillable PDFs with field counts.

Usage:
    python scan_fillable_pdfs.py /path/to/folder [--output report.json]
    
Author: FillThatPDF Team
Date: February 2026
"""

import os
import sys
import json
import argparse
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple, Optional
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing

import pikepdf

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


def analyze_pdf(pdf_path: str) -> Optional[Dict]:
    """
    Analyze a single PDF for form fields.
    Returns field info if fillable, None otherwise.
    """
    try:
        with pikepdf.open(pdf_path) as pdf:
            # Check for AcroForm
            if '/AcroForm' not in pdf.Root:
                return None
            
            acroform = pdf.Root.AcroForm
            fields = acroform.get('/Fields', [])
            
            if not fields or len(fields) == 0:
                return None
            
            # Count fields by type
            field_counts = defaultdict(int)
            total_fields = 0
            
            def count_fields(field_obj):
                nonlocal total_fields
                try:
                    if isinstance(field_obj, pikepdf.Object):
                        field = field_obj if isinstance(field_obj, dict) else pdf.get_object(field_obj)
                    else:
                        return
                    
                    # Get field type
                    ft = str(field.get('/FT', ''))
                    if ft == '/Tx':
                        field_counts['text'] += 1
                    elif ft == '/Btn':
                        ff = int(field.get('/Ff', 0))
                        if ff & 32768:  # Radio
                            field_counts['radio'] += 1
                        elif ff & 65536:  # Pushbutton
                            field_counts['button'] += 1
                        else:
                            field_counts['checkbox'] += 1
                    elif ft == '/Ch':
                        ff = int(field.get('/Ff', 0))
                        if ff & 131072:  # Combo
                            field_counts['dropdown'] += 1
                        else:
                            field_counts['listbox'] += 1
                    elif ft == '/Sig':
                        field_counts['signature'] += 1
                    elif ft:
                        field_counts['other'] += 1
                    
                    total_fields += 1
                    
                    # Check for kids (nested fields)
                    kids = field.get('/Kids', [])
                    for kid in kids:
                        count_fields(kid)
                        
                except Exception:
                    pass
            
            for field in fields:
                count_fields(field)
            
            if total_fields == 0:
                return None
            
            return {
                'path': pdf_path,
                'pages': len(pdf.pages),
                'total_fields': total_fields,
                'field_types': dict(field_counts),
                'size_mb': os.path.getsize(pdf_path) / (1024 * 1024)
            }
            
    except Exception as e:
        return None


def scan_directory(root_path: str, max_workers: int = 4) -> Tuple[List[Dict], Dict]:
    """
    Scan directory recursively for fillable PDFs.
    """
    root = Path(root_path)
    
    if not root.exists():
        logger.error(f"❌ Path does not exist: {root_path}")
        return [], {}
    
    # Find all PDFs
    logger.info(f"🔍 Scanning for PDFs in: {root_path}")
    pdf_files = []
    
    for pdf_path in root.rglob("*.pdf"):
        # Skip hidden files and temp files
        if any(part.startswith('.') for part in pdf_path.parts):
            continue
        if '~' in pdf_path.name:
            continue
        pdf_files.append(str(pdf_path))
    
    for pdf_path in root.rglob("*.PDF"):
        if any(part.startswith('.') for part in pdf_path.parts):
            continue
        if '~' in pdf_path.name:
            continue
        pdf_files.append(str(pdf_path))
    
    # Deduplicate
    pdf_files = list(set(pdf_files))
    logger.info(f"   Found {len(pdf_files)} PDF files")
    
    if not pdf_files:
        return [], {}
    
    # Analyze PDFs in parallel
    logger.info(f"📋 Analyzing PDFs (using {max_workers} workers)...")
    
    fillable_pdfs = []
    stats = {
        'total_pdfs': len(pdf_files),
        'fillable_pdfs': 0,
        'total_fields': 0,
        'errors': 0,
        'by_field_count': defaultdict(int),
    }
    
    processed = 0
    
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(analyze_pdf, pdf): pdf for pdf in pdf_files}
        
        for future in as_completed(futures):
            processed += 1
            if processed % 100 == 0:
                logger.info(f"   Processed {processed}/{len(pdf_files)}...")
            
            try:
                result = future.result(timeout=30)
                if result:
                    fillable_pdfs.append(result)
                    stats['fillable_pdfs'] += 1
                    stats['total_fields'] += result['total_fields']
                    
                    # Categorize by field count
                    fc = result['total_fields']
                    if fc < 10:
                        stats['by_field_count']['1-9'] += 1
                    elif fc < 50:
                        stats['by_field_count']['10-49'] += 1
                    elif fc < 100:
                        stats['by_field_count']['50-99'] += 1
                    elif fc < 500:
                        stats['by_field_count']['100-499'] += 1
                    else:
                        stats['by_field_count']['500+'] += 1
                        
            except Exception as e:
                stats['errors'] += 1
    
    # Sort by field count
    fillable_pdfs.sort(key=lambda x: x['total_fields'], reverse=True)
    
    return fillable_pdfs, dict(stats)


def print_report(fillable_pdfs: List[Dict], stats: Dict, root_path: str):
    """Print a summary report."""
    logger.info("\n" + "=" * 70)
    logger.info("📊 FILLABLE PDF SCAN REPORT")
    logger.info("=" * 70)
    logger.info(f"Scanned: {root_path}")
    logger.info(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    
    logger.info(f"\n📁 STATISTICS:")
    logger.info(f"   Total PDFs scanned:    {stats['total_pdfs']:,}")
    logger.info(f"   Fillable PDFs found:   {stats['fillable_pdfs']:,}")
    logger.info(f"   Total form fields:     {stats['total_fields']:,}")
    logger.info(f"   Scan errors:           {stats['errors']:,}")
    
    if stats['fillable_pdfs'] > 0:
        avg_fields = stats['total_fields'] / stats['fillable_pdfs']
        logger.info(f"   Avg fields per PDF:    {avg_fields:.1f}")
    
    logger.info(f"\n📈 BY FIELD COUNT:")
    for category in ['1-9', '10-49', '50-99', '100-499', '500+']:
        count = stats.get('by_field_count', {}).get(category, 0)
        logger.info(f"   {category:>10} fields: {count:,} PDFs")
    
    # Field type totals
    type_totals = defaultdict(int)
    for pdf in fillable_pdfs:
        for ftype, count in pdf['field_types'].items():
            type_totals[ftype] += count
    
    logger.info(f"\n🏷️  FIELD TYPES (total across all PDFs):")
    for ftype, count in sorted(type_totals.items(), key=lambda x: -x[1]):
        logger.info(f"   {ftype:>12}: {count:,}")
    
    # Top 20 PDFs
    logger.info(f"\n🏆 TOP 20 FILLABLE PDFs (by field count):")
    for i, pdf in enumerate(fillable_pdfs[:20], 1):
        name = Path(pdf['path']).name
        if len(name) > 50:
            name = name[:47] + "..."
        logger.info(f"   {i:2}. {pdf['total_fields']:4} fields | {pdf['pages']:3} pages | {name}")
    
    if len(fillable_pdfs) > 20:
        logger.info(f"   ... and {len(fillable_pdfs) - 20} more")


def main():
    parser = argparse.ArgumentParser(
        description="Scan directory for fillable PDFs",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("path", help="Directory to scan")
    parser.add_argument("--output", "-o", help="Output JSON report path")
    parser.add_argument("--workers", "-w", type=int, default=4,
                        help="Number of parallel workers (default: 4)")
    parser.add_argument("--min-fields", type=int, default=1,
                        help="Minimum fields to include (default: 1)")
    
    args = parser.parse_args()
    
    # Scan
    fillable_pdfs, stats = scan_directory(args.path, args.workers)
    
    # Filter by min fields
    if args.min_fields > 1:
        fillable_pdfs = [p for p in fillable_pdfs if p['total_fields'] >= args.min_fields]
        logger.info(f"\n   Filtered to {len(fillable_pdfs)} PDFs with ≥{args.min_fields} fields")
    
    # Print report
    print_report(fillable_pdfs, stats, args.path)
    
    # Save JSON
    if args.output:
        output_path = args.output
    else:
        output_path = f"fillable_scan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    
    report = {
        'scan_path': args.path,
        'scan_date': datetime.now().isoformat(),
        'statistics': stats,
        'fillable_pdfs': fillable_pdfs
    }
    
    with open(output_path, 'w') as f:
        json.dump(report, f, indent=2)
    
    logger.info(f"\n💾 Full report saved: {output_path}")
    
    return fillable_pdfs


if __name__ == "__main__":
    main()
