import cv2
import numpy as np
from pdf2image import convert_from_path
import os
from pathlib import Path
import json
import pdfplumber

class VisualPDFScanner:
    def __init__(self, pdf_path, output_dir="debug_output"):
        self.pdf_path = Path(pdf_path)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self.pages = []  # List of images
        self.pdf_text_data = {} # page_num -> list of word dicts
        self.detected_fields = [] # Final results with labels

    def pdf_to_images(self, dpi=300):
        """Convert PDF to a list of numpy arrays (images)."""
        print(f"📄 Converting PDF to images (DPI={dpi})...")
        pil_images = convert_from_path(str(self.pdf_path), dpi=dpi)
        self.pages = [np.array(img) for img in pil_images]
        print(f"   Converted {len(self.pages)} pages.")

    def extract_text_data(self):
        """Use pdfplumber to extract robust text coordinates."""
        print("📖 Extracting text layer via pdfplumber...")
        with pdfplumber.open(self.pdf_path) as pdf:
            for i, page in enumerate(pdf.pages):
                words = page.extract_words(x_tolerance=2, y_tolerance=2, keep_blank_chars=False)
                # Normalize keys for consistency
                # pdfplumber returns: x0, top, x1, bottom, text
                self.pdf_text_data[i] = words
                # Store page dimensions for scaling
                self.pdf_text_data[i] = {
                    "words": words,
                    "width": float(page.width),
                    "height": float(page.height)
                }
        print(f"   Extracted text from {len(self.pdf_text_data)} pages.")

    def _map_rect_to_pdf(self, rect_px, page_num, img_shape):
        """Convert pixel coordinates to PDF points."""
        # rect_px is (x, y, w, h) in pixels
        # img_shape is (height, width, channels)
        
        pdf_dims = self.pdf_text_data.get(page_num)
        if not pdf_dims: return None
        
        pdf_w = pdf_dims["width"]
        pdf_h = pdf_dims["height"]
        img_h, img_w = img_shape[:2]
        
        scale_x = pdf_w / img_w
        scale_y = pdf_h / img_h
        
        x_px, y_px, w_px, h_px = rect_px
        
        # Calculate PDF coordinates
        x0 = x_px * scale_x
        top = y_px * scale_y
        x1 = (x_px + w_px) * scale_x
        bottom = (y_px + h_px) * scale_y
        
        return {
            "x0": x0, "top": top, 
            "x1": x1, "bottom": bottom,
            "width": x1-x0, "height": bottom-top
        }

    def _find_best_label(self, field_rect, page_num):
        """
        Find the nearest text label to the LEFT or ABOVE the field.
        """
        page_data = self.pdf_text_data.get(page_num)
        if not page_data: return "Unknown_Field"
        
        words = page_data["words"]
        fx0, ftop = field_rect["x0"], field_rect["top"]
        fx1, fbottom = field_rect["x1"], field_rect["bottom"]
        
        candidates = []
        
        for w in words:
            wx0, wtop, wx1, wbottom = float(w['x0']), float(w['top']), float(w['x1']), float(w['bottom'])
            w_text = w['text']
            
            # Distance Metrics
            # 1. Horizontal Distance (Left of field)
            is_same_row = (abs(wbottom - fbottom) < 5) or (abs(wtop - ftop) < 5)
            dist_x = fx0 - wx1
            
            # 2. Vertical Distance (Above field)
            is_above = (wbottom <= ftop + 5) and (wx0 < fx1) and (wx1 > fx0 - 20)
            dist_y = ftop - wbottom
            
            # CANDIDATE 1: Same row, to the left (Standard Label: Field)
            if is_same_row and 0 < dist_x < 150:
                candidates.append({
                    "text": w_text,
                    "score": dist_x, # Lower distance is better
                    "type": "beside"
                })
            
            # CANDIDATE 2: Directly above (Stacked Label)
            elif is_above and 0 < dist_y < 30:
                 candidates.append({
                    "text": w_text,
                    "score": dist_y * 1.5, # Penalty for vertical to prefer beside if close
                    "type": "above"
                })

        if not candidates:
            return "Unknown_Field"
            
        # Sort by lowest score (distance)
        candidates.sort(key=lambda x: x["score"])
        best = candidates[0]
        
        # If possible, try to grab multiple words if they look like a phrase
        # simplified for prototype
        return best["text"]

    def detect_lines_and_boxes(self, page_img, page_num):
        """Core CV logic matching detected visual elements."""
        # Preprocessing & Morphological (Same as before)
        gray = cv2.cvtColor(page_img, cv2.COLOR_RGB2GRAY)
        _, binary = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY_INV)

        h_kernel_len = np.array(page_img).shape[1] // 40
        horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (h_kernel_len, 1))
        detect_horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN, horizontal_kernel)
        
        v_kernel_len = np.array(page_img).shape[0] // 60
        vertical_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, v_kernel_len))
        detect_vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, vertical_kernel)
        
        table_mask = cv2.addWeighted(detect_horizontal, 0.5, detect_vertical, 0.5, 0)
        _, table_mask = cv2.threshold(table_mask, 50, 255, cv2.THRESH_BINARY)
        
        contours, _ = cv2.findContours(detect_horizontal, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        detected_items = []
        
        # Process Horizontal Lines
        for c in contours:
            x, y, w, h = cv2.boundingRect(c)
            if w > 50 and h < 20: 
                # Convert to PDF Coords immediately
                pdf_rect = self._map_rect_to_pdf((x,y,w,h), page_num, page_img.shape)
                if not pdf_rect: continue
                
                label = self._find_best_label(pdf_rect, page_num)
                
                item = {
                    "type": "text_field_line",
                    "rect_px": (x, y, w, h),
                    "rect_pdf": pdf_rect,
                    "label": label,
                    "page": page_num
                }
                detected_items.append(item)

        # Process Boxes
        box_contours, _ = cv2.findContours(table_mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        for c in box_contours:
            x, y, w, h = cv2.boundingRect(c)
            aspect_ratio = float(w)/h
            
            pdf_rect = self._map_rect_to_pdf((x, y, w, h), page_num, page_img.shape)
            if not pdf_rect: continue
            
            is_checkbox = (15 < w < 50 and 15 < h < 50 and 0.8 < aspect_ratio < 1.2)
            is_box = (w > 50 and h > 20)
            
            if is_checkbox or is_box:
                # Approximate polygon
                peri = cv2.arcLength(c, True)
                approx = cv2.approxPolyDP(c, 0.04 * peri, True)
                
                if len(approx) == 4 or is_checkbox:
                    label = self._find_best_label(pdf_rect, page_num)
                    item = {
                        "type": "checkbox" if is_checkbox else "box_field",
                        "rect_px": (x, y, w, h),
                        "rect_pdf": pdf_rect,
                        "label": label,
                        "page": page_num
                    }
                    detected_items.append(item)

        return detected_items

    def visualize_page(self, page_img, items, page_num):
        """Draw detections AND labels on the image."""
        debug_img = page_img.copy()
        for item in items:
            x, y, w, h = item['rect_px']
            label = item['label']
            
            color = (0, 0, 255) 
            if item['type'] == 'text_field_line': color = (255, 0, 0)
            elif item['type'] == 'checkbox': color = (0, 255, 0)
            elif item['type'] == 'box_field': color = (255, 165, 0)
            
            cv2.rectangle(debug_img, (x, y), (x+w, y+h), color, 2)
            
            # Draw Label (Small text)
            if label != "Unknown_Field":
                cv2.putText(debug_img, label[:15], (x, y-5), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0,0,0), 1)

        out_path = self.output_dir / f"page_{page_num+1}_hybrid_debug.png"
        cv2.imwrite(str(out_path), cv2.cvtColor(debug_img, cv2.COLOR_RGB2BGR))
        # print(f"   Saved debug view: {out_path}")

    def run(self):
        self.extract_text_data()
        self.pdf_to_images()
        
        all_detections = []
        for i, page_img in enumerate(self.pages):
            print(f"🔍 Analyzing Page {i+1}...")
            items = self.detect_lines_and_boxes(page_img, i)
            all_detections.extend(items)
            self.visualize_page(page_img, items, i)
            
            # Print sample detections
            found_labels = [x['label'] for x in items if x['label'] != 'Unknown_Field']
            if found_labels:
                print(f"   -> Found {len(items)} fields. Labels: {found_labels[:5]}...")
        
        print(f"✅ Scanning Complete. Found {len(all_detections)} elements.")

if __name__ == "__main__":
    TARGET_PDF = "/Users/36981/Desktop/PDFTest/PDFs to test/53252_DTE_EEA_Field_Inspection_Report_v11.pdf"
    scanner = VisualPDFScanner(TARGET_PDF, output_dir="visual_debug_hybrid")
    scanner.run()
