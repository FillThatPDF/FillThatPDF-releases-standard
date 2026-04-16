
import sys
import json
import pdfplumber
import argparse
import re

def find_best_names(pdf_path, fields_data):
    """
    Find best names for multiple fields.
    fields_data: list of {'id': str, 'page': int, 'rect': [x0,y0,x1,y1]}
    Returns: dict {id: name}
    """
    results = {}
    doc = None
    try:
        doc = pdfplumber.open(pdf_path)
        
        # Group by page to minimize page loading
        by_page = {}
        for f in fields_data:
            p = int(f['page'])
            if p not in by_page:
                by_page[p] = []
            by_page[p].append(f)
            
        sys.stderr.write(f"DEBUG: Requested pages: {list(by_page.keys())}, Doc pages: {len(doc.pages)}\n")
        
        for p, fields in by_page.items():
            if p < 1 or p > len(doc.pages):
                sys.stderr.write(f"DEBUG: Skipping invalid page {p}\n")
                continue
                
            page = doc.pages[p - 1]
            # extract_words returns list of dicts: {'x0', 'top', 'x1', 'bottom', 'text', ...}
            words = page.extract_words()
            
            for f in fields:
                results[f['id']] = find_name_for_field(page, words, f['rect'])
                
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
    finally:
        if doc:
            doc.close()
            
    return results

def find_name_for_field(page, words, rect):
    # rect is usually [x0, y0, x1, y1] in PDF coords (bottom-up)
    # BUT wait, the input data from auto_rename_all might be using PyMuPDF coords.
    # We need to be careful with coordinate systems.
    # PyMuPDF uses top-down by default for Rect, same as pdfplumber.
    # However, sometimes they differ in origin. Let's assume standard top-down for rect here.
    fx0, fy0, fx1, fy1 = map(float, rect)
    
    # Helper: Check if text is valid (contains actual letters, not just symbols)
    def is_valid_text(text):
        # Must contain at least one letter
        return bool(re.search(r'[a-zA-Z]', text))
    
    # Candidate words
    left_candidates = []
    header_candidates = []
    
    # Tolerances
    left_gap_max = 300 
    header_gap_max = 1000 # Look far up for column headers
    
    f_cy = (fy0 + fy1) / 2
    f_width = fx1 - fx0
    
    # DEBUG COORDINATES
    if not words:
        sys.stderr.write("DEBUG: No words found on page!\n")
        return ""
    
    # Log first word and field rect to compare coords
    w0 = words[0]
    sys.stderr.write(f"DEBUG: Field Rect: [{fx0:.1f}, {fy0:.1f}, {fx1:.1f}, {fy1:.1f}]\n")
    sys.stderr.write(f"DEBUG: First Word: '{w0['text']}' at [{w0['x0']:.1f}, {w0['top']:.1f}, {w0['x1']:.1f}, {w0['bottom']:.1f}]\n")
    
    for w in words:
        # pdfplumber format
        wx0, wy0, wx1, wy1, text = w['x0'], w['top'], w['x1'], w['bottom'], w['text']
        w_cy = (wy0 + wy1) / 2
        
        # Debug logging for words near the field
        # Check if word is roughly in the same column (x-aligned)
        overlap = max(0, min(wx1, fx1) - max(wx0, fx0))
        w_width = wx1 - wx0
        
        # 1. Left (Same Line) - High Priority
        if abs(w_cy - f_cy) < 10:
            if wx1 < fx0 and (fx0 - wx1) < left_gap_max:
                if is_valid_text(text):  # Only add if has real letters
                    left_candidates.append((fx0 - wx1, text, wx0)) 
        
        # 2. Header (Above) - Strict Vertical Alignment
        # Relaxed check: Significant horizontal overlap OR Center Aligned
        valid_overlap = (overlap > w_width * 0.3) or (overlap > f_width * 0.3)
        
        # B. Or Left Aligned (within 15pt)
        left_aligned = abs(wx0 - fx0) < 15
        
        # C. Or Center Aligned (within 20pt)
        w_cx = (wx0 + wx1) / 2
        f_cx = (fx0 + fx1) / 2
        center_aligned = abs(w_cx - f_cx) < 20
        
        is_candidate = (valid_overlap or left_aligned or center_aligned) and wy1 < fy0 and (fy0 - wy1) < header_gap_max
        
        if is_candidate and is_valid_text(text):  # Only add if has real letters
             header_candidates.append((fy0 - wy1, text, wy1, wx0)) # dist, text, y_bottom, x_left
        
    # Strategy 1: Left Label (e.g. "Name:", "Date:")
    if left_candidates:
        left_candidates.sort(key=lambda x: x[2]) # Sort by X position
        text = " ".join([c[1] for c in left_candidates])
        clean = text.strip().rstrip(":")
        if clean:
            sys.stderr.write(f"DEBUG: Found left label: '{clean}'\n")
            return clean
            
    # Strategy 2: Column Header
    if header_candidates:
        # Find the CLOSEST text above (smallest dist)
        header_candidates.sort(key=lambda x: x[0])
        
        # Group by line (Y position tolerance ~5pt)
        lines = {}
        for dist, text, y1, x0 in header_candidates:
            found_line = False
            for k in lines:
                if abs(k - y1) < 5:
                    lines[k].append((x0, text))
                    found_line = True
                    break
            if not found_line:
                lines[y1] = [(x0, text)]
        
        if lines:
            # We want the bottom-most line (max Y) which is the closest to the field
            best_y = max(lines.keys())
            words_on_line = lines[best_y]
            words_on_line.sort(key=lambda x: x[0]) # sort by X
            
            text = " ".join([w[1] for w in words_on_line])
            clean = text.strip().rstrip(":")
            if clean and is_valid_text(clean):  # Double check final result has letters
                 sys.stderr.write(f"DEBUG: Found header: '{clean}'\n")
                 return clean

    sys.stderr.write("DEBUG: No suitable label found\n")
    return ""  # Explicit return empty string

if __name__ == "__main__":
    # Support both CLI args (legacy/single) and JSON stdin (batch)
    try:
        if len(sys.argv) > 1 and sys.argv[1].endswith('.pdf'):
            # Legacy/Single mode: python script.py pdf page x0 y0 x1 y1
            sys.stderr.write(f"DEBUG: Legacy mode with {sys.argv}\n")
            if len(sys.argv) < 7:
                print(json.dumps({"error": "Invalid args"}))
                sys.exit(1)
            
            pdf_path = sys.argv[1]
            fields = [{
                'id': 'single',
                'page': int(sys.argv[2]),
                'rect': [float(sys.argv[3]), float(sys.argv[4]), float(sys.argv[5]), float(sys.argv[6])]
            }]
            
            res = find_best_names(pdf_path, fields)
            print(json.dumps({"name": res.get('single', '')}), flush=True)
            
        else:
            # Batch Mode: Read output from stdin (JSON)
            sys.stderr.write("DEBUG: Batch mode, reading stdin...\n")
            content = sys.stdin.read()
            if not content:
                sys.stderr.write("DEBUG: Empty stdin\n")
                print(json.dumps({}), flush=True)
                sys.exit(0)
                
            input_data = json.loads(content)
            pdf_path = input_data['pdfPath']
            fields = input_data['fields']
            
            sys.stderr.write(f"DEBUG: Processing {len(fields)} fields for {pdf_path}\n")
            
            res = find_best_names(pdf_path, fields)
            
            output = json.dumps(res)
            sys.stderr.write(f"DEBUG: Found {len(res)} names\n")
            print(output, flush=True)

    except Exception as e:
        sys.stderr.write(f"CRITICAL ERROR: {e}\n")
        print(json.dumps({"error": str(e)}), flush=True)
        sys.exit(1)
