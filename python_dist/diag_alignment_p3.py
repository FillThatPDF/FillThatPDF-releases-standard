import sys
import os

# Add current dir to path
sys.path.append(os.getcwd())

from smart_fillable_v23 import UniversalPDFFillable

def diag_alignment():
    # Pass debug_pages as a string for _parse_page_range
    p = UniversalPDFFillable("../46119_RECO_HVAC_Incentive_Claim_Form_v02.pdf", pages="3")
    pdf_path = "../46119_RECO_HVAC_Incentive_Claim_Form_v02.pdf"
    
    print(f"Processing {pdf_path} (Page 3)...")
    p.run()
    
    print("\n--- ALL FIELDS (Page 3 index 2) ---")
    all_fields = p.text_fields + p.checkboxes
    # Filter for page 2 and sort by Y
    p3_fields = [f for f in all_fields if f.get('page') == 2]
    p3_fields.sort(key=lambda f: (f.get('y0', f.get('y', 0)), f.get('x0', f.get('x', 0))))
    
    for f in p3_fields:
        x = f.get('x0', f.get('x', 0))
        y = f.get('y0', f.get('y', 0))
        name = f.get('name', f.get('group', 'unknown'))
        label = f.get('label', '')
        source = f.get('source', 'unknown')
        print(f"Y={y:5.1f} X={x:5.1f} Name={name:25} L='{label:20}' S={source}")
    
    if not p3_fields:
        print("⚠️ No fields found on page index 2.")
        # Check other page indices in case
        pages_found = set(f.get('page') for f in all_fields)
        print(f"Fields found on page indices: {pages_found}")

if __name__ == "__main__":
    diag_alignment()
