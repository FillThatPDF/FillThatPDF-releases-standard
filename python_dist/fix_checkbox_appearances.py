#!/usr/bin/env python3
"""
Fix checkbox and radio button appearances in a PDF after filling.

This script ensures:
1. Checkboxes have proper ZapfDingbats appearance streams
2. Radio groups have correct /AS values (only ONE kid selected, others /Off)

Usage: fix_checkbox_appearances.py <pdf_path>
"""

import sys
import os
import pikepdf


def create_zapf_checkmark_stream(width: float, height: float) -> bytes:
    """
    Create a PDF graphics stream that draws a checkmark using ZapfDingbats font.
    """
    font_size = min(width, height) * 0.8
    x_offset = width * 0.1
    y_offset = height * 0.2
    stream = f"q\n0 w\nBT\n0 g\n{x_offset:.1f} {y_offset:.1f} Td\n/ZaDb {font_size:.0f} Tf\n(4) Tj\nET\nQ\n"
    return stream.encode('latin-1')


def create_empty_stream() -> bytes:
    """Create an empty PDF graphics stream for 'Off' state"""
    return b'q\n0 w\nQ\n'


def create_zapf_font_resource() -> pikepdf.Dictionary:
    """Create ZapfDingbats font resource dictionary"""
    font_dict = pikepdf.Dictionary({
        '/BaseFont': pikepdf.Name('/ZapfDingbats'),
        '/Subtype': pikepdf.Name('/Type1'),
        '/Type': pikepdf.Name('/Font')
    })
    return pikepdf.Dictionary({
        '/Font': pikepdf.Dictionary({
            '/ZaDb': font_dict
        })
    })


def fix_radio_groups(pdf: pikepdf.Pdf) -> int:
    """
    Fix radio groups where pdf-lib incorrectly sets all kids to same /AS.
    
    For each radio group:
    - Read parent /V value
    - For each kid: if kid has that value's appearance, set /AS to it
    - Otherwise set /AS to /Off
    - Create proper ZapfDingbats appearance streams for each kid
    
    Returns number of radio groups fixed.
    """
    if '/AcroForm' not in pdf.Root or '/Fields' not in pdf.Root.AcroForm:
        return 0
    
    fixed_count = 0
    
    for field in pdf.Root.AcroForm.Fields:
        # Check if this is a radio group (Button with Kids)
        ft = str(field.get('/FT', ''))
        if ft != '/Btn' or '/Kids' not in field:
            continue
        
        # Check radio bit (bit 16)
        ff = int(field.get('/Ff', 0))
        is_radio = (ff & 32768) != 0
        if not is_radio:
            continue
        
        # Get parent value
        parent_v = str(field.get('/V', '/Off'))
        if parent_v == '/Off':
            # No selection - set all kids to Off
            for kid in field['/Kids']:
                kid['/AS'] = pikepdf.Name('/Off')
            fixed_count += 1
            continue
        
        # Find which kid should be selected
        kids = list(field['/Kids'])
        
        # Collect all kids with their on_state
        kid_states = []
        for kid in kids:
            kid_on_state = None
            if '/AP' in kid and '/N' in kid['/AP']:
                n = kid['/AP']['/N']
                if hasattr(n, 'keys'):
                    for state in n.keys():
                        state_str = str(state)
                        if state_str != '/Off':
                            kid_on_state = state_str
                            break
            kid_states.append((kid, kid_on_state))
        
        # Find kids that match parent_v
        matching_kids = [i for i, (kid, state) in enumerate(kid_states) if state == parent_v]
        
        # If multiple kids match (all have same /Option state), randomly select ONE
        import random
        if len(matching_kids) > 1:
            selected_idx = random.choice(matching_kids)
        elif len(matching_kids) == 1:
            selected_idx = matching_kids[0]
        else:
            selected_idx = -1  # No matching kid
        
        # Set /AS for all kids and create proper appearance streams
        for i, (kid, kid_on_state) in enumerate(kid_states):
            # Get kid dimensions
            rect = kid.get('/Rect', [0, 0, 10, 10])
            if hasattr(rect, '__iter__'):
                rect = [float(r) for r in rect]
            width = abs(rect[2] - rect[0])
            height = abs(rect[3] - rect[1])
            
            # Determine the on_state name (usually /Option or /Yes)
            on_state = kid_on_state if kid_on_state else '/Option'
            
            # Create checkmark appearance stream
            check_content = create_zapf_checkmark_stream(width, height)
            check_stream = pikepdf.Stream(pdf, check_content)
            check_stream['/BBox'] = pikepdf.Array([0, 0, width, height])
            check_stream['/Subtype'] = pikepdf.Name('/Form')
            check_stream['/Type'] = pikepdf.Name('/XObject')
            check_stream['/Matrix'] = pikepdf.Array([1, 0, 0, 1, 0, 0])
            check_stream['/Resources'] = create_zapf_font_resource()
            
            # Create empty off stream
            off_content = create_empty_stream()
            off_stream = pikepdf.Stream(pdf, off_content)
            off_stream['/BBox'] = pikepdf.Array([0, 0, width, height])
            off_stream['/Subtype'] = pikepdf.Name('/Form')
            off_stream['/Type'] = pikepdf.Name('/XObject')
            off_stream['/Matrix'] = pikepdf.Array([1, 0, 0, 1, 0, 0])
            
            # Update appearance dictionary
            if '/AP' not in kid:
                kid['/AP'] = pikepdf.Dictionary()
            
            kid['/AP']['/N'] = pikepdf.Dictionary({
                on_state: check_stream,
                '/Off': off_stream
            })
            
            # Set /AS based on selection
            if i == selected_idx and kid_on_state:
                kid['/AS'] = pikepdf.Name(kid_on_state)
            else:
                kid['/AS'] = pikepdf.Name('/Off')
        
        fixed_count += 1
    
    return fixed_count


def fix_checkbox_appearances(pdf_path: str) -> bool:
    """
    Fix checkbox appearances to use ZapfDingbats checkmarks.
    Also fixes radio group /AS values.
    Returns True if successful.
    """
    print(f"☑️  Fixing checkbox/radio appearances in: {pdf_path}")
    
    try:
        pdf = pikepdf.Pdf.open(pdf_path, allow_overwriting_input=True)
        fixed_checkboxes = 0
        
        for page in pdf.pages:
            if '/Annots' not in page:
                continue
            
            for annot in page.Annots:
                ft = str(annot.get('/FT', ''))
                if '/Btn' not in ft:
                    continue
                
                # Check if it's a checkbox (not radio button)
                ff = int(annot.get('/Ff', 0))
                is_radio = (ff & 32768) != 0  # bit 16 = radio
                if is_radio:
                    continue
                
                # Get the current appearance state
                current_as = str(annot.get('/AS', '/Off'))
                
                # Get field dimensions
                rect = annot.get('/Rect', [0, 0, 10, 10])
                if hasattr(rect, '__iter__'):
                    rect = [float(r) for r in rect]
                width = abs(rect[2] - rect[0])
                height = abs(rect[3] - rect[1])
                
                # Determine the "on" state name
                ap = annot.get('/AP', {})
                on_state = '/Yes'  # Default
                if '/N' in ap:
                    n = ap['/N']
                    if hasattr(n, 'keys'):
                        for key in n.keys():
                            if str(key) != '/Off':
                                on_state = str(key)
                                break
                
                # Create new appearance streams
                check_content = create_zapf_checkmark_stream(width, height)
                check_stream = pikepdf.Stream(pdf, check_content)
                check_stream['/BBox'] = pikepdf.Array([0, 0, width, height])
                check_stream['/Subtype'] = pikepdf.Name('/Form')
                check_stream['/Type'] = pikepdf.Name('/XObject')
                check_stream['/Matrix'] = pikepdf.Array([1, 0, 0, 1, 0, 0])
                check_stream['/Resources'] = create_zapf_font_resource()
                
                off_content = create_empty_stream()
                off_stream = pikepdf.Stream(pdf, off_content)
                off_stream['/BBox'] = pikepdf.Array([0, 0, width, height])
                off_stream['/Subtype'] = pikepdf.Name('/Form')
                off_stream['/Type'] = pikepdf.Name('/XObject')
                off_stream['/Matrix'] = pikepdf.Array([1, 0, 0, 1, 0, 0])
                
                # Update appearance dictionary
                if '/AP' not in annot:
                    annot['/AP'] = pikepdf.Dictionary()
                
                annot['/AP']['/N'] = pikepdf.Dictionary({
                    on_state: check_stream,
                    '/Off': off_stream
                })
                
                fixed_checkboxes += 1
        
        # Fix radio groups (pdf-lib bug workaround)
        fixed_radios = fix_radio_groups(pdf)
        
        # Set NeedAppearances to False so Acrobat uses our custom appearance streams
        if '/AcroForm' in pdf.Root:
            pdf.Root['/AcroForm']['/NeedAppearances'] = False
        
        pdf.save(pdf_path)
        
        print(f"   ✅ Fixed {fixed_checkboxes} checkboxes, {fixed_radios} radio groups")
        return True
        
    except Exception as e:
        print(f"   ❌ Error: {e}")
        return False


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: fix_checkbox_appearances.py <pdf_path>")
        sys.exit(1)
    
    pdf_path = sys.argv[1]
    
    if not os.path.exists(pdf_path):
        print(f"Error: File not found: {pdf_path}")
        sys.exit(1)
    
    success = fix_checkbox_appearances(pdf_path)
    sys.exit(0 if success else 1)
