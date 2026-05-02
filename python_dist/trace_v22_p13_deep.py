
import sys
import os
import pdfplumber

# Add current dir to path to import v22
sys.path.append(".")
from smart_fillable_v22 import UniversalPDFFillable

pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/Static PDFs/55570_DTE_SEEL_Contractor_Onboarding _Packet_v26.pdf"

# Initialize v22
detector = UniversalPDFFillable(pdf_path)
detector.pdf = pdfplumber.open(pdf_path)

# Run Page 13 (index 12)
page_num = 12
print(f"Deep Trace v22 detection for Page {page_num+1}...")

# Manually trigger pre-load
detector._preload_structural_data()

# We need to manually initialize fields array
detector.fields = []

# Try different detection passes
print("Checking _detect_all_checkboxes...")
detector._detect_all_checkboxes() # This might find them?
print(f"  Fields so far: {len(detector.fields)}")

print("Checking _detect_table_cells...")
detector._detect_table_cells()
print(f"  Fields so far: {len(detector.fields)}")

print("Checking _detect_signature_fields...")
detector._detect_signature_fields()
print(f"  Fields so far: {len(detector.fields)}")

print("Checking _detect_empty_boxes...")
detector._detect_empty_boxes()
print(f"  Fields so far: {len(detector.fields)}")

print("Checking _detect_form_lines...")
detector._detect_form_lines()
print(f"  Fields so far: {len(detector.fields)}")

# Filter for Page 13 and print their locations
p13_fields = [f for f in detector.fields if f.get('page') == page_num]
print(f"\nTOTAL FIELDS FOUND ON PAGE 13: {len(p13_fields)}")
for f in p13_fields[:10]: # Print first 10
    print(f"  {f.get('type')}: {f.get('rect')} '{f.get('label')}'")
