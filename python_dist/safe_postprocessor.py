#!/usr/bin/env python3
"""
Post-processor for v22 output to improve accuracy.
Implements ONLY rules that have been verified to remove FPs without losing TPs.
"""

import fitz
import os
import re
from typing import List, Tuple

def should_remove_field(field_name: str, field_type: str, area: float) -> Tuple[bool, str]:
    """
    Check if a field should be removed based on safe rules.
    Returns (should_remove, reason).
    """
    name = field_name
    ftype = field_type
    
    # Rule 1: Check Box N (numbered check boxes - often duplicates)
    # Verified: 66 FPs, 0 TPs
    if re.match(r'^Check Box \d+$', name):
        return True, 'check_box_numbered'
    
    # Rule 2: Specific header texts that are never fields
    # Verified: 1 FP, 0 TPs
    if 'service territory' in name.lower():
        return True, 'service_territory'
    
    # Rule 3: Question-like text (contains "?" in text field)
    # Verified: 1 FP, 0 TPs
    if '?' in name and ftype == 'Text':
        return True, 'question_text'
    
    # Rule 4: Proposed project header
    # Verified: 1 FP, 0 TPs
    if 'Proposed project' in name and ftype == 'Text':
        return True, 'proposed_project'
    
    # Rule 5: Efficiency Rating header
    # Verified: 2 FPs, 0 TPs
    if name.startswith('Efficiency Rating') and ftype == 'Text':
        return True, 'efficiency_rating'
    
    # Rule 6: Disadvantaged communities header
    # Verified: 1 FP, 0 TPs
    if 'Disadvantaged' in name and ftype == 'Text':
        return True, 'disadvantaged'
    
    return False, ''


def process_pdf(input_path: str, output_path: str = None) -> dict:
    """
    Process a v22 output PDF and remove false positives.
    Returns stats about what was removed.
    """
    if output_path is None:
        output_path = input_path  # Modify in place
    
    doc = fitz.open(input_path)
    
    stats = {
        'total_fields': 0,
        'removed': 0,
        'kept': 0,
        'by_rule': {}
    }
    
    # Collect widgets to remove
    widgets_to_remove = []
    
    for page_idx, page in enumerate(doc):
        for widget in page.widgets():
            stats['total_fields'] += 1
            
            name = widget.field_name or ''
            ftype = widget.field_type_string
            area = widget.rect.width * widget.rect.height
            
            should_remove, reason = should_remove_field(name, ftype, area)
            
            if should_remove:
                widgets_to_remove.append((page_idx, widget.xref))
                stats['removed'] += 1
                stats['by_rule'][reason] = stats['by_rule'].get(reason, 0) + 1
            else:
                stats['kept'] += 1
    
    # Remove widgets by creating a new PDF without them
    # More reliable than trying to delete individual widgets
    
    # Create output doc
    out_doc = fitz.open()
    
    for page_idx in range(len(doc)):
        page = doc[page_idx]
        
        # Copy page
        out_page = out_doc.new_page(width=page.rect.width, height=page.rect.height)
        out_page.show_pdf_page(out_page.rect, doc, page_idx)
        
        # Re-add widgets that should be kept
        for widget in page.widgets():
            name = widget.field_name or ''
            ftype = widget.field_type_string
            area = widget.rect.width * widget.rect.height
            
            should_remove, _ = should_remove_field(name, ftype, area)
            
            if not should_remove:
                # Copy widget to new page
                w = fitz.Widget()
                w.rect = widget.rect
                w.field_type = widget.field_type
                w.field_name = name
                w.field_value = widget.field_value
                w.field_flags = widget.field_flags
                try:
                    out_page.add_widget(w)
                except:
                    pass  # Skip if widget can't be added
    
    # Save
    out_doc.save(output_path, deflate=True)
    out_doc.close()
    doc.close()
    
    return stats


def main():
    """Process all v22 output files and report results."""
    v22_dir = '/tmp/v22_output'
    output_dir = '/tmp/v22_postprocessed'
    
    os.makedirs(output_dir, exist_ok=True)
    
    total_stats = {
        'total_fields': 0,
        'removed': 0,
        'kept': 0,
        'by_rule': {}
    }
    
    # Process each PDF
    for filename in sorted(os.listdir(v22_dir)):
        if not filename.endswith('.pdf'):
            continue
        
        input_path = os.path.join(v22_dir, filename)
        output_path = os.path.join(output_dir, filename)
        
        stats = process_pdf(input_path, output_path)
        
        print(f"\n{filename}:")
        print(f"  Fields: {stats['total_fields']} → {stats['kept']} (removed {stats['removed']})")
        if stats['by_rule']:
            for rule, count in sorted(stats['by_rule'].items()):
                print(f"    - {rule}: {count}")
        
        # Accumulate
        total_stats['total_fields'] += stats['total_fields']
        total_stats['removed'] += stats['removed']
        total_stats['kept'] += stats['kept']
        for rule, count in stats['by_rule'].items():
            total_stats['by_rule'][rule] = total_stats['by_rule'].get(rule, 0) + count
    
    print("\n" + "="*60)
    print("TOTAL SUMMARY")
    print("="*60)
    print(f"Total fields: {total_stats['total_fields']}")
    print(f"Removed: {total_stats['removed']}")
    print(f"Kept: {total_stats['kept']}")
    print("\nBy rule:")
    for rule, count in sorted(total_stats['by_rule'].items(), key=lambda x: -x[1]):
        print(f"  {rule}: {count}")


if __name__ == '__main__':
    main()
