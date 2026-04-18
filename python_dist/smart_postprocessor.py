#!/usr/bin/env python3
"""
Smart post-processor that removes fields overlapping text content.
Based on the rule: TRUE fields go on underlines or in empty spaces, NOT over text.
"""

import fitz
import os
import re
from collections import defaultdict

def get_text_blocks(page):
    """Get all text blocks on a page with their bounding boxes."""
    blocks = []
    text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    
    for block in text_dict.get("blocks", []):
        if block.get("type") == 0:  # Text block
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "").strip()
                    if text:
                        bbox = fitz.Rect(span["bbox"])
                        blocks.append({
                            'text': text,
                            'rect': bbox,
                            'size': span.get("size", 10)
                        })
    return blocks

def get_drawings(page):
    """Get line drawings (underlines) on a page."""
    drawings = page.get_drawings()
    lines = []
    for d in drawings:
        if d.get("items"):
            for item in d["items"]:
                if item[0] == "l":  # Line
                    p1, p2 = item[1], item[2]
                    # Check if it's roughly horizontal (underline)
                    if abs(p1.y - p2.y) < 3:  # Nearly horizontal
                        length = abs(p2.x - p1.x)
                        if length > 20:  # Minimum underline length
                            lines.append({
                                'rect': fitz.Rect(min(p1.x, p2.x), p1.y - 2, max(p1.x, p2.x), p1.y + 2),
                                'length': length
                            })
    return lines

def rect_overlap_area(r1, r2):
    """Calculate overlap area between two rectangles."""
    x1 = max(r1.x0, r2.x0)
    y1 = max(r1.y0, r2.y0)
    x2 = min(r1.x1, r2.x1)
    y2 = min(r1.y1, r2.y1)
    
    if x2 <= x1 or y2 <= y1:
        return 0
    return (x2 - x1) * (y2 - y1)

def field_overlaps_text(field_rect, text_blocks, threshold=0.3):
    """Check if field significantly overlaps text content."""
    field_area = field_rect.width * field_rect.height
    if field_area == 0:
        return False, 0
    
    total_text_overlap = 0
    overlapping_texts = []
    
    for block in text_blocks:
        overlap = rect_overlap_area(field_rect, block['rect'])
        if overlap > 0:
            total_text_overlap += overlap
            overlapping_texts.append(block['text'])
    
    overlap_ratio = total_text_overlap / field_area
    return overlap_ratio > threshold, overlap_ratio, overlapping_texts

def field_near_underline(field_rect, underlines, tolerance=15):
    """Check if field is positioned near an underline (true field indicator)."""
    for line in underlines:
        # Check if field bottom is near the underline
        if abs(field_rect.y1 - line['rect'].y0) < tolerance:
            # Check horizontal overlap
            h_overlap = min(field_rect.x1, line['rect'].x1) - max(field_rect.x0, line['rect'].x0)
            if h_overlap > 20:  # Significant horizontal alignment
                return True
    return False

def is_likely_label_field(name, field_type, text_blocks, field_rect):
    """Check if this field is likely a label, not a real input field."""
    
    # Check if field name matches text content it covers
    for block in text_blocks:
        overlap = rect_overlap_area(field_rect, block['rect'])
        if overlap > 0:
            # If field name contains the text it overlaps, it's probably a label field
            if block['text'].lower() in name.lower() or name.lower() in block['text'].lower():
                if len(block['text']) > 5:  # Not just short text
                    return True
    
    return False

def analyze_field(widget, page, text_blocks, underlines):
    """Analyze a field and determine if it's likely a real field or junk."""
    
    name = widget.field_name or ''
    ftype = widget.field_type_string
    rect = widget.rect
    area = rect.width * rect.height
    
    reasons = []
    score = 100  # Start with perfect score, deduct for issues
    
    # Rule 1: Fields covering significant text are likely junk
    overlaps_text, overlap_ratio, overlapping_texts = field_overlaps_text(rect, text_blocks, threshold=0.2)
    if overlaps_text:
        score -= 40
        reasons.append(f"overlaps_text({overlap_ratio:.0%})")
    
    # Rule 2: Text fields not near underlines might be junk (unless in table cell)
    if ftype == 'Text' and not field_near_underline(rect, underlines):
        # Additional check: is it a large field covering text?
        if overlap_ratio > 0.1 and area > 500:
            score -= 20
            reasons.append("text_field_not_on_line")
    
    # Rule 3: Field name that looks like a label/header
    label_patterns = [
        r'^[A-Z][a-z]+ [A-Z][a-z]+',  # "First Name" style headers
        r'Information$',
        r'Organization$',
        r'^Customer ',
        r'^Applicant ',
        r'TuneUp$',
        r'Insulation$',
        r'Thermostat$',
        r'Showerhead$',
        r'Aerator$',
    ]
    for pattern in label_patterns:
        if re.search(pattern, name) and ftype == 'Text' and area > 2000:
            score -= 15
            reasons.append(f"label_pattern")
            break
    
    # Rule 4: Very large text fields are often headers/labels
    if ftype == 'Text' and area > 5000 and overlap_ratio > 0.3:
        score -= 30
        reasons.append("large_text_over_content")
    
    # Rule 5: Specific junk patterns we've identified
    if re.match(r'^Check Box \d+$', name):
        score = 0
        reasons.append("numbered_checkbox")
    
    if ':' in name and ftype == 'Text':
        # Colon patterns often indicate merged label fields
        colon_count = name.count(':')
        if colon_count >= 2:
            score -= 25
            reasons.append(f"multi_colon({colon_count})")
        elif colon_count == 1 and area > 1000:
            score -= 15
            reasons.append("single_colon_large")
    
    # Rule 6: _Stack fields are aggregated labels
    if '_Stack' in name:
        score -= 40
        reasons.append("stack_field")
    
    # Bonus: Small checkboxes are usually real
    if ftype == 'CheckBox' and 20 < area < 200:
        score += 10
        reasons.append("proper_checkbox_size")
    
    return {
        'name': name,
        'type': ftype,
        'rect': rect,
        'area': area,
        'score': max(0, min(100, score)),
        'reasons': reasons,
        'overlaps_text': overlaps_text,
        'overlap_ratio': overlap_ratio
    }

def process_pdf(input_path, threshold=50):
    """Analyze all fields in a PDF and identify likely junk."""
    doc = fitz.open(input_path)
    
    results = {
        'total': 0,
        'keep': [],
        'remove': [],
        'by_page': defaultdict(list)
    }
    
    for page_idx, page in enumerate(doc):
        text_blocks = get_text_blocks(page)
        underlines = get_drawings(page)
        
        for widget in page.widgets():
            results['total'] += 1
            
            analysis = analyze_field(widget, page, text_blocks, underlines)
            analysis['page'] = page_idx
            
            if analysis['score'] >= threshold:
                results['keep'].append(analysis)
            else:
                results['remove'].append(analysis)
            
            results['by_page'][page_idx].append(analysis)
    
    doc.close()
    return results

def main():
    """Test the smart post-processor on v22 outputs."""
    
    v22_dir = '/tmp/v22_output'
    gt_dir = '/Users/36981/Desktop/PDFTest/PDFs to test/Fillable PDFs'
    
    test_files = [
        ('53252_v22_output.pdf', '53252_DTE_EEA_Field_Inspection_Report_v14_Web_Release_Fillable.pdf'),
        ('55570_v22_output.pdf', '55570_DTE_SEEL_Contractor_Onboarding _Packet_v26_fillable.pdf'),
        ('57618_v22_output.pdf', '57618_NGRID_New_York_Ack_Form_Fillable_v07_Release_Web_Fillable.pdf'),
    ]
    
    for v22_name, gt_name in test_files:
        v22_path = os.path.join(v22_dir, v22_name)
        
        if not os.path.exists(v22_path):
            print(f"Skipping {v22_name}: not found")
            continue
        
        print(f"\n{'='*70}")
        print(f"Analyzing: {v22_name}")
        print(f"{'='*70}")
        
        results = process_pdf(v22_path, threshold=50)
        
        print(f"\nTotal fields: {results['total']}")
        print(f"Keep (score >= 50): {len(results['keep'])}")
        print(f"Remove (score < 50): {len(results['remove'])}")
        
        # Show fields to remove
        print(f"\n--- Fields to REMOVE (likely junk) ---")
        for field in sorted(results['remove'], key=lambda x: x['score'])[:20]:
            reasons = ', '.join(field['reasons']) if field['reasons'] else 'low_score'
            print(f"  [{field['score']:3d}] '{field['name'][:50]}' type={field['type']} area={field['area']:.0f} reasons={reasons}")
        
        if len(results['remove']) > 20:
            print(f"  ... and {len(results['remove']) - 20} more")
        
        # Show some fields we're keeping
        print(f"\n--- Sample fields to KEEP ---")
        for field in results['keep'][:10]:
            reasons = ', '.join(field['reasons']) if field['reasons'] else ''
            print(f"  [{field['score']:3d}] '{field['name'][:50]}' type={field['type']} area={field['area']:.0f}")

if __name__ == '__main__':
    main()
