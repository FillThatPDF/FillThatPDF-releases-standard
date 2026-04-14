#!/usr/bin/env python3
"""
Auto-rename all fields in a fillable PDF based on nearby text.
Refactored to use pikepdf instead of PyMuPDF.

Usage:
    python auto_rename_all.py <pdf_path>
"""

import sys
import os
import json
import pikepdf
from pathlib import Path

# Import the auto_name_field logic
from auto_name_field import find_best_names

def get_all_fields(pdf):
    """Extract all form fields from the pikepdf document."""
    fields = []
    for page_num, page in enumerate(pdf.pages):
        if '/Annots' not in page:
            continue
            
        for annot in page.Annots:
            if annot.get('/Subtype') != pikepdf.Name('/Widget'):
                continue
                
            # Get Rect [x0, y0, x1, y1] (bottom-up in PDF)
            # find_best_names expects top-down visual coords (standard for these tools)
            rect = [float(c) for c in annot.get('/Rect', [0,0,0,0])]
            
            # Get page height for coordinate transformation if needed
            # For US Letter, 792 is standard. 
            # PyMuPDF widget.rect uses visual coords. 
            # pdfplumber also uses visual coords.
            # Convert bottom-up [x0, y0, x1, y1] to top-down [x0, top, x1, bottom]
            # Mediabox is usually [0, 0, width, height]
            mediabox = page.get('/MediaBox', [0, 0, 612, 792])
            p_height = float(mediabox[3])
            
            v_rect = [rect[0], p_height - rect[3], rect[2], p_height - rect[1]]
            
            fields.append({
                'id': str(annot.get('/T', '')),
                'name': str(annot.get('/T', '')),
                'page': page_num + 1,
                'rect': v_rect,
                'annot': annot,
                'p_height': p_height
            })
    return fields

def auto_rename_all_fields(pdf_path):
    """Auto-rename all fields in a PDF based on nearby text."""
    
    if not os.path.exists(pdf_path):
        print(json.dumps({'error': f'File not found: {pdf_path}'}))
        return False
    
    try:
        pdf = pikepdf.Pdf.open(pdf_path, allow_overwriting_input=True)
        fields = get_all_fields(pdf)
        
        if not fields:
            print(json.dumps({'message': 'No fields found', 'renamed': 0}))
            pdf.close()
            return True
        
        # Prepare field data for find_best_names
        fields_data = [
            {'id': f['id'], 'page': f['page'], 'rect': f['rect']}
            for f in fields
        ]
        
        # Suggested names using the existing naming logic (now pdfplumber based)
        suggested_names = find_best_names(pdf_path, fields_data)
        
        renamed_count = 0
        used_names = set()
        
        # First pass: collect all currently used names to avoid duplicates
        for f in fields:
            used_names.add(f['name'])
            
        # Second pass: rename
        for f in fields:
            annot = f['annot']
            # Only rename text fields. /FT /Tx
            if annot.get('/FT') != pikepdf.Name('/Tx'):
                continue
                
            old_name = f['name']
            new_name = suggested_names.get(old_name, '')
            
            if new_name and new_name != old_name:
                # Ensure unique name
                base_name = new_name
                counter = 1
                while new_name in used_names:
                    new_name = f"{base_name}_{counter}"
                    counter += 1
                
                # Remove old name if we're changing it
                if old_name in used_names:
                    used_names.remove(old_name)
                used_names.add(new_name)
                
                # Rename the widget and set tooltip
                annot['/T'] = pikepdf.String(new_name)
                
                # Set tooltip (/TU) to a readable version of the name
                tooltip = new_name.replace('_', ' ')
                annot['/TU'] = pikepdf.String(tooltip)
                
                renamed_count += 1
                sys.stderr.write(f"Renamed: {old_name} -> {new_name}\n")
        
        # Save the document
        pdf.save(pdf_path)
        pdf.close()
        
        print(json.dumps({
            'success': True,
            'renamed': renamed_count,
            'total': len(fields)
        }))
        return True
        
    except Exception as e:
        sys.stderr.write(f"Error: {str(e)}\n")
        print(json.dumps({'error': str(e)}))
        return False

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(json.dumps({'error': 'Usage: auto_rename_all.py <pdf_path>'}))
        sys.exit(1)
    
    pdf_path = sys.argv[1]
    success = auto_rename_all_fields(pdf_path)
    sys.exit(0 if success else 1)
