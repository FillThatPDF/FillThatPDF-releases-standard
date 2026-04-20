#!/usr/bin/env python3
"""
Garbage Field Cleanup
=====================

A post-processing script that removes erroneous/garbage fields from fillable PDFs.
Refactored to use pikepdf and pdfplumber instead of PyMuPDF.

Usage:
    python garbage_field_cleanup.py <input_pdf> [output_pdf]
"""

import sys
import os
import json
import re
import argparse
from pathlib import Path
from collections import defaultdict

import pikepdf
import pdfplumber


# Patterns that indicate a LEGITIMATE field (should NOT be removed)
LEGITIMATE_PATTERNS = [
    r'^date',           # Date, Date_2, etc.
    r'^name$',          # Name
    r'^signature',      # Signature
    r'^customer',       # Customer Name, Customer Signature
    r'^contractor',     # Contractor Name
    r'^participating contractor', # Participating Contractor
    r'^address',        # Address
    r'^phone',          # Phone
    r'^email',          # Email
    r'^field_?\d+$',    # Field_1, Field2, etc. (generic but likely legitimate)
    r'^city',
    r'^state',
    r'^zip',
    r'^ssn',
    r'^account',
    r'^total',
    r'^amount',
    r'^describe',       # Describe:
    r'^other',          # Generic 'Other' catch-all (Other:, Other_______, Other (describe))
    r'.*_{2,}$',        # Any label ending in 2+ underscores (signals a legitimate line field)
    r'.*_gen$',         # SmartFillable General Underscore fields (e.g. Other_GEN)
    r'.*_emb$',         # SmartFillable Embedded Underscore fields
    r'pre-?existing',   # Pre-existing conditions (this is actually a legitimate field)
    r'^notes',          # Notes, Notes_2, Additional Notes, etc.
    r'partner.*name',   # Partner Organization Name
    r'^additional',     # Additional Notes, Additional info
    r'^comments',       # Comments field
    r'^title',          # Title field
    r'^company',        # Company Name
    r'estimated project', # Estimated Project Completion Date
    r'^installed date',  # Installed Date field
    r'project information', # Project Information field
    r'^filled_box',     # SmartFillable v21+ filled box fields (colored input boxes)
    r'^[°˚]f',          # Temperature fields (˚F)
    r'account',         # Fields referencing account numbers (preserve COMB/MaxLen)
]

# Field flag for PDF COMB (push-based comb appearance)
COMB_FLAG = 0x1000000

# Keywords that strongly indicate GARBAGE (section headers, instructions)
GARBAGE_KEYWORDS = [
    'invoice for ', 'must include', 'must be', 'installations', 'installs', 'see example',
    'measures installed', 'mechanical', 'certificates', 'permit information', 'please note',
    'please ensure', 'i certify that', 'notice to', 'the following', 'one of the',
    'can be submitted', 'requirements', 'if a furnace', 'if an ac', 'the seel team',
    'upload all', 'completed supporting', 'health and safety', 'before and after',
    'certificate of completion', 'rebate; these items', 'examples of some',
    'documentation required', 'eligibility', 'limitation of liability', 'disclaimer',
    'warranty', 'information release', 'tax liability', 'property rights', 'release',
    'indemnification', 'terms and conditions', 'federal or nonprofit',
    'low-income programs', 'lowincome programs', 'follows', 'proof of income',
    'may include', 'hud ami', 'eea rebates', 'forms available', 'include required',
    'via the oit', 'the program team', 'submit request', 'allocated measures',
    'eligible to install', 'introductory meeting', 'orientation trainings',
    'sample customer', 'project forecast', 'documentation to seel', 'partner guidelines',
    'state emergency', 'income limit table', 'hud income', 'found here',
    'household received assistance', 'member your household', 'have you, or',
    'qualifying programs', 'noted below', 'sources of income', 'gov/topics',
    'govtopics', 'aspe hhs', 'huduser.gov', 'based on a review',
    'submitted documentation', 'field verification', 'residence listed',
    'project installations', 'above are required', 'signed and approved',
    'project completion', 'rebate application',
]


def intersect_area(r1, r2):
    """Calculate intersection area of two rects (x0, y0, x1, y1)."""
    dx = min(r1[2], r2[2]) - max(r1[0], r2[0])
    dy = min(r1[3], r2[3]) - max(r1[1], r2[1])
    if dx > 0 and dy > 0:
        return dx * dy
    return 0


def is_garbage_field(annot, words=None, rects=None, sensitivity='standard', page_num=0, page_height=792) -> tuple:
    """
    Determine if a single field is garbage using spatial analysis and name patterns.

    Returns (is_garbage: bool, reason: str, meta: dict)
    """
    # Check if it's a text field (/FT /Tx)
    ft = annot.get('/FT')
    if ft != pikepdf.Name('/Tx'):
        return False, "Not a text field", {}
    
    # Get Rect in PDF coordinates (bottom-up)
    rect_pdf = annot.get('/Rect')
    if not rect_pdf or len(rect_pdf) < 4:
        return False, "Invalid Rect", {}
    
    x0, y0, x1, y1 = [float(c) for c in rect_pdf]
    
    # Transform to visual coordinates (top-down) for comparison with pdfplumber
    # visual space: x0, top, x1, bottom
    v_x0, v_top, v_x1, v_bottom = x0, page_height - y1, x1, page_height - y0
    v_rect = (v_x0, v_top, v_x1, v_bottom)
    
    width = v_x1 - v_x0
    height = v_bottom - v_top
    
    # Handle potentially missing field name /T
    name = str(annot.get('/T', '')).strip()
    name_lower = name.lower()

    meta = {
        'name': name,
        'rect': v_rect,
        'width': width,
        'height': height,
    }

    # Preserve COMB fields or fields that explicitly set a MaxLen
    try:
        ff_val = int(annot.get('/Ff', 0) or 0)
    except Exception:
        ff_val = 0
    meta['ff_val'] = ff_val
    maxlen = None
    if annot.get('/MaxLen') is not None:
        try:
            maxlen = int(annot.get('/MaxLen'))
        except Exception:
            maxlen = None
    meta['maxlen'] = maxlen

    if (ff_val & COMB_FLAG) != 0:
        return False, "COMB field (preserved)", meta
    if maxlen and maxlen > 0:
        return False, "Has MaxLen (preserved)", meta

    # 1. IMMEDIATE REJECTION (Legitimate fields)
    for pattern in LEGITIMATE_PATTERNS:
        if re.search(pattern, name_lower):
            meta['matched_pattern'] = pattern
            return False, f"Matches legitimate pattern: {pattern}", meta

    # 2. SPATIAL OVERLAP ANALYSIS (Text Collision)
    collision_area = 0.0
    colliding_word_texts = []
    
    if words:
        for word in words:
            # word is from pdfplumber: {'x0', 'top', 'x1', 'bottom', 'text'}
            word_rect = (word['x0'], word['top'], word['x1'], word['bottom'])
            area = intersect_area(v_rect, word_rect)
            if area > 0:
                collision_area += area
                if re.search(r'[a-zA-Z0-9]', word['text']):
                    colliding_word_texts.append(word['text'])
                
    widget_area = width * height
    coverage_pct = (collision_area / widget_area) * 100 if widget_area > 0 else 0
    word_count = len(colliding_word_texts)
    meta['coverage_pct'] = coverage_pct
    meta['word_count'] = word_count
    meta['colliding_words'] = colliding_word_texts[:10]

    # Detect comb-like vertical separators inside the widget - preserve as likely COMB
    thin_vertical_lines = 0
    if rects and width >= 150 and height >= 10:
        for r in rects:
            rx0, rtop, rx1, rbot = r['x0'], r['top'], r['x1'], r['bottom']
            rw = rx1 - rx0
            rh = rbot - rtop
            # vertical separator: very thin and tall relative to field
            if rw <= 5 and rh >= (0.5 * height):
                # check if separator lies inside the field horizontally and vertically
                if (rx0 >= v_x0 - 1 and rx1 <= v_x1 + 1 and rtop >= v_top - 1 and rbot <= v_bottom + 1):
                    thin_vertical_lines += 1
    meta['thin_vertical_lines'] = thin_vertical_lines
    # If we detect multiple vertical separators, this is likely a comb/grid: preserve
    if thin_vertical_lines >= 4:
        return False, f"Likely comb/grid ({thin_vertical_lines} separators) - preserve", meta

    # Sensitivity Logic
    coverage_threshold = 15.0
    require_alphanumeric = True
    
    if sensitivity == 'conservative':
        coverage_threshold = 80.0
        require_alphanumeric = True
    elif sensitivity == 'aggressive':
        coverage_threshold = 5.0
        require_alphanumeric = False
    
    is_collision_garbage = False
    if coverage_pct > coverage_threshold:
        if require_alphanumeric:
            if word_count >= 1:
                is_collision_garbage = True
        else:
            is_collision_garbage = True
    
    if is_collision_garbage:
        return True, f"Text collision ({sensitivity}): {coverage_pct:.1f}% coverage", meta

    # 3. HEADER/FOOTER ANALYSIS
    # visual space: top < 60 or bottom > 750 (assuming standard 792 height)
    is_at_extreme = v_top < 60 or v_bottom > (page_height - 42)
    if is_at_extreme and word_count > 0:
        return True, "Overlaps text in header/footer area", meta

    # 4. KEYWORD & PATTERN ANALYSIS
    if sensitivity != 'conservative':
        for keyword in GARBAGE_KEYWORDS:
            if keyword in name_lower:
                return True, f"Contains garbage keyword: '{keyword}'", meta
        
        if sensitivity == 'aggressive':
            name_words = name_lower.split()
            if len(name_words) >= 3:
                sentence_markers = {'for', 'the', 'a', 'an', 'to', 'and', 'or', 'if', 'that', 'this', 'must', 'should', 'can', 'will', 'are', 'is', 'be'}
                marker_count = sum(1 for w in name_words if w in sentence_markers)
                if marker_count >= 2:
                    return True, f"Looks like a sentence ({marker_count} markers)", meta
                
        if name_lower.endswith(':') and len(name) > 30:
            return True, "Long field name ending with colon", meta
            
        if height < 14 and width > 300 and word_count >= 1:
            return True, "Wide, thin field overlapping text", meta

    # 6-8. PAGE 1 HEADER GRAPHIC DETECTION
    if page_num == 0:
        relative_y = v_top / page_height if page_height > 0 else 0
        aspect_ratio = width / height if height > 0 else 0
        
        # 6. DECORATIVE GRAPHIC COLLISION (using pdfplumber.rects)
        if rects and relative_y < 0.35:
            for r in rects:
                # r is from pdfplumber: {'x0', 'top', 'x1', 'bottom'}
                d_rect = (r['x0'], r['top'], r['x1'], r['bottom'])
                d_width = r['x1'] - r['x0']
                d_height = r['bottom'] - r['top']
                
                if d_width < 80 or d_height < 15:
                    continue
                
                area = intersect_area(v_rect, d_rect)
                if area > 0:
                    overlap_pct = (area / widget_area) * 100 if widget_area > 0 else 0
                    is_decorative = d_width > 300 and d_height < 100
                    is_circle_like = aspect_ratio < 3 and width < 60 and height < 60
                    
                    if overlap_pct > 80 and (is_decorative or is_circle_like):
                        return True, f"Overlaps decorative graphic ({overlap_pct:.0f}%)", meta

        if aspect_ratio > 12 and relative_y < 0.25 and width > 200:
            return True, f"Wide banner-like field in header area (ratio: {aspect_ratio:.1f}:1)", meta
        
        if relative_y < 0.35:
            if name.strip().isdigit():
                return True, f"Numbered field in header graphic area ({name})", meta
            if name_lower.startswith('line_field'):
                return True, f"Line_Field in header area", meta

    return False, "Appears legitimate", meta


def cleanup_garbage_fields(pdf_path: str, output_path: str = None, verbose: bool = True, sensitivity: str = 'standard') -> dict:
    """
    Remove garbage fields from a fillable PDF using spatial and pattern analysis.
    """
    result = {
        'fields_removed': 0,
        'pages_cleaned': [],
        'success': False,
        'message': ''
    }
    
    try:
        # Use allow_overwriting_input=True to handle in-place edits safely
        pdf = pikepdf.Pdf.open(str(pdf_path), allow_overwriting_input=True)
        plumber_pdf = pdfplumber.open(str(pdf_path))
    except Exception as e:
        result['message'] = f"Failed to open PDF: {e}"
        return result
    
    if verbose:
        print(f"Garbage Field Cleanup (Spatial)")
        print(f"   Input: {Path(pdf_path).name}")
        print(f"   Pages: {len(pdf.pages)}")
    
    fields_removed_total = 0
    pages_cleaned = set()
    
    # Track widgets to remove by identifying their presence in /Annots and /AcroForm
    acroform = pdf.Root.get('/AcroForm')
    all_fields = acroform.get('/Fields') if acroform else None
    
    for i, page in enumerate(pdf.pages):
        if '/Annots' not in page:
            continue
        
        # Get spatial data using pdfplumber
        plumber_page = plumber_pdf.pages[i]
        words = plumber_page.extract_words()
        rects = plumber_page.rects
        page_height = float(plumber_page.height)
        
        # We need to iterate in reverse or collect items to avoid index issues
        # But for pikepdf arrays, we'll collect references to remove
        annots_to_remove = []
        
        for annot in page.Annots:
            if annot.get('/Subtype') != pikepdf.Name('/Widget'):
                continue
            
            is_garbage, reason, meta = is_garbage_field(annot, words, rects, sensitivity=sensitivity, page_num=i, page_height=page_height)
            
            if is_garbage:
                annots_to_remove.append((annot, reason, meta))
            else:
                # When verbose (details mode), print why a field was preserved/considered legitimate
                if verbose:
                    field_name = str(annot.get('/T', 'unnamed'))
                    print(f"      - KEEP '{field_name[:40]}' -> {reason} | coverage={meta.get('coverage_pct',0):.1f}% words={meta.get('word_count',0)} thin_seps={meta.get('thin_vertical_lines',0)} ff={meta.get('ff_val',0)} maxlen={meta.get('maxlen')}")
        
        if annots_to_remove:
            if verbose:
                print(f"   Page {i + 1}: Found {len(annots_to_remove)} garbage fields")
            
            for annot, reason, meta in annots_to_remove:
                field_name = str(annot.get('/T', 'unnamed'))
                
                # 1. Remove from page's /Annots
                try:
                    removed_from_page = False
                    for idx in range(len(page.Annots)):
                        if page.Annots[idx] == annot:
                            del page.Annots[idx]
                            removed_from_page = True
                            break
                    
                    # 2. Remove from AcroForm/Fields if it exists there
                    removed_from_acro = False
                    if all_fields is not None:
                        for idx in range(len(all_fields)):
                            if all_fields[idx] == annot:
                                del all_fields[idx]
                                removed_from_acro = True
                                break
                    
                    if removed_from_page:
                        fields_removed_total += 1
                        pages_cleaned.add(i + 1)
                        if verbose:
                            # Print removal line with additional meta details when in details mode
                            print(f"      - '{field_name[:40]}' ({reason}) | coverage={meta.get('coverage_pct',0):.1f}% words={meta.get('word_count',0)} thin_seps={meta.get('thin_vertical_lines',0)} ff={meta.get('ff_val',0)} maxlen={meta.get('maxlen')}")
                    else:
                        if verbose:
                            print(f"      [WARNING] Could not find widget on page for '{field_name[:40]}' | reason={reason}")
                except Exception as e:
                    if verbose:
                        import traceback
                        # print(traceback.format_exc()) # Uncomment for deep debug
                        print(f"      Warning: Could not remove '{field_name[:20]}': {e}")
    
    plumber_pdf.close()
    
    # Save the cleaned PDF
    if fields_removed_total > 0:
        try:
            target = output_path if output_path else pdf_path
            pdf.save(str(target))
            pdf.close()
            
            if verbose:
                print(f"   [OK] Removed {fields_removed_total} garbage fields from {len(pages_cleaned)} pages")
            
            result['success'] = True
            result['fields_removed'] = fields_removed_total
            result['pages_cleaned'] = sorted(list(pages_cleaned))
            result['message'] = f"Removed {fields_removed_total} garbage fields"
        except Exception as e:
            result['message'] = f"Failed to save PDF: {e}"
    else:
        pdf.close()
        if verbose:
            print(f"   [OK] No garbage fields found - PDF is clean")
        result['success'] = True
        result['message'] = "No garbage fields found"
    
    return result


def main():
    """Command-line interface."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_pdf", help="Input PDF file path")
    parser.add_argument("output_pdf", nargs="?", help="Output PDF file path (optional)")
    parser.add_argument("--details", action="store_true", help="Print details about removed fields")
    parser.add_argument("--sensitivity", choices=['conservative', 'standard', 'aggressive'], default='standard', help="Cleanup sensitivity")
    
    args = parser.parse_args()
    
    output = args.output_pdf if args.output_pdf else args.input_pdf
    result = cleanup_garbage_fields(args.input_pdf, output, verbose=args.details, sensitivity=args.sensitivity)
    
    if result['success']:
        print(json.dumps(result, indent=2))
        sys.exit(0)
    else:
        print(f"Error: {result['message']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
