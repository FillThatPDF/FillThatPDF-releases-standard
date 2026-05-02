import pikepdf
import os

def test_pikepdf_equality():
    # Create a simple PDF with a checkbox
    pdf = pikepdf.new()
    page = pdf.add_blank_page()
    
    # Create a checkbox field/widget
    checkbox = pikepdf.Dictionary({
        '/Type': pikepdf.Name('/Annot'),
        '/Subtype': pikepdf.Name('/Widget'),
        '/FT': pikepdf.Name('/Btn'),
        '/T': pikepdf.String('Check1'),
        '/Rect': pikepdf.Array([100, 100, 120, 120]),
    })
    
    checkbox_indirect = pdf.make_indirect(checkbox)
    page.Annots = pdf.make_indirect(pikepdf.Array([checkbox_indirect]))
    
    pdf.Root.AcroForm = pdf.make_indirect(pikepdf.Dictionary({
        '/Fields': pikepdf.Array([checkbox_indirect])
    }))
    
    # Test 1: Direct comparison of indirect objects
    all_fields = pdf.Root.AcroForm.Fields
    annot_from_page = page.Annots[0]
    field_from_acro = all_fields[0]
    
    print(f"Annot from page: {annot_from_page}")
    print(f"Field from Acro: {field_from_acro}")
    print(f"Direct equality (annot_from_page == field_from_acro): {annot_from_page == field_from_acro}")
    
    # Test 2: Comparison with different wrappers
    # In modify_fields.py, annot comes from page.Annots
    # all_fields comes from pdf.Root.AcroForm.Fields
    
    # Test 3: Removal logic
    idx = 0
    found = False
    while idx < len(all_fields):
        if all_fields[idx] == annot_from_page:
            print(f"Found match at index {idx} using ==")
            del all_fields[idx]
            found = True
        else:
            idx += 1
    
    print(f"Final all_fields length: {len(all_fields)}")
    
    # Test 4: Separate Field/Widget Case
    pdf2 = pikepdf.new()
    p2 = pdf2.add_blank_page()
    widget = pdf2.make_indirect(pikepdf.Dictionary({
        '/Type': pikepdf.Name('/Annot'),
        '/Subtype': pikepdf.Name('/Widget'),
        '/Rect': pikepdf.Array([100, 100, 120, 120]),
    }))
    field = pdf2.make_indirect(pikepdf.Dictionary({
        '/FT': pikepdf.Name('/Btn'),
        '/T': pikepdf.String('Check2'),
        '/Kids': pikepdf.Array([widget])
    }))
    widget['/Parent'] = field
    p2.Annots = pdf2.make_indirect(pikepdf.Array([widget]))
    pdf2.Root.AcroForm = pdf2.make_indirect(pikepdf.Dictionary({
        '/Fields': pikepdf.Array([field])
    }))
    
    annot_from_p2 = p2.Annots[0]
    field_from_a2 = pdf2.Root.AcroForm.Fields[0]
    
    print(f"\nSeparate Field/Widget Case:")
    print(f"Annot from page == Field from Acro: {annot_from_p2 == field_from_a2}")
    if annot_from_p2.get('/Parent') == field_from_a2:
        print("Annot parent matches field in AcroForm")

if __name__ == "__main__":
    test_pikepdf_equality()
