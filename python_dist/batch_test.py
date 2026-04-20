#!/usr/bin/env python3
"""
Batch test script for smart_fillable_v21.py
Processes all PDFs in the test folders and generates output files.
"""
import os
import sys
import time
from pathlib import Path

# Add the script directory to path
script_dir = Path(__file__).parent
sys.path.insert(0, str(script_dir))

from smart_fillable_v21 import UniversalPDFFillable

# Define test folders
TEST_ROOT = Path("/Users/36981/Desktop/PDFTest/PDFs to test")
OUTPUT_DIR = Path("/Users/36981/Desktop/PDFTest/PDFs to test/output")

# Create output directory
OUTPUT_DIR.mkdir(exist_ok=True)

def find_all_pdfs(base_path):
    """Find all PDF files recursively."""
    pdfs = []
    for root, dirs, files in os.walk(base_path):
        for f in files:
            if f.lower().endswith('.pdf'):
                pdfs.append(Path(root) / f)
    return pdfs

def run_batch_test():
    """Run the batch test on all PDFs."""
    pdfs = find_all_pdfs(TEST_ROOT)
    # Filter out any in the output directory
    pdfs = [p for p in pdfs if 'output' not in str(p)]
    
    print(f"\n{'='*60}")
    print(f"  BATCH TEST: smart_fillable_v21 (auto_calibrate=True)")
    print(f"  Found {len(pdfs)} PDFs to process")
    print(f"{'='*60}\n")
    
    results = []
    
    for i, pdf_path in enumerate(pdfs, 1):
        pdf_name = pdf_path.name
        output_name = f"fillable_{pdf_name}"
        output_path = OUTPUT_DIR / output_name
        
        print(f"\n[{i}/{len(pdfs)}] Processing: {pdf_name}")
        print(f"    Output: {output_path}")
        
        start_time = time.time()
        try:
            engine = UniversalPDFFillable(str(pdf_path), str(output_path), auto_calibrate=True)
            engine.run()
            elapsed = time.time() - start_time
            results.append((pdf_name, "SUCCESS", elapsed))
            print(f"    ✅ SUCCESS ({elapsed:.2f}s)")
        except Exception as e:
            elapsed = time.time() - start_time
            results.append((pdf_name, f"FAILED: {str(e)[:50]}", elapsed))
            print(f"    ❌ FAILED: {e}")
    
    # Print summary
    print(f"\n\n{'='*60}")
    print("  BATCH TEST SUMMARY")
    print(f"{'='*60}")
    
    success_count = sum(1 for r in results if r[1] == "SUCCESS")
    print(f"\n  Total: {len(results)} | Success: {success_count} | Failed: {len(results) - success_count}\n")
    
    for name, status, elapsed in results:
        status_icon = "✅" if status == "SUCCESS" else "❌"
        print(f"  {status_icon} {name[:45]:45} | {status[:20]:20} | {elapsed:.1f}s")
    
    print(f"\n  Output files saved to: {OUTPUT_DIR}")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    run_batch_test()
