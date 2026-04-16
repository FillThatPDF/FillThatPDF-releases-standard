#!/usr/bin/env python3
"""Analyze remaining FPs to find more safe filtering rules."""

import fitz
import os
import re
from collections import defaultdict, Counter

IOU_THRESHOLD = 0.3

PDF_PAIRS = [
    ('11691', '11691_ConEd_Distributor_Application_Form_v10_fillable_RELEASE.pdf', '11691_v22_output.pdf'),
    ('14792', '14792_ConEd_Gas_HVAC_Tune-Up_Contractor_Application_v03_FINAL_RELEASE_Web_Fillable.pdf', '14792_v22_output.pdf'),
    ('32775', '32775_DTE_2022_HVAC_Customer_SelfSubmission_Application_v01_Web_Release_Fillable.pdf', '32775_v22_output.pdf'),
    ('53252', '53252_DTE_EEA_Field_Inspection_Report_v14_Web_Release_Fillable.pdf', '53252_v22_output.pdf'),
    ('55570', '55570_DTE_SEEL_Contractor_Onboarding _Packet_v26_fillable.pdf', '55570_v22_output.pdf'),
    ('57618', '57618_NGRID_New_York_Ack_Form_Fillable_v07_Release_Web_Fillable.pdf', '57618_v22_output.pdf'),
    ('57769', '57769_CH_Res_NYS_Clean_Heat_Completion_Acknowledgement_Form_v3_Web_Release_fillable.pdf', '57769_v22_output.pdf'),
    ('9787', '9787_ConEd_Res_HVAC_Gas_Rebate_Appl_v01_FINAL_RELEASE_Fillable_Locked.pdf', '9787_v22_output.pdf'),
]

gt_dir = '/Users/36981/Desktop/PDFTest/PDFs to test/Fillable PDFs'
v22_dir = '/tmp/v22_output'

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

def get_text_blocks(page):
    blocks = []
    text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    
    for block in text_dict.get("blocks", []):
        if block.get("type") == 0:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "").strip()
                    if text:
                        # Skip underline text
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

def is_currently_filtered(name, ftype, area, overlap):
    """Check if field would be filtered by current rules."""
    if re.match(r'^Check Box \d+$', name):
        return True
    if '_Stack' in name:
        return True
    colon_count = name.count(':')
    if colon_count >= 2 and ftype == 'Text' and overlap > 0.2:
        return True
    if ftype == 'Text' and overlap > 0.85 and area > 500:
        return True
    if ftype == 'Text' and area > 7000 and overlap > 0.4:
        return True
    header_patterns = [
        r'service territory', r'Proposed project', r'^Efficiency Rating',
        r'Disadvantaged',
    ]
    for pattern in header_patterns:
        if re.search(pattern, name, re.IGNORECASE):
            return True
    if '?' in name:
        return True
    return False

def get_widgets(pdf_path):
    doc = fitz.open(pdf_path)
    widgets = []
    for page_num, page in enumerate(doc):
        for w in page.widgets():
            if w.rect.is_empty:
                continue
            fname = w.field_name or ""
            # Use field_type_string for accurate type
            ftype = w.field_type_string
            widgets.append({
                'name': fname,
                'type': ftype,
                'rect': w.rect,
                'page': page_num
            })
    doc.close()
    return widgets

def main():
    remaining_fps = []
    
    for pdf_id, gt_name, v22_name in PDF_PAIRS:
        v22_path = os.path.join(v22_dir, v22_name)
        gt_path = os.path.join(gt_dir, gt_name)
        
        v22_widgets = get_widgets(v22_path)
        gt_widgets = get_widgets(gt_path)
        
        # Get text blocks per page
        doc = fitz.open(v22_path)
        text_by_page = {i: get_text_blocks(page) for i, page in enumerate(doc)}
        doc.close()
        
        # Match v22 to GT
        matched_v22 = set()
        for gt_w in gt_widgets:
            best_iou = 0
            best_idx = None
            for i, v22_w in enumerate(v22_widgets):
                if i in matched_v22:
                    continue
                if v22_w['page'] != gt_w['page']:
                    continue
                iou = calc_iou(v22_w['rect'], gt_w['rect'])
                if iou > best_iou:
                    best_iou = iou
                    best_idx = i
            if best_iou >= IOU_THRESHOLD and best_idx is not None:
                matched_v22.add(best_idx)
        
        # Collect remaining FPs (unmatched v22 fields)
        pdf_name = os.path.basename(v22_path).replace('_v22_predicted.pdf', '')
        for i, v22_w in enumerate(v22_widgets):
            if i in matched_v22:
                continue
            
            # Calculate overlap
            text_blocks = text_by_page.get(v22_w['page'], [])
            overlap, texts = field_text_overlap_ratio(v22_w['rect'], text_blocks)
            area = v22_w['rect'].width * v22_w['rect'].height
            
            # Skip if already filtered
            if is_currently_filtered(v22_w['name'], v22_w['type'], area, overlap):
                continue
            
            remaining_fps.append({
                'pdf': pdf_id,
                'name': v22_w['name'],
                'type': v22_w['type'],
                'area': area,
                'overlap': overlap,
                'texts': texts[:3],  # First 3 overlapping texts
            })
    
    print("=" * 70)
    print("REMAINING FPs AFTER CURRENT FILTERING")
    print("=" * 70)
    print(f"\nTotal remaining FPs: {len(remaining_fps)}")
    
    # Group by type
    by_type = defaultdict(list)
    for fp in remaining_fps:
        by_type[fp['type']].append(fp)
    
    for ftype, items in sorted(by_type.items()):
        print(f"\n{ftype}: {len(items)} FPs")
    
    # Analyze patterns
    print("\n" + "=" * 70)
    print("FP ANALYSIS BY OVERLAP")
    print("=" * 70)
    
    text_fps = [fp for fp in remaining_fps if fp['type'] == 'Text']
    cb_fps = [fp for fp in remaining_fps if fp['type'] == 'CheckBox']
    rb_fps = [fp for fp in remaining_fps if fp['type'] == 'RadioButton']
    
    print(f"\nText FPs with overlap > 70%: {len([f for f in text_fps if f['overlap'] > 0.7])}")
    print(f"Text FPs with overlap > 50%: {len([f for f in text_fps if f['overlap'] > 0.5])}")
    print(f"Text FPs with overlap > 30%: {len([f for f in text_fps if f['overlap'] > 0.3])}")
    
    # Sample high overlap FPs
    print("\n" + "=" * 70)
    print("SAMPLE HIGH OVERLAP TEXT FPs (>70%)")
    print("=" * 70)
    high_overlap = [f for f in text_fps if f['overlap'] > 0.7]
    for fp in high_overlap[:20]:
        print(f"  {fp['pdf']}: '{fp['name'][:50]}' area={fp['area']:.0f} overlap={fp['overlap']*100:.0f}%")
        print(f"       texts: {fp['texts']}")
    
    # Analyze name patterns
    print("\n" + "=" * 70)
    print("NAME PATTERN ANALYSIS FOR FPs")
    print("=" * 70)
    
    # Common name patterns
    patterns_count = Counter()
    for fp in remaining_fps:
        name = fp['name']
        if re.match(r'^[0-9,.]+$', name):
            patterns_count['numeric_only'] += 1
        elif re.match(r'^[A-Z][a-z]+$', name):
            patterns_count['capitalized_word'] += 1
        elif re.match(r'^[a-z]+$', name):
            patterns_count['lowercase_word'] += 1
        elif re.match(r'^[A-Z]+$', name):
            patterns_count['uppercase_word'] += 1
        elif ':' in name:
            patterns_count['contains_colon'] += 1
        elif '_' in name:
            patterns_count['contains_underscore'] += 1
        elif ' ' in name:
            patterns_count['contains_space'] += 1
        else:
            patterns_count['other'] += 1
    
    print("\nName patterns:")
    for pattern, count in patterns_count.most_common():
        print(f"  {pattern}: {count}")
    
    # Sample different patterns
    print("\n" + "=" * 70)
    print("SAMPLE FPs BY PATTERN")
    print("=" * 70)
    
    numeric_fps = [f for f in remaining_fps if re.match(r'^[0-9,.]+$', f['name'])]
    if numeric_fps:
        print(f"\nNumeric-only names ({len(numeric_fps)} FPs):")
        for fp in numeric_fps[:10]:
            print(f"  {fp['pdf']}: '{fp['name']}' type={fp['type']} area={fp['area']:.0f} overlap={fp['overlap']*100:.0f}%")
    
    # Checkbox FPs
    if cb_fps:
        print(f"\nCheckBox FPs ({len(cb_fps)}):")
        for fp in cb_fps[:15]:
            print(f"  {fp['pdf']}: '{fp['name'][:40]}' area={fp['area']:.0f} overlap={fp['overlap']*100:.0f}%")
    
    # RadioButton FPs
    if rb_fps:
        print(f"\nRadioButton FPs ({len(rb_fps)}):")
        for fp in rb_fps[:15]:
            print(f"  {fp['pdf']}: '{fp['name'][:40]}' area={fp['area']:.0f} overlap={fp['overlap']*100:.0f}%")
    
    # Large area FPs
    print("\n" + "=" * 70)
    print("LARGE AREA FPs (>5000)")
    print("=" * 70)
    large_fps = [f for f in remaining_fps if f['area'] > 5000]
    for fp in sorted(large_fps, key=lambda x: -x['area'])[:20]:
        print(f"  {fp['pdf']}: '{fp['name'][:40]}' type={fp['type']} area={fp['area']:.0f} overlap={fp['overlap']*100:.0f}%")

if __name__ == "__main__":
    main()
