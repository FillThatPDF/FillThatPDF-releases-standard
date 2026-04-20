
import pdfplumber
import json
import pikepdf
from pathlib import Path
from collections import defaultdict

v26_path = "/Users/36981/Desktop/PDFTest/PDFs to test/Static PDFs/55570_DTE_SEEL_Contractor_Onboarding _Packet_v26.pdf"
fp_path = "fingerprints_55570.json"

def apply_transfer(target_pdf_path, fp_path):
    # Load fingerprints and landmarks
    with open(fp_path, 'r') as f:
        data = json.load(f)
        fingerprints = data['fingerprints']
        landmarks = data['landmarks']
        
    print(f"Loaded {len(fingerprints)} fingerprints and landmarks for {len(landmarks)} pages.")
    
    final_fields = []
    
    # Calculate Global Page Shifts first
    page_shifts = {} # page_idx -> (dx, dy)
    
    with pdfplumber.open(target_pdf_path) as pdf:
        num_pages_v26 = len(pdf.pages)
        print(f"Target PDF has {num_pages_v26} pages.")
        
        # 1. Build Similarity Matrix
        # Rows: v21 pages (from landmarks)
        # Cols: v26 pages (all)
        v21_indices = sorted([int(k) for k in landmarks.keys()])
        sim_matrix = {} # (v21_idx, v26_idx) -> score
        
        print("Building Similarity Matrix...")
        v26_words_cache = {}
        for j in range(num_pages_v26):
            v26_words_cache[j] = set(w['text'] for w in pdf.pages[j].extract_words() if len(w['text']) > 4)
            
        for i in v21_indices:
            v21_lms = landmarks[str(i)]
            v21_text_set = set(l['text'] for l in v21_lms)
            
            # Optimization: Only check moving window forward?
            # Safe to check broad window i to i+20
            start_search = max(0, i - 2)
            end_search = min(num_pages_v26, i + 20)
            
            for j in range(start_search, end_search):
                v26_text_set = v26_words_cache[j]
                intersect = len(v21_text_set.intersection(v26_text_set))
                union = len(v21_text_set.union(v26_text_set))
                score = intersect / union if union > 0 else 0
                if score > 0.05: # Lowered threshold
                    sim_matrix[(i, j)] = score

        # 2. Find Optimal Monotonic Path (Strict Sequential)
        # Strategy: Strict lookahead window of [current, current+3].
        # If no match > 0.1, ASSUME sequential step (current + 1).
        
        page_tracking_map = {}
        current_v26 = 0
        
        print("Calculating Strict Sequential Path...")
        for i in v21_indices:
            # Window: from current_v26 to current_v26 + 4
            # We don't want to jump more than a few pages at once.
            start_search = current_v26
            end_search = min(num_pages_v26, current_v26 + 4)
            
            best_j = -1
            best_score = 0
            
            candidates = []
            for j in range(start_search, end_search):
                score = sim_matrix.get((i, j), 0)
                candidates.append((score, j))
            
            candidates.sort(key=lambda x: -x[0])
            
            # Threshold: 0.1 (Strict enough to avoid noise)
            if candidates and candidates[0][0] > 0.1:
                best_score = candidates[0][0]
                best_j = candidates[0][1]
            
            if best_j != -1:
                # High confidence match found
                page_tracking_map[i] = best_j
                current_v26 = best_j # Advance
                
                # Compute Shift
                v21_lms = landmarks[str(i)]
                p_v26 = pdf.pages[best_j]
                words_v26 = p_v26.extract_words()
                
                deltas_x = []
                deltas_y = []
                
                for lm in v21_lms:
                    candidates_w = [w for w in words_v26 if w['text'] == lm['text']]
                    if candidates_w:
                        closest = min(candidates_w, key=lambda w: abs(float(w['top']) - lm['y']) + abs(float(w['x0']) - lm['x']))
                        if abs(float(closest['top']) - lm['y']) < 300:
                             dx = float(closest['x0']) - lm['x']
                             dy = float(closest['top']) - lm['y']
                             deltas_x.append(dx)
                             deltas_y.append(dy)

                if deltas_x:
                    deltas_x.sort()
                    deltas_y.sort()
                    median_dx = deltas_x[len(deltas_x)//2]
                    median_dy = deltas_y[len(deltas_y)//2]
                    page_shifts[i] = (median_dx, median_dy)
                    print(f"  Map: v21 P{i} -> v26 P{best_j} | Score: {best_score:.2f} | Shift: ({median_dx:.1f}, {median_dy:.1f})")
                else:
                    page_shifts[i] = (0, 0)
            else:
                 # No strong match found in window.
                 # Fallback: Assume linear progression (current + 1), IF available.
                 fallback_j = min(num_pages_v26 - 1, current_v26 + 1)
                 
                 # Sanity check: If current_v26 is already far ahead, maybe just stay? 
                 # Actually, usually pages map 1:1 in a sequence.
                 # So if we mapped P20->P25, then P21 probably maps to P26.
                 
                 # Let's verify if fallback_j has ANY similarity? No, blindly trust sequence.
                 # But we must update current_v26 to avoid multiple pages collapsing to same page.
                 
                 # Improved Fallback:
                 # If previous was P(i-1) -> P(k), then P(i) -> P(k+1) is safest guess.
                 # UNLESS landmarks suggest we should stay on P(k) (e.g. split page)?
                 # For now, strict +1 increment to fill gaps.
                 
                 # Exception: If this is the VERY FIRST page (i=0) and no match, start at 0.
                 if i == 0: fallback_j = 0
                 
                 # But we need to ensure we don't run out of pages.
                 if fallback_j >= num_pages_v26: fallback_j = num_pages_v26 - 1
                 
                 page_tracking_map[i] = fallback_j
                 current_v26 = fallback_j # Force advance
                 page_shifts[i] = (0, 0) # Default shift
                 print(f"  Map: v21 P{i} -> v26 P{fallback_j} [FALLBACK +1]")
                
    # Now Apply Fields using the calculated Page Map & Shifts
    print("Applying fields...")
    for fp in fingerprints:
        orig_page = fp['page']
        
        if orig_page in page_tracking_map:
            target_page_idx = page_tracking_map[orig_page]
            shift = page_shifts.get(orig_page, (0,0))
            
            # Apply shift
            r = fp['abs_rect']
            new_rect = [
                r[0] + shift[0],
                r[1] + shift[1],
                r[2] + shift[0],
                r[3] + shift[1]
            ]
            
            # Determine complex type
            final_type = "text"
            if fp.get('is_radio'): final_type = "radio"
            elif fp.get('is_pushbutton'): final_type = "pushbutton"
            elif "/Btn" in fp['type'] or "/Ch" in fp['type']: final_type = "checkbox"

            final_fields.append({
                "rect": new_rect,
                "page": target_page_idx,
                "label": fp['original_name'],
                "parent": fp.get('parent_name', ''),
                "type": final_type,
                "flags": fp.get('flags', 0),
                "action_js": fp.get('action_js', ''),
                "as_state": fp.get('as_state', '')
            })
    # Audit distribution
    dist = {}
    for f in final_fields:
        p = f['page']
        dist[p] = dist.get(p, 0) + 1
    print("Page Distribution of Fields in v26:")
    for p in sorted(dist.keys()):
        print(f"  Page {p+1}: {dist[p]} fields")
        
    return final_fields

print("Applying Robust Topological Transfer to v26...")
results = apply_transfer(v26_path, fp_path)
print(f"Relocated {len(results)} fields.")

with open("topological_results.json", "w") as f:
    json.dump(results, f, indent=2)

print("Topological mapping complete.")
