import pikepdf
from pdf2image import convert_from_path
import cv2
import numpy as np
import json
from pathlib import Path
import os
import argparse

def normalize_coords(bbox, width, height):
    # bbox: [min_x, min_y, max_x, max_y] (Top-Left Origin)
    min_x, min_y, max_x, max_y = bbox
    
    # Clip to image bounds
    min_x = max(0, min_x); min_y = max(0, min_y)
    max_x = min(width, max_x); max_y = min(height, max_y)
    
    w = max_x - min_x
    h = max_y - min_y
    
    cx = min_x + w/2
    cy = min_y + h/2
    
    return cx/width, cy/height, w/width, h/height

def normalize_name(name):
    # Clean up filename to find match
    return name.lower().replace("fillable", "").replace("final_release", "").replace("web_release", "").replace("print_release", "").replace("noedit", "").strip(" _-")

def find_static_match(filled_path, static_files):
    # Match Strategy: 
    # 1. Exact ID match (First 5 digits, e.g. "53252")
    # 2. Strict Page Count match (checked later, but we need to prioritize ID here)
    
    f_stem = filled_path.stem.lower()
    
    # Extract ID (First 5 chars if digits)
    f_id = f_stem[:5] if f_stem[:5].isdigit() else None
    
    # Fallback to simple containment if no ID
    if not f_id:
        n_f = normalize_name(f_stem)
        for s in static_files:
             if "ai_fillable" in s.stem.lower(): continue
             n_s = normalize_name(s.stem.lower())
             if n_s in n_f or n_f in n_s: return s
        return None

    # ID Based Matching
    candidates = []
    for s in static_files:
        s_stem = s.stem.lower()
        if "ai_fillable" in s_stem: continue
        
        # Check ID
        if s_stem.startswith(f_id):
            candidates.append(s)
    
    if not candidates: return None
    
    # Sort candidates? Ideally pick the one with most similar name length or just first.
    # Usually ID match is unique enough in this dataset.
    return candidates[0]

def draw_debug(image, labels, output_path):
    # labels: list of "class x y w h"
    # image: PIL Image
    cv_img = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    h, w = cv_img.shape[:2]
    
    for l in labels:
        parts = l.split()
        cls__id = int(parts[0])
        cx, cy, bw, bh = map(float, parts[1:])
        
        # De-normalize
        px = int((cx - bw/2) * w)
        py = int((cy - bh/2) * h)
        pw = int(bw * w)
        ph = int(bh * h)
        
        color = (0, 255, 0) # Green for text
        if cls__id == 1: color = (255, 0, 0) # Blue for Checkbox
        if cls__id == 2: color = (0, 0, 255) # Red for Radio
        
        cv2.rectangle(cv_img, (px, py), (px+pw, py+ph), color, 2)
        
    cv2.imwrite(str(output_path), cv_img)

def process_pairs(filled_dir, static_dir, output_dir):
    filled_p = Path(filled_dir)
    static_p = Path(static_dir)
    output_p = Path(output_dir)
    
    filled_files = list(filled_p.glob("*.pdf"))
    static_files = list(static_p.glob("*.pdf"))
    
    # Create Output Dirs
    (output_p / "images").mkdir(parents=True, exist_ok=True)
    (output_p / "labels").mkdir(parents=True, exist_ok=True)
    (output_p / "debug").mkdir(parents=True, exist_ok=True)
    
    print(f"Found {len(filled_files)} Filled PDFs")
    print(f"Found {len(static_files)} Static PDFs")
    
    processed_count = 0
    saved_pages = 0
    
    for f_path in filled_files:
        # 1. Find Match
        s_path = find_static_match(f_path, static_files)
        if not s_path:
            # print(f"Skipping {f_path.name} (No Static Match)")
            continue
            
        print(f"Match: {f_path.name} <-> {s_path.name}")
        
        try:
            # 2. Extract Labels from Filled (pikepdf)
            pdf = pikepdf.open(str(f_path))
            num_pages = len(pdf.pages)
            
            # 3. Extract Images from Static (pdf2image)
            # Use 200 DPI to match training data
            images = convert_from_path(str(s_path), dpi=200)
            
            if len(images) != num_pages:
                print(f"   ⚠️ Page Mismatch! Filled={num_pages}, Static={len(images)}. Skipping.")
                continue
                
            # 4. Process Each Page
            for i in range(num_pages):
                page = pdf.pages[i]
                image = images[i]
                
                # Page Dimensions (From Filled PDF for normalization)
                try:
                    mediabox = page.MediaBox
                    pdf_w = float(mediabox[2])
                    pdf_h = float(mediabox[3])
                except:
                    print(f"   Skip Page {i}: Bad MediaBox")
                    continue
                
                labels = []
                page_metadata = []
                
                if "/Annots" in page:
                    for annot in page.Annots:
                        if annot.Type == "/Annot" and annot.Subtype == "/Widget":
                            rect = [float(x) for x in annot.Rect]
                            
                            # Determine Class
                            cls_id = 0 
                            field_type = "Text"
                            
                            ft = str(annot.FT) if "/FT" in annot else ""
                            ff = int(annot.Ff) if "/Ff" in annot else 0
                            
                            is_btn = (ft == "/Btn")
                            is_ch = (ft == "/Ch")
                            
                            if is_btn:
                                if (ff & 32768): # Radio
                                    cls_id = 2; field_type = "Radio"
                                elif (ff & 65536): # Pushbutton
                                    continue 
                                else:
                                    cls_id = 1; field_type = "Checkbox"
                            elif is_ch:
                                cls_id = 3; field_type = "Dropdown"
                            
                            # Coordinate Transform (PDF Bottom-Left -> Normalized Top-Left)
                            x0, y0, x1, y1 = rect
                            
                            new_y_top = pdf_h - y1      # Min Y (distance from top)
                            new_y_bottom = pdf_h - y0   # Max Y
                            
                            final_box = [x0, new_y_top, x1, new_y_bottom]
                            
                            nx, ny, nw, nh = normalize_coords(final_box, pdf_w, pdf_h)
                            
                            labels.append(f"{cls_id} {nx:.6f} {ny:.6f} {nw:.6f} {nh:.6f}")
                            
                            meta = {
                                "id": f"field_{len(labels)}",
                                "class_id": cls_id,
                                "type": field_type,
                                "name": str(annot.T) if "/T" in annot else "",
                                "rect_norm": [nx, ny, nw, nh]
                            }
                            page_metadata.append(meta)
                            
                # Save Image (from Static!)
                if labels: # Only save if labels exist
                   out_name = f"empty_{f_path.stem}_page_{i}"
                   img_save_path = output_p / "images" / f"{out_name}.jpg"
                   
                   image.save(img_save_path, "JPEG")
                   
                   # Save Labels
                   with open(output_p / "labels" / f"{out_name}.txt", "w") as lf:
                       lf.write("\n".join(labels))
                       
                   # Save JSON
                   with open(output_p / "labels" / f"{out_name}.json", "w") as jf:
                       json.dump(page_metadata, jf)
                       
                   # Debug Image (First page of file only)
                   if i == 0:
                        draw_debug(image, labels, output_p / "debug" / f"debug_{out_name}.jpg")
                   
                   saved_pages += 1
                       
            processed_count += 1
            
        except Exception as e:
            print(f"   Error processing {f_path.name}: {e}")
            
    # create classes.txt
    with open(output_p / "classes.txt", "w") as f:
        f.write("Text Field\nCheckbox\nRadio Button\nDropdown")

    print(f"Done. Processed {processed_count} pairs. Saved {saved_pages} empty pages.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("filled_dir")
    parser.add_argument("static_dir")
    parser.add_argument("output_dir")
    args = parser.parse_args()
    
    process_pairs(args.filled_dir, args.static_dir, args.output_dir)
