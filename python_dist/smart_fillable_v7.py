#!/usr/bin/env python3
"""
Smart Fillable PDF v7: The Visual-First Engine
==============================================

Replaces legacy heuristics with Computer Vision (OpenCV) + Hybrid Text Mapping.
- Detects structure by "looking" at the page (VisualPDFScanner).
- Maps labels by reading the text layer (pdfplumber).
- Generates robust fillable PDFs (pikepdf).

Author: Antigravity Agent
Date: January 2026
"""

import sys
import os
import argparse
import cv2
import numpy as np
from pathlib import Path
from collections import defaultdict
import pdfplumber
import pikepdf
from pdf2image import convert_from_path

# ==============================================================================
# COMPONENT 1: VISUAL SCANNER ENGINE
# ==============================================================================

class VisualPDFScanner:
    """
    Uses Computer Vision to detecting horizontal lines and boxes, 
    then maps them to PDF text labels.
    """
    def __init__(self, pdf_path):
        self.pdf_path = Path(pdf_path)
        self.pages = []  # List of images
        self.pdf_text_data = {} # page_num -> list of word dicts
        
    def pdf_to_images(self, dpi=300):
        print(f"📄 Converting PDF to images (DPI={dpi})...")
        pil_images = convert_from_path(str(self.pdf_path), dpi=dpi)
        self.pages = [np.array(img) for img in pil_images]
        print(f"   Converted {len(self.pages)} pages.")

    def extract_text_data(self):
        print("📖 Extracting text layer via pdfplumber...")
        with pdfplumber.open(self.pdf_path) as pdf:
            for i, page in enumerate(pdf.pages):
                words = page.extract_words(x_tolerance=2, y_tolerance=2, keep_blank_chars=False)
                self.pdf_text_data[i] = {
                    "words": words,
                    "width": float(page.width),
                    "height": float(page.height)
                }
        print(f"   Extracted text from {len(self.pdf_text_data)} pages.")

    def _map_rect_to_pdf(self, rect_px, page_num, img_shape):
        """Convert pixel coordinates to PDF points."""
        pdf_dims = self.pdf_text_data.get(page_num)
        if not pdf_dims: return None
        
        pdf_w = pdf_dims["width"]
        pdf_h = pdf_dims["height"]
        img_h, img_w = img_shape[:2]
        
        scale_x = pdf_w / img_w
        scale_y = pdf_h / img_h
        
        x_px, y_px, w_px, h_px = rect_px
        
        x0 = x_px * scale_x
        top = y_px * scale_y
        x1 = (x_px + w_px) * scale_x
        bottom = (y_px + h_px) * scale_y
        
        # Invert Y for PDF coordinate system (Bottom-Left origin) vs Image (Top-Left)
        # pdfplumber 'top/bottom' are from top, but PDF raw coords usually need bottom-up.
        # However, pikepdf usually expects /Rect [x0 y0 x1 y1] where y0 is bottom.
        # pdfplumber 'bottom' is y-distance from top.
        # PDF MediaBox height is needed.
        
        # CORRECT COORDINATE TRANSFORM FOR PIKEPDF (UserSpace):
        # PDF Y = PageHeight - ImageY
        
        rect_y0 = pdf_h - bottom
        rect_y1 = pdf_h - top
        
        return [x0, rect_y0, x1, rect_y1]

    def _find_best_label(self, field_rect, page_num):
        """Find nearest text label."""
        page_data = self.pdf_text_data.get(page_num)
        if not page_data: return "Field"
        
        words = page_data["words"]
        # PDF Rect is [x0, bottom, x1, top]
        fx0, fy0, fx1, fy1 = field_rect
        
        # Convert back to Top-Down for text matching (pdfplumber style)
        pdf_h = page_data["height"]
        ftop = pdf_h - fy1
        fbottom = pdf_h - fy0
        
        candidates = []
        for w in words:
            wx0, wtop, wx1, wbottom = float(w['x0']), float(w['top']), float(w['x1']), float(w['bottom'])
            w_text = w['text']
            
            # 1. Beside (Left)
            is_same_row = (abs(wbottom - fbottom) < 10) or (abs(wtop - ftop) < 10)
            dist_x = fx0 - wx1
            
            # 2. Above (Column Header)
            # Relaxed x-overlap check (allow slight misalignment)
            overlap_x = min(fx1, wx1) - max(fx0, wx0)
            is_above = (wbottom <= ftop + 20) and (overlap_x > 0)
            dist_y = ftop - wbottom
            
            if is_same_row and 0 < dist_x < 300:
                candidates.append({"text": w_text, "score": dist_x, "type": "beside"})
            elif is_above and 0 < dist_y < 1000: # Look far up for column headers
                 # Vertical distance penalty is low to encourage finding headers
                 candidates.append({"text": w_text, "score": dist_y * 0.5, "type": "above"})

        if not candidates: return "Field"
        candidates.sort(key=lambda x: x["score"])
        
        # Simple phrase joining: Grab previous 2 words if they are close
        best_candidate = candidates[0]
        best_text = best_candidate['text']
        
        # TODO: Full phrase reconstruction (omitted for speed, basic word is usually sufficient)
        # If the best match is a "beside" label, it's likely "Label:", so we take it.
        # If it's a "above" label, it's likely a column header.
        
        return best_text

    def _refine_field_collision(self, field_rect, page_num):
        """
        Check if text exists INSIDE the detected field box.
        If yes, shrink the field to exclude the text. 
        Solves the 'Covered Everything' issue.
        """
        page_data = self.pdf_text_data.get(page_num)
        if not page_data: return field_rect
        
        words = page_data["words"]
        fx0, fy0, fx1, fy1 = field_rect  # PDF Coords (x0, top, x1, bottom) - Wait, internal format is different
        
        # NOTE: _map_rect_to_pdf returns [x0, bottom_pdf, x1, top_pdf] for pikepdf
        # Let's normalize to pdfplumber TOP-DOWN for comparison
        pdf_h = page_data["height"]
        
        # Valid PDF rect is [x0, y0, x1, y1] where y0 < y1 usually (pikepdf)
        # But y0 is bottom-left relative.
        # Let's convert back to pdfplumber (Top-Left 0,0)
        
        # Input 'field_rect' is [x0, y0, x1, y1] from _map_rect_to_pdf
        # y0 is distance from bottom. y1 is distance from bottom.
        
        r_x0, r_y0, r_x1, r_y1 = field_rect
        r_bottom = pdf_h - r_y0 # Convert back to top-down distance
        r_top = pdf_h - r_y1
        
        # Check for intersection
        collisions = []
        for w in words:
            # Word Box
            wx0, wtop, wx1, wbottom = float(w['x0']), float(w['top']), float(w['x1']), float(w['bottom'])
            
            # Intersection Check
            x_overlap = max(0, min(r_x1, wx1) - max(r_x0, wx0))
            y_overlap = max(0, min(r_bottom, wbottom) - max(r_top, wtop))
            
            if x_overlap > 0 and y_overlap > 0:
                collisions.append(w)
        
        if not collisions:
            return field_rect
            
        # If collisions found, assume it's a label on the LEFT or TOP side.
        # Find the right-most edge of the text to chop off
        max_text_x1 = 0
        for w in collisions:
             max_text_x1 = max(max_text_x1, float(w['x1']))
             
        # Heuristic: If text takes up > 90% of the box, it's probably not a field, maybe just a text box.
        # But if it takes up 30-50%, it's likely "Label: [ Empty Space ]"
        
        if max_text_x1 > r_x0 and max_text_x1 < r_x1:
            print(f"      ✂️  Clipping field collision: {int(r_x0)} -> {int(max_text_x1 + 5)}")
            # New x0 is after the text + padding
            new_px_x0 = max_text_x1 + 5
            
            # Return new rect in PDF coords
            return [new_px_x0, r_y0, r_x1, r_y1]
            
        return field_rect
        return field_rect

    def _box_contains_text(self, rect, page_num):
        """Returns True if the box contains significant text (likely a header/label container)."""
        page_data = self.pdf_text_data.get(page_num)
        if not page_data: return False
        
        words = page_data["words"]
        r_x0, r_y0, r_x1, r_y1 = rect
        
        # Convert to Top-Down for comparison with pdfplumber words
        pdf_h = page_data["height"]
        r_bottom = pdf_h - r_y0 
        r_top = pdf_h - r_y1
        
        # Count chars inside
        chars_inside = 0
        for w in words:
            wx0, wtop, wx1, wbottom = float(w['x0']), float(w['top']), float(w['x1']), float(w['bottom'])
            
            # Check if word overlaps significantly (> 50% of word area)
            x_overlap = max(0, min(r_x1, wx1) - max(r_x0, wx0))
            y_overlap = max(0, min(r_bottom, wbottom) - max(r_top, wtop))
            
            overlap_area = x_overlap * y_overlap
            word_area = (wx1 - wx0) * (wbottom - wtop)
            
            if word_area > 0 and (overlap_area / word_area) > 0.5:
                 chars_inside += len(w['text'])
                
        # If box has more than 3 chars overlapping, it's likely a label/header box.
        if chars_inside > 3:
             return True
        return False
    def scan(self):
        """Run the full scan and return list of detected fields."""
        self.extract_text_data()
        self.pdf_to_images()
        
        all_detections = []
        
        for page_num, page_img in enumerate(self.pages):
            print(f"🔍 [Page {page_num+1}] Visual Scan...")
            
            # DEBUG: Dump Page 1 Words - REMOVED for Production
            # if page_num == 0: ...
            
            # CV Logic (Tuned for Sensitivity)
            gray = cv2.cvtColor(page_img, cv2.COLOR_RGB2GRAY)
            
            # Use Adaptive Threshold for better shadow/background handling
            # BlockSize 51, C=10. Inverted so lines are White.
            binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 51, 10)

            # Tuned Kernels (Fixed sizes to ensure small checkboxes are caught)
            # Horizontal: 40px (Kill text, keep lines)
            h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (40, 1))
            det_h = cv2.morphologyEx(binary, cv2.MORPH_OPEN, h_kernel)
            
            # Vertical: 10px (Small enough to keep checkboxes)
            # Note: This might pick up some text fragments, but filtering by size later helps.
            v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 10))
            det_v = cv2.morphologyEx(binary, cv2.MORPH_OPEN, v_kernel)
            
            table_mask = cv2.addWeighted(det_h, 0.5, det_v, 0.5, 0)
            _, table_mask = cv2.threshold(table_mask, 50, 255, cv2.THRESH_BINARY)
            
            # 1. Text Fields (Lines)
            contours, _ = cv2.findContours(det_h, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for c in contours:
                x, y, w, h = cv2.boundingRect(c)
                if w > 50 and h < 20: 
                    rect_pdf = self._map_rect_to_pdf((x,y,w,h), page_num, page_img.shape)
                    if rect_pdf:
                        # 0. Filter: Strict Header Rejection
                        # If the raw detection overlaps text significantly, reject it.
                        if self._box_contains_text(rect_pdf, page_num):
                             print(f"      🗑️  Skipping Text Overlap Field (Pre-Refine): {rect_pdf}")
                             continue

                        # 1. Refine Collision
                        rect_pdf = self._refine_field_collision(rect_pdf, page_num)
                        
                        label = self._find_best_label(rect_pdf, page_num)
                        
                        # Smart Typing: If label implies checkbox AND field is small, force it.
                        clean_label = label.lower()
                        is_boolean = any(k in clean_label for k in ["yes", "no", "pass", "fail", "n/a", "check", "verify"])
                        
                        # Only apply Smart Typing if width is small (< 40)
                        # Text fields are usually wider.
                        w_pdf = rect_pdf[2] - rect_pdf[0]
                        if is_boolean and w_pdf < 40:
                             field_type = "checkbox"
                        else:
                             field_type = "text"
                        
                        all_detections.append({
                            "type": field_type, "rect": rect_pdf, "label": label, "page": page_num
                        })

            # 2. Checkboxes & Boxes
            box_contours, _ = cv2.findContours(table_mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
            for c in box_contours:
                x, y, w, h = cv2.boundingRect(c)
                aspect = float(w)/h
                rect_pdf = self._map_rect_to_pdf((x,y,w,h), page_num, page_img.shape)
                if not rect_pdf: continue
                
                # Checkbox: Width 10-30px (Strict Tiny Squares)
                # Was 50px, but user says "small tiny squares".
                is_cb = (10 < w < 35 and 10 < h < 35 and 0.8 < aspect < 1.2)
                is_box = (w > 35 and h > 20)
                
                if is_cb:
                    label = self._find_best_label(rect_pdf, page_num)
                    all_detections.append({
                        "type": "checkbox", "rect": rect_pdf, "label": label, "page": page_num
                    })
                elif is_box:
                    # Validate box shape
                    peri = cv2.arcLength(c, True)
                    approx = cv2.approxPolyDP(c, 0.04 * peri, True)
                    
                    # RELAXED: Accept if 4 corners OR if very large box (likely image container)
                    is_large = (w > 100 and h > 100)
                    
                    if len(approx) == 4 or is_large:
                         # 0. Filter: Strict Header Rejection (unless large image box)
                        if not is_large and self._box_contains_text(rect_pdf, page_num):
                            print(f"      🗑️  Skipping Text Container Box (Header?): {rect_pdf}")
                            continue

                        # Refine Collision
                        rect_pdf = self._refine_field_collision(rect_pdf, page_num)

                        label = self._find_best_label(rect_pdf, page_num)
                        
                        # Smart Typing for boxes too
                        clean_label = label.lower()
                        is_boolean = any(k in clean_label for k in ["yes", "no", "pass", "fail", "n/a", "check", "verify"])
                        
                        # Only apply Smart Typing if box is small-ish (< 40)
                        # Giant cells should stay Text.
                        w_pdf = rect_pdf[2] - rect_pdf[0]
                        if is_boolean and w_pdf < 40:
                            field_type = "checkbox"
                        else:
                            field_type = "text"
                        
                        all_detections.append({
                            "type": field_type, "rect": rect_pdf, "label": label, "page": page_num
                        })
                        
        print(f"✅ Scan Complete. Total Fields: {len(all_detections)}")
        print(f"   - Checkboxes: {sum(1 for f in all_detections if f['type']=='checkbox')}")
        print(f"   - Text Fields: {sum(1 for f in all_detections if f['type']=='text')}")
        return all_detections


# ==============================================================================
# COMPONENT 2: PDF GENERATOR
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
            # Initialize AcroForm
            if '/AcroForm' not in pdf.Root:
                pdf.Root.AcroForm = pikepdf.Dictionary({
                    '/Fields': pikepdf.Array([]),
                    '/NeedAppearances': True
                })
            acroform = pdf.Root.AcroForm
            acroform.NeedAppearances = True
            
            # Clear existing to prevent dupes
            acroform.Fields = pikepdf.Array([])

            count = 0
            for f in fields:
                page_num = f['page']
                page = pdf.pages[page_num]
                
                # Transform Name
                name = self._generate_unique_name(f['label'], page_num)
                rect = f['rect'] # [x0, y0, x1, y1]
                
                # Create Widget
                widget_dict = {
                    '/Type': pikepdf.Name('/Annot'),
                    '/Subtype': pikepdf.Name('/Widget'),
                    '/T': pikepdf.String(name),
                    '/Rect': pikepdf.Array(rect),
                    # Appearance Characteristics (Border, Color)
                    '/MK': pikepdf.Dictionary({
                        '/BC': pikepdf.Array([0, 0, 0]),     # Black Border
                        '/BG': pikepdf.Array([1, 1, 1]),     # White Background
                    })
                }
                
                if f['type'] == 'checkbox':
                    widget_dict['/FT'] = pikepdf.Name('/Btn')
                    widget_dict['/Ff'] = 0  # No flags for now
                    widget_dict['/V'] = pikepdf.Name('/Off')
                    widget_dict['/AS'] = pikepdf.Name('/Off') # Current state
                    
                    # Appearance Stream (Crucial for Checkboxes)
                    # We need a valid /AP dict.
                    # Simplified: Just define N/Off and N/Yes
                    # Note: Actually constructing valid AP streams manually is hard.
                    # Best approach: Let Acrobat/Preview auto-generate by setting NeedAppearances=True (which we did).
                    # BUT, 'Giant Checkmark' suggests we might be setting a weird font size in DA?
                    # Remove Manual AP if NeedAppearances is True?
                    # Let's try supplying a minimal AP to prevent giant scaling.
                    
                    # Standard Checkbox AP usually uses ZapfDingbats.
                    widget_dict['/DA'] = pikepdf.String("/ZapfDingbats 0 Tf 0 g")
                    
                else:
                    widget_dict['/FT'] = pikepdf.Name('/Tx')
                    widget_dict['/DA'] = pikepdf.String("/Helv 10 Tf 0 g") # Default font
                    
                    # Image Box check
                    w = rect[2] - rect[0]
                    h = rect[3] - rect[1]
                    if w > 100 and h > 100:
                        widget_dict['/T'] = pikepdf.String(f"Image_{name}")
                
                widget = pikepdf.Dictionary(widget_dict)
                
                # Add to Page & AcroForm
                annot = pdf.make_indirect(widget)
                if '/Annots' not in page:
                    page.Annots = pdf.make_indirect(pikepdf.Array())
                page.Annots.append(annot)
                acroform.Fields.append(annot)
                count += 1

            pdf.save(self.output_path)
            print(f"✅ Saved {count} fields to {self.output_path}")

# ==============================================================================
# MAIN ENTRY POINT
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(description="Smart Fillable PDF v7 (Visual-First)")
    parser.add_argument("input_pdf", help="Path to input PDF")
    parser.add_argument("output_pdf", nargs="?", help="Path to output PDF")
    args = parser.parse_args()

    input_path = Path(args.input_pdf)
    if not args.output_pdf:
        output_path = input_path.parent / f"{input_path.stem}_fillable.pdf"
    else:
        output_path = Path(args.output_pdf)

    # 1. Scan
    scanner = VisualPDFScanner(str(input_path))
    fields = scanner.scan()

    # 2. Generate
    generator = PDFGenerator(str(input_path), str(output_path))
    generator.create(fields)

if __name__ == "__main__":
    main()
