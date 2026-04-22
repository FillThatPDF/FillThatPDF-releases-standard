#!/usr/bin/env python3
"""
Smart Fillable PDF - AI Version (Avenue 3)
==========================================

Uses YOLOv11 to detect form fields, then maps them to text labels
and generates a fillable PDF.

Model: training_runs/fillthatpdf_yolo/weights/best.pt
"""

import sys
import os
import argparse
import cv2
import numpy as np
import cv2 # Visual Snapping
import numpy as np
from pathlib import Path
from collections import defaultdict
import pdfplumber
import pikepdf
from pdf2image import convert_from_path
from ultralytics import YOLO

# ==============================================================================
# COMPONENT 1: AI SCANNER ENGINE
# ==============================================================================

class AIScanner:
    def __init__(self, pdf_path, model_path, conf=0.15, imgsz=1280):
        self.pdf_path = Path(pdf_path)
        self.model_path = model_path
        self.conf = conf
        self.imgsz = imgsz
        self.pages = []  # List of images
        self.pdf_text_data = {} # page_num -> list of word dicts
        
        print(f"🧠 Loading AI Brain: {self.model_path}")
        self.model = YOLO(self.model_path)
        
    def pdf_to_images(self, dpi=200): # Match training DPI
        print(f"📄 Converting PDF to images (DPI={dpi})...")
        pil_images = convert_from_path(str(self.pdf_path), dpi=dpi)
        self.pages = [np.array(img) for img in pil_images]
        print(f"   Converted {len(self.pages)} pages.")

    def extract_text_data(self):
        print("📖 Extracting text layer for naming...")
        with pdfplumber.open(self.pdf_path) as pdf:
            for i, page in enumerate(pdf.pages):
                words = page.extract_words(x_tolerance=2, y_tolerance=2, keep_blank_chars=False)
                lines = page.lines
                self.pdf_text_data[i] = {
                    "words": words,
                    "lines": lines,
                    "width": float(page.width),
                    "height": float(page.height),
                    "cropbox": [float(x) for x in page.bbox] # [x0, y0, x1, y1] (Top-Left Origin)
                }
    
    def _find_best_label(self, field_rect, page_num):
        """Find nearest text label with improved scoring for colons/questions."""
        page_data = self.pdf_text_data.get(page_num)
        if not page_data: return "Field", 9999
        
        words = page_data["words"]
        fx0, fy0, fx1, fy1 = field_rect # [x0, bottom, x1, top]
        pdf_h = page_data["height"]
        ftop, fbottom = pdf_h - fy1, pdf_h - fy0
        
        candidates = []
        for w in words:
            wx0, wtop, wx1, wbottom = float(w['x0']), float(w['top']), float(w['x1']), float(w['bottom'])
            w_text = w['text'].strip()
            
            # 1. Horizontal Match (Beside)
            is_same_row = (abs(wbottom - fbottom) < 12) or (abs(wtop - ftop) < 12)
            dist_x = fx0 - wx1
            
            # 2. Vertical Match (Above)
            overlap_x = min(fx1, wx1) - max(fx0, wx0)
            is_above = (wbottom <= ftop + 15) and (overlap_x > -10)
            dist_y = ftop - wbottom
            
            score = 9999
            if is_same_row and 0 <= dist_x < 400:
                score = dist_x
            elif is_above and 0 <= dist_y < 50:
                 score = dist_y * 1.5 + 50 # Bias horizontal
            
            # 3. Interior Match (Inside the box) - NEW
            wcx = (wx0 + wx1) / 2
            wcy = (wbottom + wtop) / 2 # top-down word center
            
            ftop_td = pdf_h - fy1
            fbot_td = pdf_h - fy0
            
            # Liberal Interior Match: Add 15px padding to handle shift and large fonts
            if (fx0 - 15 <= wcx <= fx1 + 15) and (ftop_td - 15 <= wcy <= fbot_td + 15):
                 score = 0 # Perfect score for inside label
            else:
                 # Debug Print for specific page to diagnose alignment
                 if page_num == 44: # Zero-indexed Page 45
                     # Proximity Check (only print if somewhat close to avoid spam)
                     if abs(wcx - (fx0+fx1)/2) < 50 and abs(wcy - (ftop_td+fbot_td)/2) < 50:
                         print(f"   [Debug Lbl] '{w_text}' at ({wcx:.1f}, {wcy:.1f}) | Field ({fx0:.1f}, {ftop_td:.1f}, {fx1:.1f}, {fbot_td:.1f})")
            
            if score < 500:
                # Bonus for colons/questions (Strong Signal)
                if w_text.endswith(":") or w_text.endswith("?"):
                    score *= 0.3
                candidates.append({"text": w_text, "score": score})
        
        if not candidates: return "Field", 9999
        candidates.sort(key=lambda x: x["score"])
        return candidates[0]['text'], candidates[0]['score']
        
    def _detect_visual_lines(self, page_img):
        """
        Use Computer Vision (OpenCV) to find sharp table lines/grids.
        Returns: (h_lines, v_lines) -> lists of coordinates
        """
        # 1. Preprocess
        gray = cv2.cvtColor(page_img, cv2.COLOR_RGB2GRAY)
        
        # v7.8: Median Blur to remove sub-pixel artifact noise (the "Ghost Grid" fix)
        ksize = 3
        gray = cv2.medianBlur(gray, ksize)
        
        # Adaptive Threshold (Tighter offset to ignore faint background lines)
        binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 51, 25)
        
        # 3. Detect Lines
        # Reduced kernels to catch smaller table cells
        h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (30, 1)) 
        det_h = cv2.morphologyEx(binary, cv2.MORPH_OPEN, h_kernel)
        
        v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 15)) 
        det_v = cv2.morphologyEx(binary, cv2.MORPH_OPEN, v_kernel)
        
        # 4. Get Coordinates
        h_lines = []
        contours_h, _ = cv2.findContours(det_h, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours_h:
            x, y, w, h = cv2.boundingRect(c)
            # Center Y of the line
            line_y = y + h/2
            h_lines.append(line_y)
            
        v_lines = []
        contours_v, _ = cv2.findContours(det_v, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours_v:
            x, y, w, h = cv2.boundingRect(c)
            # Center X of the line
            line_x = x + w/2
            v_lines.append(line_x)
            
        return h_lines, v_lines, det_h, det_v

    def _snap_to_visual_lines(self, rect_px, h_lines, v_lines, page_h, page_w):
        """
        Snap pixel coordinates to the nearest visual line.
        rect_px: [x0, y0, x1, y1] (Top-Down Image Coords for easier CV matching)
        """
        x0, y0, x1, y1 = rect_px
        SNAP_DIST = 12 # Pixels (Liberal snap for fuzzy AI)
        
        # Snap Y (Horizontal alignments)
        best_y0, dist_y0 = y0, 999
        best_y1, dist_y1 = y1, 999
        
        for ly in h_lines:
            d0 = abs(ly - y0)
            d1 = abs(ly - y1)
            
            if d0 < SNAP_DIST and d0 < dist_y0:
                best_y0 = ly
                dist_y0 = d0
            if d1 < SNAP_DIST and d1 < dist_y1:
                best_y1 = ly
                dist_y1 = d1
                
        # Snap X (Vertical alignments - Columns)
        best_x0, dist_x0 = x0, 999
        best_x1, dist_x1 = x1, 999
        
        for lx in v_lines:
            d0 = abs(lx - x0)
            d1 = abs(lx - x1)
            
            if d0 < SNAP_DIST and d0 < dist_x0:
                best_x0 = lx
                dist_x0 = d0
            if d1 < SNAP_DIST and d1 < dist_x1:
                best_x1 = lx
                dist_x1 = d1
        
        # Apply snaps only if found
        final_x0 = best_x0 if dist_x0 < SNAP_DIST else x0
        final_y0 = best_y0 if dist_y0 < SNAP_DIST else y0
        final_x1 = best_x1 if dist_x1 < SNAP_DIST else x1
        final_y1 = best_y1 if dist_y1 < SNAP_DIST else y1
        
        return [final_x0, final_y0, final_x1, final_y1]

    def _box_contains_text(self, rect, page_num, threshold=0.15):
        """
        Check if a box already contains significant text.
        rect is [x0, y0, x1, y1] PDF coordinates.
        threshold: fraction of box area that can be overlapped by text.
        """
        x0, y0, x1, y1 = rect
        box_area = (x1 - x0) * (y1 - y0)
        if box_area <= 0: return False
        
        # Check words
        overlap_area = 0
        for w in self.pdf_text_data[page_num]['words']:
            wx0, wtop, wx1, wbot = float(w['x0']), float(w['top']), float(w['x1']), float(w['bottom'])
            # Convert word to bottom-up
            pdf_h = self.pdf_text_data[page_num]['height']
            wy0 = pdf_h - wbot
            wy1 = pdf_h - wtop
            
            # Intersection
            ix0 = max(x0, wx0)
            iy0 = max(y0, wy0)
            ix1 = min(x1, wx1)
            iy1 = min(y1, wy1)
            
            if ix1 > ix0 and iy1 > iy0:
                overlap_area += (ix1 - ix0) * (iy1 - iy0)
        
        return (overlap_area / box_area) > threshold
        
    def _snap_to_lines(self, rect_pdf, page_num):
        # ... (Old logical text snap - keeping as fallback or removing?)
        # Let's replace usage in main loop instead of deleting to be safe, 
        # or just let it sit unused.
        return rect_pdf
        
    def _calculate_iou(self, rect1, rect2):
        # rect: [x0, y0, x1, y1] (Bottom-Up format okay for IoU as long as consistent)
        x_left = max(rect1[0], rect2[0])
        y_top = max(rect1[1], rect2[1])
        x_right = min(rect1[2], rect2[2])
        y_bottom = min(rect1[3], rect2[3])
        
        if x_right < x_left or y_bottom < y_top:
            return 0.0
            
        intersection_area = (x_right - x_left) * (y_bottom - y_top)
        area1 = (rect1[2] - rect1[0]) * (rect1[3] - rect1[1])
        area2 = (rect2[2] - rect2[0]) * (rect2[3] - rect2[1])
        
        return intersection_area / float(area1 + area2 - intersection_area)

    def _nms(self, detections, iou_threshold=0.3):
        if not detections: return []
        sorted_dets = sorted(detections, key=lambda x: x['conf'], reverse=True)
        keep = []
        while sorted_dets:
            best = sorted_dets.pop(0)
            keep.append(best)
            remaining = []
            for det in sorted_dets:
                if self._calculate_iou(best['rect'], det['rect']) < iou_threshold:
                    remaining.append(det)
            sorted_dets = remaining
        return keep

    def _merge_horizontal(self, detections, gap_threshold=10):
        """Merge text fields that are horizontally close (for split lines)."""
        text_dets = [d for d in detections if d['type'] == 'text']
        other_dets = [d for d in detections if d['type'] != 'text']
        if not text_dets: return detections
        
        text_dets.sort(key=lambda x: (x['rect'][1], x['rect'][0]))
        merged = []
        while text_dets:
            curr = text_dets.pop(0)
            i = 0
            while i < len(text_dets):
                next_det = text_dets[i]
                y_overlap = min(curr['rect'][3], next_det['rect'][3]) - max(curr['rect'][1], next_det['rect'][1])
                curr_h = curr['rect'][3] - curr['rect'][1]
                next_h = next_det['rect'][3] - next_det['rect'][1]
                gap = next_det['rect'][0] - curr['rect'][2]
                if y_overlap > 0.5 * min(curr_h, next_h) and abs(gap) < gap_threshold:
                    curr['rect'] = [min(curr['rect'][0], next_det['rect'][0]), min(curr['rect'][1], next_det['rect'][1]),
                                   max(curr['rect'][2], next_det['rect'][2]), max(curr['rect'][3], next_det['rect'][3])]
                    curr['conf'] = max(curr['conf'], next_det['conf'])
                    text_dets.pop(i)
                else: i += 1
            merged.append(curr)
        return merged + other_dets

    def _generate_grid_candidates(self, det_h, det_v, page_w, page_h):
        """Generate field candidates from visual grid cells (holes in the grid)."""
        # Combine masks to form the grid skeleton
        # Dilate slightly to close small gaps
        kernel = np.ones((3,3), np.uint8)
        grid_mask = cv2.bitwise_or(det_h, det_v)
        grid_mask = cv2.dilate(grid_mask, kernel, iterations=1)
        
        # Invert to find cells (holes)
        # Note: We need a border around the page to close open cells?
        # Actually, finding contours on inverted mask
        inv_mask = cv2.bitwise_not(grid_mask)
        
        cnts, _ = cv2.findContours(inv_mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        candidates = []
        
        img_h, img_w = det_h.shape[:2]
        min_w = img_w * 0.02 # 2% of width
        min_h = img_h * 0.01
        
        for c in cnts:
            x, y, w, h = cv2.boundingRect(c)
            # Filter Logic:
            # 1. Must be big enough to be a field
            # 2. Must not be the entire page
            # 3. v7.8: Must not be HUGE vertically (fields are ~12pt, ghost grids are 500pt)
            # Max height: 45 pt (converted to pixels at 200 DPI: 45 * 200 / 72 = 125px)
            # Standard field height in pixels at 200 DPI: 12 * 200 / 72 = 33px.
            max_h_px = (45 * 200 / 72)
            
            if w > min_w and h > min_h and w < img_w * 0.9 and h < max_h_px:
                 # Convert Bottom-Up PDF coords?
                 # No, YOLO expects Normalized or Pixel Coords.
                 # Let's return Pixel Coords [x0, y0, x1, y1] (Top-Down)
                 candidates.append([x, y, x+w, y+h])
                 
        return candidates

    def scan(self):
        self.extract_text_data()
        self.pdf_to_images()
        all_detections = []
        classes = self.model.names
        print(f"   Model Classes: {classes}")
        debug_dir = Path("debug_ai_output")
        debug_dir.mkdir(exist_ok=True)
        
        for page_num, page_img in enumerate(self.pages):
            print(f"🔍 [Page {page_num+1}] AI Inference...")
            results = self.model.predict(page_img, conf=self.conf, imgsz=self.imgsz, verbose=False)
            result = results[0]
            
            pdf_dims = self.pdf_text_data.get(page_num)
            pdf_dims = self.pdf_text_data.get(page_num)
            pdf_w = pdf_dims["width"]; pdf_h = pdf_dims["height"]
            img_h, img_w = page_img.shape[:2]
            scale_x = pdf_w / img_w; scale_y = pdf_h / img_h
            
            # Visual Snapping Phase
            h_lines, v_lines, det_h, det_v = self._detect_visual_lines(page_img)
            grid_boxes = self._generate_grid_candidates(det_h, det_v, img_w, img_h)
            print(f"   [Grid] Generating {len(grid_boxes)} visual candidates...")

            # Create a combined list of raw dicts
            raw_detections = []
            
            # 1. Add AI Detections
            for r in results[0].boxes.data.tolist():
                x1, y1, x2, y2, conf, cls_id = r
                field_type = "text"
                class_name = classes[cls_id]
                if class_name == "Checkbox": field_type = "checkbox"
                elif class_name == "Radio Button": field_type = "radio"
                elif class_name == "Dropdown": field_type = "combo"

                if (x2-x1) < 6 or (y2-y1) < 6: continue # Relaxed filter

                raw_detections.append({
                    'rect': [x1, y1, x2, y2], # Pixel Coords Top-Left
                    'conf': conf,
                    'type': field_type,
                    'source': 'ai'
                })
                
            # 2. Add Visual Candidates
            for g in grid_boxes:
                # PROBE P13 Generation Check
                if page_num == 12:
                    gx0, gy0, gx1, gy1 = g
                    # Map to PDF approx [136, 645, 304, 657] (Bottom-Up)
                    # Point (220, 651) -> Pixel (220/scale_x, (pdf_h-651)/scale_y)
                    # approx (220*3, (792-651)*3) = (660, 423)
                    if 600 < (gx0+gx1)/2 < 720 and 400 < (gy0+gy1)/2 < 450:
                         print(f"   [PROBE P13 GEN] Found Grid Box at ({gx0}, {gy0}, {gx1}, {gy1})")
                
                raw_detections.append({
                    'rect': [float(g[0]), float(g[1]), float(g[2]), float(g[3])],
                    'conf': 0.35, # Medium Confidence
                    'type': 'text',
                    'source': 'visual'
                })
                
            # Process Detections
            final_page_dets = []
            for d in raw_detections:
                x1, y1, x2, y2 = d['rect']
                
                # ... existing coordinate transformation ...
                # Needs to match loop below
            debug_img = cv2.cvtColor(page_img.copy(), cv2.COLOR_RGB2BGR)
            
            # DEBUG: Draw Words (Red)
            page_data = self.pdf_text_data.get(page_num)
            if page_data:
                pdf_w, pdf_h = page_data["width"], page_data["height"]
                scale_x = img_w / pdf_w
                scale_y = img_h / pdf_h
                for w in page_data["words"]:
                    wx0, wtop, wx1, wbottom = float(w['x0']), float(w['top']), float(w['x1']), float(w['bottom'])
                    # pdfplumber is Top-Down, Image is Top-Down. Easy.
                    dpx0 = int(wx0 * scale_x)
                    dpy0 = int(wtop * scale_y)
                    dpx1 = int(wx1 * scale_x)
                    dpy1 = int(wbottom * scale_y)
                    cv2.rectangle(debug_img, (dpx0, dpy0), (dpx1, dpy1), (0, 0, 255), 1)

            # DEBUG: Draw Lines
            for ly in h_lines:
                cv2.line(debug_img, (0, int(ly)), (img_w, int(ly)), (255, 0, 0), 1) # Blue H-Lines
            for lx in v_lines:
                cv2.line(debug_img, (int(lx), 0), (int(lx), img_h), (255, 0, 0), 1) # Blue V-Lines
            for gb in grid_boxes:
                # gb is [x, y, x+w, y+h] Top-Down
                cv2.rectangle(debug_img, (int(gb[0]), int(gb[1])), (int(gb[2]), int(gb[3])), (255, 255, 0), 2) # Cyan Boxes
            
            # 1. NMS (IoU)
            page_dets = raw_detections 
            raw_count = len(page_dets)
            page_dets = self._nms(page_dets, iou_threshold=0.4)
            # 2. Merge Horizontal
            page_dets = self._merge_horizontal(page_dets, gap_threshold=15)
            print(f"   Reduced {raw_count} -> {len(page_dets)} fields.")
            
            final_page_dets = []
            final_page_dets = []
            
            # CALIBRATION: Systematic shift found via verify_accuracy.py
            # Residual dx=-2.70, dy=2.46 -> Adjusted values:
            dx, dy = 1.85, 0.44

            for d in page_dets:
                # 3. Hybrid Snap: Snap Pixels BEFORE transforming to PDF Coords
                # d['rect'] is [x1, y1, x2, y2] Image Pixels (Top-Down)
                
                # SNAP (in Image Pixels)
                px0, py0, px1, py1 = d['rect']
                snapped_px = self._snap_to_visual_lines([px0, py0, px1, py1], h_lines, v_lines, img_h, img_w)
                
                # Transform Snapped Pixels -> PDF Points (Bottom-Up)
                pdf_dims = self.pdf_text_data[page_num]
                # Use EXACT DPI-based scale (standard 0.36 for 200 DPI)
                cur_scale = 72.0 / 200.0 
                
                # Apply Calibration ONLY to AI detections
                curr_dx, curr_dy = (dx, dy) if d.get('source') == 'ai' else (0, 0)
                
                final_x0 = (snapped_px[0] * cur_scale) + curr_dx
                final_rect_y1 = (pdf_dims["height"] - (snapped_px[1] * cur_scale)) + curr_dy
                final_x1 = (snapped_px[2] * cur_scale) + curr_dx
                final_rect_y0 = (pdf_dims["height"] - (snapped_px[3] * cur_scale)) + curr_dy
                
                # PROBE: Page 13 Check
                # Target: [135.9, 645.3, 303.9, 657.3] (Bottom-Up)
                if page_num == 12:
                     cx = (final_x0 + final_x1) / 2
                     cy = (final_rect_y0 + final_rect_y1) / 2
                     # Target Center approx (220, 651)
                     if 200 < cx < 240 and 640 < cy < 660:
                         print(f"   [PROBE P13] Found Candidate near target! Rect: ({final_x0:.1f}, {final_rect_y0:.1f}, {final_x1:.1f}, {final_rect_y1:.1f}) Src:{d.get('source')}")
                
                # Update Rect to PDF Coords (Bottom-Up)
                d['rect'] = [final_x0, final_rect_y0, final_x1, final_rect_y1]
                
                # Update Pixel Rect for Debug Drawing matches PDF (Image Pixels)
                d['pixel_rect'] = snapped_px
                d['page'] = page_num
                
                label, score = self._find_best_label(d['rect'], page_num)
                d['label'] = label
                d['label_score'] = score # Store score for later filtering
            
            # CLEANUP & REFINEMENT (v7.7 - Page-Self-Calibration)
            print(f"   Refining {len(page_dets)} fields...")
            
            # Step 1: Find Page-Specific Median Shift
            shifts_y = []
            for d in page_dets:
                label = d.get('label')
                score = d.get('label_score', 9999)
                if score < 30: # Only use "Inside" or "Super Close" labels as anchors
                    # Find label center (PDF)
                    found = False
                    for w in self.pdf_text_data[page_num]['words']:
                        if w['text'] == label:
                            lx0, ltop, lx1, lbot = w['x0'], w['top'], w['x1'], w['bottom']
                            pdf_h = self.pdf_text_data[page_num]['height']
                            ly_center = pdf_h - ((ltop + lbot) / 2) # Bottom-Up center
                            fy_center = (d['rect'][1] + d['rect'][3]) / 2 # Current center
                            shifts_y.append(ly_center - fy_center)
                            found = True
                            break
            
            # Use Median to avoid outliers
            page_shift_y = np.median(shifts_y) if shifts_y else 0
            if page_shift_y != 0:
                 print(f"      [CALIB] Page {page_num+1} applied median Shift_Y: {page_shift_y:.2f}pt (Anchors: {len(shifts_y)})")

            final_page_dets = []
            for d in page_dets:
                # Apply Page-Specific Shift
                d['rect'][1] += page_shift_y
                d['rect'][3] += page_shift_y

                label = d.get('label', 'Field')
                score = d.get('label_score', 9999)
                is_checkbox = d.get('type') in ['checkbox', 'radio']
                
                # REJECT UNLABELLED VISUAL CANDIDATES (Noise Reduction)
                if d.get('source') == 'visual' and score > 200: # Slightly more liberal for shifted boxes
                    continue
                
                # REJECT HEADERS (Restored Overlap Filter)
                # Ensure we check OVERLAP AFTER SHIFTING
                if self._box_contains_text(d['rect'], page_num, threshold=0.4):
                     continue

                if d['conf'] > 0.5:
                    keep = True
                elif d['conf'] > 0.20:
                    keep = (score < 200) or is_checkbox 
                else:
                    keep = is_checkbox and (score < 50)
                
                if keep:
                    final_page_dets.append(d)
                    
            print(f"   Final Clean: {len(final_page_dets)} fields.")
            page_dets = final_page_dets

            for d in page_dets:
                all_detections.append(d)
                x1, y1, x2, y2 = d['pixel_rect']
                cv2.rectangle(debug_img, (int(x1), int(y1)), (int(x2), int(y2)), (0, 255, 0), 2)
                cv2.putText(debug_img, f"{d['label']}", (int(x1), int(y1)-5), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0,255,0), 1)
            
            if page_num < 100:
                out_path = debug_dir / f"page_{page_num+1}.jpg"
                try:
                    cv2.imwrite(str(out_path), debug_img)
                    print(f"      📸 Saved Debug Image: {out_path.absolute()}")
                except Exception as e:
                    print(f"      ❌ Failed to save debug image: {e}")
                
        print(f"✅ AI Scan Complete. Found {len(all_detections)} fields.")
        
        # Save for Grid Search
        import json
        with open("debug_detections.json", "w") as f:
            json.dump(all_detections, f, indent=2)
            
        return all_detections

# ==============================================================================
# COMPONENT 2: PDF GENERATOR (Reused)
# ==============================================================================

class PDFGenerator:
    def __init__(self, input_path, output_path):
        self.input_path = input_path
        self.output_path = output_path
        self.column_counters = defaultdict(int)

    def _generate_unique_name(self, label, page_num):
        clean_label = "".join(c for c in label if c.isalnum() or c in " _-")
        clean_label = clean_label.strip().replace(" ", "_")
        if not clean_label: clean_label = "Field"
        
        self.column_counters[clean_label] += 1
        count = self.column_counters[clean_label]
        return f"{clean_label}_{count}" if count > 1 else clean_label

    def create(self, fields):
        print(f"\n💾 Generating Fillable PDF: {self.output_path}")
        
        with pikepdf.open(self.input_path) as pdf:
            if '/AcroForm' not in pdf.Root:
                pdf.Root.AcroForm = pikepdf.Dictionary({
                    '/Fields': pikepdf.Array([]),
                    '/NeedAppearances': True
                })
            acroform = pdf.Root.AcroForm
            acroform.NeedAppearances = True
            acroform.Fields = pikepdf.Array([]) # Clear existing

            for f in fields:
                page_num = f['page']
                page = pdf.pages[page_num]
                name = self._generate_unique_name(f['label'], page_num)
                rect = f['rect']
                
                widget_dict = {
                    '/Type': pikepdf.Name('/Annot'),
                    '/Subtype': pikepdf.Name('/Widget'),
                    '/T': pikepdf.String(name),
                    '/Rect': pikepdf.Array(rect),
                    '/MK': pikepdf.Dictionary({
                        '/BC': pikepdf.Array([0, 0, 0]),
                        '/BG': pikepdf.Array([1, 1, 1]),
                    })
                }
                
                if f['type'] == 'checkbox':
                    widget_dict['/FT'] = pikepdf.Name('/Btn')
                    widget_dict['/Ff'] = 0
                    widget_dict['/V'] = pikepdf.Name('/Off')
                    # widget_dict['/AS'] = pikepdf.Name('/Off')
                    widget_dict['/DA'] = pikepdf.String("/ZapfDingbats 0 Tf 0 g")
                elif f['type'] == 'radio':
                    widget_dict['/FT'] = pikepdf.Name('/Btn')
                    widget_dict['/Ff'] = 32768 # Radio flag
                    widget_dict['/DA'] = pikepdf.String("/ZapfDingbats 0 Tf 0 g")
                elif f['type'] == 'combo':
                     widget_dict['/FT'] = pikepdf.Name('/Ch')
                     widget_dict['/Ff'] = 131072 # Combo flag
                     widget_dict['/DA'] = pikepdf.String("/Helv 10 Tf 0 g")
                else:
                    widget_dict['/FT'] = pikepdf.Name('/Tx')
                    widget_dict['/DA'] = pikepdf.String("/Helv 10 Tf 0 g")
                
                annot = pdf.make_indirect(pikepdf.Dictionary(widget_dict))
                if '/Annots' not in page:
                    page.Annots = pdf.make_indirect(pikepdf.Array())
                page.Annots.append(annot)
                acroform.Fields.append(annot)

            pdf.save(self.output_path)

def main():
    parser = argparse.ArgumentParser(description="AI Fillable PDF Scanner")
    parser.add_argument("input_pdf", help="Path to input PDF")
    parser.add_argument("model_path", help="Path to .pt model file")
    parser.add_argument("--conf", type=float, default=0.01, help="Confidence Threshold (default 0.01)")
    parser.add_argument("--imgsz", type=int, default=1280, help="Inference Image Size (default 1280)")
    args = parser.parse_args()
    
    output_path = Path(args.input_pdf).parent / f"{Path(args.input_pdf).stem}_AI_fillable.pdf"
    
    scanner = AIScanner(args.input_pdf, args.model_path, conf=args.conf, imgsz=args.imgsz)
    fields = scanner.scan()
    
    generator = PDFGenerator(args.input_pdf, str(output_path))
    generator.create(fields)

if __name__ == "__main__":
    main()
