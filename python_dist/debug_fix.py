from smart_fillable_v21 import UniversalPDFFillable
import pdfplumber
import logging

logging.basicConfig(level=logging.INFO)

pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/57643_DTE_MF_Measures_and_Specs_Income_Qualified_Catalog_v2.pdf"
output_path = "DTE_Debug_Output.pdf"

print("--- Final Verification Run ---")
detector = UniversalPDFFillable(pdf_path, output_pdf=output_path)
detector.pdf = pdfplumber.open(pdf_path)
detector._preload_structural_data()

print("\nRunning _detect_comb_fields_from_lines...")
detector._detect_comb_fields_from_lines()

print("\n--- Field Report Page 7 ---")
comb_count = 0
for field in detector.text_fields:
    if field['page'] == 6:
        if field.get('source') == 'comb_box_grid':
            print(f"✅ Found Comb Field: '{field['name']}' at x={field['x0']:.1f}")
            comb_count += 1

if comb_count == 2:
    print("\n✅ SUCCESS: Found 2 separate comb fields!")
else:
    print(f"\n❌ FAILURE: Found {comb_count} comb fields (Expected 2)")
