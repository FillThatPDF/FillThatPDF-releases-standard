#!/usr/bin/env python3
"""Quick test of checkbox sizes."""

from v22_postprocessor import get_fields_with_details
from pathlib import Path

gt_pdf = Path('/Users/36981/Desktop/PDFTest/PDFs to test/Fillable PDFs/55570_DTE_SEEL_Contractor_Onboarding _Packet_v26_fillable.pdf')
v22_pdf = Path('/tmp/batch_test_output/55570_DTE_SEEL_Contractor_Onboarding _Packet_v26_v22_output.pdf')

gt_fields = get_fields_with_details(gt_pdf)
v22_fields = get_fields_with_details(v22_pdf)

gt_checkboxes = [f for f in gt_fields if f['type'] == 'checkbox']
print(f'GT checkboxes: {len(gt_checkboxes)}')
if gt_checkboxes:
    areas = [f['area'] for f in gt_checkboxes]
    print(f'  Area range: {min(areas):.1f} - {max(areas):.1f}')
    large = sum(1 for a in areas if a > 1000)
    print(f'  Large (>1000): {large}')

v22_checkboxes = [f for f in v22_fields if f['type'] == 'checkbox' and f['area'] > 1000]
print(f'v22 oversized checkboxes (>1000): {len(v22_checkboxes)}')

# Check header pattern impacts
header_removed = [f for f in v22_fields if any(c.isalpha() for c in f['name'])]
print(f'v22 fields with letters in name: {len(header_removed)}')
