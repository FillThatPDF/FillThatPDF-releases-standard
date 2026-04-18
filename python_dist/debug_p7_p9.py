import sys
from pathlib import Path
import json
import pdfplumber

# Add the directory to sys.path
sys.path.append("/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/FillThatPDF_Optimized/python_dist")

from smart_fillable_v21 import UniversalPDFFillable

pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/Blank/57643_DTE_MF_Measures_and_Specs_Income_Qualified_Catalog_v2_FILLABLE.pdf"

def debug_pages(page_nums):
    processor = UniversalPDFFillable(pdf_path)
    processor._preload_structural_data()
    
    with pdfplumber.open(pdf_path) as pdf:
        processor.pdf = pdf
        
        # Run all detection passes as seen in run()
        print("Running detection passes...")
        processor._detect_checkboxes_vector()
        processor._detect_checkboxes_character()
        processor._detect_table_cells()
        processor._detect_signature_fields()
        processor._detect_special_label_fields()
        processor._detect_inline_label_fields()
        processor._detect_general_underscore_fields()
        processor._detect_colon_separated_fields()
        processor._detect_empty_boxes()
        processor._detect_form_lines()
        processor._detect_box_entry_fields()
        
        # Post-processing (matching run() order)
        processor._cleanup_field_boundaries()
        processor._merge_adjacent_fields()
        processor._extend_fields_to_line_endpoints()
        processor._remove_overlap_duplicates()
        
        for page_num in page_nums:
            print(f"\n--- DEBUGGING PAGE {page_num + 1} ---")
            
            # Filter fields for this page
            page_fields = [f for f in processor.text_fields if f['page'] == page_num]
            
            print(f"Detected {len(page_fields)} text fields on page {page_num + 1}")
            
            # Look for the account number fields or overlaps
            for f in sorted(page_fields, key=lambda x: (x.get('y0', 0), x.get('x0', 0))):
                comb = f.get('format_opts', {}).get('comb', False)
                comb_str = "[COMB]" if comb else ""
                source = f.get('source', 'unknown')
                name = f.get('name', 'unnamed')
                label = f.get('label', '')
                x0, y0 = f.get('x0', 0), f.get('y0', 0)
                x1, y1 = f.get('x1', 0), f.get('y1', 0)
                print(f"Field: '{name}' {comb_str} x0={x0:.1f}, y0={y0:.1f}, x1={x1:.1f}, y1={y1:.1f}, label='{label}', source='{source}'")

if __name__ == "__main__":
    debug_pages([6, 8]) # 0-indexed for Page 7 and Page 9
