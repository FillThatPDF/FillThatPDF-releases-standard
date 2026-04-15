#!/usr/bin/env python3
"""
V22 Post-Processor - Filter False Positives
=============================================

Applies rule-based filtering to v22 output to remove false positives.

Based on error analysis:
- 316 FPs are TINY (<100 sq pts) - filter these
- Many FPs are table headers/labels (can detect by name patterns)
- Many FPs in margins and top areas
- Many checkbox FPs are actually bullets/markers

Usage:
    python v22_postprocessor.py input.pdf output.pdf [--analyze]

Author: FillThatPDF Team
Date: February 2026
"""

import sys
import re
import logging
from pathlib import Path
from typing import Dict, List, Tuple, Set
from collections import defaultdict

import pikepdf

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


# Field names that are likely FALSE POSITIVES (headers/labels, not input fields)
HEADER_PATTERNS = [
    r'^(ENERGY STAR|energy star)',  # Product names
    r'^(Natural Gas|Split System|Heat Pump)',  # Equipment types
    r'^(Refrigerator|Dehumidifier|Furnace|Boiler|AC)',  # Appliance names
    r'^(Attic|Basement|Wall|Ceiling)\s*(Insulation|Wall)',  # Section headers
    r'^(Above|Below).*Ground',  # Section headers
    r'^R\d+\+',  # R-value labels
    r'^(Date|Phone|Email|Fax|Name|Address|City|State|Zip)$',  # Single-word labels
    r'^(Yes|No|N/A|NA|TBD)$',  # Option values (not fields)
    r'^(following|continued|see below|page \d+)',  # Reference text
    r'^\d+$',  # Pure numbers (likely row/column numbers)
    r'^[A-Z]\.$',  # Single letter labels
    r'^(Total|Subtotal|Grand Total)',  # Summary labels
    r'^(Required|Optional|Mandatory)',  # Instruction labels
    r'^(Check|Select|Choose|Enter)',  # Instruction text
    r'^\(.*\)$',  # Parenthetical text
]

# Compile patterns
HEADER_REGEXES = [re.compile(p, re.IGNORECASE) for p in HEADER_PATTERNS]


def is_likely_header(field_name: str) -> bool:
    """Check if field name looks like a header/label rather than input field."""
    for regex in HEADER_REGEXES:
        if regex.search(field_name):
            return True
    return False


def get_fields_with_details(pdf_path: Path) -> List[Dict]:
    """Extract fields with all details needed for filtering."""
    fields = []
    
    with pikepdf.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            # Get page dimensions
            mediabox = page.MediaBox if '/MediaBox' in page else [0, 0, 612, 792]
            page_width = float(mediabox[2] - mediabox[0])
            page_height = float(mediabox[3] - mediabox[1])
            
            if "/Annots" not in page:
                continue
            
            for annot_idx, annot in enumerate(page.Annots):
                try:
                    if annot.get("/Subtype") != "/Widget":
                        continue
                    
                    rect = annot.get("/Rect", None)
                    if rect is None:
                        continue
                    
                    x0, y0, x1, y1 = float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])
                    
                    # Normalize
                    if x0 > x1: x0, x1 = x1, x0
                    if y0 > y1: y0, y1 = y1, y0
                    
                    width = x1 - x0
                    height = y1 - y0
                    area = width * height
                    
                    # Field type
                    ft = str(annot.get("/FT", ""))
                    ff = int(annot.get("/Ff", 0)) if "/Ff" in annot else 0
                    
                    if ft == "/Btn":
                        field_type = "radio" if (ff & 32768) else "checkbox"
                    elif ft == "/Ch":
                        field_type = "dropdown"
                    else:
                        field_type = "text"
                    
                    name = str(annot.get("/T", ""))
                    
                    # Position relative to page
                    rel_x = x0 / page_width
                    rel_y = y0 / page_height
                    center_x = (x0 + x1) / 2 / page_width
                    center_y = (y0 + y1) / 2 / page_height
                    
                    fields.append({
                        'name': name,
                        'bbox': [x0, y0, x1, y1],
                        'page': page_num,
                        'annot_idx': annot_idx,
                        'type': field_type,
                        'width': width,
                        'height': height,
                        'area': area,
                        'rel_x': rel_x,
                        'rel_y': rel_y,
                        'center_x': center_x,
                        'center_y': center_y,
                        'page_width': page_width,
                        'page_height': page_height,
                        'aspect_ratio': width / height if height > 0 else 0
                    })
                
                except Exception:
                    continue
    
    return fields


def should_remove_field(field: Dict) -> Tuple[bool, str]:
    """
    Determine if a field should be removed.
    Returns (should_remove, reason).
    """
    name = field['name']
    area = field['area']
    field_type = field['type']
    width = field['width']
    height = field['height']
    rel_x = field['rel_x']
    rel_y = field['rel_y']
    aspect_ratio = field['aspect_ratio']
    
    # Rule 1: Remove fields with header-like names (most reliable rule)
    if is_likely_header(name):
        return True, f"header_pattern ({name[:30]})"
    
    # Rule 2: Remove extremely wide fields that are too short (horizontal lines)
    if width > 200 and height < 5:
        return True, f"horizontal_line (w={width:.1f}, h={height:.1f})"
    
    # Rule 3: Remove checkbox-typed fields that are WAY too large (likely not checkboxes)
    # Real checkboxes are typically 36-100 sq pts
    if field_type == 'checkbox' and area > 1000:
        return True, f"oversized_checkbox ({area:.1f} > 1000)"
    
    # Rule 4: Remove radio-typed fields that are suspiciously large
    if field_type == 'radio' and area > 800:
        return True, f"oversized_radio ({area:.1f} > 800)"
    
    # Rule 5: Remove fields in extreme right margin (>97% of page width)
    if rel_x > 0.97:
        return True, f"right_margin (x={rel_x:.2f})"
    
    # Rule 6: Remove fields with names that are ONLY numbers (pure numeric, no letters)
    # But allow names like "Cell_123" or "Field_1"
    if name and not any(c.isalpha() for c in name):
        return True, f"pure_numeric_name ({name[:20]})"
    
    # Rule 7: Remove extremely tall narrow text fields (likely spanning multiple rows)
    if height > 150 and aspect_ratio < 0.3 and field_type == 'text':
        return True, f"tall_narrow_text (h={height:.1f}, ar={aspect_ratio:.2f})"
    
    return False, ""


def apply_filters(input_pdf: Path, output_pdf: Path, analyze_only: bool = False) -> Dict:
    """Apply post-processing filters to v22 output."""
    
    fields = get_fields_with_details(input_pdf)
    logger.info(f"📋 Found {len(fields)} fields in v22 output")
    
    # Categorize fields
    keep_fields = []
    remove_fields = []
    removal_reasons = defaultdict(int)
    
    for field in fields:
        should_remove, reason = should_remove_field(field)
        if should_remove:
            remove_fields.append((field, reason))
            # Extract rule name from reason
            rule_name = reason.split(' ')[0]
            removal_reasons[rule_name] += 1
        else:
            keep_fields.append(field)
    
    logger.info(f"✅ Keeping: {len(keep_fields)} fields")
    logger.info(f"❌ Removing: {len(remove_fields)} fields")
    
    if removal_reasons:
        logger.info(f"\n📊 Removal breakdown:")
        for reason, count in sorted(removal_reasons.items(), key=lambda x: -x[1]):
            logger.info(f"   {reason}: {count}")
    
    if analyze_only:
        logger.info(f"\n🔍 Sample removals:")
        for field, reason in remove_fields[:10]:
            logger.info(f"   [{field['type']}] {field['name'][:40]:40s} → {reason}")
        return {
            'total': len(fields),
            'kept': len(keep_fields),
            'removed': len(remove_fields),
            'reasons': dict(removal_reasons)
        }
    
    # Create filtered PDF
    fields_to_remove = set()
    for field, _ in remove_fields:
        fields_to_remove.add((field['page'], field['annot_idx']))
    
    with pikepdf.open(input_pdf) as pdf:
        for page_num, page in enumerate(pdf.pages):
            if "/Annots" not in page:
                continue
            
            new_annots = []
            for annot_idx, annot in enumerate(page.Annots):
                if (page_num, annot_idx) not in fields_to_remove:
                    new_annots.append(annot)
            
            page.Annots = pikepdf.Array(new_annots)
        
        # Update AcroForm field count if present
        if '/AcroForm' in pdf.Root:
            acroform = pdf.Root.AcroForm
            if '/Fields' in acroform:
                # Filter fields array too
                old_fields = list(acroform.Fields)
                # This is trickier - we need to match by name or reference
                # For now, just update the annotation filtering
        
        pdf.save(output_pdf)
    
    logger.info(f"\n💾 Saved filtered PDF: {output_pdf}")
    
    return {
        'total': len(fields),
        'kept': len(keep_fields),
        'removed': len(remove_fields),
        'reasons': dict(removal_reasons)
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Post-process v22 output to remove false positives')
    parser.add_argument('input_pdf', help='Input PDF (v22 output)')
    parser.add_argument('output_pdf', nargs='?', help='Output PDF (filtered)')
    parser.add_argument('--analyze', '-a', action='store_true', help='Analyze only, don\'t modify')
    
    args = parser.parse_args()
    
    input_path = Path(args.input_pdf)
    if not input_path.exists():
        logger.error(f"❌ Input not found: {input_path}")
        sys.exit(1)
    
    if args.analyze:
        logger.info(f"🔍 Analyzing: {input_path.name}")
        apply_filters(input_path, None, analyze_only=True)
    else:
        output_path = Path(args.output_pdf) if args.output_pdf else input_path.with_stem(input_path.stem + '_filtered')
        logger.info(f"🔧 Processing: {input_path.name}")
        apply_filters(input_path, output_path, analyze_only=False)


if __name__ == "__main__":
    main()
