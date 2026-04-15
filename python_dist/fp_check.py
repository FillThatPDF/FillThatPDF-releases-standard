import fitz
import os
import re

V22_PATH = '/tmp/v22_all_output/53252_DTE_EEA_Field_Inspection_Report_v11_v22_output.pdf'

def get_text_blocks(page):
    blocks = []
    text_dict = page.get_text('dict', flags=fitz.TEXT_PRESERVE_WHITESPACE)
    for block in text_dict.get('blocks', []):
        if block.get('type') == 0:
            for line in block.get('lines', []):
                for span in line.get('spans', []):
                    text = span.get('text', '').strip()
                    if text:
                        non_underscore = text.replace('_', '').replace('-', '').strip()
                        if len(non_underscore) < len(text) * 0.3:
                            continue
                        blocks.append({'text': text, 'rect': fitz.Rect(span['bbox'])})
    return blocks

def field_text_overlap_ratio(field_rect, text_blocks):
    field_area = field_rect.width * field_rect.height
    if field_area == 0:
        return 0
    total_overlap = 0
    for block in text_blocks:
        x1 = max(field_rect.x0, block['rect'].x0)
        y1 = max(field_rect.y0, block['rect'].y0)
        x2 = min(field_rect.x1, block['rect'].x1)
        y2 = min(field_rect.y1, block['rect'].y1)
        if x2 > x1 and y2 > y1:
            total_overlap += (x2-x1)*(y2-y1)
    return min(1.0, total_overlap / field_area)

doc = fitz.open(V22_PATH)
fps = []
for page_idx, page in enumerate(doc):
    text_blocks = get_text_blocks(page)
    for widget in page.widgets():
        name = widget.field_name or ''
        ftype = widget.field_type_string
        area = widget.rect.width * widget.rect.height
        overlap = field_text_overlap_ratio(widget.rect, text_blocks)
        width = widget.rect.width
        height = widget.rect.height
        
        if re.match(r'^Check Box \d+$', name) or '_Stack' in name:
            continue
            
        if ftype == 'Text':
            fps.append({
                'name': name, 'area': area, 'overlap': overlap,
                'width': width, 'height': height, 'page': page_idx,
                'has_colon': ':' in name, 'has_space': ' ' in name
            })

print(f'Text fields total: {len(fps)}')
print(f'overlap=0 AND width>200: {len([f for f in fps if f["overlap"] == 0 and f["width"] > 200])}')
print(f'has colon AND has space: {len([f for f in fps if f["has_colon"] and f["has_space"]])}')
print(f'3+ words in name: {len([f for f in fps if len(f["name"].split()) > 2])}')
print(f'overlap=0: {len([f for f in fps if f["overlap"] == 0])}')
print(f'height < 15: {len([f for f in fps if f["height"] < 15])}')
print(f'width/height > 20: {len([f for f in fps if f["width"] / max(1, f["height"]) > 20])}')
