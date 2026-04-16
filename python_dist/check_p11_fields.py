#!/usr/bin/env python3
import sys
sys.path.insert(0, '/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/FillThatPDF_Optimized_v4/python_dist')
import PyPDF2

# Open the generated PDF and check P11 fields
pdf = PyPDF2.PdfReader('/Users/36981/Desktop/PDFTest/test_57643_v35.28_FINAL.pdf')
fields = pdf.get_fields()

print("=== P11 Fields (page 10, 0-indexed) ===")
p11_fields = []
for name, field in fields.items():
    if '/P' in str(field) or '/Parent' in str(field):
        # Try to get page number
        try:
            page = field.get('/P')
            if page and hasattr(page, 'indirect_reference'):
                page_num = pdf.pages.index(page)
                if page_num == 10:  # P11
                    field_type = field.get('/FT', 'Unknown')
                    p11_fields.append((name, field_type))
        except:
            pass

# Sort by name and show field types
for name, ftype in sorted(p11_fields)[:30]:
    print(f"{name}: {ftype}")
    
print(f"\nTotal P11 fields shown: {min(len(p11_fields), 30)} of {len(p11_fields)}")

# Check for checkboxes specifically
checkboxes = [f for f in p11_fields if '/Btn' in str(f[1])]
print(f"Checkboxes on P11: {len(checkboxes)}")
if checkboxes:
    print("Checkbox fields:")
    for name, ftype in checkboxes[:10]:
        print(f"  {name}")
