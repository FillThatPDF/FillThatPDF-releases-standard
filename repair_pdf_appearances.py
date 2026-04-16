#!/usr/bin/env python3
"""
Repair Script: Add Appearance Streams to Existing PDFs

This script repairs PDFs that were created before the radio button/checkbox fixes.
It adds appearance streams to all radio buttons and checkboxes that are missing them.

Usage:
    python3 repair_pdf_appearances.py <input_pdf> [output_pdf]
    
If output_pdf is not specified, it will overwrite the input_pdf.
"""

import sys
import pikepdf
from pathlib import Path

def create_appearance_stream(pdf, width, height, is_on=True, ca_char='4'):
    """Create an appearance stream for a checkbox/radio button."""
    
    if is_on:
        # ON appearance - draw checkmark
        font_size = min(width, height) * 0.8
        x_off = (width - font_size * 0.6) / 2
        y_off = (height - font_size * 0.6) / 2
        
        stream_data = f"""q
BT
/ZaDb {font_size:.2f} Tf
0 g
{x_off:.2f} {y_off:.2f} Td
({ca_char}) Tj
ET
Q"""
        
        stream = pikepdf.Stream(
            pdf,
            stream_data.encode('latin-1'),
            pikepdf.Dictionary({
                '/Type': pikepdf.Name('/XObject'),
                '/Subtype': pikepdf.Name('/Form'),
                '/BBox': pikepdf.Array([0, 0, width, height]),
                '/Resources': pikepdf.Dictionary({
                    '/Font': pikepdf.Dictionary({
                        '/ZaDb': pikepdf.Dictionary({
                            '/Type': pikepdf.Name('/Font'),
                            '/Subtype': pikepdf.Name('/Type1'),
                            '/BaseFont': pikepdf.Name('/ZapfDingbats')
                        })
                    })
                })
            })
        )
    else:
        # OFF appearance - empty
        stream = pikepdf.Stream(
            pdf,
            b'q Q',
            pikepdf.Dictionary({
                '/Type': pikepdf.Name('/XObject'),
                '/Subtype': pikepdf.Name('/Form'),
                '/BBox': pikepdf.Array([0, 0, width, height])
            })
        )
    
    return pdf.make_indirect(stream)

def repair_pdf(input_path, output_path=None):
    """Repair a PDF by adding appearance streams to all form fields."""
    
    if output_path is None:
        output_path = input_path
    
    print(f"Repairing: {input_path}")
    print(f"Output: {output_path}")
    print()
    
    pdf = pikepdf.open(input_path, allow_overwriting_input=True)
    
    if '/AcroForm' not in pdf.Root:
        print("❌ No AcroForm found in PDF!")
        pdf.close()
        return False
    
    acroform = pdf.Root.AcroForm
    fields = acroform.Fields
    
    repaired_count = 0
    border_removed_count = 0
    
    # Build annot to page map
    annot_to_page = {}
    for page in pdf.pages:
        if '/Annots' in page:
            for annot in page.Annots:
                if hasattr(annot, 'objgen'):
                    annot_to_page[annot.objgen] = page
    
    # Process all fields
    for field in fields:
        ft = field.get('/FT', '')
        
        # Skip non-button fields
        if str(ft) != '/Btn':
            continue
        
        # Check if it's a radio button parent (has kids)
        if '/Kids' in field:
            # This is a radio button group parent
            print(f"Processing radio group: {field.get('/T', 'Unnamed')}")
            
            for kid in field.Kids:
                needs_repair = False
                
                # Check if AP is missing
                if '/AP' not in kid:
                    needs_repair = True
                elif '/N' not in kid.get('/AP', {}):
                    needs_repair = True
                
                if needs_repair:
                    # Get dimensions
                    rect = kid.get('/Rect', [0, 0, 10, 10])
                    width = float(rect[2]) - float(rect[0])
                    height = float(rect[3]) - float(rect[1])
                    
                    # Get export value (on-state name)
                    value_name = 'Yes'
                    if '/V' in kid:
                        v = kid.get('/V', '/Off')
                        if str(v) != '/Off':
                            value_name = str(v).lstrip('/')
                    
                    # Get checkmark character
                    mk = kid.get('/MK', {})
                    ca_char = '4'  # Default checkmark
                    if mk and '/CA' in mk:
                        ca_char = str(mk['/CA'])
                    
                    # Create appearances
                    on_stream = create_appearance_stream(pdf, width, height, True, ca_char)
                    off_stream = create_appearance_stream(pdf, width, height, False, ca_char)
                    
                    # Set AP dictionary
                    ap_n = pikepdf.Dictionary()
                    ap_n['/Off'] = off_stream
                    ap_n[pikepdf.Name('/' + value_name)] = on_stream
                    kid['/AP'] = pikepdf.Dictionary({'/N': ap_n})
                    
                    # Ensure MK/CA is set
                    if '/MK' not in kid:
                        kid['/MK'] = pikepdf.Dictionary()
                    kid['/MK']['/CA'] = ca_char
                    
                    # Set DA to use ZapfDingbats
                    kid['/DA'] = pikepdf.String('0 g /ZaDb 0 Tf')
                    
                    # Ensure page reference
                    if hasattr(kid, 'objgen') and kid.objgen in annot_to_page:
                        kid['/P'] = annot_to_page[kid.objgen].obj
                    
                    repaired_count += 1
                
                # Remove border if present
                mk = kid.get('/MK', {})
                if mk and '/BC' in mk:
                    del mk['/BC']
                    border_removed_count += 1
        
        else:
            # This is an individual checkbox
            needs_repair = False
            
            # Check if AP is missing
            if '/AP' not in field:
                needs_repair = True
            elif '/N' not in field.get('/AP', {}):
                needs_repair = True
            
            if needs_repair:
                # Get dimensions
                rect = field.get('/Rect', [0, 0, 10, 10])
                width = float(rect[2]) - float(rect[0])
                height = float(rect[3]) - float(rect[1])
                
                # Get checkmark character
                mk = field.get('/MK', {})
                ca_char = '4'  # Default checkmark
                if mk and '/CA' in mk:
                    ca_char = str(mk['/CA'])
                
                # Create appearances
                on_stream = create_appearance_stream(pdf, width, height, True, ca_char)
                off_stream = create_appearance_stream(pdf, width, height, False, ca_char)
                
                # Set AP dictionary
                ap_n = pikepdf.Dictionary()
                ap_n['/Off'] = off_stream
                ap_n[pikepdf.Name('/Yes')] = on_stream
                field['/AP'] = pikepdf.Dictionary({'/N': ap_n})
                
                # Ensure MK/CA is set
                if '/MK' not in field:
                    field['/MK'] = pikepdf.Dictionary()
                field['/MK']['/CA'] = ca_char
                
                # Set DA to use ZapfDingbats
                field['/DA'] = pikepdf.String('0 g /ZaDb 0 Tf')
                
                # Ensure page reference
                if hasattr(field, 'objgen') and field.objgen in annot_to_page:
                    field['/P'] = annot_to_page[field.objgen].obj
                
                repaired_count += 1
            
            # Remove border if present
            mk = field.get('/MK', {})
            if mk and '/BC' in mk:
                del mk['/BC']
                border_removed_count += 1
    
    # Ensure AcroForm has NeedAppearances (as backup)
    acroform['/NeedAppearances'] = True
    
    # Save repaired PDF
    pdf.save(output_path)
    pdf.close()
    
    print()
    print(f"✅ Repaired {repaired_count} form fields")
    print(f"✅ Removed borders from {border_removed_count} fields")
    print(f"✅ Saved to: {output_path}")
    
    return True

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 repair_pdf_appearances.py <input_pdf> [output_pdf]")
        print("\nIf output_pdf is not specified, the input_pdf will be overwritten.")
        sys.exit(1)
    
    input_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else None
    
    success = repair_pdf(input_path, output_path)
    sys.exit(0 if success else 1)
