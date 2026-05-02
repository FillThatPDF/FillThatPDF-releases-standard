
import pdfplumber
import json
import pikepdf
from pathlib import Path

v21_path = "/Users/36981/Desktop/PDFTest/PDFs to test/Fillable PDFs/55570_DTE_SEEL_Contractor_Onboarding_Packet_v21_Web_Release_Fillable.pdf"

def get_fingerprints(pdf_path):
    fingerprints = []
    with pdfplumber.open(pdf_path) as plumber_pdf:
        with pikepdf.open(pdf_path) as pikepdf_pdf:
            for page_num in range(len(pikepdf_pdf.pages)):
                page_pikepdf = pikepdf_pdf.pages[page_num]
                page_plumber = plumber_pdf.pages[page_num]
                words = page_plumber.extract_words()
                
                if not hasattr(page_pikepdf, 'Annots'): continue
                
                for annot in page_pikepdf.Annots:
                    if annot.get('/Subtype') != '/Widget': continue
                    
                    rect = [float(x) for x in annot.Rect] # [x0, y0, x1, y1]
                    cx = (rect[0] + rect[2]) / 2
                    cy = (rect[1] + rect[3]) / 2
                    
                    best_w = None
                    min_dist = 9999
                    
                    # Filter out numeric-only words or very short words as anchors
                    candidate_words = [w for w in words if not w['text'].isdigit() and len(w['text']) > 2]
                    if not candidate_words: candidate_words = words # Fallback if only dots/numbers
                    
                    ph = float(page_plumber.height) # Define ph here for dist_to_field lambda
                    dist_to_field = lambda w: ((float(w['x0']) + float(w['x1'])) / 2 - (rect[0] + rect[2]) / 2)**2 + \
                                              (ph - (float(w['top']) + float(w['bottom'])) / 2 - (rect[1] + rect[3]) / 2)**2
                    candidate_words.sort(key=dist_to_field)
                    best_w = candidate_words[0] if candidate_words else None
                    
                    if best_w:
                        w_ux_center = (float(best_w['x0']) + float(best_w['x1'])) / 2
                        w_uy_center = ph - ((float(best_w['top']) + float(best_w['bottom'])) / 2)

                        # Use a LOCALITY-BASED anchor approach (Nearest N words horizontally)
                        # Instead of the entire line (which might span multiple columns), 
                        # define a window around the field's center x-coordinate.
                        
                        line_y = float(best_w['top'])
                        
                        # Define proximity window: Field Center +/- 250px (covers typical column width)
                        # If the field is very wide, use the field's own width
                        field_width = rect[2] - rect[0]
                        search_radius = max(250, field_width * 1.5)
                        
                        line_words = [w for w in words if abs(float(w['top']) - line_y) < 3 and abs(((float(w['x0']) + float(w['x1']))/2) - w_ux_center) < search_radius]
                        line_words.sort(key=lambda w: float(w['x0']))
                        anchor_text = " ".join([w['text'] for w in line_words])
                        
                        # Improved robustness for checklist questions
                        # If the line starts with variable words (No, Major, Is, Does), try to capture the core phrase
                        # e.g. "No air leaks..." -> "air leaks..."
                        core_text = anchor_text
                        first_word = anchor_text.split()[0].lower() if anchor_text else ""
                        if first_word in ['no', 'major', 'minor', 'is', 'does', 'check']:
                            # Use the rest of the sentence as the primary anchor
                            core_parts = anchor_text.split()[1:]
                            if len(core_parts) > 3: # Only if enough substance remains
                                core_text = " ".join(core_parts)
                                anchor_text = core_text # Prefer the core invariant part
                        
                        # Special handling for "Black Square" checkboxes (common in this PDF)
                        # If the anchor starts with ■ (U+25A0) or similar, treat as checkbox label
                        if anchor_text and (anchor_text[0] in ['■', '□', '▪', '▫']):
                             # It's likely a checkbox label
                             anchor_text = anchor_text[1:].strip()
                        
                        # If the line is too short (< 20 chars), include the line ABOVE it for context
                        if len(anchor_text) < 20:
                            lines_above = sorted(list(set(round(float(w['top']), 0) for w in words if float(w['top']) < line_y - 2)), reverse=True)
                            if lines_above:
                                prev_y = lines_above[0]
                                prev_words = sorted([w for w in words if abs(float(w['top']) - prev_y) < 3], key=lambda w: float(w['x0']))
                                prev_text = " ".join([w['text'] for w in prev_words])
                                anchor_text = prev_text + " | " + anchor_text
                        
                        w_ux_center = (float(best_w['x0']) + float(best_w['x1'])) / 2
                        w_uy_center = ph - ((float(best_w['top']) + float(best_w['bottom'])) / 2)
                        
                        # Capture hierarchy for radio buttons
                        field_name = str(annot.get('/T', ''))
                        parent_name = ""
                        ff = int(annot.get('/Ff', 0))
                        
                        if not field_name and '/Parent' in annot:
                            parent_name = str(annot.Parent.get('/T', ''))
                            field_name = f"{parent_name}.{annot.get('/AS', 'Widget')}"
                        elif not field_name:
                            field_name = f"Unnamed_{page_num}_{id(annot)}"

                        is_pushbutton = (ff & 0x10000) != 0
                        is_radio = (ff & 0x8000) != 0
                        
                        action_js = ""
                        if '/A' in annot and '/JS' in annot.A:
                            action_js = str(annot.A.JS)
                        
                        fingerprints.append({
                            "page": page_num,
                            "anchor": anchor_text, 
                            "anchor_pos": [w_ux_center, w_uy_center], 
                            "dx0": rect[0] - w_ux_center,
                            "dy0": rect[1] - w_uy_center,
                            "dx1": rect[2] - w_ux_center,
                            "dy1": rect[3] - w_uy_center,
                            "type": str(annot.get('/FT', 'None')),
                            "flags": ff,
                            "is_radio": is_radio,
                            "is_pushbutton": is_pushbutton,
                            "action_js": action_js,
                            "as_state": str(annot.get('/AS', '')),
                            "original_name": field_name,
                            "parent_name": parent_name,
                            "parent_name": parent_name,
                            "abs_rect": rect
                        })
    
    # Extract Page Landmarks (Grid of words for Global Registration)
    print("Extracting Page Landmarks for Global Registration...")
    landmarks = {}
    with pdfplumber.open(pdf_path) as plumber_pdf:
        for i, page in enumerate(plumber_pdf.pages):
            words = page.extract_words()
            # Select ~50 stable words spread across the page
            # Sort by length (longer = more unique) and take top 100, then sample spatially
            long_words = [w for w in words if len(w['text']) > 4 and w['text'].isalpha()]
            
            # Simple spatial sampling: Grid 5x5
            grid_landmarks = []
            if long_words:
                w_per_cell = 2
                cw = page.width / 5
                ch = page.height / 5
                for r in range(5):
                    for c in range(5):
                        cell_words = [w for w in long_words if (c*cw < float(w['x0']) < (c+1)*cw) and (r*ch < float(w['top']) < (r+1)*ch)]
                        if cell_words:
                            # Take longest in cell
                            cell_words.sort(key=lambda w: -len(w['text']))
                            grid_landmarks.extend(cell_words[:w_per_cell])
            
            landmarks[i] = [{
                'text': w['text'],
                'x': float(w['x0']),
                'y': float(w['top']) 
            } for w in grid_landmarks]

    return fingerprints, landmarks

print("Extracting Robust Fingerprints from v21...")
fps, lms = get_fingerprints(v21_path)
output_data = {
    "fingerprints": fps,
    "landmarks": lms
}
with open("fingerprints_55570.json", "w") as f:
    json.dump(output_data, f, indent=2)
print(f"Saved {len(fps)} fingerprints and landmarks for {len(lms)} pages.")
