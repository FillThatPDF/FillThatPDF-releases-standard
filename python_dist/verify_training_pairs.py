#!/usr/bin/env python3
"""
Training Data Pair Verification Tool
=====================================

Verifies that Static PDFs have matching Fillable PDFs with:
- Same page count
- Same page dimensions
- Reports mismatches for correction

Usage:
    python verify_training_pairs.py
"""

import os
import re
from pathlib import Path
from collections import defaultdict
import pikepdf

STATIC_DIR = Path("/Users/36981/Desktop/PDFTest/PDFs to test/Static PDFs")
FILLABLE_DIR = Path("/Users/36981/Desktop/PDFTest/PDFs to test/Fillable PDFs")


def extract_form_id(filename: str) -> str:
    """Extract the numeric form ID from filename (e.g., '55570' from '55570_DTE_...')"""
    match = re.match(r'^(\d+)', filename)
    return match.group(1) if match else None


def get_pdf_info(path: Path) -> dict:
    """Get page count and dimensions from PDF."""
    try:
        with pikepdf.open(path) as pdf:
            pages = len(pdf.pages)
            dims = []
            for pg in pdf.pages:
                mb = pg.MediaBox
                w, h = float(mb[2]), float(mb[3])
                dims.append((round(w, 1), round(h, 1)))
            
            # Count widgets
            widgets = 0
            for pg in pdf.pages:
                if "/Annots" in pg:
                    for ann in pg.Annots:
                        if ann.get("/Subtype") == "/Widget":
                            widgets += 1
            
            return {
                "pages": pages,
                "dimensions": dims,
                "widgets": widgets,
                "error": None
            }
    except Exception as e:
        return {"pages": 0, "dimensions": [], "widgets": 0, "error": str(e)}


def find_best_match(static_name: str, fillable_files: list) -> tuple:
    """Find the best matching fillable file for a static file."""
    form_id = extract_form_id(static_name)
    if not form_id:
        return None, "No form ID found"
    
    # Find all fillable files with same form ID
    candidates = [f for f in fillable_files if f.name.startswith(form_id)]
    
    if not candidates:
        return None, f"No fillable file with ID {form_id}"
    
    if len(candidates) == 1:
        return candidates[0], None
    
    # Multiple candidates - try to match version
    static_lower = static_name.lower()
    
    # Extract version from static (e.g., "v26", "v03")
    version_match = re.search(r'_v(\d+)', static_lower)
    static_version = version_match.group(1) if version_match else None
    
    if static_version:
        for cand in candidates:
            if f"_v{static_version}" in cand.name.lower():
                return cand, None
    
    # Return first candidate with warning
    return candidates[0], f"Multiple matches, using: {candidates[0].name}"


def main():
    print("="*70)
    print("📊 TRAINING DATA PAIR VERIFICATION")
    print("="*70)
    print(f"\nStatic Dir:   {STATIC_DIR}")
    print(f"Fillable Dir: {FILLABLE_DIR}")
    
    # Get all files
    static_files = sorted([f for f in STATIC_DIR.glob("*.pdf")])
    fillable_files = sorted([f for f in FILLABLE_DIR.glob("*.pdf")])
    
    print(f"\nFound {len(static_files)} static PDFs")
    print(f"Found {len(fillable_files)} fillable PDFs")
    
    # Results
    matched = []
    mismatched = []
    unmatched = []
    
    print("\n" + "-"*70)
    print("VERIFICATION RESULTS")
    print("-"*70)
    
    for static_path in static_files:
        fillable_path, warning = find_best_match(static_path.name, fillable_files)
        
        if fillable_path is None:
            unmatched.append((static_path.name, warning))
            continue
        
        # Get info from both
        static_info = get_pdf_info(static_path)
        fillable_info = get_pdf_info(fillable_path)
        
        if static_info["error"] or fillable_info["error"]:
            mismatched.append((
                static_path.name,
                fillable_path.name,
                f"Error: {static_info['error'] or fillable_info['error']}"
            ))
            continue
        
        # Check page count
        if static_info["pages"] != fillable_info["pages"]:
            mismatched.append((
                static_path.name,
                fillable_path.name,
                f"Page mismatch: {static_info['pages']} vs {fillable_info['pages']}"
            ))
            continue
        
        # Check dimensions (first page only for brevity)
        if static_info["dimensions"][0] != fillable_info["dimensions"][0]:
            mismatched.append((
                static_path.name,
                fillable_path.name,
                f"Dimension mismatch: {static_info['dimensions'][0]} vs {fillable_info['dimensions'][0]}"
            ))
            continue
        
        # Match!
        matched.append({
            "static": static_path.name,
            "fillable": fillable_path.name,
            "pages": static_info["pages"],
            "widgets": fillable_info["widgets"],
            "warning": warning
        })
    
    # Print results
    print(f"\n✅ MATCHED PAIRS ({len(matched)}):")
    print(f"{'Static':<50} {'Fillable':<50} {'Pages':>6} {'Fields':>7}")
    print("-"*120)
    for m in matched:
        warn = " ⚠️" if m["warning"] else ""
        print(f"{m['static'][:48]:<50} {m['fillable'][:48]:<50} {m['pages']:>6} {m['widgets']:>7}{warn}")
    
    if mismatched:
        print(f"\n❌ MISMATCHED PAIRS ({len(mismatched)}):")
        for static, fillable, reason in mismatched:
            print(f"   {static}")
            print(f"   → {fillable}")
            print(f"   Reason: {reason}\n")
    
    if unmatched:
        print(f"\n⚠️  UNMATCHED STATIC PDFs ({len(unmatched)}):")
        for name, reason in unmatched:
            print(f"   {name}")
            print(f"   Reason: {reason}\n")
    
    # Summary
    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)
    print(f"✅ Matched pairs:   {len(matched)}")
    print(f"❌ Mismatched:      {len(mismatched)}")
    print(f"⚠️  Unmatched:       {len(unmatched)}")
    
    total_pages = sum(m["pages"] for m in matched)
    total_fields = sum(m["widgets"] for m in matched)
    print(f"\n📊 Training Data Available:")
    print(f"   Total pages:  {total_pages}")
    print(f"   Total fields: {total_fields}")
    
    # Return matched pairs for use by other scripts
    return matched, mismatched, unmatched


if __name__ == "__main__":
    main()
