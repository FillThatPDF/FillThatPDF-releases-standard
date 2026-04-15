#!/usr/bin/env python3
"""Debug which TPs are being lost by filtering in 57618"""

import fitz
import re

IOU_THRESHOLD = 0.3

def calc_iou(r1, r2):
    x1 = max(r1.x0, r2.x0)
    y1 = max(r1.y0, r2.y0)
    x2 = min(r1.x1, r2.x1)
    y2 = min(r1.y1, r2.y1)
    
    if x2 <= x1 or y2 <= y1:
        return 0.0
    
    intersection = (x2 - x1) * (y2 - y1)
    area1 = (r1.x1 - r1.x0) * (r1.y1 - r1.y0)
    area2 = (r2.x1 - r2.x0) * (r2.y1 - r2.y0)
    union = area1 + area2 - intersection
    
    return intersection / union if union > 0 else 0.0

def get_text_blocks(page, include_underlines=False):
    """Get all text blocks on a page.
    
    Args:
        page: PyMuPDF page object
        include_underlines: If False, ignore text that is mostly underscores
    """
    blocks = []
    text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    
    for block in text_dict.get("blocks", []):
        if block.get("type") == 0:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "").strip()
                    if text:
                        # Skip underline-only text (form fill lines)
                        if not include_underlines:
                            non_underscore = text.replace('_', '').replace('-', '').strip()
                            if len(non_underscore) < len(text) * 0.3:
                                continue
                        bbox = fitz.Rect(span["bbox"])
                        blocks.append({'text': text, 'rect': bbox})
    return blocks

def rect_overlap_area(r1, r2):
    x1 = max(r1.x0, r2.x0)
    y1 = max(r1.y0, r2.y0)
    x2 = min(r1.x1, r2.x1)
    y2 = min(r1.y1, r2.y1)
    
    if x2 <= x1 or y2 <= y1:
        return 0
    return (x2 - x1) * (y2 - y1)

def field_text_overlap_ratio(field_rect, text_blocks):
    field_area = field_rect.width * field_rect.height
    if field_area == 0:
        return 0, []
    
    total_overlap = 0
    overlapping = []
    for block in text_blocks:
        overlap = rect_overlap_area(field_rect, block['rect'])
        if overlap > 0:
            total_overlap += overlap
            overlapping.append(block['text'])
    
    return min(1.0, total_overlap / field_area), overlapping

def should_filter_field(name, ftype, area, overlap_ratio):
    # Rule 1: Check Box N pattern
    if re.match(r'^Check Box \d+$', name):
        return True, 'check_box_numbered'
    
    # Rule 2: _Stack fields
    if '_Stack' in name:
        return True, 'stack_field'
    
    # Rule 3: Multiple colons + overlap
    colon_count = name.count(':')
    if colon_count >= 2 and ftype == 'Text' and overlap_ratio > 0.2:
        return True, 'multi_colon_overlap'
    
    # Rule 4: Very high text overlap (>85%)
    if ftype == 'Text' and overlap_ratio > 0.85 and area > 500:
        return True, 'high_text_overlap'
    
    # Rule 5: Large text field with high overlap
    if ftype == 'Text' and area > 7000 and overlap_ratio > 0.4:
        return True, 'large_text_overlap'
    
    # Rule 6: Known header patterns
    header_patterns = [r'service territory', r'Proposed project', r'^Efficiency Rating', r'Disadvantaged']
    for pattern in header_patterns:
        if re.search(pattern, name, re.I) and ftype == 'Text':
            return True, 'header_pattern'
    
    # Rule 7: Question text
    if '?' in name and ftype == 'Text':
        return True, 'question_text'
    
    return False, ''

# Load PDFs
v22_path = '/tmp/v22_output/57618_v22_output.pdf'
gt_path = '/Users/36981/Desktop/PDFTest/PDFs to test/Fillable PDFs/57618_NGRID_New_York_Ack_Form_Fillable_v07_Release_Web_Fillable.pdf'

v22_doc = fitz.open(v22_path)
gt_doc = fitz.open(gt_path)

# Get GT fields
gt_fields = []
for page in gt_doc:
    for w in page.widgets():
        gt_fields.append({'name': w.field_name, 'rect': w.rect, 'type': w.field_type_string})

# Get v22 fields with filtering info
v22_fields = []
filtered_fields = []

for page_idx, page in enumerate(v22_doc):
    text_blocks = get_text_blocks(page)
    
    for w in page.widgets():
        name = w.field_name or ''
        ftype = w.field_type_string
        area = w.rect.width * w.rect.height
        overlap_ratio, overlapping_text = field_text_overlap_ratio(w.rect, text_blocks)
        
        field = {
            'name': name,
            'rect': w.rect,
            'type': ftype,
            'area': area,
            'overlap_ratio': overlap_ratio,
            'overlapping_text': overlapping_text
        }
        
        should_remove, reason = should_filter_field(name, ftype, area, overlap_ratio)
        
        if should_remove:
            field['filter_reason'] = reason
            filtered_fields.append(field)
        else:
            v22_fields.append(field)

# Match filtered fields against GT to see which are TPs
print("="*70)
print("FILTERED FIELDS THAT MATCH GT (Lost TPs)")
print("="*70)

lost_tps = []
for ff in filtered_fields:
    for gt in gt_fields:
        iou = calc_iou(ff['rect'], gt['rect'])
        if iou >= IOU_THRESHOLD:
            ff['matched_gt'] = gt['name']
            ff['iou'] = iou
            lost_tps.append(ff)
            break

print(f"\nFiltered {len(filtered_fields)} fields total")
print(f"Lost TPs: {len(lost_tps)}")

for ff in lost_tps:
    r = ff['rect']
    print(f"\n  v22: '{ff['name']}' type={ff['type']} area={ff['area']:.0f}")
    print(f"       rect=({r.x0:.0f},{r.y0:.0f},{r.x1:.0f},{r.y1:.0f})")
    print(f"       overlap={ff['overlap_ratio']:.0%} filter_reason={ff['filter_reason']}")
    print(f"       matched GT: '{ff['matched_gt']}' IoU={ff['iou']:.2f}")
    if ff['overlapping_text']:
        print(f"       overlapping text: {ff['overlapping_text'][:3]}")

print("\n" + "="*70)
print("FILTERED FIELDS THAT ARE FPs (Correctly removed)")
print("="*70)

fps = [f for f in filtered_fields if f not in lost_tps]
print(f"\nCorrectly removed FPs: {len(fps)}")
for ff in fps[:10]:
    print(f"  '{ff['name'][:50]}' reason={ff['filter_reason']} overlap={ff['overlap_ratio']:.0%}")
