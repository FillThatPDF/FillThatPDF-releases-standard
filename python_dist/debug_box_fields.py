#!/usr/bin/env python3
"""
Debug script to analyze box-type account number fields on page 7.
These appear to be constructed from lines, not filled rectangles.
"""

import pdfplumber
from collections import defaultdict

PDF_PATH = "/Users/36981/Desktop/PDFs to test/57643_DTE_MF_Measures_and_Specs_Income_Qualified_Catalog_v2.pdf"
PAGE_NUM = 6  # 0-indexed, so page 7 = index 6

def analyze_lines():
    """Analyze lines to find box patterns."""
    print("=" * 60)
    print("LINE ANALYSIS - Finding Box Patterns")
    print("=" * 60)
    
    pdf = pdfplumber.open(PDF_PATH)
    page = pdf.pages[PAGE_NUM]
    
    lines = page.lines
    
    # Separate vertical and horizontal lines
    v_lines = []
    h_lines = []
    
    for l in lines:
        x0, x1 = l['x0'], l['x1']
        y0, y1 = l['top'], l['bottom']
        
        if abs(x0 - x1) < 2:  # Vertical
            v_lines.append({
                'x': (x0 + x1) / 2,
                'y0': min(y0, y1),
                'y1': max(y0, y1),
                'length': abs(y1 - y0)
            })
        elif abs(y0 - y1) < 2:  # Horizontal
            h_lines.append({
                'x0': min(x0, x1),
                'x1': max(x0, x1),
                'y': (y0 + y1) / 2,
                'length': abs(x1 - x0)
            })
    
    print(f"\nVertical lines: {len(v_lines)}")
    print(f"Horizontal lines: {len(h_lines)}")
    
    # Find vertical lines that are short (box height ~15-25pt)
    short_v_lines = [l for l in v_lines if 10 < l['length'] < 35]
    print(f"\nShort vertical lines (10-35pt): {len(short_v_lines)}")
    
    # Group short vertical lines by Y position
    v_by_y = defaultdict(list)
    for l in short_v_lines:
        y_key = round(l['y0'] / 5) * 5
        v_by_y[y_key].append(l)
    
    # Find groups with many vertical lines (indicating box rows)
    print("\n--- Potential Box Rows (by vertical line clusters) ---")
    for y_key, vlines in sorted(v_by_y.items()):
        if len(vlines) >= 5:  # At least 5 vertical lines = likely a box row
            vlines.sort(key=lambda l: l['x'])
            print(f"\nY~{y_key}: {len(vlines)} vertical lines")
            
            # Calculate spacing between lines
            if len(vlines) >= 2:
                spacings = [vlines[i+1]['x'] - vlines[i]['x'] for i in range(len(vlines)-1)]
                avg_spacing = sum(spacings) / len(spacings)
                print(f"  Average spacing: {avg_spacing:.1f}pt")
                print(f"  Line height: {vlines[0]['length']:.1f}pt")
                
                # Show X positions
                x_positions = [round(l['x'], 1) for l in vlines[:15]]
                print(f"  X positions: {x_positions}")
    
    # Look for the "Account" text and nearby structures
    print("\n--- Text Near Account Labels ---")
    words = page.extract_words()
    for word in words:
        if 'account' in word['text'].lower():
            text_y = word['top']
            text_x = word['x0']
            print(f"\n'{word['text']}' at [{text_x:.1f}, {text_y:.1f}]")
            
            # Find vertical lines below this text
            nearby_v = [l for l in short_v_lines 
                       if abs(l['y0'] - text_y) < 60 and l['y0'] > text_y - 10]
            print(f"  Vertical lines within 60pt below: {len(nearby_v)}")
            
            if nearby_v:
                nearby_v.sort(key=lambda l: l['x'])
                x_pos = [round(l['x'], 1) for l in nearby_v[:12]]
                print(f"  First few X positions: {x_pos}")
                if len(nearby_v) >= 2:
                    spacings = [nearby_v[i+1]['x'] - nearby_v[i]['x'] for i in range(min(10, len(nearby_v)-1))]
                    box_widths = [round(s, 1) for s in spacings]
                    print(f"  Box widths: {box_widths}")
    
    pdf.close()

def detect_box_groups():
    """Detect complete box groups from line intersections."""
    print("\n" + "=" * 60)
    print("BOX GROUP DETECTION")
    print("=" * 60)
    
    pdf = pdfplumber.open(PDF_PATH)
    page = pdf.pages[PAGE_NUM]
    
    lines = page.lines
    
    # Separate and clean lines
    v_lines = []
    h_lines = []
    
    for l in lines:
        x0, x1 = l['x0'], l['x1']
        y0, y1 = l['top'], l['bottom']
        
        if abs(x0 - x1) < 2:  # Vertical
            v_lines.append({
                'x': round((x0 + x1) / 2, 1),
                'y0': round(min(y0, y1), 1),
                'y1': round(max(y0, y1), 1),
                'length': abs(y1 - y0)
            })
        elif abs(y0 - y1) < 2:  # Horizontal
            h_lines.append({
                'x0': round(min(x0, x1), 1),
                'x1': round(max(x0, x1), 1),
                'y': round((y0 + y1) / 2, 1),
                'length': abs(x1 - x0)
            })
    
    # Find short vertical lines (box sides)
    short_v = [l for l in v_lines if 10 < l['length'] < 35]
    
    # Group by top Y position (allows ~2pt tolerance)
    v_groups = defaultdict(list)
    for l in short_v:
        y_key = round(l['y0'] / 3) * 3
        v_groups[y_key].append(l)
    
    print("\nDetected Box Groups:")
    box_groups = []
    
    for y_key, vlines in sorted(v_groups.items()):
        if len(vlines) >= 8:  # Account numbers have 10+ boxes
            vlines.sort(key=lambda l: l['x'])
            
            # Calculate consistent spacing
            spacings = [vlines[i+1]['x'] - vlines[i]['x'] for i in range(len(vlines)-1)]
            
            # Filter to find consistent spacing (box rows have uniform spacing)
            consistent_spacing = None
            for target_spacing in [18, 19, 20, 21, 22, 23, 24, 25]:
                matches = sum(1 for s in spacings if abs(s - target_spacing) < 3)
                if matches >= 8:
                    consistent_spacing = target_spacing
                    break
            
            if consistent_spacing:
                # This is a box row!
                num_boxes = len(vlines) - 1  # n vertical lines = n-1 boxes
                box_height = vlines[0]['length']
                x_start = vlines[0]['x']
                x_end = vlines[-1]['x']
                y_start = vlines[0]['y0']
                
                print(f"\n✅ Box Row at Y={y_start:.1f}")
                print(f"   {num_boxes} boxes, {box_height:.1f}pt tall, {consistent_spacing:.1f}pt wide")
                print(f"   X range: {x_start:.1f} to {x_end:.1f}")
                
                box_groups.append({
                    'y': y_start,
                    'x_start': x_start,
                    'x_end': x_end,
                    'num_boxes': num_boxes,
                    'box_width': consistent_spacing,
                    'box_height': box_height,
                    'vertical_lines': vlines
                })
    
    # Find labels for each box group
    words = page.extract_words()
    print("\n--- Matching Labels to Box Groups ---")
    
    for group in box_groups:
        # Look for text above the boxes
        nearby_words = []
        for word in words:
            # Word should be above or at the same level as boxes, and within x-range
            if word['top'] < group['y'] + 5 and word['top'] > group['y'] - 50:
                if word['x0'] >= group['x_start'] - 50 and word['x0'] <= group['x_end'] + 50:
                    nearby_words.append(word)
        
        if nearby_words:
            # Sort by proximity to box row
            nearby_words.sort(key=lambda w: abs(w['top'] - group['y']))
            label_parts = [w['text'] for w in nearby_words[:6]]
            label_text = ' '.join(label_parts)
            print(f"\nBox group at Y={group['y']:.1f}: '{label_text[:50]}'")
    
    pdf.close()
    return box_groups

if __name__ == "__main__":
    analyze_lines()
    detect_box_groups()
