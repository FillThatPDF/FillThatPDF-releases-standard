import sys
import os
# Add the current directory to sys.path so we can import UniversalPDFFillable
sys.path.append(os.getcwd())

from smart_fillable_v21 import UniversalPDFFillable
import pdfplumber

pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/57643_DTE_MF_Measures_and_Specs_Income_Qualified_Catalog_v2.pdf"

# Initialize detector
detector = UniversalPDFFillable(pdf_path)

# Only process Page 7 (index 6) for speed
with pdfplumber.open(pdf_path) as pdf:
    # Manually trigger detection steps up to box entry
    detector.pdf = pdf
    detector.page_count = len(pdf.pages)
    detector._cache_page_info()
    detector._preload_structural_data()
    # Mocking the process just for page 7
    detector._detect_box_entry_fields()

    print(f"\nDetected Box Entry Fields on Page 7:")
    for f in detector.text_fields:
        if f['page'] == 6 and f.get('source') == 'box_entry':
            print(f"  Field: {f['name']}")
            print(f"  Bounds: x0={f['x0']:.1f}, x1={f['x1']:.1f}, y0={f['y0']:.1f}, y1={f['y1']:.1f}")
            print(f"  Max Len: {f.get('max_len')}")
            print(f"  Comb: {f.get('comb')}")

    # Now simulate the cleanup
    print("\nTriggering _extend_fields_to_line_endpoints...")
    detector._extend_fields_to_line_endpoints()
    
    for f in detector.text_fields:
        if f['page'] == 6 and f.get('source') == 'box_entry':
            print(f"  After Extend - Bounds: x0={f['x0']:.1f}, x1={f['x1']:.1f}")

    print("\nTriggering _cleanup_field_boundaries...")
    detector._cleanup_field_boundaries()

    for f in detector.text_fields:
        if f['page'] == 6 and f.get('source') == 'box_entry':
             print(f"  After Cleanup - Bounds: x0={f['x0']:.1f}, x1={f['x1']:.1f}")
