#!/usr/bin/env python3
"""
NEW DETECTION PASS: Horizontal-Line-Only Tables
================================================

This module adds a specialized detection pass for tables that ONLY have
horizontal divider lines (no vertical cell boundaries).

Example: NYSEG/RG&E forms with Account Name, Contact, Title, Email fields
separated by horizontal lines but no vertical dividers.

DETECTION STRATEGY:
1. Find all strong horizontal lines (spanning >70% of page width)
2. Group into rows based on Y-position
3. Detect text labels (with colons) between the lines
4. Create fields to the right of labels, bounded by:
   - Left: End of label text + small gap
   - Right: Right edge of the line (or page margin)
   - Top/Bottom: Between the horizontal lines

INTEGRATION:
- Runs as PASS 4D (after table detection, before cleanup)
- Non-intrusive: Only creates fields in empty regions
- Respects existing fields (won't overlap)

Author: Extension to smart_fillable_v22
Date: February 2026
"""

def _detect_horizontal_line_tables(self):
    """
    NEW PASS: Detect and create fields in tables with only horizontal divider lines.
    
    This handles PDFs like NYSEG forms where:
    - Tables have horizontal lines separating rows
    - NO vertical lines defining columns
    - Labels are inline with colons (e.g., "Account Name:")
    - Fields should span from label end to right edge
    
    Strategy:
    1. Find horizontal lines spanning most of page width (>400pt)
    2. Group lines by Y position to find row boundaries
    3. Between each pair of lines, find text with colons
    4. Create text fields to the right of the label
    """
    import fitz  # PyMuPDF for line detection
    
    print(f"\n🏗️  PASS 4D: Detecting horizontal-line-only tables...")
    
    created_count = 0
    
    for page_num in range(self.page_count):
        # Skip text-only pages
        if page_num in self.text_only_pages:
            continue
            
        # Open page with PyMuPDF for line detection
        doc = fitz.open(str(self.input_pdf))
        fitz_page = doc[page_num]
        page = self.pdf.pages[page_num]
        
        page_width = page.width
        page_height = page.height
        
        # === STEP 1: Find all horizontal lines ===
        drawings = fitz_page.get_drawings()
        h_lines = []
        
        for drawing in drawings:
            for item in drawing['items']:
                if item[0] == 'l':  # line
                    p1, p2 = item[1], item[2]
                    # Horizontal line (same Y within tolerance)
                    if abs(p1.y - p2.y) < 2:
                        line_width = abs(p2.x - p1.x)
                        # Only consider substantial horizontal lines (>60% page width)
                        if line_width > page_width * 0.6:
                            h_lines.append({
                                'y': (p1.y + p2.y) / 2,  # Average Y
                                'x1': min(p1.x, p2.x),
                                'x2': max(p1.x, p2.x),
                                'width': line_width
                            })
        
        if len(h_lines) < 2:
            # Need at least 2 lines to define a row
            doc.close()
            continue
        
        # Sort lines by Y position (top to bottom)
        h_lines.sort(key=lambda l: l['y'])
        
        # Group lines that are very close (within 3 points)
        line_groups = []
        for line in h_lines:
            if line_groups and abs(line_groups[-1]['y'] - line['y']) < 3:
                # Merge into existing group (use average)
                line_groups[-1]['y'] = (line_groups[-1]['y'] + line['y']) / 2
                line_groups[-1]['x1'] = min(line_groups[-1]['x1'], line['x1'])
                line_groups[-1]['x2'] = max(line_groups[-1]['x2'], line['x2'])
            else:
                line_groups.append(line.copy())
        
        if len(line_groups) < 2:
            doc.close()
            continue
        
        print(f"   Page {page_num + 1}: Found {len(line_groups)} horizontal line groups")
        
        # === STEP 2: Get all text on the page ===
        words = page.extract_words(
            x_tolerance=3,
            y_tolerance=3,
            keep_blank_chars=False,
            use_text_flow=True
        )
        
        # === STEP 3: For each row (between pairs of lines), look for labels ===
        for i in range(len(line_groups) - 1):
            line_top = line_groups[i]
            line_bottom = line_groups[i + 1]
            
            row_y1 = line_top['y']
            row_y2 = line_bottom['y']
            row_height = row_y2 - row_y1
            
            # Skip very thin rows (< 12pt)
            if row_height < 12:
                continue
            
            # Skip very tall rows (> 80pt) - likely not a form row
            if row_height > 80:
                continue
            
            # Find text in this row
            row_words = [w for w in words if row_y1 < w['top'] < row_y2]
            
            if not row_words:
                continue
            
            # Look for text ending with colon (labels)
            labels = []
            for word in row_words:
                text = word['text'].strip()
                if text.endswith(':'):
                    # This is likely a label
                    labels.append({
                        'text': text,
                        'x0': word['x0'],
                        'x1': word['x1'],
                        'y0': word['top'],
                        'y1': word['bottom']
                    })
            
            if not labels:
                # No labels found, skip this row
                continue
            
            # Sort labels by X position (left to right)
            labels.sort(key=lambda l: l['x0'])
            
            # === STEP 4: Create fields to the right of each label ===
            for label in labels:
                label_text = label['text'].rstrip(':')
                
                # Field starts after the label with a small gap
                field_x0 = label['x1'] + 3
                
                # Field ends at the right edge of the line (or before next label)
                # Find next label in this row
                next_labels = [l for l in labels if l['x0'] > label['x1']]
                if next_labels:
                    # End before next label
                    field_x1 = next_labels[0]['x0'] - 5
                else:
                    # End at line right edge
                    field_x1 = line_top['x2'] - 5
                
                # Field spans the row height (with small margins)
                field_y0 = row_y1 + 2
                field_y1 = row_y2 - 2
                
                # Validate field dimensions
                field_width = field_x1 - field_x0
                field_height = field_y1 - field_y0
                
                if field_width < 30:  # Too narrow
                    continue
                if field_height < 8:  # Too short
                    continue
                
                # Check if this area already has a field
                overlap = False
                for existing in self.text_fields:
                    if existing['page'] != page_num:
                        continue
                    # Check overlap
                    if not (field_x1 < existing['x0'] or field_x0 > existing['x1'] or
                            field_y1 < existing['y0'] or field_y0 > existing['y1']):
                        overlap = True
                        break
                
                if overlap:
                    # Don't create field if it overlaps existing field
                    continue
                
                # Create the field!
                field_name = self._sanitize_field_name(f"{label_text}_P{page_num + 1}")
                
                self.text_fields.append({
                    'page': page_num,
                    'x0': field_x0,
                    'y0': field_y0,
                    'x1': field_x1,
                    'y1': field_y1,
                    'name': field_name,
                    'label': label_text,
                    'field_type': 'text',
                    'source': 'horizontal_line_table',
                    'row_index': i
                })
                
                created_count += 1
        
        doc.close()
    
    if created_count > 0:
        print(f"   ✅ Created {created_count} fields in horizontal-line tables")
    else:
        print(f"   No horizontal-line table fields detected")
    
    return created_count


# Add this method to the UniversalPDFFillable class
UniversalPDFFillable._detect_horizontal_line_tables = _detect_horizontal_line_tables
