#!/usr/bin/env python3
"""
Auto-discover PDF pairs and run v22 + analysis.
"""
import os
import re

STATIC_DIR = "/Users/36981/Desktop/PDFTest/PDFs to test/Static PDFs"
FILLABLE_DIR = "/Users/36981/Desktop/PDFTest/PDFs to test/Fillable PDFs"

# List all files
static_files = os.listdir(STATIC_DIR)
fillable_files = os.listdir(FILLABLE_DIR)

print("Static PDFs:")
for f in sorted(static_files):
    if f.endswith('.pdf'):
        # Extract prefix (first number)
        m = re.match(r'^(\d+)', f)
        if m:
            prefix = m.group(1)
            print(f"  {prefix}: {f}")

print("\nFillable PDFs:")
for f in sorted(fillable_files):
    if f.endswith('.pdf'):
        m = re.match(r'^(\d+)', f)
        if m:
            prefix = m.group(1)
            print(f"  {prefix}: {f}")

# Auto-match by prefix
print("\n\n=== AUTO-MATCHED PAIRS ===")
matched = []
for sf in sorted(static_files):
    if not sf.endswith('.pdf'):
        continue
    m = re.match(r'^(\d+)', sf)
    if not m:
        continue
    prefix = m.group(1)
    
    # Find matching fillable
    for ff in fillable_files:
        if ff.startswith(prefix) and ff.endswith('.pdf') and 'fillable' in ff.lower():
            print(f"  {prefix}: {sf[:50]}...")
            print(f"         → {ff[:50]}...")
            matched.append((prefix, sf, ff))
            break

print(f"\nTotal matched pairs: {len(matched)}")
