#!/usr/bin/env python3
"""
Test script to verify radio button and checkbox fixes in FillThatPDF.

This script checks that:
1. Appearance streams (/AP) are created for radio buttons and checkboxes
2. No borders are added (as requested)
3. ZapfDingbats is used for checkmarks
4. Page references are set

Usage:
    python3 test_radio_checkbox_fix.py <path_to_fillable_pdf>
"""

import sys
import pikepdf

def test_radio_buttons(pdf_path):
    """Test radio button structure in the PDF."""
    print(f"\n{'='*60}")
    print(f"Testing: {pdf_path}")
    print(f"{'='*60}\n")
    
    pdf = pikepdf.open(pdf_path)
    
    if '/AcroForm' not in pdf.Root:
        print("❌ No AcroForm found!")
        return False
    
    acroform = pdf.Root.AcroForm
    fields = acroform.Fields
    
    print(f"Total fields: {len(fields)}")
    print(f"NeedAppearances: {acroform.get('/NeedAppearances', 'Not set')}")
    print()
    
    # Find radio groups and checkboxes
    radio_parents = []
    checkboxes = []
    
    for field in fields:
        ft = field.get('/FT', '')
        ff = int(field.get('/Ff', 0))
        name = str(field.get('/T', ''))
        
        # Check for radio buttons (Btn with radio flag 0x8000)
        if str(ft) == '/Btn' and (ff & 0x8000):
            has_kids = '/Kids' in field
            kids_count = len(field.Kids) if has_kids else 0
            radio_parents.append({
                'name': name,
                'field': field,
                'has_kids': has_kids,
                'kids_count': kids_count
            })
        # Check for checkboxes (Btn without radio flag)
        elif str(ft) == '/Btn' and not (ff & 0x8000):
            checkboxes.append({
                'name': name,
                'field': field
            })
    
    # Test radio button groups
    if radio_parents:
        print(f"\n📻 Found {len(radio_parents)} radio button groups:\n")
        
        for parent in radio_parents:
            print(f"  Group: {parent['name']}")
            print(f"    Kids: {parent['kids_count']}")
            
            if parent['has_kids']:
                all_ok = True
                for i, kid in enumerate(parent['field'].Kids):
                    print(f"\n    Kid {i+1}:")
                    
                    # Check /AP (Appearance)
                    has_ap = '/AP' in kid
                    print(f"      /AP exists: {'✅' if has_ap else '❌'}")
                    if not has_ap:
                        all_ok = False
                    
                    # Check /MK/CA (Checkmark character)
                    mk = kid.get('/MK', {})
                    ca = mk.get('/CA', None) if mk else None
                    has_ca = ca is not None
                    print(f"      /MK/CA: {'✅' if has_ca else '❌'} ({ca if has_ca else 'N/A'})")
                    
                    # Check for border
                    bc = mk.get('/BC', None) if mk else None
                    has_border = bc is not None
                    print(f"      /MK/BC (border): {'❌ (HAS BORDER!)' if has_border else '✅ (No border)'}")
                    
                    # Check /DA (Default Appearance with ZapfDingbats)
                    da = str(kid.get('/DA', ''))
                    has_zadb = '/ZaDb' in da
                    print(f"      /DA uses ZaDb: {'✅' if has_zadb else '❌'}")
                    
                    # Check /P (Page reference)
                    has_page = '/P' in kid
                    print(f"      /P (page ref): {'✅' if has_page else '❌'}")
                    
                    if not has_ap or not has_ca or not has_zadb:
                        all_ok = False
                
                if all_ok:
                    print(f"\n    ✅ All kids properly configured!")
                else:
                    print(f"\n    ⚠️  Some kids missing appearance configuration!")
    else:
        print("No radio button groups found.")
    
    # Test individual checkboxes
    if checkboxes:
        print(f"\n☑️  Found {len(checkboxes)} checkboxes:\n")
        
        for i, cb in enumerate(checkboxes[:5]):  # Show first 5
            print(f"  Checkbox {i+1}: {cb['name'][:40]}...")
            field = cb['field']
            
            # Check /AP
            has_ap = '/AP' in field
            print(f"    /AP exists: {'✅' if has_ap else '❌'}")
            
            # Check /MK/CA
            mk = field.get('/MK', {})
            ca = mk.get('/CA', None) if mk else None
            has_ca = ca is not None
            print(f"    /MK/CA: {'✅' if has_ca else '❌'} ({ca if has_ca else 'N/A'})")
            
            # Check /DA
            da = str(field.get('/DA', ''))
            has_zadb = '/ZaDb' in da
            print(f"    /DA uses ZaDb: {'✅' if has_zadb else '❌'}")
            
            # Check for border
            bc = mk.get('/BC', None) if mk else None
            has_border = bc is not None
            print(f"    /MK/BC (border): {'❌ (HAS BORDER!)' if has_border else '✅ (No border)'}")
            print()
        
        if len(checkboxes) > 5:
            print(f"  ... and {len(checkboxes) - 5} more checkboxes")
    else:
        print("No individual checkboxes found.")
    
    pdf.close()
    
    print(f"\n{'='*60}")
    print("Test Complete!")
    print(f"{'='*60}\n")
    
    return True

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 test_radio_checkbox_fix.py <path_to_fillable_pdf>")
        sys.exit(1)
    
    pdf_path = sys.argv[1]
    success = test_radio_buttons(pdf_path)
    sys.exit(0 if success else 1)
