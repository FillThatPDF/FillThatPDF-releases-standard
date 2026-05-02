#!/usr/bin/env python3
"""
Analyze whether v22 fields are positioned on underlines.
Based on the hint: TRUE fields go ON lines (____).
"""

import fitz
import os

def get_underlines(page):
    """Get all horizontal lines that could be form underlines."""
    lines = []
    
    # Method 1: Get drawing paths (lines)
    paths = page.get_drawings()
    for path in paths:
        for item in path.get('items', []):
            if item[0] == 'l':  # line
                p1, p2 = item[1], item[2]
                # Horizontal line (y values close)
                if abs(p1.y - p2.y) < 2:
                    width = abs(p2.x - p1.x)
                    if width > 20:  # Minimum underline length
                        lines.append({
                            'type': 'drawing',
                            'rect': fitz.Rect(min(p1.x, p2.x), p1.y - 2, max(p1.x, p2.x), p1.y + 2),
                            'width': width
                        })
    
    # Method 2: Get text underlines (______)
    text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    for block in text_dict.get("blocks", []):
        if block.get("type") == 0:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "")
                    # Check if mostly underscores
                    underscore_count = text.count('_')
                    if underscore_count >= 5 and underscore_count / max(len(text), 1) > 0.7:
                        bbox = fitz.Rect(span["bbox"])
                        lines.append({
                            'type': 'text_underscore',
                            'rect': bbox,
                            'width': bbox.width
                        })
    
    return lines

def field_near_underline(field_rect, underlines, tolerance=5):
    """Check if field bottom edge is near an underline."""
    field_bottom = field_rect.y1
    
    for line in underlines:
        line_y = line['rect'].y0  # Top of underline
        # Check if field bottom is close to underline
        if abs(field_bottom - line_y) < tolerance:
            # Check horizontal overlap
            if field_rect.x0 < line['rect'].x1 and field_rect.x1 > line['rect'].x0:
                return True
    
    return False

# Test on 57618 (good test case)
v22_path = '/tmp/v22_output/57618_v22_output.pdf'
gt_path = '/Users/36981/Desktop/PDFTest/PDFs to test/Fillable PDFs/57618_NGRID_New_York_Ack_Form_Fillable_v07_Release_Web_Fillable.pdf'

print("=== 57618 Underline Analysis ===")

doc = fitz.open(v22_path)
page = doc[0]

underlines = get_underlines(page)
print(f"\nFound {len(underlines)} underlines on page 1")
for line in underlines[:10]:
    r = line['rect']
    print(f"  {line['type']}: x={r.x0:.0f}-{r.x1:.0f}, y={r.y0:.0f}, width={line['width']:.0f}")

print("\n--- V22 Text Fields ---")
text_fields_on_line = 0
text_fields_not_on_line = 0

for w in page.widgets():
    if w.field_type == 7:  # Text
        name = w.field_name or ''
        on_line = field_near_underline(w.rect, underlines)
        if on_line:
            text_fields_on_line += 1
            print(f"  ON LINE: '{name[:50]}'")
        else:
            text_fields_not_on_line += 1
            print(f"  NOT on line: '{name[:50]}'")

print(f"\nSummary: {text_fields_on_line} on lines, {text_fields_not_on_line} not on lines")
doc.close()
