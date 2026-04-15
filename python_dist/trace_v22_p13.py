
import sys
import os
import pdfplumber

# Add current dir to path to import v22
sys.path.append(".")
from smart_fillable_v22 import UniversalPDFFillable

pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/Static PDFs/55570_DTE_SEEL_Contractor_Onboarding _Packet_v26.pdf"

# Initialize v22
detector = UniversalPDFFillable(pdf_path)

# Run only the core detection logic for Page 13 (index 12)
page_num = 12
print(f"Tracing v22 detection for Page {page_num+1}...")

# Manually trigger pre-load
detector._preload_structural_data()

# Check visual grid
grid = detector._build_visual_grid(page_num)
print(f"  v22 Grid Cells Found: {len(grid)}")

# Check signature fields
sig_fields = []
# v22 doesn't return them by page easily, we'll just check if words have underscores
words = detector.page_words.get(page_num, [])
for w in words:
    if '_' in w['text']:
        print(f"  Word has underscore: '{w['text']}' at {w['x0'], w['top']}")

# Check existing form fields
detector._detect_existing_form_fields()
exist = [f for f in detector.existing_form_fields if f.get('page') == page_num]
print(f"  Existing Fields Found: {len(exist)}")
