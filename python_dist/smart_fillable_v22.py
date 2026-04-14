#!/usr/bin/env python3
"""
Universal PDF Fillable Field Detector v1.0
==========================================

A FIRST-PASS ACCURATE fillable PDF creator that combines ALL learned
detection strategies into a single robust solution.

CRITICAL LEARNINGS INCORPORATED:
1. AcroForm needs /NeedAppearances: True for Acrobat visibility
   -> This tells Acrobat to regenerate appearances from DA strings
   -> Eliminates Preview.app dependency (cross-platform compatible)
2. Radio buttons group by column position (Pass/Fail headers)
3. Header rows: True headers have no colons and span wide
4. Signature fields: Match exact underscore bounds
5. Checkmark appearance: ZapfDingbats '4' character

DETECTION STRATEGIES:
- Vector squares (4-15pt): Checkboxes
- Unicode ☐ characters: Text-based checkboxes  
- pdfplumber tables: Structured data cells
- Underscore patterns: Signature/date lines
- Horizontal lines: Form fill lines

CHANGELOG:
- Fixed image detection in complex forms (Pages 4-8)
- enhanced negative/zero height field cleanup
- Improved super thin field detection (<3pt height)

Usage:
    python smart_fillable_v22.py input.pdf [output.pdf] [--enhanced off|conservative|aggressive] [--qa-overlay <file.pdf>] [--candidate-json <file.json>]

Author: Consolidated from smart_fillable_v13 and make_any_pdf_fillable
Date: December 2025
"""

import sys
import os
import math
import re
import subprocess
import time
import json
import traceback
import logging

logging.basicConfig(filename='/tmp/smart_fillable_debug.log', level=logging.DEBUG, filemode='w')
# Reduce verbose parsing logs from pdfminer/pdfplumber which can dramatically
# slow down parsing of complex PDFs during page iteration
logging.getLogger('pdfminer').setLevel(logging.WARNING)
logging.getLogger('pdfplumber').setLevel(logging.WARNING)

from pathlib import Path
from collections import defaultdict
from typing import List, Dict, Tuple, Optional, Any, Generator, Set
import argparse


import pdfplumber
import pikepdf
# poppler/pdf2image removed — overlays are generated vectorially via ReportLab
from PIL import Image, ImageDraw  # PIL still used elsewhere for legacy helpers (kept for now)

def _get_poppler_path():
    """Get the path to bundled poppler binaries.
    
    Returns path to poppler_bin directory if bundled, None otherwise (use system poppler).
    Handles both frozen (PyInstaller) and development environments.
    """
    import sys
    
    # Check if running as PyInstaller frozen executable
    if getattr(sys, 'frozen', False):
        # Frozen: look relative to executable's directory
        app_dir = Path(sys.executable).resolve().parent.parent.parent
        candidates = [
            app_dir / 'Resources' / 'poppler_bin',  # Inside .app bundle
            Path(sys.executable).parent / 'poppler_bin',  # Same dir as exe
        ]
    else:
        # Development: look relative to this script
        script_dir = Path(__file__).resolve().parent
        candidates = [
            script_dir.parent / 'poppler_bin',  # FillThatPDF_Optimized/poppler_bin
            script_dir / 'poppler_bin',  # python_dist/poppler_bin
        ]
    
    for candidate in candidates:
        if candidate.exists() and (candidate / 'pdfinfo').exists():
            return str(candidate)
    
    # Not bundled, rely on system poppler
    return None


class UniversalPDFFillable:
    """
    Create fillable PDFs with first-pass accuracy.
    
    Combines multiple detection strategies and applies them
    in the correct order for maximum compatibility.
    """
    
    # ========== CONFIGURATION ==========
    
    # Checkbox detection parameters
    CHECKBOX_MIN_SIZE = 4
    CHECKBOX_MAX_SIZE = 22
    CHECKBOX_SIZE_TOLERANCE = 3  # Max difference between width/height
    CHECKBOX_FIELD_SIZE_SMALL = 6   # Size for small checkbox widgets (detected < 9pt)
    CHECKBOX_FIELD_SIZE_MEDIUM = 8  # Size for medium checkbox widgets (detected 9-14pt)
    CHECKBOX_FIELD_SIZE_LARGE = 10  # Size for large checkbox widgets (detected >= 15pt)
    
    # Field formatting patterns
    FORMAT_PATTERNS = {
        'date': {'keywords': ['date:', 'date', 'installed date', 'date installed', 'start date', 'completion date', 'expected date', 'date of', 'date_3', 'date_5', 'tuneup date'], 'exclude': ['quantity', 'associated', 'funding', 'project name', 'building name', 'account number', 'project #', 'project number'], 'maxlen': 10, 'width_reduction': 0.10},  # 10% narrower for dropdown
        'zip': {'keywords': ['zip', 'postal', 'zip code'], 'exclude': [], 'maxlen': 10},
        'phone': {'keywords': ['phone', 'tel', 'fax'], 'exclude': [], 'maxlen': 14},
        'email': {'keywords': ['email', 'e-mail'], 'exclude': [], 'maxlen': 100},
        'state': {'keywords': ['state'], 'exclude': ['statement', 'estate'], 'maxlen': 2},
        'year': {'keywords': ['year'], 'exclude': [], 'maxlen': 4},
        'score': {'keywords': ['score', 'point'], 'exclude': [], 'maxlen': 3},
        # Currency fields - get decimal formatting (2 places) with optional $ symbol
        'currency': {
            'keywords': [
                'amount', 'rebate', 'cost', 'price', 'fee', 'rate',
                'invoice', 'payment', 'balance', 'subtotal', 'discount',
                'savings', 'incentive', 'credit', 'deduction',
                'reward', 'dollar', 'money',
                'expected rebate'  # Compound keyword for "Total Expected Rebate" headers
                # NOTE: 'charge' removed - conflicts with 'refrigerant charge' which is numeric
                # NOTE: 'total' removed - conflicts with 'Total Number in Household' etc
                # NOTE: 'funding' removed - can be date field ("Date: funding")
            ],
            'exclude': ['date', 'name', 'address', 'street', 'city', 'email', 'phone',
                       'signature', 'customer number', 'account number', 'model number',
                       'serial number', 'permit number', 'invoice number', 'order number',
                       'account #', 'serial #', 'model #', 'permit #', 'invoice #', 'order #',
                       'btuh', 'btu', 'refrigerant', 'cooling', 'heating', 'nominal',
                       'kwh', 'watt', 'cfm', 'psi', 'temp', 'pressure',
                       # Measurement fields - should be NUMBER not CURRENCY
                       'sq ft', 'sqft', 'square', 'linear', 'feet', 'foot',
                       'linear_feet', 'installed_linear', 'linear feet',
                       'number', 'total number',  # "Total Number" fields should be numeric, not currency
                       'household', 'number in', 'proof', 'qualifying', 'assistance',
                       'measure', 'details', 'provide', 'requested', 'needed', 'why',
                       # Field name patterns that should be plain text, not currency
                       'funding_amount', 'scope_of_funding', 'associated_eea', 'eea_measure',
                       'scope', 'associated', 'reason', 'approved', 'denied',
                       # More exclusions for text fields that contain currency keywords
                       'frequency', 'sources_of_income', 'source_of', 'tips', 'brochure',
                       'weekly', 'monthly', 'applicable', 'energy_savings'],
            'maxlen': 12
        },
        # Non-currency number fields - NO decimal formatting (integers/measurements)
        # NOTE: Fields like Account#, Serial#, Model# are EXCLUDED - they should be plain text
        'number': {
            'keywords': [
                '°f', 'degf', 'deg f', 'deg.', 'degree', 'temp', 'temperature',
                'quantity', 'qty', 'unit', 'count',
                'btuh', 'btu', 'cfm', 'watts', 'watt', 'psi', 'amps', 'amp',
                'volts', 'volt', 'ohms', 'ohm', 'hz', 'kw', 'kwh',
                'sq ft', 'sqft', 'square', 'sq. ft.', 'sq. ft', 'cubic', 'gallon', 'gal',
                'square footage', 'square_footage',  # Added for square footage fields
                'percent', '%', 'rating', 'capacity', 'size', 'weight',
                'length', 'width', 'height', 'depth', 'area', 'volume',
                'tonnage', 'ton', 'seer', 'eer', 'hspf', 'afue', 'uef', 'ef',
                'r-value', 'r value', 'rvalue', 'u-factor', 'u factor', 'ufactor',
                'hours', 'hrs', 'minutes', 'min', 'seconds', 'sec',
                'stories', 'floors', 'bedrooms', 'bathrooms', 'rooms',
                # Measurement fields - explicitly numeric NOT currency
                'feet', 'foot', 'linear feet', 'linear foot', 'linear_feet', 'installed_linear',
                'number', 'total number',  # "Number" and "Total Number" fields
                'household',
                'incentive',  # Added for incentive fields (when next to $ symbol they become numeric)
            ],
            'exclude': ['date', 'name', 'address', 'street', 'city', 'email', 'phone',
                       'signature', 'customer number', 'account number', 'model number',
                       'serial number', 'permit number', 'invoice number', 'order number',
                       'account #', 'serial #', 'model #', 'permit #', 'invoice #', 'order #',
                       'customer #', 'confirmation #', 'reference #', 'tracking #',
                       'total', 'amount', 'cost', 'price', 'rebate', 'savings',
                       'relationship', 'account_holder', 'account holder', 'customer_relationship',
                       'applicant_relationship', 'applicant', 'differen', 'eea measure', 'eea_measure', 
                       'associated', 'details', 'provide', 'requested', 'needed', 'why', 
                       'proof', 'qualifying', 'scope', 'funding_amount', 'funding amount',
                       # ID/Reference number fields - should be plain text, NOT numeric
                       'application number', 'ref id', 'ref id number', 'reference id',
                       'application_number', 'ref_id', 'ref_id_number', 'reference_id'],
            'maxlen': 0
        },
    }
    
    # Radio button column patterns
    RADIO_COLUMN_KEYWORDS = {
        'pass': 'Pass',
        'fail': 'Fail',
        'yes': 'Yes',
        'no': 'No',
        'n/a': 'NA',
        'na': 'NA',
        'not applicable': 'Not Applicable',
        'maybe': 'Maybe',
        "don't know": "Don't Know",
        'unknown': 'Unknown',
        # v17.18: Added Approved/Denied and similar patterns
        'approved': 'Approved',
        'denied': 'Denied',
        'approve': 'Approve',
        'deny': 'Deny',
        'accept': 'Accept',
        'reject': 'Reject',
        'complete': 'Complete',
        'incomplete': 'Incomplete',
        'satisfactory': 'Satisfactory',
        'unsatisfactory': 'Unsatisfactory',
        # v21: Added utility-related options for heat/water heater questions
        'natural gas': 'Natural Gas',
        'naturalgas': 'Natural Gas',  # no space version
        'gas': 'Gas',
        'electric': 'Electric',
        'propane': 'Propane',
        'oil': 'Oil',
        'solar': 'Solar',
        'wood': 'Wood',
        'none': 'None',
        'other': 'Other',
    }
    
    def __init__(self, input_pdf: str, output_pdf: str = None, settings: Dict = None, auto_calibrate: bool = False):
        self.input_pdf = Path(input_pdf)
        
        # Store settings with defaults
        self.settings = settings or {}
        self.auto_calibrate = auto_calibrate
        
        # Apply output suffix from settings (if provided)
        output_suffix = self.settings.get('output_suffix', '_fillable')
        
        if output_pdf:
            self.output_pdf = Path(output_pdf)
        else:
            stem = self.input_pdf.stem
            self.output_pdf = self.input_pdf.parent / f"{stem}{output_suffix}.pdf"
        
        # Use temp directory for overlays (avoids read-only filesystem issues in packaged apps)
        import tempfile
        temp_base = Path(tempfile.gettempdir()) / 'FillThatPDF_overlays'
        self.overlay_dir = temp_base / self.output_pdf.stem
        self.overlay_dir.mkdir(parents=True, exist_ok=True)
        
        # PDF handles (v21.7: Unified handle management)
        self.pdf = None
        
        # Detection results
        self.checkboxes: List[Dict] = []
        self.checkbox_positions: List[Dict] = []  # For overlap detection
        self.text_fields: List[Dict] = []
        self.radio_groups: List[Dict] = []
        self.table_cells: List[Dict] = []  # v20: Store table cells for coverage inspection
        self.page_lines: Dict[int, List] = {} # v21.24: Store page lines for boundary checks
        self.text_only_pages: set = set()  # v21.5: Pages with no form fields (skip detection)
        self.text_only_pages_detected = False # Caching flag
        self.tally_sheet_pages: set = set()  # v35.15: Pages with tally sheets (skip field creation)
        self.header_bars_cache = None # Caching for header bar detection
        self.invoice_table_bboxes = [] # v22.5: Store boundaries of P20+ invoice tables
        self.box_entry_areas: List[Dict] = []  # Pre-detected box entry (account number) areas
        self._checkbox_column_counts: Dict = {}  # v33.71: Track checkbox counts by column band
        self._table_cell_areas: List[Dict] = []  # v33.71: Track table cell interiors for checkbox blocking
        
        # v33.42: Tiered Logic Rankings (Higher = More Confidence)
        self.SOURCE_RANKS = {
            'user_injected': 1000,
            'large_image_rect': 600,  # v35.5: Image boxes are VERY high priority - they should beat checkboxes
            'box_entry': 550,
            'comb_box_grid': 550,
            'strict_grid': 450,   # High priority for real table cells
            'signature': 450,
            'date': 450,
            'character': 410,     # Character checkboxes are very reliable
            'special_label': 350,
            'inline_label': 350,
            'structural_box': 250, # Solid detected boxes
            'table': 180,
            'table_data_row': 180,
            'sub_table_data_row': 180,
            'vector': 150,        # Geometric shapes (could be text box outlines)
            'curve': 150,
            'line_box': 140,      # Often misidentifies text enclosures
            'embedded_underscore': 460,  # v33.80: High priority - detected underscores are very reliable
            'underscore': 100,
            'general_underscore': 100,
            'split_field': 100,
            'colon_separated': 90,
            'form_line_presplit': 80,
            'empty_box_lines': 50,
            'visual_in_cell': 40,
        }
        
        # Page data cache
        self.page_words: Dict[int, List] = {}
        self.page_heights: Dict[int, float] = {}
        self.page_widths: Dict[int, float] = {}
        self.page_rotations: Dict[int, int] = {}
        self.page_mediaboxes: Dict[int, tuple] = {}
        self.page_derotation_matrices: Dict[int, Any] = {}
        self.page_rects: Dict[int, List] = {}        # Cached rectangles
        self.page_curves: Dict[int, List] = {}       # v22.22: Cached curves
        self.page_edges: Dict[int, List] = {}        # v22.5: Cached edges (for dividers)
        self.page_tables: Dict[int, List] = {}       # Cached tables
        self.page_images: Dict[int, List] = {}       # v22.76: Cached images (for exclusion)
        self.page_count: int = 0
        
        self._cache_page_info()  # Pre-load rotation info
        
        # Existing form fields in source PDF (to avoid duplication)
        self.existing_checkboxes: List[Dict] = []  # Pre-existing checkboxes from source
        self._detect_existing_form_fields()  # Load existing fields

        # Enhanced-mode related defaults (v22)
        self.enhanced_mode = 'off'         # 'off'|'conservative'|'aggressive'
        self.qa_overlay = None             # Path to QA overlay output (optional)
        self.candidate_json = None         # Path to candidate JSON output (optional)
        self.page_stats: Dict[int, Dict] = {}  # Per-page calibration stats
        self.enhanced_candidates: List[Dict] = []  # Collected candidate proposals
        
        # Field naming
        self.column_counters = defaultdict(int)
        self.used_names = set()
        
        # Statistics
        self.stats = defaultdict(int)
        
        # Debug mode
        self.debug_mode = self.settings.get('debug', False)
    

    # ================================================================
    # AUTO-CALIBRATION METHODS (ported from V4)
    # ================================================================

    def analyze_pdf_structure(self):
        """
        AUTO-CALIBRATION: Analyze PDF structure to determine optimal detection settings.
        Returns dict with analysis data and recommended settings.
        """
        print(f"\n{'='*60}")
        print("🔍 AUTO-CALIBRATION: Analyzing PDF Structure...")
        print(f"{'='*60}\n")
    
        analysis = {
            'cell_widths': [],
            'cell_heights': [],
            'checkbox_sizes': [],
            'edge_distances': [],
            'rect_widths': [],
            'rect_heights': [],
            'has_tables': False,
            'has_checkboxes': False,
            'has_lines': False,
            'page_count': 0,
            'char_heights': [],
        }
    
        with pdfplumber.open(str(self.input_pdf)) as pdf:
            analysis['page_count'] = len(pdf.pages)
        
            for page_num, page in enumerate(pdf.pages):
                print(f"  Analyzing page {page_num + 1}...")
            
                # Analyze table structure
                tables = page.find_tables()
                if tables:
                    analysis['has_tables'] = True
                    for table in tables:
                        for cell in (table.cells or []):
                            if cell and len(cell) >= 4:
                                width = cell[2] - cell[0]
                                height = cell[3] - cell[1]
                                x_dist = min(cell[0], page.width - cell[2])
                                y_dist = min(cell[1], page.height - cell[3])
                            
                                analysis['cell_widths'].append(width)
                                analysis['cell_heights'].append(height)
                                analysis['edge_distances'].append(min(x_dist, y_dist))
            
                # Analyze rectangles for checkboxes
                for rect in (page.rects or []):
                    w = rect['x1'] - rect['x0']
                    h = rect['y1'] - rect['y0']
                    analysis['rect_widths'].append(w)
                    analysis['rect_heights'].append(h)
                
                    # Potential checkbox: small, roughly square
                    if 3 <= w <= 30 and 3 <= h <= 30:
                        if abs(w - h) <= 4:  # Square-ish
                            analysis['checkbox_sizes'].append((w + h) / 2)
                            analysis['has_checkboxes'] = True
            
                # Analyze lines
                if page.lines:
                    analysis['has_lines'] = True
            
                # Analyze character heights for font sizing
                for char in (page.chars or []):
                    if 'height' in char:
                        analysis['char_heights'].append(char['height'])
    
        # Calculate recommended settings
        recommendations = self._calculate_optimal_settings(analysis)
    
        return {
            'analysis': analysis,
            'recommendations': recommendations
        }

    def _calculate_optimal_settings(self, analysis):
        """Calculate optimal detection settings based on PDF analysis."""
        settings = {}
    
        print(f"\n{'='*60}")
        print("💡 Calculating Optimal Settings...")
        print(f"{'='*60}\n")
    
        # === FIELD SIZE THRESHOLDS ===
        if analysis['cell_widths']:
            min_width = min(analysis['cell_widths'])
            avg_width = sum(analysis['cell_widths']) / len(analysis['cell_widths'])
        
            # Set min_field_width to 80% of smallest cell, but not below 5pt
            settings['min_field_width'] = max(5, int(min_width * 0.8))
        
            print(f"📏 Cell Width Analysis:")
            print(f"   Smallest cell: {min_width:.1f} pt")
            print(f"   Average cell: {avg_width:.1f} pt")
            print(f"   → Recommended min_field_width: {settings['min_field_width']} pt")
    
        if analysis['cell_heights']:
            min_height = min(analysis['cell_heights'])
            avg_height = sum(analysis['cell_heights']) / len(analysis['cell_heights'])
        
            # Set min_field_height to 80% of smallest cell, but not below 5pt
            settings['min_field_height'] = max(5, int(min_height * 0.8))
        
            print(f"\n📐 Cell Height Analysis:")
            print(f"   Smallest cell: {min_height:.1f} pt")
            print(f"   Average cell: {avg_height:.1f} pt")
            print(f"   → Recommended min_field_height: {settings['min_field_height']} pt")
    
        # === CHECKBOX SETTINGS ===
        if analysis['checkbox_sizes']:
            # Use median for better stability against bullets
            sizes = sorted(analysis['checkbox_sizes'])
            median_cb = sizes[len(sizes)//2]
            
            # Use ±3.5pt tolerance for the main checkbox set (v33.53: Widened to catch P2 chars)
            settings['checkbox_size_min'] = max(3.5, int(median_cb - 3.5))
            settings['checkbox_size_max'] = int(median_cb + 3.5)
            
            # v33.43: Detect if form has massive square image upload boxes
            if analysis['rect_widths']:
                 max_rect = max(analysis['rect_widths'])
                 if max_rect > 50:
                      settings['image_box_min_size'] = 50
        
            print(f"\n☑️  Checkbox Analysis (Auto-Calibrated):")
            print(f"   Median checkbox: {median_cb:.1f} pt")
            print(f"   → Range: {settings['checkbox_size_min']} - {settings['checkbox_size_max']} pt")
        else:
            settings['checkbox_size_min'] = 7
            settings['checkbox_size_max'] = 14
            print(f"\n☑️  No checkboxes detected, using defaults (7-14pt)")
    
        # === HEADER LIMITS ===
        # v33.43: Auto-detect top banding
        top_bands = [h for w, h in zip(analysis['rect_widths'], analysis['rect_heights']) if h < 60 and w > 400]
        if top_bands:
             # Highest band is likely the first section header
             # Use its Y as the limit
             pass
        
        # === PAGE EDGE MARGIN ===
        if analysis['edge_distances']:
            min_edge_dist = min(analysis['edge_distances'])
            settings['page_edge_margin'] = max(5, int(min_edge_dist * 0.8))
            print(f"\n🔲 Edge Distance Analysis:")
            print(f"   Closest field to edge: {min_edge_dist:.1f} pt")
            print(f"   → Recommended page_edge_margin: {settings['page_edge_margin']} pt")
        else:
            settings['page_edge_margin'] = 20
    
        # === MIN UNDERSCORE LENGTH ===
        if analysis['cell_widths']:
            avg_cell = sum(analysis['cell_widths']) / len(analysis['cell_widths'])
            settings['min_underscore_length'] = max(15, int(avg_cell * 0.5))
            print(f"📏 Underscore Length: {settings['min_underscore_length']}")
    
        print(f"\n{'='*60}\n")
        return settings

    def apply_calibration(self, recommendations):
        """Apply auto-calibrated settings to v21 thresholds."""
        print("✅ Applying auto-calibrated settings...\n")
    
        # Map v4 settings to v21 constant names
        if 'min_field_width' in recommendations:
            min_fw = recommendations['min_field_width']
            # v21 uses these for various checks
            self.settings['min_field_width'] = min_fw
            print(f"   Set min_field_width = {min_fw}")
    
        if 'min_field_height' in recommendations:
            min_fh = recommendations['min_field_height']
            self.settings['min_field_height'] = min_fh
            print(f"   Set min_field_height = {min_fh}")
    
        if 'checkbox_size_min' in recommendations:
            cb_min = recommendations['checkbox_size_min']
            self.CHECKBOX_MIN_SIZE = cb_min
            print(f"   Set CHECKBOX_MIN_SIZE = {cb_min}")
    
        if 'checkbox_size_max' in recommendations:
            cb_max = recommendations['checkbox_size_max']
            self.CHECKBOX_MAX_SIZE = cb_max
            print(f"   Set CHECKBOX_MAX_SIZE = {cb_max}")
    
        if 'page_edge_margin' in recommendations:
            margin = recommendations['page_edge_margin']
            self.PAGE_EDGE_MARGIN = margin
            print(f"   Set PAGE_EDGE_MARGIN = {margin}")
    
        if 'min_underscore_length' in recommendations:
            min_us = recommendations['min_underscore_length']
            self.settings['min_underscore_length'] = min_us
            print(f"   Set min_underscore_length = {min_us}")
    
        print()

    def _get_setting(self, key: str, default: Any = None) -> Any:
        """Get setting value from user settings, falling back to provided default."""
        return self.settings.get(key, default)
    
    def _is_near_page_edge(self, page_num: int, x0: float, y0: float, x1: float, y1: float) -> bool:
        """Check if a field position is near the page edges (should be skipped)."""
        if not self.settings.get('skip_page_edges', False):
            return False  # Feature disabled
        
        margin = self.settings.get('page_edge_margin', 30)
        page_width = self.page_widths.get(page_num, 612)  # Default letter width
        page_height = self.page_heights.get(page_num, 792)  # Default letter height
        
        # Check if field is within margin of any edge
        if x0 < margin or x1 > (page_width - margin):
            return True
        if y0 < margin or y1 > (page_height - margin):
            return True
        return False
    
    def _get_underscore_pattern(self) -> str:
        """Get the underscore pattern based on min_underscore_length setting."""
        length = self.settings.get('min_underscore_length', 3)
        return '_' * max(3, length)  # Minimum 3 underscores
    
    def _preload_structural_data(self):
        """
        v21.7: Perform a single unified pass over the PDF using pdfplumber 
        to cache all lines, rects, words, and tables for all pages.
        
        This eliminates the 10+ redundant full-PDF scans that previously 
        caused slow performance on multi-page documents.
        """
        if not self.input_pdf.exists():
            print(f"   ⚠️ Error: Input PDF not found: {self.input_pdf}")
            return

        print(f"   📊 Pre-loading structural data for {self.page_count} page(s)...")
        import time
        start_time = time.time()
        
        try:
            with pdfplumber.open(str(self.input_pdf)) as pdf:
                for page_num, page in enumerate(pdf.pages):
                    # Cache layout info
                    self.page_heights[page_num] = float(page.height)
                    self.page_widths[page_num] = float(page.width)
                    
                    # Cache structural elements
                    self.page_words[page_num] = page.extract_words()
                    self.page_lines[page_num] = page.lines or []
                    self.page_rects[page_num] = page.rects or []
                    self.page_curves[page_num] = page.curves or []
                    self.page_edges[page_num] = page.edges or []
                    self.page_tables[page_num] = page.find_tables()
                    
                    # v22.76: Cache images for image area exclusion
                    self.page_images[page_num] = page.images if hasattr(page, 'images') else []
                    
            duration = time.time() - start_time
            print(f"   ✅ Structural data cached in {duration:.2f}s")
            # Detect dense square grids as an early heuristic to avoid creating checkboxes in these regions
            try:
                self._detect_square_grid_regions()
            except Exception:
                pass
        except Exception as e:
            print(f"   ⚠️ Warning: Partial failure during structural pre-load: {e}")
    
    def _detect_text_only_pages(self):
        """
        v21.5: Detect pages that are text-only (no form fields expected).
        """
        if self.text_only_pages_detected:
            return self.text_only_pages
            
        for page_num in range(self.page_count):
            words = self.page_words.get(page_num, [])
            lines = self.page_lines.get(page_num, [])
            
            # Check for horizontal form lines (>50pt wide, nearly horizontal)
            form_lines = []
            for line in lines:
                width = line['x1'] - line['x0']
                height = abs(line.get('y1', line['top']) - line.get('y0', line['top']))
                if width > 50 and height < 2:
                    form_lines.append(line)
            
            # Check for underscore text patterns
            underscore_count = sum(1 for w in words if '___' in w['text'])
            
            # Calculate word density
            page_area = self.page_widths.get(page_num, 0) * self.page_heights.get(page_num, 0)
            word_density = len(words) / (page_area / 10000) if page_area > 0 else 0
            
            # Check for numbered list colon patterns (e.g., "1. Rebates:", "2. Customer eligibility:")
            colon_words = [w for w in words if w['text'].endswith(':')]
            numbered_list_colons = 0
            for cw in colon_words:
                # Check if there's a number or period just before this word on same line
                for w in words:
                    if abs(w['top'] - cw['top']) < 3 and w['x1'] < cw['x0'] and w['x1'] > cw['x0'] - 30:
                        if w['text'].rstrip('.').isdigit() or w['text'] in ['.', ')']:
                            numbered_list_colons += 1
                            break
            
            # Decision: Mark as text-only if:
            # - No form lines AND no underscores AND high word density
            # - OR: Majority of colon-words are numbered list items
            is_text_only = False
            
            if len(form_lines) == 0 and underscore_count == 0:
                # No form structure at all
                if word_density > 15:  # High word density = body text
                    is_text_only = True
                elif len(colon_words) > 0 and numbered_list_colons >= len(colon_words) * 0.5:
                    # Most colons are part of numbered lists
                    is_text_only = True
            
            if is_text_only:
                self.text_only_pages.add(page_num)
                print(f"   Page {page_num + 1}: Detected as text-only (no form fields expected)")
        
        self.text_only_pages_detected = True
        return self.text_only_pages
    
    def _predetect_tally_sheets(self):
        """
        v35.15: Pre-detect pages that are tally sheets (handwritten data entry forms).
        These pages have large grids with mostly empty cells and should NOT have fillable fields.
        Must run BEFORE table detection to avoid creating 100s of junk fields.
        
        Detection criteria:
        1. Page contains "Tally Sheet" in the header text (definitive)
        2. Page has >50 cells AND >80% empty AND has tally-sheet-like columns
        
        v35.18: Exclude pages with worksheet indicators (Rebate, Equipment Type, etc.)
        v35.40: Tally sheets should still get TEXT FIELDS, just not auto-fill checkboxes.
               The tally_sheet_pages set is used to disable checkbox auto-fill, NOT all fields.
        """
        for page_num in range(self.page_count):
            page = self.pdf.pages[page_num]
            
            # Try to extract text to check for indicators
            try:
                page_text = (page.extract_text() or "")[:500].lower()
            except Exception as e:
                page_text = ""
            
            # v35.18: If page has worksheet indicators, it's NOT a tally sheet
            worksheet_indicators = ['rebate', 'equipment type', 'ref #', 'unit cost', 'total rebate', 'model number', 'serial number', 'installed', 'quantity']
            if any(ind in page_text for ind in worksheet_indicators):
                continue
            
            # Quick header check for explicit 'tally' indicators
            has_tally_header = 'tally' in page_text or 'tally sheet' in page_text

            # Build visual grid to get cell count and check emptiness
            grid_cells = self._build_visual_grid(page_num)
            
            if not grid_cells or len(grid_cells) < 50:
                continue  # Need at least 50 cells to consider as tally sheet
            
            # Count empty cells
            empty_cells = 0
            for c in grid_cells:
                try:
                    crop = page.within_bbox((c['x0']+1, c['y0']+1, c['x1']-1, c['y1']-1))
                    txt = (crop.extract_text() or "").strip()
                    if not txt:
                        empty_cells += 1
                except:
                    empty_cells += 1  # If can't extract, assume empty
            
            empty_frac = empty_cells / len(grid_cells)
            
            # v22.96: Further relaxed thresholds. 
            # High cell count (>100) and moderate emptiness (>50%) is enough for tally sheets.
            is_dense_grid = len(grid_cells) > 100
            if (empty_frac > 0.70 or (is_dense_grid and empty_frac > 0.50)) and has_tally_header:
                self.tally_sheet_pages.add(page_num)
                print(f"   Page {page_num + 1}: Detected as tally sheet ({len(grid_cells)} cells, {empty_frac:.0%} empty) - will create text fields but no auto-fill checkboxes")
    
    # ========== MAIN WORKFLOW ==========
    
    def _split_merged_multi_label_fields(self):
        """
        v36.5: UNIVERSAL - Detect and split fields that contain multiple logical labels merged into one.
        Example: "Type of Insulation Installed Quantity Installed Final R-Value"
        This often happens in tables where vertical separators are missing or excluded.
        """
        fields_to_add = []
        fields_to_remove = set()
        
        # Generic sub-header keywords that indicate a transition to a new field
        split_markers = [
            'typeof', 'quantity', 'qty', 'final', 'rvalue', 'r-value', 
            'date', 'name', 'phone', 'email', 'amount', 'score', 'points'
        ]
        
        for i, field in enumerate(self.text_fields):
            lbl = field.get('label', '')
            if not lbl or len(lbl) < 15: continue
            
            # Normalize label for marker detection
            norm_lbl = lbl.replace(':', '').replace('-', '').replace(' ', '').lower()
            
            # Find which markers are present
            found_markers = []
            for m in split_markers:
                if m in norm_lbl:
                    found_markers.append(m)
            
            # GENERIC N-WAY SPLIT based on marker density
            if len(found_markers) >= 2 and (field['x1'] - field['x0']) > 150:
                print(f"   [MultiSplit] {len(found_markers)}-way split for merged label: '{lbl}'")
                width = field['x1'] - field['x0']
                num_splits = len(found_markers)
                
                # Proportional split based on number of markers
                for idx, m in enumerate(found_markers):
                    f_part = field.copy()
                    f_part['x0'] = field['x0'] + (width * (idx / num_splits))
                    f_part['x1'] = field['x0'] + (width * ((idx + 1) / num_splits))
                    f_part['label'] = m.capitalize()
                    f_part['source'] = 'multi_split'
                    f_part['name'] = self._generate_unique_name(f_part['label'], field['page'])
                    fields_to_add.append(f_part)
                
                fields_to_remove.add(i)

        if fields_to_remove:
            # Clean up overlaps
            new_rects = [(f['page'], f['x0'], f['y0'], f['x1'], f['y1']) for f in fields_to_add]
            for j, other in enumerate(self.text_fields):
                if j in fields_to_remove: continue
                for p, nx0, ny0, nx1, ny1 in new_rects:
                    if other['page'] == p:
                        dx = min(other['x1'], nx1) - max(other['x0'], nx0)
                        dy = min(other['y1'], ny1) - max(other['y0'], ny0)
                        if dx > 5 and dy > 5:
                            fields_to_remove.add(j)
                            break

            for idx in sorted(fields_to_remove, reverse=True):
                if idx < len(self.text_fields):
                    self.text_fields.pop(idx)
            self.text_fields.extend(fields_to_add)

    def run(self) -> Path:
        """Execute the complete fillable PDF creation workflow."""
        self._print_header()
        
        # Open unified PDF handle
        self.pdf = pdfplumber.open(str(self.input_pdf))
        try:
            # v21.7: Pre-load all structural data ONCE
            self._preload_structural_data()
            
            # AUTO-CALIBRATION: Analyze PDF and optimize settings (if enabled)
            if self.auto_calibrate:
                print("\n" + "="*60)
                print("🔧 AUTO-CALIBRATION MODE ENABLED")
                print("="*60 + "\n")
                result = self.analyze_pdf_structure()
                self.apply_calibration(result['recommendations'])
            
            # PASS 0: Detect text-only pages (v21.5)
            print("📄 PASS 0: Detecting text-only pages...")
            self._detect_text_only_pages()
            
            # PASS 0.3: Pre-detect tally sheet pages (v35.15)
            # Must run BEFORE table detection to avoid creating 100s of junk fields
            print("\n📊 PASS 0.3: Pre-detecting tally sheet pages...")
            self._predetect_tally_sheets()
            
            # PASS 0.5: Pre-detect box_entry areas (account number rows)
            # This is done EARLY so checkbox detection can exclude these areas
            print("\n📦 PASS 0.5: Pre-detecting box entry areas...")
            self._predetect_box_entry_areas()
            
            # PASS 1: Detect all field types
            print("\n🔲 PASS 1: Detecting all checkbox types...")
            self._detect_all_checkboxes()
            
            # NEW v36.7: Detect horizontal-line-only tables (NYSEG/RG&E forms)
            # This handles PDFs with horizontal dividers but no vertical cell boundaries
            # RUNS VERY EARLY (right after checkboxes) to claim fields before other passes
            print("\n🏗️  PASS 1.5: Detecting horizontal-line-only tables...")
            self._detect_horizontal_line_tables()
            
            # v22.3: Box detection moved back to Pass 4C (after tables) so overlap removal works
            print("\n📋 PASS 2: Detecting table cells...")
            self._detect_table_cells()
            
            # NEW v36.7b: Add missing left columns in tables
            # Runs after regular table detection to find columns pdfplumber missed
            print("\n📊 PASS 2.5: Adding missing table left columns...")
            self._detect_missing_table_left_columns()
            
            print("\n✍️  PASS 3: Detecting signature/date fields...")
            self._detect_signature_fields()
            
            print("\n🏷️  PASS 3A: Detecting special label fields...")
            self._detect_special_label_fields()
            
            print("\n🏷️  PASS 3B: Detecting inline label fields (City/State/ZIP)...")
            self._detect_inline_label_fields()
            
            print("\n📝 PASS 3C: Detecting general underscore text patterns...")
            self._detect_general_underscore_fields()
            
            print("\n🏷️  PASS 3D: Detecting colon-separated label fields...")
            # v35.25: TEMPORARILY DISABLED - causing too many garbage fields overlaying table labels
            # self._detect_colon_separated_fields()
            print("   [DISABLED] Colon-separated detection skipped to prevent garbage fields")
            
            # v35.23: Moved colon label detection AFTER table detection to prevent duplicates
            # with embedded underscore fields (was causing garbage "Name of Contact Person" etc)
            # self._detect_colon_label_cells()  # MOVED TO AFTER TABLE DETECTION
            
            print("\n📦 PASS 4: Detecting empty boxes for text areas...")
            self._detect_empty_boxes()
            
            # v36.6: Final Self-Review Discovery
            print("\n🔍 PASS 6: Final Self-Review Discovery (Finding missing fields)...")
            self._discovery_pass_missing_labeled_fields()
            
            print("\n� PASS 4A: Detecting large image rectangles...")
            self._detect_large_image_rectangles()
            
            print("\n�📏 PASS 4B: Detecting form lines (underlines)...")
            self._detect_form_lines()
            
            print("\n📦 PASS 4C: Detecting box entry fields (account numbers)...")
            # v22.3: Runs AFTER table detection, removes overlapping wider fields
            self._detect_box_entry_fields()
            
            # v35.23: Colon label detection moved here (after table detection)
            # This prevents duplicate fields with embedded underscores
            print("\n🏷️  PASS 4C2: Detecting colon-label cell fields...")
            self._detect_colon_label_cells()
            
            # CLEANUP: Remove checkboxes that overlap with box_entry fields
            # self._remove_checkboxes_overlapping_box_entry()
            
            # v22.76: Remove checkboxes overlapping images
            self._remove_checkboxes_over_images()
            
            # v22.83: Remove checkboxes in text-only table columns
            self._remove_checkboxes_in_text_columns()
            
            print("\n🧹 PASS 4C: Cleaning up field boundaries...")
            self._cleanup_field_boundaries()

            # v35.42: Adjust fields relative to detected header bars (thin-field nuke)
            print("\n🔧 PASS 4C0: Adjusting fields to header bars (thin-field cleanup)")
            self._adjust_fields_to_header_bars()
            
            print("\n� PASS 4C1: Removing fields from colored headers...")
            self._remove_colored_header_fields()

            # v35.42: Remove checkbox widgets that were present inside tables on pages
            # where the visual grid is treated as text-only (no auto-checkboxes)
            print("\n� PASS 4C1b: Removing checkbox widgets inside text-only grids...")

            # v35.42: Ensure Heat Pump Tune-Up checklist fields exist (some were removed by header cleanup)
            print("\n� PASS 4C1c: Ensuring Heat Pump Checklist fields...")

            # ---- v22 ENHANCED MODE (conservative scaffolding) ----
            if self.enhanced_mode and self.enhanced_mode != 'off':
                print(f"\n🔬 PASS 4F: Running enhanced detection (mode={self.enhanced_mode})")
                all_candidates = []
                for p in range(self.page_count):
                    self._per_page_calibration(p)
                    if self._should_run_enhanced_on_page(p):
                        cands = self._collect_enhanced_candidates(p)
                        all_candidates.extend(cands)
                        if self.enhanced_mode == 'conservative':
                            applied = self._apply_enhanced_candidates(cands, threshold=0.90)
                            if applied:
                                print(f"   Applied {applied} conservative enhanced fields on page {p + 1}")
                self.enhanced_candidates = all_candidates
                if self.candidate_json:
                    self._write_candidate_json(self.enhanced_candidates, self.candidate_json)
                if self.qa_overlay:
                    self._generate_candidate_overlay(self.enhanced_candidates, self.qa_overlay)
                print(f"   Enhanced candidates collected: {len(self.enhanced_candidates)}")

            
            print("\n🏷️  PASS 4E: Enriching checkbox labels from beside text...")
            self._enrich_checkbox_labels_from_beside_text()
            
            print("\n🔘 PASS 5: Identifying radio button groups...")
            self._identify_radio_groups()
            
            print("\n🏷️  PASS 5A: Refining horizontal checkbox labels...")
            self._refine_horizontal_checkbox_labels()
            
            print("\n🏷️  PASS 5B: Assigning header-based checkbox names...")
            self._assign_header_based_checkbox_names()
            
            # PASS 5C: Split fields on separators (v21.75)
            # Must run before merging to prevent re-merging
            print("\n✂️  PASS 5C: Splitting fields on separators...")
            self._split_fields_on_separators()
            
            print("\n🛡️  PASS 6: Resolving all field conflicts (Tiered Engine)...")
            if self.settings.get('enable_duplicate_fields', False) == True:
                 print("   [Clean] Skipping conflict resolution (enable_duplicate_fields=True)")
                 self._merge_adjacent_fields()
            else:
                 self._resolve_all_conflicts()
            
            # v36.5: UNIVERSAL - Detect and split merged labels (merged table cells)
            # Must run AFTER conflict resolution so forced fields are preserved.
            self._split_merged_multi_label_fields()
            
            # PASS 6C: Extend fields to form line endpoints (v21.5)
            print("\n📏 PASS 6C: Extending fields to form line endpoints...")
            self._extend_fields_to_line_endpoints()
            
            # v25.03: Disabling snapping which causes boundary violations (e.g. Page 2 Hero overlap)
            # print("\n📏 PASS 6D: Snapping fields to visual boxes...")
            # self._snap_fields_to_visual_boxes()

            # v36.5: UNIVERSAL - Hero Area Cleanup
            # Remove short, suspected residue fields in the top "hero" area of any page.
            to_remove = []
            for i, field in enumerate(self.text_fields):
                h = field['y1'] - field['y0']
                # If it's a short field in the top 100px and looks like a generic line-segment-field
                if field['y0'] < 100 and 'Line_Field' in field.get('name', '') and h < 20:
                    to_remove.append(i)
            if to_remove:
                self.text_fields = [f for idx, f in enumerate(self.text_fields) if idx not in to_remove]
                print(f"   v22.21: Removed {len(to_remove)} residual short Line_Fields from Page 1")
        
            # GENERIC FIXES for Tables and Line Fields (v22.21: Disabled - too unstable)
            # if self.debug_mode:
            #     print("🧠 PASS 6E: Inferring missing table rows from labels...")
            self._infer_missing_table_rows_from_labels()

            # if self.debug_mode:
            #     print("↕️  PASS 6F: Expanding fields vertically to fill gaps...")
            # v25.01: Disabling experimental expansion which causes UPWARD overlaps
            # self._expand_fields_vertically_based_on_spacing()

            # v30.01/31: Removed forced bottom narrative restoration
            # Keep behavior generic with no page-specific injected fields.

            # PASS 7: Create the PDF with all fields
            print("\n📝 PASS 7: Creating fillable PDF...")
            
            
            self._create_fillable_pdf()
            
            # PASS 8: Fix font encoding (initial)
            print("\n🔤 PASS 8: Fixing font encoding...")
            self._fix_font_encoding()
            
            # PASS 9: Create radio button groups
            print("\n🔘 PASS 9: Creating radio button groups...")
            self._create_radio_groups()
            
            # PASS 10: Generate QA overlays
            print("\n🎨 PASS 9: Generating QA overlays...")
            self._generate_overlays()
            
            self._print_summary()
            
            # PASS 10: Add JavaScript formatting
            print("\n📜 PASS 10: Adding JavaScript formatting...")
            self._add_javascript_formatting()
            
            # PASS 12: Final font encoding & NeedAppearances flag
            print("\n🔤 PASS 12: Applying final AcroForm settings...")
            self._fix_font_encoding()
        finally:
            if self.pdf:
                self.pdf.close()
                self.pdf = None
        
        return self.output_pdf
    
    # ========== EXISTING FORM FIELD DETECTION ==========
    
    def _transform_storage_to_visual(self, page_num: int, x0: float, y0: float, 
                                          x1: float, y1: float) -> tuple:
        """
        Reverse of _transform_coords_for_rotation.
        Maps storage (pikepdf) to visual (pdfplumber) coordinates.
        """
        rotation = self.page_rotations.get(page_num, 0)
        pw, ph = self.page_mediaboxes.get(page_num, (612, 792))
        
        # 1. Inverse Rotation mapping
        if rotation == 90:
            # Storage (x, y) -> Visual (pw - y, x)
            rx0, ry0, rx1, ry1 = pw - y1, x0, pw - y0, x1
        elif rotation == 180:
            # Storage (x, y) -> Visual (pw - x, ph - y)
            rx0, ry0, rx1, ry1 = pw - x1, ph - y1, pw - x0, ph - y0
        elif rotation == 270:
            # Storage (x, y) -> Visual (y, ph - x)
            rx0, ry0, rx1, ry1 = y0, ph - x1, y1, ph - x0
        else:
            rx0, ry0, rx1, ry1 = x0, y0, x1, y1
            
        # 2. Flip Y from bottom-up to top-down
        return (rx0, ph - ry1, rx1, ph - ry0)

    def _detect_existing_form_fields(self):
        """
        Detect existing form fields in the source PDF using pikepdf.
        Scans both document-level AcroForm fields and page-level annotations.
        """
        try:
            with pikepdf.open(str(self.input_pdf)) as pdf:
                # First, scan document-level AcroForm fields
                if '/AcroForm' in pdf.Root:
                    acroform = pdf.Root.AcroForm
                    if '/Fields' in acroform:
                        self._scan_acroform_fields(pdf, acroform.Fields)
                
                # Then scan page-level annotations (for any fields not in AcroForm)
                for page_num, page in enumerate(pdf.pages):
                    if '/Annots' not in page:
                        continue
                    
                    for annot in page.Annots:
                        if annot.get('/Subtype') == '/Widget':
                            ft = annot.get('/FT')
                            # Check parent if FT is missing on child (common in radio groups)
                            parent = annot.get('/Parent')
                            if not ft and parent:
                                ft = parent.get('/FT')
                                
                            if ft == '/Btn':
                                # Check if it's a checkbox vs radio
                                ff = int(annot.get('/Ff', 0))
                                if not ff: # Might be in parent
                                    ff = int(parent.get('/Ff', 0)) if parent else 0
                                
                                is_radio = bool(ff & (1 << 15))
                                if not is_radio:
                                    rect = annot.get('/Rect')
                                    if rect:
                                        # Transform to visual coords for collision detection
                                        v_coords = self._transform_storage_to_visual(
                                            page_num, float(rect[0]), float(rect[1]), 
                                            float(rect[2]), float(rect[3])
                                        )
                                        self.existing_checkboxes.append({
                                            'page': page_num,
                                            'x': v_coords[0],
                                            'y': v_coords[1],
                                            'width': v_coords[2] - v_coords[0],
                                            'height': v_coords[3] - v_coords[1],
                                            'name': str(annot.get('/T', ''))
                                        })
            
            if self.existing_checkboxes:
                print(f"   ℹ️  Source PDF has {len(self.existing_checkboxes)} existing checkboxes")
                
        except Exception as e:
            print(f"   ⚠️  Could not detect existing fields: {e}")
    
    def _scan_acroform_fields(self, pdf, fields, page_cache=None):
        """
        Recursively scan AcroForm fields for button fields.
        """
        if page_cache is None:
            page_cache = {}
            
        for field in fields:
            ft = field.get('/FT')
            if ft == '/Btn':
                # Check if it's a checkbox vs radio
                ff = int(field.get('/Ff', 0))
                is_radio = bool(ff & (1 << 15))
                if not is_radio:
                    # Find which page this field is on
                    page_num = self._find_field_page(pdf, field, page_cache)
                    if page_num is None:
                        # If no /P reference, try to find the field on any page by matching properties
                        page_num = self._find_field_page_by_search(pdf, field, page_cache)
                    
                    if page_num is not None:
                        rect = field.get('/Rect')
                        if rect:
                            # Transform to visual coords for collision detection
                            v_coords = self._transform_storage_to_visual(
                                page_num, float(rect[0]), float(rect[1]), 
                                float(rect[2]), float(rect[3])
                            )
                            self.existing_checkboxes.append({
                                'page': page_num,
                                'x': v_coords[0],
                                'y': v_coords[1],
                                'width': v_coords[2] - v_coords[0],
                                'height': v_coords[3] - v_coords[1],
                                'name': str(field.get('/T', ''))
                            })
            
            # Recursively scan children (for form hierarchies)
            if '/Kids' in field:
                self._scan_acroform_fields(pdf, field.Kids, page_cache)
    
    def _find_field_page(self, pdf, field, page_cache):
        """
        Find which page a field belongs to by checking its reference.
        """
        try:
            # Get the field's object reference
            field_ref = field._obj
            if hasattr(field_ref, 'objgen'):
                obj_id = field_ref.objgen[0]
                
                # Check cache first
                if obj_id in page_cache:
                    return page_cache[obj_id]
                
                # Scan pages to find which one contains this field
                for page_num, page in enumerate(pdf.pages):
                    if '/Annots' in page:
                        for annot in page.Annots:
                            annot_ref = annot._obj
                            if hasattr(annot_ref, 'objgen') and annot_ref.objgen[0] == obj_id:
                                page_cache[obj_id] = page_num
                                return page_num
            
            # If we can't find the page, try to get it from the field's page reference
            if '/P' in field:
                page_ref = field['/P']
                if hasattr(page_ref, '_obj'):
                    page_obj = page_ref._obj
                    for page_num, page in enumerate(pdf.pages):
                        if page._obj == page_obj:
                            return page_num
                            
        except Exception:
            pass
        
        return None
    
    def _find_field_page_by_search(self, pdf, field, page_cache):
        """
        Find field page by searching all pages for matching annotations.
        Used when field has no /P reference.
        """
        try:
            field_name = str(field.get('/T', ''))
            field_rect = field.get('/Rect')
            
            if not field_name and not field_rect:
                return None
            
            # Search all pages for matching annotation
            for page_num, page in enumerate(pdf.pages):
                if '/Annots' not in page:
                    continue
                    
                for annot in page.Annots:
                    if annot.get('/Subtype') == '/Widget':
                        # Check if names match
                        annot_name = str(annot.get('/T', ''))
                        if field_name and annot_name == field_name:
                            return page_num
                        
                        # Check if rects match (as fallback)
                        if field_rect and not field_name:
                            annot_rect = annot.get('/Rect')
                            if annot_rect and self._rects_match(field_rect, annot_rect):
                                return page_num
                                
        except Exception:
            pass
        
        return None
    
    def _rects_match(self, rect1, rect2, tolerance=1.0):
        """Check if two rectangles match within tolerance."""
        try:
            if len(rect1) != 4 or len(rect2) != 4:
                return False
            
            for i in range(4):
                if abs(float(rect1[i]) - float(rect2[i])) > tolerance:
                    return False
            return True
        except:
            return False
    
    def _cache_page_info(self):
        """Cache page rotation and dimensions using pikepdf."""
        with pikepdf.open(str(self.input_pdf)) as pdf:
            self.page_count = len(pdf.pages)
            for page_num in range(self.page_count):
                page = pdf.pages[page_num]
                # Cache rotation (0, 90, 180, 270)
                rotation = int(page.get('/Rotate', 0))
                self.page_rotations[page_num] = rotation
                
                # Cache dimensions from MediaBox [x0, y0, x1, y1]
                mbox = page.MediaBox
                w = float(mbox[2]) - float(mbox[0])
                h = float(mbox[3]) - float(mbox[1])
                self.page_mediaboxes[page_num] = (w, h)
                self.page_widths[page_num] = w
                self.page_heights[page_num] = h
    
    def _transform_coords_for_rotation(self, page_num: int, x0: float, y0: float, 
                                        x1: float, y1: float) -> tuple:
        """
        Transform visual coordinates (from pdfplumber) to storage coordinates for pikepdf widgets.
        
        For rotated pages, pdfplumber gives coordinates in "visual" space (how the page appears),
        but PDF storage coordinates need to account for rotation.
        """
        rotation = self.page_rotations.get(page_num, 0)
        pw, ph = self.page_mediaboxes.get(page_num, (612, 792))
        
        # NOTE: pdfplumber uses TOP-LEFT origin (y=0 at top).
        # pikepdf /Rect uses BOTTOM-LEFT origin (y=0 at bottom).
        
        # 1. Flip Y from top-down to bottom-up (pikepdf default)
        y0_bu = ph - y1
        y1_bu = ph - y0
        
        if rotation == 0:
            return (x0, y0_bu, x1, y1_bu)
        
        # 2. Handle rotation if needed
        # (This depends on whether the /Rect needs to be pre-rotated or if Acrobat handles it)
        # Most modern PDF tools expect /Rect in "visual" bottom-up space, 
        # but some older ones or specific edge cases might differ.
        # v21 logic upgrade: generic rotation handling
        
        if rotation == 90:
            # Visual (x, y_bu) -> Storage (y_bu, pw - x)
            return (y0_bu, pw - x1, y1_bu, pw - x0)
        elif rotation == 180:
            # Visual (x, y_bu) -> Storage (pw - x, ph - y_bu)
            return (pw - x1, ph - y1_bu, pw - x0, ph - y0_bu)
        elif rotation == 270:
            # Visual (x, y_bu) -> Storage (ph - y_bu, x)
            return (ph - y1_bu, x0, ph - y0_bu, x1)
            
        return (x0, y0_bu, x1, y1_bu)

    def _detect_all_checkboxes(self):
        """Unified pass for all checkbox detection types."""
        self._detect_checkboxes_character()
        self._detect_checkboxes_vector()
        self._detect_checkboxes_curves()
        self._detect_checkboxes_line_boxes()
        print(f"   ✅ Done: Found {len(self.checkboxes)} total unique checkboxes")
    
    def _detect_checkboxes_vector(self):
        """Detect checkboxes from vector squares (4-15pt) using pdfplumber."""
        for page_num in range(self.page_count):
            page_checkboxes = 0
            
            # Use cached rects from pdfplumber pass
            rects = self.page_rects.get(page_num, [])
            for rect in rects:
                w = float(rect['width'])
                h = float(rect['height'])
                
                # Check if it's a small square (checkbox)
                if (self.CHECKBOX_MIN_SIZE <= w <= self.CHECKBOX_MAX_SIZE and
                    self.CHECKBOX_MIN_SIZE <= h <= self.CHECKBOX_MAX_SIZE and
                    abs(w - h) <= self.CHECKBOX_SIZE_TOLERANCE):
                    
                    # v33.49: Removed solid filled skip. Replaced with keyword rejection.
                    # This allows Yes/No boxes on P2 to be detected.

                    # v36.5: GENERIC EXCLUSION FOR VECTOR CHECKBOXES IN TEXT COLUMNS
                    # Check if this checkbox aligns with a column header that implies text/data entry
                    # (e.g., Score, Point, Value, Quantity, Number)
                    col_words = self._get_words_in_bbox(page_num, (float(rect['x0'])-20, 0, float(rect['x0'])+float(rect['width'])+20, float(rect['top'])), overlap_threshold=0.01)
                    is_text_data_column = False
                    
                    text_col_keywords = ['score', 'point', 'value', 'qty', 'quantity', 'number', '#', 'count', 'amount']
                    for word_obj in col_words:
                        txt = word_obj['text'].lower()
                        if any(kw in txt for kw in text_col_keywords):
                             is_text_data_column = True
                             break
                    
                    if is_text_data_column:
                        print(f"   [TextColSkip] Vector checkbox at ({float(rect['x0']):.1f},{float(rect['top']):.1f}) skipped (Text Data column)")
                        continue

                    # pdfplumber uses 'top' (top-down visual)
                    if self._add_checkbox(page_num, float(rect['x0']), float(rect['top']), w, h, 'vector'):
                        page_checkboxes += 1
            
            if page_checkboxes > 0:
                print(f"   Page {page_num + 1}: {page_checkboxes} vector checkboxes")
    
    def _detect_checkboxes_character(self):
        """Detect checkboxes from Unicode ☐ characters and Wingdings fonts."""
        for page_num in range(self.page_count):
            page = self.pdf.pages[page_num]
            chars = page.chars
            page_checkboxes = 0
            
            for char in chars:
                is_checkbox = False
                char_text = char.get('text', '')
                font_name = (char.get('fontname') or '').lower()
                
                # v33.16: Check for standard checkbox characters: ☐ ☑ ☒ □ ■
                # Added \uf063 (Private Use Area checkbox common in forms)
                if char_text in ['☐', '☑', '☒', '□', '■', '\uf06f', '\uf0fe', '\uf063']:
                    is_checkbox = True
                
                # Check for CID-encoded characters (e.g., "(cid:133)")
                # pdfplumber sometimes returns CID notation for embedded fonts
                elif char_text.startswith('(cid:') and char_text.endswith(')'):
                    try:
                        cid_num = int(char_text[5:-1])
                        # CID 133 (0x85) is a common Wingdings2 checkbox
                        # Also check other common checkbox CIDs
                        if cid_num in [133, 168, 113, 114, 163, 61603]:
                            is_checkbox = True
                    except ValueError:
                        pass
                
                # Check for Wingdings/Wingdings2 checkboxes
                # - 0x85, 0x6F, 0xA8, 0x71, 0x72: Wingdings empty/checked boxes
                # - 0xF0A3 (61603): Wingdings2 empty checkbox (used in RECO forms)
                # - 0xA3 (163): Sometimes the high byte is stripped
                # - 0xF063 (61539): Private Use Area checkbox common in forms
                elif 'wingding' in font_name or 'webdings' in font_name or 'advp' in font_name:
                    # ord() only works on single characters
                    char_ord = ord(char_text[0]) if char_text and len(char_text) == 1 else 0
                    if char_ord in [0x85, 0x6F, 0xA8, 0x71, 0x72, 0xF0A3, 0xA3, 163, 61603, 99, 111, 0xF063, 61539]:
                        is_checkbox = True

                # Check for Zapf Dingbats checkboxes
                # 'o', 'p', 'q' (111, 112, 113) are commonly squares
                elif 'zapf' in font_name:
                    char_ord = ord(char_text[0]) if char_text and len(char_text) == 1 else 0
                    if char_ord in [0x6F, 0x70, 0x71, 111, 112, 113]:
                        is_checkbox = True
                
                if is_checkbox:
                    x = float(char['x0'])
                    y = float(char['top'])
                    w = float(char['x1']) - x
                    h = float(char['bottom']) - y
                    
                    # v21.50: Use ACTUAL character dimensions for centering
                    # Previously used max(w,h) for BOTH width and height, which caused
                    # horizontal misalignment when checkbox chars are taller than wide
                    # (e.g., □ char at 10.9x18pt would be recorded as 18x18, shifting right)
                    
                    # Avoid duplicates with vector detection
                    if self._add_checkbox(page_num, x, y, w, h, 'character'):
                        page_checkboxes += 1
            
            if page_checkboxes > 0:
                print(f"   Page {page_num + 1}: {page_checkboxes} character checkboxes")
        
        print(f"   Total: {len(self.checkboxes)} checkboxes")

    def _detect_checkboxes_line_boxes(self):
        """Detect checkboxes drawn as small line-based boxes (no rect objects).
        
        v33.33: Improved algorithm that matches Y-pairs by segment count similarity,
        not just strict adjacency. This handles grids where header rows (with fewer
        wide segments) are interspersed with checkbox rows (many narrow segments).
        """
        total_added = 0
        for page_num in range(self.page_count):
            h_lines = []
            v_lines = []
            for line in self.page_lines.get(page_num, []):
                x0, x1 = float(line.get('x0', 0)), float(line.get('x1', 0))
                y0, y1 = float(line.get('top', 0)), float(line.get('bottom', 0))
                if abs(y0 - y1) < 2:
                    h_lines.append((min(x0, x1), (y0 + y1) / 2, max(x0, x1)))
                elif abs(x0 - x1) < 2:
                    v_lines.append(((x0 + x1) / 2, min(y0, y1), max(y0, y1)))

            # Also consider thin rects as lines
            for rect in self.page_rects.get(page_num, []):
                rx0, rx1 = float(rect.get('x0', 0)), float(rect.get('x1', 0))
                ry0, ry1 = float(rect.get('top', 0)), float(rect.get('bottom', 0))
                w = abs(rx1 - rx0)
                h = abs(ry1 - ry0)
                if h < 2 and w > 6:
                    h_lines.append((min(rx0, rx1), (ry0 + ry1) / 2, max(rx0, rx1)))
                elif w < 2 and h > 6:
                    v_lines.append(((rx0 + rx1) / 2, min(ry0, ry1), max(ry0, ry1)))

            # Group h_lines by Y position
            h_by_y = {}
            for hx0, hy, hx1 in h_lines:
                y_key = round(hy, 0)
                if y_key not in h_by_y:
                    h_by_y[y_key] = []
                h_by_y[y_key].append((hx0, hx1))
            
            sorted_ys = sorted(h_by_y.keys())
            
            # v33.33: Find pairs where both rows have SIMILAR segment counts
            # and spacing is checkbox-sized. Look ahead up to 3 positions.
            valid_pairs = []
            for i in range(len(sorted_ys)):
                y_top = sorted_ys[i]
                top_count = len(h_by_y[y_top])
                
                # Look ahead to find matching bottom row
                for j in range(i + 1, min(i + 4, len(sorted_ys))):
                    y_bot = sorted_ys[j]
                    bot_count = len(h_by_y[y_bot])
                    row_height = y_bot - y_top
                    
                    # Skip if spacing not checkbox-sized
                    if row_height < 6 or row_height > 40:
                        continue
                    
                    # Skip if segment counts differ significantly (e.g., 27 vs 3)
                    # Allow some variance (within 50% of each other)
                    min_count = min(top_count, bot_count)
                    max_count = max(top_count, bot_count)
                    if min_count < 3:
                        continue  # Too few segments for checkbox detection
                    if max_count > min_count * 2:
                        continue  # Too different (e.g., 27 vs 3)
                    
                    valid_pairs.append((y_top, y_bot))
                    break  # Found a match, move to next top row
            
            page_added = 0
            
            # For each valid pair, find matching column segments
            for y_top, y_bot in valid_pairs:
                top_segs = h_by_y.get(round(y_top, 0), [])
                bot_segs = h_by_y.get(round(y_bot, 0), [])
                
                # Match segments by X overlap
                for tx0, tx1 in top_segs:
                    for bx0, bx1 in bot_segs:
                        x_left = max(tx0, bx0)
                        x_right = min(tx1, bx1)
                        w = x_right - x_left
                        if w < 6 or w > 40:
                            continue
                        
                        h = y_bot - y_top
                        
                        # Check for vertical lines at boundaries
                        has_left = any(abs(vx - x_left) < 2 and vy0 <= y_top + 1 and vy1 >= y_bot - 1 
                                      for vx, vy0, vy1 in v_lines)
                        has_right = any(abs(vx - x_right) < 2 and vy0 <= y_top + 1 and vy1 >= y_bot - 1 
                                       for vx, vy0, vy1 in v_lines)
                        if not (has_left and has_right):
                            continue

                        # v33.33: Avoid TRUE overlaps with already-detected checkboxes
                        if self._add_checkbox(page_num, x_left, y_top, w, h, 'line_box'):
                            page_added += 1

            if page_added > 0:
                total_added += page_added
                print(f"   Page {page_num + 1}: {page_added} line-box checkboxes")

    def _predetect_box_entry_areas(self):
        """
        Pre-detect box entry areas (account number rows) BEFORE checkbox detection.
        
        These areas contain 10+ uniformly-spaced small cells used for digit entry.
        Checkboxes should NOT be detected in these areas - they become text fields.
        """
        if not self.page_tables:
            # Need to pre-load tables
            self._preload_structural_data()
        
        for page_num in range(self.page_count):
            tables = self.page_tables.get(page_num, [])
            if not tables:
                continue
            
            for table in tables:
                cells = table.cells or []
                if len(cells) < 10:
                    continue
                
                # Group cells by Y position (same row)
                from collections import defaultdict
                rows = defaultdict(list)
                for c in cells:
                    if c and len(c) >= 4:
                        y_key = round(c[1] / 3) * 3  # Group by 3pt
                        rows[y_key].append(c)
                
                for y_key, row_cells in rows.items():
                    if len(row_cells) < 10:  # Need 10+ cells for account number pattern
                        continue
                    
                    # Sort by x position
                    row_cells.sort(key=lambda c: c[0])
                    
                    # Check if cells are uniformly sized (small)
                    widths = [c[2] - c[0] for c in row_cells]
                    avg_width = sum(widths) / len(widths)
                    
                    # Account number cells are typically 10-18pt wide
                    if not (8 <= avg_width <= 22):
                        continue

                    # Check uniformity (variance should be low)
                    max_deviation = max(abs(w - avg_width) for w in widths)
                    if max_deviation > 5:  # Allow 5pt deviation
                        continue

                    # v33.40: Account number boxes are NARROW (zip codes, SSNs, accounts)
                    # If cells are > 30pt wide, it's likely a normal table row, not a box entry area.
                    if avg_width > 30:
                        continue

                    # This is a box entry row! Store its bounding box
                    x0 = min(c[0] for c in row_cells)
                    y0 = min(c[1] for c in row_cells)
                    x1 = max(c[2] for c in row_cells)
                    y1 = max(c[3] for c in row_cells)
                    self.box_entry_areas.append({'page': page_num, 'x0': x0, 'y0': y0, 'x1': x1, 'y1': y1})

    # ---------------------- ENHANCED MODE HELPERS (v22) ----------------------
    def _per_page_calibration(self, page_num: int):
        """Compute simple per-page stats used to gate enhanced detection."""
        if page_num in self.page_stats:
            return self.page_stats[page_num]
        stats = {}
        try:
            plumber_page = self.pdf.pages[page_num]
            chars = plumber_page.chars or []
            sizes = [float(c.get('size', c.get('height', 0))) for c in chars if c.get('size') or c.get('height')]
            stats['avg_glyph_height'] = (sum(sizes) / len(sizes)) if sizes else 0
            stats['char_count'] = len(chars)
            stats['line_count'] = len(self.page_lines.get(page_num, []))
            stats['table_cell_count'] = sum(len(t.cells or []) for t in self.page_tables.get(page_num, []))
            stats['existing_field_count'] = self._page_existing_field_count(page_num)
        except Exception:
            stats = {'avg_glyph_height': 0, 'char_count': 0, 'line_count': 0, 'table_cell_count': 0, 'existing_field_count': 0}
        self.page_stats[page_num] = stats
        return stats

    def _page_existing_field_count(self, page_num: int) -> int:
        cnt = 0
        for f in self.text_fields + self.checkboxes + self.radio_groups:
            if f.get('page') == page_num:
                cnt += 1
        return cnt

    def _should_run_enhanced_on_page(self, page_num: int) -> bool:
        """Decide whether to run enhanced detectors on this page (conservative gating)."""
        if self.enhanced_mode == 'aggressive':
            return True
        stats = self._per_page_calibration(page_num)
        existing = stats.get('existing_field_count', 0)
        expected = max(1, int(stats.get('table_cell_count', 0) * 0.25 + stats.get('line_count', 0) * 0.15))
        # Run if sparse compared to structural expectations
        return existing < expected or existing < 2

    def _collect_enhanced_candidates(self, page_num: int) -> List[Dict]:
        """Collect high-confidence candidate fields (conservative)."""
        candidates = []
        plumber_page = self.pdf.pages[page_num]
        # Method A: empty table cells
        tables = self.page_tables.get(page_num, [])
        for table in tables:
            cells = table.cells or []
            for c in cells:
                try:
                    x0, y0, x1, y1 = c[0], c[1], c[2], c[3]
                except Exception:
                    continue
                # Skip tiny cells
                if (x1 - x0) < 12 or (y1 - y0) < 10:
                    continue
                # Skip cells with any text inside
                txt = ''.join(w.get('text','') for w in plumber_page.extract_words() if float(w['x0']) >= x0 - 1 and float(w['x1']) <= x1 + 1 and float(w['top']) >= y0 - 1 and float(w['bottom']) <= y1 + 1)
                if txt.strip():
                    continue
                # Check overlap with existing fields
                overlaps = any(self._rects_overlap((x0,y0,x1,y1),(f.get('x0',0),f.get('y0',0),f.get('x1',0),f.get('y1',0))) for f in self.text_fields + self.checkboxes)
                if overlaps:
                    continue
                candidates.append({'page': page_num, 'x0': x0, 'y0': y0, 'x1': x1, 'y1': y1, 'type': 'table_cell', 'score': 0.95, 'source': 'enhanced_table_cell'})
        # Method B: short underlines/form lines not already captured
        lines = self.page_lines.get(page_num, [])
        for ln in lines:
            # ln expected to have x0,x1,top,bottom
            x0 = ln.get('x0') if isinstance(ln, dict) else ln[0]
            x1 = ln.get('x1') if isinstance(ln, dict) else ln[2]
            top = ln.get('top') if isinstance(ln, dict) else ln[1]
            bottom = ln.get('bottom') if isinstance(ln, dict) else ln[3]
            length = x1 - x0
            if length < max(40, self.settings.get('min_underscore_length', 30)):
                continue
            # Avoid header bar lines
            if any(self._rects_overlap((x0, top, x1, bottom), (hb['x0'], hb['y0'], hb['x1'], hb['y1'])) for hb in (self.invoice_table_bboxes or [])):
                continue
            overlaps = any(self._rects_overlap((x0, top - 2, x1, bottom + 2),(f.get('x0',0),f.get('y0',0),f.get('x1',0),f.get('y1',0))) for f in self.text_fields + self.checkboxes)
            if overlaps:
                continue
            # Proximity to text below the line improves confidence
            nearby = self._find_nearby_text(page_num, x0, top, direction='below', max_dist=14)
            score = 0.90 if nearby else 0.80
            if score >= 0.90:
                candidates.append({'page': page_num, 'x0': x0, 'y0': top - 8, 'x1': x1, 'y1': bottom + 2, 'type': 'underline', 'score': score, 'source': 'enhanced_line'})
        return candidates

    def _apply_enhanced_candidates(self, candidates: List[Dict], threshold: float = 0.9) -> int:
        """Apply (create) candidates that exceed threshold as text fields.
        Returns number applied.
        """
        applied = 0
        for c in candidates:
            if c.get('score',0) < threshold:
                continue
            # Create field structure similar to other text_fields
            name = self._generate_unique_name('EnhancedField', c['page'])
            field = {
                'page': c['page'],
                'x0': c['x0'], 'y0': c['y0'], 'x1': c['x1'], 'y1': c['y1'],
                'name': name,
                'label': c.get('source','enhanced'),
                'format': 'text',
                'format_opts': {},
                'source': 'enhanced_auto_applied'
            }
            self.text_fields.append(field)
            applied += 1
        return applied

    def _write_candidate_json(self, candidates: List[Dict], path: str):
        try:
            with open(path, 'w') as fh:
                json.dump(candidates, fh, indent=2)
            print(f"   🔁 Candidate JSON written to: {path}")
        except Exception as e:
            print(f"   ⚠️  Candidate JSON write failed: {e}")

    def _generate_candidate_overlay(self, candidates: List[Dict], out_pdf: str):
        """Create a vector PDF overlay with candidate rectangles (no raster / poppler).
        Uses ReportLab to draw rectangles directly so there is no dependency on poppler.
        """
        try:
            from reportlab.pdfgen import canvas
            from reportlab.lib.colors import red

            c = canvas.Canvas(out_pdf)
            total_pages = self.page_count

            for pnum in range(total_pages):
                # Set page size to match source PDF page (points)
                page_w = self.page_widths.get(pnum, self.pdf.pages[pnum].width)
                page_h = self.page_heights.get(pnum, self.pdf.pages[pnum].height)
                c.setPageSize((page_w, page_h))
                c.setStrokeColor(red)
                c.setLineWidth(1.5)
                c.setFont("Helvetica", 8)

                cands = [ci for ci in candidates if ci.get('page') == pnum]
                for ci in cands:
                    x0 = float(ci['x0']); y0 = float(ci['y0']); x1 = float(ci['x1']); y1 = float(ci['y1'])
                    w = x1 - x0; h = y1 - y0
                    if w <= 0 or h <= 0:
                        continue
                    # Draw rectangle (ReportLab uses bottom-left origin like PDF)
                    c.rect(x0, y0, w, h, stroke=1, fill=0)
                    # Label near top-left of rect
                    try:
                        label = ci.get('type') or ci.get('label') or ci.get('source','candidate')
                        c.drawString(x0 + 3, y0 + h - 10, str(label)[:40])
                    except Exception:
                        pass

                c.showPage()
            c.save()
            print(f"   ✅ Vector QA overlay written to: {out_pdf}")
        except Exception as e:
            print(f"   ⚠️  Vector QA overlay generation failed: {e}")

    def _rects_overlap(self, r1: Tuple[float,float,float,float], r2: Tuple[float,float,float,float]) -> bool:
        x0a,y0a,x1a,y1a = r1
        x0b,y0b,x1b,y1b = r2
        # Simple intersection test
        return not (x1a <= x0b or x1b <= x0a or y1a <= y0b or y1b <= y0a)
                    

    def _is_in_quantity_column(self, page_num, x, y):
        """Generic heuristic to identify columns that are for data entry (ID, Quantity) 
        and should NOT have auto-fill checkboxes.
        """
        # 1. Check cached tables for the page
        tables = self.page_tables.get(page_num, [])
        page = self.pdf.pages[page_num]
        
        for table in tables:
            rows = table.rows
            if not rows: continue
            
            # Identify column horizontal range
            col_x0 = col_x1 = None
            for row in rows:
                if not hasattr(row, 'cells'): continue
                for cell in row.cells:
                    if not cell: continue
                    if cell[0] - 2 <= x <= cell[2] + 2:
                        col_x0, col_x1 = cell[0], cell[2]
                        break
                if col_x0 is not None: break
            
            if col_x0 is None: continue
            
            # Check headers (first 2 rows) for generic quantity keywords
            header_txt = ""
            for row in rows[:2]:
                if not hasattr(row, 'cells'): continue
                for cell in row.cells:
                    if not cell: continue
                    if cell[0] == col_x0:
                        try:
                            header_txt += (page.within_bbox(cell).extract_text() or "").lower() + " "
                        except: pass
            
            # Generic quantity/ID keywords (word boundaries/short strings only)
            # v22.92: Tightened to avoid blocking "Account Number" fields.
            qty_kws = ['#', 'no.', 'qty', 'count', 'ref', 'id', 'units']
            short_header = header_txt[:25].strip().lower()
            if any(kw == short_header or f" {kw} " in f" {short_header} " for kw in qty_kws):
                return True
                
            # 'number' keyword: Only reject if it's the dominant word in a short header
            if 'number' in short_header and len(short_header) < 12 and not any(ok in short_header for ok in ['account', 'phone', 'license']):
                return True
                
            # Column content check: if most cells are numeric or very short, it's a data col
            numeric_count = 0
            populated_cells = 0
            total_cells = 0
            for row in rows:
                if not hasattr(row, 'cells'): continue
                for cell in row.cells:
                    if not cell: continue
                    if cell[0] == col_x0:
                        total_cells += 1
                        try:
                            txt = (page.within_bbox(cell).extract_text() or "").strip()
                            if txt:
                                populated_cells += 1
                                if txt.isdigit() or len(txt) < 4:
                                    numeric_count += 1
                        except: pass
            
            # v22.96: Only block if significant portion of populated cells are numeric
            # AND we have at least some populated cells to judge by.
            if populated_cells > 2 and numeric_count / populated_cells > 0.8:
                return True
                
        return False

    def _add_checkbox(self, page_num: int, x: float, y: float, w: float, h: float, source: str) -> bool:
        """Add a checkbox to the detection list. Returns True if added."""
        # v35.19: Skip tally sheet pages entirely - they're for handwritten data, not checkboxes
        # The grid squares on tally sheets are data entry cells, not checkboxes
        if page_num in self.tally_sheet_pages:
            # print(f"      [TRACER] Page {page_num+1} {source} REJECTED - tally sheet page")
            return False
        
        # Check for duplicates first
        if self._is_duplicate_checkbox(page_num, x, y):
            print(f"      [TRACER] Page {page_num+1} {source} REJECTED as Duplicate at ({x:.1f},{y:.1f})")
            return False

        if y < 40: # Generic Top Margin: Reject fields too close to top edge (likely header text/numbers)
            print(f"      [TRACER] Page {page_num+1} {source} REJECTED by Header Area at y={y:.1f}")
            return False
        
        if self.box_entry_areas:
            cb_center_x = x + w / 2
            cb_center_y = y + h / 2
            for area in self.box_entry_areas:
                if (area['page'] == page_num and
                    area['x0'] <= cb_center_x <= area['x1'] and
                    area['y0'] <= cb_center_y <= area['y1']):
                    # v36.0: Allow line based checkboxes inside box entries (container boxes)
                    if source in ['line_box', 'line_box_v3']:
                        continue
                        
                    print(f"      [TRACER] Page {page_num+1} {source} REJECTED by BoxEntry area at y={y:.1f}")
                    return False
        
        # Heuristic: Block checkboxes in identified Quantity/ID columns
        if self._is_in_quantity_column(page_num, x, y):
            print(f"      [TRACER] Page {page_num+1} {source} REJECTED - Inside Quantity/ID column")
            return False
        
        # Find label
        label = self._find_nearby_text(page_num, x + w, y, direction='right', max_dist=18)
        if not label:
            label = self._find_label_above(page_num, x, y, w)
            
        # Icon Shield: Pattern-based rejection of checkboxes near Reference IDs (e.g. CA-L20)
        # These are usually just icons in the leftmost column
        if not label or len(label) < 2:
            try:
                # Look for nearby product code pattern
                scan_rect = (x - 60, y - 5, x + 5, y + h + 5)
                ref_text = (self.pdf.pages[page_num].within_bbox(scan_rect).extract_text() or "").strip()
                if re.search(r'[A-Z]{1,3}-\d{1,4}', ref_text):
                    print(f"      [TRACER] Page {page_num+1} {source} REJECTED - Near Reference ID icon '{ref_text}'")
                    return False
            except:
                pass
        
        # v33.45: KEYWORD REJECTION for misidentified text boxes (Apt No, City, etc.)
        if label:
            lbl_low = label.lower()
            # v33.51: Aggressive rejections for structural/text boxes misidentified as checkboxes.
            # Removed 'email', 'date', 'phone' from absolute reject - they can be valid choice options.
            anti_kws = ['apt', 'unit', 'suite', 'city', 'state', 'zip', 'no.', 'number', 'amount', 'manufacturer', 'model #', 'serial #', 'license']
            for kw in anti_kws:
                if kw in lbl_low:
                    # v33.65: Exception logic for valid checkboxes
                    # 1. 'city' is in 'electricity', 'capacity', 'velocity' -> These are valid
                    if kw == 'city' and any(valid in lbl_low for valid in ['electricity', 'capacity', 'velocity']):
                        continue
                    
                    # 2. 'unit' is in 'multi-family', 'townhome', 'home' -> These are valid house type options
                    if kw == 'unit' and any(valid in lbl_low for valid in ['family', 'home', 'townhome', 'rowhome', 'duplex']):
                        continue
                        
                    # 3. Special check: keep real checkboxes like "Yes/No" or "Pass/Fail"
                    if any(ok_kw in lbl_low for ok_kw in ['yes', 'no', 'complete', 'agree', 'other:', 'pass', 'fail']):
                        continue
                        
                    print(f"      [TRACER] Page {page_num+1} {source} REJECTED by keyword '{kw}' (label: '{label}')")
                    return False 
    
        # v33.78: Smart Task-Context Checkbox Filtering
        # Vector and character checkboxes are REAL checkboxes - always allow them
        if source not in ['vector', 'character']:
            col_key = (page_num, round(x / 15) * 15)
            current_count = self._checkbox_column_counts.get(col_key, 0)
            # v22.96: INCREASE LIMIT for tally sheets and trusted sources
            # Tally sheets (P17/18) can have 20+ rows of visual checkboxes.
            limit = 40 if page_num in self.tally_sheet_pages else 12
            if current_count >= limit:
                print(f"      [TRACER] Page {page_num+1} {source} REJECTED - Column spam at x={x:.1f} (count={current_count})")
                return False 
        
        cb_data = {
            'page': page_num,
            'x': x,
            'y': y,
            'width': w,
            'height': h,
            'label': label,
            'row_y': round(y),
            'source': source,
        }
        print(f"      [TRACER] Page {page_num+1} {source} ADDED at ({x:.1f},{y:.1f}) label='{label}'")
        self.checkboxes.append(cb_data)
        
        self.checkbox_positions.append({
            'page': page_num,
            'x0': x - 2,
            'y0': y - 2,
            'x1': x + w + 2,
            'y1': y + h + 2,
            'source': source
        })
        
        # v33.71: Update column count for spam prevention (15pt bands)
        col_key = (page_num, round(x / 15) * 15)
        self._checkbox_column_counts[col_key] = self._checkbox_column_counts.get(col_key, 0) + 1
        
        return True

    def _remove_checkboxes_over_images(self):
        """
        v22.76: Remove checkboxes that overlap with images.
        Images (photos, flyers, etc.) should not have checkboxes placed over them.
        """
        if not self.checkboxes:
            return
        
        removed = 0
        checkboxes_to_keep = []
        
        for cb in self.checkboxes:
            cb_page = cb['page']
            cb_x0 = cb['x']
            cb_y0 = cb['y']
            cb_x1 = cb_x0 + cb['width']
            cb_y1 = cb_y0 + cb['height']
            cb_area = cb['width'] * cb['height']
            
            overlaps_image = False
            for img in self.page_images.get(cb_page, []):
                img_x0 = float(img.get('x0', 0))
                img_y0 = float(img.get('top', 0))
                img_x1 = float(img.get('x1', 0))
                img_y1 = float(img.get('bottom', 0))
                
                # Check for overlap
                overlap_x0 = max(cb_x0, img_x0)
                overlap_y0 = max(cb_y0, img_y0)
                overlap_x1 = min(cb_x1, img_x1)
                overlap_y1 = min(cb_y1, img_y1)
                
                if overlap_x1 > overlap_x0 and overlap_y1 > overlap_y0:
                    overlap_area = (overlap_x1 - overlap_x0) * (overlap_y1 - overlap_y0)
                    if cb_area > 0 and overlap_area / cb_area > 0.5:
                        overlaps_image = True
                        break
            
            if overlaps_image:
                removed += 1
            else:
                checkboxes_to_keep.append(cb)
        
        if removed > 0:
            self.checkboxes = checkboxes_to_keep
            print(f"   ✂️ Removed {removed} checkboxes overlapping images")

    def _remove_checkboxes_in_text_columns(self):
        """
        v22.83: Remove checkboxes that appear in text-only table columns.
        
        On pages with dense data tables, checkboxes should only be in
        the Pass/Fail columns, not in the "Measures Installed", "Score", or 
        "Reason for Failure" columns.
        
        This detects tables with explicit checkbox header columns (Pass/Fail)
        and removes any checkboxes that are outside those columns.
        """
        if not self.checkboxes:
            return
        
        print("   🧹 Checking for checkboxes in text-only columns...")
        
        # Track removed checkboxes by id
        removed_set = set()
        
        for page_num in range(self.page_count):
            tables = self.page_tables.get(page_num, [])
            page_height = self.page_heights.get(page_num, 792)
            
            for table in tables:
                bbox = table.bbox
                if not bbox:
                    continue
                
                # Get header row to identify Pass/Fail columns
                rows = table.rows
                if not rows:
                    continue
                
                header_row = rows[0]
                if not hasattr(header_row, 'cells') or not header_row.cells:
                    continue
                
                # Identify checkbox columns by header text
                checkbox_column_ranges = []
                page = self.pdf.pages[page_num]
                
                for cell in header_row.cells:
                    if not cell:
                        continue
                    cx0, cy0, cx1, cy1 = cell
                    try:
                        crop = page.within_bbox((cx0+1, cy0+1, cx1-1, cy1-1))
                        text = (crop.extract_text() or "").strip().lower()
                        
                        # v22.89: These column headers indicate checkboxes are expected
                        # Use EXACT match on short keywords to avoid matching "failure" as "fail"
                        # Headers like "Reason for Failure" should NOT be checkbox columns
                        checkbox_keywords = ['pass', 'fail', 'yes', 'no', 'n/a', 'na', 'check', 'y', 'n']
                        is_checkbox_header = text in checkbox_keywords
                        if is_checkbox_header:
                            checkbox_column_ranges.append((cx0, cx1))
                    except:
                        pass
                
                if not checkbox_column_ranges:
                    continue  # No checkbox columns identified
                
                # Table boundaries in pdfplumber coords (top-down)
                table_x0, table_y0, table_x1, table_y1 = bbox
                # Convert to PDF coords
                table_pdf_y0 = page_height - table_y1
                table_pdf_y1 = page_height - table_y0
                
                # Check each checkbox on this page
                page_checkboxes = [cb for cb in self.checkboxes if cb['page'] == page_num]
                
                for cb in page_checkboxes:
                    cb_x = cb['x']
                    cb_y = cb['y']  # PDF coords
                    
                    # Check if checkbox is inside this table
                    if not (table_x0 <= cb_x <= table_x1 and table_pdf_y0 <= cb_y <= table_pdf_y1):
                        continue  # Not in this table
                    
                    # Check if checkbox is in a valid checkbox column
                    in_checkbox_column = False
                    for col_x0, col_x1 in checkbox_column_ranges:
                        if col_x0 - 5 <= cb_x <= col_x1 + 5:  # 5pt tolerance
                            in_checkbox_column = True
                            break

                    if not in_checkbox_column:
                        print(f"      ✂️ Removing checkbox '{cb.get('label', 'Unnamed')}' at x={cb_x:.1f} (not in Pass/Fail column)")
                        removed_set.add(id(cb))
        
        # Apply removal
        if removed_set:
            self.checkboxes = [cb for cb in self.checkboxes if id(cb) not in removed_set]
            print(f"   v22.83: Removed {len(removed_set)} checkboxes from non-checkbox columns")
        
        for page_num in range(self.page_count):
            tables = self.page_tables.get(page_num, [])
            page_height = self.page_heights.get(page_num, 792)
            
            for table in tables:
                bbox = table.bbox
                if not bbox:
                    continue
                
                rows = table.rows
                if not rows:
                    continue
                
                header_row = rows[0]
                if not hasattr(header_row, 'cells') or not header_row.cells:
                    continue
                
                # Identify checkbox columns
                checkbox_column_ranges = []
                page = self.pdf.pages[page_num]
                
                for cell in header_row.cells:
                    if not cell:
                        continue
                    cx0, cy0, cx1, cy1 = cell
                    try:
                        crop = page.within_bbox((cx0+1, cy0+1, cx1-1, cy1-1))
                        text = (crop.extract_text() or "").strip().lower()
                        
                        # v22.89: Use EXACT match on short checkbox keywords
                        checkbox_keywords = ['pass', 'fail', 'yes', 'no', 'n/a', 'na', 'check', 'y', 'n']
                        if text in checkbox_keywords:
                            checkbox_column_ranges.append((cx0, cx1))
                    except:
                        pass
                
                if not checkbox_column_ranges:
                    continue
                
                table_x0, table_y0, table_x1, table_y1 = bbox
                table_pdf_y0 = page_height - table_y1
                table_pdf_y1 = page_height - table_y0
                
                for i, cb in enumerate(self.checkboxes):
                    if cb['page'] != page_num:
                        continue
                    
                    cb_x = cb['x']
                    cb_y = cb['y']
                    
                    if not (table_x0 <= cb_x <= table_x1 and table_pdf_y0 <= cb_y <= table_pdf_y1):
                        continue
                    
                    in_checkbox_column = False
                    for col_x0, col_x1 in checkbox_column_ranges:
                        if col_x0 - 5 <= cb_x <= col_x1 + 5:
                            in_checkbox_column = True
                            break
                    
                    if not in_checkbox_column:
                        removed_set.add(i)
        
        # Rebuild checkbox list
        final_checkboxes = [cb for i, cb in enumerate(self.checkboxes) if i not in removed_set]
        actual_removed = len(self.checkboxes) - len(final_checkboxes)
        
        if actual_removed > 0:
            self.checkboxes = final_checkboxes
            print(f"   ✂️ Removed {actual_removed} checkboxes from text-only columns")
        
        pass

    def _remove_checkboxes_overlapping_box_entry(self):
        """
        Remove checkboxes that overlap with box_entry fields.
        
        This allows separated detection: checkboxes detect everything,
        box_entry detects account numbers, then we clean up conflicts.
        Box_entry wins because account number boxes shouldn't be checkboxes.
        """
        if not self.checkboxes or not self.text_fields:
            return
        
        # Find all box_entry fields
        box_entry_fields = [f for f in self.text_fields if f.get('source') == 'box_entry']
        if not box_entry_fields:
            return
        
        removed = 0
        checkboxes_to_keep = []
        
        for cb in self.checkboxes:
            cb_page = cb['page']
            cb_x = cb['x']
            cb_y = cb['y']
            cb_w = cb['width']
            cb_h = cb['height']
            cb_center_x = cb_x + cb_w / 2
            cb_center_y = cb_y + cb_h / 2
            
            overlaps_box_entry = False
            for f in box_entry_fields:
                if f['page'] != cb_page:
                    continue
                # Check if checkbox center is inside the box_entry field
                if (f['x0'] <= cb_center_x <= f['x1'] and 
                    f['y0'] <= cb_center_y <= f['y1']):
                    overlaps_box_entry = True
                    break
            
            if overlaps_box_entry:
                removed += 1
            else:
                checkboxes_to_keep.append(cb)
        
        if removed > 0:
            self.checkboxes = checkboxes_to_keep
            # Also update checkbox_positions
            self.checkbox_positions = [
                {'page': cb['page'], 'x0': cb['x']-2, 'y0': cb['y']-2, 
                 'x1': cb['x']+cb['width']+2, 'y1': cb['y']+cb['height']+2}
                for cb in self.checkboxes
            ]
            print(f"   ✂️ Removed {removed} checkboxes overlapping box_entry fields")

    def _cell_has_visual_checkbox(self, page_num: int, x0: float, y0: float, x1: float, y1: float) -> bool:
        """Return True if the cell contains visual cues of a checkbox.

        Checks for any existing detected checkbox in the area, small rects or
        small closed paths from page rects/lines that look like a checkbox.
        """
        cell_w = x1 - x0
        cell_h = y1 - y0
        
        # 1) Check if we've already detected a checkbox inside the cell
        for cb in getattr(self, 'checkboxes', []):
            if cb['page'] != page_num:
                continue
            cx = cb['x'] + cb['width'] / 2
            cy = cb['y'] + cb['height'] / 2
            if x0 - 2 <= cx <= x1 + 2 and y0 - 2 <= cy <= y1 + 2:
                return True

        # 2) Check page_rects for small filled rects inside cell (likely checkbox square)
        # BUT: Be more strict - the rect should be centered in the cell and reasonably sized
        rects = self.page_rects.get(page_num, [])
        for r in rects:
            rx0 = float(r.get('x0', r.get('x', 0)))
            ry0 = float(r.get('y0', r.get('y', 0)))
            rx1 = float(r.get('x1', r.get('x', rx0) + r.get('width', 0)))
            ry1 = float(r.get('y1', r.get('y', ry0) + r.get('height', 0)))
            rw = rx1 - rx0
            rh = ry1 - ry0
            # small square box folded into cell
            # v35.40/v35.42: Even more strict - reduce size limit and tighten centering
            # v22.96: RELAXED - Increase max size to 45 and loosen centering to 35% of cell
            if (x0 - 2 <= rx0 and rx1 <= x1 + 2 and y0 - 2 <= ry0 and ry1 <= y1 + 2
                    and 4 <= rw <= 45 and 4 <= rh <= 45 and abs(rw - rh) <= max(6, 0.25 * max(rw, rh))):
                # Check if the rect is centered OR just reasonably inside
                rect_center_x = (rx0 + rx1) / 2
                rect_center_y = (ry0 + ry1) / 2
                cell_center_x = (x0 + x1) / 2
                cell_center_y = (y0 + y1) / 2
                # v35.42: Tighter centering tolerance - must be within 25% of cell size
                # v22.96: Relaxed to 40% to catch slightly off-center boxes
                if abs(rect_center_x - cell_center_x) < cell_w * 0.40 and abs(rect_center_y - cell_center_y) < cell_h * 0.40:
                    return True

        # 3) Check for small square formed by lines (vertical/horizontal) inside cell
        # v35.40: More strict - only consider it a checkbox if we find a complete box formation
        # that is centered in the cell, not just any intersecting lines
        lines = self.page_lines.get(page_num, [])
        # Build quick lookup of small horizontal/vertical line segments inside cell
        h_segments = []
        v_segments = []
        for l in lines:
            try:
                lx0 = float(l.get('x0', l.get('x', 0)))
                lx1 = float(l.get('x1', l.get('x', lx0)))
                ly0 = float(l.get('top', l.get('y0', 0)))
                ly1 = float(l.get('bottom', l.get('y1', 0)))
            except Exception:
                continue
            w = abs(lx1 - lx0)
            h = abs(ly1 - ly0)
            if not (x0 - 4 <= (lx0 + lx1) / 2 <= x1 + 4 and y0 - 4 <= (ly0 + ly1) / 2 <= y1 + 4):
                continue
            # Horizontal line
            if h < 1 and w >= 4 and w <= 45:  # v22.96: Increased from 35 to 45
                h_segments.append((lx0, lx1, ly0))
            # Vertical line
            if w < 1 and h >= 4 and h <= 45:  # v22.96: Increased from 35 to 45
                v_segments.append((ly0, ly1, lx0))

        # If we have both h and v segments that form a box of small dims, consider it a visual checkbox
        # v35.40/v35.42: Check if the formed box is centered in the cell with tighter tolerance
        for hx0, hx1, hy in h_segments:
            for vx0, vx1, vx in v_segments:
                # Check if vertical x coordinate lies within horizontal span and vertical spans intersect horizontal y
                if hx0 - 2 <= vx <= hx1 + 2 and vx0 - 2 <= hy <= vx1 + 2:
                    box_w = min(hx1, x1) - max(hx0, x0)
                    box_h = min(vx1, y1) - max(vx0, y0)
                    if 4 <= box_w <= 45 and 4 <= box_h <= 45 and abs(box_w - box_h) <= max(8, 0.4 * max(box_w, box_h)):
                        # v22.96: Loosened centering check to 40% of cell size
                        box_center_x = (max(hx0, x0) + min(hx1, x1)) / 2
                        box_center_y = (max(vx0, y0) + min(vx1, y1)) / 2
                        cell_center_x = (x0 + x1) / 2
                        cell_center_y = (y0 + y1) / 2
                        if abs(box_center_x - cell_center_x) < cell_w * 0.40 and abs(box_center_y - cell_center_y) < cell_h * 0.40:
                            return True

        return False

    def _remove_checkboxes_in_table_cells(self) -> int:
        """Remove any pre-detected checkboxes that fall inside table cells.

        If a checkbox is found inside a table cell and the setting
        'disable_checkboxes_in_table_cells' is True (default), the checkbox
        will be removed and a text field covering the cell will be created.
        This method now handles both newly-detected checkboxes (self.checkboxes)
        and pre-existing widget checkboxes detected earlier (self.existing_checkboxes).
        Returns the number of removed/detected checkboxes converted.
        """
        removed = 0
        new_checkboxes = []

        # Process newly-detected checkboxes
        STRONG_CB_SOURCES = {'character', 'vector', 'line_box', 'curve'}
        for cb in getattr(self, 'checkboxes', []):
            cb_cx = cb['x'] + cb['width'] / 2
            cb_cy = cb['y'] + cb['height'] / 2
            contained = False
            matched_cell = None
            for cell in getattr(self, 'table_cells', []):
                if cell.get('page') != cb.get('page'):
                    continue
                if cell['x0'] <= cb_cx <= cell['x1'] and cell['y0'] <= cb_cy <= cell['y1']:
                    contained = True
                    matched_cell = cell
                    break
            if contained and self.settings.get('disable_checkboxes_in_table_cells', True):
                # First: Detect if this checkbox lies in a numeric/score column header area.
                # If the column header above contains 'score', 'point', or 'point value' then
                # this column should be numeric and should NOT contain checkboxes.
                try:
                    above_bbox = (matched_cell['x0'], max(0, matched_cell['y0'] - 60), matched_cell['x1'], matched_cell['y0'] + 1)
                    header_text = self._get_text_in_bbox(matched_cell['page'], above_bbox).lower()
                except Exception:
                    header_text = ''

                is_score_column = any(tok in header_text for tok in ('score', 'point value', 'point', 'point_value'))

                # If we have strong visual evidence for this checkbox (character/vector/line)
                # then PRESERVE the checkbox (do NOT convert it to a text field), unless it's
                # in a score/point column where checkboxes are logically invalid.
                if cb.get('source') in STRONG_CB_SOURCES and not is_score_column:
                    if self.debug_mode:
                        print(f"   [Keep] Preserving visual checkbox in table cell pg={cb.get('page')} src={cb.get('source')}")
                    new_checkboxes.append(cb)
                else:
                    # Either weak/no-visual checkbox OR it's in a score column: convert to a text field that fills this cell
                    name = self._generate_unique_name(f"Cell_{int(matched_cell['x0'])}_{int(matched_cell['y0'])}", matched_cell['page'])
                    # If this was a score column, label accordingly
                    src_tag = 'removed_checkbox_cell'
                    if is_score_column:
                        src_tag = 'removed_score_checkbox'
                        if self.debug_mode:
                            print(f"   [ScoreCol] Converting checkbox in Score column to text field at P{matched_cell['page']+1} header='{header_text[:20]}'")
                    self.text_fields.append({
                        'page': matched_cell['page'],
                        'x0': matched_cell['x0'] + 2, 'y0': matched_cell['y0'] + 2,
                        'x1': matched_cell['x1'] - 2, 'y1': matched_cell['y1'] - 2,
                        'name': name,
                        'source': src_tag
                    })
                    removed += 1
            else:
                new_checkboxes.append(cb)

        self.checkboxes = new_checkboxes
        # Rebuild checkbox_positions too
        self.checkbox_positions = [
            {'page': cb['page'], 'x0': cb['x'] - 2, 'y0': cb['y'] - 2, 'x1': cb['x'] + cb['width'] + 2, 'y1': cb['y'] + cb['height'] + 2}
            for cb in self.checkboxes
        ]

        # Process existing widget checkboxes discovered from source PDF
        # These are stored in self.existing_checkboxes with 'page','x','y','width','height','name'
        removed_existing = 0
        new_existing = []
        for ecb in getattr(self, 'existing_checkboxes', []):
            cb_cx = ecb['x'] + ecb['width'] / 2
            cb_cy = ecb['y'] + ecb['height'] / 2
            contained = False
            matched_cell = None
            for cell in getattr(self, 'table_cells', []):
                if cell.get('page') != ecb.get('page'):
                    continue
                if cell['x0'] <= cb_cx <= cell['x1'] and cell['y0'] <= cb_cy <= cell['y1']:
                    contained = True
                    matched_cell = cell
                    break
            if contained and self.settings.get('disable_checkboxes_in_table_cells', True):
                # If we can find visual evidence (lines/boxes) inside the widget bbox, PRESERVE it
                ecb_x0, ecb_y0 = ecb['x'], ecb['y']
                ecb_x1, ecb_y1 = ecb['x'] + ecb['width'], ecb['y'] + ecb['height']
                has_visual = False
                try:
                    has_visual = self._cell_has_visual_checkbox(ecb.get('page'), ecb_x0, ecb_y0, ecb_x1, ecb_y1)
                except Exception:
                    has_visual = False

                if has_visual:
                    # Also check whether the widget sits in a 'score' header column — if so, convert it
                    try:
                        above_bbox = (matched_cell['x0'], max(0, matched_cell['y0'] - 60), matched_cell['x1'], matched_cell['y0'] + 1)
                        header_text = self._get_text_in_bbox(matched_cell['page'], above_bbox).lower()
                    except Exception:
                        header_text = ''
                    is_score_column = any(tok in header_text for tok in ('score', 'point value', 'point', 'point_value'))

                    if is_score_column:
                        if self.debug_mode:
                            print(f"   [ScoreColExisting] Replacing existing widget checkbox in Score column with text field pg={ecb.get('page')} name={ecb.get('name')} header='{header_text[:20]}'")
                        name = self._generate_unique_name(f"Cell_{int(matched_cell['x0'])}_{int(matched_cell['y0'])}", matched_cell['page'])
                        self.text_fields.append({
                            'page': matched_cell['page'],
                            'x0': matched_cell['x0'] + 2, 'y0': matched_cell['y0'] + 2,
                            'x1': matched_cell['x1'] - 2, 'y1': matched_cell['y1'] - 2,
                            'name': name,
                            'source': 'removed_existing_score_widget'
                        })
                        if not hasattr(self, '_existing_checkboxes_to_remove'):
                            self._existing_checkboxes_to_remove = []
                        self._existing_checkboxes_to_remove.append(ecb)
                        removed_existing += 1
                    else:
                        if self.debug_mode:
                            print(f"   [KeepExisting] Preserving existing visual widget checkbox pg={ecb.get('page')} name={ecb.get('name')}")
                        new_existing.append(ecb)
                else:
                    name = self._generate_unique_name(f"Cell_{int(matched_cell['x0'])}_{int(matched_cell['y0'])}", matched_cell['page'])
                    # Create a text field placeholder to replace the widget
                    self.text_fields.append({
                        'page': matched_cell['page'],
                        'x0': matched_cell['x0'] + 2, 'y0': matched_cell['y0'] + 2,
                        'x1': matched_cell['x1'] - 2, 'y1': matched_cell['y1'] - 2,
                        'name': name,
                        'source': 'removed_existing_widget'
                    })
                    # Record it for later removal during PDF write
                    if not hasattr(self, '_existing_checkboxes_to_remove'):
                        self._existing_checkboxes_to_remove = []
                    self._existing_checkboxes_to_remove.append(ecb)
                    removed_existing += 1
            else:
                new_existing.append(ecb)

        self.existing_checkboxes = new_existing
        total_removed = removed + removed_existing
        if total_removed:
            print(f"   [Clean] Removed {removed} detected + {removed_existing} existing checkboxes inside table cells (converted to text fields)")
        return total_removed

    def _detect_square_grid_regions(self):
        """Detect repeated small square shapes arranged in a grid (visual checkbox grids).

        This is a generic heuristic: find many small rects/curves of similar size arranged
        into rows and columns and record bounding regions in self.square_grid_regions.
        Later, when deciding whether to create checkboxes, cells overlapping these regions
        can prefer text fields instead.
        """
        self.square_grid_regions = []
        for page_num in range(self.page_count):
            rects = list(self.page_rects.get(page_num, [])) + list(self.page_curves.get(page_num, []))
            # Collect small square candidates
            cand = []
            for r in rects:
                x0 = float(r.get('x0', r.get('x', 0)))
                x1 = float(r.get('x1', x0 + r.get('width', 0)))
                y0 = float(r.get('top', r.get('y0', 0)))
                y1 = float(r.get('bottom', r.get('y1', 0)))
                w = abs(x1 - x0)
                h = abs(y1 - y0)
                if 6 <= w <= 24 and 6 <= h <= 24 and abs(w - h) <= max(2, 0.15 * max(w, h)):
                    cand.append({'x0': x0, 'y0': y0, 'x1': x1, 'y1': y1, 'w': w, 'h': h})

            if len(cand) < 6:
                continue

            # Cluster candidates by proximity (grid-like spacing)
            # Group by approximate row (y) using 6pt tolerance
            rows = {}
            for c in cand:
                y_key = round(c['y0'] / 6) * 6
                rows.setdefault(y_key, []).append(c)
            # If we have many rows with consistent column counts, assume a grid
            row_counts = [len(v) for v in rows.values()]
            if not row_counts: continue
            median_cols = sorted(row_counts)[len(row_counts)//2]
            good_rows = [v for v in rows.values() if len(v) >= max(3, median_cols - 2)]
            if len(good_rows) < 2:
                continue

            # Build region bbox around the grid candidates
            all_x0 = min(c['x0'] for c in cand)
            all_y0 = min(c['y0'] for c in cand)
            all_x1 = max(c['x1'] for c in cand)
            all_y1 = max(c['y1'] for c in cand)
            self.square_grid_regions.append({'page': page_num, 'x0': all_x0, 'y0': all_y0, 'x1': all_x1, 'y1': all_y1})

        if getattr(self, 'square_grid_regions', None):
            print(f"   [GridHeuristic] Detected {len(self.square_grid_regions)} square grid region(s)")

    def _is_in_square_grid(self, page_num: int, x0: float, y0: float, x1: float, y1: float) -> bool:
        """Return True if bbox intersects any detected square grid region on the page."""
        if not getattr(self, 'square_grid_regions', None):
            return False
        for r in self.square_grid_regions:
            if r['page'] != page_num: continue
            # Simple intersection check
            if not (x1 < r['x0'] or x0 > r['x1'] or y1 < r['y0'] or y0 > r['y1']):
                return True
        return False

    def _purge_existing_checkbox_annotations(self, output_path: str = None) -> str:
        """Purge widget annotations that were flagged for removal (those in
        self._existing_checkboxes_to_remove) from the source PDF and write a new PDF.

        Returns the path to the new PDF written.
        """
        if not hasattr(self, '_existing_checkboxes_to_remove') or not self._existing_checkboxes_to_remove:
            return str(self.output_pdf)

        src = str(self.input_pdf)
        if not output_path:
            out = Path(self.output_pdf).with_name(f"{Path(self.output_pdf).stem}_no_cell_checkboxes{Path(self.output_pdf).suffix}")
        else:
            out = Path(output_path)

        with pikepdf.open(src) as pdf:
            acro = pdf.Root.get('/AcroForm') or None
            for page_num in range(len(pdf.pages)):
                page = pdf.pages[page_num]
                annots = page.get('/Annots', [])
                if not annots:
                    continue
                new_annots = []
                for a in annots:
                    try:
                        subtype = a.get('/Subtype')
                        if subtype != '/Widget':
                            new_annots.append(a)
                            continue
                        ft = a.get('/FT') or (a.get('/Parent') and a.get('/Parent').get('/FT'))
                        if ft != '/Btn':
                            new_annots.append(a); continue
                        rect = a.get('/Rect')
                        if not rect:
                            new_annots.append(a); continue
                        # Transform rect to visual coords for comparison
                        v_coords = self._transform_storage_to_visual(page_num, float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3]))
                        cx = (v_coords[0] + v_coords[2]) / 2
                        cy = (v_coords[1] + v_coords[3]) / 2
                        # If this annot matches one we flagged for removal, drop it
                        matched = False
                        for ecb in self._existing_checkboxes_to_remove:
                            if ecb.get('page') != page_num: continue
                            ex_cx = ecb['x'] + ecb['width'] / 2
                            ex_cy = ecb['y'] + ecb['height'] / 2
                            if abs(ex_cx - cx) < 2.5 and abs(ex_cy - cy) < 2.5:
                                matched = True
                                break
                        if not matched:
                            new_annots.append(a)
                    except Exception:
                        new_annots.append(a)
                # Replace annots array
                if new_annots:
                    page['/Annots'] = pikepdf.Array(new_annots)
                else:
                    if '/Annots' in page: del page['/Annots']

            # Rebuild AcroForm /Fields (remove deleted fields)
            if acro and '/Fields' in acro:
                new_fields = pikepdf.Array()
                for f in acro['/Fields']:
                    try:
                        rect = f.get('/Rect')
                        if rect:
                            v_coords = self._transform_storage_to_visual(0, float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3]))
                        # Keep all fields for safety - deeper matching can be added if necessary
                        new_fields.append(f)
                    except:
                        new_fields.append(f)
                acro['/Fields'] = new_fields

            pdf.save(str(out))
        print(f"   [Purge] Wrote new PDF with removed widgets: {out}")
        return str(out)

    def _detect_checkboxes_curves(self):
        """Detect checkboxes from small curve rectangles."""
        for page_num in range(self.page_count):
            curves = self.page_curves.get(page_num, [])
            page_checkboxes = 0
            for curve in curves:
                x0 = float(curve.get('x0', 0))
                x1 = float(curve.get('x1', 0))
                y0 = float(curve.get('top', 0))
                y1 = float(curve.get('bottom', 0))
                w = abs(x1 - x0)
                h = abs(y1 - y0)
                if 6 <= w <= 45 and 6 <= h <= 45 and abs(w - h) <= 8:
                    if self._add_checkbox(page_num, x0, y0, w, h, 'curve'):
                        page_checkboxes += 1
            if page_checkboxes > 0:
                print(f"   Page {page_num + 1}: {page_checkboxes} curve checkboxes")
    
    def _find_label_above(self, page_num: int, x: float, y: float, w: float) -> Optional[str]:
        """Find a text label directly above the checkbox.
        
        This handles cases where checkbox labels are positioned above the checkbox
        (common in forms with header rows like "Pass | Fail").
        """
        if page_num not in self.page_words:
            return None
        
        words = self.page_words[page_num]
        cx = x + w / 2  # Checkbox center x
        
        # Look for text 5-30pt above the checkbox, horizontally aligned
        candidates = []
        for word in words:
            word_x0 = float(word['x0'])
            word_x1 = float(word['x1'])
            word_y = float(word['top'])
            word_cx = (word_x0 + word_x1) / 2  # Word center x
            
            # Word must be above checkbox (lower y = higher on page)
            y_diff = y - word_y  # Positive if word is above
            if not (5 <= y_diff <= 30):
                continue
            
            # Word must be horizontally aligned with checkbox
            # Check if checkbox center is within word bounds, or word center is within checkbox bounds
            x_aligned = (word_x0 <= cx <= word_x1) or (x <= word_cx <= x + w) or abs(word_cx - cx) < 15
            if not x_aligned:
                continue
            
            text = word['text'].strip()
            if text and text not in ['•', '-', '–', '—']:
                candidates.append((y_diff, text))
        
        if candidates:
            # Return the closest label above
            candidates.sort()
            return candidates[0][1]
        
        return None
    
    def _find_row_description(self, cluster: list) -> Optional[str]:
        """Find the question/description text to the left of a checkbox cluster.
        
        For Yes/No/NA radio groups, this finds text like "Baffles/blockers installed?"
        that appears on the same row but to the left of the checkboxes.
        
        Args:
            cluster: List of checkbox dicts on the same row
            
        Returns:
            Row description text if found, or None
        """
        if not cluster:
            return None
        
        # Get the leftmost checkbox in the cluster
        leftmost = min(cluster, key=lambda c: c['x'])
        page_num = leftmost['page']
        cb_y = leftmost['y']  # Y coordinate (already in pdfplumber-compatible coords)
        cb_x = leftmost['x']  # Left edge of leftmost checkbox
        
        if page_num not in self.page_words:
            return None
        
        words = self.page_words[page_num]
        
        # Find all words to the LEFT of the checkboxes, on approximately the same row
        # Use tighter Y tolerance and prefer text that is AT or ABOVE the checkbox
        # In most forms, checkboxes are vertically centered with or slightly below their label text
        row_words = []
        for word in words:
            word_x1 = float(word['x1'])  # Right edge of word
            word_y = float(word['top'])
            
            # Word must be to the LEFT of the leftmost checkbox (with some gap)
            if word_x1 > cb_x - 15:
                continue
            
            # Y difference: positive if word is ABOVE checkbox, negative if below
            # In pdfplumber coords: smaller Y = higher on page
            y_diff = cb_y - word_y
            
            # Word should be on same row: allow words up to 8pt above or 6pt below checkbox
            # This handles cases where checkboxes are centered with text slightly below
            # Row spacing is typically 16-17pt, so this won't cross into adjacent rows
            if y_diff < -6 or y_diff > 8:
                continue
            
            text = word['text'].strip()
            # Skip bullet points and punctuation-only characters (but keep single letters like "a")
            if text in ['•', '-', '–', '—', '*', '□', '■', '|', '.', ',', ':', ';']:
                continue
            
            row_words.append({
                'text': text,
                'x0': float(word['x0']),
                'x1': word_x1
            })
        
        if not row_words:
            return None
        
        # Sort words by x position (leftmost first)
        row_words.sort(key=lambda w: w['x0'])
        
        # v22 FIX: Prioritize NEAREST text to the checkboxes
        # If there's text immediately to the left (within 80pt), use that instead of
        # scanning all the way left. This prevents picking up distant labels like
        # "Approx. Above-Grade Conditioned Sq. Ft.:" when "+Basement" is right there.
        
        # Find words CLOSE to the checkboxes (within 80pt to the left)
        close_words = [w for w in row_words if cb_x - w['x1'] <= 80 and cb_x - w['x1'] > 0]
        
        # If we found close words, use only those
        if close_words:
            # Sort by x position and join
            close_words.sort(key=lambda w: w['x0'])
            description = ' '.join(w['text'] for w in close_words)
            description = description.strip()
            if len(description) >= 3 and not description.isdigit():
                return description
        
        # FALLBACK: Original logic - scan left from checkboxes
        # Build description from contiguous words (words close together)
        # Start from the rightmost word (closest to checkboxes) and work backwards
        # to find a coherent phrase
        row_words.reverse()  # Now rightmost first
        
        # First, identify if there are checkbox labels to the left of the Yes/No/NA checkboxes
        # If there's a gap > 100pt followed by words like "Atmospheric", "Power", "Heat",
        # those are checkbox option labels, not part of the question
        # We should stop before reaching those
        
        # Find words that might be other checkbox labels (isolated words with gaps around them)
        # Typically question text ends with "?" or similar punctuation
        
        description_parts = []
        seen_texts = set()  # Avoid duplicates
        last_x0 = cb_x
        found_question_end = False
        
        for w in row_words:
            text = w['text']
            text_lower = text.lower()
            
            # Skip if we've already seen this word (deduplication)
            if text_lower in seen_texts:
                continue
            
            # Check if there's a reasonable gap (allow larger gaps for table layouts)
            gap = last_x0 - w['x1']
            
            # If we already found question text ending with "?" and there's a big gap,
            # the remaining words are likely checkbox labels from another column
            if found_question_end and gap > 50:
                break
            
            # If there's a very large gap and we haven't found question end yet,
            # and this looks like a single-word label (not ending in punctuation),
            # it might be a checkbox label - skip it
            if gap > 100 and not text.endswith('?') and not text.endswith(':'):
                # Check if this could be a checkbox option label
                # Common patterns: single capitalized word, or words like "pump", "vented"
                if len(text) < 15 and not any(c in text for c in '?:,'):
                    # This might be a checkbox label, skip it
                    continue
            
            if gap > 450:  # Allow up to 450pt gap for table layouts where checkboxes are far right
                break
            
            seen_texts.add(text_lower)
            description_parts.insert(0, text)  # Insert at beginning since we're going backwards
            last_x0 = w['x0']
            
            # Mark if we found question-ending text
            if text.endswith('?') or text.endswith(':'):
                found_question_end = True
            
            # Limit description length
            if len(description_parts) >= 15:
                break
        
        if not description_parts:
            return None
        
        description = ' '.join(description_parts)
        
        # Clean up the description
        description = description.strip()
        
        # Skip if it's too short or looks like a page number
        if len(description) < 3 or description.isdigit():
            return None
        
        return description
    
    def _find_column_header_for_checkbox(self, cb: Dict) -> Optional[str]:
        """Find a Pass/Fail/Yes/No column header above a checkbox.
        
        This searches UP the page from the checkbox's position to find
        a column header text (Pass, Fail, Yes, No, N/A) that is horizontally
        aligned with the checkbox.
        
        Args:
            cb: Checkbox dict with 'page', 'x', 'y', 'width' keys
            
        Returns:
            Column header text if found (properly capitalized), or None
        """
        page_num = cb['page']
        if page_num not in self.page_words:
            return None
        
        words = self.page_words[page_num]
        cb_x = cb['x']
        cb_y = cb['y']
        cb_w = cb.get('width', 10)
        cb_cx = cb_x + cb_w / 2  # Checkbox center x
        
        # Look for column header keywords above the checkbox
        # Search up to 500pt above (covers large tables spanning most of the page)
        candidates = []
        for word in words:
            text = word['text'].strip().lower()
            
            # Only look for known column header keywords
            if text not in self.RADIO_COLUMN_KEYWORDS:
                continue
            
            word_x0 = float(word['x0'])
            word_x1 = float(word['x1'])
            word_y = float(word['top'])
            word_cx = (word_x0 + word_x1) / 2
            
            # Word must be above checkbox (lower y = higher on page in PDF coords)
            y_diff = cb_y - word_y
            if not (10 <= y_diff <= 500):
                continue
            
            # Word must be horizontally aligned with checkbox (within 20pt of center)
            if abs(word_cx - cb_cx) > 20:
                continue
            
            # Found a candidate - return properly capitalized version
            candidates.append((y_diff, self.RADIO_COLUMN_KEYWORDS[text]))
        
        if candidates:
            # Return the closest (smallest y_diff) header above
            candidates.sort()
            return candidates[0][1]
        
        return None
    
    def _is_duplicate_checkbox(self, page_num: int, x: float, y: float, tolerance: float = 12) -> bool:
        """Check if a checkbox already exists at or very near this position.
        v36.1: Increased tolerance to 12pt to prevent doubled checkboxes from multiple detection methods.
        """
        for cb in self.checkboxes:
            if cb['page'] == page_num:
                if abs(cb['x'] - x) < tolerance and abs(cb['y'] - y) < tolerance:
                    return True
        return False
    
    def _merge_phantom_cells(self, page_num, cells):
        """
        Merge adjacent table cells if there is no physical vertical line between them.
        v21.44: Prevents row areas from being split into narrow cells by labels in unrelated rows.
        v35.40: Improved to avoid merging cells that should remain separate (like P21 boxes).
        """
        if not cells: return []
        
        # 1. Group cells by row (Y-coordinate)
        rows = defaultdict(list)
        for c in cells:
            # cell is (x0, y0, x1, y1)
            y_key = round(c[1] / 1.0) * 1.0 # 1pt tolerance for row alignment
            rows[y_key].append(list(c))
            
        merged_cells = []
        # 2. Extract physical vertical lines for this page with Y-bounds
        v_lines = []
        # Check standard lines, rects, and edges for vertical segments
        raw_lines = self.page_lines.get(page_num, []) + self.page_rects.get(page_num, []) + self.page_edges.get(page_num, [])
        for l in raw_lines:
            lx0 = float(l.get('x0', l.get('x', 0)))
            lx1 = float(l.get('x1', l.get('x', lx0) + l.get('width', 0)))
            ly0 = float(l.get('top', l.get('y0', 0)))
            ly1 = float(l.get('bottom', l.get('y1', 0)))
            if abs(lx1 - lx0) < 1.5: # Vertical
                v_lines.append(((lx0 + lx1) / 2, ly0, ly1))
                
        # 3. Process each row and merge adjacent cells if no line separates them
        for y in sorted(rows.keys()):
            row_cells = sorted(rows[y], key=lambda c: c[0])
            if not row_cells: continue
            
            current_merged = row_cells[0]
            for i in range(1, len(row_cells)):
                next_cell = row_cells[i]
                
                # Gap between cells
                gap = next_cell[0] - current_merged[2]
                
                # Cell dimensions
                curr_w = current_merged[2] - current_merged[0]
                next_w = next_cell[2] - next_cell[0]
                
                # v35.40: Don't merge if both cells are large and similar width
                # This prevents merging separate large input boxes (like on P21)
                both_large = curr_w > 100 and next_w > 100
                similar_width = abs(curr_w - next_w) < 50
                
                # v21.45: Check BOTH X-proximity AND Y-overlap for the vertical line separator
                has_line = False
                cy0, cy1 = current_merged[1], current_merged[3]
                for lx, ly0, ly1 in v_lines:
                    if abs(lx - current_merged[2]) < 5 or abs(lx - next_cell[0]) < 5:
                        # Check if line actually crosses the row's Y range
                        overlap = max(0, min(cy1, ly1) - max(cy0, ly0))
                        if overlap > (cy1 - cy0) * 0.5: # Solid separator
                            has_line = True
                            break
                
                # v35.40: Don't merge large similar-width cells even without visible line
                should_not_merge = (both_large and similar_width)
                
                # If gap is small (< 5pt) and no line separator and not large cells, merge
                if not has_line and gap < 5 and not should_not_merge:
                    # No physical line separator -> merge them!
                    current_merged[2] = next_cell[2] # Extend right edge
                    current_merged[3] = max(current_merged[3], next_cell[3]) # Coordinate max height
                else:
                    # Physical line exists or large gap or shouldn't merge -> start a new merged group
                    merged_cells.append(tuple(current_merged))
                    current_merged = next_cell
                    
            merged_cells.append(tuple(current_merged))
            
        return merged_cells
    
    # ========== TABLE CELL DETECTION ==========
    
    # v34.1: Generic detection for columns that should NOT have fields
    # These are columns with dense text (instructions, descriptions, links) but NO fill indicators
    def _is_dense_text_column(self, page_num: int, col_x0: float, col_x1: float, 
                               cells_in_column: List[Tuple], page) -> bool:
        """
        Generically detect if a column contains dense descriptive text (not fillable).
        
        A column is "dense text" if:
        1. Most cells have text that fills >60% of cell width
        2. No cells have fill indicators (underscores, colons with space, empty areas)
        3. Text is multi-word descriptive content (not short labels)
        
        Returns True if column should be SKIPPED for field creation.
        """
        if not cells_in_column:
            return False
        
        cells_with_dense_text = 0
        cells_with_fill_indicators = 0
        total_checked = 0
        
        for cell in cells_in_column:
            cx0, cy0, cx1, cy1 = cell
            cell_w = cx1 - cx0
            cell_h = cy1 - cy0
            
            if cell_w < 20 or cell_h < 10:
                continue
            
            try:
                cell_text = (page.within_bbox((cx0, cy0, cx1, cy1)).extract_text() or "").strip()
                words = page.within_bbox((cx0, cy0, cx1, cy1)).extract_words()
            except:
                continue
            
            total_checked += 1
            
            # Check for fill indicators
            has_fill_indicator = False
            
            # 1. Underscores indicate fill area
            if '___' in cell_text or '_____' in cell_text:
                has_fill_indicator = True
            
            # 2. Colon with significant space after = label + field
            if ':' in cell_text and words:
                for w in words:
                    if w['text'].endswith(':'):
                        space_after = cx1 - float(w['x1'])
                        if space_after > 30:  # Significant space for input
                            has_fill_indicator = True
                            break
            
            # 3. Empty or nearly empty cell = data entry area
            if not cell_text or len(cell_text.replace(' ', '')) < 3:
                has_fill_indicator = True
            
            # 4. Checkbox patterns
            if any(p in cell_text.lower() for p in ['☐', '☑', '□', '■']):
                has_fill_indicator = True
            
            if has_fill_indicator:
                cells_with_fill_indicators += 1
                continue
            
            # Check for dense text (fills most of cell)
            if words:
                text_x0 = min(float(w['x0']) for w in words)
                text_x1 = max(float(w['x1']) for w in words)
                text_width = text_x1 - text_x0
                fill_ratio = text_width / cell_w if cell_w > 0 else 0
                
                # Dense if text fills >50% of width AND has multiple words
                word_count = len(cell_text.split())
                if fill_ratio > 0.50 and word_count >= 2:
                    cells_with_dense_text += 1
        
        if total_checked < 2:
            return False
        
        # Column is dense text if:
        # - Most cells have dense text (>60%)
        # - Few cells have fill indicators (<30%)
        dense_ratio = cells_with_dense_text / total_checked if total_checked > 0 else 0
        fill_ratio = cells_with_fill_indicators / total_checked if total_checked > 0 else 0
        
        is_dense = dense_ratio > 0.60 and fill_ratio < 0.30
        
        if is_dense:
            print(f"   [DenseColumn] Skipping column x={col_x0:.0f}-{col_x1:.0f}: {dense_ratio:.0%} dense, {fill_ratio:.0%} fillable")
        
        return is_dense
    
    # v34.2: Generic detection for cells that contain placeholder images (photo upload boxes)
    # v35.0: FIXED - Also detect vector-based placeholder icons (mountain/circle curves)
    # v22.81: FIXED - Also detect large empty rectangles as placeholders
    # v22.96: Robust Text Detection Helpers
    def _get_words_in_bbox(self, page_num, bbox, overlap_threshold=0.20):
        """
        Generic helper to find words that significantly overlap a bbox.
        Uses crop() instead of within_bbox() to handle boundary-riding text.
        """
        try:
            page = self.pdf.pages[page_num]
            # Use crop which includes overlapping objects
            crop = page.crop(bbox)
            candidates = crop.extract_words()
            
            x0, y0, x1, y1 = bbox
            cell_area = (x1 - x0) * (y1 - y0)
            
            valid_words = []
            for w in candidates:
                # Calculate intersection
                ix0 = max(x0, w['x0'])
                iy0 = max(y0, w['top'])
                ix1 = min(x1, w['x1'])
                iy1 = min(y1, w['bottom'])
                
                if ix1 > ix0 and iy1 > iy0:
                    intersection_area = (ix1 - ix0) * (iy1 - iy0)
                    word_area = (w['x1'] - w['x0']) * (w['bottom'] - w['top'])
                    
                    # If word overlaps bbox by > threshold of word area OR cell area
                    if word_area > 0 and (intersection_area / word_area > overlap_threshold):
                        valid_words.append(w)
                    elif cell_area > 0 and (intersection_area / cell_area > overlap_threshold):
                        valid_words.append(w)
            return valid_words
        except:
            return []

    def _get_text_in_bbox(self, page_num, bbox, overlap_threshold=0.20):
        """Unified helper to get text from a bbox using robust overlap detection."""
        words = self._get_words_in_bbox(page_num, bbox, overlap_threshold)
        if not words: return ""
        # Sort by top then left
        words.sort(key=lambda w: (w['top'], w['x0']))
        return " ".join(w['text'] for w in words).strip()

    def _cell_has_placeholder_image(self, page_num: int, x0: float, y0: float, 
                                     x1: float, y1: float) -> bool:
        """
        Detect if a cell contains a placeholder image/icon for photo upload.
        
        Detects:
        1. Embedded raster images (traditional)
        2. Vector-drawn placeholder icons (mountain + circle curves in gray fill)
        3. Large empty rectangles (common placeholder pattern)
        
        These cells should become image upload fields, not checkboxes or text fields.
        """
        try:
            page = self.pdf.pages[page_num]
            cell_w = x1 - x0
            cell_h = y1 - y0
            
            # Skip small cells
            if cell_w < 60 or cell_h < 60:
                return False
            
            cell_objs = page.within_bbox((x0, y0, x1, y1)).objects
            
            # Method 1: Check for embedded raster images
            images = cell_objs.get('image', [])
            if images:
                for img in images:
                    img_w = float(img.get('width', img.get('x1', 0) - img.get('x0', 0)))
                    img_h = float(img.get('height', img.get('bottom', img.get('y1', 0)) - img.get('top', img.get('y0', 0))))
                    # Image should be reasonably sized (at least 30% of cell)
                    if img_w > cell_w * 0.3 or img_h > cell_h * 0.3:
                        return True
                # Any image in a large cell
                return True
            
            # Method 2: Check for vector placeholder icon (curves with no text)
            # The classic placeholder icon is drawn as:
            # - A mountain shape (triangular curve)
            # - A circle/sun shape (elliptical curve)
            # Both are filled with gray/light color
            chars = cell_objs.get('char', [])
            curves = cell_objs.get('curve', [])
            
            # If cell has curves but no text, it might be a placeholder icon
            if curves and not chars:
                # Check if curves are gray-filled (placeholder style)
                gray_filled_curves = 0
                for curve in curves:
                    fill = curve.get('fill', False)
                    nsc = curve.get('non_stroking_color')
                    if fill and nsc:
                        # Gray colors have similar RGB values or low alpha
                        if isinstance(nsc, tuple):
                            if len(nsc) >= 3:
                                # Check if gray-ish (R≈G≈B) or has alpha channel
                                r, g, b = nsc[0], nsc[1], nsc[2]
                                is_gray = abs(r - g) < 0.1 and abs(g - b) < 0.1
                                is_light = r > 0.5 or g > 0.5 or b > 0.5
                                if is_gray or is_light or len(nsc) == 4:  # CMYK or RGBA
                                    gray_filled_curves += 1
                            elif len(nsc) == 1:  # Grayscale
                                gray_filled_curves += 1
                
                # If we have 1-5 gray-filled curves in a roughly square cell with no text,
                # it's very likely a placeholder icon
                aspect = cell_w / cell_h if cell_h > 0 else 0
                if gray_filled_curves >= 1 and 0.5 < aspect < 2.0:
                    return True
            
            # Method 3: Check for large empty rectangles (common placeholder pattern)
            # These are typically 100x100pt+ boxes with no text, just an outline
            rects = cell_objs.get('rect', [])
            if rects and not chars:
                # Look for rectangles that fill most of the cell
                for rect in rects:
                    rect_w = float(rect.get('width', 0))
                    rect_h = float(rect.get('height', 0))
                    # If rectangle is large (>100pt) and fills >60% of cell, it's a placeholder
                    if rect_w > 100 and rect_h > 100:
                        fill_ratio_w = rect_w / cell_w if cell_w > 0 else 0
                        fill_ratio_h = rect_h / cell_h if cell_h > 0 else 0
                        if fill_ratio_w > 0.6 and fill_ratio_h > 0.6:
                            return True
                    
        except Exception as e:
            pass
        
        return False
    
    # v34.3: Analyze table structure to find columns that need fields vs informational columns
    def _analyze_table_columns(self, page_num: int, table, page) -> Dict:
        """
        Analyze a table to determine which columns should have fields.
        
        Returns a dict with:
        - 'skip_columns': list of (x0, x1) ranges to skip
        - 'field_columns': list of (x0, x1) ranges that should have fields
        - 'image_columns': list of (x0, x1) ranges for image upload
        """
        result = {
            'skip_columns': [],
            'field_columns': [],
            'image_columns': []
        }
        
        cells = table.cells
        if not cells or len(cells) < 4:
            return result
        
        # Group cells by column (X position)
        col_tolerance = 10
        columns = {}  # x_key -> list of cells
        
        for cell in cells:
            cx0, cy0, cx1, cy1 = cell
            # Find or create column bucket
            found_col = None
            for col_x in columns.keys():
                if abs(cx0 - col_x) < col_tolerance:
                    found_col = col_x
                    break
            
            if found_col is not None:
                columns[found_col].append(cell)
            else:
                columns[cx0] = [cell]
        
        # Analyze each column
        for col_x, col_cells in sorted(columns.items()):
            if len(col_cells) < 2:
                continue
            
            # Get column bounds
            col_x0 = min(c[0] for c in col_cells)
            col_x1 = max(c[2] for c in col_cells)
            
            # Check for image upload column (cells with placeholder images)
            image_cells = 0
            for cell in col_cells:
                if self._cell_has_placeholder_image(page_num, *cell):
                    image_cells += 1
            
            if image_cells > len(col_cells) * 0.5:
                result['image_columns'].append((col_x0, col_x1))
                continue
            
            # Check for dense text column
            if self._is_dense_text_column(page_num, col_x0, col_x1, col_cells, page):
                result['skip_columns'].append((col_x0, col_x1))
            else:
                result['field_columns'].append((col_x0, col_x1))
        
        return result
    
    # v22.6: Strict Visual Grid Builder (Replaces/Augments Table Detection)
    def _build_visual_grid(self, page_num):
        """
        Reconstruct precise table cells from raw PDF line intersections.
        Returns a list of cells [{'x0', 'y0', 'x1', 'y1'}]
        """
        h_lines = []
        v_lines = []
        
        # Combine lines and rects (thin rects treated as lines)
        raw_objects = self.page_lines.get(page_num, []) + self.page_rects.get(page_num, [])
        
        for obj in raw_objects:
            # Normalize keys
            x0 = float(obj.get('x0', 0))
            x1 = float(obj.get('x1', x0 + obj.get('width', 0)))
            y0 = float(obj.get('top', obj.get('y0', 0))) # Visual top
            y1 = float(obj.get('bottom', obj.get('y1', 0))) # Visual bottom
            
            w = x1 - x0
            h = y1 - y0
            
            if w < 0 or h < 0: continue
            
            # Horizontal (Width > Height, reasonable length)
            if w > h and w > 10 and h < 5:
                y_center = (y0 + y1) / 2
                h_lines.append({'y': y_center, 'x0': x0, 'x1': x1})
            
            # Vertical (Height > Width, reasonable length)
            elif h > w and h > 10 and w < 5:
                x_center = (x0 + x1) / 2
                v_lines.append({'x': x_center, 'y0': y0, 'y1': y1})

        # Cluster lines (tolerance 2pt)
        def cluster_lines(lines, coord_key):
            lines.sort(key=lambda l: l[coord_key])
            clusters = []
            if not lines: return []
            
            current_cluster = [lines[0]]
            for l in lines[1:]:
                if l[coord_key] - current_cluster[-1][coord_key] < 2.0:
                    current_cluster.append(l)
                else:
                    clusters.append(current_cluster)
                    current_cluster = [l]
            clusters.append(current_cluster)
            
            merged = []
            for c in clusters:
                avg_coord = sum(l[coord_key] for l in c) / len(c)
                
                if coord_key == 'y':
                    # Horizontal lines - merge freely
                    min_start = min(l['x0'] for l in c)
                    max_end = max(l['x1'] for l in c)
                    merged.append({'y': avg_coord, 'x0': min_start, 'x1': max_end})
                else:
                    # v33.05: Vertical lines - DON'T merge lines with gaps > 10pt
                    # Sort by y0 to find contiguous segments
                    c_sorted = sorted(c, key=lambda l: l['y0'])
                    segments = []
                    current_seg = [c_sorted[0]]
                    
                    for l in c_sorted[1:]:
                        # Check if this line continues from the previous one (gap < 10pt)
                        prev_end = current_seg[-1]['y1']
                        if l['y0'] - prev_end < 10:
                            current_seg.append(l)
                        else:
                            # Gap too large - start new segment
                            segments.append(current_seg)
                            current_seg = [l]
                    segments.append(current_seg)
                    
                    # Create a merged line for each contiguous segment
                    for seg in segments:
                        seg_y0 = min(l['y0'] for l in seg)
                        seg_y1 = max(l['y1'] for l in seg)
                        merged.append({'x': avg_coord, 'y0': seg_y0, 'y1': seg_y1})
            
            return merged

        clean_h = cluster_lines(h_lines, 'y')
        clean_v = cluster_lines(v_lines, 'x')
        
        # Form cells
        cells = []
        for i in range(len(clean_h) - 1):
            top_line = clean_h[i]
            for j in range(i + 1, len(clean_h)):
                bottom_line = clean_h[j]
                
                # Find valid V lines crossing both
                valid_v_lines = []
                for v in clean_v:
                    if v['y0'] <= top_line['y'] + 2 and v['y1'] >= bottom_line['y'] - 2:
                        valid_v_lines.append(v)
                
                valid_v_lines.sort(key=lambda v: v['x'])
                
                if len(valid_v_lines) >= 2:
                    for k in range(len(valid_v_lines) - 1):
                        left_v = valid_v_lines[k]
                        right_v = valid_v_lines[k+1]
                        
                        # Verify H lines span this width
                        if not (top_line['x0'] <= left_v['x'] + 2 and top_line['x1'] >= right_v['x'] - 2): continue
                        if not (bottom_line['x0'] <= left_v['x'] + 2 and bottom_line['x1'] >= right_v['x'] - 2): continue
                        
                        cell = {
                            'x0': left_v['x'],
                            'y0': top_line['y'],
                            'x1': right_v['x'],
                            'y1': bottom_line['y']
                        }
                        
                        # Verify no splitting H line inside
                        is_split = False
                        for mid_h in clean_h:
                            if mid_h['y'] > cell['y0'] + 2 and mid_h['y'] < cell['y1'] - 2:
                                if mid_h['x0'] <= cell['x1'] - 2 and mid_h['x1'] >= cell['x0'] + 2:
                                    is_split = True
                                    break
                        if not is_split:
                            cells.append(cell)
        
        # v33: Filter out COMB field cells (rows of 5+ small uniform boxes)
        # These are handled separately by _detect_box_entry_fields
        if cells:
            cells = self._filter_comb_cells(cells)
        
        if cells:
            print(f"   Note: Built visual grid for Page {page_num}: {len(cells)} cells")
        return cells
    
    def _filter_comb_cells(self, cells):
        """
        v33: Remove cells that are part of a COMB field pattern.
        
        COMB fields are rows of 5+ small uniform cells (8-25pt wide) with consistent spacing.
        These should be handled as single COMB fields, not individual text fields.
        """
        from collections import defaultdict
        
        if not cells:
            return cells
        
        # Group cells by Y position (within 3pt tolerance)
        rows = defaultdict(list)
        for c in cells:
            y_key = round(c['y0'] / 3) * 3
            rows[y_key].append(c)
        
        # Identify COMB rows
        comb_cells_to_remove = set()
        
        for y_key, row_cells in rows.items():
            if len(row_cells) < 5:  # Need at least 5 cells for COMB pattern
                continue
            
            # Sort by X position
            row_cells.sort(key=lambda c: c['x0'])
            
            # Check for uniform width and spacing
            widths = [c['x1'] - c['x0'] for c in row_cells]
            avg_width = sum(widths) / len(widths)
            
            # COMB cells are typically 8-25pt wide
            if not (8 <= avg_width <= 25):
                continue
            
            # Check width uniformity (all within 30% of average)
            width_uniform = all(abs(w - avg_width) < avg_width * 0.3 for w in widths)
            if not width_uniform:
                continue
            
            # Check spacing uniformity
            spacings = [row_cells[i+1]['x0'] - row_cells[i]['x1'] for i in range(len(row_cells)-1)]
            if not spacings:
                continue
            
            avg_spacing = sum(spacings) / len(spacings)
            
            # COMB cells are typically adjacent (spacing < 3pt) or nearly adjacent
            spacing_uniform = all(abs(s - avg_spacing) < 5 for s in spacings) and avg_spacing < 5
            
            if width_uniform and spacing_uniform:
                # This is a COMB row - mark all cells for removal
                print(f"   [CombFilter] Filtering {len(row_cells)} COMB cells at Y={y_key}")
                for c in row_cells:
                    comb_cells_to_remove.add((c['x0'], c['y0'], c['x1'], c['y1']))
        
        # Return filtered cells
        return [c for c in cells if (c['x0'], c['y0'], c['x1'], c['y1']) not in comb_cells_to_remove]

    def _is_in_header_bar(self, x0, y0, x1, y1, header_bars):
        """Check if a cell significantly overlaps with a header bar."""
        # v33.37: Only skip if the header bar is SIGNIFICANT (Wide)
        # to avoid skipping colored field boxes like P1 "Name".
        cell_area = (x1 - x0) * (y1 - y0)
        if cell_area <= 0: return False
        
        for hb in header_bars:
            # Header bars like "Name" box are usually < 200pt wide.
            # Real section headers span most of the page (> 300pt).
            hb_w = hb['x1'] - hb['x0']
            if hb_w < 200: # Narrow colored box -> probably a field, not a skipping-header
                continue
                
            hx0 = hb['x0']
            hy0 = hb['y0']
            hx1 = hb['x1']
            hy1 = hb['y1']
            
            # Calculate intersection
            ix0 = max(x0, hx0)
            iy0 = max(y0, hy0)
            ix1 = min(x1, hx1)
            iy1 = min(y1, hy1)
            
            w = max(0, ix1 - ix0)
            h = max(0, iy1 - iy0)
            intersection = w * h
            
            # v22.42: Lowered threshold from 0.5 to 0.2
            # Section B/C headers on P1 (54pt tall cell, 18pt bar) are only 33% coverage.
            if intersection > 0.2 * cell_area:
                return True
        return False

    def _rect_has_text(self, page_num: int, rect: Dict) -> bool:
        """Check if any text exists within the given rectangle coordinates."""
        words = self.page_words.get(page_num, [])
        rx0, ry0, rx1, ry1 = rect['x0'], rect['y0'], rect['x1'], rect['y1']
        for w in words:
            # Use center point for check
            cx = (float(w['x0']) + float(w['x1'])) / 2
            cy = (float(w['top']) + float(w['bottom'])) / 2
            if rx0 - 0.5 <= cx <= rx1 + 0.5 and ry0 - 0.5 <= cy <= ry1 + 0.5:
                return True
        return False

    def _rect_has_text(self, page_num: int, rect: Dict) -> bool:
        """Check if any text exists within the given rectangle coordinates."""
        words = self.page_words.get(page_num, [])
        rx0, ry0, rx1, ry1 = rect['x0'], rect['y0'], rect['x1'], rect['y1']
        for w in words:
            # Use center point for check
            cx = (float(w['x0']) + float(w['x1'])) / 2
            cy = (float(w['top']) + float(w['bottom'])) / 2
            if rx0 - 0.5 <= cx <= rx1 + 0.5 and ry0 - 0.5 <= cy <= ry1 + 0.5:
                return True
        return False

    def _detect_table_cells(self):
        """Detect table cells using cached pdfplumber data with smart header detection."""
        for page_num in range(self.page_count):
            # v35.20: Tally sheet pages still get TEXT fields, but no checkboxes
            # Don't skip entirely - just disable checkbox creation for these pages
            is_tally_page = page_num in self.tally_sheet_pages
            if is_tally_page:
                print(f"   Page {page_num + 1}: Tally sheet - creating text fields only (no checkboxes)")
            
            page_fields = 0
            # We need the actual page object for `page.within_bbox().extract_text()` later
            # This assumes self.pdf is already open and pages are accessible by index
            page = self.pdf.pages[page_num]

            page_width = self.page_widths.get(page_num, 612)
            page_height = self.page_heights.get(page_num, 792)
            tables = self.page_tables.get(page_num, [])
            
            # v21.7: Use cached header bars for this page to filter out false positives
            if self.header_bars_cache is None:
                self.header_bars_cache = self._detect_header_bars()
            header_bars = self.header_bars_cache.get(page_num, [])

            # v22.6: STRICT GRID PATH
            # If we can reconstruct a solid grid (e.g. > 5 cells), use it EXCLUSIVELY.
            grid_cells = self._build_visual_grid(page_num)
            
            # v33.07: Generic table-cell backfill
            # If strict visual grid misses any table cells, add non-overlapping
            # pdfplumber table cells to ensure completeness without hardcoding.
            if tables:
                def _cell_overlap_ratio(a, b):
                    ax0, ay0, ax1, ay1 = a
                    bx0, by0, bx1, by1 = b
                    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
                    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
                    if ix1 <= ix0 or iy1 <= iy0:
                        return 0.0
                    inter = (ix1 - ix0) * (iy1 - iy0)
                    area = (ax1 - ax0) * (ay1 - ay0)
                    return inter / area if area > 0 else 0.0

                grid_tuples = [(c['x0'], c['y0'], c['x1'], c['y1']) for c in grid_cells]
                for table in tables:
                    for cell in (table.cells or []):
                        if not isinstance(cell, (list, tuple)) or len(cell) != 4:
                            continue
                        cx0, cy0, cx1, cy1 = cell
                        if (cx1 - cx0) < 40 or (cy1 - cy0) < 12:
                            continue
                        if any(_cell_overlap_ratio(cell, gc) > 0.8 for gc in grid_tuples):
                            continue
                        grid_cells.append({'x0': cx0, 'y0': cy0, 'x1': cx1, 'y1': cy1})
           
            if len(grid_cells) > 5:
                print(f"   🏗️  Using Strict Visual Grid for Page {page_num} ({len(grid_cells)} cells)")
                
                # Convert to tuple format (x0, y0, x1, y1)
                cells = [(c['x0'], c['y0'], c['x1'], c['y1']) for c in grid_cells]
                
                # Store cells for coverage checking
                for c in cells:
                     self.table_cells.append({
                        'page': page_num,
                        'x0': c[0], 'y0': c[1], 'x1': c[2], 'y1': c[3],
                        'visual_x0': c[0]
                    })

                # v33.29: Ensure dense visual grids are completely filled
                # Decide whether this visual grid is checkbox-style (mostly small square cells)
                total_cells = len(cells) if cells else 0
                small_cells = []
                empty_cells = []
                for c in cells:
                    cw = c[2] - c[0]
                    ch = c[3] - c[1]
                    if 6 <= cw <= 60 and 6 <= ch <= 60 and abs(cw - ch) <= max(6, 0.12 * max(cw, ch)):
                        small_cells.append(c)
                    
                    # v35.15: Check if cell is empty (for tally sheet detection)
                    try:
                        crop = page.within_bbox((c[0]+1, c[1]+1, c[2]-1, c[3]-1))
                        txt = (crop.extract_text() or "").strip()
                        if not txt:
                            empty_cells.append(c)
                    except:
                        empty_cells.append(c)  # If can't extract, assume empty
                
                small_frac = len(small_cells) / total_cells if total_cells else 0.0
                empty_frac = len(empty_cells) / total_cells if total_cells else 0.0
                
                # v35.18: Detect tally sheet pages - ONLY if page has tally sheet indicators
                page_text = (page.extract_text() or "")[:500].lower()
                worksheet_indicators = ['rebate', 'equipment type', 'ref #', 'unit cost', 'total rebate', 'model number', 'serial number', 'installed', 'quantity']
                # Use already-detected tally sheet state
                is_tally_sheet = page_num in self.tally_sheet_pages
                if is_tally_sheet:
                    print(f"   [TallySheet] P{page_num+1} is tally sheet - skipping checkbox auto-fill")
                
                grid_prefers_checkboxes = (small_frac >= 0.75) # Increased from 0.60 to be more conservative
                
                if is_tally_sheet:
                    grid_prefers_checkboxes = False
                elif not grid_prefers_checkboxes:
                    print(f"   [GridTextOnly] P{page_num+1} visual grid small_frac={small_frac:.2f}; treating grid as text-only and skipping auto-fill of checkboxes")
                
                # v22.92: Text Field Fallback for Tally Sheets and Text-Only Grids
                # Even if we aren't auto-filling checkboxes, we must ensure empty cells get text fields.
                if is_tally_sheet or not grid_prefers_checkboxes:
                    added_fallback = 0
                    for c in cells:
                        cx0, cy0, cx1, cy1 = c
                        # Skip if cell too small/large
                        if (cx1 - cx0) < 15 or (cy1 - cy0) < 10: continue

                        # Check for overlap with existing fields
                        overlaps = False
                        for f in self.text_fields:
                            if f['page'] != page_num: continue
                            if not (f['x1'] < cx0 - 1 or f['x0'] > cx1 + 1 or f['y1'] < cy0 - 1 or f['y0'] > cy1 + 1):
                                overlaps = True; break
                        if overlaps: continue

                        # Check for text inside using WORDS (stricter than extract_text)
                        # v22.93: Prevent fields over text by checking word centroids
                        # v22.96: Use robust helper for text detection
                        words = self._get_words_in_bbox(page_num, (cx0, cy0, cx1, cy1), overlap_threshold=0.15)
                        if words: continue

                        # v36.1: RELAXED - Allow fields in shaded table cells by removing HeaderSkipGridFallback
                        # This restores missing fields on P32 (System Watts, Coil Capacity).
                        
                        # Create generic text field
                        field_name = self._generate_unique_name(f"Cell_{int(cx0)}_{int(cy0)}", page_num)
                        self.text_fields.append({
                            'page': page_num,
                            'x0': cx0 + 2, 'y0': cy0 + 2, 'x1': cx1 - 2, 'y1': cy1 - 2,
                            'name': field_name,
                            'source': 'grid_fallback'
                        })
                        added_fallback += 1
                    if added_fallback:
                        print(f"   [GridFallback] P{page_num+1}: Added {added_fallback} fallback text fields to empty grid cells")

                if grid_prefers_checkboxes:
                    # For each grid cell, if there's no checkbox and no text, add a centered checkbox
                    for c in cells:
                        cx0, cy0, cx1, cy1 = c
                        cell_w = cx1 - cx0
                        cell_h = cy1 - cy0
                        # Only small cells are grid checkboxes
                        if not (6 <= cell_w <= 60 and 6 <= cell_h <= 60):
                            continue
                        # Check for any existing checkbox overlap
                        overlaps = False
                        for cb in self.checkbox_positions:
                            if cb['page'] != page_num: continue
                            if not (cb['x1'] < cx0 - 1 or cb['x0'] > cx1 + 1 or cb['y1'] < cy0 - 1 or cb['y0'] > cy1 + 1):
                                overlaps = True; break
                        if overlaps:
                            continue
                        # Check for any text inside
                        try:
                            txt = (self.pdf.pages[page_num].within_bbox((cx0+1, cy0+1, cx1-1, cy1-1)).extract_text() or '').strip()
                        except Exception:
                            txt = ''
                        if txt:
                            continue
                            
                        # CRITICAL: Even if the grid prefers checkboxes, still verify a visual cue exists
                        # This prevents false checkboxes in empty text grid cells (Page 2 Manufacturer fields)
                        if not self._cell_has_visual_checkbox(page_num, cx0, cy0, cx1, cy1):
                            # print(f"   [SkipGridCB] P{page_num} empty grid cell at ({cx0:.1f},{cy0:.1f}) has no visual box")
                            continue
                            
                        # Add checkbox centered in cell
                        # But if table-cell checkboxes are disabled, create a text field instead
                        if self.settings.get('disable_checkboxes_in_table_cells', True):
                            field_name = self._generate_unique_name(f"Cell_{int(cx0)}_{int(cy0)}", page_num)
                            self.text_fields.append({
                                'page': page_num,
                                'x0': cx0 + 2, 'y0': cy0 + 2, 'x1': cx1 - 2, 'y1': cy1 - 2,
                                'name': field_name,
                                'source': 'grid_fallback_table_cell'
                            })
                        else:
                            size = min(cell_w, cell_h) * 0.85
                            cb_x = cx0 + (cell_w - size) / 2
                            cb_y = cy0 + (cell_h - size) / 2
                            self._add_checkbox(page_num, cb_x, cb_y, size, size, 'table_cell')
                
                # Find header rows (same logic as before)
                y_values = sorted(set(c[1] for c in cells))
                row_starts = []
                if y_values:
                    current_y = y_values[0]
                    row_starts.append(current_y)
                    for y in y_values[1:]:
                        if y - current_y > 2: # Tolerance
                            current_y = y
                            row_starts.append(current_y)
                
                # Assume first 1-2 rows might be headers if they contain bold text or specific keywords
                # For now, rely on `_analyze_cell_for_field` to detect "Is this a label?"
                header_y_max = 0

                # v35.23: Detect checkbox columns from short header labels (Yes/No/Pass/Fail/etc)
                # BUT exclude quantity columns (# of Units, # of Fixtures, etc.)
                checkbox_columns = []
                header_row_y = None
                row_cells = {}
                for c in cells:
                    row_cells.setdefault(round(c[1], 1), []).append(c)
                for y_key in sorted(row_cells.keys()):
                    row = row_cells[y_key]
                    if len(row) < 3:
                        continue
                    labeled = []
                    for rc in row:
                        rt = self._get_text_in_bbox(page_num, rc, overlap_threshold=0.20)
                        if rt:
                            labeled.append((rc, rt))
                        if len(labeled) >= 2:
                            header_row_y = y_key
                            for rc, rt in labeled:
                                short = rt.strip().lower()
                                # v35.28: Skip quantity/number columns (expanded patterns)
                                # v22.95: RELAXED FILTER - Only block explicitly numeric quantity headers.
                                # Allow "Type", "Measure", "System" to have checkboxes.
                                rt_lower = rt.lower()
                                is_numeric_header = ('# of' in rt or 'number of' in rt_lower or 
                                                   'qty' in short or 'quantity' in short or 
                                                   'count' in short or '# fixtures' in rt_lower or
                                                   'score' in rt_lower or 'point' in rt_lower)  # v36.2: Exclude Score/Point columns
                                
                                if is_numeric_header:
                                    continue  # NOT a checkbox column
                                    
                                if len(short) <= 3 or short in ['yes', 'no', 'n/a', 'na', 'pass', 'fail', 'y', 'n', 'type', 'measure', 'system', 'primary', 'cooling', 'heating']:
                                    cw = rc[2] - rc[0]
                                    if cw <= 50: # Slightly relaxed width
                                        checkbox_columns.append((rc[0], rc[2]))
                            break # Found the header row; stop scanning for more header rows
                
                # Analyze each cell
                for cell in cells:
                    cx0, cy0, cx1, cy1 = cell
                    cell_w = cx1 - cx0
                    cell_h = cy1 - cy0
                    
                    # v35.28: Check if this column has a quantity header (prevent checkboxes)
                    col_is_quantity = False
                    if header_row_y is not None:
                        for header_cell in row_cells.get(header_row_y, []):
                            # Check if this cell is in the same column as a quantity header
                            if abs(cx0 - header_cell[0]) < 5 and abs(cx1 - header_cell[2]) < 5:
                                try:
                                    header_text = (page.within_bbox(header_cell).extract_text() or "").strip()
                                    if ('# of' in header_text or 'number of' in header_text.lower() or 
                                        '# fixtures' in header_text.lower() or 'qty' in header_text.lower() or
                                        'quantity' in header_text.lower() or 'count' in header_text.lower()):
                                        col_is_quantity = True
                                        break
                                except Exception:
                                    pass
                    
                    # v34.7: GENERIC IMAGE UPLOAD DETECTION
                    # If cell has placeholder image and is reasonably sized, create image upload field
                    if self._cell_has_placeholder_image(page_num, cx0, cy0, cx1, cy1):
                        if cell_w > 50 and cell_h > 50:  # Minimum size for photo
                            # This is an image upload box - create full-cell image field
                            field_name = self._generate_unique_name("Photo_Upload", page_num)
                            self.text_fields.append({
                                'page': page_num,
                                'x0': cx0 + 2,
                                'y0': cy0 + 2,
                                'x1': cx1 - 2,
                                'y1': cy1 - 2,
                                'name': field_name,
                                'label': 'Photo Upload',
                                'format': 'image',
                                'format_opts': {},
                                'source': 'image_grid_cell',
                                'is_image_box': True
                            })
                            page_fields += 1
                            print(f"   [ImageUpload] P{page_num+1} Created image upload at ({cx0:.0f},{cy0:.0f}) {cell_w:.0f}x{cell_h:.0f}")
                            continue
                    
                    # v34.8: GENERIC DENSE TEXT DETECTION (replaces hardcoded page/position checks)
                    # Skip cells with dense text that fills most of the cell (informational, not fillable)
                    inner_text = self._get_text_in_bbox(page_num, (cx0, cy0, cx1, cy1), overlap_threshold=0.15)
                    inner_words = self._get_words_in_bbox(page_num, (cx0, cy0, cx1, cy1), overlap_threshold=0.15)
                    
                    if inner_words and len(inner_text) > 5:
                            # Calculate text fill ratio
                            txt_x0 = min(float(w['x0']) for w in inner_words)
                            txt_x1 = max(float(w['x1']) for w in inner_words)
                            txt_width = txt_x1 - txt_x0
                            fill_ratio = txt_width / cell_w if cell_w > 0 else 0
                            
                            # Check for fill indicators that should NOT be skipped
                            has_fill_indicator = (
                                '___' in inner_text or 
                                ((':' in inner_text) and (cx1 - txt_x1 > 25)) or
                                any(c in inner_text for c in ['☐', '☑', '□'])
                            )
                            
                            # Skip if text fills >70% and no fill indicators and >3 words
                            word_count = len(inner_text.split())
                            if fill_ratio > 0.70 and not has_fill_indicator and word_count >= 3:
                                # print(f"   [DenseTextSkip] P{page_num} cell at ({cx0:.0f},{cy0:.0f}) skipped - fill={fill_ratio:.0%}")
                                continue
                    # Skip cells overlapping header bars
                    if self._is_in_header_bar(cx0, cy0, cx1, cy1, header_bars):
                        # v36.5: UNIVERSAL - Allow shaded cells if they contain data-entry cues or common labels
                        # This replaces hardcoded Page/Keyword checks with generic structural analysis.
                        txt_lower = inner_text.lower()
                        
                        # 1. Structural Field Cues (Colons, underscores, or explicit checkbox chars)
                        has_indicators = any(x in txt_lower for x in ['yes', 'no', 'n/a', '☐', '☑', '□'])
                        has_field_cues = (':' in inner_text or '___' in inner_text or has_indicators)
                        
                        # 2. Generic Field Keywords (Broadly applicable to any form)
                        generic_field_kws = [
                            'name', 'date', 'address', 'phone', 'tel', 'email', 'city', 'state', 'zip',
                            'total', 'amount', 'qty', 'quantity', 'number', 'ref', 'id', 'type', 'status',
                            'notes', 'comments', 'description', 'account', 'customer', 'applicant', 'contractor',
                            'pass', 'fail', 'score', 'value'
                        ]
                        has_generic_kw = any(f" {kw} " in f" {txt_lower} " or txt_lower.startswith(kw) or txt_lower.endswith(kw) for kw in generic_field_kws)
                        
                        # 3. Label-and-Input Pattern: Multiple words but significant empty space at the right
                        # This indicates a label followed by a blank area for data entry, even if shaded.
                        is_likely_input = (len(inner_text) > 5 and cx1 - txt_x1 > 35)
                        
                        # 4. Top-of-page Header Exception: Often contains high-value fields like Application Number
                        is_top_header = (cy0 < 150) # Generic top margin for "Account" type fields
                        
                        should_allow = (has_field_cues or (is_top_header and has_generic_kw) or is_likely_input or has_generic_kw)
                        
                        if should_allow:
                            # Allow it
                            pass
                        else:
                            print(f"   [HeaderSkip] P{page_num} cell {cx0:.0f},{cy0:.0f} skipped (Header Bar)")
                            continue

                    # v34.9: GENERIC - Use robust text extraction
                    cell_text = self._get_text_in_bbox(page_num, (cx0-0.5, cy0-0.5, cx1+0.5, cy1+0.5), overlap_threshold=0.15)
                    words = self._get_words_in_bbox(page_num, (cx0-0.5, cy0-0.5, cx1+0.5, cy1+0.5), overlap_threshold=0.15)
                    
                    
                    # SMART FIX 3: GENERIC Header Detection (Text Density & Position)
                    # Replaces hardcoded keywords with structural logic valid for any form.
                    if words and cell_text.strip():
                        # 1. Calculate text bounding box
                        wx0 = min(w['x0'] for w in words)
                        wy0 = min(w['top'] for w in words)
                        wx1 = max(w['x1'] for w in words)
                        wy1 = max(w['bottom'] for w in words)
                        
                        txt_w = wx1 - wx0
                        txt_h = wy1 - wy0
                        cell_w = cx1 - cx0
                        cell_h = cy1 - cy0
                        
                        # 2. Heuristic: Text Fill Ratio
                        # Headers usually fill the cell (width-wise or height-wise block)
                        # Data input cells usually have small labels or are empty
                        width_fill = txt_w / cell_w
                        height_fill = txt_h / cell_h
                        
                        # v35.21: If cell has a colon (label cell), don't skip - it needs field beside the label
                        has_colon = ':' in cell_text
                        
                        # Skip if text fills significant portion of cell (indicating header/label cell)
                        # BUT allow colon cells through - they get fields beside the label
                        if '_' not in cell_text and not has_colon and (width_fill > 0.85 or height_fill > 0.6):
                            print(f"   [GenericSkip] P{page_num} cell skipped (High Fill: w={width_fill:.0%}, h={height_fill:.0%}) text='{cell_text[:30]}'")
                            continue

                        # 3. Heuristic: Text Centering (Headers are often centered)
                        # Check if margins are roughly equal
                        # v35.21: Allow colon cells through - they need fields beside the label
                        margin_left = wx0 - cx0
                        margin_right = cx1 - wx1
                        if not has_colon and width_fill > 0.4 and abs(margin_left - margin_right) < 10:
                            # Likely centered text -> Header
                            print(f"   [GenericSkip] P{page_num} cell skipped (Centered Text)")
                            continue
                        
                        # Also skip strictly "Yes" or "No" cells if they are small (these are labels for checkboxes)
                        cleanup_text = cell_text.replace('\n', ' ').strip()
                        if cleanup_text.lower() in ['yes', 'no'] and (cx1-cx0) < 40:
                             print(f"   [LabelSkip] P{page_num} cell {cx0:.0f},{cy0:.0f} skipped ('{cleanup_text}')")
                             continue
                        
                    # v34.10: GENERIC Below-Label alignment for LARGE BOXES (>25pt tall)
                    # Large cells have room for label at top + field below
                    is_large_box_section = (cy1 - cy0) > 25

                    # v35.27: Checkbox columns from short header labels (but NOT in quantity columns)
                    if header_row_y is not None and cy0 > header_row_y + 2 and checkbox_columns and not col_is_quantity:
                        in_checkbox_col = any(not (cx1 < c0 + 2 or cx0 > c1 - 2) for c0, c1 in checkbox_columns)
                        if in_checkbox_col and not cell_text.strip() and not words:
                            # If the grid overall prefers text, only create a checkbox here
                            # if we can see an explicit visual checkbox inside the cell.
                            if not grid_prefers_checkboxes and not self._cell_has_visual_checkbox(page_num, cx0, cy0, cx1, cy1):
                                print(f"   [GridTextOnly] P{page_num} checkbox column at ({cx0:.1f},{cy0:.1f}) - grid treated as text-only; creating text field instead")
                            else:
                                # Only create a checkbox in a checkbox column if there is a visual checkbox
                                size = min(cell_w, cell_h, 18)
                                cb_x = cx0 + (cell_w - size) / 2
                                cb_y = cy0 + (cell_h - size) / 2

                                if not self._cell_has_visual_checkbox(page_num, cx0, cy0, cx1, cy1):
                                    # No visual square detected; skip creating a checkbox here
                                    print(f"   [SkipCB] P{page_num} col cell at ({cx0:.1f},{cy0:.1f}) has no visual checkbox; will be treated as text")
                                else:
                                    overlaps_cb = False
                                    for cb in self.checkbox_positions:
                                        if cb['page'] != page_num:
                                            continue
                                        if not (cb['x1'] < cb_x - 2 or cb['x0'] > cb_x + size + 2 or cb['y1'] < cb_y - 2 or cb['y0'] > cb_y + size + 2):
                                            overlaps_cb = True
                                            break
                                    if not overlaps_cb:
                                        # Prefer a text field fallback if table-cell checkboxes disabled
                                        if self.settings.get('disable_checkboxes_in_table_cells', True):
                                            field_name = self._generate_unique_name(f"Cell_{int(cx0)}_{int(cy0)}", page_num)
                                            self.text_fields.append({
                                                'page': page_num,
                                                'x0': cx0 + 2, 'y0': cy0 + 2, 'x1': cx1 - 2, 'y1': cy1 - 2,
                                                'name': field_name,
                                                'source': 'table_col_fallback'
                                            })
                                            continue
                                        else:
                                            # If this region looks like a dense square grid, prefer a text fallback
                                            if self._is_in_square_grid(page_num, cx0, cy0, cx1, cy1):
                                                field_name = self._generate_unique_name(f"Cell_{int(cx0)}_{int(cy0)}", page_num)
                                                self.text_fields.append({
                                                    'page': page_num,
                                                    'x0': cx0 + 2, 'y0': cy0 + 2, 'x1': cx1 - 2, 'y1': cy1 - 2,
                                                    'name': field_name,
                                                    'source': 'grid_region_fallback'
                                                })
                                                continue
                                            self._add_checkbox(page_num, cb_x, cb_y, size, size, 'table_col')
                                            continue

                    # v35.27: Small square table cells -> checkboxes (generic, but NOT in quantity columns)
                    cell_w = cx1 - cx0
                    cell_h = cy1 - cy0
                    # Relax square tolerance slightly to allow slight rectangle grids and avoid every-other detection
                    is_small_square = (6 <= cell_w <= 40 and 6 <= cell_h <= 40 and abs(cell_w - cell_h) <= max(6, 0.12 * max(cell_w, cell_h)))
                    if is_small_square and grid_prefers_checkboxes and not cell_text.strip() and not words and not col_is_quantity:
                        # Require a visual cue (rect/lines or pre-detected checkbox) before creating checkbox
                        if not self._cell_has_visual_checkbox(page_num, cx0, cy0, cx1, cy1):
                            print(f"   [SkipCB] P{page_num} small cell at ({cx0:.1f},{cy0:.1f}) no visual checkbox; creating text field instead")
                        else:
                            # Avoid duplicate checkbox at same spot (allow tighter spacing for dense grids)
                            overlaps_cb = False
                            for cb in self.checkbox_positions:
                                if cb['page'] != page_num:
                                    continue
                                # Use smaller gap tolerance in dense grids
                                gap_tol = 1.5 if min(cell_w, cell_h) < 18 else 2.0
                                if not (cb['x1'] < cx0 - gap_tol or cb['x0'] > cx1 + gap_tol or cb['y1'] < cy0 - gap_tol or cb['y0'] > cy1 + gap_tol):
                                    overlaps_cb = True
                                    break
                            if not overlaps_cb:
                                if self.settings.get('disable_checkboxes_in_table_cells', True):
                                    field_name = self._generate_unique_name(f"Cell_{int(cx0)}_{int(cy0)}", page_num)
                                    self.text_fields.append({
                                        'page': page_num,
                                        'x0': cx0 + 2, 'y0': cy0 + 2, 'x1': cx1 - 2, 'y1': cy1 - 2,
                                        'name': field_name,
                                        'source': 'table_cell_fallback'
                                    })
                                    continue
                                else:
                                    size = min(cell_w, cell_h)
                                    cb_x = cx0 + (cell_w - size) / 2
                                    cb_y = cy0 + (cell_h - size) / 2
                                    # Respect square-grid heuristic
                                    if self._is_in_square_grid(page_num, cx0, cy0, cx1, cy1):
                                        field_name = self._generate_unique_name(f"Cell_{int(cx0)}_{int(cy0)}", page_num)
                                        self.text_fields.append({
                                            'page': page_num,
                                            'x0': cx0 + 2, 'y0': cy0 + 2, 'x1': cx1 - 2, 'y1': cy1 - 2,
                                            'name': field_name,
                                            'source': 'grid_region_fallback'
                                        })
                                        continue
                                    self._add_checkbox(page_num, cb_x, cb_y, size, size, 'table_cell')
                                    continue

                    # If we find an explicit visual checkbox inside a (possibly large) cell, create it.
                    if not cell_text.strip() and not words and self._cell_has_visual_checkbox(page_num, cx0, cy0, cx1, cy1):
                        # create a small checkbox centered in the cell (respect a max size for visual cues)
                        size = min(18, cell_w, cell_h)
                        cb_x = cx0 + (cell_w - size) / 2
                        cb_y = cy0 + (cell_h - size) / 2
                        overlaps_cb = False
                        for cb in self.checkbox_positions:
                            if cb['page'] != page_num:
                                continue
                            if not (cb['x1'] < cb_x - 2 or cb['x0'] > cb_x + size + 2 or cb['y1'] < cb_y - 2 or cb['y0'] > cb_y + size + 2):
                                overlaps_cb = True
                                break
                        if not overlaps_cb:
                            if self.settings.get('disable_checkboxes_in_table_cells', True):
                                field_name = self._generate_unique_name(f"Cell_{int(cx0)}_{int(cy0)}", page_num)
                                self.text_fields.append({
                                    'page': page_num,
                                    'x0': cx0 + 2, 'y0': cy0 + 2, 'x1': cx1 - 2, 'y1': cy1 - 2,
                                    'name': field_name,
                                    'source': 'visual_in_cell_fallback'
                                })
                                continue
                            else:
                                # Prevent checkboxes inside detected square-grid regions
                                if self._is_in_square_grid(page_num, cx0, cy0, cx1, cy1):
                                    field_name = self._generate_unique_name(f"Cell_{int(cx0)}_{int(cy0)}", page_num)
                                    self.text_fields.append({
                                        'page': page_num,
                                        'x0': cx0 + 2, 'y0': cy0 + 2, 'x1': cx1 - 2, 'y1': cy1 - 2,
                                        'name': field_name,
                                        'source': 'grid_region_fallback'
                                    })
                                    continue
                                self._add_checkbox(page_num, cb_x, cb_y, size, size, 'visual_in_cell')
                                continue

                    # If a checkbox exists inside this cell, do NOT create a text field
                    overlaps_cb = False
                    for cb_pos in self.checkbox_positions:
                        if cb_pos['page'] != page_num:
                            continue
                        if not (cb_pos['x1'] < cx0 - 2 or cb_pos['x0'] > cx1 + 2 or cb_pos['y1'] < cy0 - 2 or cb_pos['y0'] > cy1 + 2):
                            # v33.38: If the overlapping checkbox is just a small vector square
                            # and the cell is large, don't let it block a text field.
                            if cell_w > 40 and cb_pos.get('source') == 'vector':
                                # Likely a bullet or legend box. Ignore it and prefer the text field.
                                continue
                            overlaps_cb = True
                            break
                    if overlaps_cb and not self.settings.get('disable_checkboxes_in_table_cells', True):
                        # v33.39: Don't let checkboxes block text fields in large table cells
                        # (prevents decorative boxes/bullets from killing input fields).
                        if cell_w > 45:
                             # Allow fall-through to create text field; cleanup will remove the checkbox if needed
                             pass
                        else:
                             continue
                    
                    # v33.79: Check for underscore patterns BEFORE creating strict_grid field
                    # This ensures fields like "Volts ___ x Amps ___ = ___ Watts" get split properly
                    if '__' in cell_text and not cell_text.lower().startswith('name'):
                        print(f"   [StrictGridUnderscore] P{page_num+1} cell at y={cy0:.0f}-{cy1:.0f} has underscores, calling embedded detection...")
                        embedded_fields = self._detect_embedded_underscore_fields(
                            page_num, page, cx0, cy0, cx1, cy1
                        )
                        print(f"   [StrictGridUnderscore] Found {len(embedded_fields)} embedded fields")
                        if embedded_fields:
                            for idx, field in enumerate(embedded_fields):
                                h = field['y1'] - field['y0']
                                w = field['x1'] - field['x0']
                                # print(f"    Field {idx}: {w:.1f}x{h:.1f} source={field.get('source')} y={field['y0']:.1f}")
                                self.text_fields.append(field)
                                page_fields += 1
                            # v33.81: Mark this region as processed to prevent double detection in regular table loop
                            if not hasattr(self, '_underscore_processed_regions'):
                                self._underscore_processed_regions = []
                            self._underscore_processed_regions.append({
                                'page': page_num, 'x0': cx0, 'y0': cy0, 'x1': cx1, 'y1': cy1
                            })
                            continue  # Skip creating a single large field
                    
                    field_def = self._analyze_cell_for_field(
                        page_num, cx0, cy0, cx1, cy1,
                        cell_text, words, {}, 
                        prefer_below=is_large_box_section,
                        row_label=None, 
                        force_field_if_empty=True,
                        source_type='strict_grid'
                    )
                    
                    if field_def:
                        self.text_fields.append(field_def)
                        page_fields += 1
                
                # v33.37: Allow backfill from regular table detection 
                # for areas missed by the visual grid.
                # Overlap removal will handle duplicates.
                pass

                # v35.x: Optionally remove any pre-detected checkboxes that land inside table cells
                if self.settings.get('disable_checkboxes_in_table_cells', True):
                    removed = self._remove_checkboxes_in_table_cells()
                    if removed:
                        print(f"   [Clean] Removed {removed} pre-detected checkboxes inside table cells (converted to text fields)")

            # v22.4: Get page vertical lines for cell splitting
            # Filter for valid vertical lines (height > 5, width < 3)
            # v22.5: Include page_edges for missed dividers
            page_v_lines = []
            raw_lines = self.page_lines.get(page_num, []) + self.page_rects.get(page_num, []) + self.page_edges.get(page_num, [])
            for l in raw_lines:
                # Handle line, rect, and edge dictionaries
                lx0 = float(l.get('x0', l.get('x', 0)))
                lx1 = float(l.get('x1', l.get('x', lx0) + l.get('width', 0)))
                ly0 = float(l.get('top', l.get('y0', 0)))
                ly1 = float(l.get('bottom', l.get('y1', 0)))
                
                # Edges might not have height/width, use y0/y1
                w = abs(lx1 - lx0)
                h = abs(ly1 - ly0)
                
                if w < 3 and h > 5:
                    # Store center X
                    page_v_lines.append(((lx0 + lx1) / 2, ly0, ly1))
            
            for table in tables:
                cells = table.cells
                if not cells:
                    continue
                
                # v21.44: Merge "phantom cells" that are split by unrelated visual elements
                # This ensures fields span the full width of their visual area
                cells = self._merge_phantom_cells(page_num, cells)
                
                # v22.4: Split cells that contain a vertical line (fix for P13 and P21)
                # Some tables are detected as merged despite having a visual divider
                # v35.40: Improved to detect ALL vertical dividers in large cells
                final_cells = []
                for cell in cells:
                    cx0, cy0, cx1, cy1 = cell
                    cell_w = cx1 - cx0
                    cell_h = cy1 - cy0
                    
                    # Skip narrow cells (already split)
                    if cell_w < 30:
                        final_cells.append(cell)
                        continue
                    
                    # Find ALL vertical lines within this cell (for multi-split support)
                    split_positions = []
                    for vx, vy0, vy1 in page_v_lines:
                        # v35.40: Reduced margin to catch dividers closer to cell edges
                        if cx0 + 5 < vx < cx1 - 5:  # Changed from +10/-10 to +5/-5
                            ov_y0 = max(cy0, vy0)
                            ov_y1 = min(cy1, vy1)
                            overlap = max(0, ov_y1 - ov_y0)
                            # v35.40: Lowered threshold to 60% to catch partial dividers
                            if overlap > cell_h * 0.6:  # Changed from 0.8 to 0.6
                                split_positions.append(vx)
                    
                    # Sort split positions left to right
                    split_positions.sort()
                    
                    if split_positions:
                        # Split cell at each divider position
                        prev_x = cx0
                        for split_x in split_positions:
                            final_cells.append((prev_x, cy0, split_x, cy1))
                            prev_x = split_x
                        # Add the rightmost segment
                        final_cells.append((prev_x, cy0, cx1, cy1))
                        print(f"   ✂️  Split cell at x={split_positions} page {page_num+1} due to visual divider(s)")
                    else:
                        final_cells.append(cell)
                cells = final_cells
                
                # v20: Store ALL cells for coverage checking
                for cell in cells:
                    cx0, cy0, cx1, cy1 = cell
                    # v22.31: Store cells in TOP-DOWN coordinates (Visual)
                    self.table_cells.append({
                        'page': page_num,
                        'x0': cx0, 'y0': cy0, 'x1': cx1, 'y1': cy1,
                        'visual_x0': cx0  # Store visual X for column alignment logic if needed
                    })

                
                # Skip tables that extend beyond page boundaries (background elements)
                if table.bbox:
                    tx0, ty0, tx1, ty1 = table.bbox
                    if tx0 < 0 or tx1 > page_width or ty0 < 0 or ty1 > page_height:
                        continue
                
                # Find header row(s) - some tables have a "super-header" row above actual column headers
                # Get all unique Y values (rows) sorted
                y_values = sorted(set(c[1] for c in cells))
                
                # Skip rows that are TRUE headers (title rows, section headers)
                # BUT NOT data entry rows that happen to span the width
                # TRUE HEADER: Single cell with short centered text (like "I. CUSTOMER INFO")
                # DATA ENTRY: Multiple cells OR cells with form field labels
                table_width = table.bbox[2] - table.bbox[0] if table.bbox else 500
                header_y_values = set()  # Track which rows are TRUE headers (for skipping)
                first_data_row_idx = None  # Track first non-spanning data row
                column_header_row_idx = None  # Track the column header row for naming
                
                # SCAN ALL ROWS to find all header rows (spanning OR column headers)
                for row_idx, row_y in enumerate(y_values):
                    row_cells = [c for c in cells if abs(c[1] - row_y) < 2]
                    row_width = sum(c[2] - c[0] for c in row_cells)
                    
                    # Check if this looks like a TRUE header row
                    is_true_header = False
                    
                    # TYPE 1: Single spanning cell (section header/title)
                    if len(row_cells) == 1 and row_width > table_width * 0.8:
                        try:
                            cell = row_cells[0]
                            cell_text = page.within_bbox((cell[0]-1, cell[1]-1, cell[2]+1, cell[3]+1)).extract_text() or ""
                            cell_text = cell_text.strip()
                            # Section headers typically: start with Roman numeral, short, all caps parts
                            # Or are long description paragraphs (>100 chars)
                            # Also: short titles like "Application" (single word or few words, no colon)
                            is_short_title = (
                                len(cell_text) < 50 and  # Slightly longer for section titles
                                ':' not in cell_text and  # No colon (not a label)
                                '\n' not in cell_text and  # Single line
                                not any(c.isdigit() for c in cell_text[:5])  # Doesn't start with numbers
                            )
                            is_section_header = (
                                cell_text.startswith(('I.', 'II.', 'III.', 'IV.', 'V.', 'VI.', 'VII.', 'VIII.')) or
                                cell_text.isupper() or
                                len(cell_text) > 100 or  # Long paragraph description
                                'ADMINISTRATIVE' in cell_text.upper() or
                                is_short_title or
                                (len(row_cells) == 1 and row_width > table_width * 0.9 and ':' not in cell_text) # Spanning title
                            )
                            is_true_header = is_section_header
                        except:
                            is_true_header = True  # Assume header if can't read
                    
                    # TYPE 2: Multi-cell column header row (all cells have text, no empty data cells)
                    # This catches rows like "Type | Unit Size | Efficiency | ..." 
                    # BUT NOT form input rows like "Company Name | Contact Person | Email"
                    # where cells have short labels with lots of empty space for user input
                    elif len(row_cells) >= 2:  # Multi-cell row (Relaxed from 3 to 2)
                        cells_with_text = 0
                        cells_empty = 0
                        cells_with_colon = 0
                        cells_are_form_inputs = 0  # Track cells that look like form inputs
                        cells_are_data = 0  # Track cells that contain checkbox patterns or numeric data
                        
                        for cell in row_cells:
                            cell_w = cell[2] - cell[0]
                            if cell_w < 10:  # Skip tiny separator cells
                                continue
                            try:
                                cell_text = page.within_bbox((cell[0]-1, cell[1]-1, cell[2]+1, cell[3]+1)).extract_text() or ""
                                cell_text = cell_text.strip()
                                if cell_text:
                                    cells_with_text += 1
                                    if ':' in cell_text:
                                        cells_with_colon += 1
                                    
                                    # IMPROVED: Check if cell contains checkbox patterns or numeric data
                                    # These are DATA cells, not header labels
                                    cell_lower = cell_text.lower()
                                    is_checkbox_pattern = any(p in cell_lower for p in ['yes', 'no', 'n/a', 'pass', 'fail'])
                                    is_numeric = cell_text.replace('.', '').replace(',', '').replace('-', '').isdigit()
                                    if is_checkbox_pattern or is_numeric:
                                        cells_are_data += 1
                                    
                                    # Check if this looks like a FORM INPUT cell:
                                    # - Short label text (< 25 chars, excluding whitespace/newlines)
                                    # - Cell width much larger than needed for text
                                    # - Cell height > 18 (enough for handwriting/typing)
                                    # Strip newlines and spaces for accurate text length
                                    clean_text = cell_text.replace(' ', '').replace('\n', '')
                                    text_len = len(clean_text)
                                    cell_h = cell[3] - cell[1]
                                    # Estimate text width: ~6pt per character (first line only)
                                    first_line = cell_text.split('\n')[0].strip()
                                    first_line_len = len(first_line.replace(' ', ''))
                                    estimated_text_width = first_line_len * 6
                                    
                                    # Form input detection:
                                    # Method 1: Cell is much wider than text needs
                                    # Method 2: Text only fills small portion of cell area
                                    text_area_ratio = (estimated_text_width * 12) / (cell_w * cell_h) if cell_w * cell_h > 0 else 1
                                    
                                    is_form_input = (
                                        cell_h > 18 and (
                                            first_line.lower().startswith('name') or  # Force accept Name fields
                                            (text_len < 25 and (
                                                cell_w > estimated_text_width * 1.5 or  # Relaxed from 2x to 1.5x
                                                text_area_ratio < 0.4  # Text uses < 40% of cell area
                                            ))
                                        )
                                    )
                                    if is_form_input:
                                        cells_are_form_inputs += 1
                                    


                                else:
                                    cells_empty += 1
                            except:
                                cells_empty += 1
                        
                        # Column header row detection
                        total_wide_cells = cells_with_text + cells_empty
                        
                        # PEEK AHEAD: Check if next row has empty cells (indicating data entry)
                        has_empty_row_below = False
                        if row_idx + 1 < len(y_values):
                            next_row_y = y_values[row_idx + 1]
                            next_row_cells = [c for c in cells if abs(c[1] - next_row_y) < 2]
                            next_cells_empty = 0
                            for nc in next_row_cells:
                                try:
                                    nt = (page.within_bbox((nc[0]-1, nc[1]-1, nc[2]+1, nc[3]+1)).extract_text() or "").strip()
                                    # Treat underscores as empty (placeholder for data entry)
                                    nt_clean = nt.replace('_', '').strip()
                                    if not nt_clean: next_cells_empty += 1
                                except: pass
                            # If next row has empty cells (and this row has text), this is likely a header
                            if next_cells_empty > 0 and len(next_row_cells) >= 2:
                                has_empty_row_below = True
                        
                        # STANDARD CHECK: No empty cells, no colons (strict)
                        is_standard_header = (
                            total_wide_cells >= 2 and 
                            cells_empty == 0 and 
                            cells_with_colon == 0 and 
                            cells_are_data == 0 and
                            cells_are_form_inputs < total_wide_cells * 0.5
                        )
                        
                        # RELAXED CHECK (PEEK AHEAD): Allow colons if row below is empty data
                        # This catches headers like "Combustion Efficiency:" -> [Empty Cell]
                        is_peek_ahead_header = (
                            has_empty_row_below and
                            total_wide_cells >= 2 and
                            cells_empty == 0 and
                            cells_are_data == 0
                        )

                        if is_standard_header or is_peek_ahead_header:
                            is_true_header = True
                            column_header_row_idx = row_idx
                        # else: This is a form row with labels, not a header
                    
                    if is_true_header:
                        header_y_values.add(row_y)  # Mark this row to skip
                    elif first_data_row_idx is None:
                        # Remember the first non-header row (potential data row)
                        first_data_row_idx = row_idx
                
                # Prefer the column header row for naming; otherwise use row 0
                # Column header = multi-cell header with column names (Date, Score, etc.)
                # NOT spanning section headers which are just titles
                header_row_idx = column_header_row_idx if column_header_row_idx is not None else 0
                
                # Get header cells from the column header row for column naming
                header_row_y = y_values[header_row_idx] if header_row_idx < len(y_values) else y_values[0]
                header_cells = [c for c in cells if abs(c[1] - header_row_y) < 2]
                
                # Determine if this row is a TRUE header
                # Pass all_cells and all_y_values to detect form layouts
                first_row_is_header, headers_map, skip_columns = self._analyze_header_row(
                    page, header_cells, all_cells=cells, all_y_values=y_values
                )
                
                # If it's a header, add it to the skip set
                if first_row_is_header:
                    header_y_values.add(header_row_y)
                
                # Check if this is a "Required Information on Invoice" table
                # These tables should ONLY have fields in the underscore fill areas
                is_invoice_table = self._is_invoice_info_table(page, table)
                
                if is_invoice_table:
                    # v22.5: Store boundary for global suppression
                    tx0, ty0, tx1, ty1 = table.bbox if table.bbox else (0,0,0,0)
                    self.invoice_table_bboxes.append({'page': page_num, 'x0': tx0, 'y0': ty0, 'x1': tx1, 'y1': ty1})
                    
                    # For invoice tables, detect underscore fill-in fields only
                    underscore_fields = self._detect_underscore_fields(page_num, page, table)
                    for field in underscore_fields:
                        self.text_fields.append(field)
                        page_fields += 1
                    continue  # Skip normal cell processing for this table
                
                # v34.4: GENERIC COLUMN ANALYSIS
                # Analyze table columns to find which ones have dense text (skip) vs fillable content
                column_analysis = self._analyze_table_columns(page_num, table, page)
                dense_text_columns = column_analysis.get('skip_columns', [])
                image_upload_columns = column_analysis.get('image_columns', [])
                
                # DETECT EMPTY DATA ROWS UNDER COLUMN HEADERS
                # If a table has column headers followed by MOSTLY empty rows, create numbered fields
                # under each header (e.g., "Installation Type_1", "Installation Type_2", etc.)
                # Only treat as empty-data-row table if >50% of data rows are empty (pure data entry table)
                has_empty_data_rows = False
                if first_row_is_header and headers_map:
                    # Get all row Y values
                    all_row_y = sorted(set(c[1] for c in cells))
                    header_row_y = header_cells[0][1] if header_cells else all_row_y[0]
                    data_rows = [y for y in all_row_y if y > header_row_y + 5]
                    
                    # Check if we have consecutive empty rows (data entry rows)
                    # First, check first 10 rows to determine if this is an empty-data-row table
                    sample_empty = 0
                    sample_total = 0
                    for row_y in data_rows[:10]:  # Sample first 10 rows
                        row_cells = [c for c in cells if abs(c[1] - row_y) < 2]
                        if len(row_cells) < 2:
                            continue
                        sample_total += 1
                        all_empty = True
                        for cell in row_cells:
                            try:
                                cell_text = page.within_bbox((cell[0]-1, cell[1]-1, cell[2]+1, cell[3]+1)).extract_text() or ""
                                if cell_text.strip():
                                    all_empty = False
                                    break
                            except:
                                pass
                        if all_empty:
                            sample_empty += 1
                    
                    # If >50% of sampled rows are empty, collect ALL empty rows (not just first 10)
                    empty_rows = []
                    if sample_total > 0 and sample_empty / sample_total > 0.5:
                        for row_y in data_rows:  # Check ALL data rows
                            row_cells = [c for c in cells if abs(c[1] - row_y) < 2]
                            if len(row_cells) < 2:
                                continue
                            all_empty = True
                            for cell in row_cells:
                                try:
                                    cell_text = page.within_bbox((cell[0]-1, cell[1]-1, cell[2]+1, cell[3]+1)).extract_text() or ""
                                    if cell_text.strip():
                                        all_empty = False
                                        break
                                except:
                                    pass
                            if all_empty and len(row_cells) >= 2:
                                empty_rows.append((row_y, row_cells))
                    
                    # Process if we found 2+ empty rows
                    if len(empty_rows) >= 2:
                        has_empty_data_rows = True
                        
                        # v16.1: Build local headers map from rows ABOVE the first empty row
                        # Motor Info has multi-row header (Installation Type/N=New/S=Stock, etc)
                        local_headers_map = {}
                        first_empty_y = min(row_y for row_y, _ in empty_rows)
                        
                        # Find all cells in the header area (between Motor Info title and first empty row)
                        # Headers are typically 30-50pt above the first data row
                        header_area_min_y = first_empty_y - 50
                        header_area_max_y = first_empty_y - 5
                        
                        header_cells = [c for c in cells if header_area_min_y < c[1] < header_area_max_y]
                        
                        # Group header cells by x position (column)
                        # v16.1: Skip wide spanning cells (like instructions) and filter out instruction text
                        from collections import defaultdict
                        cols_text = defaultdict(list)
                        for hcell in header_cells:
                            hx0, hy0, hx1, hy1 = hcell
                            cell_width = hx1 - hx0
                            
                            # Skip wide spanning cells (>100pt) - these are usually instructions
                            if cell_width > 100:
                                continue
                            
                            try:
                                htext = (page.within_bbox((hcell[0]-1, hcell[1]-1, hcell[2]+1, hcell[3]+1)).extract_text() or "").strip().replace('\n', ' ')
                                # Skip cells with instruction-like text
                                if htext and not htext.lower().startswith('note') and 'eligibility' not in htext.lower():
                                    cols_text[round(hx0)].append((hy0, htext))
                            except:
                                pass
                        
                        # Combine text from each column to form full header name
                        for col_x, texts in cols_text.items():
                            # Sort by y and take the first/primary text
                            sorted_texts = sorted(texts, key=lambda t: t[0])
                            # Use first row text as primary header, clean it up
                            primary_header = sorted_texts[0][1].split('(')[0].strip()  # Remove parenthetical
                            if primary_header and len(primary_header) < 30:  # Skip long text
                                local_headers_map[col_x] = primary_header
                        
                        for row_idx, (row_y, row_cells) in enumerate(empty_rows, start=1):
                            for cell in row_cells:
                                cx0, cy0, cx1, cy1 = cell
                                
                                # Skip tiny cells
                                if (cx1 - cx0) < 30 or (cy1 - cy0) < 10:
                                    continue
                                
                                # v16.1: Skip if position already has a field
                                if self._position_has_field(page_num, cx0, cy0, cx1, cy1):
                                    continue
                                
                                # v16.3: Clip field width if it overlaps the next column header
                                # This fixes the Quantity column issue where fields extend into Incentive column
                                # Gather all header X positions
                                all_header_xs = sorted(list(local_headers_map.keys()) + 
                                                     [k if isinstance(k, (int, float)) else k[0] 
                                                      for k in headers_map.keys()])
                                
                                # Find the next header cutoff
                                # Look for a header that starts INSIDE this cell (after the cell start)
                                next_header_x = None
                                for hx in all_header_xs:
                                    # If header starts significantly after cell start (>20pt means it's a different column)
                                    # AND significantly before cell end (>10pt means overlap is real)
                                    if hx > cx0 + 30 and hx < cx1 - 10:
                                        # v21.45: Only clip if there is a physical divider in THIS row range
                                        has_physical_divider = False
                                        for vx, vy0, vy1 in page_v_lines:
                                            if abs(vx - hx) < 5:
                                                overlap = max(0, min(cy1, vy1) - max(cy0, vy0))
                                                if overlap > (cy1 - cy0) * 0.5:
                                                    has_physical_divider = True
                                                    break
                                        if has_physical_divider:
                                            next_header_x = hx
                                            break
                                
                                if next_header_x:
                                    cx1 = next_header_x - 4  # Clip to start of next column with small gap
                                
                                # v18: REMOVED hardcoded coordinate clipping (was v16.3 Motors fix)
                                # The generic next_header_x logic above handles column splitting
                                
                                # Find the column header for this cell using local_headers_map
                                # v16.1: Use headers extracted from rows above empty data rows
                                
                                # Find the column header for this cell using local_headers_map
                                # v16.1: Use headers extracted from rows above empty data rows
                                header_name = None
                                for hx_key, hname in local_headers_map.items():
                                    if abs(cx0 - hx_key) < 20:  # Same column
                                        header_name = hname
                                        break
                                
                                # Fall back to global headers_map if not found locally
                                if not header_name:
                                    for hx_key, hname in headers_map.items():
                                        hx = hx_key if isinstance(hx_key, (int, float)) else hx_key[0] if isinstance(hx_key, tuple) else hx_key
                                        if abs(cx0 - hx) < 20:
                                            header_name = hname
                                            break
                                
                                if not header_name:
                                    # No header found, use generic name
                                    header_name = "Field"
                                
                                # Create numbered field: HeaderName_1, HeaderName_2, etc.
                                base_name = self._clean_field_name(header_name)
                                field_name = f"{base_name}_{row_idx}"
                                field_name = self._generate_unique_name(field_name, page_num)
                                
                                # Detect format based on header name
                                field_format, format_opts = self._detect_field_format(None, header_name)
                                
                                self.text_fields.append({
                                    'page': page_num,
                                    'x0': cx0,
                                    'y0': cy0,
                                    'x1': cx1,
                                    'y1': cy1,
                                    'name': f"{field_name}_TDR",
                                    'label': header_name,
                                    'format': field_format,
                                    'format_opts': format_opts,
                                    'source': 'table_data_row'
                                })
                                page_fields += 1
                
                # v16.1: DON'T skip normal cell processing even if empty data rows were found
                # Many forms (like Motors) have BOTH empty data rows AND labeled cells
                # Process empty rows above, then ALSO process labeled cells below
                # (was: if has_empty_data_rows: continue)
                
                # DETECT SUB-TABLE HEADERS WITH EMPTY DATA ROWS
                # Look for LOCAL sections with column headers (containing "(Table X)") followed by empty rows
                # This handles cases like TABLE 4 in VFD forms where empty data entry rows appear mid-page
                sub_table_fields_created = 0
                if not has_empty_data_rows:
                    # Find cells that look like column headers (contain "(Table X)" pattern)
                    from collections import defaultdict
                    potential_headers = []
                    cells_by_row = defaultdict(list)  # Group all cells by row for later expansion
                    
                    for cell in cells:
                        try:
                            text = (page.within_bbox((cell[0]-1, cell[1]-1, cell[2]+1, cell[3]+1)).extract_text() or "").strip()
                            hx0, hy0, hx1, hy1 = cell
                            row_y_key = round(hy0 / 5) * 5
                            cells_by_row[row_y_key].append((hy0, hx0, hx1, text))
                            
                            # Look for patterns like "(Table 1)", "(Table 2)", etc.
                            if '(Table' in text:
                                potential_headers.append((hy0, hx0, hx1, text))
                        except:
                            pass
                    
                    # Group headers by row (within 5pt tolerance)
                    headers_by_row = defaultdict(list)
                    for y, x0, x1, text in potential_headers:
                        row_y_key = round(y / 5) * 5
                        headers_by_row[row_y_key].append((y, x0, x1, text))
                    
                    # EXPAND: If a row has 3+ "(Table X)" headers, include ALL cells from that row
                    # This captures headers like "Location/Designation" that don't have "(Table X)"
                    for row_y_key in list(headers_by_row.keys()):
                        if len(headers_by_row[row_y_key]) >= 3:
                            # Get all cells from this row
                            all_row_cells = cells_by_row.get(row_y_key, [])
                            existing_x0s = {h[1] for h in headers_by_row[row_y_key]}
                            
                            # Add any cells not already in headers
                            for y, x0, x1, text in all_row_cells:
                                if x0 not in existing_x0s and text.strip():
                                    headers_by_row[row_y_key].append((y, x0, x1, text))
                    
                    # For each potential header row with 3+ headers
                    for row_y_key in sorted(headers_by_row.keys()):
                        local_headers = headers_by_row[row_y_key]
                        if len(local_headers) < 3:  # Need at least 3 column headers
                            continue
                        
                        # Get exact y of this header row
                        header_row_y = local_headers[0][0]
                        
                        # Build local headers_map for this sub-table
                        local_headers_map = {}
                        for _, hx0, hx1, htext in local_headers:
                            clean_name = self._clean_field_name(htext)
                            local_headers_map[(hx0, hx1)] = clean_name
                        
                        # Find rows immediately below this header row
                        rows_below = sorted(set(c[1] for c in cells if c[1] > header_row_y + 5))
                        
                        # Check for consecutive empty rows
                        local_empty_rows = []
                        for rb in rows_below[:6]:  # Check up to 6 rows below
                            row_cells = [c for c in cells if abs(c[1] - rb) < 2]
                            if len(row_cells) < 3:  # Need at least 3 cells to match headers
                                break  # Stop at sparse row
                            
                            all_empty = True
                            for rc in row_cells:
                                try:
                                    text = (page.within_bbox(rc).extract_text() or "").strip()
                                    if text:
                                        all_empty = False
                                        break
                                except:
                                    pass
                            
                            if all_empty:
                                local_empty_rows.append((rb, row_cells))
                            else:
                                break  # Stop at non-empty row
                        
                        # If we have 2+ consecutive empty rows, create fields
                        if len(local_empty_rows) >= 2:
                            print(f"      Sub-table at y={header_row_y:.0f}: {len(local_headers)} headers, {len(local_empty_rows)} empty rows")
                            for row_idx, (row_y, row_cells) in enumerate(local_empty_rows, start=1):
                                for cell in row_cells:
                                    cx0, cy0, cx1, cy1 = cell
                                    
                                    # Skip tiny cells
                                    if (cx1 - cx0) < 30 or (cy1 - cy0) < 8:
                                        continue
                                    
                                    # Find the column header for this cell
                                    header_name = None
                                    for (hx0, hx1), hname in local_headers_map.items():
                                        if abs(cx0 - hx0) < 20:  # Same column
                                            header_name = hname
                                            break
                                    
                                    if not header_name:
                                        header_name = "Field"
                                    
                                    # Create numbered field: HeaderName_1, HeaderName_2, etc.
                                    base_name = self._clean_field_name(header_name)
                                    field_name = f"{base_name}_{row_idx}"
                                    field_name = self._generate_unique_name(field_name, page_num)
                                    
                                    # Check if this position already has a field
                                    if self._position_has_field(page_num, cx0, cy0, cx1, cy1):
                                        continue
                                    
                                    # Detect format based on header name
                                    field_format, format_opts = self._detect_field_format(None, header_name)
                                    
                                    self.text_fields.append({
                                        'page': page_num,
                                        'x0': cx0,
                                        'y0': cy0,
                                        'x1': cx1,
                                        'y1': cy1,
                                        'name': field_name,
                                        'label': header_name,
                                        'format': field_format,
                                        'format_opts': format_opts,
                                        'source': 'sub_table_data_row'
                                    })
                                    page_fields += 1
                                    sub_table_fields_created += 1
                    
                    if sub_table_fields_created > 0:
                        print(f"      Created {sub_table_fields_created} sub-table fields")
                
                print(f"    Table: {len(cells)} cells, bbox={table.bbox}")
                
                # v16.8: Determine consistent placement for this table section
                # Pre-scan cells to decide if we should enforce "Below Label" placement for ALL cells.
                # This ensures consistent look (all beside or all below) within a table.
                # v21.47: REVISED - Use capability-based majority voting instead of keyword detection
                # Count how many cells CAN support below-label placement (cell height > 25pt)
                # If most cells can, use below-label for ALL cells uniformly
                cells_can_below = 0
                cells_total = 0
                
                for cell in cells:
                    try:
                        cx0, cy0, cx1, cy1 = cell
                        
                        # cx0 check removed from here, moving to _analyze_cell_for_field for robustness
                        # if page_num == 1 and cx0 < 480: continue 
                        
                        cell_w = cx1 - cx0
                        cell_w = cx1 - cx0
                        cell_h = cy1 - cy0
                        
                        # Skip tiny cells and header cells
                        if cell_w < 30 or cell_h < 12:
                            continue
                        
                        cells_total += 1
                        
                        # A cell can support below-label if height > 25pt
                        # (enough room for label at top + field below)
                        if cell_h > 25:
                            cells_can_below += 1
                    except:
                        pass
                
                # If majority of cells can support below-label, use it for ALL
                section_prefers_below = cells_total > 0 and (cells_can_below / cells_total) >= 0.5
                
                # Process data cells (normal tables)
                for cell in cells:
                    x0, y0, x1, y1 = cell
                    
                    # v34.5: GENERIC - Skip cells in dense text columns (no fill indicators)
                    # This prevents fields from being created in descriptive/instructional columns
                    is_in_dense_column = False
                    for dc_x0, dc_x1 in dense_text_columns:
                        # Check if cell overlaps with dense text column
                        if not (x1 < dc_x0 + 5 or x0 > dc_x1 - 5):
                            is_in_dense_column = True
                            break
                    if is_in_dense_column:
                        continue
                    
                    # v34.6: GENERIC - Check if cell is in an image upload column
                    # These should become image upload fields, not regular text fields
                    is_in_image_column = False
                    for ic_x0, ic_x1 in image_upload_columns:
                        if not (x1 < ic_x0 + 5 or x0 > ic_x1 - 5):
                            is_in_image_column = True
                            break
                    
                    if is_in_image_column:
                        # Create image upload field for this cell
                        cell_w = x1 - x0
                        cell_h = y1 - y0
                        if cell_w > 50 and cell_h > 50:  # Reasonable size for image
                            field_name = self._generate_unique_name("Photo_Upload", page_num)
                            self.text_fields.append({
                                'page': page_num,
                                'x0': x0 + 2,
                                'y0': y0 + 2,
                                'x1': x1 - 2,
                                'y1': y1 - 2,
                                'name': field_name,
                                'label': 'Photo Upload',
                                'format': 'image',
                                'format_opts': {},
                                'source': 'image_column',
                                'is_image_box': True
                            })
                            page_fields += 1
                            continue
                    
                    # v21.7: Skip cells that are inside or mostly overlap a header bar
                    # These are likely section titles or table headers with dark backgrounds
                    is_in_header_bar = False
                    for bar in header_bars:
                        # Check if cell center is inside the bar
                        mid_x = (x0 + x1) / 2
                        mid_y = (y0 + y1) / 2
                        if bar['x0'] - 2 <= mid_x <= bar['x1'] + 2 and bar['y0'] - 2 <= mid_y <= bar['y1'] + 2:
                            is_in_header_bar = True
                            break
                    if is_in_header_bar:
                        # v22.14: EXCEPTION - If cell has a colon label, allow it even if in header bar
                        # (Handles "Distributor Name:" which has gray background)
                        should_skip = True
                        try:
                            # Quick peek at content
                            p_crop = page.within_bbox((x0, y0, x1, y1))
                            p_text = (p_crop.extract_text() or "").strip()
                            if ':' in p_text or (y1 - y0) > 25:
                                should_skip = False
                        except:
                            pass
                        
                        if should_skip:
                            continue
                    
                    # v16.3: Clip cell width if it overlaps the next column header (universal fix)
                    # v21.44: Only clip if we are below the header row that defined these headers
                    # This prevents headers from a bottom table from clipping form fields at the top
                    # v21.45: Also skip clipping for form-style cells with descriptive labels
                    # (These are standalone form fields that should span their full detected width)
                    next_header_x = None
                    
                    # Skip zero-area or invalid cells
                    if x1 <= x0 + 0.5 or y1 <= y0 + 0.5:
                        continue

                    # Extract cell text early to check for form-style labels
                    try:
                        cell_crop_check = page.within_bbox((x0-1, y0-1, x1+1, y1+1))
                        cell_text_check = (cell_crop_check.extract_text() or "").strip().lower()
                    except:
                        cell_text_check = ""
                    
                    # Skip clipping for form-style cells with descriptive labels
                    # v21.46: EXPANDED - Also treat tall cells (>25pt) with colon labels as form cells
                    # These cells have space for below-label placement and shouldn't be clipped
                    form_label_keywords = ['customer', 'signature', 'name', 'title', 'address', 'city', 'state', 'zip', 'email', 'phone', 'tax id', 'company', 'account', 'number', 'date', 'inspector', 'organization', 'partner', 'relationship', 'applicant', 'performed', 'work', 'type', 'year', 'program', 'ref', 'application', 'inspection']
                    cell_height_check = y1 - y0
                    is_tall_label_cell = cell_height_check > 25 and ':' in cell_text_check
                    # v22.4: Also skip clipping for VERY TALL cells (h>100) which are large text areas
                    # like Description boxes - these should span their full cell width, not be clipped
                    is_large_text_area = cell_height_check > 100
                    is_form_label_cell = any(kw in cell_text_check for kw in form_label_keywords) or is_tall_label_cell or is_large_text_area
                    
                    if y0 > header_row_y + 5 and not is_form_label_cell:
                        # This handles cases where pdfplumber merges columns (like Quantity merging into Incentive)
                        # Gather all header X positions
                        all_header_xs = sorted([k if isinstance(k, (int, float)) else k[0] 
                                              for k in headers_map.keys()])
                        
                        for hx in all_header_xs:
                            # If header starts significantly after cell start (>20pt)
                            # AND significantly before cell end (>10pt)
                            if hx > x0 + 30 and hx < x1 - 10:
                                # v21.45: Only clip if there is a physical divider in THIS row range
                                has_physical_divider = False
                                for vx, vy0, vy1 in page_v_lines:
                                    if abs(vx - hx) < 5:
                                        overlap = max(0, min(y1, vy1) - max(y0, vy0))
                                        if overlap > (y1 - y0) * 0.5:
                                            has_physical_divider = True
                                            break
                                if has_physical_divider:
                                    next_header_x = hx
                                    break
                    
                    if next_header_x:
                        x1 = next_header_x - 4  # Clip to start of next column
                    
                    # v18: REMOVED hardcoded x1=535 clipping (was v16.3 Motors Quantity fix)
                    # This was inappropriately clipping Field Inspect cells on Page 1
                    # The generic next_header_x logic above handles proper column splitting
                    
                    # Skip header row(s) - these are spanning section headers that should never have fields
                    # Note: This check runs regardless of first_row_is_header because header_y_values
                    # contains ALL spanning section headers found during the scanning phase
                    # v21.7: Increased tolerance to 5pt
                    # Fix UnboundLocalError: Ensure cell_crop_check is defined
                    cell_crop_check = page.within_bbox((x0, y0, x1, y1))
                    cell_text_check = (cell_crop_check.extract_text() or "").strip().lower()
                    
                    # v21.7: Increased tolerance to 5pt
                    if any(abs(y0 - hy) < 5 for hy in header_y_values):
                        # EXCEPTION: If cell has embedded underscores, process it anyway
                        # (e.g. Calculation Worksheets that look like headers but have fields)
                        # NOTE: Use cell_text_check as cell_text isn't extracted yet
                        # v22.11: Explicitly allow "Name" fields which might be mistaken for headers
                        if '____' in cell_text_check or cell_text_check.startswith('name') or 'qualifying' in cell_text_check:
                            pass
                        else:
                            continue
                    
                    # Skip small cells
                    if (x1 - x0) < 30 or (y1 - y0) < 12:
                        continue
                    
                    # v22.12: Distributor Form Page 2 Table Fix
                    # Skip Columns 1 & 2 ("HVAC Measure Type", "Efficiency Rating") which are just labels
                    # Keep Column 3 ("Quantity") - Check x0 (Column start)
                    # Col 2 ends around 425, Col 3 starts around 429
                    pass
                    
                    # Skip Pass/Fail columns (handled by checkboxes)
                    if self._is_skip_column(x0, skip_columns):
                        continue

                    # Extract cell content first
                    try:
                        cell_crop = page.within_bbox((x0-1, y0-1, x1+1, y1+1))
                        cell_text = (cell_crop.extract_text() or "").strip()
                        words = cell_crop.extract_words()
                    except:
                        cell_text = ""
                        words = []

                    # v21.47: SKIP TEXT-HEAVY CELLS (Instructions, Lists, Descriptions)
                    # Form fields (labels) are typically short.
                    # "Maintenance Tasks for Furnaces: ..." is > 100 chars and has bullets.
                    if len(cell_text) > 80 or '■' in cell_text or cell_text.count('\n') > 4:
                        # EXCEPTION: If it contains "Describe" or "Notes" or underscores, it might be a large text area
                        is_large_text_field = (
                            '____' in cell_text or 
                            'describe:' in cell_text.lower() or 
                            'notes:' in cell_text.lower() or 
                            'comments:' in cell_text.lower() or
                            cell_text.lower().startswith('name') or
                            'qualifying' in cell_text.lower()
                        )

                        if not is_large_text_field:
                            # It's likely just instructions/text -> SKIP
                            continue

                    # Skip cells with checkboxes UNLESS the cell also has a "Label:" pattern
                    # (like "■■ Other ... Describe:" where we want a field after "Describe:")
                    if self._cell_overlaps_checkbox(page_num, x0, y0, x1, y1):
                        # Check if there's a colon-label with space to the right
                        has_label_field = False
                        for word in words:
                            if word['text'].endswith(':') and len(word['text']) > 1:
                                space_right = x1 - float(word['x1'])
                                if space_right > 15:  # Lowered from 50 to allow +Basement Sq. Ft. cells
                                    has_label_field = True
                                    break
                        if not has_label_field:
                            continue  # Skip cells with only checkboxes
                    
                    # SKIP: First column cells without ":" are typically just labels
                    # Find if this is in the first (leftmost) column
                    # EXCEPTION 1: Cells starting with "$" are currency input fields
                    # EXCEPTION 2: Cells that look like form fields (short labels like City, State, Zip)
                    # EXCEPTION 3: Cells that are WIDE with short text (field area with label)
                    # EXCEPTION 4: Cells with label at top and space below for input
                    min_cell_x0 = min(c[0] for c in cells)
                    is_first_column = abs(x0 - min_cell_x0) < 5
                    starts_with_currency = cell_text.startswith('$')
                    
                    # Check if this is a fillable field cell (short label, wide cell)
                    # Common form fields: City, State, Zip, Name, Address, Phone, Email, Date, etc.
                    cell_width = x1 - x0
                    cell_height = y1 - y0
                    cell_height = y1 - y0
                    text_len = len(cell_text.replace(' ', ''))
                    
                    # Also check if text is concentrated at top of cell (label with input below)
                    text_at_top = False
                    if words and cell_height > 20:
                        # Check if all text is in top half of cell
                        max_text_y = max(float(w.get('bottom', w.get('y1', y0))) for w in words)
                        cell_mid_y = y0 + cell_height / 2
                        text_at_top = max_text_y < cell_mid_y + 5
                    
                    is_form_field_cell = (
                        cell_height > 15 and  # Tall enough for input
                        (cell_width > 80 or cell_height > 20) and  # Wide or tall enough
                        (text_len < 50 or text_at_top)  # Short text OR text at top with space below
                    )
                    
                    # Skip ONLY if first column AND not a form field pattern
                    # v22.8: Allow "Name" fields even without colons (e.g. ConEd instructions)
                    if is_first_column and cell_text and ':' not in cell_text and not starts_with_currency and not cell_text.lower().startswith('name'):
                        if not is_form_field_cell:
                            # First column with text but no colon and not a form field = label-only, skip
                            continue
                    
                    # v21.49 / v36.1 P35: Only skip when cell text IS exactly a section header (not contains).
                    # So "Equipment Information" header row is skipped, but "SEER (if known):" etc. get fields.
                    SKIP_LABELS_EXACT = ['combustion efficiency', 'maintenance tasks', 'equipment information', 'pre-service', 'post-service']
                    cell_clean = cell_text.strip().lower()
                    if any(cell_clean == skip for skip in SKIP_LABELS_EXACT):
                        continue

                    # Check if cell has embedded underscore fill patterns
                    # (like "Volts __________ x Amps __________")
                    # v22.10: Skip for "Name" fields (treat as full cell field)
                    # v33.75: Relaxed to 2 underscores to match _detect_embedded_underscore_fields
                    if '__' in cell_text and not cell_text.lower().startswith('name'):
                        # Detect underscore patterns within this cell
                        embedded_fields = self._detect_embedded_underscore_fields(
                            page_num, page, x0, y0, x1, y1
                        )
                        for field in embedded_fields:
                            self.text_fields.append(field)
                            page_fields += 1
                        continue  # Don't also create regular field
                    
                    # SPECIAL CASE: @ symbol in cell - create fields on BOTH sides
                    # This handles page 49's "Before @ After" pattern
                    if '@' in cell_text:
                        at_fields = self._detect_at_symbol_fields(
                            page_num, x0, y0, x1, y1, words, headers_map
                        )
                        for field in at_fields:
                            self.text_fields.append(field)
                            page_fields += 1
                        continue  # Don't also create regular field
                    
                    # Determine field position and name
                    # v16.8: Use consistent placement decided by pre-scan
                    prefer_below = section_prefers_below
                    
                    # NEW: Find row label from column 0 (leftmost cell in same row)
                    # This helps name empty cells like "Associated EEA Measure(s):" rows
                    row_label = None
                    if not is_first_column:  # Only look for row label if NOT in first column
                        # Find cells in the same row (similar y0)
                        same_row_cells = [c for c in cells if abs(c[1] - y0) < 3]
                        # Find the leftmost cell in this row
                        if same_row_cells:
                            leftmost_cell = min(same_row_cells, key=lambda c: c[0])
                            # Check if it's truly the first column
                            if abs(leftmost_cell[0] - min_cell_x0) < 5:
                                try:
                                    left_crop = page.within_bbox(leftmost_cell)
                                    left_text = (left_crop.extract_text() or "").strip()
                                    # Use as row label if it ends with ":" and is descriptive
                                    if left_text and ':' in left_text:
                                        row_label = left_text
                                except:
                                    pass
                    
                    # v33.81: Skip cells that overlap with underscore-processed regions
                    # This prevents double-detection where strict_grid creates underscore fields
                    # and regular table loop creates overlapping table fields
                    if hasattr(self, '_underscore_processed_regions') and self._underscore_processed_regions:
                        skip_cell = False
                        for region in self._underscore_processed_regions:
                            if region['page'] != page_num:
                                continue
                            # Check if cell overlaps significantly with region
                            ov_x0 = max(x0, region['x0'])
                            ov_x1 = min(x1, region['x1'])
                            ov_y0 = max(y0, region['y0'])
                            ov_y1 = min(y1, region['y1'])
                            if ov_x1 > ov_x0 and ov_y1 > ov_y0:
                                # Overlap exists - cell is inside or touching region
                                overlap_area = (ov_x1 - ov_x0) * (ov_y1 - ov_y0)
                                cell_area = (x1 - x0) * (y1 - y0)
                                if cell_area > 0 and overlap_area / cell_area > 0.5:
                                    skip_cell = True
                                    break
                        if skip_cell:
                            continue
                    
                    field_info = self._analyze_cell_for_field(
                        page_num, x0, y0, x1, y1, cell_text, words, headers_map, prefer_below, row_label
                    )
                    

                    
                    if field_info:
                        self.text_fields.append(field_info)
                        page_fields += 1
            
            if page_fields > 0:
                print(f"   Page {page_num + 1}: {page_fields} table fields")
        


        print(f"   Total from tables: {len(self.text_fields)} fields")
        
        # v21: Check for colon-label cells that table detection missed
        self._detect_colon_label_cells()
    
    def _detect_colon_label_cells(self):
        """
        v21: Detect fields in table cells where the label ends with a colon
        and there's significant space (>50pt) remaining in the cell.
        """
        colon_fields = 0
        
        for page_num in range(self.page_count):
            page = self.pdf.pages[page_num]
            tables = self.page_tables.get(page_num, [])
            words = self.page_words.get(page_num, [])
            
            # v21.7: Use cached header bars
            if self.header_bars_cache is None:
                self.header_bars_cache = self._detect_header_bars()
            header_bars = self.header_bars_cache.get(page_num, [])

            for table in tables:
                # v22.31: SKIP invoice info tables (handled by strict underscore logic)
                if self._is_invoice_info_table(page, table):
                    continue

                for cell in table.cells:
                    cx0, cy0, cx1, cy1 = cell
                    cell_width = cx1 - cx0
                    cell_height = cy1 - cy0
                    
                    # Skip very small cells
                    if cell_width < 80 or cell_height < 15:
                        continue
                    
                    # v35.9: Skip tall cells (>60pt) that are label cells with adjacent empty data cells
                    # These are form layouts like "Weatherization Quality of Work:" | [empty data cell]
                    # The empty cell should get a full-height field, not a colon-based inline field
                    if cell_height > 60:
                        # Check if there's an adjacent cell on the same row (within 5pt Y)
                        has_adjacent_empty = False
                        for other_cell in table.cells:
                            if other_cell == cell:
                                continue
                            ox0, oy0, ox1, oy1 = other_cell
                            # Same row? (Y overlaps significantly)
                            if abs(oy0 - cy0) < 5 and abs(oy1 - cy1) < 5:
                                # Adjacent? (horizontally touching or close)
                                if abs(ox0 - cx1) < 5 or abs(ox1 - cx0) < 5:
                                    # Check if the other cell is empty
                                    try:
                                        other_text = (page.within_bbox((ox0+1, oy0+1, ox1-1, oy1-1)).extract_text() or "").strip()
                                        if not other_text:
                                            has_adjacent_empty = True
                                            break
                                    except:
                                        pass
                        if has_adjacent_empty:
                            print(f"   [ColonLabelSkip] P{page_num} tall label cell at y={cy0:.1f} has adjacent empty cell - skipping colon field")
                            continue
                    
                    # v21.48: Check if cell overlaps a header bar (section title detection)
                    is_in_header_bar = False
                    mid_x = (cx0 + cx1) / 2
                    mid_y = (cy0 + cy1) / 2
                    for bar in header_bars:
                        if bar['x0'] - 2 <= mid_x <= bar['x1'] + 2 and bar['y0'] - 2 <= mid_y <= bar['y1'] + 2:
                            is_in_header_bar = True
                            break
                    if is_in_header_bar:
                        continue

                    # v35.22: Skip if cell already has a field (check entire cell area)
                    # This prevents creating duplicate colon fields when embedded underscore fields exist
                    cell_has_field = False
                    for tf in self.text_fields:
                        if tf['page'] != page_num:
                            continue
                        # Check if there's any field overlap within this cell
                        if not (tf['x1'] < cx0 or tf['x0'] > cx1 or tf['y1'] < cy0 or tf['y0'] > cy1):
                            # Field overlaps with this cell
                            cell_has_field = True
                            break
                    if cell_has_field:
                        continue
                        
                    # Extract cell text for length check
                    try:
                        cell_crop = page.within_bbox((cx0-1, cy0-1, cx1+1, cy1+1))
                        cell_text = (cell_crop.extract_text() or "").strip()
                    except:
                        cell_text = ""

                    # v21.48: Skip text-heavy cells (Instructions, Lists)
                    if len(cell_text) > 80 or '■' in cell_text or cell_text.count('\n') > 4:
                         # EXCEPTION logic same as table_processing
                        is_large_text_field = (
                            '____' in cell_text or 
                            'describe:' in cell_text.lower() or 
                            'notes:' in cell_text.lower() or 
                            'comments:' in cell_text.lower()
                        )
                        if not is_large_text_field:
                            continue
                    
                    # Find words in this cell
                    cell_words = []
                    for w in words:
                        w_cx = (float(w['x0']) + float(w['x1'])) / 2
                        w_cy = (float(w['top']) + float(w['bottom'])) / 2
                        if cx0 < w_cx < cx1 and cy0 < w_cy < cy1:
                            cell_words.append(w)
                    
                    if not cell_words:
                        continue
                    
                    # Find colon-ending words
                    colon_words = [w for w in cell_words 
                                   if w['text'].strip().endswith(':') or 
                                      w['text'].strip().endswith('.:')]
                    
                    if not colon_words:
                        continue
                    
                    # v22.86: Handle multi-colon cells by creating MULTIPLE fields
                    # Sort colon words by X position (left to right)
                    colon_words_sorted = sorted(colon_words, key=lambda w: float(w['x0']))
                    
                    # v22.84: Convert pdfplumber coords (top-down) to PDF coords (bottom-up)
                    page_height = self.page_heights.get(page_num, 792)
                    
                    for colon_idx, colon_word in enumerate(colon_words_sorted):
                        colon_x1 = float(colon_word['x1'])
                        
                        # Determine the end X boundary for this field
                        # If there's a next colon word, end before it; otherwise use cell boundary
                        if colon_idx + 1 < len(colon_words_sorted):
                            # Find the label start of the next colon (word before the colon)
                            next_colon = colon_words_sorted[colon_idx + 1]
                            next_colon_x0 = float(next_colon['x0'])
                            # The field should end before the next label starts
                            # Find words that are part of the next label (between current colon end and next colon)
                            next_label_start = next_colon_x0
                            for w in cell_words:
                                w_x0 = float(w['x0'])
                                w_x1 = float(w['x1'])
                                # Find words that appear before the next colon word
                                if colon_x1 < w_x1 < next_colon_x0 and abs(float(w['top']) - float(colon_word['top'])) < 5:
                                    # This word is part of the next label
                                    if w_x0 < next_label_start:
                                        next_label_start = w_x0
                            field_x1 = next_label_start - 3
                        else:
                            field_x1 = cx1 - 3
                        
                        # Calculate space after this colon
                        space_after = field_x1 - colon_x1
                        
                        # Need at least 30pt of space for a useful field
                        if space_after < 30:
                            continue
                        
                        # Build label from words before/including this colon
                        label_parts = []
                        prev_colon_x1 = colon_words_sorted[colon_idx - 1]['x1'] if colon_idx > 0 else cx0
                        for w in sorted(cell_words, key=lambda w: float(w['x0'])):
                            w_x0 = float(w['x0'])
                            # Include words that are after previous colon and up to/including current colon
                            if w_x0 >= prev_colon_x1 - 5:
                                label_parts.append(w['text'].strip())
                                if w == colon_word:
                                    break
                        label = ' '.join(label_parts).rstrip(':').strip()
                        
                        # Skip empty or too-short labels
                        if len(label) < 3:
                            continue
                        
                        # v36.1 P35: Only skip when label IS exactly a section header (not contains)
                        SKIP_LABELS_EXACT = ['combustion efficiency', 'maintenance tasks', 'equipment information']
                        if label.strip().lower() in SKIP_LABELS_EXACT:
                            continue
                        
                        # v22.88: Use pdfplumber coords directly (top-down)
                        # _transform_coords_for_rotation() will convert to pikepdf (bottom-up)
                        field_y0 = float(colon_word['top']) - 2
                        field_y1 = float(colon_word['bottom']) + 2
                        field_x0 = colon_x1 + 3

                        base_name = self._clean_field_name(label)
                        field_name = self._generate_unique_name(base_name, page_num)
                        field_format, format_opts = self._detect_field_format(None, label)
                        
                        self.text_fields.append({
                            'page': page_num,
                            'x0': field_x0,
                            'y0': field_y0,
                            'x1': field_x1,
                            'y1': field_y1,
                            'name': field_name,
                            'label': label,
                            'format': field_format,
                            'format_opts': format_opts,
                            'source': 'colon_label_cell'
                        })
                        colon_fields += 1
        
        if colon_fields > 0:
            print(f"   Found {colon_fields} colon-label cell fields")
    
    def _detect_horizontal_line_tables(self):
        """
        NEW PASS (v36.7): Detect and create fields in tables with only horizontal divider lines.
        
        This handles PDFs like NYSEG/RG&E forms where:
        - Tables have horizontal lines separating rows
        - NO vertical lines defining columns  
        - Labels are inline with colons (e.g., "Account Name:")
        - Fields should span from label end to right edge
        
        Strategy:
        1. Find horizontal lines spanning most of page width using pdfplumber
        2. Group lines by Y position to find row boundaries
        3. Between each pair of lines, find text with colons
        4. Create text fields to the right of the label
        """
        created_count = 0
        
        for page_num in range(self.page_count):
            # Skip text-only pages
            if page_num in self.text_only_pages:
                continue
                
            page = self.pdf.pages[page_num]
            page_width = page.width
            page_height = page.height
            
            # === STEP 1: Find all horizontal lines using pdfplumber ===
            all_lines = page.lines
            
            # Filter for horizontal lines (top and bottom Y are very close)
            h_lines_raw = [l for l in all_lines if abs(l['top'] - l['bottom']) < 2]
            
            if len(h_lines_raw) < 2:
                continue
            
            # Convert to simpler format and merge segments at same Y position
            # Group by Y position (within 3 points tolerance)
            y_groups = {}
            for line in h_lines_raw:
                y = line['top']
                x0 = line['x0']
                x1 = line['x1']
                
                # Find existing group
                found_group = None
                for y_key in y_groups.keys():
                    if abs(y - y_key) < 3:
                        found_group = y_key
                        break
                
                if found_group is not None:
                    # Add to existing group
                    y_groups[found_group].append({'x0': x0, 'x1': x1})
                else:
                    # Create new group
                    y_groups[y] = [{'x0': x0, 'x1': x1}]
            
            # For each Y position, calculate total line coverage
            line_groups = []
            for y, segments in y_groups.items():
                # Calculate min and max X across all segments
                min_x = min(seg['x0'] for seg in segments)
                max_x = max(seg['x1'] for seg in segments)
                total_width = sum(seg['x1'] - seg['x0'] for seg in segments)
                
                # Only keep lines that span most of page width (>60%)
                if total_width > page_width * 0.6:
                    line_groups.append({
                        'y': y,
                        'x1': min_x,
                        'x2': max_x,
                        'width': total_width
                    })
            
            if len(line_groups) < 2:
                continue
            
            # Sort by Y position
            line_groups.sort(key=lambda l: l['y'])
            
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
                    field_name = self._clean_field_name(label_text)
                    field_name = self._generate_unique_name(field_name, page_num)
                    field_format, format_opts = self._detect_field_format(None, label_text)
                    
                    self.text_fields.append({
                        'page': page_num,
                        'x0': field_x0,
                        'y0': field_y0,
                        'x1': field_x1,
                        'y1': field_y1,
                        'name': field_name,
                        'label': label_text,
                        'format': field_format,
                        'format_opts': format_opts,
                        'field_type': 'text',
                        'source': 'horizontal_line_table',
                        'row_index': i
                    })
                    
                    created_count += 1
        
        if created_count > 0:
            print(f"   ✅ Created {created_count} fields in horizontal-line tables")
        else:
            print(f"   No horizontal-line table fields detected")
        
        return created_count
    
    def _detect_missing_table_left_columns(self):
        """
        Reconstruct full table grids using horizontal line endpoints.
        
        Strategy: When pdfplumber misses columns, we can infer the full grid by:
        1. Finding horizontal lines in table areas
        2. Using line endpoints (x0, x1) to determine ALL column boundaries
        3. Reconstructing the complete grid from these boundaries
        4. Creating fields for any missing cells
        """
        created_count = 0
        
        for page_num in range(self.page_count):
            page = self.pdf.pages[page_num]

            all_lines = page.lines
            h_lines = [l for l in all_lines if abs(l['top'] - l['bottom']) < 2]
            if len(h_lines) < 2:
                continue

            tables = page.find_tables()
            if not tables:
                continue

            for table in tables:
                table_x0, table_y0, table_x1, table_y1 = table.bbox
                table_width = table_x1 - table_x0
                table_height = table_y1 - table_y0

                # Skip very small or very tall tables
                if table_width < 120 or table_height < 20 or table_height > 250:
                    continue

                # Collect horizontal lines within this table's Y range
                table_h_lines = [l for l in h_lines if table_y0 - 2 <= l['top'] <= table_y1 + 2]

                # Build row boundaries from horizontal line Y positions (fallback to cell Y coords)
                y_groups = {}
                for line in table_h_lines:
                    y = round(line['top'])
                    found_group = None
                    for y_key in y_groups.keys():
                        if abs(y - y_key) < 3:
                            found_group = y_key
                            break
                    if found_group is not None:
                        y_groups[found_group].append(line)
                    else:
                        y_groups[y] = [line]

                row_y_boundaries = sorted(y_groups.keys())
                if len(row_y_boundaries) < 2:
                    # Fallback: use cell Y coords if horizontal lines are missing
                    cell_y_coords = set()
                    for cell in table.cells:
                        if cell:
                            cell_y_coords.add(cell[1])
                            cell_y_coords.add(cell[3])
                    row_y_boundaries = sorted(cell_y_coords)

                if len(row_y_boundaries) < 3:
                    continue

                # Build X boundaries from table cell edges
                x_boundaries = set()
                for cell in table.cells:
                    if cell:
                        x_boundaries.add(cell[0])
                        x_boundaries.add(cell[2])

                if not x_boundaries:
                    continue

                x_boundaries = sorted(x_boundaries)

                # Extend boundaries using horizontal line spans to capture missing left/right columns
                if table_h_lines:
                    leftmost_x = min(l['x0'] for l in table_h_lines)
                    rightmost_x = max(l['x1'] for l in table_h_lines)
                else:
                    leftmost_x = None
                    rightmost_x = None

                min_x = min(x_boundaries)
                max_x = max(x_boundaries)

                if leftmost_x is not None and leftmost_x < min_x - 10:
                    x_boundaries.insert(0, leftmost_x)
                if rightmost_x is not None and rightmost_x > max_x + 10:
                    x_boundaries.append(rightmost_x)

                # Ensure table bbox edges exist
                if all(abs(table_x0 - xb) > 6 for xb in x_boundaries):
                    x_boundaries.append(table_x0)
                if all(abs(table_x1 - xb) > 6 for xb in x_boundaries):
                    x_boundaries.append(table_x1)

                x_boundaries = sorted(x_boundaries)
                filtered_x = []
                for x in x_boundaries:
                    if not filtered_x or x - filtered_x[-1] > 8:
                        filtered_x.append(x)
                x_boundaries = filtered_x

                if len(x_boundaries) < 3:
                    continue

                # Create grid cells
                for row_idx in range(len(row_y_boundaries) - 1):
                    row_y0 = row_y_boundaries[row_idx]
                    row_y1 = row_y_boundaries[row_idx + 1]
                    row_height = row_y1 - row_y0

                    if row_height < 12 or row_height > 90:
                        continue

                    for col_idx in range(len(x_boundaries) - 1):
                        col_x0 = x_boundaries[col_idx]
                        col_x1 = x_boundaries[col_idx + 1]
                        col_width = col_x1 - col_x0

                        if col_width < 25 or col_width > 500:
                            continue

                        cell_x0 = col_x0 + 2
                        cell_x1 = col_x1 - 2
                        cell_y0 = row_y0 + 2
                        cell_y1 = row_y1 - 2

                        # Skip if cell contains text
                        try:
                            cell_text = page.within_bbox((cell_x0, cell_y0, cell_x1, cell_y1)).extract_text() or ""
                            cell_text = cell_text.replace('\n', ' ').strip()
                            if any(ch.isalpha() for ch in cell_text) and len(cell_text) > 2:
                                continue
                        except Exception:
                            pass

                        # Check overlap with existing fields
                        has_field = False
                        for existing in self.text_fields:
                            if existing['page'] != page_num:
                                continue

                            ex0, ey0, ex1, ey1 = existing['x0'], existing['y0'], existing['x1'], existing['y1']
                            overlap_x0 = max(cell_x0, ex0)
                            overlap_x1 = min(cell_x1, ex1)
                            overlap_y0 = max(cell_y0, ey0)
                            overlap_y1 = min(cell_y1, ey1)

                            if overlap_x1 > overlap_x0 and overlap_y1 > overlap_y0:
                                overlap_area = (overlap_x1 - overlap_x0) * (overlap_y1 - overlap_y0)
                                cell_area = (cell_x1 - cell_x0) * (cell_y1 - cell_y0)
                                if overlap_area > cell_area * 0.3:
                                    has_field = True
                                    break

                        if has_field:
                            continue

                        field_name = f"Grid_R{row_idx}C{col_idx}"
                        field_name = self._generate_unique_name(field_name, page_num)

                        self.text_fields.append({
                            'page': page_num,
                            'x0': cell_x0,
                            'y0': cell_y0,
                            'x1': cell_x1,
                            'y1': cell_y1,
                            'name': field_name,
                            'label': '',
                            'format': 'text',
                            'format_opts': {},
                            'field_type': 'text',
                            'source': 'grid_reconstruction'
                        })

                        created_count += 1
        
        if created_count > 0:
            print(f"   ✅ Added {created_count} fields via grid reconstruction")
        
        return created_count
    
    def _analyze_header_row(self, page, header_cells, all_cells=None, all_y_values=None) -> Tuple[bool, Dict, set]:
        """
        Analyze header row to determine:
        1. Is the first row a true header? (vs data row with labels)
        2. Map of column positions to header names
        3. Set of columns to skip (Pass/Fail)
        
        LEARNED: True headers have cells WITHOUT colons containing column titles
        
        IMPROVED: If multiple rows have cells without colons, this is a FORM layout
        where each cell is a label (not a traditional header+data table).
        """
        first_row_is_header = False
        headers_map = {}
        skip_columns = set()
        
        # Count cells with/without colons to determine if this is a header row
        cells_without_colon = 0
        cells_with_colon = 0
        
        for hcell in header_cells:
            hx0, hy0, hx1, hy1 = hcell
            try:
                htext = page.within_bbox((hcell[0]-1, hcell[1]-1, hcell[2]+1, hcell[3]+1)).extract_text() or ""
                htext = htext.replace('\n', ' ').strip()
                
                if htext:
                    # Clean the header name using consistent logic
                    header_name = self._clean_field_name(htext)
                    
                    # Count cells with/without colons
                    if ':' in htext:
                        cells_with_colon += 1
                    else:
                        cells_without_colon += 1
                    
                    headers_map[(hx0, hx1)] = header_name
                    
                    # Mark Pass/Fail columns to skip
                    hl = header_name.lower()
                    if hl in ['pass', 'fail', 'yes', 'no']:
                        skip_columns.add((hx0, hx1))
            except:
                pass
        
        # v16.3: Fallback - if we found very few headers (<3) for a wide row (>300pt),
        # OR if the rightmost header is far from the right edge (missing headers on right),
        # try to detect headers from words instead (handles merged cells)
        min_x = min(c[0] for c in header_cells) if header_cells else 0
        max_x = max(c[2] for c in header_cells) if header_cells else 0
        row_width = max_x - min_x
        
        # Calculate full table width from all cells if available
        table_max_x = max_x
        if all_cells:
            table_max_x = max(c[2] for c in all_cells)
        
        # Check max header x found so far
        max_header_x = 0
        if headers_map:
            max_header_x = max(k if isinstance(k, (int, float)) else k[1] for k in headers_map.keys())
        
        # Use table width instead of just header row width to detect missing headers
        missing_right_headers = (table_max_x - max_header_x > 50)  # Significant gap on right
        
        if (len(headers_map) < 3 or missing_right_headers) and table_max_x - min_x > 300:
            try:
                # Get all words in this row's bounding box
                min_y = min(c[1] for c in header_cells)
                max_y = max(c[3] for c in header_cells)
                # Extend bbox to full table width to capture missing right-side headers
                row_bbox = (min_x, min_y, table_max_x, max_y)
                # v22.31: If extract_words() fails, try raw character extraction (handles white-on-dark better)
                words = page.within_bbox(row_bbox).extract_words()
                if not words:
                    chars = page.within_bbox(row_bbox).chars
                    if chars:
                        # Convert chars to pseudo-words for column detection
                        # Group by proximity
                        chars.sort(key=lambda c: float(c['x0']))
                        current_word = []
                        last_x1 = -100
                        for c in chars:
                            if float(c['x0']) > last_x1 + 3: # New word
                                if current_word:
                                    w_text = "".join([x['text'] for x in current_word])
                                    w_x0 = current_word[0]['x0']
                                    w_x1 = current_word[-1]['x1']
                                    words.append({'text': w_text, 'x0': w_x0, 'x1': w_x1})
                                current_word = []
                            current_word.append(c)
                            last_x1 = float(c['x1'])
                        if current_word:
                             w_text = "".join([x['text'] for x in current_word])
                             w_x0 = current_word[0]['x0']
                             w_x1 = current_word[-1]['x1']
                             words.append({'text': w_text, 'x0': w_x0, 'x1': w_x1})

                # Group words into columns (gap > 10pt = new column)
                if words:
                    words.sort(key=lambda w: float(w['x0']))
                    current_col_x0 = float(words[0]['x0'])
                    current_col_text = []
                    last_x1 = float(words[0]['x1'])
                    
                    headers_map = {} # Reset to use word-based headers
                    cells_without_colon = 0 # Reset counters
                    cells_with_colon = 0
                    
                    for w in words:
                        wx0 = float(w['x0'])
                        if wx0 > last_x1 + 15:  # New column
                            # Save previous column
                            htext = " ".join(current_col_text)
                            if htext:
                                hname = self._clean_field_name(htext)
                                headers_map[(current_col_x0, last_x1)] = hname
                                if ':' in htext: cells_with_colon += 1
                                else: cells_without_colon += 1
                            
                            # Start new column
                            current_col_x0 = wx0
                            current_col_text = []
                        
                        current_col_text.append(w['text'])
                        last_x1 = float(w['x1'])
                    
                    # Add last column
                    if current_col_text:
                        htext = " ".join(current_col_text)
                        if htext:
                            hname = self._clean_field_name(htext)
                            headers_map[(current_col_x0, last_x1)] = hname
                            if ':' in htext: cells_with_colon += 1
                            else: cells_without_colon += 1
            except Exception as e:
                print(f"Error in word-based header detection: {e}")
        # - If multiple cells: require majority without colons
        # - If single cell: treat as header if it has no colon (e.g., "Total Rebate" standalone table)
        total_header_cells = cells_with_colon + cells_without_colon
        if total_header_cells == 1 and cells_without_colon == 1:
            # Single-cell header row without colon = true header
            first_row_is_header = True
        if cells_without_colon >= 2 and cells_without_colon > cells_with_colon:
            first_row_is_header = True
        
        
        # IMPROVED: Check if this is a FORM LAYOUT (not a traditional header+data table)
        # In form layouts, multiple consecutive rows have cells without colons because
        # each cell is a label for a form field.
        # In traditional tables, only the header row lacks colons; data rows have actual data.
        # KEY DISTINCTION: Data tables have EMPTY cells (fillable areas), form layouts have labels in every cell.
        if first_row_is_header and all_cells and all_y_values:
            # Check the next 3 data rows - if they ALSO mostly lack colons, it's a form layout
            header_row_y = header_cells[0][1]  # Y of header row
            data_row_y_values = [y for y in all_y_values if y > header_row_y + 5][:3]
            
            rows_without_colons = 0
            total_empty_cells = 0  # Count empty cells across all rows
            total_cells_checked = 0
            
            for row_y in data_row_y_values:
                row_cells = [c for c in all_cells if abs(c[1] - row_y) < 2]
                if len(row_cells) > 1:  # Multi-cell row
                    row_colon_count = 0
                    row_text_count = 0
                    row_empty_count = 0
                    for cell in row_cells:
                        try:
                            cell_text = page.within_bbox((cell[0]-1, cell[1]-1, cell[2]+1, cell[3]+1)).extract_text() or ""
                            cell_text = cell_text.strip()
                            total_cells_checked += 1
                            if not cell_text:  # Empty cell
                                row_empty_count += 1
                                total_empty_cells += 1
                            elif len(cell_text) < 50:  # Short label-like text
                                # IMPROVED: Don't count checkbox patterns or numeric values as form labels
                                # These are DATA, not labels
                                cell_lower = cell_text.lower()
                                is_checkbox_pattern = any(p in cell_lower for p in ['yes', 'no', 'n/a', 'pass', 'fail'])
                                is_numeric = cell_text.replace('.', '').replace(',', '').replace('-', '').isdigit()
                                
                                if is_checkbox_pattern or is_numeric:
                                    # This is data, not a label - count as empty for form detection
                                    row_empty_count += 1
                                    total_empty_cells += 1
                                else:
                                    row_text_count += 1
                                    if ':' not in cell_text:
                                        row_colon_count += 1
                        except:
                            pass
                    # If most cells in this row lack colons AND are not empty, it's a form row
                    if row_text_count > 0 and row_colon_count >= row_text_count * 0.7:
                        rows_without_colons += 1
            
            # If 2+ of the next 3 rows also lack colons, it MIGHT be a form layout
            # BUT if there are significant empty cells, it's actually a DATA TABLE (not form layout)
            # Data tables have empty cells where values should be filled in; forms have labels everywhere
            has_significant_empty_cells = total_cells_checked > 0 and total_empty_cells >= total_cells_checked * 0.2
            
            if rows_without_colons >= 2 and not has_significant_empty_cells:
                first_row_is_header = False
        
        # Only use headers_map if the first row is a true header row
        # Otherwise cells should use their own labels
        if not first_row_is_header:
            headers_map = {}
        
        return first_row_is_header, headers_map, skip_columns
    
    def _is_invoice_info_table(self, page, table) -> bool:
        """
        Check if this is a 'Required Information on Invoice' style table.
        
        These tables have columns like:
        - Air Sealing, Rebate Amount, Customer Type, Required Conditions,
        - Quality Standards, Required Information on Invoice, Supporting Documentation
        
        For these tables, we ONLY want fields in the "Required Information" column
        where there are underscore fill-in patterns.
        """
        try:
            data = table.extract()
            if not data:
                return False
            
            # Check first 5 rows (increased from 1 to catch deeply nested headers)
            for row in data[:5]:
                if not row: continue
                for h in row:
                    if not h: continue
                    h_lower = h.lower()
                    # Flexible matching for "Required Information on Invoice"
                    if 'required information' in h_lower and ('invoice' in h_lower or 'documentation' in h_lower):
                        return True
                    if 'information on invoice' in h_lower:
                        return True
        except:
            pass
        return False
    
    def _is_inside_invoice_table(self, page_num, x0, y0, x1, y1) -> bool:
        """v22.5: Check if a bounding box overlaps with any known invoice table on the page."""
        for bbox in self.invoice_table_bboxes:
            if bbox['page'] == page_num:
                # Check for significant overlap
                # Intersection area / Candidate area > some threshold?
                # Or just any overlap?
                # Usually table cells overlap precisely.
                ov_x0 = max(x0, bbox['x0'])
                ov_y0 = max(y0, bbox['y0'])
                ov_x1 = min(x1, bbox['x1'])
                ov_y1 = min(y1, bbox['y1'])
                
                if ov_x1 > ov_x0 + 5 and ov_y1 > ov_y0 + 5:
                    return True
        return False
    
    def _detect_underscore_fields(self, page_num: int, page, table) -> List[Dict]:
        """
        Detect fill-in fields from underscore text patterns in "Required Information" column.
        
        These are TEXT underscores like "Pre-Test (CFM50): ____________________________"
        not vector lines.
        """
        fields = []
        
        try:
            # 1. Find the "Required Information" column boundaries
            data = table.extract()
            if not data: return fields
            
            headers = None
            req_info_col_idx = None
            
            # Scan first 5 rows for header
            for row in data[:5]:
                if not row: continue
                # print(f"      DEBUG: Checking row for headers: {[str(x)[:20] for x in row if x]}")
                for idx, h in enumerate(row):
                    if h and ('required information' in h.lower() or 'information on invoice' in h.lower()):
                        headers = row
                        req_info_col_idx = idx
                if req_info_col_idx is not None: break
            
            if req_info_col_idx is None: 
                return fields
            
            # Get column X boundaries from cells
            # We need to find the X range that corresponds to this column index
            # This is tricky because rows merge/split. Use approximate x positions.
            cells = table.cells
            col_x_starts = sorted(list(set(round(c[0], 0) for c in cells)))
            
            if req_info_col_idx >= len(col_x_starts): return fields
            
            col_x0 = col_x_starts[req_info_col_idx]
            # Find next column start or use table edge
            col_x1 = table.bbox[2]
            if req_info_col_idx + 1 < len(col_x_starts):
                col_x1 = col_x_starts[req_info_col_idx + 1]

            # 2. Get all words and merge adjacent underscores
            words = page.extract_words()
            merged_underscores = []
            
            # Step A: Find all underscore-containing words
            und_words = [w for w in words if '_' in w['text']]
            if not und_words: return fields
            
            # Step B: Group by y-coordinate and merge horizontal neighbors
            # Sort by Y then X
            und_words.sort(key=lambda w: (float(w['top']), float(w['x0'])))
            
            current_cluster = []
            for w in und_words:
                if not current_cluster:
                    current_cluster.append(w)
                    continue
                
                last = current_cluster[-1]
                # Check if on same line (within 2pt Y) and close horizontally (within 5pt)
                if abs(float(w['top']) - float(last['top'])) < 2 and (float(w['x0']) - float(last['x1'])) < 5:
                    current_cluster.append(w)
                else:
                    # Process current cluster
                    bx0 = float(current_cluster[0]['x0'])
                    bx1 = float(current_cluster[-1]['x1'])
                    by0 = float(current_cluster[0]['top'])
                    by1 = float(current_cluster[0]['bottom'])
                    text = "".join(x['text'] for x in current_cluster)
                    
                    if text.count('_') >= 3:
                        merged_underscores.append({'x0': bx0, 'top': by0, 'x1': bx1, 'bottom': by1, 'text': text})
                    
                    current_cluster = [w]
            
            # Process final cluster
            if current_cluster:
                bx0 = float(current_cluster[0]['x0'])
                bx1 = float(current_cluster[-1]['x1'])
                by0 = float(current_cluster[0]['top'])
                by1 = float(current_cluster[0]['bottom'])
                text = "".join(x['text'] for x in current_cluster)
                if text.count('_') >= 3:
                    merged_underscores.append({'x0': bx0, 'top': by0, 'x1': bx1, 'bottom': by1, 'text': text})
            
            # 3. Create fields for underscores in the target column
            for und in merged_underscores:
                # STRICT FILTER: Must be strictly within the target column X range
                if not (col_x0 - 5 <= und['x0'] <= col_x1 + 5):
                    continue
                
                # Find label (look left on broader scope)
                wx0, wy = und['x0'], und['top']
                label = None
                
                # Look for words to the left on the same line
                # v22.5: Restrict to current column to prevent sucking in text from adjacent columns
                line_words = [w for w in words if abs(float(w['top']) - wy) < 5 and float(w['x1']) < wx0 and float(w['x0']) > col_x0 - 5]
                line_words.sort(key=lambda w: float(w['x0']))
                
                if line_words:
                    # Use all text on the line to the left as the label
                    label_text = " ".join(w['text'] for w in line_words).strip().rstrip(':')
                    if label_text:
                        label = label_text
                
                # If no label on same line, look at line above (within 15pt) if it's close to the left edge of column
                if not label:
                     # v22.5: Ensure above_words also stay within the current column boundaries
                     above_words = [w for w in words if 5 < (wy - float(w['bottom'])) < 20 and col_x0 - 5 < float(w['x0']) < col_x1]
                     if above_words:
                         label = " ".join(w['text'] for w in above_words).strip()

                if not label:
                    base_name = "Invoice_Field"
                else:
                    # v22.5: Garbage Label Filter
                    # Legitimate form labels are rarely >60 chars or >10 words.
                    # This prevents descriptive paragraphs with underscores from becoming massive field names.
                    if len(label) > 100 or len(label.split()) > 15:
                        # print(f"   🗑️  Skipping garbage underscore label: {label[:30]}...")
                        continue
                        
                    base_name = self._clean_field_name(label)
                
                field_name = self._generate_unique_name(base_name, page_num)
                field_format, format_opts = self._detect_field_format(None, label)
                
                fields.append({
                    'page': page_num,
                    'x0': und['x0'],
                    'y0': und['top'] - 2,
                    'x1': und['x1'],
                    'y1': und['bottom'] + 2,
                    'name': field_name,
                    'label': label,
                    'format': field_format,
                    'format_opts': format_opts,
                    'source': 'underscore'
                })
        
        except Exception as e:
            print(f"   ⚠️  Error detecting underscore fields: {e}")
        
        return fields

    def _detect_embedded_underscore_fields(self, page_num: int, page, 
                                           cell_x0: float, cell_y0: float, 
                                           cell_x1: float, cell_y1: float) -> List[Dict]:
        """
        Detect fill-in fields from underscore TEXT patterns WITHIN a table cell.
        
        Used for cells like:
        - "Blower Motor Volts __________ x Amps __________ = __________ Watts"
        - "Coil Entering WB __________ = __________ Btu/Lb Enthalpy a"
        
        Returns a list of field definitions for each underscore pattern found.
        """
        fields = []
        
        try:
            # Get words within this cell
            words = page.within_bbox((cell_x0, cell_y0, cell_x1, cell_y1)).extract_words()
            
            for i, word in enumerate(words):
                text = word['text']
                
                # Check if this is an underscore pattern
                # v33.73: Relaxed to 2 underscores to catch shorter lines (e.g. "Age: __")
                if '__' in text:
                    wx0 = float(word['x0'])
                    wy = float(word['top'])
                    wx1 = float(word['x1'])
                    
                    # v21.50: Extract prefix as label if word starts with text (e.g. "Volts____")
                    prefix_label = None
                    first_underscore_idx = text.find('_')
                    if first_underscore_idx > 0:
                        prefix_label = text[:first_underscore_idx].rstrip(':').strip()
                    
                    # SURGICAL FIX: Find character-level start of underscores to skip labels
                    found_underscore_x = wx0
                    if hasattr(page, 'chars') and page.chars:
                        # Extract all characters for this word using index mapping if possible, 
                        # or more lenient coordinate search
                        word_chars = [c for c in page.chars if abs(float(c['top']) - wy) < 5 and wx0 - 1 <= float(c['x0']) <= wx1 + 1]
                        # Sort by x0
                        word_chars.sort(key=lambda c: float(c['x0']))
                        
                        for char in word_chars:
                            if char['text'] == '_':
                                found_underscore_x = float(char['x0'])
                                break
                    elif first_underscore_idx > 0:
                        # Fallback: estimate based on character count ratio if chars not available
                        ratio = first_underscore_idx / len(text)
                        found_underscore_x = wx0 + (wx1 - wx0) * ratio
                    
                    # Find label - look to the left on the same line, but STOP if we hit another underscore
                    # v21.49: Better label extraction for phrases like "Coil Entering WB"
                    label_parts = []
                    label_max_x1 = 0
                    
                    # Look back from current word index i
                    for j in range(i - 1, -1, -1):
                        other_word = words[j]
                        oy = float(other_word['top'])
                        ox1 = float(other_word['x1'])
                        other_text = other_word['text']
                        
                        # Stop if we hit another underscore word
                        if '____' in other_text:
                            break
                            
                        # Same line (within 5pt) and to the left
                        if abs(oy - wy) < 5 and ox1 < wx0 + 5:
                            clean_text = other_text.rstrip(':').strip()
                            if clean_text:
                                if len(clean_text) > 1 or clean_text in ['=', 'x', '+', '-', '÷', '/', '#']:
                                    label_parts.insert(0, clean_text)
                                    label_max_x1 = max(label_max_x1, ox1)
                            # If it's a very large gap, stop
                            if (wx0 - ox1) > 50:
                                break
                    
                    if prefix_label:
                        label_parts.append(prefix_label)
                    
                    label = ' '.join(label_parts).strip()
                    
                    # Fix common OCR errors in labels (generic technical terms)
                    if label:
                        if label.upper() == 'VOLTS': label = 'Volts'
                        if label.upper() == 'AMPS': label = 'Amps'
                        if label.upper() == 'CFM': label = 'CFM'
                        if 'ENTHALPY' in label.upper(): label = 'Enthalpy'
                    
                    # Generate field name
                    if label:
                        base_name = self._clean_field_name(label)
                    else:
                        base_name = "Fill_Field"
                    
                    field_name = self._generate_unique_name(base_name, page_num)
                    
                    # Check for format based on label
                    field_format, format_opts = self._detect_field_format(None, label)
                    
                    # SURGICAL: Ensure field starts AFTER label even if coordinates overlap
                    final_x0 = max(found_underscore_x, label_max_x1 + 3.5)
                    
                    # v35.22: Use normal field naming without _EMB suffix
                    # field_name = f"{field_name}_EMB"  # REMOVED
                    
                    # Align bottom of field (height 14) with text bottom
                    wb = float(word['bottom'])
                    field_y0 = wb - 13
                    field_y1 = wb + 1
                    
                    fields.append({
                        'page': page_num,
                        'x0': final_x0,
                        'y0': field_y0,
                        'x1': wx1,
                        'y1': field_y1,
                        'name': field_name,
                        'label': label,
                        'format': field_format,
                        'format_opts': format_opts,
                        'source': 'embedded_underscore'
                    })
                    
            
        except Exception as e:
            pass  # Silently skip problematic cells
        
        return fields

    def _detect_at_symbol_fields(self, page_num: int, x0: float, y0: float,
                                  x1: float, y1: float, words: List,
                                  headers_map: Dict) -> List[Dict]:
        """
        Detect fields on BOTH SIDES of @ symbol in a cell.
        
        Used for cells like "@ CFM50" in Before/After columns (page 49).
        Creates two fields: one before @, one after @.
        """
        fields = []
        
        # Find the @ symbol position
        at_word = None
        for word in words:
            if word['text'].strip() == '@':
                at_word = word
                break
        
        if not at_word:
            return fields
        
        at_x0 = float(at_word['x0'])
        at_x1 = float(at_word['x1'])
        at_y = float(at_word['top'])
        at_y_bottom = float(at_word['bottom'])
        
        # Get column header for naming
        header_name = self._get_column_header(x0, x1, headers_map) or "Value"
        
        # Create field BEFORE the @ symbol (left side)
        if at_x0 - x0 > 20:  # Enough space before @
            field_name = self._generate_unique_name(f"{header_name}_Before", page_num)
            fields.append({
                'page': page_num,
                'x0': x0 + 2,
                'y0': at_y - 1,
                'x1': at_x0 - 3,  # Stop before @
                'y1': at_y_bottom + 1,
                'name': field_name,
                'label': f"{header_name} (before @)",
                'format': None,
                'format_opts': {},
                'source': 'at_symbol'
            })
        
        # Create field AFTER the @ symbol (right side)
        if x1 - at_x1 > 20:  # Enough space after @
            field_name = self._generate_unique_name(f"{header_name}_After", page_num)
            fields.append({
                'page': page_num,
                'x0': at_x1 + 3,  # Start after @
                'y0': at_y - 1,
                'x1': x1 - 2,
                'y1': at_y_bottom + 1,
                'name': field_name,
                'label': f"{header_name} (after @)",
                'format': None,
                'format_opts': {},
                'source': 'at_symbol'
            })
        
        return fields

    def _is_colored_header_cell(self, page_num, x0, y0, x1, y1):
        """
        v35.29 / v35.41: Universal helper to check if a cell has colored header background.
        Returns True if cell overlaps any DARK colored rectangle (dark blue, dark teal, dark gray).
        v35.41: ONLY detects DARK headers, ignores light alternating row colors for readability.
        Use this EVERYWHERE before creating fields to prevent fields in header bars.
        """
        # Normalize coords in case they're inverted (some fields have y0 > y1)
        if y0 > y1:
            y0, y1 = y1, y0
        if x0 > x1:
            x0, x1 = x1, x0
        
        page = self.pdf.pages[page_num]
        
        # Check ALL page rectangles for overlap with this cell
        for r in page.rects:
            # Check if rectangle overlaps this cell
            if not (r['x1'] < x0 or r['x0'] > x1 or r['y1'] < y0 or r['y0'] > y1):
                # v22.96: Only count as header if the rectangle is WIDE (> 250pt)
                # This distinguishes header bars from alternating colored cell backgrounds.
                rw = r['x1'] - r['x0']
                if rw < 250: 
                    continue
                color = r.get('non_stroking_color', [])
                if isinstance(color, (int, float)): color = [color]
                
                # Skip white fills and very light colors
                if color == [1, 1, 1] or color == 1 or not color:
                    continue
                
                # v35.41/v35.42: Detect teal/cyan headers but allow very light alternating rows
                # Dark blue headers (RGB): Blue >0.7, Red <0.2
                if len(color) == 3 and color[2] > 0.7 and color[0] < 0.2:
                    # Skip if it's very bright (light blue alternating rows > 0.85 average)
                    brightness = (color[0] + color[1] + color[2]) / 3
                    if brightness < 0.85:  # Catch darker blues, skip very light
                        return True
                
                # Dark gray (grayscale <0.5)
                if len(color) == 1 and color[0] < 0.5:
                    return True
                
                # Teal/cyan headers (RGB): Green/Blue high, Red low
                # Teal headers ~(0.0, 0.6, 0.6) brightness=0.4, Light alternating ~(0.8, 0.9, 0.95) brightness=0.88
                # v22.92: Broadened brightness to 0.82 to catch lighter teal bars
                if len(color) == 3 and color[1] > 0.35 and color[2] > 0.35 and color[0] < 0.45:
                    brightness = (color[0] + color[1] + color[2]) / 3
                    if brightness < 0.82:
                        return True
                        
                # Green/Teal headers (Page 10, etc.): Green dominant
                # v22.93: Added detection for Green headers (G > 0.4, G > R+0.1, G > B)
                if len(color) == 3 and color[1] > 0.4 and color[1] > color[0] + 0.1 and color[1] > color[2]:
                    # Ensure it's not too light (brightness < 0.85)
                    brightness = (color[0] + color[1] + color[2]) / 3
                    if brightness < 0.85:
                        return True
                
                # CMYK cyan/teal headers: C high, with some K component
                # v22.92: Lowered K threshold to 0.04 to catch lighter teal
                if len(color) == 4 and color[0] > 0.6 and color[1] < 0.4:
                    if color[3] > 0.04:
                        return True
                
                # CMYK dark gray headers (K > 0.4)
                if len(color) == 4 and color[3] > 0.4:
                    return True
        
        # Also check paths (some PDFs use paths for headers)
        # v35.41/v35.42: Detect teal headers but allow very light alternating rows
        cell_objs = page.within_bbox((x0, y0, x1, y1)).objects
        for p in cell_objs.get('path', []):
            color = p.get('non_stroking_color', [])
            if isinstance(color, (int, float)): color = [color]
            
            if color and color != [1, 1, 1] and color != 1:
                # Dark blue
                if len(color) == 3 and color[2] > 0.7 and color[0] < 0.2:
                    brightness = (color[0] + color[1] + color[2]) / 3
                    if brightness < 0.85:
                        return True
                # Dark gray
                if len(color) == 1 and color[0] < 0.5:
                    return True
                # Teal/cyan with brightness check
                if len(color) == 3 and color[1] > 0.4 and color[2] > 0.4 and color[0] < 0.4:
                    brightness = (color[0] + color[1] + color[2]) / 3
                    if brightness < 0.75:
                        return True
                # CMYK cyan with K check
                if len(color) == 4 and color[0] > 0.7 and color[1] < 0.3 and color[3] > 0.08:
                    return True
                # CMYK dark gray
                if len(color) == 4 and color[3] > 0.4:
                    return True

        # v35.42 fallback: some PDFs draw header fills without rect/path color info.
        # In that case, sample the rendered pixels in the middle of the cell and check brightness/color.
        # v22.96: ONLY sample wide cells (>100pt). Sampling tiny 10x10 checkboxes with black borders 
        # results in low average brightness, causing them to be removed as headers.
        if (x1 - x0) < 100:
            return False

        try:
            import pdfplumber
            from PIL import Image
            import numpy as np
            with pdfplumber.open(str(self.input_pdf)) as plpdf:
                plpage = plpdf.pages[page_num]
                # Convert bottom-up (x0,y0,x1,y1) to pdfplumber crop box (x0, top, x1, bottom)
                top = plpage.height - y1
                bottom = plpage.height - y0
                # sample a small central strip (up to 18pt tall)
                sample_top = top
                sample_bottom = min(top + min(18, bottom - top), bottom)
                if sample_bottom > sample_top:
                    crop = (x0, sample_top, x1, sample_bottom)
                    im = plpage.crop(crop).to_image(resolution=50)
                    arr = np.array(im.original)
                    if arr.size:
                        avg = arr.mean(axis=(0, 1))[:3] / 255.0
                        brightness = float(avg.mean())
                        # teal/dark detection: green/blue > red and overall not too bright
                        if (avg[1] > avg[0] * 1.1 and avg[2] > avg[0] * 1.1 and brightness < 0.75) or (brightness < 0.55):
                            return True
        except Exception:
            pass

        return False

    def _analyze_cell_for_field(self, page_num, x0, y0, x1, y1, cell_text, words, headers_map, prefer_below=False, row_label=None, force_field_if_empty=False, source_type='table_data_row'):
        """Cleaned & Unified cell analyzer for Round 23."""
        
        # v35.20: Tally sheet pages DO get text fields now (removed skip)
        
        margin = 2
        cell_width = x1 - x0
        cell_height = y1 - y0
        clean_text = cell_text.strip().replace('  ', ' ')
        clean_lower = clean_text.lower()
        page = self.pdf.pages[page_num]
        
        # v35.27: UNIVERSAL IMAGE/LOGO EXCLUSION - Skip cells overlapping ANY image
        # Never create fields on top of logos, graphics, or placeholder images
        for img in page.images:
            # Check if cell overlaps this image (both use PDF bottom-up coordinates)
            if not (x1 < img['x0'] or x0 > img['x1'] or y1 < img['y0'] or y0 > img['y1']):
                # Cell overlaps this image - skip it
                print(f"   [LogoSkip] P{page_num} cell at ({x0:.1f},{y0:.1f}) overlaps image/logo")
                return None
        
        # v35.29 / v35.41: Use helper function for colored header detection
        # v35.41: Skip colored header check for tally sheets - they use alternating colored rows for readability
        # v22.96: CRITICAL BYPASS - If cell has a colon (label pointer), do NOT skip due to color.
        # This prevents skipping labeled fields on P13/P14 which have alternating row colors.
        is_tally = page_num in self.tally_sheet_pages
        if not is_tally and ':' not in clean_text:
            if self._is_colored_header_cell(page_num, x0, y0, x1, y1):
                print(f"   [ColorSkip] P{page_num} header cell '{clean_text[:20]}' at y={y0:.1f} skipped")
                return None

        # v35.10: LABEL CELL PROTECTION - Skip label cells that have adjacent empty data cells
        # When a tall cell (>60pt) contains colon-ending label text at the top, and there's an
        # adjacent empty cell for data entry, the label cell should NOT become a text field.
        # This prevents "Weatherization Quality of Work:" from getting a field when there's
        # an empty cell next to it for the actual data entry.
        if cell_height > 60 and clean_text.strip().endswith(':') and words and "weatherization quality of work" not in clean_lower:
            # Check if text is at the top of the cell (label-at-top pattern)
            text_top = min(float(w['top']) for w in words)
            text_bottom = max(float(w['bottom']) for w in words)
            room_below = y1 - text_bottom
            
            if (text_top - y0 < 10) and (room_below > 40):  # Label at top with lots of room
                # Check for adjacent empty cell in the same row
                for cell_data in self.table_cells:
                    if cell_data['page'] != page_num:
                        continue
                    cx0_other = cell_data['x0']
                    cy0_other = cell_data['y0']
                    cx1_other = cell_data['x1']
                    cy1_other = cell_data['y1']
                    
                    # Skip self
                    if abs(cx0_other - x0) < 2 and abs(cy0_other - y0) < 2:
                        continue
                    
                    # Same row? (Y overlaps within 5pt)
                    if abs(cy0_other - y0) < 5 and abs(cy1_other - y1) < 5:
                        # Adjacent? (horizontally touching)
                        if abs(cx0_other - x1) < 5 or abs(cx1_other - x0) < 5:
                            # Check if the other cell is empty
                            # v22.96: Use robust helper for adjacent cell check
                            other_text = self._get_text_in_bbox(page_num, (cx0_other, cy0_other, cx1_other, cy1_other), overlap_threshold=0.15)
                            if not other_text:
                                print(f"   [LabelCellSkip] P{page_num} label cell '{clean_text[:30]}...' at y={y0:.1f} skipped - adjacent empty data cell")
                                return None

        # 1. FILTERS & SUPPRESSION

        # v33.74: Row Gap Detection (Fix #6)
        # If a horizontal line runs through the MIDDLE of this cell, it's likely bridging rows
        # or capturing a gap/merge error.
        p_lines = self.page_lines.get(page_num, [])
        if cell_height > 15: # Only check significant cells
            inner_top = y0 + (cell_height * 0.25)
            inner_bottom = y1 - (cell_height * 0.25)
            for line in p_lines:
                # Horizontal check
                l_top = float(line.get('top', 0))
                l_bot = float(line.get('bottom', 0))
                if abs(l_top - l_bot) < 2:
                     l_y = (l_top + l_bot) / 2
                     if inner_top <= l_y <= inner_bottom:
                         # Check X overlap (>50%)
                         l_x0 = float(line.get('x0', 0))
                         l_x1 = float(line.get('x1', 0))
                         overlap_x0 = max(x0, l_x0)
                         overlap_x1 = min(x1, l_x1)
                         if overlap_x1 > overlap_x0 and (overlap_x1 - overlap_x0) > cell_width * 0.5:
                             print(f"   [GapSkip] P{page_num} structural box at y={y0:.1f}-{y1:.1f} crossed by line at y={l_y:.1f}")
                             return None
        
        # v35.20: Pre-filled Data Value Detection
        # Cells that already contain data values should NOT become fields
        # These include: prices ($XX.XX), units (Fixture, Lamp, Sensor, Ton), percentages, etc.
        if clean_text and cell_width < 120:  # Only check narrower cells (data columns)
            import re
            # Price patterns: $50.00, $1,000.00, etc.
            if re.match(r'^\$[\d,]+(\.\d{2})?$', clean_text):
                return None  # Pre-filled price - skip
            # Unit labels that are data values (not labels for user input)
            data_unit_values = ['fixture', 'lamp', 'ton', 'sensor', 'unit', 'each', 'sqft', 'sq ft', 
                               'heat pump', 'ac unit', 'thermostat', 'boiler', 'furnace',
                               'controlled', 'fixture controlled', 'per lamp', 'per fixture']
            if clean_lower in data_unit_values:
                return None  # Pre-filled unit value - skip
        
        # v33.03: Instruction Box Suppression
        # Large boxes with substantial text that contains warning/instruction keywords
        # These are NOT fillable fields - they're read-only instruction areas
        instruction_keywords = ['failure to', 'required documentation', 'delay or disqualify', 
                               'questions please call', 'submit required', 'not be processed',
                               'must be completed', 'must be submitted', 'will not be accepted']
        if cell_width > 400 and len(clean_text) > 50:
            if any(k in clean_lower for k in instruction_keywords):
                print(f"   [InstructionSkip] P{page_num} instruction box at y={y0:.1f} skipped")
                return None
        
        # v22.47: Header Protection
        header_triggers = ['A.', 'B.', 'C.', 'D.', 'E.', 'Distributor Information', 'General Program', 'Planned Training', 'Additional Documentation', 'D. Additional']
        if cell_width > 400 and any(k in clean_text for k in header_triggers):
            return None

        # Detection-time guard: avoid creating fields that overlap colored header bars
        # These thin visual bars often produce tiny spurious text fields. If the
        # cell overlaps a detected header bar by >=25% of its area, skip creating
        # a field unless it's clearly a label (colon or readable padded text), or
        # the cell is large/tall (height > 16pt or width > min(200pt, page_w*0.5)).
        if self.header_bars_cache is None:
            self.header_bars_cache = self._detect_header_bars()
        header_bars = self.header_bars_cache.get(page_num, [])
        if header_bars:
            cell_w = cell_width
            cell_h = cell_height
            cell_area = cell_w * cell_h if cell_w > 0 and cell_h > 0 else 0
            if cell_area > 0:
                for hb in header_bars:
                    ix0 = max(x0, hb['x0']); iy0 = max(y0, hb['y0'])
                    ix1 = min(x1, hb['x1']); iy1 = min(y1, hb['y1'])
                    if ix1 <= ix0 or iy1 <= iy0:
                        continue
                    inter = (ix1 - ix0) * (iy1 - iy0)
                    if inter / cell_area >= 0.25:
                        # Check for colon/readable text in a padded bbox
                        text_inside = ''
                        try:
                            pad_x = max(8, cell_w * 0.3)
                            pad_y = max(4, cell_h * 0.5)
                            ex_x0 = max(0, x0 - pad_x)
                            ex_y0 = max(0, y0 - pad_y)
                            ex_x1 = min(page.width, x1 + pad_x)
                            ex_y1 = min(page.height, y1 + pad_y)
                            extracted = page.within_bbox((ex_x0, ex_y0, ex_x1, ex_y1)).extract_text()
                            if extracted:
                                text_inside = extracted.strip()
                        except Exception:
                            text_inside = ''
                        page_w = getattr(self, 'page_widths', {}).get(page_num, None) or (page.width if hasattr(self, 'pdf') and self.pdf else None)
                        max_allowed_width = 200
                        if page_w is not None:
                            max_allowed_width = min(max_allowed_width, page_w * 0.5)

                        # Allow field if colon/readable text present, or cell is large
                        if text_inside and (':' in text_inside or len(text_inside) > 1):
                            break
                        if cell_h > 16 or cell_w > max_allowed_width:
                            break

                        # Otherwise skip creating a field for this cell
                        print(f"   [HeaderSkip] P{page_num} cell at ({x0:.1f},{y0:.1f}) skipped (Header Bar overlap)")
                        return None
            
        # v22.46: No-Colon Paragraph Kill
        # v24.03/24.08: EXEMPTIONS for Section C bottom and Hero Area
        p_kill_exemptions = ['contractors interested', 'generate interest', 'territory manager', 'how do you plan']
        is_p_kill_exempt = any(k in clean_lower for k in p_kill_exemptions)

        # v22.46: Instruction Keyword Suppression (Surgical)
        instruction_kills = ['service territory', 'qualifying measure', 'consumer promotion', 'percentage', 'please estimate', 'list', 'detail', 'hvac measure type']
        if any(k in clean_lower for k in instruction_kills) and cell_height < 65:
            # Exempt generic Hero areas (Top 250pt) from instruction kills
            # Titles often contain "list" or "detail" but shouldn't be killed.
            if not (y0 < 250) and not is_p_kill_exempt:
                return None

        if ':' not in cell_text and len(words) > 3 and cell_height < 65 and not is_p_kill_exempt:
            label_keywords = ['name', 'address', 'city', 'state', 'zip', 'phone', 'email', 'date', 'brands', 'contact', 'contractor', 'license']
            is_label = any((k in clean_lower and (len(clean_lower) < 15 or clean_lower.startswith(k))) for k in label_keywords)
            
            # Generic Hero Area Protection: Top of any page (titles/headers) shouldn't satisfy instruction kills
            is_hero_area = (y0 < 150)
            
            # v33.08: Allow narrative question boxes with substantial room below
            if words:
                text_bottom = max(w['bottom'] for w in words)
                room_below = y1 - text_bottom
            else:
                room_below = y1 - y0
            is_narrative_box = (cell_width > 180 and room_below > 8 and cell_height >= 14)
            if not is_label and not is_hero_area and not is_p_kill_exempt and not is_narrative_box:
                return None

        # v22.51: Nuclear Footer Sweep (Page 1)

        # 2. COORDINATE CALCULATION
        
        # v24.03: Lowered margin to 1.5pt for closer "premium" fit
        field_margin = 1.5
        field_x0, field_y0, field_x1, field_y1 = x0 + field_margin, y0 + 1.2, x1 - field_margin, y1 - 1.2
        # v35.50 / v36.0: UNIVERSAL TEXT-SHUNNING LOGIC - Fields go BESIDE or UNDER labels, never OVER.
        # If the cell already contains text, adjust field boundaries to avoid covering it.
        is_label_at_top = False
        current_source = source_type
        
        # v36.0: Fallback words with expanded bbox + low overlap so we don't miss boundary text
        if not words and cell_text.strip():
            words = self._get_words_in_bbox(page_num, (x0 - 3, y0 - 3, x1 + 3, y1 + 3), overlap_threshold=0.08)
        
        if self.debug_mode:
            # print(f"   [Analyze] P{page_num} cell '{cell_text[:20]}' source={source_type} h={cell_height:.1f} w={cell_width:.1f} has_words={bool(words)}")
            pass

        if words:
            tx0 = min(w['x0'] for w in words)
            ty0 = min(w['top'] for w in words)
            tx1 = max(w['x1'] for w in words)
            ty1 = max(w['bottom'] for w in words)
            room_right = x1 - tx1
            room_below = y1 - ty1
            is_left_aligned = (tx0 - x0 < 20)
            is_top_aligned = (ty0 - y0 < 14)
            
            # UNIVERSAL - Prefer UNDER placement in cells if room exists (>10pt)
            # This handles blue boxes/standard headers where text is a label at top.
            # Only fallback to BESIDE if there is absolutely no vertical room.
            
            can_fit_under = (room_below > 10)
            can_fit_beside = (room_right > 20)
            
            if can_fit_under:
                # Force UNDER placement for standard form fields (Page 1 blue boxes)
                field_y0 = max(field_y0, ty1 + 2.0)
                field_x0 = x0 + field_margin  # full width to left edge of cell
                is_label_at_top = True
                current_source = 'structural_box'
            elif can_fit_beside:
                 # Fallback to BESIDE only if tight vertically (Page 14 top tables)
                field_x0 = max(field_x0, tx1 + 4.5)
                current_source = 'structural_box'
            else:
                 # No room? Default to UNDER but squeeze it (last resort)
                field_y0 = max(field_y0, ty1 + 1.0)
                is_label_at_top = True
            
            # v35.50 / v36.0: DATA VALUE PROTECTION (e.g. "20", "40" in Point Value column)
            if not is_label_at_top and len(clean_text) < 5 and cell_height < 18:
                if room_right > 15:
                    field_x0 = max(field_x0, tx1 + 4.0)
                elif tx0 - x0 > 15:
                    field_x1 = min(field_x1, tx0 - 4.0)
                else:
                    if cell_width < 60:
                        print(f"   [LabelOverlapKill] P{page_num} cell '{clean_text}' at y={y0:.1f} skipped (covers value)")
                        return None

        # Path B: Structural Box Fill - ONLY for EMPTY cells (no words)
        # Cells WITH words are fully handled by the unified section above
        elif source_type in ['strict_grid', 'table'] and not words:
            current_source = 'structural_box'
            # Empty cell - field fills the box
            pass
            
        # Path C: Cells with words that didn't match the unified section's source_type
        # This handles 'table_data_row' without going through Path B's elif
            
        # 3. UNIQUE NAMING & FORMATTING
        
        # v25.04: If box is empty, search for label text ABOVE the box
        label_text = cell_text
        if not label_text.strip() or label_text.lower().startswith('field'):
            # Look for words in a strip above the box (up to 45pt above for multi-line)
            try:
                above_bbox = (x0, max(0, y0 - 45), x1, y0 + 1)
                above_words = page.within_bbox(above_bbox).extract_words()
                if above_words:
                    # Filter out purely punctuation/short noise
                    meaningful = [w for w in above_words if len(w['text']) > 1 or w['text'].isalnum()]
                    if meaningful:
                        # Join and clean
                        lines = {}
                        for w in meaningful:
                            ry = round(w['top'], 1)
                            if ry not in lines: lines[ry] = []
                            lines[ry].append(w)
                        
                        sorted_lines = [lines[k] for k in sorted(lines.keys())]
                        full_label = []
                        for line in sorted_lines:
                            full_label.append(" ".join([w['text'] for w in sorted(line, key=lambda w: w['x0'])]))
                        
                        label_text = " ".join(full_label)
            except: pass
        
        field_name = self._clean_field_name(label_text.strip())
        # v25.04: Strip leading Section markers (A. , B. , etc)
        import re
        field_name = re.sub(r'^[A-Z]\.\s+', '', field_name)
        
        # v25.04: If label is still too long, limit to first 12 words
        field_name = self._clean_field_name(label_text.strip())
        if not field_name:
            field_name = self._get_column_header(x0, x1, headers_map) or "Field"
        # v26.05: Limit insanely long question names to first 12 words
        if len(field_name.split('_')) > 12:
            field_name = "_".join(field_name.split('_')[:12])
        
        field_name = self._generate_unique_name(field_name, page_num)
        field_format, format_opts = self._detect_field_format(cell_text, field_name)
        
        if self.debug_mode:
            # print(f"   [Analyze] P{page_num} -> Result: {field_name} at {field_x0:.1f},{field_y0:.1f} w={field_x1-field_x0:.1f} source={current_source}")
            pass

        # 4. FINAL SIZE & BOUNDARY POLISH
        
        # v27.01/28/31: Narrative Height Guarantee & Row Standardization
        # Sections with large white boxes at bottom of pages or top of subsequent pages
        page_h = self.page_heights.get(page_num, 792)
        is_bottom_narrative = (y1 > (page_h - 80))
        is_top_narrative = (y0 < 250 and page_num > 0) # Generic "hero" area on subsequent pages
        
        # v35.11: EXCLUDE empty structural boxes from hero_narrative - they should fill their cells
        is_empty_structural = (current_source == 'structural_box' and not words)
        is_narrative_target = (is_bottom_narrative or is_top_narrative) and not is_empty_structural
        
        # v31.06: Generic Row Standardization for Structural Boxes
        # Any standard-height visual box (15-25pt) should have a full-size field.
        # v33.19: EXEMPT cells with label-at-top - those need below-label placement, not full-fill
        is_standard_row = (15 < cell_height < 25) and current_source == 'structural_box' and not is_label_at_top
        is_narrative = (cell_height >= 25)
        
        # Standard height target for rows (18.2pt is our premium standard)
        target_min_h = 18.2 if is_standard_row else (24.0 if is_narrative else 10.0)
        current_h = field_y1 - field_y0
        
        if current_h < target_min_h or is_narrative_target or is_standard_row:
            # Force target height by spreading UPWARDS and DOWNWARDS
            # v28.02: Prioritize reaching the bottom for narrative targets
            if is_narrative_target:
                field_y1 = y1 - 0.7
                field_y0 = max(y0 + 0.7, field_y1 - target_min_h)
            elif is_standard_row:
                # Center vertically and FILL cell (tight 0.4pt padding for professional look)
                # This ensures fields look "full" as requested by the user.
                # NOTE: is_label_at_top cells are excluded from is_standard_row
                h_target = cell_height - 0.8
                center_y = (y0 + y1) / 2
                field_y0 = center_y - (h_target / 2)
                field_y1 = center_y + (h_target / 2)
            elif is_label_at_top:
                # v33.04: PRESERVE below-label placement - only extend y1 downward
                # Do NOT override field_y0 to move it above the text label
                field_y1 = min(y1 - 0.5, field_y1 + 10.0)
                # field_y0 stays at text_bottom + 2.0 from earlier
            else:
                field_y1 = min(y1 - 0.5, field_y1 + 10.0)
                field_y0 = max(y0 + 1.0, field_y1 - target_min_h)
        
        # v25.04: Universal Boundary Clipping (Hard Constraint)
        # Tighter 0.5pt margins for professional look
        field_x0 = max(field_x0, x0 + 0.5)
        field_x1 = min(field_x1, x1 - 0.5)
        field_y0 = max(field_y0, y0 + 0.5)
        field_y1 = min(field_y1, y1 - 0.5)
        
        # v24.05: Surgical Nuke for P2 Section D Header Garbage
        # v25/26: Removing this because it kills the typing area for Section C questions.
        # Instead, we rely on _detect_header_bars and path protection.
        pass

        # v24.05: Page 1 Section B/C Gutter Alignment (Reverted in v25 per user request)
        # We allow fields to be full-width again for a "closer" Round 23 look
        pass

        # 5. OVERLAP CHECK
        if self._position_has_field(page_num, field_x0, field_y0, field_x1, field_y1):
            return None

        # v33.31: Minimum Field Dimension Validation
        # Prevent garbage fields with invalid dimensions
        final_width = field_x1 - field_x0
        final_height = field_y1 - field_y0
        if final_width < 14 or final_height < 6:
            # print(f"   [GarbageSkip] Field '{field_name}' skipped: {final_width:.1f}x{final_height:.1f}pt too small")
            return None
            
        # v33.72: Kill extreme aspect ratio fields (tall/thin garbage)
        if final_width > 0 and final_height > 0:
            aspect = final_height / final_width
            if aspect > 6.0:
                 return None
                 
        if final_height < 0 or final_width < 0:
            # Invalid negative dimensions
            return None

        # v35.0: Detect structural image boxes using unified placeholder detection
        # Detects both embedded images AND vector-drawn placeholder icons
        is_image_box = False
        has_placeholder_image = self._cell_has_placeholder_image(page_num, x0, y0, x1, y1)
        
        if current_source == 'structural_box' and has_placeholder_image:
             ratio = final_width / final_height if final_height > 0 else 0
             if (final_width > 80 and final_height > 80 and 
                 final_width < 600 and final_height < 500 and
                 ratio < 3.5 and ratio > 0.3):
                     is_image_box = True
                     print(f"   [ImageBox] P{page_num} box with placeholder at y={field_y0:.1f} detected as image upload")

        # 6. RETURN DEFINITION
        return {
            'page': page_num,
            'x0': field_x0,
            'y0': field_y0,
            'x1': field_x1,
            'y1': field_y1,
            'name': field_name,
            'label': field_name,
            'format': 'image' if is_image_box else field_format,
            'format_opts': format_opts,
            'source': current_source,
            'skip_snapping': True if current_source == 'structural_box' else False,
            'is_image_box': is_image_box
        }
    def _detect_signature_fields(self):
        """
        Detect signature, date, and name fields from underscore TEXT patterns.
        
        LEARNED: Use EXACT underscore bounds for field width.
        These are TEXT underscore characters like "_______" not vector lines.
        
        PATTERNS DETECTED:
        - "Signature: ____________________"
        - "Date: ____________________"
        - "Authorized Representative Name: ____________________"
        - "Name: ____________________"
        - "Title: ____________________"
        """
        sig_fields = 0
        
        # Labels that have underscore fill patterns
        # EXPANDED: Include Manager, Lead, Representative, Technician, etc.
        fill_labels = ['signature:', 'date:', 'name:', 'title:', 
                      'manager:', 'lead:', 'representative:', 'technician:',
                      'supervisor:', 'inspector:', 'contractor:']
        
        for page_num in range(self.page_count):
            page = self.pdf.pages[page_num] # Access page from self.pdf
            if page_num not in self.page_words:
                self.page_words[page_num] = page.extract_words()
            if page_num not in self.page_lines:
                self.page_lines[page_num] = page.lines
            words = self.page_words[page_num]
            used_underscore_indices = set()
            
            for i, word in enumerate(words):
                text = word['text'].strip().lower()
                
                # Check for any fill-in label (signature, date, name, title, etc.)
                is_fill_label = any(label in text for label in fill_labels)
                
                if is_fill_label:
                    label_y = float(word['top'])
                    label_x1 = float(word['x1'])
                    
                    # Find the FIRST underscore block after this label
                    for j in range(i + 1, min(i + 8, len(words))):  # Look up to 8 words ahead
                        if j in used_underscore_indices:
                            continue
                        
                        next_word = words[j]
                        next_text = next_word['text']
                        next_x0 = float(next_word['x0'])
                        next_y = float(next_word['top'])
                        
                        # Same line (within 5pt) and contains underscores (at least 4)
                        if abs(next_y - label_y) < 5 and '____' in next_text:
                            used_underscore_indices.add(j)
                            
                            # Use EXACT bounds
                            underscore_start = next_x0
                            underscore_end = float(next_word['x1'])
                            
                            # Determine field type from label
                            if 'signature' in text:
                                # v22.5: Improved look-back to build full labels like "Authorized Representative Signature"
                                full_label_parts = []
                                for k in range(max(0, i - 4), i + 1):
                                    label_word = words[k]
                                    lw_y = float(label_word['top'])
                                    if abs(lw_y - label_y) < 3:  # Same line
                                        full_label_parts.append(label_word['text'].rstrip(':'))
                                full_label = ' '.join(full_label_parts) if full_label_parts else "Signature"
                                field_type = self._clean_field_name(full_label)
                                field_format = None
                            elif 'date' in text:
                                field_type = "Date"
                                field_format = 'date'
                            elif 'name' in text:
                                # Check for multi-word labels like "Authorized Representative Name:"
                                # Look back to build full label
                                full_label_parts = []
                                for k in range(max(0, i - 3), i + 1):
                                    label_word = words[k]
                                    lw_y = float(label_word['top'])
                                    if abs(lw_y - label_y) < 3:  # Same line
                                        full_label_parts.append(label_word['text'].rstrip(':'))
                                full_label = ' '.join(full_label_parts) if full_label_parts else "Name"
                                field_type = self._clean_field_name(full_label)
                                field_format = None
                            elif 'title' in text:
                                field_type = "Title"
                                field_format = None
                            elif 'manager' in text:
                                # Look for "Account Manager" or similar
                                full_label_parts = []
                                for k in range(max(0, i - 2), i + 1):
                                    label_word = words[k]
                                    lw_y = float(label_word['top'])
                                    if abs(lw_y - label_y) < 3:
                                        full_label_parts.append(label_word['text'].rstrip(':'))
                                full_label = ' '.join(full_label_parts) if full_label_parts else "Manager"
                                field_type = self._clean_field_name(full_label)
                                field_format = None
                            elif 'lead' in text:
                                # Look for "Program Lead" or similar
                                full_label_parts = []
                                for k in range(max(0, i - 3), i + 1):
                                    label_word = words[k]
                                    lw_y = float(label_word['top'])
                                    if abs(lw_y - label_y) < 3:
                                        full_label_parts.append(label_word['text'].rstrip(':'))
                                full_label = ' '.join(full_label_parts) if full_label_parts else "Lead"
                                field_type = self._clean_field_name(full_label)
                                field_format = None
                            elif any(label_type in text for label_type in ['representative', 'technician', 
                                                                            'supervisor', 'inspector', 'contractor']):
                                # Generic handling for other role labels
                                full_label_parts = []
                                for k in range(max(0, i - 2), i + 1):
                                    label_word = words[k]
                                    lw_y = float(label_word['top'])
                                    if abs(lw_y - label_y) < 3:
                                        full_label_parts.append(label_word['text'].rstrip(':'))
                                full_label = ' '.join(full_label_parts) if full_label_parts else text.rstrip(':').title()
                                field_type = self._clean_field_name(full_label)
                                field_format = None
                            else:
                                field_type = "Field"
                                field_format = None
                            
                            field_name = self._generate_unique_name(field_type, page_num)
                            
                            # Position field so bottom sits ON the underscore/line
                            # The underscore text indicates where the line is
                            # Field should have its BOTTOM at the baseline of the underscore
                            underscore_bottom = float(next_word['bottom'])
                            field_height = 14  # Standard field height
                            
                            # CHECK: Skip if already have a field at this position
                            # This prevents duplicates from table detection + signature detection
                            if self._position_has_field(page_num, underscore_start, 
                                                       underscore_bottom - field_height,
                                                       underscore_end, underscore_bottom):
                                break  # Skip this field, already detected
                            
                            self.text_fields.append({
                                'page': page_num,
                                'x0': underscore_start,
                                'y0': underscore_bottom - field_height,  # Top of field
                                'x1': underscore_end,
                                'y1': underscore_bottom,  # Bottom ON the line
                                'name': field_name,
                                'label': word['text'].replace(':', '').strip(),
                                'format': field_format,
                                'format_opts': {'maxlen': 10} if field_format == 'date' else {},
                                'source': 'signature'
                            })
                            sig_fields += 1
                            
                            width = underscore_end - underscore_start
                            print(f"      {field_name}: width={width:.1f}pt")
                            break
        
        if sig_fields > 0:
            print(f"   Found {sig_fields} signature/date fields")
        
        # v21.5: SECOND PASS - Detect label-BELOW-underscore patterns
        # Pattern: underscore line on one row, label (Date, Signature) on row below
        # This handles PDFs where signature/date lines have labels below them
        self._detect_label_below_underscore_fields()
    
    def _detect_label_below_underscore_fields(self):
        """
        Detect fill-in fields where the label is BELOW the underscore line.
        
        v21.5: This handles a common pattern where signature/date areas have:
        - An underscore line on one row (e.g., y=163)
        - A label like "Date" or "Signature" on the row below (e.g., y=171)
        
        Example from NGRID form Page 2:
            ________________________________________  _______________________________
            Contractor signature                     Date
        """
        fields_added = 0
        
        # Labels to look for below underscore lines (without colon since they're below)
        # v36.1 P34: Add technician name/signature for bottom-of-page lines
        label_patterns = {
            'date': {'format': 'date', 'format_opts': {'maxlen': 10}},
            'signature': {'format': None, 'format_opts': {}},
            'technician signature': {'format': None, 'format_opts': {}},
            'technician name': {'format': None, 'format_opts': {}},
            'contractor signature': {'format': None, 'format_opts': {}},
            'customer signature': {'format': None, 'format_opts': {}},
            'name': {'format': None, 'format_opts': {}},
            'title': {'format': None, 'format_opts': {}},
            'print name': {'format': None, 'format_opts': {}},
        }
        
        for page_num in range(self.page_count):
            page = self.pdf.pages[page_num] # Access page from self.pdf
            if page_num not in self.page_words:
                self.page_words[page_num] = page.extract_words()
            words = self.page_words[page_num]
            
            # Find all underscore blocks on this page
            underscore_blocks = []
            for i, word in enumerate(words):
                if '____' in word['text'] and len(word['text']) >= 10:  # At least 10 underscores
                    underscore_blocks.append({
                        'index': i,
                        'x0': float(word['x0']),
                        'x1': float(word['x1']),
                        'top': float(word['top']),
                        'bottom': float(word['bottom']),
                        'used': False
                    })
            
            # For each underscore block, look for a label BELOW it
            for uscore in underscore_blocks:
                if uscore['used']:
                    continue
                
                # Look for labels that appear 5-15pt below the underscore
                # and are horizontally within the underscore's x-range
                for word in words:
                    word_text = word['text'].lower().strip()
                    word_y = float(word['top'])
                    word_x0 = float(word['x0'])
                    word_x1 = float(word['x1'])
                    
                    # Check if word is at the same level or below the underscore (0-18pt)
                    # v21.5: Changed from y_diff >= 3 to y_diff >= 0 because underscore bottom
                    # and label top can nearly overlap (e.g., underscore bottom=171.4, label top=171.8)
                    y_diff = word_y - uscore['bottom']
                    if y_diff < 0 or y_diff > 18:
                        continue
                    
                    # Check if word is horizontally aligned with underscore
                    # (label center should be within underscore x-range)
                    word_center = (word_x0 + word_x1) / 2
                    if word_center < uscore['x0'] - 10 or word_center > uscore['x1'] + 10:
                        continue
                    
                    # Check if this is a recognized label pattern
                    # Build the full label by checking adjacent words on same line
                    same_line_words = [w for w in words 
                                      if abs(float(w['top']) - word_y) < 3
                                      and uscore['x0'] - 10 <= (float(w['x0']) + float(w['x1']))/2 <= uscore['x1'] + 10]
                    same_line_words.sort(key=lambda w: float(w['x0']))
                    full_label = ' '.join(w['text'].lower().strip() for w in same_line_words)
                    
                    # Match against known patterns
                    matched_pattern = None
                    for pattern, config in label_patterns.items():
                        if pattern in full_label:
                            matched_pattern = pattern
                            field_format = config['format']
                            format_opts = config['format_opts']
                            break
                    
                    if not matched_pattern:
                        continue
                    
                    # Create the field on the underscore
                    field_height = 14
                    field_x0 = uscore['x0']
                    field_x1 = uscore['x1']
                    field_y1 = uscore['bottom']
                    field_y0 = field_y1 - field_height
                    
                    # Check if a field already exists at this position
                    if self._position_has_field(page_num, field_x0, field_y0, field_x1, field_y1):
                        uscore['used'] = True
                        continue
                    
                    # Generate field name from the label
                    clean_label = matched_pattern.replace(' ', '_').title()
                    field_name = self._generate_unique_name(clean_label, page_num)
                    
                    self.text_fields.append({
                        'page': page_num,
                        'x0': field_x0,
                        'y0': field_y0,
                        'x1': field_x1,
                        'y1': field_y1,
                        'name': field_name,
                        'label': matched_pattern.title(),
                        'format': field_format,
                        'format_opts': format_opts,
                        'source': 'label_below_underscore'
                    })
                    
                    uscore['used'] = True
                    fields_added += 1
                    print(f"      {field_name} ({matched_pattern}): format={field_format}")
                    break
        
        if fields_added > 0:
            print(f"   Found {fields_added} label-below-underscore fields")
    
    def _detect_special_label_fields(self):
        """
        v36.5: UNIVERSAL - Detect fields based on structural patterns like 
        trailing colons or label keywords followed by empty space/lines.
        """
        special_fields = 0

        for page_num in range(self.page_count):
            page = self.pdf.pages[page_num]
            words = self.page_words.get(page_num, [])
            lines = self.page_lines.get(page_num, [])
            
            # Get header bar boundaries for field sizing
            header_bars = self._detect_header_bars()
            
            # Get form right edge from header bars
            bars = header_bars.get(page_num, [])
            form_right_edge = max((b['right_edge'] for b in bars), default=page.width - 40)
            
            # UNIVERSAL STRUCTURAL PASS - Target header-bar style labeled fields
            # These are labels ending in ":" that sit on top of a visual underline segment
            for i, word in enumerate(words):
                w_txt = word['text']
                if w_txt.endswith(':') and len(w_txt) > 3:
                    word_y = float(word['top'])
                    word_x1 = float(word['x1'])
                    
                    # Search for an underline immediately below (within 20pt)
                    nearby_underline = [l for l in lines 
                                       if abs(l['y0'] - l['y1']) < 2 
                                       and float(l['top']) > float(word['bottom'])
                                       and float(l['top']) < float(word['bottom']) + 20
                                       and float(l['x0']) < word_x1 + 60
                                       and (float(l['x1']) - float(l['x0'])) > 50]
                    
                    if nearby_underline:
                        line = min(nearby_underline, key=lambda l: l['top'])
                        # Field boundaries based on the underline
                        field_x0 = max(word_x1 + 3, float(line['x0']))
                        field_x1 = float(line['x1'])
                        field_y0 = float(line['top']) - 13
                        field_y1 = float(line['top'])
                        
                        if not self._position_has_field(page_num, field_x0, field_y0, field_x1, field_y1):
                             # Build full label from preceding text on same line
                            row_words = [w for w in words if abs(w['top'] - word_y) < 3 and w['x1'] <= word_x1]
                            full_label = ' '.join(w['text'] for w in sorted(row_words, key=lambda w: w['x0'])).rstrip(':')
                            field_name = self._generate_unique_name(full_label, page_num)
                            self.text_fields.append({
                                'page': page_num, 'x0': field_x0, 'y0': field_y0, 'x1': field_x1, 'y1': field_y1,
                                'name': field_name, 'label': full_label, 'format': None, 'format_opts': {}, 'source': 'special_structural_header'
                            })
                            special_fields += 1
            
            # UNIVERSAL PATTERN 1: Any label ending in ":" followed by 100+ points of empty space or a line
            for i, word in enumerate(words):
                w_txt = word['text']
                if w_txt.endswith(':') and len(w_txt) > 3:
                    word_y = float(word['top'])
                    word_x1 = float(word['x1'])
                    if not self._position_has_field(page_num, word_x1 + 5, word_y - 5, word_x1 + 100, word_y + 15):
                        nearby_lines = [l for l in lines if abs(l['y0'] - l['y1']) < 2 and abs(float(l['top']) - word_y) < 15 and float(l['x0']) > word_x1 - 5]
                        if nearby_lines:
                            line = min(nearby_lines, key=lambda l: float(l['x0']))
                            field_x0 = word_x1 + 3
                            field_x1 = float(line['x1'])
                            field_y0 = float(line['top']) - 13
                            field_y1 = float(line['top'])
                            label_txt = w_txt.rstrip(':')
                            field_name = self._generate_unique_name(label_txt, page_num)
                            self.text_fields.append({
                                'page': page_num, 'x0': field_x0, 'y0': field_y0, 'x1': field_x1, 'y1': field_y1,
                                'name': field_name, 'label': label_txt, 'format': None, 'format_opts': {}, 'source': 'special_structural'
                            })
                            special_fields += 1

            # UNIVERSAL PATTERN 2: Any label keywords (Name, Address) with 3+ lines stacked below
            for i, word in enumerate(words):
                if word['text'] in ['Name:', 'Address:', 'Payee:', 'Contractor:']:
                    label_bottom = float(word['bottom'])
                    stacked_lines = [l for l in lines 
                                   if abs(l['y0'] - l['y1']) < 2 
                                   and float(l['top']) > label_bottom + 2
                                   and float(l['top']) < label_bottom + 120
                                   and (float(l['x1']) - float(l['x0'])) > 150]
                    if len(stacked_lines) >= 3:
                        line_ys = sorted(list(set(round(float(l['top'])) for l in stacked_lines)))
                        for ly in line_ys:
                            field_x0 = min(float(l['x0']) for l in stacked_lines if round(float(l['top'])) == ly)
                            field_x1 = max(float(l['x1']) for l in stacked_lines if round(float(l['top'])) == ly)
                            if not self._position_has_field(page_num, field_x0, ly - 15, field_x1, ly + 2):
                                label_base = word['text'].rstrip(':')
                                field_name = self._generate_unique_name(f"{label_base}_Stack", page_num)
                                self.text_fields.append({
                                    'page': page_num, 'x0': field_x0, 'y0': ly - 14, 'x1': field_x1, 'y1': ly,
                                    'name': field_name, 'label': label_base, 'format': None, 'format_opts': {}, 'source': 'special_structural'
                                })
                                special_fields += 1


        
        if special_fields > 0:
            print(f"   Found {special_fields} special label fields")
    
    # ========== INLINE LABEL DETECTION ==========
    
    def _detect_inline_label_fields(self):
        """
        Detect inline labels like "City:", "State:", "ZIP:" that are NOT in table cells.
        These labels are followed by an empty area where the field should go.
        
        FIXED: Find the actual underline below the label and place field ON the line.
        Also apply proper formatting for State/ZIP/Phone fields.
        """
        inline_fields = 0
        
        # Labels to look for (outside of tables)
        # v16.1: Extended list for Date/Address fields, ADMINISTRATIVE section, and Phone fields
        inline_labels = [
            'city', 'state', 'zip', 'phone', 'fax', 'email', 'e-mail',
            'name', 'address', 'company', 'title', 'county', 'contact', 'person',
            'date', 'application', 'street', 'account', 'electric', 'completion',
            'received', 'approved', 'project', 'manager', 'rep', 'incentive', 'final', 'pre',
            'telephone', 'customer'  # For Customer Telephone / Customer Fax cells
        ]
        
        # Use cached lines from structural pre-load (v21.7 result)
        page_lines = defaultdict(list)  # page_num -> list of (y, x0, x1)
        
        for page_num in range(self.page_count):
            lines = self.page_lines.get(page_num, [])
            for line in lines:
                y = float(line['top'])
                x0, x1 = float(line['x0']), float(line['x1'])
                if x1 - x0 > 20:
                    page_lines[page_num].append((y, x0, x1))
        
        for page_num, words in self.page_words.items():
            # v16.1: Disable inline detection - rely on table detection only
            # Table detection creates ONE field per cell spanning full width
            continue
            
            page_height = self.page_heights.get(page_num, 792)
            lines = page_lines.get(page_num, [])
            
            # Group words by Y position (same line)
            by_row = defaultdict(list)
            for word in words:
                # Round to 5pt buckets
                row_y = round(float(word['top']) / 5) * 5
                by_row[row_y].append(word)
            
            # Sort row keys for looking up previous rows
            sorted_row_ys = sorted(by_row.keys())
            
            for row_idx, row_y in enumerate(sorted_row_ys):
                row_words = by_row[row_y]
                # Sort by x position
                row_words = sorted(row_words, key=lambda w: float(w['x0']))
                
                # Find label words (end with ":" or match known labels)
                labels_on_row = []
                for i, word in enumerate(row_words):
                    text = word['text'].strip()
                    text_lower = text.lower().replace(':', '')
                    
                    # Check if this is a label (key inline labels like State, ZIP, Phone)
                    is_key_label = text_lower in inline_labels
                    
                    if is_key_label:
                        # Build full tooltip by collecting preceding words up to previous label
                        # This captures "Agency Contact Phone:" not just "Phone:"
                        full_label_parts = []
                        
                        # Find where the previous label ended
                        prev_label_end = 0
                        if labels_on_row:
                            prev_label_end = labels_on_row[-1]['index'] + 1
                        
                        # Collect words between previous label and this one
                        for j in range(prev_label_end, i + 1):
                            word_text = row_words[j]['text'].strip()
                            if word_text:
                                full_label_parts.append(word_text.replace(':', ''))
                        
                        full_label = ' '.join(full_label_parts)
                        
                        # If no context on current row (just label alone), look at previous row
                        if len(full_label_parts) == 1 and row_idx > 0:
                            prev_row_y = sorted_row_ys[row_idx - 1]
                            prev_row_words = sorted(by_row[prev_row_y], key=lambda w: float(w['x0']))
                            # Get context from previous row (e.g., "Agency Street Address:")
                            prev_context = []
                            for pw in prev_row_words:
                                pw_text = pw['text'].strip().replace(':', '')
                                # Stop at common ending words
                                if pw_text.lower() in ['address', 'information', 'details']:
                                    prev_context.append(pw_text)
                                    break
                                prev_context.append(pw_text)
                            if prev_context:
                                # Add context as prefix (e.g., "Agency - City")
                                context_str = ' '.join(prev_context[:2])  # First 2 words
                                full_label = f"{context_str} {full_label}"
                        
                        labels_on_row.append({
                            'word': word,
                            'index': i,
                            'x0': float(word['x0']),
                            'x1': float(word['x1']),
                            'y': float(word['top']),
                            'label': text.replace(':', '').strip(),  # Short label for field name
                            'full_label': full_label  # Full label for tooltip
                        })
                
                # Skip rows with only one label or no labels
                if len(labels_on_row) < 2:
                    continue
                
                # Check if any of these labels are already covered by table cells
                already_covered = False
                for label in labels_on_row:
                    for tf in self.text_fields:
                        if tf['page'] == page_num:
                            if (tf['x0'] <= label['x0'] <= tf['x1'] and
                                abs(tf['y0'] - label['y']) < 20):
                                already_covered = True
                                break
                    if already_covered:
                        break
                
                if already_covered:
                    continue
                
                # Find the line near this row of labels
                # Lines can be BELOW labels (normal) or ABOVE labels (Avangrid pattern)
                label_y = labels_on_row[0]['y']
                label_bottom = max(l['y'] for l in labels_on_row) + 10  # Approximate label bottom
                row_lines = []  # Lines that belong to this row
                lines_above = []  # Lines above labels
                for line_y, lx0, lx1 in lines:
                    if 3 < (line_y - label_y) < 20:  # Line is below label
                        row_lines.append((line_y, lx0, lx1))
                    elif -15 < (line_y - label_y) < 0:  # Line is above label (within 15pt)
                        lines_above.append((line_y, lx0, lx1))
                
                best_line_y = row_lines[0][0] if row_lines else None
                best_line_above_y = lines_above[0][0] if lines_above else None
                
                # Create fields between labels
                for i, label in enumerate(labels_on_row):
                    label_text = label['label'].lower()
                    
                    # Find the line that corresponds to this label
                    # First try lines below, then lines above
                    matching_line = None
                    line_is_above = False
                    
                    # Try lines below first
                    for line_y, lx0, lx1 in row_lines:
                        # Line should contain label OR start after it
                        # Case 1: Line starts after label end (with tolerance)
                        # Case 2: Line contains label (lx0 <= label_x0 and lx1 > label_x1)
                        label_x1 = label['x1']
                        label_x0 = label.get('x0', label_x1 - 50)  # Estimate label start
                        
                        if lx0 > label_x1 - 10 or (lx0 <= label_x0 + 10 and lx1 > label_x1):
                            # For middle labels (State), line should start near label end
                            # For last label, line should extend toward page edge
                            if i + 1 < len(labels_on_row):
                                next_label = labels_on_row[i + 1]
                                # Line should end before next label
                                if lx1 < next_label['x0'] + 10:
                                    matching_line = (line_y, lx0, lx1)
                                    break
                            else:
                                # Last label - line should extend toward page edge
                                if lx1 > 500:  # Near right edge
                                    matching_line = (line_y, lx0, lx1)
                                    break
                    
                    # If no line below, try lines above (Avangrid pattern)
                    if not matching_line and lines_above:
                        for line_y, lx0, lx1 in lines_above:
                            label_x1 = label['x1']
                            label_x0 = label.get('x0', label_x1 - 50)
                            
                            if lx0 > label_x1 - 10 or (lx0 <= label_x0 + 10 and lx1 > label_x1):
                                if i + 1 < len(labels_on_row):
                                    next_label = labels_on_row[i + 1]
                                    if lx1 < next_label['x0'] + 10:
                                        matching_line = (line_y, lx0, lx1)
                                        line_is_above = True
                                        break
                                else:
                                    if lx1 > 500:
                                        matching_line = (line_y, lx0, lx1)
                                        line_is_above = True
                                        break
                    
                    # Use matching line bounds if found
                    if matching_line:
                        line_y, line_x0, line_x1 = matching_line
                        # Field starts after label, ends at line end
                        field_x0 = max(line_x0, label['x1'] + 2)
                        
                        # FIX v16: For rightmost fields (no next label), extend to page edge
                        # instead of using line end which may be short of cell boundary
                        page_width = self.page_widths.get(page_num, 612)
                        if i + 1 >= len(labels_on_row):  # This is the rightmost label
                            # Use page width minus right margin (27pt matches typical form boundary)
                            # Many forms have content extending to ~585pt on 612pt pages
                            field_x1 = page_width - 27
                        else:
                            field_x1 = line_x1
                        
                        # Get label vertical bounds for height calculation
                        label_y_top = label['y']
                        label_y_bottom = label_y_top + 9  # Approximate label height
                        
                        # v16.1: Place field BELOW label (user preference for this form)
                        field_y0 = label_y_bottom + 1  # Just below label
                        field_y1 = field_y0 + 10  # Fixed height for good input area
                    else:
                        # Fallback to label-based positioning
                        field_x0 = label['x1'] + 2
                        if i + 1 < len(labels_on_row):
                            next_label = labels_on_row[i + 1]
                            field_x1 = next_label['x0'] - 5
                        else:
                            # FIX v16: Use page width minus margin instead of hardcoded 550
                            page_width = self.page_widths.get(page_num, 612)
                            field_x1 = page_width - 27  # Match form content boundary
                        
                        # v16.1: Place field BELOW label (user preference for this form)
                        label_y_top = label['y']
                        label_y_bottom = label_y_top + 9
                        field_y0 = label_y_bottom + 1  # Just below label
                        field_y1 = field_y0 + 10  # Fixed height for good input area
                    
                    # Calculate field width
                    field_width = field_x1 - field_x0
                    
                    # v16.1: If gap to next label is too narrow, try placing field BELOW label
                    # This handles cases like State (41pt cell) where gap to Zip is only 18pt
                    if field_width < 20 and i + 1 < len(labels_on_row):
                        # Narrow gap - place field below label instead
                        label_y_top = label['y']
                        label_y_bottom = label_y_top + 9  # Label height ~9pt
                        
                        # Field spans full label width below the label text
                        # Use approximate cell boundaries based on labels
                        next_label = labels_on_row[i + 1]
                        field_x0 = label['x0'] - 5  # Start slightly before label
                        field_x1 = next_label['x0'] - 3  # End at next label boundary
                        field_y0 = label_y_bottom + 1  # Just below label
                        field_y1 = field_y0 + 10  # Fixed height for input
                        field_width = field_x1 - field_x0
                    
                    # Skip if still too narrow or too wide
                    max_width = 350 if label_text == 'city' else 250
                    if field_width < 15 or field_width > max_width:
                        continue
                    
                    # Check if position already has a field
                    if self._position_has_field(page_num, field_x0, field_y0 - 5, 
                                                field_x1, field_y1 + 5):
                        continue
                    
                    # Create field name
                    field_name = self._generate_unique_name(label['label'], page_num)
                    
                    # Apply formatting based on label
                    field_format, format_opts = self._detect_field_format(None, label['label'])
                    
                    self.text_fields.append({
                        'page': page_num,
                        'x0': field_x0,
                        'y0': field_y0,
                        'x1': field_x1,
                        'y1': field_y1,
                        'name': field_name,
                        'label': label.get('full_label', label['label']),  # Use full label for tooltip
                        'format': field_format,
                        'format_opts': format_opts,
                        'source': 'inline_label'
                    })
                    inline_fields += 1
                    
                    fmt_str = f", format={field_format}" if field_format else ""
                    print(f"      {field_name}: x={field_x0:.0f}-{field_x1:.0f}, width={field_width:.0f}pt{fmt_str}")
        
        if inline_fields > 0:
            print(f"   Found {inline_fields} inline label fields")
    
    # ========== GENERAL UNDERSCORE TEXT DETECTION ==========
    
    def _detect_general_underscore_fields(self):
        """
        Detect fill-in fields from ANY underscore TEXT patterns on the page.
        
        Patterns like:
        - "Manufacturer: ______________________"
        - "Model #: ______________________"
        - "S/N: ______________________"
        - "SEER (if known): ______________________"
        """
        underscore_fields = 0
        
        for page_num in range(self.page_count):
            page = self.pdf.pages[page_num]
            if page_num not in self.page_words:
                self.page_words[page_num] = page.extract_words()
                self.page_lines[page_num] = page.lines
            words = self.page_words.get(page_num, [])
            used_underscore_indices = set()
            
            # First pass: mark underscores already used by signature detection
            for tf in self.text_fields:
                if tf['page'] == page_num and tf.get('source') == 'signature':
                    # Mark underscores in this area as used
                    for i, word in enumerate(words):
                        if '____' in word['text']:
                            wx0, wx1 = float(word['x0']), float(word['x1'])
                            wy = float(word['top'])
                            # Check if this underscore overlaps with an existing field
                            if (tf['x0'] - 5 <= wx0 <= tf['x1'] + 5 and
                                abs(wy - tf['y0']) < 20):
                                used_underscore_indices.add(i)
            
            # Find underscore patterns and their labels
            # Get page characters for precise underscore positioning
            page_chars = page.chars if hasattr(page, 'chars') else []
            
            for i, word in enumerate(words):
                
                if i in used_underscore_indices:
                    continue
                
                text = word['text']
                
                # Check if this is an underscore pattern (at least 4 underscores)
                if '____' not in text:
                    continue
                
                underscore_x0 = float(word['x0'])
                underscore_x1 = float(word['x1'])
                underscore_y = float(word['top'])
                underscore_bottom = float(word['bottom'])
                
                # v21.50: Extract prefix label (e.g. "Volts____")
                prefix_label = None
                first_underscore_idx = text.find('_')
                if first_underscore_idx > 0:
                    prefix_label = text[:first_underscore_idx].rstrip(':').strip()
                
                # SURGICAL FIX: Find character-level start to skip labels like "Volts"
                found_underscore_x = underscore_x0
                if page_chars:
                    # Filter chars for this word on same line
                    word_chars = [c for c in page_chars if abs(float(c['top']) - underscore_y) < 5 and underscore_x0 - 1 <= float(c['x0']) <= underscore_x1 + 1]
                    word_chars.sort(key=lambda c: float(c['x0']))
                    for char in word_chars:
                        if char['text'] == '_':
                            found_underscore_x = float(char['x0'])
                            break
                elif first_underscore_idx > 0:
                    ratio = first_underscore_idx / len(text)
                    found_underscore_x = underscore_x0 + (underscore_x1 - underscore_x0) * ratio
                
                # Update x0 to start at the actual underscores
                underscore_x0 = found_underscore_x
                
                
                underscore_width = underscore_x1 - underscore_x0
                page_h = self.page_heights.get(page_num, 792)
                is_bottom_of_page = (underscore_y > page_h - 120)
                min_width = 20 if is_bottom_of_page else 30  # v36.1 P34: allow shorter lines at bottom (Technician/Date/Sig)
                if underscore_width < min_width:
                    continue
                
                # v22.5: Skip if inside an invoice table (handled by _detect_underscore_fields)
                if self._is_inside_invoice_table(page_num, underscore_x0, underscore_y, underscore_x1, underscore_bottom):
                    continue
                
                # Check if position already has a field
                # Use exact underscore position with minimal tolerance (field sits ON underscore)
                # y0 = underscore_y - 2 (small buffer above)
                # y1 = underscore_y + 12 (field height)
                if self._position_has_field(page_num, underscore_x0, underscore_y - 1,
                                           underscore_x1, underscore_y + 10):
                    used_underscore_indices.add(i)
                    continue
                
                # Check if this area overlaps existing checkboxes - if so, skip it
                # This avoids creating text fields in checkbox row areas
                overlaps_checkbox = False
                for cb in self.checkboxes:
                    if cb['page'] != page_num:
                        continue
                    cb_x0, cb_y0 = cb['x'], cb['y']
                    cb_x1, cb_y1 = cb['x'] + cb['width'], cb['y'] + cb['height']
                    
                    # Check overlap - use a tolerance of 2pt to avoid edge cases
                    if (underscore_x0 < cb_x1 + 2 and underscore_x1 > cb_x0 - 2 and
                        underscore_y < cb_y1 + 2 and underscore_bottom > cb_y0 - 2):
                        overlaps_checkbox = True
                        break
                
                if overlaps_checkbox:
                    # For "Other____" fields, we still want to create a field but positioned
                    # after the checkbox. Adjust x0 to be past any overlapping checkbox.
                    if 'Other' in text:
                        # Find rightmost overlapping checkbox
                        max_cb_x1 = 0
                        for cb in self.checkboxes:
                            if cb['page'] != page_num:
                                continue
                            cb_x0, cb_y0 = cb['x'], cb['y']
                            cb_x1, cb_y1 = cb['x'] + cb['width'], cb['y'] + cb['height']
                            if (underscore_y < cb_y1 + 2 and underscore_bottom > cb_y0 - 2 and
                                cb_x0 < underscore_x1):
                                max_cb_x1 = max(max_cb_x1, cb_x1)
                        
                        if max_cb_x1 > 0 and max_cb_x1 < underscore_x1 - 30:
                            # Adjust underscore_x0 to start after checkbox + small gap
                            underscore_x0 = max_cb_x1 + 5
                        else:
                            # Can't fit a reasonable field, skip
                            continue
                    else:
                        continue
                
                # Find the label - look for text to the left on same line, but STOP if we hit another underscore
                label = None
                label_word = None
                label_max_x1 = 0
                label_parts = []
                
                # Use a while-loop style lookback for more control
                curr_idx = i - 1
                while curr_idx >= max(0, i - 10):
                    prev_word = words[curr_idx]
                    prev_y = float(prev_word['top'])
                    prev_x1 = float(prev_word['x1'])
                    prev_text = prev_word['text']
                    
                    # Stop if we hit another underscore
                    if '____' in prev_text:
                        break
                        
                    # Same line (within 5pt)
                    if abs(prev_y - underscore_y) < 5:
                        # To the left
                        if prev_x1 < underscore_x0 + 5:
                            clean_text = prev_text.rstrip(':').strip()
                            if clean_text:
                                if len(clean_text) > 1 or clean_text in ['=', 'x', '+', '-', '÷', '/', '#']:
                                    label_parts.insert(0, clean_text)
                                    label_max_x1 = max(label_max_x1, prev_x1)
                            
                            # If it's a very large gap (40pt is enough for most labels), stop
                            if (underscore_x0 - prev_x1) > 40:
                                break
                    curr_idx -= 1
                
                if prefix_label:
                    label_parts.append(prefix_label)
                
                label = ' '.join(label_parts).strip()
                
                # v22.5: Garbage Label Filter
                # Eliminate long descriptive text incorrectly caught as labels
                if label and (len(label) > 90 or len(label.split()) > 12):
                    # print(f"   🗑️  Skipping garbage general label: {label[:30]}...")
                    continue
                    
                # UNIVERSAL OCR FIXES (Technical Terms)
                if label:
                    label_up = label.upper()
                    if 'AMMPS' in label_up or 'AMPS' in label_up: label = 'Amps'
                    if label.upper() == 'VOLTS': label = 'Volts'
                    if label.upper() == 'AMPS': label = 'Amps'
                    if label.upper() == 'CFM': label = 'CFM'
                
                # Generate field name
                if label:
                    base_name = self._clean_field_name(label)
                elif 'Other' in text:
                    # For "Other____" patterns without a colon label
                    base_name = "Other"
                else:
                    base_name = "Field"
                
                field_name = self._generate_unique_name(base_name, page_num)
                
                # Determine format
                field_format, format_opts = self._detect_field_format(None, label)
                
                # SURGICAL: Ensure field starts AFTER label even if coordinates overlap
                final_x0 = max(underscore_x0, label_max_x1 + 3.5)
                
                # Place field ON the underscore line
                field_height = int(self._get_setting('field_height', 14))
                
                self.text_fields.append({
                    'page': page_num,
                    'x0': final_x0,
                    'y0': underscore_bottom - 13,  # Top of field
                    'x1': underscore_x1,
                    'y1': underscore_bottom + 1,  # Bottom slightly below line
                    'name': field_name,
                    'label': label if label else ('Other' if 'Other' in text else None),
                    'format': field_format,
                    'format_opts': format_opts,
                    'source': 'general_underscore'
                })
                used_underscore_indices.add(i)
                underscore_fields += 1
        
        
        if underscore_fields > 0:
            print(f"   Found {underscore_fields} general underscore fields")
    
    def _detect_colon_separated_fields(self):
        """
        Detect multiple fillable fields in rows where labels end with colons.
        """
        colon_fields = 0
        
        for page_num in range(self.page_count):
            page = self.pdf.pages[page_num]
            # v21.5: Skip text-only pages
            if page_num in self.text_only_pages:
                continue
                
            words = self.page_words.get(page_num, page.extract_words())
            if not words:
                continue
            
            # Get horizontal lines for this page to detect label-below-line patterns
            page_lines = page.lines if hasattr(page, 'lines') else []
            horizontal_lines = []
            for line in page_lines:
                width = line['x1'] - line['x0']
                # Horizontal line (at least 50pt wide)
                if width > 50 and abs(line.get('height', 0)) < 2:
                    horizontal_lines.append({
                        'x0': line['x0'],
                        'x1': line['x1'],
                        'y': line['top']
                    })
            
            # v22.42: Use synchronized table cell boundaries (from self.table_cells if present)
            # to ensure we don't double-detect fields in already-processed table cells.
            tall_cells = []
            source_cells = self.table_cells if self.table_cells else []
            for cell in source_cells:
                if cell.get('page') == page_num:
                    cell_height = cell['y1'] - cell['y0']
                    # Cells >15pt tall typical have field below label or internal structure
                    if cell_height > 15:
                        tall_cells.append((cell['x0'], cell['y0'], cell['x1'], cell['y1']))
            
            # Fallback to pdfplumber tables if no custom grid was built
            if not tall_cells:
                tables = page.find_tables()
                for table in tables:
                    for cell in table.cells:
                        cx0, cy0, cx1, cy1 = cell
                        if (cy1 - cy0) > 25:
                            tall_cells.append((cx0, cy0, cx1, cy1))
            
            # Group words by row (y position within 5pt tolerance)
            rows = {}
            for word in words:
                y = round(float(word['top']) / 5) * 5  # Round to nearest 5pt
                if y not in rows:
                    rows[y] = []
                rows[y].append(word)
            
            # Process each row
            for y, row_words in rows.items():
                # Sort by x position
                row_words.sort(key=lambda w: float(w['x0']))
                
                # Check if this row is inside a tall table cell (skip if so)
                # because those cells use field-below-label layout
                # v35.24: Also check if inside ANY table cell (not just tall ones)
                first_word = row_words[0] if row_words else None
                if first_word:
                    word_y = float(first_word['top'])
                    word_x = float(first_word['x0'])
                    in_tall_cell = False
                    for cx0, cy0, cx1, cy1 in tall_cells:
                        if cx0 <= word_x <= cx1 and cy0 <= word_y <= cy1:
                            in_tall_cell = True
                            break
                    if in_tall_cell:
                        continue  # Skip this row - table detection handles it
                    
                    # v35.24: ALSO skip if inside ANY table cell (from pdfplumber)
                    # This prevents colon detection from creating fields in table cells
                    # that don't have underscores but are still part of form tables
                    tables = page.find_tables()
                    in_any_table_cell = False
                    for table in tables:
                        for cell in table.cells:
                            cx0, cy0, cx1, cy1 = cell
                            # Check if word is inside this cell
                            if cx0 <= word_x <= cx1 and cy0 <= word_y <= cy1:
                                # Check if cell is NOT just a header cell (>15pt tall suggests data cell)
                                cell_h = cy1 - cy0
                                if cell_h > 15:
                                    in_any_table_cell = True
                                    break
                        if in_any_table_cell:
                            break
                    if in_any_table_cell:
                        continue  # Skip - let table detection handle it
                
                # Find all colon-ending words (labels)
                colon_labels = []
                for i, word in enumerate(row_words):
                    text = word['text'].strip()
                    # Label ends with colon (but not just a colon)
                    if text.endswith(':') and len(text) > 1 and '____' not in text:
                        # Check if this is a meaningful label
                        label_text = text.rstrip(':').strip()
                        if len(label_text) >= 2:  # At least 2 characters
                            word_x0 = float(word['x0'])
                            
                            # IMPROVED: Look backwards to capture multi-word labels like "Quantity Installed:"
                            # Find preceding words that are part of this label phrase
                            # Stop if we hit another colon-ending word, or a gap > 30pt
                            full_label_parts = [label_text]
                            full_label_x0 = word_x0
                            
                            for j in range(i - 1, -1, -1):
                                prev_word = row_words[j]
                                prev_text = prev_word['text'].strip()
                                prev_x1 = float(prev_word['x1'])
                                
                                # Stop if we hit another colon-ending word (different label)
                                if prev_text.endswith(':') and len(prev_text) > 1:
                                    break
                                
                                # Stop if gap is too large (> 30pt) - indicates different phrase
                                if word_x0 - prev_x1 > 30:
                                    break
                                
                                # Stop if we hit non-alphabetic content (numbers, symbols)
                                if prev_text and not any(c.isalpha() for c in prev_text):
                                    break
                                    
                                # This word is part of the label
                                full_label_parts.insert(0, prev_text)
                                full_label_x0 = float(prev_word['x0'])
                                word_x0 = full_label_x0  # Update for next iteration
                            
                            # Use full label
                            label_text = ' '.join(full_label_parts)
                            
                            # v22.45: Skip instructional paragraphs in colon detector
                            if len(label_text) > 40:
                                continue
                                
                            word_x0 = full_label_x0
                            word_y0 = float(word['top'])
                            
                            # v22.5: Skip if inside an invoice table (handled by _detect_underscore_fields)
                            if self._is_inside_invoice_table(page_num, word_x0, word_y0, float(word['x1']), float(word['bottom'])):
                                continue
                            
                            # Check for label-below-line pattern: if there's a horizontal line
                            # that looks like a fill-in line AFTER this label (like "Name:____")
                            # then skip - form_lines will handle it.
                            # BUT: Don't skip if the line is just a table border that spans the table width
                            has_fill_line_after = False
                            word_x1 = float(word['x1'])
                            for hline in horizontal_lines:
                                # Line must be near the label vertically (above or at same level)
                                if abs(word_y0 - hline['y']) < 10:
                                    # Critical: Line must START near where the label ENDS
                                    # This indicates a fill-in line (Name:_____)
                                    # NOT just a table border spanning the whole row
                                    if abs(hline['x0'] - word_x1) < 15:
                                        has_fill_line_after = True
                                        break
                                    # Also check if line starts just after the colon
                                    if hline['x0'] > word_x1 and hline['x0'] < word_x1 + 30:
                                        has_fill_line_after = True
                                        break
                            
                            if has_fill_line_after:
                                continue  # Skip - form_lines will create field on the line
                            
                            # v22.5: Garbage Label Filter for Colons
                            # v22.42: Tightened from 70 to 40 to catch Section B paragraphs.
                            # Reject overly long descriptive text that happens to contain a colon.
                            if len(label_text) > 40 or len(label_text.split()) > 10:
                                continue
                                
                            colon_labels.append({
                                'word': word,
                                'index': i,
                                'x0': word_x0,
                                'x1': float(word['x1']),
                                'y0': word_y0,
                                'y1': float(word['bottom']),
                                'label': label_text
                            })
                
                # v18: Handle SINGLE colon labels too (for cells like "Type of Insulation Installed:")
                # Previously required 2+ labels in same row, but single-label cells are common
                if len(colon_labels) < 1:
                    continue
                
                # For single-label rows, special handling: field spans from colon to cell/column edge
                
                # For each colon label, create a field from end of label to start of next label
                for idx, label_info in enumerate(colon_labels):
                    # v35.23: Skip if this area already has a field (embedded underscore, etc.)
                    # Check for existing fields in the horizontal area beside this label
                    field_test_x0 = label_info['x1']
                    field_test_x1 = field_test_x0 + 200  # Check 200pt to the right
                    field_test_y0 = label_info['y0'] - 5
                    field_test_y1 = label_info['y1'] + 5
                    
                    has_existing_field = False
                    for tf in self.text_fields:
                        if tf['page'] == page_num:
                            # Check if there's any field overlap in this area
                            if not (tf['x1'] < field_test_x0 or tf['x0'] > field_test_x1 or 
                                    tf['y1'] < field_test_y0 or tf['y0'] > field_test_y1):
                                has_existing_field = True
                                break
                    
                    if has_existing_field:
                        continue  # Skip creating duplicate field
                    
                    # v22.42: Increased buffer from 3 to 5
                    # Field starts after the colon
                    field_x0 = label_info['x1'] + 5
                    
                    # Field ends at the start of the next label, or at row end
                    if idx < len(colon_labels) - 1:
                        next_label = colon_labels[idx + 1]
                        # Look for where the next label's prefix might start
                        # (words that are part of the next label phrase)
                        field_x1 = next_label['x0'] - 3
                        
                        # If the next label has preceding words, find the first word
                        # that's closer to next_label than to current field
                        for j in range(label_info['index'] + 1, next_label['index']):
                            test_word = row_words[j]
                            test_x0 = float(test_word['x0'])
                            test_text = test_word['text'].strip()
                            # If this word looks like it could be part of next label's name
                            if test_text and not test_text.startswith('_'):
                                # Check if it's closer to next label than to field start
                                dist_to_next = next_label['x0'] - float(test_word['x1'])
                                dist_from_field = test_x0 - field_x0
                                if dist_to_next < 30 and dist_from_field > 50:
                                    # This word is part of next label, end field before it
                                    field_x1 = test_x0 - 3
                                    break
                    else:
                        # Last label - find row end, but check for existing fields or column boundaries
                        last_word = row_words[-1]
                        if label_info['index'] < len(row_words) - 1:
                            field_x1 = float(last_word['x1'])
                        else:
                            # Label is last word - need to find the right edge
                            # Check if there's an existing field to the right that would limit us
                            field_x1 = page.width - 40  # Default to page width
                            
                            # Look for any existing text fields on this row to the right
                            for tf in self.text_fields:
                                if tf['page'] == page_num:
                                    # Check if this field is on the same row (within 12pt vertically)
                                    if abs(tf['y0'] - label_info['y0']) < 12:
                                        # Check if this field starts to the right of our field start
                                        if tf['x0'] > field_x0 + 30:
                                            # Use the start of this field as our right boundary
                                            field_x1 = min(field_x1, tf['x0'] - 5)
                            
                            # Also check tables for column boundaries
                            tables = page.find_tables()
                            for table in tables:
                                cells = table.cells
                                for cell in cells:
                                    cx0, cy0, cx1, cy1 = cell
                                    # If cell is in same row and our field is inside this cell
                                    if abs(cy0 - label_info['y0']) < 5:
                                        if cx0 < field_x0 < cx1:
                                            # Use the cell right edge as our limit
                                            field_x1 = min(field_x1, cx1 - 5)
                            
                            # v21.42: Explicit label-aware boundary check
                            # Scan for ANY words to the right of the current label on the same row
                            # that aren't explicitly '_'_ underscore text (which we allow)
                            row_y = label_info['y0']
                            for word in row_words:
                                wx0 = float(word['x0'])
                                wy = float(word['top'])
                                if abs(wy - row_y) < 5 and wx0 > label_info['x1'] + 5:
                                    # This is a word to the right. It shouldn't be covered by a field
                                    # unless it's explicitly '_' underscore text (which we allow)
                                    if '____' not in word['text'] and wx0 < field_x1 + 10:
                                        # Clip field to stop before this word
                                        field_x1 = min(field_x1, wx0 - 5)
                            
                            # v22.5: BLOCK Colon Fields if they cross a vertical divider (line/rect/edge)
                            # This handles tables with missed dividers where colon pattern is used as fallback.
                            # Get dividers for this page from the shared structural cache
                            raw_divs = (self.page_lines.get(page_num, []) + 
                                         self.page_rects.get(page_num, []) + 
                                         self.page_edges.get(page_num, []))
                            for div_item in raw_divs:
                                dx0 = float(div_item.get('x0', div_item.get('x', 0)))
                                dx1 = float(div_item.get('x1', div_item.get('x', dx0) + div_item.get('width', 0)))
                                dy0 = float(div_item.get('top', div_item.get('y0', 0)))
                                dy1 = float(div_item.get('bottom', div_item.get('y1', 0)))
                                dw = abs(dx1 - dx0)
                                dh = abs(dy1 - dy0)
                                
                                if dw < 3 and dh > 5:
                                    dvx = (dx0 + dx1) / 2
                                    # Is divider inside our proposed field? (Between x0 and x1)
                                    if field_x0 + 5 < dvx < field_x1 - 5:
                                        # Does it overlap vertically with the row?
                                        if dy0 - 2 <= label_info['y0'] <= dy1 + 2:
                                            # Clip field to stop AT the divider
                                            field_x1 = dvx - 3
                    
                    # Calculate field width
                    field_width = field_x1 - field_x0
                    
                    # Skip if field is too narrow (< 30pt)
                    if field_width < 30:
                        continue
                    
                    # Before shrinking wide fields, check if there's an actual form LINE
                    # that defines the field width. Look for horizontal lines that:
                    # 1. Are within 10pt above or 15pt below the label Y position
                    # 2. CONTAIN the field start position (line x0 <= field_x0 <= line x1)
                    # 3. End before the next label (if there is one) or at a column boundary
                    line_for_field = None
                    for hline in horizontal_lines:
                        line_y = hline['y']
                        # Line must be near the label (within 10pt above or 15pt below)
                        # v21.5: Expanded from -5 to -10 to handle lines drawn above labels
                        if -10 < line_y - label_info['y0'] < 15:
                            # Line must CONTAIN the field start position
                            # v21.5: Changed from "line x0 near field x0" to "line contains field x0"
                            if hline['x0'] <= field_x0 + 5 and hline['x1'] > field_x0 + 20:
                                # Found a line that defines this field area
                                # But check: does this line extend past the next label?
                                if idx < len(colon_labels) - 1:
                                    next_label = colon_labels[idx + 1]
                                    # If line ends before next label, use line end
                                    if hline['x1'] <= next_label['x0'] - 5:
                                        line_for_field = hline
                                        break
                                    # If line extends past next label, find where to clip
                                    # (the line is a full border, not a field line)
                                else:
                                    # No next label - use this line
                                    line_for_field = hline
                                    break
                    
                    if line_for_field:
                        # Use the actual line end as field boundary (with small margin)
                        field_x1 = line_for_field['x1'] - 2
                        field_width = field_x1 - field_x0
                    elif idx < len(colon_labels) - 1 and field_width > 200:
                        # No line found and field is too wide - shrink it
                        field_x1 = field_x0 + 100
                    
                    # v18: For rows with 2+ colon labels (like "Type: Quantity: R-Value:"), we need
                    # to REMOVE any existing full-row field and create split fields instead.
                    # This fixes the issue where table detection creates 1 field for the whole row.
                    if len(colon_labels) >= 2:
                        # Find and remove any overlapping field in this row
                        fields_to_remove = []
                        for i, existing_field in enumerate(self.text_fields):
                            if existing_field['page'] != page_num:
                                continue
                            # Check if field is on same row (within 10pt vertically)
                            if abs(existing_field['y0'] - label_info['y0']) < 10:
                                # Check if field overlaps with our intended field area
                                if (existing_field['x0'] < field_x1 and existing_field['x1'] > field_x0):
                                    # This is a full-row field that needs to be split
                                    # Only remove if it's wider than what we're creating (indicating it spans multiple)
                                    if existing_field['x1'] - existing_field['x0'] > field_width + 50:
                                        fields_to_remove.append(i)
                        
                        # Remove overlapping fields (reverse order to preserve indices)
                        for i in reversed(fields_to_remove):
                            removed = self.text_fields.pop(i)
                            print(f"   v18: Replaced full-row field '{removed['name']}' with split fields")
                        
                        # v21: After removal, check if a similar-sized field already exists
                        # This prevents duplicates when table detection already created correct fields
                        if self._position_has_field(page_num, field_x0 - 3, label_info['y0'] - 5,
                                                   field_x1 + 3, label_info['y1'] + 5):
                            continue
                    else:
                        # For single-colon rows, still check for existing and skip if found
                        if self._position_has_field(page_num, field_x0 - 3, label_info['y0'] - 5,
                                                   field_x1 + 3, label_info['y1'] + 5):
                            continue
                    
                    # Generate field name from label
                    base_name = self._clean_field_name(label_info['label'])
                    if "Type of Insulation" in base_name:
                        # Ensure we don't accidentally create duplicates if re-running
                        pass
                    field_name = self._generate_unique_name(base_name, page_num)
                    
                    # Detect format based on label
                    field_format, format_opts = self._detect_field_format(None, label_info['label'])
                    
                    # v22.83: Convert pdfplumber coords (top-down) to PDF coords (bottom-up)
                    page_height = self.page_heights.get(page_num, 792)
                    pdf_y0 = page_height - label_info['y1']  # y1 is bottom in pdfplumber
                    pdf_y1 = page_height - label_info['y0']  # y0 is top in pdfplumber
                    
                    # v22.43: Vertical Centering for Colon detection (Round 19)
                    # Use 13pt fixed height centered on the label row
                    row_center_y = (pdf_y0 + pdf_y1) / 2
                    
                    self.text_fields.append({
                        'page': page_num,
                        'x0': field_x0,
                        'y0': row_center_y - 6.5,
                        'x1': field_x1,
                        'y1': row_center_y + 6.5,
                        'name': field_name,
                        'label': label_info['label'],
                        'format': field_format,
                        'format_opts': format_opts,
                        'source': 'colon_separated'
                    })
                    # DEBUG: Log every colon_separated field created
                    colon_fields += 1
        
        
        if colon_fields > 0:
            print(f"   Found {colon_fields} colon-separated fields")

    # ========== HEADER BAR DETECTION AND FIELD CLEANUP ==========
    
    def _detect_header_bars(self):
        """Detect filled rectangular header bars (paths & rects) that define form sections."""
        if self.header_bars_cache is not None:
            return self.header_bars_cache
            
        header_bars = defaultdict(list)
        for page_num in range(self.page_count):
            page = self.pdf.pages[page_num]
            objs = page.objects
            
            # Step 1: Detect candidate shapes (Wide, filled, moderately tall)
            candidates = []
            # Check Rects
            for r in objs.get('rect', []):
                if r.get('fill'):
                    w, h = r['x1'] - r['x0'], r['y1'] - r['y0']
                    # Generic Header Bar: width > 60 (covers table column headers), height > 8
                    if w > 60 and 8 < h < 40:
                        candidates.append(r)
            # Check Paths
            for p in objs.get('path', []):
                if p.get('fill'):
                    w, h = p['x1'] - p['x0'], p['y1'] - p['y0']
                    if w > 60 and 8 < h < 40:
                        candidates.append(p)
            
            for c in candidates:
                color = c.get('non_stroking_color', [])
                # v31.07: Normalize color to list (pdfplumber can return scalar float for grayscale)
                if isinstance(color, (int, float)):
                    color = [color]

                # Check for "Colored" (Not White)
                is_colored = False
                if color:
                    # White is (1,1,1) or (0,0,0,0) or (1)
                    # Check if significantly non-white
                    # Grayscale
                    if len(color) == 1 and color[0] < 0.95: 
                         is_colored = True
                    # RGB
                    elif len(color) == 3 and (color[0] < 0.95 or color[1] < 0.95 or color[2] < 0.95):
                         is_colored = True
                    # CMYK (0,0,0,0 is white)
                    elif len(color) == 4 and sum(color) > 0.05:
                         is_colored = True
                
                if is_colored:
                    # Generic Header Bar: width > 60 (covers table column headers), height > 8
                    # CRITICAL GENERIC CHECK:
                    # A Header Bar MUST contain text (e.g. "Section I", "Manufacturer").
                    # If a colored box is EMPTY, it is likely a FILLED FIELD (Input Box).
                    # This protects fields like P1 "Name" (Blue Box) from being skipped.
                    rect_dict = {'x0': c['x0'], 'y0': c['top'], 'x1': c['x1'], 'y1': c['bottom']}
                    if self._rect_has_text(page_num, rect_dict):
                        header_bars[page_num].append({
                            'x0': c['x0'], 'x1': c['x1'],
                            'y0': c['top'], 'y1': c['bottom'],
                            'right_edge': c['x1'], 'is_dark': True # Reuse flag for logic downstream
                        })
        
        self.header_bars_cache = header_bars
        return header_bars

    def _clip_field_rect_to_avoid_text(self, page_num: int, field: dict, min_width: float = 10, min_height: float = 8) -> bool:
        """
        v36.0: UNIVERSAL LABEL PROTECTION
        Shrink a text field's rect so it never overlaps any text (labels/values) on the page.
        Fields must go BESIDE or UNDER labels, never OVER. Uses page_words; mutates field in place.
        Returns True if any change was made.
        """
        if page_num not in self.page_words:
            try:
                page = self.pdf.pages[page_num]
                self.page_words[page_num] = page.extract_words()
            except Exception:
                return False
        words = self.page_words[page_num]
        x0, y0, x1, y1 = float(field['x0']), float(field['y0']), float(field['x1']), float(field['y1'])
        # Slightly expand to catch words that touch the field boundary
        pad = 3
        expanded = (x0 - pad, y0 - pad, x1 + pad, y1 + pad)
        field_cx = (x0 + x1) / 2
        field_cy = (y0 + y1) / 2
        changed = False
        # Collect words that overlap the field (any intersection)
        for w in words:
            wx0, wtop, wx1, wbot = float(w['x0']), float(w['top']), float(w['x1']), float(w['bottom'])
            if wx1 <= expanded[0] or wx0 >= expanded[2] or wbot <= expanded[1] or wtop >= expanded[3]:
                continue
            # Overlap: field rect vs word rect
            ox0 = max(x0, wx0)
            oy0 = max(y0, wtop)
            ox1 = min(x1, wx1)
            oy1 = min(y1, wbot)
            if ox1 <= ox0 or oy1 <= oy0:
                continue
            # Shrink field so it no longer overlaps this word (BESIDE or UNDER, never OVER)
            word_cx = (wx0 + wx1) / 2
            word_cy = (wtop + wbot) / 2
            new_x0, new_y0, new_x1, new_y1 = x0, y0, x1, y1
            if word_cx < field_cx:
                new_x0 = max(x0, wx1 + 3)
            elif word_cx > field_cx:
                new_x1 = min(x1, wx0 - 3)
            if word_cy < field_cy:
                new_y0 = max(y0, wbot + 3)
            elif word_cy > field_cy:
                new_y1 = min(y1, wtop - 3)
            # Enforce minimum size
            if (new_x1 - new_x0) >= min_width and (new_y1 - new_y0) >= min_height:
                if new_x0 > x0 or new_x1 < x1 or new_y0 > y0 or new_y1 < y1:
                    x0, y0, x1, y1 = new_x0, new_y0, new_x1, new_y1
                    field['x0'], field['y0'], field['x1'], field['y1'] = x0, y0, x1, y1
                    field_cx, field_cy = (x0 + x1) / 2, (y0 + y1) / 2
                    changed = True
        return changed

    def _clip_all_text_fields_away_from_labels(self):
        """
        v36.0: UNIVERSAL PASS - Ensure no text field overlaps any label/value text.
        Runs for both auto-calibration and non-auto modes. Fields go BESIDE or UNDER, never OVER.
        """
        clipped = 0
        for field in self.text_fields:
            page_num = field['page']
            if self._clip_field_rect_to_avoid_text(page_num, field):
                clipped += 1
        if clipped > 0:
            print(f"   [LabelSafe] Clipped {clipped} field(s) so they do not cover text labels")
    
    def _is_image_heavy_page(self, page_num):
        """
        v36.5: UNIVERSAL - Detect if a page is primarily images (photo gallery)
        rather than a form. Checks for large image placeholders and low text density.
        """
        page_h = self.page_heights.get(page_num, 792)
        image_boxes = [f for f in self.text_fields if f['page'] == page_num and f.get('is_image_box')]
        
        # If we have 6+ large image upload fields and they make up > 50% of page fields
        total_p_fields = len([f for f in self.text_fields if f['page'] == page_num])
        if len(image_boxes) >= 6 and (len(image_boxes) / total_p_fields > 0.5 if total_p_fields > 0 else False):
            return True
        return False

    def _cleanup_field_boundaries(self):
        """
        Post-process detected fields to:
        0. v36.0: Clip all text fields away from labels (never over text)
        1. Adjust field right edges to respect header bar boundaries
        2. Remove spurious fields in body text areas (below Disclaimer header)
        3. Fix fields that extend beyond their containing section
        
        This is called after all field detection passes to clean up boundary issues.
        """
        header_bars = self._detect_header_bars()
        removed_count = 0
        adjusted_count = 0
        if self.settings.get('enable_duplicate_fields', False) == True:
            # If duplicates allowed, we might also want to be less aggressive with cleanup?
            # For now, keep them separate.
            pass

        # v36.0: UNIVERSAL - Clip every text field so it never overlaps labels (auto + non-auto)
        print("   [LabelSafe] Clipping fields so they do not cover text labels...")
        self._clip_all_text_fields_away_from_labels()

        # v36.0: Respect the main "Clean Up Garbage Fields" toggle
        enable_cleanup = self.settings.get('enable_garbage_field_cleanup', True)
        if not enable_cleanup:
            print(f"   [FINAL_CLEANUP] Garbage cleanup DISABLED by user setting")

        fields_to_remove = set()  # Use set for indices
        
        # v22.21: Universal Logo/Header Exclusion (Generic Top Margin)
        for i, field in enumerate(self.text_fields):
            page_h = self.page_heights.get(field['page'], 792)
            
            # v36.5: DYNAMIC - Header limit based on proportion of page
            # Covers most header/hero areas without hardcoded page indices.
            header_limit = 70 if page_h < 500 else 95 
            
            # v35.8: GENERIC - Skip cleanup for pages that are clearly photo galleries or image-only
            if self._is_image_heavy_page(field['page']):
                continue  # Skip all header cleanup for photo gallery pages

            # v33.01: PROTECT box_entry (comb) fields from header cleanup
            # Account number fields at top of pages are legitimate fields
            if field.get('source') == 'box_entry' or field.get('comb'):
                continue  # Never nuke comb fields
            
            # v35.7: PROTECT image boxes from header cleanup
            # Image boxes (especially on pages 30-32 "Additional Pictures") are valid
            # upload areas even when near the top of the page
            if field.get('is_image_box'):
                continue  # Never nuke image boxes
            
            # v35.12: PROTECT strict_grid (table cell) fields from header cleanup
            # Table cells detected from visual grids are legitimate data entry areas,
            # not header residue. Example: Page 2 Summary table starts at y~85.
            if field.get('source') == 'strict_grid':
                continue  # Never nuke table cell fields
            
            # v35.21: PROTECT all table-sourced fields from header cleanup
            field_source = field.get('source', '')
            valid_sources = ['strict_grid', 'table', 'table_data_row', 'structural_box', 'colon_label', 'grid_fallback', 'visual_in_cell', 'table_col', 'multi_split']
            if field_source in valid_sources:
                continue  # Don't nuke table-derived or valid detected fields
            
            # v22.85: PROTECT fields with clear labels (universal keywords)
            field_label = (field.get('label', '') or field.get('name', '')).lower()
            generic_p_kws = ['name', 'date', 'address', 'total', 'amt', 'qty', 'status', 'type', 'id', 'ref', 'value', 'install', 'quantity', 'label', 'summary', 'phone', 'email', 'zip', 'city', 'state', 'company', 'project']
            if ':' in field_label or '?' in field_label or any(kw in field_label for kw in generic_p_kws):
                continue

            if enable_cleanup and field['y0'] < header_limit:
                print(f"   [FINAL_CLEANUP] Nuking Header Residue '{field.get('name')}' y0={field['y0']:.1f} (limit={header_limit} page={field['page']})")
                fields_to_remove.add(i)
                removed_count += 1
                continue
            else:
                # print(f\"   [DEBUG_Cleanup] KEEPING '{field.get('name')}' page={field['page']} y0={field['y0']:.1f} limit={header_limit}\")
                pass

            # v33.70: Garbage Field Filters (width, aspect ratio, negative height)
            field_width = field['x1'] - field['x0']
            field_height = field['y1'] - field['y0']
            
            # If cleanup is disabled, skip the heuristic garbage filters
            if not enable_cleanup:
                continue

            # v22.81: Reject fields with negative or zero height (malformed/thin header lines)
            if field_height <= 0:
                print(f"   [FINAL_CLEANUP] Nuking Zero/Negative Height Field '{field.get('name')}' height={field_height:.1f}pt")
                fields_to_remove.add(i)
                removed_count += 1
                continue
            
            # v22.81: Reject extremely thin fields (height < 3pt) - usually header line artifacts
            if field_height < 3:
                print(f"   [FINAL_CLEANUP] Nuking Super Thin Field '{field.get('name')}' height={field_height:.1f}pt")
                fields_to_remove.add(i)
                removed_count += 1
                continue
            
            # Reject extremely narrow fields (vertical garbage lines)
            if field_width < 12:
                print(f"   [FINAL_CLEANUP] Nuking Narrow Garbage '{field.get('name')}' width={field_width:.1f}pt")
                fields_to_remove.add(i)
                removed_count += 1
                continue
            
            # Reject fields with extreme aspect ratios (height >> width)
            if field_height > 0 and field_width > 0:
                aspect_ratio = field_height / field_width
                if aspect_ratio > 6:  # Height is 6x+ wider than width
                    print(f"   [FINAL_CLEANUP] Nuking Tall Garbage '{field.get('name')}' aspect={aspect_ratio:.1f}")
                    fields_to_remove.add(i)
                    removed_count += 1
                    continue
            
            # v22.80: NAME-BASED COLUMN TEXT FILTER - UNIVERSAL
            if field_width < 65:
                # v36.5: Generic field name protections
                fname = field.get('name', '')
                is_valid_name = any(x in fname for x in ['Account', 'Number', 'Date', 'ID', 'Code', 'Value'])
                
                if not is_valid_name:
                    # v36.5: UNIVERSAL - COLUMN CONTENT FILTER
                    garbage_cues = ['energy', 'star', 'bath', 'shower', 'sink', 'each', 'heating', 'cooling', 'combo', 'primary', 'installed']
                    if any(w.lower() in fname.lower() for w in garbage_cues) and not ':' in field.get('label', ''):
                        print(f"   [FINAL_CLEANUP] Nuking Column Residue '{fname}' (narrow & content-like)")
                        fields_to_remove.add(i)
                        removed_count += 1
                        continue
                if i in fields_to_remove:
                        continue
        
        # Remove identified garbage fields
        if fields_to_remove:
            print(f"   [FINAL_CLEANUP] Actively deleting {len(fields_to_remove)} flagged fields")
            for idx in sorted(fields_to_remove, reverse=True):
                 if idx < len(self.text_fields):
                     self.text_fields.pop(idx)

    # end of cleanup
    pass

    def _remove_colored_header_fields(self):
        """
        Generic cleanup pass to remove fields that ended up in dark column headers.
        """
        removed = []
        for i, field in enumerate(self.text_fields):
            # v22.96: Protect descriptively named fields (labels) from color cleanup.
            # Only grid-fallback and line-fields should be candidates for color residue removal.
            fname = field.get('name', '')
            if not (fname.startswith('Cell_') or fname.startswith('Line_') or fname.startswith('Box_')):
                continue

            # Protect intentionally created large boxes
            if field.get('source') == 'empty_box_lines' or field.get('source') == 'user_injected':
                continue

            if self._is_colored_header_cell(field['page'], field['x0'], field['y0'], field['x1'], field['y1']):
                # Heuristic: If it's a very tall field (>40pt), it's likely a data entry area with a background, not a header
                if (field['y1'] - field['y0']) > 40:
                    continue
                print(f"   [ColoredHeader] Removing field '{field.get('name')}' from colored header at P{field['page']+1}")
                removed.append(i)

        for idx in reversed(removed):
            del self.text_fields[idx]
            
        # Clean up checkboxes in headers too
        removed_cb = []
        for i, cb in enumerate(self.checkboxes):
            # v22.96: DO NOT remove trusted checkboxes from colored regions
            # True checkboxes found in Pass 3 are rarely design elements in headers
            source = cb.get('source', '')
            if source in ['vector', 'character']:
                continue
                
            x0, y1 = cb['x'], cb['y'] + cb['height']
            if self._is_colored_header_cell(cb['page'], cb['x'], cb['y'], cb['x'] + cb['width'], y1):
                print(f"   [ColoredHeader] Removing checkbox from header at P{cb['page']+1}")
                removed_cb.append(i)
                
        for idx in reversed(removed_cb):
            del self.checkboxes[idx]

        # v22.97: Conservative purge of *very small* text fields that sit on top of
        # detected colored header bars. This removes thin artifacts introduced during
        # some remake flows while being conservative (only small fields from suspect
        # sources are removed and only when they have significant overlap).
        try:
            self._purge_thin_fields_under_header_bars()
        except Exception as e:
            print('Warning: _purge_thin_fields_under_header_bars raised:', e)

    def _purge_thin_fields_under_header_bars(self, max_height=14, overlap_frac=0.25):
        """
        Conservative cleanup pass:
        - Removes text fields <= max_height points when >= overlap_frac of their
          area overlaps a detected colored header bar on the same page.
        - Only considers fields coming from suspect sources (grid fallback, line-box, etc.)
        - Records a small sample in `self._last_purge_samples` for auditing.
        """
        header_bars = self._detect_header_bars()
        removed = []
        samples = []
        # Exclude 'structural_box' (label cells) from suspect sources to avoid removing real headers
        suspect_sources = (
            'grid_fallback','line_box','general_underscore',
            'empty_box','empty_box_lines','enhanced_line','enhanced_auto_applied','visual_in_cell','box_entry'
        )

        for i, f in enumerate(list(self.text_fields)):
            # Conservative: only target automatically-generated grid/line/box candidates
            fname = f.get('name','')
            if not (fname.startswith('Cell_') or fname.startswith('Line_') or fname.startswith('Box_')):
                continue
            if f.get('source') not in suspect_sources and f.get('source') is not None:
                continue

            fh = f['y1'] - f['y0']
            # Tighten: ignore anything taller than a stricter default (12pt) or non-positive
            if fh > min(max_height, 12) or fh <= 0:
                continue

            bars = header_bars.get(f['page'], [])
            if not bars:
                continue

            farea = (f['x1'] - f['x0']) * (f['y1'] - f['y0'])
            if farea <= 0:
                continue

            removed_this = False
            for b in bars:
                ix0 = max(f['x0'], b['x0']); iy0 = max(f['y0'], b['y0'])
                ix1 = min(f['x1'], b['x1']); iy1 = min(f['y1'], b['y1'])
                if ix1 <= ix0 or iy1 <= iy0:
                    continue
                inter = (ix1 - ix0) * (iy1 - iy0)
                if inter / farea >= overlap_frac:
                    # SAFETY #1: skip if bbox contains readable text (protect labels)
                    text_inside = ''
                    try:
                        if hasattr(self, 'pdf') and self.pdf:
                            plpage = self.pdf.pages[f['page']]
                            field_w = f['x1'] - f['x0']
                            # pad a bit to capture nearby label text
                            pad_x = max(8, field_w * 0.3)
                            pad_y = max(4, fh * 0.5)
                            ex_x0 = max(0, f['x0'] - pad_x)
                            ex_y0 = max(0, f['y0'] - pad_y)
                            ex_x1 = min(plpage.width, f['x1'] + pad_x)
                            ex_y1 = min(plpage.height, f['y1'] + pad_y)
                            extracted = plpage.within_bbox((ex_x0, ex_y0, ex_x1, ex_y1)).extract_text()
                            if extracted:
                                text_inside = extracted.strip()
                    except Exception:
                        text_inside = ''

                    if text_inside:
                        # If there's a colon or more than 1 char, assume label/header and skip
                        if ':' in text_inside or len(text_inside) > 1:
                            print(f"   [ThinHeaderPurge] Skip removing '{fname}' on P{f['page']+1} because text found: '{text_inside[:30]}'")
                            continue

                    # SAFETY #2: width check - avoid removing wide fields
                    page_w = getattr(self, 'page_widths', {}).get(f['page'], None)
                    max_allowed_width = 200
                    if page_w is not None:
                        max_allowed_width = min(max_allowed_width, page_w * 0.5)
                    field_w = f['x1'] - f['x0']
                    if field_w > max_allowed_width:
                        print(f"   [ThinHeaderPurge] Skip removing '{fname}' on P{f['page']+1} because width {field_w:.1f} > {max_allowed_width:.1f}")
                        continue

                    print(f"   [ThinHeaderPurge] Removing small field '{fname}' from P{f['page']+1} h={fh:.1f} overlap={inter/farea:.2f} src={f.get('source')}")
                    try:
                        self.text_fields.remove(f)
                    except ValueError:
                        pass
                    samples.append({'page': f['page']+1, 'rect': [f['x0'], f['y0'], f['x1'], f['y1']], 'h': fh, 'source': f.get('source'), 'text': text_inside})
                    removed.append(f)
                    removed_this = True
                    break
            if removed_this:
                continue

        print(f"   [ThinHeaderPurge] Removed {len(removed)} thin header fields")
        self._last_purge_samples = samples
        return samples

    def _adjust_fields_to_header_bars(self):
        """
        Adjusts field boundaries based on detected header bars.
        This is called after all field detection passes to clean up boundary issues.
        """
        header_bars = self._detect_header_bars()
        removed_count = 0
        adjusted_count = 0
        fields_to_remove = set()
        
        # DIAGNOSTIC FALLBACK: If header bars were NOT detected on some pages,
        # list small suspect text fields near the top of the page so we can
        # inspect artifacts that look like header-under thin fields but are
        # embedded in images/bitmaps and thus missed by vector header detection.
        for p in range(self.page_count):
            print(f"   [HeaderThinFallbackRun] P{p+1} has_header_bar={bool(header_bars.get(p))}")
            if p not in header_bars or not header_bars[p]:
                # Scan for small thin fields near top of page (y0 < 220) from suspect sources
                for i, tf in enumerate(self.text_fields):
                    if tf.get('page') != p:
                        continue
                    fh = tf['y1'] - tf['y0']
                    gap_top = tf['y0']
                    # exclude structural_box so we don't flag labels
                    suspect_sources = ('grid_fallback','line_box','general_underscore','empty_box','empty_box_lines','enhanced_line','enhanced_auto_applied','visual_in_cell')
                    if fh <= 22 and gap_top <= 220 and (tf.get('source') in suspect_sources or fh < 8):
                        print(f"   [HeaderThinFallbackScan] P{p+1} field '{tf.get('name')}' src={tf.get('source')} x0={tf['x0']:.1f} x1={tf['x1']:.1f} y0={tf['y0']:.1f} y1={tf['y1']:.1f} h={fh:.1f}")
                    # BROADER SCAN (no source filter): report all small thin fields near top
                    if fh <= 22 and gap_top <= 220:
                        print(f"   [HeaderThinFallbackScanAll] P{p+1} field '{tf.get('name')}' src={tf.get('source')} x0={tf['x0']:.1f} x1={tf['x1']:.1f} y0={tf['y0']:.1f} y1={tf['y1']:.1f} h={fh:.1f}")

        for page_num in header_bars:
            bars = sorted(header_bars[page_num], key=lambda b: b['y0'])
            if not bars:
                continue
            # Diagnostic: print header bars detected for this page
            try:
                bar_desc = ', '.join([f"(x0={b.get('x0',0):.1f},x1={b.get('x1',0):.1f},y0={b.get('y0',0):.1f},y1={b.get('y1',b.get('bottom',0)):.1f},right_edge={b.get('right_edge',0):.1f})" for b in bars])
                print(f"   [HeaderBars] P{page_num+1}: {bar_desc}")
            except Exception:
                pass
            
            # Find the right edge of the form (typically the rightmost header bar edge)
            # BUT: If header bars don't span near the full page width, use table boundaries instead
            page = self.pdf.pages[page_num]
            page_width = page.width

            # v35.42: Remove tiny text fields that sit immediately under header bars
            # These are usually artifacts from grid fallback where a tiny thin field
            # (height < 12pt) sits within a few points below a header bar. They look
            # like thin placeholders under the blue header stripes.
            removed_under_headers = []
            for bar in bars:
                bar_y1 = bar.get('y1', bar.get('bottom', None))
                if bar_y1 is None:
                    continue

                # DIAGNOSTIC PASS: list *all* text_fields that are within 50pt below this header
                # (regardless of source) so we can inspect why HeaderThinNuke didn't trigger.
                for i, tf in enumerate(self.text_fields):
                    if tf.get('page') != page_num:
                        continue
                    fh = tf['y1'] - tf['y0']
                    gap = tf['y0'] - bar_y1
                    fw = tf['x1'] - tf['x0']
                    is_right_column = tf['x0'] > page_width * 0.72 and fw <= 60
                    if 0 <= gap <= 50 and fh <= 30:
                        print(f"   [HeaderThinScan] P{page_num+1} field '{tf.get('name')}' src={tf.get('source')} x0={tf['x0']:.1f} x1={tf['x1']:.1f} y0={tf['y0']:.1f} y1={tf['y1']:.1f} h={fh:.1f} gap={gap:.1f} right_col={is_right_column} bar_y1={bar_y1:.1f}")

                for i, tf in enumerate(self.text_fields):
                    if tf.get('page') != page_num:
                        continue
                    # Small/thin fields (likely artifacts)
                    fh = tf['y1'] - tf['y0']
                    gap = tf['y0'] - bar_y1
                    # Expanded tolerance: allow larger gap and slightly taller tiny fields
                    # v35.45: Removed structural_box to protect P1 labels. Lowered height threshold from 22 to 10 to protect P3 table rows.
                    suspect_sources = ('grid_fallback','line_box','general_underscore','empty_box','empty_box_lines','enhanced_line','enhanced_auto_applied','visual_in_cell')
                    # If the field's top is within 30pt below header bar bottom and is short, or it's extremely small (fh<8)
                    # Expanded tolerances to capture stubborn header-under artifacts seen in complex forms
                    if 0 <= gap <= 30 and fh <= 10 and (tf.get('source') in suspect_sources or fh < 8):
                        # Extra safeguard: don't remove legitimate right-side score/point boxes
                        page = self.pdf.pages[page_num]
                        page_width = page.width
                        fw = tf['x1'] - tf['x0']
                        is_right_column = tf['x0'] > page_width * 0.72 and fw <= 60
                        if is_right_column and fw <= 40:
                            # Likely a point/score box - keep it
                            continue
                        print(f"   [HeaderThinNuke] Removing tiny field '{tf.get('name')}' under header bar at P{page_num+1} (h={fh:.1f} gap={gap:.1f} y0={tf['y0']:.1f} bar_y1={bar_y1:.1f} src={tf.get('source')})")
                        removed_under_headers.append(i)
            for idx in sorted(set(removed_under_headers), reverse=True):
                del self.text_fields[idx]
            
            max_bar_right = max(b['right_edge'] for b in bars)
            max_bar_width = max(b['x1'] - b['x0'] for b in bars)
            
            # If widest header bar is <500pt (not spanning most of page), find form edge from tables
            if max_bar_width < 500:
                # Use table boundaries instead of narrow header bars
                tables = self.page_tables.get(page_num, [])
                if tables:
                    table_right_edge = max(t.bbox[2] for t in tables)
                    # Cap to page width - v16: changed from 36 to 27 for better form boundary
                    form_right_edge = min(table_right_edge, page_width - 27)
                else:
                    # Fallback to page width minus margin
                    form_right_edge = page_width - 27
            else:
                form_right_edge = max_bar_right
            
            # Ensure form_right_edge doesn't exceed page bounds
            # v16: Changed from 36 to 27 to allow fields to extend to actual form boundary
            form_right_edge = min(form_right_edge, page_width - 27)
            
            # Find body text areas (below certain headers like "Disclaimer")
            # These should NOT have fillable fields
            body_text_start_y = None
            words = self.page_words.get(page_num, [])
            
            for bar in bars:
                # Check if this header contains "Disclaimer" or similar
                bar_words = [w for w in words if bar['y0'] <= w['top'] <= bar['y1']]
                bar_text = ' '.join(w['text'] for w in bar_words).lower()
                if 'disclaimer' in bar_text:
                    body_text_start_y = bar['y1']  # Body text starts after Disclaimer header
                    break
            
            # Process fields on this page
            for i, field in enumerate(self.text_fields):
                if field['page'] != page_num:
                    continue
                
                # 0. Remove fields that extend beyond page boundaries (background elements)
                if field['x0'] < 0 or field['x1'] > page_width:
                    print(f"   [DEBUG_Rm] Out of Bounds '{field.get('name')}' x={field['x0']:.1f}-{field['x1']:.1f}")
                    fields_to_remove.add(i)
                    removed_count += 1
                    continue
                
                # 0.5. Remove fields near page edges (if skip_page_edges is enabled)
                # v30.01: Exempt structural_box and Narrative Targets from edge nuke
                is_footer_area = (field['y1'] > (self.page_heights.get(field['page'], 792) - 65))
                if field.get('source') not in ['structural_box', 'embedded_underscore'] and not is_footer_area:
                    if self._is_near_page_edge(page_num, field['x0'], field['y0'], field['x1'], field['y1']):
                        print(f"   [DEBUG_Rm] Near Edge '{field.get('name')}'")
                        fields_to_remove.add(i)
                        removed_count += 1
                        continue
                
                # 1. Remove fields in body text area (after Disclaimer)
                if body_text_start_y and field['y0'] > body_text_start_y:
                    print(f"   [DEBUG_Rm] Body Text Area '{field.get('name')}' y0={field['y0']:.1f} limit={body_text_start_y:.1f}")
                    fields_to_remove.add(i)
                    removed_count += 1
                    continue

                
                # 2. Adjust fields that extend beyond header bar right edge
                # v22.6: PROTECT box_entry fields from clipping (they have precise box-based boundaries)
                if field['x1'] > form_right_edge + 5:  # 5pt tolerance
                    if field.get('source') != 'box_entry':
                        field['x1'] = form_right_edge - 2
                        adjusted_count += 1
                
                # 3. Extend fields that end short of the header bar edge
                # Only for fields near the right side (within 100pt of edge)
                # and that end significantly before the edge (>20pt gap)
                # SKIP: Don't extend inline_label or special_label fields (they have defined boundaries)
                # v16.4: Removed table_data_row from skip list to allow them to expand to form edge if needed
                # BUT we must protect our clipped Quantity fields!
                field_source = field.get('source', '')
                if field_source in ['table', 'inline_label', 'special_label', 'general_underscore', 'embedded_underscore']:
                    continue  # These have defined boundaries from cells/lines/labels
                
                # v16.4: Targeted protection for Quantity column fields (~430-535)
                # We specifically clipped these to ~535 to avoid Incentive column overlap
                # Don't let cleanup logic re-expand them!
                # v16.7: Added name check to avoid trapping other fields (like 'Date') in this column
                f_name_lower = field.get('name', '').lower()
                if 420 < field['x0'] < 520 and field['x1'] < 540:
                    if 'quantity' in f_name_lower or 'qty' in f_name_lower:
                        continue
                
                # v16.5: Universal Safe Expansion
                # Allow ANY field to expand rightward until it hits an obstruction (Field or Text)
                # or the form edge. This fixes "Date of Application" and other left-side fields.
                
                # v21: SKIP expansion for table-sourced fields - they have defined cell boundaries
                # v21.5: Also skip underscore-based fields - they have correct width from underscore text
                # v21.42: Also skip empty boxes and image boxes - they must maintain detected geometry
                if field_source in ['table', 'table_data_row', 'colon_separated', 
                                    'embedded_underscore', 'general_underscore', 'label_below_underscore',
                                    'empty_box', 'box_entry', 'comb_box_grid'] or field.get('is_image_box'):
                    continue  # These fields already have proper boundaries from detection

                
                # 1. Find nearest FIELD obstruction
                nearest_field_x = form_right_edge
                for other in self.text_fields:
                    if other['page'] == page_num and other is not field:
                        # Check if other field is to the right and on same row (vertical overlap)
                        # Relaxed vertical overlap check:
                        y_overlap = min(field['y1'], other['y1']) - max(field['y0'], other['y0'])
                        if other['x0'] >= field['x1'] - 2 and y_overlap > 2:
                            if other['x0'] < nearest_field_x:
                                nearest_field_x = other['x0']
                
                # 2. Find nearest TEXT obstruction
                # Use cached page words
                nearest_text_x = form_right_edge
                for w in words:
                    # Check if word is to the right
                    if float(w['x0']) >= field['x1'] - 2:
                        w_top = float(w['top'])
                        w_bottom = float(w['bottom'])
                        
                        # Check for vertical overlap with field
                        y_overlap = min(field['y1'], w_bottom) - max(field['y0'], w_top)
                        if y_overlap > 1:  # v16.6: Stricter >1 check (was >2 then >0)
                             if float(w['x0']) < nearest_text_x:
                                 nearest_text_x = float(w['x0'])

                # Determine expansion limit (closest obstruction)
                expansion_limit = min(nearest_field_x, nearest_text_x, form_right_edge)
                
                # Apply expansion if there is significant space (>5pt)
                # Subtract small margin (2pt) from limit
                target_x1 = expansion_limit - 2
                
                if target_x1 > field['x1'] + 5:
                    # print(f"Expanding {field.get('name')} from {field['x1']:.1f} to {target_x1:.1f}")
                    field['x1'] = target_x1
                    adjusted_count += 1
        
        # v20: Normalize heights for fields on the same row (City/State/ZIP alignment)
        # Group fields by page and approximate y-center (within 8pt tolerance)
        normalized_count = 0
        for page_num in set(f['page'] for f in self.text_fields):
            page_fields = [f for f in self.text_fields if f['page'] == page_num]
            
            # Group by y-midpoint (bucket by 8pt)
            by_row = defaultdict(list)
            for field in page_fields:
                y_mid = (field['y0'] + field['y1']) / 2
                row_key = round(y_mid / 8) * 8  # 8pt buckets
                by_row[row_key].append(field)
            
            # For each row with 2+ fields, normalize heights
            for row_key, row_fields in by_row.items():
                if len(row_fields) < 2:
                    continue

                # v22.33: Skip normalization for table fields or if fields differ significantly in height
                # This prevents "Crushing" of tall table cells to match adjacent small text labels
                should_normalize = True
                avg_height = sum(f['y1'] - f['y0'] for f in row_fields) / len(row_fields)
                
                for f in row_fields:
                    source = f.get('source', '')
                    h = f['y1'] - f['y0']
                    
                    # Don't normalize table cells - they are geometrically precise
                    if 'table' in source or 'box_entry' in source or 'empty_box' in source or 'structural_box' in source:
                        should_normalize = False
                        break
                        
                    # v25.04: Protect Hero Area fields from normalization
                    if f['page'] == 1 and f['y0'] < 300:
                        should_normalize = False
                        break
                        
                    # Don't normalize if one field is much larger (e.g. textarea vs text input)
                    if abs(h - avg_height) > 5:
                        should_normalize = False
                        break
                
                if not should_normalize:
                    continue

                # Find the row's common y0 (min) and y1 (max) for uniform height
                min_y0 = min(f['y0'] for f in row_fields)
                max_y1 = max(f['y1'] for f in row_fields)
                
                # Apply to all fields in the row
                for field in row_fields:
                    if field['y0'] != min_y0 or field['y1'] != max_y1:
                        field['y0'] = min_y0
                        field['y1'] = max_y1
                        normalized_count += 1
        
        if normalized_count > 0:
            print(f"   Normalized {normalized_count} field heights for row alignment")

        # v33.30: Normalize structural_box/table row heights for consistency
        # Group structural/table fields by row (8pt buckets) and make heights uniform
        struct_norm = 0
        by_row = defaultdict(list)
        for f in self.text_fields:
            if f.get('source') in ['structural_box', 'strict_grid', 'table'] and f.get('page') is not None:
                y_mid = (f['y0'] + f['y1']) / 2
                row_key = round(y_mid / 8) * 8
                by_row[(f['page'], row_key)].append(f)
        for (pg, rk), group in by_row.items():
            if len(group) < 2: continue
            # compute median height
            heights = sorted([g['y1'] - g['y0'] for g in group])
            target = heights[len(heights)//2]
            # apply centered heights
            for g in group:
                cy = (g['y0'] + g['y1'])/2
                new_y0 = cy - target/2
                new_y1 = cy + target/2
                if new_y0 < 0 or new_y1 > self.page_heights.get(pg,792): continue
                if abs((g['y1']-g['y0']) - target) > 0.5:
                    g['y0'] = new_y0
                    g['y1'] = new_y1
                    struct_norm += 1
        if struct_norm > 0:
            print(f"   Normalized structural row heights for {struct_norm} fields")

        # v33.40: Ensure strict_grid text fields fill their cells when appropriate
        filled_count = 0
        for f in list(self.text_fields):
            if f.get('source') == 'strict_grid':
                # Find the table cell that should contain this field based on center
                page = f['page']
                cx = (f['x0'] + f['x1']) / 2
                cy = (f['y0'] + f['y1']) / 2
                found_cell = None
                # literal 'tables' were built earlier in detection and available as self.detector.table_cells
                for cell in self.table_cells:
                    if cell['page'] != page: continue
                    if (cell['x0'] - 2 <= cx <= cell['x1'] + 2) and (cell['y0'] - 2 <= cy <= cell['y1'] + 2):
                        found_cell = cell
                        break
                if not found_cell:
                    continue
                cell_w = found_cell['x1'] - found_cell['x0']
                field_w = f['x1'] - f['x0']
                # Expand small text fields inside strict grids to fill the cell (but keep small padding)
                if field_w < 0.6 * cell_w:
                    old = (f['x0'], f['x1'])
                    f['x0'] = found_cell['x0'] + 2
                    f['x1'] = found_cell['x1'] - 2
                    # Also gently increase height to cell height (without overlaying labels)
                    ch = found_cell['y1'] - found_cell['y0']
                    fh = f['y1'] - f['y0']
                    if ch > fh + 2:
                        cy = (f['y0'] + f['y1'])/2
                        new_h = max(fh, ch - 4)
                        f['y0'] = cy - new_h/2
                        f['y1'] = cy + new_h/2
                    filled_count += 1
        if filled_count > 0:
            print(f"   Filled {filled_count} strict_grid text fields to cell bounds")

        # v33.41: Targeted normalization for specific labeled tables
        def _normalize_table_by_header(header_text: str, exemplar_keywords: list):
            changed = 0
            for page_num, words in self.page_words.items():
                # Find header word sequence approximate match
                header_positions = [w for w in words if header_text.lower() in w['text'].lower()]
                if not header_positions:
                    continue
                # Choose first occurrence
                header_word = header_positions[0]
                header_y = float(header_word['top'])
                # Table area: from header_y downwards up to 300pt (heuristic)
                area_top = header_y
                area_bottom = header_y + 300
                # Collect candidate fields in this box
                candidates = [f for f in self.text_fields if f['page'] == page_num and f['y0'] >= area_top - 2 and f['y1'] <= area_bottom + 2]
                if not candidates:
                    continue
                # Find exemplars (zip/email) heights
                exemplars = [f for f in candidates if any(k in (f.get('name') or '').lower() for k in exemplar_keywords)]
                if not exemplars:
                    # Try text label match in label content
                    exemplars = [f for f in candidates if any(k in (f.get('label') or '').lower() for k in exemplar_keywords)]
                if not exemplars:
                    continue
                target_h = int(sum(f['y1'] - f['y0'] for f in exemplars) / len(exemplars))
                # Apply target height centered in their current midline, respecting page bounds
                for f in candidates:
                    cy = (f['y0'] + f['y1'])/2
                    new_y0 = cy - target_h/2
                    new_y1 = cy + target_h/2
                    if new_y0 < 0 or new_y1 > self.page_heights.get(page_num, 792):
                        continue
                    if abs((f['y1'] - f['y0']) - target_h) > 0.5:
                        f['y0'] = new_y0
                        f['y1'] = new_y1
                        changed += 1
            if changed:
                print(f"   Normalized {changed} fields to exemplar height for header '{header_text}'")

        # Apply to common reported tables
        _normalize_table_by_header('contractor information', ['zip', 'email', 'zip code'])
        _normalize_table_by_header('customer/inspector information', ['primary heating', 'email', 'zip', 'zip code'])


        # v22.5: FINAL BOUNDARY CLAMP (Pass 6E) - Fail-safe divider respect
        # Force-clip ANY field that crosses a visual vertical divider (line/rect/edge)
        clamped_count = 0
        for field in self.text_fields:
            page_num = field['page']
            field_x0 = field['x0']
            field_x1 = field['x1']
            mid_y = (field['y0'] + field['y1']) / 2
            
            # Use cached structural data (v22.5: Includes edges)
            page_v_divs = []
            raw_lines = self.page_lines.get(page_num, []) + self.page_rects.get(page_num, []) + self.page_edges.get(page_num, [])
            for l in raw_lines:
                lx0 = float(l.get('x0', l.get('x', 0)))
                lx1 = float(l.get('x1', l.get('x', lx0) + l.get('width', 0)))
                ly0 = float(l.get('top', l.get('y0', 0)))
                ly1 = float(l.get('bottom', l.get('y1', 0)))
                if abs(lx1 - lx0) < 3 and abs(ly1 - ly0) > 5:
                    page_v_divs.append(((lx0 + lx1) / 2, ly0, ly1))
            
            for vx, vy0, vy1 in page_v_divs:
                # Does this divider cut through the middle of the field?
                # Avoid clipping fields that are BEHIND a divider - check ONLY internal intersections
                # v22.6: PROTECT box_entry fields (comb fields) which contain internal dividers
                if field.get('source') == 'box_entry':
                    continue
                
                if field_x0 + 10 < vx < field_x1 - 5:
                    # Does it overlap vertically with the field?
                    if vy0 - 3 <= mid_y <= vy1 + 3:
                        # CLAMP the field to stop 3pt before the divider
                        field['x1'] = vx - 3
                        clamped_count += 1
                        # Break - continue checking other fields (unlikely multiple dividers cut one field)
                        break
        
        if clamped_count > 0:
            print(f"   🛡️ Final Boundary Clamp: Force-clipped {clamped_count} fields crossing dividers")
        
        # Remove marked fields (in reverse order to maintain indices)
        for i in sorted(fields_to_remove, reverse=True):
            del self.text_fields[i]
        
        if removed_count > 0 or adjusted_count > 0:
            print(f"   Cleaned up fields: {removed_count} removed, {adjusted_count} adjusted")

    # ========== FORM LINE DETECTION ==========
    
    def _enrich_checkbox_labels_from_column_headers(self):
        """
        Enrich checkbox labels by looking for column headers (Pass/Fail/Yes/No/N/A)
        above the checkboxes.
        
        This is called BEFORE radio group identification to ensure checkboxes
        have proper labels for grouping. The original _find_label_above only
        looks 5-30pt above, but column headers can be much further (50-200pt).
        
        This pass specifically looks for RADIO_COLUMN_KEYWORDS above checkboxes
        that don't already have those labels.
        """
        enriched_count = 0
        
        for cb in self.checkboxes:
            # Skip if already has a radio keyword label
            current_label = (cb.get('label') or '').lower().strip()
            if current_label in self.RADIO_COLUMN_KEYWORDS:
                continue
            
            # Look for column header above this checkbox
            column_header = self._find_column_header_for_checkbox(cb)
            if column_header:
                # Found a column header - update the label
                cb['label'] = column_header
                enriched_count += 1
        
        if enriched_count > 0:
            print(f"   Enriched {enriched_count} checkbox labels from column headers")
    
    def _enrich_checkbox_labels_from_beside_text(self):
        """
        Enrich checkbox labels by looking for radio keywords BESIDE the checkbox.
        
        v17.18: This is a second pass after column headers. Many Yes/No labels appear
        immediately to the RIGHT of the checkbox (within 40pt), not above.
        
        This is especially common for forms like:
        "[ ] Yes  [ ] No" or "[ ] Approved  [ ] Denied"
        """
        enriched_count = 0
        
        for cb in self.checkboxes:
            # Skip if already has a radio keyword label
            current_label = (cb.get('label') or '').lower().strip()
            if current_label in self.RADIO_COLUMN_KEYWORDS:
                continue
            
            page_num = cb['page']
            # v21.24: Ensure page_lines is cached. If not, try to get from plumber if we have a word cache
            if page_num not in self.page_lines:
                self.page_lines[page_num] = []
                # We don't want to re-open PDF here if possible, but if we have words
                # we likely have lines cached if we used the centralized pass logic.
                # However, some passes might only extract words.
                # For now, initialize to empty to avoid AttributeErrors.
            if page_num not in self.page_words:
                continue
            
            words = self.page_words[page_num]
            
            # Look for text immediately to the RIGHT of the checkbox
            cb_right = cb['x'] + cb.get('width', 10)
            cb_y = cb['y']
            cb_height = cb.get('height', 10)
            cb_center_y = cb_y + cb_height / 2
            
            best_match = None
            best_dist = float('inf')
            
            for word in words:
                word_x0 = float(word['x0'])
                word_y = float(word['top'])
                text = word['text'].strip().lower()
                
                # Word must be to the RIGHT of checkbox, within 30pt (tightened - 60pt was catching wrong labels)
                if word_x0 < cb_right or word_x0 > cb_right + 30:
                    continue
                
                # Word must be on same line (within 12pt vertically, widened from 8pt)
                if abs(word_y - cb_center_y) > 12:
                    continue
                
                # Check if it's a radio keyword - find the CLOSEST one
                if text in self.RADIO_COLUMN_KEYWORDS:
                    dist = word_x0 - cb_right
                    if dist < best_dist:
                        best_dist = dist
                        best_match = self.RADIO_COLUMN_KEYWORDS[text]
            
            if best_match:
                cb['label'] = best_match
                enriched_count += 1
        
            print(f"   Enriched {enriched_count} checkbox labels from beside text")

    def _overlaps_existing_field(self, page_num: int, x0: float, y0: float, x1: float, y1: float) -> bool:
        """Check if a proposed field overlaps with any existing text fields."""
        for field in self.text_fields:
            if field['page'] != page_num:
                continue
            
            # Check for overlap
            fx0, fy0 = field['x0'], field['y0']
            fx1, fy1 = field['x1'], field['y1']
            
            # Two rectangles overlap if they don't NOT overlap
            # They don't overlap if one is entirely to the left, right, above, or below the other
            if x1 <= fx0 or x0 >= fx1 or y1 <= fy0 or y0 >= fy1:
                continue  # No overlap
            
            return True  # Overlap found
        
        return False

    def _detect_box_entry_fields(self):
        """
        Detect box-type entry fields (rows of small boxes for single-digit entry).
        
        These are UNIQUE fields constructed from vertical lines, commonly used for:
        - Account numbers
        - Phone numbers
        - SSNs
        - Reference codes
        
        Detection strategy:
        1. Find short vertical lines (10-35pt height)
        2. Group by Y position (same row)
        3. Identify sequences with uniform spacing (10-40pt)
        4. Split into separate groups at gaps
        5. Create individual single-character text fields
        """
        from collections import defaultdict
        
        box_fields_created = 0
        
        # Store box fields separately to avoid overlap checks with regular fields
        box_entry_fields = []
        
        for page_num in range(self.page_count):
            page_height = self.page_heights.get(page_num, self.pdf.pages[page_num].height)
            
            # Cache words for label detection
            if page_num not in self.page_words:
                self.page_words[page_num] = self.pdf.pages[page_num].extract_words()
            
            lines = self.page_lines.get(page_num, [])
            
            # Step 1: Extract short vertical lines (box sides)
            # v22.5: Also check thin rects and EDGES (v22.6) that act as lines
            source_lines = list(lines)
            rects = self.page_rects.get(page_num, [])
            for r in rects:
                if abs(r['x0'] - r['x1']) < 3:
                     source_lines.append(r)
            
            # v22.6: Add edges (fix for 9787 comb fields)
            edges = self.page_edges.get(page_num, [])
            if not edges:
                # If edges not cached, it might be in plumber attributes
                edges = getattr(self.pdf.pages[page_num], 'edges', [])

            for e in edges:
                if e.get('orientation') == 'v':
                    source_lines.append(e)

            v_lines = []
            for l in source_lines:
                x0, x1 = l['x0'], l['x1']
                y0, y1 = l['top'], l['bottom']
                
                # Vertical line: nearly zero horizontal extent
                if abs(x0 - x1) < 2:
                    length = abs(y1 - y0)
                    # Box sides are typically 10-35pt tall
                    if 10 < length < 35:
                        v_lines.append({
                            'x': (x0 + x1) / 2,
                            'y0': min(y0, y1),
                            'y1': max(y0, y1),
                            'length': length
                        })
            
            if not v_lines:
                continue

            # v22.12: Deduplicate vertical lines at the same X position (within 1pt)
            # Duplicate edges/rects are common and break spacing logic (0.0 distance)
            # v35.15: Only merge lines that ACTUALLY overlap in Y, not just same X
            # v35.16: Also check that Y ranges genuinely overlap (not just adjacent after merging)
            # v35.17: Use tighter X tolerance (0.1pt) - box entries at x=36.2 should NOT merge with table borders at x=36.0
            # Previously, long table borders would absorb short box entry lines at slightly different X
            v_lines.sort(key=lambda l: (l['x'], l['y0']))
            dedup_v = []
            if v_lines:
                curr = v_lines[0]
                curr_orig_y1 = curr['y1']  # Track original y1 before merging
                for nex in v_lines[1:]:
                    # If EXACTLY same X (within 0.1pt) and TRULY overlapping or adjacent Y, merge
                    # Tighter X tolerance to avoid merging box entry lines with table borders
                    same_x = abs(nex['x'] - curr['x']) < 0.1
                    # Lines must actually touch based on ORIGINAL y1, not merged y1
                    y_adjacent = nex['y0'] < curr_orig_y1 + 2
                    y_gap = nex['y0'] - curr_orig_y1
                    reasonable_gap = y_gap < 10
                    if same_x and y_adjacent and reasonable_gap:
                        curr['y1'] = max(curr['y1'], nex['y1'])
                        # Don't update curr_orig_y1 - keep tracking original
                    else:
                        dedup_v.append(curr)
                        curr = nex
                        curr_orig_y1 = curr['y1']  # Reset for new chain
                dedup_v.append(curr)
            v_lines = dedup_v

            if len(v_lines) < 4:
                continue
            
            # Step 2: Group vertical lines by Y position
            v_groups = defaultdict(list)
            for l in v_lines:
                y_key = round(l['y0'] / 3) * 3
                v_groups[y_key].append(l)
            
            # Step 3: Process each potential box row
            for y_key, vlines in v_groups.items():
                if len(vlines) < 4:
                    continue
                
                # Sort by X position
                vlines.sort(key=lambda l: l['x'])
                
                # v33: IMPROVED - Find UNIFORM spacing clusters instead of all spacings
                # This filters out table border lines that have irregular spacing
                spacings = [vlines[i+1]['x'] - vlines[i]['x'] for i in range(len(vlines)-1)]
                
                # Find the most common spacing (should be the box width)
                # v33: Use stricter range for comb field detection (8-20pt typical)
                spacing_counts = defaultdict(int)
                for s in spacings:
                    rounded = round(s)
                    # v33: Narrower range for comb fields (character boxes are typically 10-18pt)
                    if 8 <= rounded <= 25:
                        spacing_counts[rounded] += 1
                
                if not spacing_counts:
                    continue
                
                # Use the most common spacing as the box width
                box_spacing = max(spacing_counts.keys(), key=lambda k: spacing_counts[k])
                
                # v33: Need at least 8 consistent spacings for a valid comb field
                # (Account numbers have 10-16 boxes typically)
                if spacing_counts[box_spacing] < 8:
                    continue
                
                # Step 4: v33 IMPROVED - Extract ONLY the contiguous uniformly-spaced lines
                # Start from lines with consistent spacing, ignoring table borders
                box_groups = []
                tolerance = max(3, box_spacing * 0.2)  # 20% tolerance or 3pt min
                
                i = 0
                while i < len(spacings):
                    # Look for start of a uniform sequence
                    if abs(spacings[i] - box_spacing) <= tolerance:
                        current_group = [vlines[i]]
                        # Extend while spacing is consistent
                        while i < len(spacings) and abs(spacings[i] - box_spacing) <= tolerance:
                            current_group.append(vlines[i+1])
                            i += 1
                        
                        # v33: Bridge small internal gaps (dashes in account numbers)
                        # Look ahead for continuation after a gap
                        while i < len(spacings) - 1:
                            gap = spacings[i]
                            # Allow gaps up to 2x box spacing (for dashes/separators)
                            if gap <= box_spacing * 2.5 and i + 1 < len(spacings):
                                next_spacing = spacings[i + 1]
                                if abs(next_spacing - box_spacing) <= tolerance:
                                    # Found continuation - bridge the gap
                                    current_group.append(vlines[i + 1])
                                    i += 1
                                    # Continue extending
                                    while i < len(spacings) and abs(spacings[i] - box_spacing) <= tolerance:
                                        current_group.append(vlines[i + 1])
                                        i += 1
                                else:
                                    break
                            else:
                                break
                        
                        if len(current_group) >= 8:  # v33: Minimum 8 boxes for comb field
                            box_groups.append(current_group)
                    else:
                        i += 1
                
                # Step 5: Create ONE COMB field per box group
                # Comb fields automatically space characters evenly across the field
                for group in box_groups:
                    x_start = group[0]['x']
                    x_end = group[-1]['x']
                    
                    # v22.13: Calculate num_boxes based on total width and detected spacing
                    # With N lines, we have N-1 boxes (lines are boundaries between boxes)
                    num_boxes = len(group) - 1
                    
                    # v35.18: x_end is already the RIGHT edge of the last box (the rightmost boundary line)
                    # No need to add box_spacing - the lines define both left AND right edges
                    x_end_full = x_end
                    
                    if num_boxes < 4:
                        continue
                        
                    # v22.14: Use median height and bottom to avoid tall borders
                    # Sort by length to find median
                    sorted_by_len = sorted(group, key=lambda l: l['length'])
                    median_line = sorted_by_len[len(sorted_by_len)//2]
                    
                    box_height = median_line['length']
                    y_bottom = median_line['y1']
                    y_top = y_bottom - box_height
                    
                    # Determine label
                    label = self._find_box_row_label(page_num, x_start, y_top, x_end)
                    
                    print(f"   Page {page_num+1}: Found {num_boxes} box cells at Y={y_top:.0f}, label='{label[:40] if label else 'None'}'")
                    
                    # Generate field name from label
                    if label:
                        import re
                        clean_label = re.sub(r'[^\w\s]', '', label)  # Remove special chars
                        clean_label = re.sub(r'\s+', '_', clean_label.strip())  # Spaces to underscores
                        clean_label = clean_label[:40]  # Limit length
                        field_name = clean_label
                    else:
                        field_name = f"Box_P{page_num+1}_Y{int(y_top)}"
                    
                    # Make name unique
                    field_name = self._generate_unique_name(field_name, page_num)
                    
                    # Box entry fields should take priority over table cell fields
                    # v18.4: BE AGGRESSIVE. If we found a comb, anything else in this horizontal band is suspect.
                    overlapping_indices = []
                    for idx, existing in enumerate(self.text_fields):
                        if existing.get('page') != page_num:
                            continue
                        if existing.get('source') == 'box_entry':
                            continue
                            
                        ex0, ey0, ex1, ey1 = existing['x0'], existing['y0'], existing['x1'], existing['y1']
                        
                        # Check for overlap - v35.14: use x_end_full
                        overlap_x = max(0, min(x_end_full, ex1) - max(x_start, ex0))
                        overlap_y = max(0, min(y_top + box_height, ey1) - max(y_top, ey0))
                        
                        # Area-based check
                        existing_area = (ex1 - ex0) * (ey1 - ey0)
                        box_area = (x_end_full - x_start) * box_height
                        
                        is_high_overlap = False
                        if existing_area > 0 and overlap_x > 0:
                            # If existing field overlaps significantly with our X-range and Y-range
                            # OR if existing field is very close vertically
                            y_dist = min(abs(y_top - ey1), abs(ey0 - (y_bottom)))
                            
                            x_overlap_pct = overlap_x / (x_end_full - x_start)
                            
                            if x_overlap_pct > 0.3: # Any significant X overlap
                                if overlap_y > 2: # Actual Y overlap
                                    is_high_overlap = True
                                elif y_dist < 15: # Vertically very close
                                    is_high_overlap = True
                        
                        if is_high_overlap:
                            overlapping_indices.append(idx)
                            print(f"      Removing overlapping field '{existing.get('name')}' (source: {existing.get('source')}) to favor box_entry")
                    
                    # Remove overlapping fields in reverse order
                    for idx in sorted(overlapping_indices, reverse=True):
                        del self.text_fields[idx]
                    
                    # Add our new field - v35.14: use x_end_full to cover all boxes
                    self.text_fields.append({
                        'page': page_num, 
                        'x0': x_start, 'y0': y_top, 
                        'x1': x_end_full, 'y1': y_bottom,
                        'name': field_name,
                        'source': 'box_entry',
                        'max_len': num_boxes,
                        'comb': True,
                        'alignment': 'left',
                        'tooltip': label if label else f"Entry boxes",
                    })
                    box_fields_created += 1

        
        if box_fields_created > 0:
            print(f"   ✅ Total: {box_fields_created} box entry fields created")
        else:
            print(f"   No box entry fields found")

    
    def _find_box_row_label(self, page_num: int, x_start: float, y_top: float, x_end: float) -> Optional[str]:
        """Find the label text above or to the left of a box row."""
        if page_num not in self.page_words:
            return None
        
        words = self.page_words[page_num]
        candidates = []
        
        for word in words:
            word_x0 = float(word['x0'])
            word_x1 = float(word['x1'])
            word_y = float(word['top'])
            word_bottom = float(word['bottom'])
            
            # Label should be above the boxes (within 50pt) or at the same level
            if word_bottom > y_top + 5:  # Word is below the boxes
                continue
            if word_y < y_top - 60:  # Word is too far above
                continue
            
            # Label should be horizontally aligned with or to the left of boxes
            if word_x0 > x_end + 20:  # Word is to the right of boxes
                continue
            
            text = word['text'].strip()
            if len(text) < 2:
                continue
            
            # Prefer words containing relevant keywords
            text_lower = text.lower()
            score = 0
            if 'account' in text_lower or 'number' in text_lower:
                score += 10
            if 'utility' in text_lower or 'gas' in text_lower or 'electric' in text_lower:
                score += 5
            if 'phone' in text_lower or 'ssn' in text_lower or 'ein' in text_lower:
                score += 10
            
            # Score based on proximity
            y_dist = abs(word_y - y_top)
            x_dist = max(0, x_start - word_x1)  # Distance to left
            proximity_score = 100 - y_dist - (x_dist * 0.5)
            
            candidates.append({
                'text': text,
                'score': score + proximity_score,
                'x0': word_x0,
                'y': word_y
            })
        
        if not candidates:
            return None
        
        # Sort by score (highest first)
        candidates.sort(key=lambda c: c['score'], reverse=True)
        
        # Build label from top candidates that are close together
        best = candidates[0]
        label_parts = [best['text']]
        
        # Add adjacent words on the same line
        for c in candidates[1:6]:
            if abs(c['y'] - best['y']) < 5:  # Same line
                if c['x0'] < best['x0']:
                    label_parts.insert(0, c['text'])
                else:
                    label_parts.append(c['text'])
        
        return ' '.join(label_parts)

    def _detect_large_image_rectangles(self):
        """
        Detect large empty rectangles that should become image upload buttons.
        
        v22.82: Specifically catches rectangles on pages like 4-8 of field inspection reports
        where large empty boxes (161x111pt) are used for "Attach Image" placeholders.
        
        These are distinct from text area boxes in that they are:
        1. Larger square/rectangular shapes (>80pt x >80pt)
        2. Typically within table cells but not caught by table processing
        3. Often have "Attach Image" or similar text nearby
        """
        print("\n   [LARGE_IMAGE_RECT] Starting detection pass...")
        image_box_count = 0
        
        # Minimum size for image boxes (smaller than typical text areas)
        MIN_WIDTH = 80
        MIN_HEIGHT = 80
        MAX_WIDTH = 600
        MAX_HEIGHT = 500
        MIN_ASPECT = 0.3
        MAX_ASPECT = 3.5
        
        for page_num in range(self.page_count):
            page = self.pdf.pages[page_num]
            page_width = float(page.width)
            page_height = float(page.height)
            
            # Get all rectangles on this page
            rects = self.page_rects.get(page_num, [])
            
            print(f"   [LARGE_IMAGE_RECT] Page {page_num}: Found {len(rects)} cached rectangles")
            
            # v22.82: Specifically catches rectangles that act as image placeholders.
            large_count = sum(1 for r in rects if (r['x1'] - r['x0']) > 100 and (r['bottom'] - r['top']) > 100)
            if large_count > 0:
                print(f"   [LARGE_IMAGE_RECT] Page {page_num}: {large_count} large rectangles (>100x100pt)")
            
            if not rects:
                continue
            
            # Get existing fields to check for overlaps
            existing_fields = [f for f in self.text_fields if f['page'] == page_num]
            existing_fields += [f for f in self.checkboxes if f['page'] == page_num]
            
            # Get text characters for checking if rectangle is empty
            page_chars = page.chars
            
            for rect in rects:
                x0 = float(rect['x0'])
                y_top = float(rect['top'])
                x1 = float(rect['x1'])
                y_bottom = float(rect['bottom'])
                
                # v36.1: Skip if in the top of the page (likely header background)
                # unless it specifically has "Attach Image" text
                if y_top < 90:
                    continue
                
                width = x1 - x0
                height = y_bottom - y_top
                
                # Check size requirements
                if width < MIN_WIDTH or height < MIN_HEIGHT:
                    continue
                if width > MAX_WIDTH or height > MAX_HEIGHT:
                    continue
                    
                aspect_ratio = width / height if height > 0 else 0
                if aspect_ratio < MIN_ASPECT or aspect_ratio > MAX_ASPECT:
                    continue
                
                # Check if mostly empty (minimal text inside)
                chars_inside = [c for c in page_chars 
                               if c['x0'] >= x0 and c['x1'] <= x1 
                               and c['top'] >= y_top and c['bottom'] <= y_bottom]
                
                if len(chars_inside) > 5:  # Allow a few chars (like "Attach Image" labels)
                    continue
                
                # Check overlap with existing fields (>70% overlap = skip)
                overlaps_existing = False
                for existing_field in existing_fields:
                    # Handle both formats: rect tuple or x0/y0/x1/y1 dict
                    if 'rect' in existing_field:
                        ex0, ey0, ex1, ey1 = existing_field['rect']
                    else:
                        ex0 = existing_field.get('x0', 0)
                        ey0 = existing_field.get('y0', 0)
                        ex1 = existing_field.get('x1', 0)
                        ey1 = existing_field.get('y1', 0)
                    
                    # Convert PDF y to top-down for comparison
                    ex_top = page_height - ey1
                    ex_bottom = page_height - ey0
                    
                    # Calculate overlap area
                    overlap_x0 = max(x0, ex0)
                    overlap_y0 = max(y_top, ex_top)
                    overlap_x1 = min(x1, ex1)
                    overlap_y1 = min(y_bottom, ex_bottom)
                    
                    if overlap_x1 > overlap_x0 and overlap_y1 > overlap_y0:
                        overlap_area = (overlap_x1 - overlap_x0) * (overlap_y1 - overlap_y0)
                        rect_area = width * height
                        overlap_ratio = overlap_area / rect_area if rect_area > 0 else 0
                        
                        if overlap_ratio > 0.7:
                            overlaps_existing = True
                            break
                
                if overlaps_existing:
                    continue
                
                # v22.89: Store pdfplumber coordinates (top-down)
                # _transform_coords_for_rotation() will convert to pikepdf (bottom-up) 
                # DO NOT convert here - that causes double conversion!
                print(f"   [LARGE_IMAGE_RECT] ✓ P{page_num} Image box: {width:.1f}x{height:.1f}pt at ({x0:.1f}, {y_top:.1f})")
                
                # v22.83: Add small inset (2pt) to account for stroke width
                # This ensures the field sits inside the rectangle, not on the border
                inset = 2
                field_x0 = x0 + inset
                field_x1 = x1 - inset
                # Use pdfplumber coords (y_top, y_bottom) not PDF coords
                field_y0 = y_top + inset
                field_y1 = y_bottom - inset
                
                # Create image upload button field
                field_name = f"ImageUpload_P{page_num}_{image_box_count + 1}"
                self.text_fields.append({
                    'page': page_num,
                    'x0': field_x0,
                    'y0': field_y0,
                    'x1': field_x1,
                    'y1': field_y1,
                    'name': field_name,
                    'label': 'Attach Image',
                    'format': 'image',
                    'format_opts': {},
                    'is_image_box': True,
                    'source': 'large_image_rect'
                })
                image_box_count += 1
        
        print(f"   [LARGE_IMAGE_RECT] ✓ Created {image_box_count} image upload buttons")
        return image_box_count

    def _detect_form_lines(self):
        """
        Detect horizontal lines that indicate form fill areas.
        
        ENHANCED: Uses pdfplumber lines for consistent coordinate system.
        Positions fields so the BOTTOM of the field sits ON the line.
        
        SKIP: Lines that are part of header bar rectangles (borders of filled headers).
        """
        line_fields = 0
        
        # DEBUG
        DEBUG_PAGE = -1  # Set to page index (e.g., 4 for Page 5) to enable debug
        DEBUG_LOGS = False
        
        # Get header bar positions for skipping their border lines
        header_bars = self._detect_header_bars()
        
        # Ensure invoice table bboxes are available for localized skipping
        if not self.invoice_table_bboxes:
            for page_num in range(self.page_count):
                plumber_page = self.pdf.pages[page_num]
                tables = self.page_tables.get(page_num, [])
                for table in tables:
                    if self._is_invoice_info_table(plumber_page, table):
                        tx0, ty0, tx1, ty1 = table.bbox
                        self.invoice_table_bboxes.append({'page': page_num, 'x0': tx0, 'y0': ty0, 'x1': tx1, 'y1': ty1})
        
        
        for page_num in range(self.page_count):
            plumber_page = self.pdf.pages[page_num]
            if DEBUG_PAGE >= 0:
                print(f"   [DEBUG_TRACE] Processing page {page_num}")
            
            page_height = self.page_heights.get(page_num, plumber_page.height)
            
            # Get header bar Y boundaries for this page
            page_header_bars = header_bars.get(page_num, [])
            header_bar_ys = set()
            for bar in page_header_bars:
                # Add top and bottom of each header bar
                header_bar_ys.add(round(bar['y0'], 0))
                header_bar_ys.add(round(bar['y1'], 0))

            
            # Cache words for label detection
            if page_num not in self.page_words:
                self.page_words[page_num] = plumber_page.extract_words()
            if page_num not in self.page_lines:
                self.page_lines[page_num] = plumber_page.lines
            
            # Find table regions and their row boundaries
            table_regions = []
            table_row_ys = set()  # Y positions that are table borders
            tables = self.page_tables.get(page_num, [])
            for table in tables:
                if table.bbox:
                    tx0, ty0, tx1, ty1 = table.bbox
                    table_regions.append(table.bbox)
                    
                    # Get row boundaries from table structure
                    if hasattr(table, 'cells') and table.cells:
                        for cell in table.cells:
                            table_row_ys.add(round(cell[1], 0))  # top
                            table_row_ys.add(round(cell[3], 0))  # bottom
            
            # Get lines from pdfplumber (use 'top'/'bottom' NOT 'y0'/'y1')
            # IMPORTANT: pdfplumber 'y0/y1' are raw PDF coords (y=0 at bottom)
            #            pdfplumber 'top/bottom' are normalized (y=0 at top, like fitz)
            
            # CACHE VERTICAL LINES (for radio group splitting fallback)
            if not hasattr(self, 'vertical_lines'):
                self.vertical_lines = {}
            
            self.vertical_lines[page_num] = []
            
            all_lines = []
            for line in self.page_lines.get(page_num, []):
                # Get line coordinates
                lx0 = float(line.get('x0', 0))
                lx1 = float(line.get('x1', 0))
                ltop = float(line.get('top', 0))
                lbottom = float(line.get('bottom', 0))
                line_width = float(line.get('linewidth', line.get('width', 1)))
                
                # Capture vertical lines for radio group splitting
                if abs(lx0 - lx1) < 2:
                    if not hasattr(self, 'vertical_lines'):
                        self.vertical_lines = {}
                    if page_num not in self.vertical_lines:
                        self.vertical_lines[page_num] = []
                    self.vertical_lines[page_num].append((lx0, ltop, lbottom))
                    continue
                
                # Capture horizontal lines for form field detection
                # Must be approximately horizontal (within 2pt of same Y)
                if abs(ltop - lbottom) < 2:
                    line_length = abs(lx1 - lx0)
                    y_pos = (ltop + lbottom) / 2  # Average Y position
                    
                    # Skip very short lines (< 15pt) - not form fields
                    # NOTE: State underlines can be as short as 17pt
                    if line_length < 15:
                        continue
                    
                    # Skip lines that are too thick (> 3pt) - likely borders, not underlines
                    if line_width > 3:
                        # Allow thick lines if they look like signature/date lines
                        if line_length > 60:
                            sig_words = ['signature', 'date', 'name', 'title', 'inspector', 'contractor', 'authorized', 'technician']
                            nearby = False
                            for w in self.page_words.get(page_num, []):
                                wy = float(w['top'])
                                if abs(wy - y_pos) > 18:
                                    continue
                                wx1 = float(w['x1'])
                                wx0 = float(w['x0'])
                                if wx1 < x0 - 10 or wx0 > x1 + 10:
                                    continue
                                if any(k in w['text'].lower() for k in sig_words):
                                    nearby = True
                                    break
                            if not nearby:
                                continue
                        else:
                            continue
                    
                    # Store as (x0, y, x1, line_width)
                    all_lines.append((min(lx0, lx1), y_pos, max(lx0, lx1), line_width))
            
            # ALSO CHECK RECTS (often lines are drawn as thin rectangles)
            for rect in self.page_rects.get(page_num, []):
                width = float(rect.get('width', 0))
                height = float(rect.get('height', 0))
                
                # Check if vertical thin rect (separator)
                if width < 3 and height > 10:
                    lx0 = float(rect.get('x0', 0))
                    ltop = float(rect.get('top', 0))
                    lbottom = float(rect.get('bottom', 0))
                    if not hasattr(self, 'vertical_lines'):
                        self.vertical_lines = {}
                    if page_num not in self.vertical_lines:
                        self.vertical_lines[page_num] = []
                    self.vertical_lines[page_num].append((lx0, ltop, lbottom))
                
                # Check if horizontal thin rect (form underline)
                if height < 3 and width > 15:
                    rx0 = float(rect.get('x0', 0))
                    rx1 = float(rect.get('x1', 0))
                    rtop = float(rect.get('top', 0))
                    rbottom = float(rect.get('bottom', 0))
                    y_pos = (rtop + rbottom) / 2
                    
                    if width < 15: # Skip very short lines
                        continue
                    if height > 3: # Skip thick lines
                        continue
                    
                    all_lines.append((min(rx0, rx1), y_pos, max(rx0, rx1), height))
            


            inline_keywords = ['city', 'state', 'zip', 'phone', 'fax', 'email', 'county', 'country', 'date']
            split_all_lines = []
            
            for x0, y, x1, line_width in all_lines:
                # Find all label words near this line (labels are typically BEFORE/LEFT of their underline)
                labels_on_line = []
                for word in self.page_words.get(page_num, []):
                    word_text = word['text'].strip().lower().replace(':', '')
                    word_y = float(word['top'])
                    word_x0 = float(word['x0'])
                    word_x1 = float(word['x1'])
                    # v21.16: Label can be ABOVE or BELOW the line
                    # v21.35: Reduced from 15pt to 8pt to prevent row bleeding (text from row above)
                    word_bottom = float(word['bottom'])
                    y_diff_top = abs(y - word_y)     # Distance from word top to line
                    y_diff_bot = abs(y - word_bottom) # Distance from word bottom to line
                    if y_diff_top > 8 and y_diff_bot > 8:
                        continue  # Skip words too far from line
                    
                    # Check if this is a known inline label
                    if word_text not in inline_keywords:
                        continue
                    
                    # v21.20: Look for preceding word(s) to build full label (e.g., "Office Phone")
                    full_label = word['text'].strip()
                    preceding_x1 = word_x0  # Start looking from before this word
                    following_x1 = word_x1  # Track end position for following words
                    
                    # Find the word immediately before this keyword (within 20pt gap)
                    for prev_word in self.page_words.get(page_num, []):
                        prev_x0 = float(prev_word['x0'])
                        prev_x1 = float(prev_word['x1'])
                        prev_y = float(prev_word['top'])
                        
                        # Must be on same line (within 3pt vertically)
                        if abs(prev_y - word_y) > 3:
                            continue
                        
                        # Must end before our keyword starts (with small gap)
                        gap = word_x0 - prev_x1
                        if 0 <= gap < 20:  # Adjacent word
                            # Check if it's a modifier like "Office", "Mobile", "Home", "Work"
                            prev_text = prev_word['text'].strip()
                            prev_lower = prev_text.lower().replace(':', '')
                            if prev_lower not in inline_keywords:  # Not another keyword
                                full_label = prev_text + ' ' + full_label
                                preceding_x1 = prev_x0
                                break  # Only take one preceding word
                    
                    # v21.33: Also look for following words (e.g., "Email Address")
                    for next_word in self.page_words.get(page_num, []):
                        # Skip the keyword word itself
                        if next_word is word:
                            continue
                            
                        next_x0 = float(next_word['x0'])
                        next_x1 = float(next_word['x1'])
                        next_y = float(next_word['top'])
                        
                        # Must be on same line (within 3pt vertically)
                        if abs(next_y - word_y) > 3:
                            continue
                        
                        # Must start after our keyword ends (with small gap)
                        gap = next_x0 - following_x1
                        if 0 <= gap < 20:  # Adjacent word
                            next_text = next_word['text'].strip()
                            next_lower = next_text.lower().replace(':', '')
                            # Only add if it's not another keyword (extend label)
                            if next_lower not in inline_keywords:
                                full_label = full_label + ' ' + next_text
                                following_x1 = next_x1
                                break  # Only take one following word
                    
                    # Remove trailing colon if present
                    label_clean = full_label.rstrip(':')
                    
                    # label-below-line pattern (underlines with label underneath)
                    # IMPORTANT: Label should be reasonably close to the start of the line (within 50pt)
                    # to prevent "label stealing" from previous fields on the same row.
                    if word_x1 < x1 + 20 and word_x1 > x0 - 20:
                        labels_on_line.append({
                            'text': label_clean,
                            'x0': preceding_x1,  # Use the leftmost x position
                            'x1': following_x1,  # v21.33: Use the rightmost x position (calculated above)
                            'y': word_y
                        })
                
                # Sort by x position
                labels_on_line.sort(key=lambda l: l['x0'])
                
                # DEBUG: Print found labels
                if len(labels_on_line) >= 2:
                    print(f"   DEBUG INLINE: Found {len(labels_on_line)} labels on line y={y:.0f}")
                    for lbl in labels_on_line:
                        print(f"      Label '{lbl['text']}' x0={lbl['x0']:.0f} x1={lbl['x1']:.0f}")
                
                # If we found multiple labels on this line, split into segments
                if len(labels_on_line) >= 1:
                    page_width = self.page_widths.get(page_num, 612)
                    
                    # v21.38: Also add the leading segment (from line start to first inline keyword)
                    # This handles rows like "Mailing Address: _____ City: _____ State: _____ ZIP:"
                    first_label = labels_on_line[0]
                    # Gap before first inline keyword that should be processed by segment detection
                    if first_label['x0'] > x0 + 50:  # At least 50pt gap before first label
                        # Add the leading segment with None label (for segment detection)
                        leading_x1 = first_label['x0'] - 5
                        if leading_x1 - x0 > 30:
                            split_all_lines.append((x0, y, leading_x1, line_width, None))
                    
                    for i, label in enumerate(labels_on_line):
                        # v21.15: Field starts RIGHT AFTER the label word
                        # This places the field as close to the label as possible
                        # v21.18: Removed padding (+2->0) per user request for exact underlined alignment
                        # v21.28: Re-added 3pt padding to prevent overlap with label text
                        field_x0 = label['x1'] + 3
                        
                        # Field ends at the next label or at line end
                        if i < len(labels_on_line) - 1:
                            # End a few points before the next label
                            field_x1 = labels_on_line[i + 1]['x0'] - 5
                        else:
                            # Last label - field goes to line end (or reasonable width)
                            field_x1 = x1
                        
                        # Only add if width is sufficient (> 20pt)
                            split_all_lines.append((field_x0, y, field_x1, line_width, label['text'].capitalize()))
                else:
                    # No splitting needed - keep original line (with None for label)
                    split_all_lines.append((x0, y, x1, line_width, None))
            # Use split lines for processing
            # DEDUPLICATION: Presplit lines take precedence over non-split lines
            # If a non-split line (label=None) overlaps a presplit line, skip it
            presplit_positions = set()
            for line_data in split_all_lines:
                if len(line_data) == 5 and line_data[4] is not None:  # Has inline label
                    x0, y, x1 = line_data[0], line_data[1], line_data[2]
                    presplit_positions.add((round(y), round(x0), round(x1)))
                

            
            deduplicated_lines = []
            for line_data in split_all_lines:
                if len(line_data) == 5:
                    x0, y, x1, lw, label = line_data
                else:
                    x0, y, x1, lw = line_data
                    label = None
                
                if label is None:
                    # Check if this non-split line overlaps any presplit line
                    overlaps_presplit = False
                    for py, px0, px1 in presplit_positions:
                        if abs(y - py) < 5:  # Same row (within 5pt)
                            # Check X overlap
                            if not (x1 < px0 - 5 or x0 > px1 + 5):
                                overlaps_presplit = True
                                break
                    if overlaps_presplit:
                        continue  # Skip this non-split line
                
                deduplicated_lines.append(line_data)
            
            all_lines = deduplicated_lines
            
            # Process lines
            for line_data in all_lines:
                # Unpack - may have 4 or 5 elements depending on splitting
                if len(line_data) == 5:
                    x0, y, x1, line_width, inline_label = line_data
                else:
                    x0, y, x1, line_width = line_data
                    inline_label = None
                    
                    
                # Check if this line is INSIDE a header bar (not just border)
                # v35.43: Changed from border check to full containment check to avoid
                # detecting underlines of header text as fields.
                is_inside_header = False
                for bar in page_header_bars:
                    # Check if line Y is within the bar's Y range (with slight tolerance)
                    if bar['y0'] - 2 <= y <= bar['y1'] + 2:
                        # Also check X overlap to be safe
                        if not (x1 < bar['x0'] or x0 > bar['x1']):
                            is_inside_header = True
                            break
                
                if is_inside_header:
                    continue
                    
                # Check if this line is a table border
                is_table_border = any(abs(y - ty) < 3 for ty in table_row_ys)
                if is_table_border:
                    continue
                
                # v22.5: Skip if inside an invoice table (handled by _detect_underscore_fields)
                if self._is_inside_invoice_table(page_num, x0, y-5, x1, y+5):
                    continue
                
                # v21.35: MOVED overlap check into the segment loop (Phase 3)

                
                # Find label - look to the left, above, or below
                # Some forms have "label-below-line" pattern (underlines with label underneath)
                # IMPORTANT: Prefer colon-ending labels (they're intentional field labels)
                # v21.4: _find_nearby_text now returns (text, x1) tuple
                
                # Some forms have "label-below-line" pattern (underlines with label underneath)
                # IMPORTANT: Prefer colon-ending labels (they're intentional field labels)
                # v21.4: _find_nearby_text now returns (text, x1) tuple
                label_left_res = self._find_nearby_text(page_num, x0, y, 
                                                   direction='left', max_dist=150)
                label_above_res = self._find_nearby_text(page_num, x0, y,
                                                    direction='above', max_dist=25)
                label_below_res = self._find_nearby_text(page_num, x0, y,
                                                    direction='below', max_dist=10)
                
                # Label selection logic v21.23: Compare scores from all directions
                # direction_res returns (text, is_colon, score, x0, x1)
                candidates = []
                if label_left_res: candidates.append(('left', label_left_res))
                if label_above_res: candidates.append(('above', label_above_res))
                if label_below_res: candidates.append(('below', label_below_res))
                
                if inline_label:
                    label = inline_label
                elif candidates:
                    # Sort candidates by score (lowest is best)
                    # Tie-breaker: naturally sorted by order of appearance (left, then above, then below)
                    
                    # v21.29: Give a priority bonus to 'left' if it's within a reasonable distance
                    # to prevent 'below' or 'above' from stealing the label if they have colons
                    scored_candidates = []
                    for d, res in candidates:
                        s = res[2]
                        if d == 'left' and res[2] < 100:
                            s -= 50
                        scored_candidates.append((s, d, res))
                    
                    scored_candidates.sort(key=lambda x: x[0])
                    best_score, best_dir, best_res = scored_candidates[0]
                    label = best_res[0]
                    # DEBUG: Log label selection
                    if DEBUG_LOGS and page_num == DEBUG_PAGE:
                        print(f"      DEBUG y={y:.1f} candidate_count={len(candidates)}: selected='{label}' via {best_dir} (score {best_score:.1f})")
                        for d, res in candidates:
                            print(f"         - {d}: '{res[0]}' score={res[2]:.1f}")
                if label:
                    # v22.46: Skip instruction labels in Pass 4B
                    instruction_kills = ['service territory', 'qualifying measure', 'consumer promotion', 'meeting frequency', 'participate', 'anticipate', 'percentage', 'please estimate', 'contractors purchasing', 'territory', 'additional documentation', 'documentation to submit']
                    if any(k in label.lower() for k in instruction_kills):
                        continue
                    
                    # v35.20: Skip labels that look like sentences or document content (not field labels)
                    # - Labels with more than 5 words are likely sentences
                    # - Labels starting with articles are likely descriptive text
                    # - Labels containing "and" surrounded by words are compound descriptions
                    label_lower = label.lower().strip()
                    word_count = len(label.split())
                    if word_count > 5:
                        continue  # Too many words - likely a sentence
                    if label_lower.startswith(('the ', 'a ', 'an ', 'for ', 'to ', 'by ', 'in ', 'on ')):
                        continue  # Starts like a sentence
                    if ' and ' in label_lower and word_count > 3:
                        continue  # Compound phrase like "Before Retrofit and After Retrofit"
                        
                    base_name = self._clean_field_name(label)
                else:
                    base_name = "Line_Field"
                
                field_name = self._generate_unique_name(base_name, page_num)
                field_format, format_opts = self._detect_field_format(None, label)
                
                # Position field so BOTTOM sits ON the line
                # Field extends ABOVE the line (height defined by setting)
                # In pdfplumber coords: y increases downward, so field is ABOVE line at y-height to y
                field_height = int(self._get_setting('field_height', 14))
                
                # v21.17: Re-enable pre-split bypass with proper handling
                # If inline_label is set, use the pre-calculated x0/x1 from inline splitting.
                # The inline splitting already set x0 = label_x1 + 2 and x1 = next_label_x0 - 5
                if inline_label:
                    # DEBUG: Print format detection for pre-split fields
                    
                    # Create field directly using pre-calculated boundaries
                    new_field = {
                        'page': page_num,
                        'x0': x0,  # Already positioned by inline splitting
                        'y0': y - field_height,
                        'x1': x1,
                        'y1': y,
                        'name': field_name,
                        'label': label,
                        'tooltip': label,
                        'format': field_format,
                        'format_options': format_opts,
                        'source': 'form_line_presplit'
                    }
                    self.text_fields.append(new_field)
                    continue  # Skip to next line segment
                
                # Instead of ONE field per line, we iterate horizontally.
                # This handles "Company Name ______" (by skipping intermediate text)
                # AND "City ______ State ______" (by extracting two fields).
                
                # Get line words (cached)
                words = self.page_words.get(page_num, [])
                line_words = []
                for word in words:
                    # Check 'bottom' (text ON line) OR 'top' (text BELOW line).
                    # 10pt tolerance balances: capturing sub-labels vs avoiding adjacent rows.
                    w_bot = float(word['bottom'])
                    w_top = float(word['top'])
                    
                    # Vertical check only - x will be handled by consume/collision logic
                    # v21.34: Exclude text strictly below the line (next row) to prevent bridging
                    if w_top > y + 5:
                        continue
                        
                    # v21.35: Reduced from 15pt to 12pt (relaxed from 8pt to catch Page 9 labels)
                    if abs(w_bot - y) < 12 or abs(w_top - y) < 12:
                        logging.debug(f"  Consuming word '{word['text']}' on line y={y}")
                        line_words.append(word)
                line_words.sort(key=lambda w: float(w['x0']))
                
                logging.debug(f"Line y={y}: Found {len(line_words)} overlapping words with 8pt tolerance. Checking collisions...")
                cursor = x0 # Start at beginning of line
                
                # SAFETY: prevent infinite loops
                iter_count = 0
                while cursor < x1 - 5 and iter_count < 10:
                    iter_count += 1
                    consumed_words_for_seg = []
                    consumed_words_first_y = None  # Track vertical position of first consumed word
                    # We advance 'cursor' past any text that overlaps it, or is close enough to bridge.
                    # This clears "Company...Name" effectively.
                    
                    current_push_pos = cursor
                    
                    for word in line_words:
                        wx0 = float(word['x0'])
                        wx1 = float(word['x1'])
                        wy = float(word['top'])
                        
                        # Optimization: if word is completely behind cursor, skip
                        if wx1 <= current_push_pos:
                            continue
                            
                        # Gap check
                        gap = wx0 - current_push_pos
                        
                        # If overlap or gap is small (< 30pt), consume it
                        # Increased from 20 to 30 to better handle "Email   Address"
                        if gap < 30:
                            # Only consume if it looks like content (not underscores)
                            text = word['text'].strip()
                            if '_' in text and len(text) > 1:
                                # Hit an underscore block! Use this as field start.
                                # Don't consume it.
                                break
                            
                            # v21.32: Only add to label if on same visual row (within 3pt)
                            # This prevents cross-row merging like "Company Name" + "Street Address"
                            if consumed_words_first_y is None:
                                consumed_words_first_y = wy
                            
                            if abs(wy - consumed_words_first_y) <= 3:
                                consumed_words_for_seg.append(text)
                            
                            # Always advance cursor past this word regardless of row
                            current_push_pos = max(current_push_pos, wx1)
                            
                        else:
                            # Gap is large -> Start of field content
                            break
                            
                    # v21.18: Removed padding (+2->0) per user request
                    # v21.28: Re-added 3pt padding to prevent overlap with label text
                    field_x0 = current_push_pos + 3 # Start exactly at label end or line start
                    
                    # If we pushed past the end of the line, stop
                    if field_x0 >= x1:
                        break
                        
                    # --- Phase 2: Find Collision (End of Field) ---
                    # Scan for the NEXT text/label on the line.
                    
                    collision_x = None
                    collision_end_x = None
                    
                    for word in line_words:
                        wx0 = float(word['x0'])
                        wx1 = float(word['x1'])
                        
                        # Must be strictly AFTER our start
                        if wx0 > field_x0:
                            # Found potential collision
                            text = word['text'].strip()
                            
                            # v21.12: Ignore Hints/Noise in Collision Detection
                            # Don't split fields for hint text like "(please print)" or "(optional)"
                            if text.startswith('(') or text.endswith(')'):
                                continue
                            if text.lower() in ['print', 'sign', 'here', 'please', 'optional', 'required', 'if', 'applicable']:
                                continue
                            if all(c in '.,:;-\'"' for c in text): # Ignore punctuation
                                continue

                            # Check if it's a label (no underscores)
                            # v22.6: Any text without underscores is a potential collision
                            # unless it's very short (like $ or @ which are field content)
                            if '_' not in text and len(text) > 1:
                                collision_x = wx0
                                collision_end_x = wx1
                                break
                                
                    if collision_x:
                        # Field ends before this label
                        field_x1 = collision_x - 5
                        # v21.27: Set next cursor to START of collision so next iteration can consume it as a label
                        next_cursor = collision_x 
                    else:
                        # No collision, field goes to end of line
                        field_x1 = x1
                        next_cursor = x1 # Will terminate loop
                        
                    # --- Phase 3: Add Field (if valid) ---
                    # v21.33: Lowered from 20pt to 15pt to capture short lines like "# dwelling units"
                    field_width = field_x1 - field_x0
                         
                    # v21.35: Check for overlap with existing fields at the SEGMENT level
                    # Use tighter y-range (y-5 to y+5) to avoid catching separate rows
                    # v21.38: Instead of skipping if field exists, REPLACE narrower fields with wider new ones
                    existing_idx = None
                    existing_field = None
                    for i, f in enumerate(self.text_fields):
                        if f['page'] != page_num:
                            continue
                        # Check for overlap with 1pt tolerance
                        TOLERANCE = 1.0
                        if (f['x0'] < field_x1 - TOLERANCE and f['x1'] > field_x0 + TOLERANCE and
                            f['y0'] < y + 5 - TOLERANCE and f['y1'] > y - 5 + TOLERANCE):
                            existing_idx = i
                            existing_field = f
                            break
                    
                    if existing_field:
                        existing_width = existing_field['x1'] - existing_field['x0']
                        new_width = field_x1 - field_x0
                        
                        # v21.51: PROTECT underscore-based fields from replacement
                        # These fields have precise boundaries from the underscore text pattern
                        # and should NOT be replaced by wider form_line_segment fields
                        existing_source = existing_field.get('source', '')
                        protected_sources = ['embedded_underscore', 'general_underscore', 
                                           'label_below_underscore', 'underscore', 'split_field']
                        if existing_source in protected_sources:
                            # Skip this segment - underscore field has correct boundaries
                            cursor = field_x1 + 5
                            continue
                        
                        if new_width > existing_width + 10:
                            # New field is significantly wider - replace the existing one
                            # Remove the narrower existing field and continue to create the wider one
                            self.text_fields.pop(existing_idx)
                            # Continue to create the new wider field
                        else:
                            # Existing field is wider or similar - skip this segment
                            cursor = field_x1 + 5
                            continue
                        
                    # v22.96: Relaxed from 20 to 12 to catch short fields like "Electricity provider"
                    if field_width > 12:
                        # v21.26: Determine label for this segment
                        seg_label = ' '.join(consumed_words_for_seg).strip().rstrip(':')
                        
                        # Fallback 1: Use inline label if available (presplit)
                        if not seg_label and inline_label:
                            seg_label = inline_label
                            
                        # Fallback 2: Look LEFT for label (for orphan lines like "Age of building")
                        if not seg_label:
                            # Search line_words for text ending just before the field
                            left_candidates = []
                            for word in line_words:
                                wx1 = float(word['x1'])
                                # Word must be strictly to the left of field start
                                # Check for reasonable proximity (within 150pt)
                                if wx1 < field_x0 and (field_x0 - wx1) < 150:
                                    left_candidates.append(word)
                            
                            if left_candidates:
                                # Sort by position so we join them in order
                                left_candidates.sort(key=lambda w: float(w['x0']))
                                seg_label = ' '.join([w['text'] for w in left_candidates]).strip()
                        
                        if seg_label:
                            # v22.46: Instruction Kill
                            instruction_kills = ['service territory', 'qualifying measure', 'consumer promotion', 'meeting frequency', 'participate', 'anticipate', 'percentage', 'please estimate', 'contractors purchasing', 'territory', 'additional documentation', 'documentation to submit', 'please list', 'please detail', 'interested in being']
                            if any(k in seg_label.lower() for k in instruction_kills):
                                cursor = field_x1 + 5
                                continue
                                
                        base_name_seg = self._clean_field_name(seg_label) if seg_label else "Line_Field"
                        unique_name_seg = self._generate_unique_name(base_name_seg, page_num)
                        
                        self.text_fields.append({
                            'page': page_num,
                            'x0': field_x0,
                            'y0': y - field_height,
                            'x1': field_x1,
                            'y1': y,
                            'name': unique_name_seg,
                            'label': seg_label,
                            'type': 'text',
                            'source': 'form_line_segment'
                        })
                        
                    # --- Phase 4: Advance ---
                    cursor = next_cursor
                    # Correctly closing loop and function scope logic if needed
                    pass 
                line_fields += 1
        
        if line_fields > 0:
            print(f"   Found {line_fields} form line fields")
    
    # ========== EMPTY BOX DETECTION ==========
    
    def _detect_empty_boxes(self):
        """
        Detect large empty rectangular boxes that should become text areas.
        
        These are boxes formed by horizontal and vertical lines that:
        1. Have minimum dimensions (>100pt x 30pt) to distinguish from table cells
        2. Are mostly empty (no significant text content)
        3. Often appear under headers like "Customer's additional needs beyond scope"
        """
        # Read thresholds from settings (with sensible defaults)
        min_box_width = self.settings.get('min_empty_box_width', 100)
        min_box_height = self.settings.get('min_empty_box_height', 15) # Reduced from 30 for Distributor form

        
        empty_box_fields = 0
        header_bars = self._detect_header_bars()  # Ensure generic header bars are loaded
        
        for page_num in range(self.page_count):
            # v22.45/30: Enable for Page 0 to catch Section C narrative boxes
            page = self.pdf.pages[page_num]
            # Get all lines on the page
            h_lines = []  # Horizontal lines
            v_lines = []  # Vertical lines
            
            for line in self.page_lines.get(page_num, []):
                x0, y0 = float(line['x0']), float(line['top'])
                x1, y1 = float(line['x1']), float(line['bottom'])
                
                # Horizontal lines: same Y (within 1pt tolerance)
                if abs(y0 - y1) < 1 and abs(x1 - x0) > 50:
                    h_lines.append({'y': (y0 + y1) / 2, 'x0': min(x0, x1), 'x1': max(x0, x1)})
                
                # Vertical lines: same X (within 1pt tolerance)
                # v22.22: Lowered from 20 to 10 to catch small question boxes
                elif abs(x0 - x1) < 1 and abs(y1 - y0) > 10:
                    v_lines.append({'x': (x0 + x1) / 2, 'y0': min(y0, y1), 'y1': max(y0, y1)})

            # v22.22: Merge adjacent horizontal segments on the same Y
            if h_lines:
                h_lines.sort(key=lambda l: (l['y'], l['x0']))
                merged_h = []
                if h_lines:
                    curr = h_lines[0].copy()
                    for i in range(1, len(h_lines)):
                        nxt = h_lines[i]
                        # Merge if same Y and adjacent or small gap
                        if abs(nxt['y'] - curr['y']) < 0.5 and nxt['x0'] <= curr['x1'] + 5:
                            curr['x1'] = max(curr['x1'], nxt['x1'])
                        else:
                            merged_h.append(curr)
                            curr = nxt.copy()
                    merged_h.append(curr)
                h_lines = merged_h
                print(f"   [DEBUG_Boxes] Page {page_num} Merged {len(h_lines)} horizontal lines")


            
            # Also check rect edges and curves
            candidate_shapes = self.page_rects.get(page_num, []) + self.page_curves.get(page_num, [])
            for rect in candidate_shapes:
                x0, y0 = float(rect['x0']), float(rect['top'])
                x1, y1 = float(rect['x1']), float(rect['bottom'])

                # v36.1: Skip if in the top of the page (likely header background)
                if y0 < 90:
                    continue
                
                # Skip very small rectangles (min_box_height check happens later)
                if (x1 - x0) < 50 or (y1 - y0) < 12:
                    continue

                
                # Skip rectangles that extend beyond page boundaries (background elements)
                page_width = page.width
                page_height = page.height
                if x0 < 0 or x1 > page_width or y0 < 0 or y1 > page_height:
                    continue
                
                # A large rect defines a box directly
                width = x1 - x0
                height = y1 - y0
                
                # Skip giant boxes that cover most of the page (likely background elements)
                max_box_width = page_width * 0.95  # Skip if >95% of page width
                max_box_height = page_height * 0.80  # Skip if >80% of page height
                if width > max_box_width or height > max_box_height:
                    continue
                
                if width > 100:
                    # print(f"   [DEBUG_Ex] Page 0 Shape: w={width:.1f} h={height:.1f} x0={x0:.1f} y0={y0:.1f}")
                    pass

                if width > min_box_width and height > min_box_height:
                    
                    # v22.5: Skip if inside an invoice table
                    if self._is_inside_invoice_table(page_num, x0, y0, x1, y1):
                        continue


                # Check if mostly empty - look for text inside
                words = self.page_words.get(page_num, [])
                text_inside_words = []
                for w in words:
                    wx0, wy0 = float(w['x0']), float(w['top'])
                    wx1, wy1 = float(w['x1']), float(w['bottom'])
                    # Text is inside if center is within box
                    if x0 < (wx0 + wx1)/2 < x1 and y0 < (wy0 + wy1)/2 < y1:
                        text_inside_words.append(w)
                
                if width > 100:
                    # print(f"      -> Text inside: {len(text_inside_words)} words")
                    pass
                    if len(text_inside_words) > 50:
                        print(f"      -> Rejected: Too much text")

                
                # Check for label inside the box (at the top)
                internal_label = None
                field_y0 = y0
                label_right = None
                
                if text_inside_words:
                    # Sort words by top
                    text_inside_words.sort(key=lambda w: float(w['top']))
                    top_text_words = []
                    last_y = float(text_inside_words[0]['top'])
                    
                    for w in text_inside_words:
                        if float(w['top']) < y0 + height * 0.3: # Top 30% of box
                            top_text_words.append(w)
                            field_y0 = max(field_y0, float(w['bottom']))
                    
                    if top_text_words:
                        internal_label = ' '.join(w['text'] for w in top_text_words).strip()
                        # Clean up trailing colons if it's an internal label
                        internal_label = internal_label.rstrip(':').strip()
                        try:
                            label_right = max(float(w['x1']) for w in top_text_words)
                        except:
                            label_right = None
                
                total_text_str = ' '.join(w['text'] for w in text_inside_words).strip()
                
                # If mostly empty OR contains just an internal label, it's a text area
                # v22.6: Allow more text if it looks like an internal label
                is_mostly_empty = len(total_text_str) < 20
                room_below = (y1 - field_y0) if internal_label else (y1 - y0)
                has_room_below = room_below > (height * 0.35)
                is_internal_label = internal_label and (
                    (len(total_text_str) < 150 and (':' in total_text_str or len(internal_label) > len(total_text_str) * 0.7))
                    or (has_room_below and len(internal_label) > 0)
                )
                
                if is_mostly_empty or is_internal_label:
                    # v21.40: For large boxes, replace small overlapping fields instead of skipping
                    box_area = width * height
                    overlapping_fields = []
                    for i, field in enumerate(self.text_fields):
                        if field['page'] != page_num:
                            continue
                        # v33.82: DO NOT swallow embedded_underscore fields (they are critical user input)
                        # v22.96: DO NOT swallow checkboxes (vector, character, visual_in_cell, table_cell)
                        checkbox_sources = ['vector', 'character', 'visual_in_cell', 'table_cell', 'tiny_cell', 'table_col']
                        if field.get('source') in checkbox_sources or field.get('source') == 'embedded_underscore':
                            continue
                        TOLERANCE = 1.0
                        if (field['x0'] < x1 - TOLERANCE and field['x1'] > x0 + TOLERANCE and
                            field['y0'] < y1 - TOLERANCE and field['y1'] > y0 + TOLERANCE):
                            overlapping_fields.append((i, field))
                    
                    if overlapping_fields:
                        large_field_exists = False
                        indices_to_remove = []
                        for idx, field in overlapping_fields:
                            field_area = (field['x1'] - field['x0']) * (field['y1'] - field['y0'])
                            # v22.25: Drastically lower threshold to protect ANY field from being swallowed by a section box
                            if field_area > box_area * 0.01:

                                large_field_exists = True
                                break
                            else:
                                indices_to_remove.append(idx)
                        
                        if large_field_exists:
                            continue
                        else:
                            for idx in sorted(indices_to_remove, reverse=True):
                                self.text_fields.pop(idx)



                    # Find label (external or internal)


                    if internal_label:
                        label_text = internal_label
                        base_name = self._clean_field_name(label_text)
                    else:
                        # v22.22: Increased max_dist for Page 1 large blocks
                        # v22.27: Explicitly use rect coords (x0, y0) in the rect loop
                        label_result = self._find_nearby_text(page_num, x0, y0,
                                                      direction='above', max_dist=60)



                        if label_result:
                            label_text = label_result[0] if isinstance(label_result, tuple) else label_result
                            base_name = self._clean_field_name(label_text)
                        else:
                            label_text = "Notes"
                            base_name = "Notes"
                    
                    # v22.22: Skip section headers and logo text
                    bad_labels = ["A.", "B.", "C.", "D.", "A ", "B ", "C ", "D ", "Program", "Application", "Date Submitted", 
                                  "Information", "General", "Type", "Rating", "Quantity", "Sales", "Projections", "Page", "Totals"]
                    lt_strip = label_text.strip()
                    if any(lt_strip.startswith(bl) for bl in bad_labels) or lt_strip in ["A", "B", "C", "D"]:
                        continue

                    # SMART FIX 3B: GENERIC Header Detection for Empty Boxes
                    
                    # 1. Skip if overlapping a detected header bar (Colored Box)
                    if self._is_in_header_bar(x0, y0, x1, y1, header_bars.get(page_num, [])):
                        print(f"      -> Skipping empty box '{label_text}' - Inside Header Bar")
                        continue

                    # 2. GENERIC Text Analysis (Content & Geometry)
                    # Use pdfplumber to get exact text bounds in this box to distinguish labels from fields
                    # (This generically skips 'Propane', 'Equipment Requirements', and buttons)
                    try:
                        page_obj = self.pdf.pages[page_num]
                        # Ensure crop box is valid
                        if x1 > x0 and y1 > y0:
                            words_in_box = page_obj.within_bbox((x0, y0, x1, y1)).extract_words()
                            
                            if words_in_box:
                                # Calc text bounds
                                w_top = min(w['top'] for w in words_in_box)
                                w_bot = max(w['bottom'] for w in words_in_box)
                                
                                txt_h = w_bot - w_top
                                box_h = y1 - y0
                                
                                # A. Vertical Centering (Label/Button)
                                # Labels like 'Propane' are often vertically centered in small boxes
                                top_m = w_top - y0
                                bot_m = y1 - w_bot
                                
                                # v35.18: Only apply vertically-centered skip to cells that CONTAIN their own text
                                # NOT to empty data cells that just happen to have nearby header text
                                # If label came from above (_find_nearby_text), this cell is a DATA cell - don't skip
                                is_label_from_above = not internal_label and label_text
                                
                                if box_h < 50 and abs(top_m - bot_m) < 8 and not is_label_from_above:
                                     print(f"      -> Skipping empty box '{label_text}' - Vertically Centered Label")
                                     continue
                                     
                                # B. High Text Fill (Header/Label)
                                # If text occupies most of the vertical space -> Header
                                if txt_h / box_h > 0.6 and not is_label_from_above:
                                     print(f"      -> Skipping empty box '{label_text}' - High Vertical Fill")
                                     continue
                                     
                                # C. Long Text Header
                                # If text is long and no colon -> Instruction/Header
                                total_text = " ".join(w['text'] for w in words_in_box)
                                if len(total_text) > 20 and ':' not in total_text:
                                     print(f"      -> Skipping empty box '{label_text}' - Long Header Text")
                                     continue
                    except Exception as e:
                        # Fallback if page access fails (unlikely)
                        print(f"   [GenericCheck] Error checking box content: {e}")
                        pass


                    if len(lt_strip) < 3: # Skip noise
                        continue
                        
                    field_name = self._generate_unique_name(base_name, page_num)



                    
                    # Check if this is an IMAGE box
                    # v35.0: Detect BOTH embedded images AND vector placeholder icons
                    has_placeholder_image = self._cell_has_placeholder_image(page_num, x0, y0, x1, y1)
                    
                    ratio = width / height if height > 0 else 0
                    is_image_box = (has_placeholder_image and
                                   width > 80 and height > 80 and 
                                   width < 600 and height < 500 and
                                   ratio < 3.5 and ratio > 0.3)
                    
                    # Heuristic: skip full-width section headers or long single-line instructions
                    # - Short uppercase headers (e.g., 'CUSTOMER INFORMATION')
                    # - Very long short-height paragraph lines (e.g., T&C single-line banner)
                    if width > page_width * 0.8 and label_text:
                        words = label_text.split()
                        is_short_upper_header = (height <= 24 and label_text == label_text.upper() and len(words) <= 6)
                        is_long_paragraph_banner = (len(words) > 15 and height <= 60)
                        if is_short_upper_header or is_long_paragraph_banner:
                            continue

                    # Create large text area filling the box (or remaining part of box)
                    # v35.40: BEFORE creating field, check if box should be split by interior vertical lines
                    boxes_to_create = [(x0, y0, x1, y1)]  # Start with full box
                    if width > 100:  # Only split large boxes
                        # Look for vertical lines that divide this box
                        interior_v_lines = []
                        for v in v_lines:
                            if x0 + 10 < v['x'] < x1 - 10:  # Interior vertical line
                                # Check if line spans most of the box height
                                v_overlap = min(v['y1'], y1) - max(v['y0'], field_y0 if internal_label else y0)
                                box_height = y1 - (field_y0 if internal_label else y0)
                                if v_overlap > box_height * 0.7:  # Line spans >70% of box
                                    interior_v_lines.append(v['x'])
                        
                        if interior_v_lines:
                            # Split the box at each interior line
                            interior_v_lines.sort()
                            boxes_to_create = []
                            prev_x = x0
                            for v_x in interior_v_lines:
                                boxes_to_create.append((prev_x, y0, v_x, y1))
                                prev_x = v_x
                            boxes_to_create.append((prev_x, y0, x1, y1))
                            print(f"   ✂️  Split empty box into {len(boxes_to_create)} parts at x={interior_v_lines}")
                    
                    # Create field(s) for each box segment
                    for box_idx, (bx0, by0, bx1, by1) in enumerate(boxes_to_create):
                        box_width = bx1 - bx0
                        # Skip tiny segments
                        if box_width < 30:
                            continue
                            
                        # Generate unique name for each segment
                        if len(boxes_to_create) > 1:
                            segment_name = f"{field_name}_{box_idx + 1}"
                        else:
                            segment_name = field_name
                        
                        self.text_fields.append({
                            'page': page_num,
                            'x0': bx0 + 3,
                            'y0': field_y0 + 3 if internal_label else by0 + 3,
                            'x1': bx1 - 3,
                            'y1': by1 - 3,
                            'name': segment_name,
                            'label': label_text,
                            'format': 'image' if is_image_box else 'text',
                            'format_opts': {'multiline': True},
                            'source': 'empty_box_lines',
                            'is_image_box': is_image_box
                        })
                        empty_box_fields += 1
                    
                    if boxes_to_create:
                        box_type = 'Image box' if is_image_box else 'Empty box'
                        if len(boxes_to_create) > 1:
                            print(f"   Page {page_num + 1}: {box_type} '{field_name}' split into {len(boxes_to_create)} fields ({width:.0f}x{height:.0f}pt) label='{label_text}'")
                        else:
                            print(f"   Page {page_num + 1}: {box_type} '{field_name}' ({width:.0f}x{height:.0f}pt) label='{label_text}'")
            
            # Also try to find boxes formed by intersecting horizontal and vertical lines
            # Look for pairs of horizontal lines with vertical lines connecting them
            for h1 in h_lines:
                for h2 in h_lines:
                    if h2['y'] <= h1['y']:
                        continue
                    
                    # Check if these two horizontal lines could form top/bottom of a box
                    height = h2['y'] - h1['y']
                    # v21.39: Increased max height from 200 to 400 to capture tall boxes like Before/After Retrofit (288pt)
                    if height < min_box_height or height > 400:  # Too small or too large
                        continue
                    
                    # Find overlapping X range
                    x_left = max(h1['x0'], h2['x0'])
                    x_right = min(h1['x1'], h2['x1'])
                    width = x_right - x_left
                    
                    if width < min_box_width:  # Too narrow
                        continue
                    
                    # Check for vertical lines at left and right edges
                    has_left = any(
                        abs(v['x'] - x_left) < 5 and
                        v['y0'] <= h1['y'] + 5 and
                        v['y1'] >= h2['y'] - 5
                        for v in v_lines
                    )
                    has_right = any(
                        abs(v['x'] - x_right) < 5 and
                        v['y0'] <= h1['y'] + 5 and
                        v['y1'] >= h2['y'] - 5
                        for v in v_lines
                    )
                    
                    # v22.22: Relaxed vertical line check for wide boxes (large blocks)
                    # For wide blocks, often vertical lines are missing or fragmented
                    # v22.23 (Generic): Full relax for large blocks if they look like questions (generic)
                    is_complete_box = (has_left and has_right)
                    is_large_block = (width > 400 and (has_left or has_right or height > 40))
                    
                    if is_complete_box or is_large_block:



                        # We have a complete box
                        box_x0, box_y0 = x_left, h1['y']
                        box_x1, box_y1 = x_right, h2['y']
                        
                        # v22.5: Skip if inside an invoice table
                        if self._is_inside_invoice_table(page_num, box_x0, box_y0, box_x1, box_y1):
                            continue
                        
                        # Check if mostly empty
                        words = self.page_words.get(page_num, [])
                        text_inside = []
                        for w in words:
                            wx0, wy0 = float(w['x0']), float(w['top'])
                            wx1, wy1 = float(w['x1']), float(w['bottom'])
                            if box_x0 < (wx0 + wx1)/2 < box_x1 and box_y0 < (wy0 + wy1)/2 < box_y1:
                                text_inside.append(w['text'])
                        
                        total_text_str = ' '.join(text_inside).strip()
                        box_height = box_y1 - box_y0
                        
                        # v22.22: Allow more text in large blocks (instructions etc)
                        max_text_len = 50 if width > 300 else 20
                        is_mostly_empty = len(total_text_str) < max_text_len

                        internal_label = None
                        field_y0 = box_y0
                        
                        if text_inside:
                            # Re-collect words as dictionaries with positions
                            text_inside_words = []
                            for w in words:
                                wx0, wy0 = float(w['x0']), float(w['top'])
                                wx1, wy1 = float(w['x1']), float(w['bottom'])
                                if box_x0 < (wx0 + wx1)/2 < box_x1 and box_y0 < (wy0 + wy1)/2 < box_y1:
                                    text_inside_words.append(w)
                            
                            text_inside_words.sort(key=lambda w: float(w['top']))
                            top_text_words = []
                            for w in text_inside_words:
                                if float(w['top']) < box_y0 + box_height * 0.3:
                                    top_text_words.append(w)
                                    field_y0 = max(field_y0, float(w['bottom']))
                            
                            if top_text_words:
                                internal_label = ' '.join(w['text'] for w in top_text_words).strip()
                                internal_label = internal_label.rstrip(':').strip()

                        # v22.6: Allow more text if it looks like an internal label
                        is_mostly_empty = len(total_text_str) < 20
                        is_internal_label = internal_label and len(total_text_str) < 150 and (':' in total_text_str or len(internal_label) > len(total_text_str) * 0.7)
                        
                        if is_mostly_empty or is_internal_label:
                            # v21.40: For large empty boxes, replace small overlapping fields
                            box_area = (box_x1 - box_x0) * box_height
                            
                            # Check for overlapping fields and decide whether to skip or replace
                            overlapping_fields = []
                            for i, field in enumerate(self.text_fields):
                                if field['page'] != page_num:
                                    continue
                                TOLERANCE = 1.0
                                if (field['x0'] < box_x1 - TOLERANCE and field['x1'] > box_x0 + TOLERANCE and
                                    field['y0'] < box_y1 - TOLERANCE and field['y1'] > box_y0 + TOLERANCE):
                                    overlapping_fields.append((i, field))
                            
                            if overlapping_fields:
                                large_field_exists = False
                                indices_to_remove = []
                                
                                for idx, field in overlapping_fields:
                                    field_area = (field['x1'] - field['x0']) * (field['y1'] - field['y0'])
                                    if field_area > box_area * 0.01:
                                        large_field_exists = True
                                        break
                                    else:
                                        indices_to_remove.append(idx)
                                
                                if large_field_exists:
                                    continue
                                else:
                                    for idx in sorted(indices_to_remove, reverse=True):
                                        self.text_fields.pop(idx)
                            
                            # Find label
                            if internal_label:
                                label_text = internal_label
                                base_name = self._clean_field_name(label_text)
                            else:
                                res = self._find_nearby_text(page_num, box_x0, box_y0,
                                                              direction='above', max_dist=40)
                                label_text = res[0] if res else "Notes"
                                base_name = self._clean_field_name(label_text)
                            
                            field_name = self._generate_unique_name(base_name, page_num)
                            
                            # Check if this is an IMAGE box (photo upload area)
                            # v33.77: Only boxes with embedded placeholder images are image boxes
                            has_placeholder_image = False
                            try:
                                box_objs = page.within_bbox((box_x0, box_y0, box_x1, box_y1)).objects
                                if box_objs.get('image', []):
                                    has_placeholder_image = True
                            except:
                                pass
                            
                            ratio = width / height if height > 0 else 0
                            is_image_box = (has_placeholder_image and
                                           width > 80 and height > 80 and 
                                           width < 600 and height < 500 and
                                           ratio < 3.5 and ratio > 0.3)
                            
                            # v35.40: BEFORE creating field, check if box should be split by interior vertical lines
                            boxes_to_create = [(box_x0, box_y0, box_x1, box_y1)]  # Start with full box
                            if width > 100:  # Only split large boxes
                                # Look for vertical lines that divide this box
                                interior_v_lines = []
                                for v in v_lines:
                                    if box_x0 + 10 < v['x'] < box_x1 - 10:  # Interior vertical line
                                        # Check if line spans most of the box height
                                        v_overlap = min(v['y1'], box_y1) - max(v['y0'], field_y0 if internal_label else box_y0)
                                        box_height_check = box_y1 - (field_y0 if internal_label else box_y0)
                                        if v_overlap > box_height_check * 0.7:  # Line spans >70% of box
                                            interior_v_lines.append(v['x'])
                                
                                if interior_v_lines:
                                    # Split the box at each interior line
                                    interior_v_lines.sort()
                                    boxes_to_create = []
                                    prev_x = box_x0
                                    for v_x in interior_v_lines:
                                        boxes_to_create.append((prev_x, box_y0, v_x, box_y1))
                                        prev_x = v_x
                                    boxes_to_create.append((prev_x, box_y0, box_x1, box_y1))
                                    print(f"   \u2702\ufe0f  Split empty box from lines into {len(boxes_to_create)} parts at x={interior_v_lines}")
                            
                            # Create field(s) for each box segment
                            for box_idx, (bx0, by0, bx1, by1) in enumerate(boxes_to_create):
                                box_width = bx1 - bx0
                                # Skip tiny segments
                                if box_width < 30:
                                    continue
                                    
                                # Generate unique name for each segment
                                if len(boxes_to_create) > 1:
                                    segment_name = f"{field_name}_{box_idx + 1}"
                                else:
                                    segment_name = field_name
                                
                                self.text_fields.append({
                                    'page': page_num,
                                    'x0': bx0 + 3,
                                    'y0': field_y0 + 3 if internal_label else by0 + 3,
                                    'x1': bx1 - 3,
                                    'y1': by1 - 3,
                                    'name': segment_name,
                                    'label': label_text,
                                    'format': 'image' if is_image_box else 'text',
                                    'format_opts': {'multiline': True},
                                    'source': 'empty_box_lines',
                                    'is_image_box': is_image_box
                                })
                                empty_box_fields += 1
                            
                            if boxes_to_create:
                                box_type = 'Image box' if is_image_box else 'Empty box'
                                if len(boxes_to_create) > 1:
                                    print(f"   Page {page_num + 1}: {box_type} from lines '{field_name}' split into {len(boxes_to_create)} fields ({width:.0f}x{height:.0f}pt)")
                                else:
                                    print(f"   Page {page_num + 1}: {box_type} from lines '{field_name}' ({width:.0f}x{height:.0f}pt)")
        
        if empty_box_fields > 0:
            print(f"   Total: {empty_box_fields} empty box fields")
    
    def _discovery_pass_missing_labeled_fields(self):
        """
        v36.6: Final "Self-Review" scan to find labels ending in ":" 
        that do not have an associated field yet.
        """
        discovered_count = 0
        for page_num in range(self.page_count):
            words = self.page_words.get(page_num, [])
            lines = self.page_lines.get(page_num, [])
            
            # Group words by line for context
            words_by_row = defaultdict(list)
            for w in words:
                ry = round(float(w['top']) / 3) * 3
                words_by_row[ry].append(w)
            
            for word in words:
                w_txt = word['text']
                if not w_txt.endswith(':'): continue
                if len(w_txt) < 3: continue
                
                wx0, wy0, wx1, wy1 = float(word['x0']), float(word['top']), float(word['x1']), float(word['bottom'])
                
                # Close proximity check
                already_has_field = False
                for f in self.text_fields:
                    if f['page'] == page_num:
                        # Field starts just after the label?
                        if f['x0'] >= wx1 - 5 and f['x0'] < wx1 + 100 and abs(f['y0'] - wy0) < 15:
                            already_has_field = True; break
                        # Field covers the potential gap area?
                        if f['x0'] < wx1 + 20 and f['x1'] > wx1 + 10 and abs((f['y0']+f['y1'])/2 - (wy0+wy1)/2) < 10:
                            already_has_field = True; break
                
                if already_has_field: continue
                
                # Check checkboxes too
                for cb in self.checkboxes:
                    if cb['page'] == page_num:
                         if cb['x'] >= wx1 - 5 and cb['x'] < wx1 + 80 and abs(cb['y'] - wy0) < 15:
                            already_has_field = True; break
                
                if already_has_field: continue
                
                # DISCOVER: Look for an empty area or underline
                discovered_f = None
                
                # Find bounds: next word on same line or page edge
                row_y = round(wy0 / 3) * 3
                row_words = sorted(words_by_row.get(row_y, []), key=lambda x: x['x0'])
                next_word = next((w for w in row_words if float(w['x0']) > wx1 + 5), None)
                limit_x = float(next_word['x0']) if next_word else self.page_widths.get(page_num, 612) - 40
                
                # Case A: Horizontal Underline after label
                segment_lines = [l for l in lines 
                                if abs(l['y0'] - l['y1']) < 2
                                and abs(float(l['top']) - wy1) < 15
                                and float(l['x0']) < limit_x 
                                and float(l['x1']) > wx1]
                
                if segment_lines:
                    line = min(segment_lines, key=lambda l: float(l['top']))
                    f_x0 = max(wx1 + 3, float(line['x0']))
                    f_x1 = min(limit_x - 3, float(line['x1']))
                    f_y0 = float(line['top']) - 13
                    f_y1 = float(line['top'])
                    discovered_f = (f_x0, f_y0, f_x1, f_y1, 'discovery_line')
                else:
                    # Case B: Significant empty gap
                    gap_width = limit_x - wx1
                    if gap_width > 40:
                        # Ensure no overlap with checkboxes
                        collision = False
                        for cb in self.checkboxes:
                            if cb['page'] == page_num and wx1 < cb['x'] < limit_x:
                                collision = True; break
                        if not collision:
                            discovered_f = (wx1 + 5, wy0 - 10, limit_x - 5, wy1 + 2, 'discovery_gap')
                
                if discovered_f:
                    f_x0, f_y0, f_x1, f_y1, src = discovered_f
                    # Stricter text check: ensure discovered area is TRULY empty
                    inner_words = self._get_words_in_bbox(page_num, (f_x0, f_y0, f_x1, f_y1), overlap_threshold=0.1)
                    if not inner_words:
                        label_txt = w_txt.rstrip(':')
                        field_name = self._generate_unique_name(label_txt, page_num)
                        self.text_fields.append({
                            'page': page_num, 'x0': f_x0, 'y0': f_y0, 'x1': f_x1, 'y1': f_y1,
                            'name': field_name, 'label': label_txt, 'format': None, 'format_opts': {}, 'source': f'discovery_{src}'
                        })
                        discovered_count += 1
                        print(f"      [Discovery] Added field for '{label_txt}' on P{page_num+1} ({src})")

        if discovered_count > 0:
            print(f"   Self-Review found and added {discovered_count} missing labeled fields")
    
    # ========== RADIO BUTTON GROUPING ==========
    

    def _detect_comb_fields_from_lines(self):
        """
        Detect fields formed by grids of small boxes (comb fields).
        Commonly used for Account Numbers.
        """
        comb_fields_found = 0
        from collections import defaultdict
        
        for page_num in range(self.page_count):
            lines = self.page_lines.get(page_num, [])
            if not lines:
                continue
                
            # Separate vertical lines
            v_lines = []
            for l in lines:
                if abs(l['x0'] - l['x1']) < 2:  # Vertical
                    v_lines.append({
                        'x': round((l['x0'] + l['x1']) / 2, 1),
                        'y0': round(l['top'], 1),
                        'y1': round(l['bottom'], 1),
                        'length': abs(l['bottom'] - l['top'])
                    })
            
            # Find short vertical lines (box sides, typical height 10-35pt)
            short_v = [l for l in v_lines if 10 < l['length'] < 35]
            
            # Group by top Y position (allows ~3pt tolerance)
            v_groups = defaultdict(list)
            for l in short_v:
                y_key = round(l['y0'] / 3) * 3
                v_groups[y_key].append(l)
            
            for y_key, vlines in sorted(v_groups.items()):
                if len(vlines) >= 8:  # Account numbers have usually 10+ boxes
                    vlines.sort(key=lambda l: l['x'])
                    
                    # Calculate consistent spacing
                    spacings = [vlines[i+1]['x'] - vlines[i]['x'] for i in range(len(vlines)-1)]
                    
                    # Filter to find consistent spacing (box rows have uniform spacing)
                    consistent_spacing = None
                    for target_spacing in [18, 19, 20, 21, 22, 23, 24, 25]:
                        matches = sum(1 for s in spacings if abs(s - target_spacing) < 3)
                        # If >80% of spacings match target
                        if matches >= len(spacings) * 0.8:
                            consistent_spacing = target_spacing
                            break
                    
                    if consistent_spacing:
                        # Split into subgroups based on gaps
                        # If a spacing is > 1.8x the consistent spacing, treat as separate field
                        subgroups = []
                        current_group = [vlines[0]]
                        
                        for i in range(len(spacings)):
                            spacing = spacings[i]
                            # Use strict tolerance (5pt) to prevent merging widely separated grids
                            # or extending into unrelated vertical lines
                            if abs(spacing - consistent_spacing) > 5:
                                # Gap or irregularity found, close current group and start new one
                                subgroups.append(current_group)
                                current_group = [vlines[i+1]]
                            else:
                                current_group.append(vlines[i+1])
                        subgroups.append(current_group)
                        
                        for v_subgroup in subgroups:
                            if len(v_subgroup) < 4: # Ignore tiny fragments (need at least 3 boxes = 4 lines)
                                continue

                            # This is a box row!
                            x_start = v_subgroup[0]['x']
                            x_end = v_subgroup[-1]['x']
                            y_start = v_subgroup[0]['y0']
                            y_end = v_subgroup[0]['y1']
                            num_boxes = len(v_subgroup) - 1
                            
                            # Check for overlap with existing fields
                            if self._position_has_field(page_num, x_start, y_start, x_end, y_end):
                                continue
                                
                            # Find label above
                            res = self._find_nearby_text(page_num, x_start, y_start, 
                                                        direction='above', max_dist=50)
                            label = res[0] if res else None
                            
                            if label:
                                base_name = self._clean_field_name(label)
                            else:
                                base_name = f"CombField_{int(y_start)}_{int(x_start)}"
                                
                            field_name = self._generate_unique_name(base_name, page_num)
                            
                            self.text_fields.append({
                                'page': page_num,
                                'x0': x_start,
                                'y0': y_start,
                                'x1': x_end,
                                'y1': y_end,
                                'name': field_name,
                                'label': label,
                                'format': 'text',
                                'format_opts': {
                                    'comb': True, 
                                    'maxlen': num_boxes,
                                    'do_not_scroll': True
                                },
                                'source': 'comb_box_grid'
                            })
                            comb_fields_found += 1
                            print(f"   Page {page_num+1}: Found comb field '{field_name}' ({num_boxes} char boxes) at x={x_start:.0f}")
        
        if comb_fields_found > 0:
            print(f"   Found {comb_fields_found} comb/grid fields")

    def _detect_section_based_radio_groups(self):
        """
        Detect radio groups based on section headers.
        
        Looks for patterns like:
        - "II. BUSINESS TYPE WHERE EQUIPMENT IS INSTALLED"
        - Followed by checkboxes
        - Until next section "III. PROJECT TYPE"
        
        All checkboxes between section headers belong to ONE radio group
        (you can only select ONE business type, ONE project type, etc.)
        """
        section_groups = []
        
        for page_num in range(self.page_count):
            words = self.page_words.get(page_num, [])
            
            # Find section headers (Roman numerals: I., II., III., IV., V., etc.)
            section_headers = []
            for word in words:
                text = word['text'].strip()
                # Match Roman numeral patterns
                if text in ['I.', 'II.', 'III.', 'IV.', 'V.', 'VI.', 'VII.', 'VIII.', 'IX.', 'X.']:
                    section_headers.append({
                        'text': text,
                        'y': float(word['top']),
                        'x': float(word['x0'])
                    })
            
            if len(section_headers) < 2:
                continue  # Need at least 2 sections to find checkboxes between them
            
            # Sort headers by Y position
            section_headers.sort(key=lambda h: h['y'])
            
            # Find checkboxes between each pair of consecutive sections
            for i in range(len(section_headers) - 1):
                header_start = section_headers[i]
                header_end = section_headers[i + 1]
                
                # Get checkboxes on this page between these Y values
                section_cbs = []
                for cb in self.checkboxes:
                    if cb['page'] == page_num:
                        cb_y = cb['y']
                        # Checkbox is between this header and the next
                        if header_start['y'] < cb_y < header_end['y']:
                            section_cbs.append(cb)
                
                # IMPORTANT: Only create radio groups if checkboxes have radio keyword labels
                # This prevents grouping regular checklists (like equipment items) as radio buttons
                if len(section_cbs) >= 2:
                    # Count how many have radio keyword labels
                    all_labels = [(cb.get('label') or '').lower().strip() for cb in section_cbs]
                    radio_keyword_count = sum(
                        1 for lbl in all_labels 
                        if lbl in self.RADIO_COLUMN_KEYWORDS
                    )
                    
                    # Only group if at least 2 checkboxes have Yes/No/Pass/Fail type labels
                    if radio_keyword_count >= 2:
                        section_groups.append({
                            'name': f"Section_{header_start['text'].replace('.', '')}",
                            'page': page_num,
                            'y_start': header_start['y'],
                            'y_end': header_end['y'],
                            'checkboxes': section_cbs
                        })
        return section_groups

    def _detect_horizontal_yes_no_groups(self, grouped_cb_ids: Set[int]):
        """
        Detect horizontal Yes/No/NA checkbox groups and force them into radio groups.
        
        This handles cases like "+Basement" row where we have "Yes", "No", and "N/A" checkboxes
        side-by-side but without column headers.
        
        v18: Now looks for TRIPLETS (Yes/No/NA) in addition to pairs.
        """
        # Group ungrouped checkboxes by page
        ungrouped_by_page = defaultdict(list)
        for cb in self.checkboxes:
            if id(cb) not in grouped_cb_ids:
                ungrouped_by_page[cb['page']].append(cb)
        
        for page_num, cbs in ungrouped_by_page.items():
            # Sort by Y then X
            # Use small Y tolerance for "same line" sorting (matches row detection tolerance)
            cbs.sort(key=lambda c: (round(c['y'] / 8), c['x']))
            
            i = 0
            while i < len(cbs):
                cb1 = cbs[i]
                
                # Look for all checkboxes on the same row (within 4pt Y tolerance)
                row_cbs = [cb1]
                j = i + 1
                while j < len(cbs) and abs(cbs[j]['y'] - cb1['y']) <= 8:  # Increased from 4 to 8
                    # Also check horizontal proximity (<150pt gap)
                    prev_cb = row_cbs[-1]
                    if (cbs[j]['x'] - (prev_cb['x'] + prev_cb.get('width', 10))) <= 150:
                        # CHECK: Is there a text separator (colon label) between prev_cb and cbs[j]?
                        # CHECK: Is there a text separator (colon label) between prev_cb and cbs[j]?
                        has_separator = False
                        words = self.page_words.get(page_num, [])
                        for w in words:
                            wx0 = float(w['x0'])
                            wt = float(w['top'])
                            # Word starts between the checkboxes?
                            if wx0 > prev_cb['x'] + 5 and wx0 < cbs[j]['x'] - 5:
                                # Same Y-row?
                                if abs(wt - prev_cb['y']) < 30:
                                    text = w['text'].strip()
                                    if text.endswith(':'):
                                        has_separator = True
                                        break
                        
                        if not has_separator:
                            row_cbs.append(cbs[j])
                            j += 1
                        # If separator found, STOP extending this group
                        else:
                            break
                    else:
                        # Gap too large, stop extending
                        break
                
                # Need at least 2 checkboxes on same row
                if len(row_cbs) < 2:
                    i += 1
                    continue
                
                # Check if any have Yes/No/NA labels
                labels = [(cb.get('label') or '').strip().lower() for cb in row_cbs]
                
                def is_radio_keyword(lbl):
                    # Use RADIO_COLUMN_KEYWORDS for comprehensive matching
                    return lbl in self.RADIO_COLUMN_KEYWORDS or \
                           lbl.startswith('yes') or lbl.startswith('no') or \
                           lbl.startswith('pass') or lbl.startswith('fail') or \
                           lbl.startswith('n/a') or lbl in ['y', 'n', 'na']
                
                radio_count = sum(1 for lbl in labels if is_radio_keyword(lbl))
                
                # SAFETY CHECK: Don't group if checkboxes are in different table cells
                cells_differ = False
                if radio_count >= 2 and len(row_cbs) >= 2:
                    page_cells = [c for c in self.table_cells if c['page'] == page_num]
                    for idx in range(1, len(row_cbs)):
                        cb1, cb2 = row_cbs[idx-1], row_cbs[idx]
                        cell1 = None
                        cell2 = None
                        for cell in page_cells:
                            if cell['x0'] <= cb1['x'] <= cell['x1'] and cell['y0'] <= cb1['y'] <= cell['y1']:
                                cell1 = (cell['x0'], cell['x1'])
                            if cell['x0'] <= cb2['x'] <= cell['x1'] and cell['y0'] <= cb2['y'] <= cell['y1']:
                                cell2 = (cell['x0'], cell['x1'])
                        if cell1 and cell2 and cell1 != cell2:
                            cells_differ = True
                            break
                
                # If at least 2 have radio keywords AND not in different cells, group them
                if radio_count >= 2 and not cells_differ:
                    # Found a group! Use the text to the LEFT of the group as the name
                    res = self._find_row_description(row_cbs)
                    row_desc = res[0] if res else None
                    if row_desc:
                        group_name = self._clean_field_name(row_desc)
                    else:
                        group_name = f"Radio_Group_Page{page_num}_{int(cb1['y'])}"
                    
                    # Add suffix based on group size
                    if len(row_cbs) >= 3:
                        group_name += "_YesNoNA"
                    else:
                        group_name += "_YesNo"
                    
                    print(f"DEBUG RADIO GROUP: Found horizontal group '{group_name}' on Page {page_num+1} with {len(row_cbs)} options: {labels}")

                    # Assign radio values based on labels
                    for cb in row_cbs:
                        lbl = (cb.get('label') or '').strip().lower()
                        if lbl.startswith('yes') or lbl == 'y':
                            cb['radio_value'] = 'Yes'
                        elif lbl.startswith('no') and not lbl.startswith('n/a'):
                            cb['radio_value'] = 'No'
                        elif lbl.startswith('n/a') or lbl == 'na':
                            cb['radio_value'] = 'NA'
                        elif lbl.startswith('pass'):
                            cb['radio_value'] = 'Pass'
                        elif lbl.startswith('fail'):
                            cb['radio_value'] = 'Fail'
                        else:
                            cb['radio_value'] = cb.get('label', 'Option')

                    # Create the radio group
                    self.radio_groups.append({
                        'name': group_name,
                        'page': page_num,
                        'checkboxes': row_cbs,
                        'type': 'horizontal_yes_no_na'
                    })
                    
                    # Mark all as grouped
                    for cb in row_cbs:
                        grouped_cb_ids.add(id(cb))
                        # v33.58: Flag as radio group child to avoid conflict removal
                        cb['is_radio_group_child'] = True
                        print(f"      [TRACER] Page {cb['page']+1} HorizontalYesNo flagged as RadioGroupChild at ({cb['x']:.1f},{cb['y']:.1f})")
                    
                    i += len(row_cbs)  # Skip all grouped
                else:
                    i += 1

    def _identify_radio_groups(self):
        """
        Identify checkboxes that should be grouped as radio buttons.
        
        UPDATED LOGIC:
        1. First, try SECTION-BASED grouping: checkboxes between section headers belong together
        2. Then, group checkboxes on the same row (within 5pt Y tolerance)
        3. Include checkboxes BELOW row if within 15pt (for Fiberglass/Cellulose + Spray Foam pattern)
        4. CLUSTER checkboxes that are close together (<100pt apart)
           - Wide gaps (>100pt) split into separate clusters
           - Each cluster of 2+ becomes a radio group
        """
        # PASS 1: Try section-based grouping first
        # Look for checkboxes between Roman numeral headers (I., II., III., etc.)
        # These should ALL be in one group (e.g., Business Type, Project Type)
        section_groups = self._detect_section_based_radio_groups()
        
        # Mark checkboxes that are already in section groups
        section_grouped_cbs = set()
        for sg in section_groups:
            for cb in sg['checkboxes']:
                section_grouped_cbs.add(id(cb))
        
        # PASS 1.5: Detect Yes/No Horizontal Neighbors
        # Force grouping of adjacent Yes/No or Yes/No/NA checkboxes (e.g. Page 19 +Basement)
        self._detect_horizontal_yes_no_groups(section_grouped_cbs)
        
        # PASS 2: Group remaining checkboxes by row
        by_row = defaultdict(list)
        for cb in self.checkboxes:
            if id(cb) in section_grouped_cbs:
                continue  # Already grouped by section
            row_key = (cb['page'], round(cb['row_y'] / 5) * 5)
            by_row[row_key].append(cb)
        
        # Look for checkboxes BELOW a row that should be included (Spray Foam pattern)
        # If a checkbox is within 15pt below a row with 2+ CLOSE checkboxes, merge it
        # IMPORTANT: Only merge if the row above has checkboxes that are CLOSE horizontally
        # This prevents merging on pages like 37 where checkboxes are in separate columns
        # ALSO: Don't merge if the below row has the SAME structure (same count and X positions)
        # - this indicates a regular table with repeating rows, not an offset pattern
        row_keys = list(by_row.keys())
        for page, row_y in row_keys:
            cbs_on_row = by_row[(page, row_y)]
            if len(cbs_on_row) >= 2:  # This row has multiple checkboxes
                # Check if checkboxes on this row are CLOSE horizontally (< 100pt max gap)
                x_sorted = sorted([cb['x'] for cb in cbs_on_row])
                max_gap = max((x_sorted[i+1] - x_sorted[i]) for i in range(len(x_sorted)-1))
                
                if max_gap > 100:
                    # Row has wide-spaced checkboxes (like page 37)
                    # Don't merge any checkboxes from below
                    continue
                
                # Row has CLOSE checkboxes - look for checkboxes 8-18pt below
                for other_key in row_keys:
                    other_page, other_y = other_key
                    if other_page != page:
                        continue
                    y_diff = other_y - row_y
                    if 8 <= y_diff <= 18:  # Close below
                        other_cbs = by_row[other_key]
                        
                        # DON'T merge if the below row has 3+ checkboxes
                        # (it's likely a complete Yes/No/NA set for a different question)
                        if len(other_cbs) >= 3:
                            continue
                        
                        # DON'T merge if the below row has the SAME structure as this row
                        # (same count and similar X positions = regular table pattern)
                        if len(other_cbs) == len(cbs_on_row):
                            other_x_sorted = sorted([cb['x'] for cb in other_cbs])
                            x_matches = all(
                                abs(other_x_sorted[i] - x_sorted[i]) < 10 
                                for i in range(len(x_sorted))
                            )
                            if x_matches:
                                # Same structure - don't merge, it's a repeating table row
                                continue
                        
                        # Check if the below checkbox is at same X as any on the row above
                        for ocb in list(other_cbs):  # Use list() to allow modification
                            for rcb in cbs_on_row:
                                if abs(ocb['x'] - rcb['x']) < 10:  # Same column
                                    # Merge this checkbox into the row above
                                    cbs_on_row.append(ocb)
                                    other_cbs.remove(ocb)
                                    break
        
        group_num = 0
        for key, cbs in by_row.items():
            # Need at least 2 checkboxes on the same row to consider grouping
            if len(cbs) < 2:
                continue
            
            cbs_sorted = sorted(cbs, key=lambda c: c['x'])
            
            # CLUSTER checkboxes by proximity (< 100pt apart)
            # This allows multiple radio groups per row (e.g., two yes/no pairs)
            clusters = []
            current_cluster = [cbs_sorted[0]]
            
            for i in range(1, len(cbs_sorted)):
                gap = cbs_sorted[i]['x'] - cbs_sorted[i-1]['x']
                
                # Check for table cell boundary crossing
                crosses_cell_boundary = False
                cb1, cb2 = cbs_sorted[i-1], cbs_sorted[i]
                
                # Find containing cells
                cell1 = None
                cell2 = None
                
                # Use cached page cells if possible for performance (optimizable, but list is short enough)
                page_cells = [c for c in self.table_cells if c['page'] == cb1['page']]
                
                # TOLERANCE for cell containment (checkboxes might be slightly offset)
                TOL = 5
                
                for cell in page_cells:
                    # Check cb1
                    if cell['x0']-TOL <= cb1['x'] <= cell['x1']+TOL and cell['y0']-TOL <= cb1['y'] <= cell['y1']+TOL:
                        cell1 = cell
                    # Check cb2
                    if cell['x0']-TOL <= cb2['x'] <= cell['x1']+TOL and cell['y0']-TOL <= cb2['y'] <= cell['y1']+TOL:
                        cell2 = cell
                
                # If both are in cells, and they are DIFFERENT cells, force split
                if cell1 and cell2 and cell1 != cell2:
                    crosses_cell_boundary = True
                    # DEBUG: Print split decision
                    # print(f"   ✂️ Splitting radio group at table cell boundary: {cb1.get('label')} | {cb2.get('label')}", file=sys.stderr)

                # FALLBACK: Check for vertical separator lines (if cell detection failed)
                if not crosses_cell_boundary and hasattr(self, 'vertical_lines'):
                    page_v_lines = self.vertical_lines.get(cb1['page'], [])
                    for vx, vy0, vy1 in page_v_lines:
                        # Is line horizontally between checkboxes?
                        # Use slightly relaxed bounds to catch lines near the gap
                        if cb1['x'] < vx < cb2['x']:
                            # Is line vertically overlapping the checkboxes?
                            # Checkboxes usually height ~10-12
                            cby = cb1['y']
                            # Check if line covers the checkbox's Y position (with margin)
                            if vy0 <= cby + 5 and vy1 >= cby - 5: 
                                crosses_cell_boundary = True
                                break

                # FALLBACK 2: Check for "Label:" text between checkboxes (e.g. "Water Heater:")
                if not crosses_cell_boundary:
                    words = self.page_words.get(cb1['page'], [])
                    for w in words:
                        # Check if word is physically between cb1 and cb2
                        # Allow some margins: > cb1.x+10 (skip check box itself) and < cb2.x-5
                        wx0 = float(w['x0'])
                        wx1 = float(w['x1'])
                        wt = float(w['top'])
                        
                        wx0 = float(w['x0'])
                        wx1 = float(w['x1'])
                        wt = float(w['top'])
                        
                        # LOGIC UPDATE: Check if word *STARTS* in the gap
                        # (Don't check ending, as it might abut the next checkbox)
                        if wx0 > cb1['x'] + 5 and wx0 < cb2['x'] - 5:
                            # Check Y alignment (same row) - RELAXED TOLERANCE (was 15)
                            # Text Y might differ significantly from checkbox Y
                            if abs(wt - cb1['y']) < 30:
                                text = w['text'].strip()
                                # If we find a word ending in colon (e.g. "Source:", "Heater:"), it's a separator
                                if text.endswith(':'):
                                    crosses_cell_boundary = True
                                    # print(f"   Te Split on text separator '{text}'", file=sys.stderr)
                                    break

                if gap > 100 or crosses_cell_boundary:
                    # Wide gap - start new cluster
                    if len(current_cluster) >= 2:
                        clusters.append(current_cluster)
                    current_cluster = [cbs_sorted[i]]
                else:
                    # Close - add to current cluster
                    current_cluster.append(cbs_sorted[i])
            
            # Don't forget the last cluster
            if len(current_cluster) >= 2:
                clusters.append(current_cluster)
            
            # First, filter clusters to identify which ones should be radio groups
            # vs individual checkboxes
            radio_clusters = []
            for cluster in clusters:
                all_labels = [(cb.get('label') or '').lower().strip() for cb in cluster]
                
                # Count how many labels are radio keywords
                radio_keyword_count = sum(
                    1 for lbl in all_labels 
                    if lbl in self.RADIO_COLUMN_KEYWORDS
                )
                
                # ONLY create radio groups if we have at least 2 radio keyword labels
                # This ensures standalone checkboxes or unlabeled checkboxes stay as individual checkboxes
                # Examples that SHOULD become radio groups: "Yes | No | N/A", "Pass | Fail"
                # Examples that SHOULD NOT: unmarked boxes, "Check if applicable" boxes, etc.
                if radio_keyword_count >= 2:
                    radio_clusters.append(cluster)
            
            # If there are multiple RADIO clusters on this row, they should share 
            # the same base name from the row description, with _1, _2 suffixes
            row_base_name = None
            if len(radio_clusters) > 1:
                # Multiple clusters on same row - use shared base name with suffix
                # Use the leftmost cluster to find the row description
                leftmost_cluster = min(radio_clusters, key=lambda c: min(cb['x'] for cb in c))
                res = self._find_row_description(leftmost_cluster)
                row_description = res[0] if res else None
                if row_description:
                    row_base_name = self._clean_field_name(row_description)
            
            # Create a radio group for each radio cluster
            for cluster_idx, cluster in enumerate(radio_clusters):
                page_num = key[0]  # Extract page number from key
                
                # Try to get a descriptive name from the row context
                first_cb = cluster[0]
                row_label = first_cb.get('row_label') or ''
                first_label = first_cb.get('label') or ''
                
                # Check if labels are generic Yes/No/NA/Pass/Fail
                all_labels = [(cb.get('label') or '').lower().strip() for cb in cluster]
                all_are_radio_keywords = all(
                    lbl in self.RADIO_COLUMN_KEYWORDS or lbl == '' 
                    for lbl in all_labels
                )
                
                group_num += 1
                
                # Priority for group naming:
                # 1. If multiple radio clusters on row, use shared row_base_name with suffix
                # 2. Row label if available (from table detection)
                # 3. Row description text to the LEFT of the checkboxes
                # 4. First checkbox label (if not a radio keyword)
                # 5. Generic name
                
                if row_base_name:
                    # Multiple clusters on same row - use shared base name with suffix
                    group_name = f"{row_base_name}_{cluster_idx + 1}"
                elif row_label:
                    group_name = self._clean_field_name(row_label)
                elif all_are_radio_keywords:
                    # Labels are Yes/No/Pass/Fail - try to find the question text to the left
                    res = self._find_row_description(cluster)
                    row_description = res[0] if res else None
                    if row_description:
                        group_name = self._clean_field_name(row_description)
                    else:
                        # Fallback to generic name
                        has_pass_fail = any(lbl in ['pass', 'fail'] for lbl in all_labels)
                        if has_pass_fail:
                            group_name = f"PassFail {group_num}"
                        else:
                            group_name = f"YesNoNA {group_num}"
                elif first_label:
                    group_name = self._clean_field_name(first_label)
                else:
                    group_name = f"RadioGroup {group_num}"
                
                # Make group name unique if needed
                group_name = self._generate_unique_name(group_name, page_num)
                
                # Assign radio values - try to find column header (Pass/Fail/Yes/No) first
                # This ensures proper tooltips for inspection-style forms
                for i, cb in enumerate(cluster):
                    cb['radio_group'] = group_name
                    
                    # First, check if the label is already a valid radio value keyword
                    label = cb.get('label', '') or ''
                    label_lower = label.lower().strip()
                    
                    if label_lower in self.RADIO_COLUMN_KEYWORDS:
                        # Label is already Pass/Fail/Yes/No - use the proper case version
                        cb['radio_value'] = self.RADIO_COLUMN_KEYWORDS[label_lower]
                    else:
                        column_header = self._find_column_header_for_checkbox(cb)
                        if column_header:
                            cb['radio_value'] = column_header
                        elif label:
                            cb['radio_value'] = label
                        else:
                            cb['radio_value'] = f"Option{i+1}"
                
                self.radio_groups.append({
                    'name': group_name,
                    'page': key[0],
                    'row_y': key[1],
                    'checkboxes': cluster
                })
                # v33.58: Flag as radio group child
                for cb in cluster:
                    cb['is_radio_group_child'] = True
                    print(f"      [TRACER] Page {cb['page']+1} RadioGroup cluster flagged at ({cb['x']:.1f},{cb['y']:.1f})")
        
        # Add section-based groups (these were created first, add them to list)
        for sg in section_groups:
            group_num += 1
            
            # Use section name for the group
            section_name = sg.get('name', '')
            page_num = sg['page']
            if section_name:
                group_name = self._clean_field_name(section_name)
                group_name = self._generate_unique_name(group_name, page_num)
            else:
                group_name = f"RadioGroup {group_num}"
            
            for i, cb in enumerate(sg['checkboxes']):
                cb['radio_group'] = group_name
                
                # Use same logic as row-based groups for radio value
                label = cb.get('label', '') or ''
                label_lower = label.lower().strip()
                
                if label_lower in self.RADIO_COLUMN_KEYWORDS:
                    cb['radio_value'] = self.RADIO_COLUMN_KEYWORDS[label_lower]
                else:
                    column_header = self._find_column_header_for_checkbox(cb)
                    if column_header:
                        cb['radio_value'] = column_header
                    elif label:
                        cb['radio_value'] = label
                    else:
                        cb['radio_value'] = f"Option{i+1}"
            
            self.radio_groups.append({
                'name': group_name,
                'page': sg['page'],
                'row_y': sg['y_start'],
                'checkboxes': sg['checkboxes']
            })
        
        # PASS 4: Cleanup - Force-group any remaining adjacent Yes/No pairs
        # This catches cases missed by earlier passes (e.g., due to separator detection quirks)
        grouped_checkbox_ids = set()
        for rg in self.radio_groups:
            for cb in rg['checkboxes']:
                grouped_checkbox_ids.add(id(cb))
        
        # Find ungrouped checkboxes with Yes/No labels
        ungrouped = [cb for cb in self.checkboxes if id(cb) not in grouped_checkbox_ids]
        
        # Group by page and row
        by_page_row = defaultdict(list)
        for cb in ungrouped:
            row_key = (cb['page'], round(cb['y'] / 10) * 10)  # 10pt row buckets
            by_page_row[row_key].append(cb)
        
        for key, cbs in by_page_row.items():
            if len(cbs) < 2:
                continue
            
            # Sort by X
            cbs.sort(key=lambda c: c['x'])
            
            # Check if labels are radio keywords
            # v22.96: Only group if labels are standard Yes/No or VERY short (like 'NA')
            # Descriptive labels like "Central System" should remain independent checkboxes.
            standard_yes_no = True
            for cb in cbs:
                l = (cb.get('label') or '').lower().strip().rstrip(':')
                if l not in ['yes', 'no', 'y', 'n', 'n/a', 'na', 'pass', 'fail']:
                    standard_yes_no = False
                    break
            
            labels = [(cb.get('label') or '').strip().lower() for cb in cbs]
            radio_count = sum(1 for lbl in labels if lbl in self.RADIO_COLUMN_KEYWORDS or 
                              lbl.startswith('yes') or lbl.startswith('no') or
                              lbl.startswith('pass') or lbl.startswith('fail'))
            
            # SAFETY CHECK: Don't group if checkboxes are in different table cells
            # This prevents "How did you hear about the program?" type checkboxes from being grouped
            if radio_count >= 2 and standard_yes_no:
                # Check if any adjacent pairs are in different cells
                page_cells = [c for c in self.table_cells if c['page'] == key[0]]
                cells_differ = False
                for idx in range(1, len(cbs)):
                    cb1, cb2 = cbs[idx-1], cbs[idx]
                    cell1 = None
                    cell2 = None
                    for cell in page_cells:
                        if cell['x0'] <= cb1['x'] <= cell['x1'] and cell['y0'] <= cb1['y'] <= cell['y1']:
                            cell1 = (cell['x0'], cell['x1'])
                        if cell['x0'] <= cb2['x'] <= cell['x1'] and cell['y0'] <= cb2['y'] <= cell['y1']:
                            cell2 = (cell['x0'], cell['x1'])
                    # If they're in different cells (different X boundaries), don't group
                    if cell1 and cell2 and cell1 != cell2:
                        cells_differ = True
                        break
                
                if cells_differ:
                    continue  # Skip this row, checkboxes are in separate cells
            
            if radio_count >= 2:
                # Create a radio group from these
                res = self._find_row_description(cbs)
                row_desc = res[0] if res else None
                group_name = self._clean_field_name(row_desc) if row_desc else f"YesNo_Cleanup_{key[0]}_{key[1]}"
                
                for i, cb in enumerate(cbs):
                    label = (cb.get('label') or '').strip().lower()
                    if label.startswith('yes') or label == 'y':
                        cb['radio_value'] = 'Yes'
                    elif label.startswith('no') and not label.startswith('n/a'):
                        cb['radio_value'] = 'No'
                    elif label.startswith('n/a') or label == 'na':
                        cb['radio_value'] = 'NA'
                    elif label in self.RADIO_COLUMN_KEYWORDS:
                        cb['radio_value'] = self.RADIO_COLUMN_KEYWORDS[label]
                    else:
                        cb['radio_value'] = cb.get('label', f'Option{i+1}')
                    cb['radio_group'] = group_name
                
                self.radio_groups.append({
                    'name': group_name,
                    'page': key[0],
                    'row_y': key[1],
                    'checkboxes': cbs,
                    'type': 'cleanup_pass'
                })
                # v33.58: Flag as radio group child
                for cb in cbs:
                    cb['is_radio_group_child'] = True
                    print(f"      [TRACER] Page {cb['page']+1} cleanup-pass flagged at ({cb['x']:.1f},{cb['y']:.1f})")
                        
                print(f"   CLEANUP PASS: Created radio group '{group_name}' with {len(cbs)} options: {labels}")
        
        print(f"   Found {len(self.radio_groups)} radio button groups")
    
    def _refine_horizontal_checkbox_labels(self):
        """Refine labels for checkboxes that are laid out horizontally on the same line.
        
        When multiple checkboxes share the same Y position, they're in a horizontal row.
        Each checkbox's label should only include text up to the NEXT checkbox's position.
        
        Example: "[CB1] Overall of attic [CB2] Insulation markers [CB3] Baffles"
        - CB1's label should be "Overall of attic" (not "Overall of attic Insulation markers Baffles")
        """
        # Group checkboxes by page and Y position (within 8pt tolerance)
        y_tolerance = 8
        rows = {}  # key = (page, rounded_y), value = list of checkboxes
        
        for cb in self.checkboxes:
            page = cb['page']
            y = cb['y']
            # Round Y to group checkboxes on same line
            rounded_y = round(y / y_tolerance) * y_tolerance
            key = (page, rounded_y)
            if key not in rows:
                rows[key] = []
            rows[key].append(cb)
        
        # Process rows with multiple checkboxes
        for (page_num, row_y), row_cbs in rows.items():
            if len(row_cbs) < 2:
                continue  # Only single checkbox, no refinement needed
            
            # Sort by X position (left to right)
            row_cbs.sort(key=lambda c: c['x'])
            
            # Get words for this page
            words = self.page_words.get(page_num, [])
            
            # For each checkbox, find text to its right but before the NEXT checkbox
            for i, cb in enumerate(row_cbs):
                cb_x = cb['x'] + cb.get('width', 10)  # Right edge of checkbox
                cb_y = cb['y']
                
                # Next checkbox X position (or end of row)
                if i + 1 < len(row_cbs):
                    next_cb_x = row_cbs[i + 1]['x']
                else:
                    next_cb_x = cb_x + 200  # Allow 200pt for last checkbox
                
                # Find words between this checkbox and the next
                label_words = []
                for word in words:
                    word_x0 = float(word['x0'])
                    word_x1 = float(word['x1'])
                    word_y = float(word['top'])
                    
                    # Word must be on same line (within y_tolerance)
                    if abs(word_y - cb_y) > y_tolerance:
                        continue
                    
                    # Word must be to the right of this checkbox
                    if word_x0 < cb_x - 5:
                        continue
                    
                    # Word must be before the next checkbox
                    if word_x0 >= next_cb_x - 5:
                        continue
                    
                    # Skip checkbox characters
                    text = word['text'].strip()
                    if text in ['■', '■■', '□', '☐', '☑', '☒', '']:
                        continue
                    
                    # Skip radio keywords
                    if text.lower() in self.RADIO_COLUMN_KEYWORDS:
                        continue
                    
                    label_words.append((word_x0, text))
                
                # Build refined label
                if label_words:
                    label_words.sort(key=lambda w: w[0])
                    refined_label = ' '.join(w[1] for w in label_words)
                    # Only update if we got a meaningful label
                    if refined_label.strip():
                        cb['label'] = refined_label.strip()
    
    def _assign_header_based_checkbox_names(self):
        """Assign names to checkboxes that are under a header like 'Have you taken the following pictures?'.
        
        These checkboxes represent individual items (not mutually exclusive options), 
        so they should:
        1. NOT be grouped as radio buttons
        2. Use their OWN label text beside the checkbox (e.g., "Overall of attic")
        3. Only fall back to header_N naming if no label is found
        """
        # Find checkboxes that are NOT in radio groups
        radio_cb_ids = set()
        for group in self.radio_groups:
            for cb in group['checkboxes']:
                radio_cb_ids.add(id(cb))
        
        # Group non-radio checkboxes by page and find header text above them
        # Look for patterns like "Have you taken the following pictures?"
        header_patterns = ['have you taken', 'following pictures', 'pictures?']
        
        for page_num in set(cb['page'] for cb in self.checkboxes):
            words = self.page_words.get(page_num, [])
            
            # Find potential header text
            headers = []
            for w in words:
                text_lower = w['text'].lower()
                if any(pattern in text_lower for pattern in ['have', 'following', 'pictures']):
                    headers.append({
                        'text': w['text'],
                        'x': float(w['x0']),
                        'y': float(w['top'])
                    })
            
            # Find "Have you taken the following pictures?" headers by combining nearby words
            # Look for sequences of words that form this header
            header_locations = []
            for i, w in enumerate(words):
                if w['text'].lower() == 'have':
                    # Try to find "you taken the following pictures?" nearby
                    header_y = float(w['top'])
                    header_words = [w['text']]
                    
                    for w2 in words:
                        if abs(float(w2['top']) - header_y) < 5 and float(w2['x0']) > float(w['x0']):
                            header_words.append(w2['text'])
                    
                    header_text = ' '.join(header_words)
                    if 'pictures' in header_text.lower():
                        header_locations.append({
                            'text': header_text,
                            'y': header_y,
                            'x': float(w['x0'])
                        })
            
            # For each header, find checkboxes below it (within ~80pt Y range)
            # that are NOT in radio groups
            for header in header_locations:
                header_y = header['y']
                matching_cbs = []
                
                for cb in self.checkboxes:
                    if cb['page'] != page_num:
                        continue
                    if id(cb) in radio_cb_ids:
                        continue
                    
                    # Checkbox should be below header (larger Y) but within 80pt
                    cb_y = cb['y']
                    if 0 < cb_y - header_y < 80:
                        matching_cbs.append(cb)
                
                if matching_cbs:
                    # For these checkboxes, use their own label text (not the header)
                    # The label should already be set from _find_nearby_text
                    # Only assign header_based_name if the checkbox has NO label
                    base_name = self._clean_field_name(header['text'])
                    if not base_name:
                        base_name = "Picture checkbox"
                    
                    # Sort by position (top to bottom, left to right)
                    matching_cbs.sort(key=lambda c: (c['y'], c['x']))
                    
                    fallback_count = 0
                    for cb in matching_cbs:
                        # Mark this as NOT a radio group item
                        cb['is_picture_checkbox'] = True
                        
                        # Use the existing label if available, otherwise fallback
                        if cb.get('label'):
                            # Label exists - use it as the field name
                            cb['header_based_name'] = self._clean_field_name(cb['label'])
                        else:
                            # No label - use header with suffix
                            fallback_count += 1
                            cb['header_based_name'] = f"{base_name}_{fallback_count}"
    
    # ========== PDF CREATION ==========
    
    def _remove_duplicate_fields(self):
        """
        v16.24: Remove duplicate fields that overlap significantly.
        This fixes issues on pages 18-30 where fields are detected by both table and underscore logic.
        """
        if not self.text_fields:
            return

        unique_fields = []
        # Sort by page to optimize comparison
        sorted_fields = sorted(self.text_fields, key=lambda f: (f['page'], f['y0'], f['x0']))
        
        removed_indices = set()
        
        for i in range(len(sorted_fields)):
            if i in removed_indices:
                continue
            
            f1 = sorted_fields[i]
            unique_fields.append(f1)
            
            # Look ahead for overlapping fields on same page
            for j in range(i + 1, len(sorted_fields)):
                if j in removed_indices:
                    continue
                
                f2 = sorted_fields[j]
                if f2['page'] != f1['page']:
                    break # Done with this page
                
                # Check overlap
                x_overlap = min(f1['x1'], f2['x1']) - max(f1['x0'], f2['x0'])
                y_overlap = min(f1['y1'], f2['y1']) - max(f1['y0'], f2['y0'])
                
                if x_overlap > 0 and y_overlap > 0:
                    area1 = (f1['x1'] - f1['x0']) * (f1['y1'] - f1['y0'])
                    area2 = (f2['x1'] - f2['x0']) * (f2['y1'] - f2['y0'])
                    overlap_area = x_overlap * y_overlap
                    
                    # If high overlap (>50% of either field), consider duplicate
                    if overlap_area > 0.5 * min(area1, area2):
                        # v21.51: Prioritize underscore-based fields (they have precise boundaries)
                        # over wider fields from other detection methods
                        f1_source = f1.get('source', '')
                        f2_source = f2.get('source', '')
                        protected_sources = ['embedded_underscore', 'general_underscore', 
                                           'label_below_underscore', 'underscore', 'split_field']
                        
                        f1_protected = f1_source in protected_sources
                        f2_protected = f2_source in protected_sources
                        
                        if f2_protected and not f1_protected:
                            # f2 is protected (underscore), f1 is not - keep f2, remove f1
                            # But f1 is already in unique_fields, so remove it and add f2
                            if f1 in unique_fields:
                                unique_fields.remove(f1)
                            unique_fields.append(f2)
                            removed_indices.add(j)  # Mark j as processed
                        else:
                            # Either both protected, neither protected, or f1 is protected
                            # In these cases, mark j as removed (keep f1)
                            removed_indices.add(j)
        
        self.text_fields = unique_fields
        print(f"   Removed {len(removed_indices)} duplicate fields")

    def _create_fillable_pdf(self):
        """Create the fillable PDF with pikepdf."""
        # v16.24: Remove duplicates before creation
        self._remove_duplicate_fields()

        with pikepdf.open(str(self.input_pdf)) as pdf:
        
            # v21.4: Remove existing form fields to avoid duplicates
            deleted_widgets = 0
            for page in pdf.pages:
                if '/Annots' in page:
                    to_remove = []
                    for i, annot in enumerate(page.Annots):
                        if annot.get('/Subtype') == '/Widget':
                            to_remove.append(i)
                    for i in reversed(to_remove):
                        del page.Annots[i]
                        deleted_widgets += 1
            if deleted_widgets > 0:
                print(f"   v21.4: Removed {deleted_widgets} existing widgets from source PDF")

            # Initialize or Clear AcroForm
            if '/AcroForm' not in pdf.Root:
                pdf.Root.AcroForm = pikepdf.Dictionary({
                    '/Fields': pikepdf.Array([]),
                    '/NeedAppearances': True
                })
            acroform = pdf.Root.AcroForm
            acroform.NeedAppearances = True
            # v22.32: Explicitly clear existing fields list to prevent duplication
            # (page.Annots deletion is not enough if they remain in the global Fields array)
            acroform.Fields = pikepdf.Array([])
            
            # Shared counter for unique names
            self.column_counters = defaultdict(int)

            # Add checkboxes
            for idx, cb in enumerate(self.checkboxes):
                page_num = cb['page']
                page = pdf.pages[page_num]
                
                # Naming Logic
                if cb.get('header_based_name'):
                    base_name = cb['header_based_name']
                else:
                    label = cb.get('label', '')
                    base_name = self._clean_field_name(label) if label else f"Check Box {idx + 1}"
                    if not base_name or base_name == "Field":
                        base_name = f"Check Box {idx + 1}"
                
                self.column_counters[base_name] += 1
                count = self.column_counters[base_name]
                field_name = base_name if count == 1 else f"{base_name}_{count}"
                
                # Sizing & Position Logic
                visual_square = min(cb['width'], cb['height'])
                field_size = max(6, min(14, visual_square * 0.85))
                
                aspect_ratio = cb['height'] / cb['width'] if cb['width'] > 0 else 1
                y_offset_factor = 0.70 if aspect_ratio > 1.3 else 0.50
                
                cx = cb['x'] + cb['width'] / 2
                cy = cb['y'] + visual_square * y_offset_factor
                half_size = field_size / 2
                
                # Storage coordinates
                tx0, ty0, tx1, ty1 = self._transform_coords_for_rotation(
                    page_num, cx - half_size, cy - half_size, cx + half_size, cy + half_size
                )
                
                # Checkbox Widget Dictionary
                widget_dict = pikepdf.Dictionary({
                    '/Type': pikepdf.Name('/Annot'),
                    '/Subtype': pikepdf.Name('/Widget'),
                    '/FT': pikepdf.Name('/Btn'),
                    '/T': pikepdf.String(field_name),
                    '/Rect': pikepdf.Array([tx0, ty0, tx1, ty1]),
                    '/F': 4, # Printable
                    '/V': pikepdf.Name('/Off'),
                    '/AS': pikepdf.Name('/Off'),
                })
                
                # Border/Style settings
                cb_border_thickness = int(self._get_setting('checkbox_border_thickness', 0))
                
                # Broadened to include curve/vector/character as they also often lack visual representation in faint PDFs
                # if cb_border_thickness == 0 and cb.get('source') in ['line_box', 'line_box_v3', 'curve', 'vector', 'character']:
                #     cb_border_thickness = 1
                    
                if cb_border_thickness > 0:
                    bc_hex = self._get_setting('checkbox_border_color', '#000000').lstrip('#')
                    if len(bc_hex) == 6:
                        bc = [int(bc_hex[i:i+2], 16) / 255.0 for i in (0, 2, 4)]
                        widget_dict.BS = pikepdf.Dictionary({'/W': cb_border_thickness, '/S': pikepdf.Name('/S')})
                        widget_dict.MK = pikepdf.Dictionary({'/BC': pikepdf.Array(bc)})
                
                # Add to page and AcroForm
                annot = pdf.make_indirect(widget_dict)
                if '/Annots' not in page:
                    page.Annots = pdf.make_indirect(pikepdf.Array())
                page.Annots.append(annot)
                acroform.Fields.append(annot)
                
                cb['field_name'] = field_name
                self.stats['checkboxes_created'] += 1

            # Add text fields
            for field in self.text_fields:
                page_num = field['page']
                page = pdf.pages[page_num]
                
                # v33.26: PROTECT box_entry (comb) fields from checkbox overlap skip
                # v35.1: PROTECT image_box fields from checkbox overlap skip
                # Comb fields are a special case - they often have internal lines/boxes
                # that get detected as checkboxes. We should NEVER skip comb fields.
                # Image boxes also should never be blocked by checkboxes
                if field.get('source') == 'box_entry' or field.get('comb') or field.get('is_image_box'):
                    overlaps_checkbox = False  # Force bypass overlap check
                else:
                    # Overlap check
                    overlaps_checkbox = False
                    base_tolerance = 1.0
                    overlap_tolerance = 2.0 if field.get('source') == 'general_underscore' else base_tolerance
                    
                    for cb in self.checkboxes:
                        if cb['page'] != page_num: continue
                        x_overlap = max(0, min(field['x1'], cb['x'] + cb['width']) - max(field['x0'], cb['x']))
                        y_overlap = max(0, min(field['y1'], cb['y'] + cb['height']) - max(field['y0'], cb['y']))
                        if x_overlap > overlap_tolerance and y_overlap > overlap_tolerance:
                            overlaps_checkbox = True
                            break
                
                if overlaps_checkbox:
                    # v33.26 DEBUG: Show when box_entry fields are skipped
                    if field.get('source') == 'box_entry' or field.get('comb'):
                        print(f"   ⚠️ COMB FIELD SKIPPED due to checkbox overlap: {field.get('name')} on page {page_num+1}")
                    self.stats['fields_skipped_overlap'] += 1
                    continue

                # Position with transformations
                tx0, ty0, tx1, ty1 = self._transform_coords_for_rotation(
                    page_num, field['x0'], field['y0'], field['x1'], field['y1']
                )
                
                # Inset gap
                gap = 0.5
                tx0, ty0, tx1, ty1 = tx0 + gap, ty0 + gap, tx1 - gap, ty1 - gap
                
                
                is_image_box = field.get('is_image_box', False)
                if is_image_box:
                    field_name = field['name'] + '_af_image'
                    widget_dict = pikepdf.Dictionary({
                        '/Type': pikepdf.Name('/Annot'),
                        '/Subtype': pikepdf.Name('/Widget'),
                        '/FT': pikepdf.Name('/Btn'),
                        '/T': pikepdf.String(field_name),
                        '/Rect': pikepdf.Array([tx0, ty0, tx1, ty1]),
                        '/F': 4,
                        '/Ff': 65536, # Pushbutton
                        '/MK': pikepdf.Dictionary({
                            '/BC': pikepdf.Array([0.75, 0.75, 0.75]),
                            '/BG': pikepdf.Array([1.0, 1.0, 1.0])
                        }),
                        '/BS': pikepdf.Dictionary({'/W': 1, '/S': pikepdf.Name('/S')})
                    })
                    if not hasattr(self, 'image_boxes'): self.image_boxes = []
                    field_copy = field.copy()
                    field_copy['name'] = field_name
                    self.image_boxes.append(field_copy)
                else:
                    field_name = field['name']
                    # Sizing logic
                    fmt = field.get('format')
                    if fmt and fmt in self.FORMAT_PATTERNS and field.get('source') not in ['table', 'table_data_row', 'table_cell']:
                        reduction = self.FORMAT_PATTERNS[fmt].get('width_reduction', 0)
                        if reduction > 0:
                            tx1 = tx0 + (tx1 - tx0) * (1 - reduction)

                    # Style resolution
                    style = field.get('style', {})
                    font_size = float(style.get('fontSize')) if style.get('fontSize') else self._get_setting('field_font_size', 9)
                    font_color_hex = (style.get('fontColor') or self._get_setting('field_font_color', '#000000')).lstrip('#')
                    font_rgb = [int(font_color_hex[i:i+2], 16) / 255.0 for i in (0, 2, 4)] if len(font_color_hex) == 6 else [0,0,0]
                    
                    font_map = {'Helvetica': 'Helv', 'Courier': 'Cour', 'Times-Roman': 'TiRo', 'Arial': 'Helv', 'Verdana': 'Helv'}
                    font_tag = font_map.get(style.get('fontFamily') or self._get_setting('field_font_family', 'Helvetica'), 'Helv')
                    
                    # Alignment
                    align = 0
                    if style.get('textAlign'):
                        align = {'left': 0, 'center': 1, 'right': 2}.get(style['textAlign'], 0)
                    elif field.get('alignment'):
                        align = {'left': 0, 'center': 1, 'right': 2}.get(field['alignment'], 0)
                    else:
                        is_small = (field['x1'] - field['x0']) < self._get_setting('small_field_threshold', 100)
                        label_lower = ((field.get('label') or '') + (field.get('name') or '')).lower()
                        if any(kw in label_lower for kw in ['date', 'name', 'address', 'city', 'state', 'zip', 'phone', 'email']) or ':' in (field.get('label') or '') or not is_small:
                            align = 0
                        else:
                            align = 1
                    
                    ff = 0x800000 # DoNotScroll
                    
                    # v35.13: Auto-enable multiline for tall fields (height > 24pt allows 2+ lines)
                    field_height = field['y1'] - field['y0']
                    is_tall_field = field_height > 24
                    if field.get('format_opts', {}).get('multiline') or is_tall_field:
                        ff |= 0x1000 # Multiline (bit 13)
                        
                    max_len = field.get('max_len') or self.FORMAT_PATTERNS.get(fmt, {}).get('maxlen')
                    if (field.get('comb') or field.get('format_opts', {}).get('comb')) and max_len:
                        ff |= 0x1000000 # Comb
                        
                    # Position with transformations
                    tx0, ty0, tx1, ty1 = self._transform_coords_for_rotation(
                        page_num, field['x0'], field['y0'], field['x1'], field['y1']
                    )
                    
                    # Inset gap
                    gap = 0.5
                    tx0, ty0, tx1, ty1 = tx0 + gap, ty0 + gap, tx1 - gap, ty1 - gap

                    widget_dict = pikepdf.Dictionary({
                        '/Type': pikepdf.Name('/Annot'),
                        '/Subtype': pikepdf.Name('/Widget'),
                        '/FT': pikepdf.Name('/Tx'),
                        '/T': pikepdf.String(field_name),
                        '/Rect': pikepdf.Array([tx0, ty0, tx1, ty1]),
                        '/F': 4,
                        '/Ff': ff,
                        '/DA': pikepdf.String(f'/{font_tag} {font_size} Tf {font_rgb[0]:.3f} {font_rgb[1]:.3f} {font_rgb[2]:.3f} rg'),
                        '/Q': align,
                        '/TU': pikepdf.String(field_name), # Tooltip name only
                        '/Source': pikepdf.String(str(field.get('source'))) # Private property for audit
                    })
                    
                    # v36.0: Force visibility for various field types that often lack visual borders in the source
                    # This includes removed existing widgets, strict grid fields, and structural boxes
                    # v36.0: Removed forced borders to restore user control and v21 stability
                    
                    if max_len: widget_dict.MaxLen = max_len
                    
                    # Background and Border
                    bg_color = self._get_setting('field_background_color', '#EDF4FF')
                    if bg_color and bg_color.lower() not in ['none', 'transparent', 'null']:
                        bg_rgb = [int(bg_color.lstrip('#')[i:i+2], 16) / 255.0 for i in (0, 2, 4)]
                        if '/MK' not in widget_dict: widget_dict.MK = pikepdf.Dictionary()
                        widget_dict.MK.BG = pikepdf.Array(bg_rgb)
                    
                    if self._get_setting('field_border_visible', False):
                        b_thick = int(self._get_setting('field_border_thickness', 1))
                        b_color_hex = self._get_setting('field_border_color', '#000000').lstrip('#')
                        b_rgb = [int(b_color_hex[i:i+2], 16) / 255.0 for i in (0, 2, 4)]
                        if '/MK' not in widget_dict: widget_dict.MK = pikepdf.Dictionary()
                        widget_dict.MK.BC = pikepdf.Array(b_rgb)
                        widget_dict.BS = pikepdf.Dictionary({'/W': b_thick, '/S': pikepdf.Name('/S')})

                    # Track for center alignment (v16.2 logic)
                    if align == 1:
                        if not hasattr(self, '_center_align_widgets'):
                            self._center_align_widgets = []
                        self._center_align_widgets.append(field_name)

                # Final add
                annot = pdf.make_indirect(widget_dict)
                if '/Annots' not in page: page.Annots = pdf.make_indirect(pikepdf.Array())
                page.Annots.append(annot)
                acroform.Fields.append(annot)
                self.stats['text_fields_created'] += 1

            pdf.save(str(self.output_pdf))
            print(f"   ✅ Created {len(acroform.Fields)} form fields using pikepdf")
        
        # v16.1: Post-process to set center alignment via /Q entry
        # PyMuPDF's text_format doesn't persist, so we modify the PDF directly
        if hasattr(self, '_center_align_widgets') and self._center_align_widgets:
            self._apply_center_alignment()
        
        # Set MK rotation for widgets on rotated pages
        self._set_widget_rotations()

        # Verify
        with pikepdf.open(str(self.output_pdf)) as verify_pdf:
            total = 0
            for page in verify_pdf.pages:
                if '/Annots' in page:
                    for annot in page.Annots:
                        if annot.get('/Subtype') == '/Widget':
                            total += 1

        print(f"   ✅ Created {total} form fields")
    
    def _apply_center_alignment(self):
        """
        Apply center text alignment to small fields via /Q PDF entry using pikepdf.
        """
        if not hasattr(self, '_center_align_widgets') or not self._center_align_widgets:
            return

        with pikepdf.open(str(self.output_pdf), allow_overwriting_input=True) as pdf:
            centered_count = 0
            for page in pdf.pages:
                if '/Annots' not in page: continue
                for annot in page.Annots:
                    if annot.get('/Subtype') == '/Widget':
                        name = str(annot.get('/T', ''))
                        if name in self._center_align_widgets:
                            annot.Q = 1 # Center
                            centered_count += 1
            pdf.save(str(self.output_pdf))
        
        if centered_count > 0:
            print(f"   ✅ Applied center alignment to {centered_count} small table fields")
    
    def _set_widget_rotations(self):
        """
        Set MK rotation on widgets for rotated pages using pikepdf.
        """
        print(f"   Page rotations: {self.page_rotations}")
        has_rotated_pages = any(r != 0 for r in self.page_rotations.values())
        if not has_rotated_pages:
            return
        
        try:
            with pikepdf.open(str(self.output_pdf), allow_overwriting_input=True) as pdf:
                modified = False
                for page_num, rotation in self.page_rotations.items():
                    if rotation == 0: continue
                    page = pdf.pages[page_num]
                    if '/Annots' not in page: continue
                    for annot in page.Annots:
                        if annot.get('/Subtype') == '/Widget':
                            if '/MK' not in annot:
                                annot.MK = pikepdf.Dictionary()
                            annot.MK.R = rotation
                            modified = True
                if modified:
                    pdf.save(str(self.output_pdf))
        except Exception as e:
            print(f"   ⚠️  Widget rotation error: {e}")
    
    def _fix_font_encoding(self):
        """
        Apply final AcroForm settings for Acrobat compatibility.
        
        This method does three critical things:
        1. Sets /DA (Default Appearance) string for text fields
        2. Sets /DR (Default Resources) with proper font encoding
           - Helvetica with WinAnsiEncoding (prevents 'invalid encoding' error)
           - ZapfDingbats for checkbox checkmarks
        3. Sets /NeedAppearances: True flag
           - Tells Acrobat to regenerate appearance streams from DA strings
           - This is the KEY fix that makes fields visible in Acrobat
           - Eliminates Preview.app workaround (works on all platforms!)
        """
        try:
            pdf = pikepdf.Pdf.open(str(self.output_pdf), allow_overwriting_input=True)
            
            # Ensure proper font resources in AcroForm
            if '/AcroForm' in pdf.Root:
                acroform = pdf.Root['/AcroForm']
                
                # Create or update DR (Default Resources) with proper font encoding
                if '/DR' not in acroform:
                    acroform['/DR'] = pikepdf.Dictionary()
                
                dr = acroform['/DR']
                
                if '/Font' not in dr:
                    dr['/Font'] = pikepdf.Dictionary()
                
                # Define Helvetica font with proper WinAnsiEncoding
                # Use BOTH /Helv and /Helvetica keys since fitz uses /Helvetica in DA strings
                helvetica_font = pdf.make_indirect(pikepdf.Dictionary({
                    '/Type': pikepdf.Name('/Font'),
                    '/Subtype': pikepdf.Name('/Type1'),
                    '/BaseFont': pikepdf.Name('/Helvetica'),
                    '/Encoding': pikepdf.Name('/WinAnsiEncoding')
                }))
                
                dr['/Font']['/Helv'] = helvetica_font
                dr['/Font']['/Helvetica'] = helvetica_font  # for compatibility
                
                # Courier (Monospace)
                courier_font = pdf.make_indirect(pikepdf.Dictionary({
                    '/Type': pikepdf.Name('/Font'),
                    '/Subtype': pikepdf.Name('/Type1'),
                    '/BaseFont': pikepdf.Name('/Courier'),
                    '/Encoding': pikepdf.Name('/WinAnsiEncoding')
                }))
                dr['/Font']['/Cour'] = courier_font
                
                # Times-Roman (Serif)
                times_font = pdf.make_indirect(pikepdf.Dictionary({
                    '/Type': pikepdf.Name('/Font'),
                    '/Subtype': pikepdf.Name('/Type1'),
                    '/BaseFont': pikepdf.Name('/Times-Roman'),
                    '/Encoding': pikepdf.Name('/WinAnsiEncoding')
                }))
                dr['/Font']['/TiRo'] = times_font
                dr['/Font']['/Helvetica'] = helvetica_font
                
                # Also add ZapfDingbats for checkboxes (used for checkmark character)
                zapf_font = pdf.make_indirect(pikepdf.Dictionary({
                    '/Type': pikepdf.Name('/Font'),
                    '/Subtype': pikepdf.Name('/Type1'),
                    '/BaseFont': pikepdf.Name('/ZapfDingbats')
                }))
                dr['/Font']['/ZaDb'] = zapf_font
                
                # Set default appearance string
                acroform['/DA'] = pikepdf.String('/Helv 0 Tf 0 g')
                
                # CRITICAL: Set NeedAppearances flag for Acrobat compatibility
                # This tells Acrobat to regenerate appearance streams from DA strings
                # Eliminates the need for Preview.app workaround (cross-platform!)
                acroform['/NeedAppearances'] = True
            
            # Apply checkbox mark style to ALL checkbox fields
            # This ensures the selected style (check, circle, cross, etc.) is used
            checkbox_style = self._get_setting('checkbox_style', 'check')
            style_map = {
                'check': '4',     # ✓ checkmark
                'circle': 'l',   # ● filled circle
                'cross': '8',    # ✗ cross
                'square': 'n',   # ■ filled square
                'diamond': 'u',  # ◆ diamond
                'star': 'H'      # ★ star
            }
            ca_char = style_map.get(checkbox_style, '4')
            
            checkbox_count = 0
            for page in pdf.pages:
                if '/Annots' not in page:
                    continue
                for annot in page.Annots:
                    # Only apply to button fields (checkboxes and radios)
                    if annot.get('/FT') != pikepdf.Name('/Btn'):
                        continue
                    
                    # v35.2: SKIP pushbuttons - they have Ff=65536 flag
                    # Pushbuttons are image upload fields that need their /MK preserved
                    ff = int(annot.get('/Ff', 0))
                    if ff & 65536:  # Pushbutton flag
                        continue
                    
                    # Set /MK/CA to the selected style character
                    if '/MK' not in annot:
                        annot['/MK'] = pikepdf.Dictionary({})
                    annot['/MK']['/CA'] = ca_char
                    
                    # Delete existing appearance streams so Acrobat regenerates them
                    # using the new /MK/CA character
                    if '/AP' in annot:
                        del annot['/AP']
                    
                    checkbox_count += 1
            
            if checkbox_count > 0:
                print(f"   ✅ Applied checkbox style '{checkbox_style}' to {checkbox_count} fields")
            
            pdf.save(str(self.output_pdf))
            print("   ✅ AcroForm settings applied (DA, DR, NeedAppearances)")
            
        except Exception as e:
            print(f"   ⚠️  Font encoding fix error: {e}")
    
    def _add_javascript_formatting(self):
        """Add JavaScript validation AFTER Preview fix."""
        pdf = pikepdf.Pdf.open(str(self.output_pdf), allow_overwriting_input=True)
        
        # Ensure AcroForm exists
        if '/AcroForm' not in pdf.Root:
            pdf.Root['/AcroForm'] = pikepdf.Dictionary({
                '/Fields': pikepdf.Array([]),
                '/NeedAppearances': True
            })
        pdf.Root['/AcroForm']['/NeedAppearances'] = True
        
        format_counts = defaultdict(int)
        
        # v22.83: Pre-detect date columns by position (checking column headers)
        date_column_ranges = {}  # {page_num: [(x0, x1), ...]}
        for page_num in range(self.page_count):
            tables = self.page_tables.get(page_num, [])
            page_obj = self.pdf.pages[page_num]
            
            for table in tables:
                if not table.rows:
                    continue
                header_row = table.rows[0]
                if not hasattr(header_row, 'cells'):
                    continue
                
                for cell in header_row.cells:
                    if not cell:
                        continue
                    cx0, cy0, cx1, cy1 = cell
                    try:
                        crop = page_obj.within_bbox((cx0+1, cy0+1, cx1-1, cy1-1))
                        text = (crop.extract_text() or "").strip().lower()
                        
                        # Column headers that indicate date fields
                        if any(kw in text for kw in ['date resolved', 'date', 'completion date', 'start date']):
                            if page_num not in date_column_ranges:
                                date_column_ranges[page_num] = []
                            date_column_ranges[page_num].append((cx0, cx1))
                            print(f"   📅 P{page_num} Date column detected: x={cx0:.1f}-{cx1:.1f} ('{text}')")
                    except:
                        pass
        
        for field in self.text_fields:
            # Skip box_entry fields - they should have plain text, no formatting
            if field.get('source') == 'box_entry':
                continue
            
            fmt = field.get('format')
            field_name_lower = field.get('name', '').lower()

            # v22: SAFEGUARD - Check suffix for text identifiers to prevent false currency/number formatting
            # This logic mirrors the fix in fill_pdf.py
            suffix = field_name_lower
            for sep in [' - ', ' – ', ' : ', '_']:
                if sep in field_name_lower:
                    parts = field_name_lower.rsplit(sep, 1)
                    if len(parts) > 1 and parts[1].strip():
                        suffix = parts[1].strip()
                        break
            
            text_identifiers = [
                'company', 'business', 'contractor', 'customer', 'applicant', 'owner',
                'name', 'contact', 'representative', 'rep', 'technician', 'installer',
                'address', 'street', 'city', 'county', 'municipality',
                'email', 'title', 'description', 'notes', 'comments', 'remarks',
                'license', 'permit', 'certificate', 'number' # 'number' usually implies Account Number (text) not numeric value
            ]
            
            # If suffix clearly indicates a text field, FORCE format to None (unless it's specifically date/phone/zip/email/state)
            if any(ident in suffix for ident in text_identifiers):
                # Don't override specific text formats like date, phone, email, etc.
                if fmt in ['currency', 'number']:
                    print(f"   ⚠️ Override: Removing '{fmt}' format from '{field.get('name')}' because suffix '{suffix}' indicates text.")
                    fmt = None

            # COMPREHENSIVE FALLBACK: Check field NAME for format keywords
            if not fmt:
                field_name_lower = field.get('name', '').lower()
                # Normalize underscores to spaces for consistent matching
                check_name = field_name_lower.replace('_', ' ')
                
                # Date detection
                if any(kw in check_name for kw in ['date', 'start date', 'completion date', 'expected date', 'date of', 'tuneup date']):
                    fmt = 'date'
                # Phone detection
                elif any(kw in field_name_lower for kw in ['phone', 'tel', 'fax']):
                    fmt = 'phone'
                # Email detection
                elif 'email' in field_name_lower or 'e-mail' in field_name_lower:
                    fmt = 'email'
                # State detection (but not "statement")
                elif 'state' in field_name_lower and 'statement' not in field_name_lower:
                    fmt = 'state'
                # Zip detection
                elif any(kw in field_name_lower for kw in ['zip', 'postal']):
                    fmt = 'zip'
                # Number detection (age, count, quantity, sq ft, etc.) - but NOT "Account Number"
                elif any(kw in field_name_lower for kw in ['age of', 'number of', 'sq ft', 'sqft', 'square', 'quantity', 'qty', 'units', 'count', 'total', 'footage']):
                    # Exclude account number fields from number formatting
                    if 'account' not in field_name_lower:
                        fmt = 'number'
            
            # v22.83: Position-based date column detection
            if not fmt and field['page'] in date_column_ranges:
                field_x = field.get('x0', 0)
                for col_x0, col_x1 in date_column_ranges[field['page']]:
                    if col_x0 - 5 <= field_x <= col_x1 + 5:
                        fmt = 'date'
                        print(f"   📅 Field '{field.get('name')}' in date column (x={field_x:.1f}) - applying date format")
                        break
            
            if not fmt:
                continue
            
            page = pdf.pages[field['page']]
            if '/Annots' not in page:
                continue
            
            for annot in page.Annots:
                if str(annot.get('/T', '')) != field['name']:
                    continue
                
                aa_dict = pikepdf.Dictionary()
                
                if fmt == 'date':
                    # Use date format from settings
                    # Adobe JS expects lowercase format strings (yyyy, mm, dd)
                    date_fmt = self.settings.get('date_format', 'mm/dd/yyyy').lower()
                    
                    # DEBUG: Print exact format being applied
                    print(f"   📅 Field '{field['name']}' (Source: {field.get('source')}): Applying date format '{date_fmt}'")
                    
                    aa_dict['/K'] = pikepdf.Dictionary({
                        '/S': pikepdf.Name('/JavaScript'),
                        '/JS': f'AFDate_KeystrokeEx("{date_fmt}");'
                    })
                    aa_dict['/F'] = pikepdf.Dictionary({
                        '/S': pikepdf.Name('/JavaScript'),
                        '/JS': f'AFDate_FormatEx("{date_fmt}");'
                    })
                    format_counts['date'] += 1
                
                elif fmt == 'zip':
                    # Use built-in ZIP code format (type 0 = 5-digit ZIP)
                    # 0 = 5-digit ZIP, 1 = ZIP+4, 2 = Phone, 3 = SSN
                    aa_dict['/K'] = pikepdf.Dictionary({
                        '/S': pikepdf.Name('/JavaScript'),
                        '/JS': 'AFSpecial_Keystroke(0);'
                    })
                    aa_dict['/F'] = pikepdf.Dictionary({
                        '/S': pikepdf.Name('/JavaScript'),
                        '/JS': 'AFSpecial_Format(0);'
                    })
                    format_counts['zip'] += 1
                
                elif fmt == 'phone':
                    # Use built-in phone format or custom settings
                    # Default: (999) 999-9999 (type 2)
                    phone_fmt_str = self.settings.get('phone_format', '(999) 999-9999')
                    
                    # Map common formats to Adobe built-in types
                    # 0=Zip(5), 1=Zip(9), 2=Phone, 3=SSN
                    tf_type = 2
                    if phone_fmt_str == '999-999-9999':
                         # Not standard built-in, would need Arbitrary Mask (AFNumber? No)
                         # Fallback to standard for now or implement Arbitrary Mask if needed
                         pass
                    
                    aa_dict['/K'] = pikepdf.Dictionary({
                        '/S': pikepdf.Name('/JavaScript'),
                        '/JS': f'AFSpecial_Keystroke({tf_type});'
                    })
                    aa_dict['/F'] = pikepdf.Dictionary({
                        '/S': pikepdf.Name('/JavaScript'),
                        '/JS': f'AFSpecial_Format({tf_type});'
                    })
                    format_counts['phone'] += 1
                
                elif fmt == 'score':
                    aa_dict['/K'] = pikepdf.Dictionary({
                        '/S': pikepdf.Name('/JavaScript'),
                        '/JS': "var re = /^[0-9]*$/; if (!re.test(event.change)) event.rc = false;"
                    })
                    format_counts['score'] += 1
                
                elif fmt == 'state':
                    # Auto-uppercase for state abbreviations
                    aa_dict['/K'] = pikepdf.Dictionary({
                        '/S': pikepdf.Name('/JavaScript'),
                        '/JS': "event.change = event.change.toUpperCase(); var re = /^[A-Za-z]*$/; if (!re.test(event.change)) event.rc = false;"
                    })
                    format_counts['state'] += 1
                
                elif fmt == 'year':
                    # Year fields - numbers only, 4 digits max
                    aa_dict['/K'] = pikepdf.Dictionary({
                        '/S': pikepdf.Name('/JavaScript'),
                        '/JS': "var re = /^[0-9]*$/; if (!re.test(event.change)) event.rc = false;"
                    })
                    format_counts['year'] += 1
                
                elif fmt == 'number':
                    # Non-currency numbers (temp, btuh, watts, etc.) - NO decimals, integers only
                    # AFNumber_Keystroke(nDec, sepStyle, negStyle, currStyle, strCurrency, bCurrencyPrepend)
                    # nDec=0 (no decimal places), sepStyle=0 (commas), no currency symbol
                    aa_dict['/K'] = pikepdf.Dictionary({
                        '/S': pikepdf.Name('/JavaScript'),
                        '/JS': 'AFNumber_Keystroke(0, 0, 0, 0, "", false);'
                    })
                    aa_dict['/F'] = pikepdf.Dictionary({
                        '/S': pikepdf.Name('/JavaScript'),
                        '/JS': 'AFNumber_Format(0, 0, 0, 0, "", false);'
                    })
                    # Keep left-alignment (default) - don't set /Q
                    format_counts['number'] += 1
                
                elif fmt == 'currency':
                    # Use format from settings
                    format_opts = field.get('format_opts', {})
                    has_dollar = format_opts.get('has_dollar_in_cell', False)
                    
                    sep_style = 0 # Default: 1,234.56 (0)
                    sep_char = self.settings.get('number_thousands_separator', ',')
                    if sep_char == '.': sep_style = 2 # 1.234,56
                    
                    dec_places = int(self.settings.get('currency_decimal_places', 2))
                    sym = self.settings.get('currency_symbol', '$')
                    sym_pos = 1 if self.settings.get('currency_symbol_position', 'prefix') == 'suffix' else 0 # 0=prefix, 1=suffix
                    neg_style = 0 # Minus sign
                    
                    # Logic: If $ is already in cell (has_dollar=True), we suppress symbol in JS formatting
                    # UNLESS user forces a different symbol via settings? 
                    # For now, if has_dollar is True, we assume cell has label "$ _____".
                    # But Adobe JS formatting ADDS the symbol to the value. 
                    # If we format with symbol, it will show "$ 123.00".
                    # If label is outside field, perfect. If label is INSIDE field (rare for fillable), double $?
                    # Let's trust the setting. If has_dollar logic was meant to suppress symbol, we do so:
                    
                    final_sym = "" if has_dollar else sym
                    prepend = "true" if sym_pos == 0 else "false"
                    
                    aa_dict['/K'] = pikepdf.Dictionary({
                        '/S': pikepdf.Name('/JavaScript'),
                        '/JS': f'AFNumber_Keystroke({dec_places}, {sep_style}, {neg_style}, 0, "{final_sym}", {prepend});'
                    })
                    aa_dict['/F'] = pikepdf.Dictionary({
                        '/S': pikepdf.Name('/JavaScript'),
                        '/JS': f'AFNumber_Format({dec_places}, {sep_style}, {neg_style}, 0, "{final_sym}", {prepend});'
                    })
                    # Keep left-alignment (default)
                    format_counts['currency'] += 1
                
                if aa_dict.keys():
                    annot['/AA'] = aa_dict
                
                # Also set MaxLen for ZIP and State fields
                if fmt in ['zip', 'state']:
                    maxlen = self.FORMAT_PATTERNS.get(fmt, {}).get('maxlen', 0)
                    if maxlen:
                        annot['/MaxLen'] = maxlen
                
                break
        
        pdf.save(str(self.output_pdf))
        
        total = sum(format_counts.values())
        if total > 0:
            parts = [f"{fmt}={count}" for fmt, count in sorted(format_counts.items())]
            print(f"   ✅ Added JavaScript to {total} fields: {', '.join(parts)}")
        
        # Add click-to-browse JavaScript for image boxes
        self._add_image_button_actions()
    
    def _add_image_button_actions(self):
        """Add JavaScript click actions to image upload button fields.
        
        Matches the format from the reference PDF (v21_Web_Release):
        - Simple JavaScript: event.target.buttonImportIcon();
        - /MK with /BC (border), /BG (background), /IF (icon fit), /TP (text position)
        """
        if not hasattr(self, 'image_boxes') or not self.image_boxes:
            # v35.2: Fallback - populate image_boxes from text_fields that have is_image_box
            count_from_text = sum(1 for f in self.text_fields if f.get('is_image_box'))
            if count_from_text > 0:
                self.image_boxes = [f for f in self.text_fields if f.get('is_image_box')]
            else:
                return
        
        pdf = pikepdf.Pdf.open(str(self.output_pdf), allow_overwriting_input=True)
        
        image_count = 0
        for field in self.image_boxes:
            page = pdf.pages[field['page']]
            if '/Annots' not in page:
                continue
            
            # Convert to list for iteration
            annots = list(page.Annots)
            for annot in annots:
                annot_name = str(annot.get('/T', ''))
                if annot_name != field['name']:
                    continue
                
                # Simple JavaScript action - matches reference PDF exactly
                annot['/A'] = pikepdf.Dictionary({
                    '/S': pikepdf.Name('/JavaScript'),
                    '/JS': 'event.target.buttonImportIcon();'
                })
                
                # Set appearance characteristics to match reference PDF
                # /BC: border color (gray), /BG: background (white)
                # /IF: icon fit (empty), /TP: 1 (icon only, no text)
                from decimal import Decimal
                annot['/MK'] = pikepdf.Dictionary({
                    '/BC': pikepdf.Array([Decimal('0.75293')]),  # Gray border
                    '/BG': pikepdf.Array([Decimal('1.0'), Decimal('1.0'), Decimal('1.0')]),  # White background
                    '/IF': pikepdf.Dictionary({}),  # Empty icon fit dict
                    '/TP': 1  # Icon only (no caption)
                })
                
                image_count += 1
                break
        
        pdf.save(str(self.output_pdf))
        
        if image_count > 0:
            print(f"   ✅ Added click-to-browse to {image_count} image boxes")

    def _create_radio_groups(self):
        """
        Create radio button groups AFTER Preview fix.
        
        LEARNED: Use pikepdf to set /Parent references and /MK with checkmark.
        IMPORTANT: Must remove children from AcroForm/Fields when adding to parent.
        """
        if not self.radio_groups:
            print("   No radio groups to create")
            return
        
        pdf = pikepdf.Pdf.open(str(self.output_pdf), allow_overwriting_input=True)
        
        if '/AcroForm' not in pdf.Root:
            pdf.Root['/AcroForm'] = pikepdf.Dictionary({
                '/Fields': pikepdf.Array([]),
                '/NeedAppearances': True
            })
        
        groups_created = 0
        # Track all widget references that become radio children (to remove from Fields)
        widgets_to_remove = []
        
        for group in self.radio_groups:
            group_name = group['name']
            page_num = group['page']
            checkboxes = group['checkboxes']
            
            if len(checkboxes) < 2:
                continue
            
            page = pdf.pages[page_num]
            if '/Annots' not in page:
                continue
            
            page_height = float(page.MediaBox[3]) if '/MediaBox' in page else 792.0
            
            # Create parent field
            parent_dict = pikepdf.Dictionary({
                '/FT': pikepdf.Name('/Btn'),
                '/Ff': 49152,  # Radio | NoToggleToOff
                '/T': group_name,
                '/Kids': pikepdf.Array([]),
                '/V': pikepdf.Name('/Off')
            })
            parent = pdf.make_indirect(parent_dict)
            
            children_found = 0
            for idx, cb in enumerate(checkboxes, start=1):
                cb_x = cb['x']
                cb_y_pdf = page_height - cb['y'] - cb['height']
                cb_value = cb.get('radio_value') or 'Option'
                cb_label = cb.get('label', '')  # Get the nearby text label for tooltip
                found_match = False
                
                for annot in page.Annots:
                    if annot.get('/Subtype') != pikepdf.Name('/Widget'):
                        continue
                    if annot.get('/FT') != pikepdf.Name('/Btn'):
                        continue
                    
                    rect = annot.get('/Rect', [])
                    if len(rect) < 4:
                        continue
                    
                    ax0, ay0 = float(rect[0]), float(rect[1])
                    
                    if abs(ax0 - cb_x) < 5 and abs(ay0 - cb_y_pdf) < 5:
                        print(f"      [create_radio_groups] Matched widget at {ax0:.1f},{ay0:.1f} for CB {cb_value}")
                        # Track this widget for removal from Fields array
                        widgets_to_remove.append(annot.objgen)
                        
                        # Convert to radio button
                        annot['/Ff'] = 49152
                        annot['/Parent'] = parent
                        
                        # CRITICAL: Radio button children should NOT have /T names
                        # Only the parent has /T - children are identified by their /AP values
                        # Having /T on children makes Acrobat treat them as separate fields!
                        if '/T' in annot:
                            del annot['/T']
                        
                        annot['/AS'] = pikepdf.Name('/Off')
                        
                        # Set tooltip to the nearby text label (not the radio value)
                        # Use label if available, otherwise fall back to radio value
                        tooltip_text = cb_label if cb_label else cb_value
                        # Guard against None (PDF dict keys cannot be set to None)
                        if tooltip_text:
                            annot['/TU'] = tooltip_text
                        
                        # LEARNED: Checkmark appearance - use style from settings
                        # ZapfDingbats character codes for different styles
                        checkbox_style_map = {
                            'check': '4',     # ✓ checkmark
                            'circle': 'l',    # ● filled circle
                            'cross': '8',     # ✗ cross
                            'square': 'n',    # ■ filled square
                            'diamond': 'u',   # ◆ diamond
                            'star': 'H'       # ★ star
                        }
                        checkbox_style = self._get_setting('checkbox_style', 'check')
                        ca_char = checkbox_style_map.get(checkbox_style, '4')
                        
                        # Fix: Retain/Apply Border Settings (v21.1)
                        # Pass 8 was overwriting /MK without /BC, causing borders to disappear
                        mk_dict = {'/CA': ca_char}
                        
                        cb_border_thickness = int(self._get_setting('checkbox_border_thickness', 0))
                        if cb_border_thickness > 0:
                            # Apply /BS
                            annot['/BS'] = pikepdf.Dictionary({
                                '/W': cb_border_thickness,
                                '/S': pikepdf.Name('/S')
                            })
                            
                            # Apply /BC to MK
                            cb_border_color = self._get_setting('checkbox_border_color', '#000000')
                            c = cb_border_color.lstrip('#')
                            if len(c) == 6:
                                bc = [int(c[i:i+2], 16)/255.0 for i in (0,2,4)]
                                mk_dict['/BC'] = pikepdf.Array(bc)
                                
                        annot['/MK'] = pikepdf.Dictionary(mk_dict)
                        
                        # CRITICAL: Manually inject border command into AP streams (v21.1)
                        # Just like we did for Checkboxes, we must force the border drawing
                        if cb_border_thickness > 0:
                            try:
                                # Calculate Inset Border
                                rect = annot.get('/Rect')
                                w = float(rect[2]) - float(rect[0])
                                h = float(rect[3]) - float(rect[1])
                                
                                c = cb_border_color.lstrip('#')
                                if len(c) == 6:
                                    cr = int(c[0:2], 16) / 255.0
                                    cg = int(c[2:4], 16) / 255.0
                                    cb = int(c[4:6], 16) / 255.0
                                else:
                                    cr, cg, cb = 0, 0, 0
                                
                                t = float(cb_border_thickness)
                                x = t / 2.0
                                y = t / 2.0
                                w_rect = w - t
                                h_rect = h - t
                                if w_rect < 0: w_rect = 0
                                if h_rect < 0: h_rect = 0
                                
                                cmd = f" q {cr:.3f} {cg:.3f} {cb:.3f} RG {t} w 0 0 0 0 k {x:.2f} {y:.2f} {w_rect:.2f} {h_rect:.2f} re S Q"
                                cmd_bytes = cmd.encode('ascii')
                                
                                if '/AP' in annot and '/N' in annot['/AP']:
                                    ap_n = annot['/AP']['/N']
                                    for key in ap_n.keys():
                                        stream = ap_n[key]
                                        # Append to existing stream
                                        current_data = stream.read_bytes()
                                        stream.write(current_data + cmd_bytes)
                                        # print(f"   Injected border to Radio {key}")
                            except Exception as e:
                                print(f"   ⚠️ Failed to inject border to radio: {e}")

                        # Update appearance dictionary to have the correct export value name
                        value_name = re.sub(r'[^\\w]', '', cb_value) or 'Option'
                        
                        # Get annot dimensions for creating appearance streams
                        annot_rect = annot.get('/Rect', [0, 0, 10, 10])
                        if hasattr(annot_rect, '__iter__'):
                            annot_rect = [float(r) for r in annot_rect]
                        annot_w = abs(annot_rect[2] - annot_rect[0])
                        annot_h = abs(annot_rect[3] - annot_rect[1])
                        
                        if '/AP' in annot and '/N' in annot['/AP']:
                            ap_n = annot['/AP']['/N']
                            # Check if ap_n has keys (non-empty)
                            has_keys = hasattr(ap_n, 'keys') and len(list(ap_n.keys())) > 0
                            
                            if has_keys:
                                # Look for existing "on" state (/Yes or any non-Off state)
                                on_state = None
                                for key in list(ap_n.keys()):
                                    if str(key) != '/Off':
                                        on_state = key
                                        break
                                
                                if on_state and str(on_state) != f'/{value_name}':
                                    # Rename existing on state to our value name
                                    ap_n[pikepdf.Name(f'/{value_name}')] = ap_n[on_state]
                                    del ap_n[on_state]
                                elif not on_state and '/Off' in ap_n:
                                    # No on state exists - copy Off appearance as the on state
                                    ap_n[pikepdf.Name(f'/{value_name}')] = ap_n['/Off']
                            else:
                                # Empty /AP/N - create proper appearance streams
                                self._create_radio_appearance(pdf, annot, value_name, annot_w, annot_h, ca_char)
                        else:
                            # No /AP/N at all - create it with proper appearance streams
                            if '/AP' not in annot:
                                annot['/AP'] = pikepdf.Dictionary()
                            self._create_radio_appearance(pdf, annot, value_name, annot_w, annot_h, ca_char)
                        
                        annot['/P'] = page.obj
                        parent['/Kids'].append(annot)
                        children_found += 1
                        break
            
            if children_found >= 2:
                pdf.Root['/AcroForm']['/Fields'].append(parent)
                groups_created += 1
        
        # Remove the widgets that became radio children from the Fields array
        # This is CRITICAL for pdf-lib to recognize them as a single radio group
        if widgets_to_remove:
            fields_array = pdf.Root['/AcroForm']['/Fields']
            new_fields = []
            removed_count = 0
            for field in fields_array:
                # Check if this field's object gen matches any we need to remove
                if hasattr(field, 'objgen') and field.objgen in widgets_to_remove:
                    removed_count += 1
                else:
                    new_fields.append(field)
            
            # Replace Fields array
            pdf.Root['/AcroForm']['/Fields'] = pikepdf.Array(new_fields)
            if removed_count > 0:
                print(f"   ✅ Removed {removed_count} widget references from Fields array")
        
        pdf.save(str(self.output_pdf))
        print(f"   ✅ Created {groups_created} radio button groups")
    
    def _create_radio_appearance(self, pdf, annot, value_name: str, width: float, height: float, ca_char: str = '4'):
        """
        Create appearance streams for a radio button with a specific on-state name.
        
        Args:
            pdf: The pikepdf PDF object
            annot: The annotation to add appearance to
            value_name: The on-state name (e.g., 'AccountHolder')
            width, height: Dimensions of the appearance stream
            ca_char: ZapfDingbats character for checkmark
        """
        # Create ZapfDingbats font resource
        zadb_font = pikepdf.Dictionary({
            '/Type': pikepdf.Name('/Font'),
            '/Subtype': pikepdf.Name('/Type1'),
            '/BaseFont': pikepdf.Name('/ZapfDingbats')
        })
        resources = pikepdf.Dictionary({
            '/Font': pikepdf.Dictionary({'/ZaDb': zadb_font})
        })
        
        # Calculate positioning
        font_size = min(width, height) * 0.8
        x_off = (width - font_size * 0.6) / 2
        y_off = (height - font_size * 0.6) / 2
        
        # Create OFF appearance (empty box)
        off_content = b'q Q'  # Simple empty graphics state
        off_stream = pikepdf.Stream(pdf, off_content)
        off_stream['/BBox'] = pikepdf.Array([0, 0, width, height])
        off_stream['/Subtype'] = pikepdf.Name('/Form')
        off_stream['/Type'] = pikepdf.Name('/XObject')
        
        # Create ON appearance (with checkmark)
        on_content = f'q\nBT\n/ZaDb {font_size:.2f} Tf\n0 g\n{x_off:.2f} {y_off:.2f} Td\n({ca_char}) Tj\nET\nQ'.encode('latin-1')
        on_stream = pikepdf.Stream(pdf, on_content)
        on_stream['/BBox'] = pikepdf.Array([0, 0, width, height])
        on_stream['/Subtype'] = pikepdf.Name('/Form')
        on_stream['/Type'] = pikepdf.Name('/XObject')
        on_stream['/Resources'] = resources
        
        # Create /AP/N dictionary with both states
        ap_n = pikepdf.Dictionary()
        ap_n['/Off'] = off_stream
        ap_n[pikepdf.Name(f'/{value_name}')] = on_stream
        
        annot['/AP']['/N'] = ap_n
    
    def _regenerate_appearances(self):
        """
        DEPRECATED: Regenerate appearance streams for all form fields.
        
        In the new pikepdf-based implementation, we rely on /NeedAppearances: True
        to handle this.
        """
        pass
    
    def _generate_overlays(self):
        """Generate vector QA overlays for the *generated* fields (no poppler).
        Creates a PDF in the overlay dir with rectangles for all final fields.
        """
        try:
            # Build candidates from final text_fields and checkboxes
            candidates = []
            for f in (self.text_fields or []):
                candidates.append({'page': f.get('page', 0), 'x0': f.get('x0',0), 'y0': f.get('y0',0), 'x1': f.get('x1',0), 'y1': f.get('y1',0), 'type': f.get('source','text')})
            for cb in (self.checkboxes or []):
                # Normalize checkbox coords to rectangles
                x = cb.get('x')
                y = cb.get('y')
                w = cb.get('width')
                h = cb.get('height')
                candidates.append({'page': cb.get('page',0), 'x0': x, 'y0': y, 'x1': x + w, 'y1': y + h, 'type': cb.get('source','checkbox')})

            overlay_pdf = str(self.overlay_dir / f"{self.output_pdf.stem}_overlay.pdf")
            self._generate_candidate_overlay(candidates, overlay_pdf)
            print(f"   ✅ Overlays saved to: {overlay_pdf}")
        except Exception as e:
            print(f"   ⚠️  Overlay generation failed: {e}")
    
    def _find_nearby_text(self, page_num: int, x: float, y: float, 
                         direction: str = 'right', max_dist: float = 40) -> Optional[Tuple[str, float]]:
        """Find text near a position.
        
        Returns: (text, x1_coordinate) tuple or None
        """
        if page_num not in self.page_words:
            return None
        
        words = self.page_words[page_num]
        
        filler_words = {'as', 'that', 'apply', 'check', 'all', 'select', 'one', 'shown', 'on', 'your', 'be', 'must', 'if', 'yes', 'no', 'to'}
        
        if direction == 'below':
            # Find text BELOW the line (for underline-with-label-below patterns)
            candidates = []
            for word in words:
                word_x0, word_x1 = float(word['x0']), float(word['x1'])
                word_top = float(word['top'])
                
                # Word must be below the line
                y_dist = word_top - y
                if y_dist < 0 or y_dist > 12: # v21.30: Increased from 8pt to 12pt to capture 8.13pt gaps
                    continue
                
                # Word should be near the left edge
                if word_x0 > x + 100 or word_x1 < x - 10:
                    continue
                
                text = word['text'].strip()
                is_colon = text.endswith(':')
                
                # Instruction detection
                text_clean = text.lower().replace(':', '').strip('().,')
                is_instruction = (text.startswith('(') or text.endswith(')') or 
                                 text_clean in filler_words or 
                                 'apply' in text_clean or 'check' in text_clean)
                
                # Score: prefer closer (bottom) words
                # Penalty for 'below' direction to disfavor text underneath lines (+300)
                score = y_dist + (0 if is_colon else 50) + (200 if is_instruction else 0) + 300
                candidates.append((score, text, is_colon, word_top, word_x0, word_x1))
            
            if candidates:
                candidates.sort()
                best = candidates[0]
                best_text, best_is_colon, best_score, best_top, b_x0, b_x1 = best[1], best[2], best[0], best[3], best[4], best[5]
                
                # v21.23: Aggregate multi-line label BELOW
                label_lines = {}
                sorted_all_lines = sorted(self.page_lines.get(page_num, []), key=lambda l: l['top'])
                
                for word in words:
                    wt = float(word['top'])
                    # Same line (within 3pt) or line just below (within 12pt)
                    if abs(wt - best_top) < 3 or 3 <= (wt - best_top) < 12:
                        # v21.24: Boundary check - is there another form line between best_top and this word?
                        has_boundary = False
                        if wt > best_top + 3:
                            for l in sorted_all_lines:
                                l_top = float(l['top'])
                                if best_top + 1 < l_top < wt - 1:
                                    if l['x0'] <= x + 50 and l['x1'] >= x - 10:
                                        has_boundary = True
                                        break
                        if has_boundary: continue
                        
                        wx0, wx1 = float(word['x0']), float(word['x1'])
                        if wx0 <= x + 150 and wx1 >= x - 20: 
                            bucket = round(wt, 1)
                            if bucket not in label_lines: label_lines[bucket] = []
                            label_lines[bucket].append((wx0, word['text']))
                
                # Join lines top to bottom, words left to right
                all_text = []
                # v21.25: Process lines bottom-up to find better stopping point (for "If Yes" labels)
                sorted_keys = sorted(label_lines.keys(), reverse=True)
                lines_to_keep = []
                for t in sorted_keys:
                    line_words_list = sorted(label_lines[t])
                    line_text = ' '.join(lw[1] for lw in line_words_list)
                    lines_to_keep.insert(0, line_text)
                    # Stop if this line looks like a complete question or a secondary label
                    lt_clean = line_text.strip().lower()
                    if lt_clean.endswith('?') or lt_clean.startswith('if ') or lt_clean.startswith('if,'):
                        break
                    # Stop if we hit a colon on a non-starting line
                    if len(lines_to_keep) > 1 and ':' in line_text:
                        break
                    # Also limit to 3 lines max for safety
                    if len(lines_to_keep) >= 3:
                        break
                
                full_label = ' '.join(lines_to_keep).strip().rstrip(':')
                # Deduplicate adjacent repeated words (v21.28)
                words_list = full_label.split()
                clean_words = []
                for wd in words_list:
                    if not clean_words or wd != clean_words[-1]:
                        clean_words.append(wd)
                full_label = ' '.join(clean_words)
                return (full_label, best_is_colon, best_score, b_x0, b_x1)
            return None
        
        elif direction == 'above':
            # Find text above the line (for "Label:\n____" patterns)
            # v21.23: Increased max_dist for multi-line labels above
            search_max = max(max_dist, 30)
            candidates = []
            for word in words:
                word_x0, word_x1 = float(word['x0']), float(word['x1'])
                word_bottom, word_top = float(word['bottom']), float(word['top'])
                
                y_dist = y - word_bottom
                if y_dist < -2 or y_dist > search_max: # Slightly overlap line
                    continue
                
                # Word should overlap horizontally
                if word_x1 < x - 20 or word_x0 > x + 150:
                    continue
                
                text = word['text'].strip()
                is_colon = text.endswith(':')
                
                # Instruction detection
                text_clean = text.lower().strip('().,')
                is_instruction = (text.startswith('(') or text.endswith(')') or 
                                 text_clean in filler_words or 
                                 'apply' in text_clean or 'check' in text_clean)
                
                # Score: prefer closer (bottom) words
                # SIGNIFICANT PENALTY for 'above' (+300) to prefer same-line 'left' labels
                score = y_dist + (0 if is_colon else 50) + (300 if is_instruction else 0) + 300
                candidates.append((score, text, is_colon, word_top, word_x0, word_x1))
            
            if candidates:
                candidates.sort()
                best = candidates[0]
                best_text, best_is_colon, best_score, best_top, b_x0, b_x1 = best[1], best[2], best[0], best[3], best[4], best[5]
                
                # v21.23: Aggregate multi-line label ABOVE
                label_lines = {}
                sorted_all_lines = sorted(self.page_lines.get(page_num, []), key=lambda l: l['top'])
                
                for word in words:
                    wt = float(word['top'])
                    # Same line (within 3pt) or line just above (within 12pt)
                    if abs(wt - best_top) < 3 or 3 <= (best_top - wt) < 12:
                        # v21.24: Boundary check - is there another form line between wt and best_top?
                        has_boundary = False
                        if wt < best_top - 3:
                            for l in sorted_all_lines:
                                l_top = float(l['top'])
                                if wt + 1 < l_top < best_top - 1:
                                    if float(l['x0']) <= x + 50 and float(l['x1']) >= x - 10:
                                        has_boundary = True
                                        break
                        if has_boundary: continue
                        
                        wx0, wx1 = float(word['x0']), float(word['x1'])
                        if wx1 >= x - 20 and wx0 <= x + 150:
                            bucket = round(wt, 1)
                            if bucket not in label_lines: label_lines[bucket] = []
                            label_lines[bucket].append((wx0, word['text']))
                
                # Join lines top to bottom, words left to right
                all_text = []
                # v21.25: Process lines bottom-up to find better stopping point (for "If Yes" labels)
                sorted_keys = sorted(label_lines.keys(), reverse=True)
                lines_to_keep = []
                for t in sorted_keys:
                    line_words_list = sorted(label_lines[t])
                    line_text = ' '.join(lw[1] for lw in line_words_list)
                    lines_to_keep.insert(0, line_text)
                    # Stop if this line looks like a complete question or a secondary label
                    lt_clean = line_text.strip().lower()
                    if lt_clean.endswith('?') or lt_clean.startswith('if ') or lt_clean.startswith('if,'):
                        break
                    # Stop if we hit a colon on a non-starting line
                    if len(lines_to_keep) > 1 and ':' in line_text:
                        break
                    # Also limit to 3 lines max for safety
                    if len(lines_to_keep) >= 3:
                        break
                
                full_label = ' '.join(lines_to_keep).strip().rstrip(':')
                # Deduplicate adjacent repeated words (v21.28)
                words_list = full_label.split()
                clean_words = []
                for wd in words_list:
                    if not clean_words or wd != clean_words[-1]:
                        clean_words.append(wd)
                full_label = ' '.join(clean_words)
                return (full_label, best_is_colon, best_score, b_x0, b_x1)
            return None
        
        elif direction == 'left':
            # For form lines, find the best label to the left
            # Labels are typically on the same visual line or slightly above
            # IMPORTANT: Don't look BELOW the line - those are label-below patterns
            candidates = []
            for word in words:
                word_x1 = float(word['x1'])  # Right edge of word
                word_y = float(word['top'])
                
                # Word must start before the line starts (or very close)
                # v21.4: Changed to check x0 instead of x1 to allow overlapping labels
                word_x0 = float(word['x0'])
                if word_x0 > x + 5:
                    continue
                
                # Word must be AT or ABOVE the line (not below)
                # y increases downward, so word_y > y means word is below line
                if word_y > y + 3:  # Allow 3pt tolerance below line
                    continue
                    
                # Word must be on roughly the same line (within 10pt above)
                y_diff = y - word_y  # Positive if word is above line
                if y_diff > 10 or y_diff < -3:  # Above by >10pt or below by >3pt
                    continue
                
                # Prefer words ending with colon (clear labels)
                text = word['text'].strip()
                is_label = text.endswith(':')
                
                # v21.21: Filter out instructions and filler words
                text_clean = text.lower().replace(':', '').strip('().,')
                is_instruction = (text.startswith('(') or text.endswith(')') or 
                                 text_clean in filler_words or 
                                 'apply' in text_clean or 'check' in text_clean)
                
                dist = x - word_x1
                score = dist + (0 if is_label else 50) + (300 if is_instruction else 0)
                candidates.append((score, text, is_label, word_y, word_x0, word_x1))
            
            if candidates:
                # Sort by score (lower is better)
                candidates.sort()
                best = candidates[0]
                best_text, best_is_colon, best_score, best_top, b_x0, b_x1 = best[1], best[2], best[0], best[3], best[4], best[5]
                best_word = {'text': best_text, 'x0': b_x0, 'x1': b_x1, 'top': best_top}
                
                # Aggregate words to the LEFT and RIGHT to form full phrase
                phrase_words = [best_word]
                
                # Iteratively find word to the left of phrase_words[0]
                current_gap_limit = 15
                
                # Safety limit
                for _ in range(10): 
                    leftmost = phrase_words[0]
                    target_x0 = float(leftmost['x0']) 
                    target_y = float(leftmost['top'])
                    
                    found_prev = None
                    best_gap = 1000
                    
                    for word in words:
                        if word in phrase_words: continue
                        
                        wx1 = float(word['x1'])
                        wy = float(word['top'])
                        
                        # Must be on roughly same line (v21.29: tightened to 2pt)
                        if abs(wy - target_y) > 2: continue
                        
                        # Must end BEFORE the current leftmost starts
                        gap = target_x0 - wx1
                        if 0 <= gap < current_gap_limit:
                            if gap < best_gap:
                                best_gap = gap
                                found_prev = word
                                
                    if found_prev:
                        phrase_words.insert(0, found_prev)
                    else:
                        break
                
                # ALSO aggregate words to the RIGHT of the initial best_word
                # This captures "Office Phone" when "Office" was found first (before the line)
                # "Phone" may be after "Office" but still part of the label
                rightmost = phrase_words[-1]
                for _ in range(5):  # Limit right-side extension
                    rightmost = phrase_words[-1]
                    target_x1 = float(rightmost['x1'])
                    target_y = float(rightmost['top'])
                    
                    found_next = None
                    best_gap = 1000
                    
                    for word in words:
                        if word in phrase_words:
                            continue
                        
                        wx0 = float(word['x0'])
                        wy = float(word['top'])
                        
                        # Must be on roughly same line (v21.29: tightened to 2pt)
                        if abs(wy - target_y) > 2:
                            continue
                        
                        # Must start AFTER the current rightmost ends
                        gap = wx0 - target_x1
                        if 0 <= gap < current_gap_limit:
                            # Don't go past the form line start
                            if wx0 < x + 5:
                                if gap < best_gap:
                                    best_gap = gap
                                    found_next = word
                    
                    if found_next:
                        phrase_words.append(found_next)
                    else:
                        break
                    
                full_text = ' '.join(w['text'].strip() for w in phrase_words)
                
                # Final phrase processing
                # Deduplicate adjacent repeated words (v21.28)
                words_list = full_text.split()
                clean_words = []
                for wd in words_list:
                    if not clean_words or wd != clean_words[-1]:
                        clean_words.append(wd)
                label_clean = ' '.join(clean_words).strip().rstrip(':')
                return (label_clean, best_is_colon, best_score, phrase_words[0]['x0'], phrase_words[-1]['x1'])
                
        else:  # direction == 'right'
            # Find the first word to the right, preferring words on the SAME LINE or slightly BELOW
            # (labels are typically beside or below checkboxes, not above)
            candidates = []
            
            for word in words:
                word_x0 = float(word['x0'])
                word_x1 = float(word['x1'])
                word_y = float(word['top'])
                
                # Must be to the right within max_dist
                x_dist = word_x0 - x
                if not (0 < x_dist < max_dist):
                    continue
                
                # Allow Y tolerance, but score based on position relative to checkbox
                y_diff = word_y - y  # Positive if word is below, negative if above
                
                # Skip if too far vertically
                if abs(y_diff) > 20:
                    continue
                
                # Score: prefer same-line (y_diff close to 0), then below, penalize above
                # Same line (within 5pt): score 0-5
                # Below (up to 20pt): score 6-25
                # Above (up to 20pt): score 100-120 (strongly penalize)
                if abs(y_diff) <= 5:
                    score = abs(y_diff)  # 0-5
                elif y_diff > 0:
                    score = 5 + y_diff  # 6-25 for below
                else:
                    score = 100 + abs(y_diff)  # 100-120 for above (penalized)
                
                candidates.append((score, x_dist, word))
            
            if not candidates:
                return None
            
            # Sort by score (prefer same-line, then below, then above)
            candidates.sort()
            best_word = candidates[0][2]
            
            first_word = best_word['text'].strip()
            first_word_x1 = float(best_word['x1'])
            first_word_y = float(best_word['top'])
            
            # Collect ALL consecutive words that form the full label
            # Continue collecting until we hit a large gap (column boundary) or radio keyword
            label_parts = [first_word]
            next_word_start = first_word_x1
            
            for word in words:
                word_x0 = float(word['x0'])
                word_y = float(word['top'])
                
                # Same line (within 5pt vertically)
                if abs(word_y - first_word_y) > 5:
                    continue
                
                # Check gap to next word
                gap = word_x0 - next_word_start
                
                # Stop at large gaps (>100pt indicates column boundary)
                if gap > 100:
                    break
                
                # Immediately after the previous word (within reasonable gap)
                if 0 < gap < 50:  # Allow up to 50pt gap for multi-line wrapping
                    text = word['text'].strip()
                    # Don't include if it's a common radio keyword (Yes, No, etc.)
                    if text.lower() in self.RADIO_COLUMN_KEYWORDS:
                        break
                    # Skip checkbox characters
                    if text in ['■', '■■', '□', '☐', '☑', '☒']:
                        break
                    
                    # v16.32: Stop if we hit a start-of-field character
                    # This prevents "No" checkboxes from absorbing "No =Total Conditioned"
                    if any(c in text for c in ['=', '+']):
                         break

                    label_parts.append(text)
                    next_word_start = float(word['x1'])
                    # Limit to 15 words max to avoid runaway collection
                    if len(label_parts) >= 15:
                        break
            
            return ' '.join(label_parts)
        
        return None
    
    def _clean_field_name(self, text: str) -> str:
        """Clean text for use as a field name.
        
        Based on analysis of correctly-named PDFs, field names should:
        - Use SPACES between words (not underscores)
        - Remove special characters like :, -, ., (), etc.
        - Preserve the natural language of the label
        - Be truncated to reasonable length
        """
        if not text:
            return "Field"
        
        # Remove common trailing characters
        clean = text.strip().rstrip(':').rstrip('.')
        
        # Remove checkbox symbols (Unicode characters used for checkboxes)
        # ☐ ☑ ☒ □ ■ ▢ ▣ ✓ ✗ ✔ ✕ ✖ and Wingdings equivalents
        checkbox_chars = '☐☑☒□■▢▣✓✗✔✕✖\uf06f\uf0fe\uf0a3'
        for ch in checkbox_chars:
            clean = clean.replace(ch, '')
        
        # Remove special characters but KEEP spaces
        # Remove: colons, parentheses, brackets, slashes, asterisks, etc.
        clean = re.sub(r'[:\(\)\[\]\{\}/\\*#@&^%$!?<>|~`"\']', '', clean)
        
        # Replace hyphens and periods with nothing (merge words)
        # e.g., "R-Value" -> "RValue", "Sq. Ft." -> "Sq Ft"
        clean = clean.replace('-', '')
        clean = clean.replace('.', ' ')
        
        # Normalize multiple spaces to single space
        clean = re.sub(r'\s+', ' ', clean).strip()
        
        # v21.23: Strip colons and normalize multiple spaces
        clean = re.sub(r'\s+', ' ', clean).strip()
        
        # If the label is JUST a filler word, it's probably wrong
        filler_words = {'as', 'that', 'apply', 'check', 'all', 'select', 'one', 'shown', 'on', 'your', 'be', 'must', 'if', 'to'}
        if clean.lower() in filler_words:
            # Check if there's more context in the raw label?
            # For now, return "Field" as a baseline if it's truly just one filler word
            return "Field"
            
        # Truncate to reasonable length (120 chars max for descriptive labels)
        if len(clean) > 120:
            clean = clean[:120].rsplit(' ', 1)[0]  # Cut at word boundary
        
        return clean or "Field"
    
    def _generate_unique_name(self, base_name: str, page_num: int) -> str:
        """Generate a unique field name.
        
        Based on analysis of correctly-named PDFs:
        - NO page number suffix (_P##)
        - Duplicate fields get _2, _3, etc. suffix
        - First occurrence has no suffix
        """
        # Use base_name directly as the key (no page number)
        # Use base_name directly as the key (no page number)
        key = base_name
        self.column_counters[key] += 1
        count = self.column_counters[key]
        
        if count == 1:
            return base_name
        return f"{base_name}_{count}"
    
    def _get_column_header(self, x0: float, x1: float, headers_map: Dict) -> Optional[str]:
        """Get column header for a cell based on X position."""
        best_match = None
        best_overlap = 0
        
        for (hx0, hx1), header_name in headers_map.items():
            overlap = max(0, min(x1, hx1) - max(x0, hx0))
            if overlap > best_overlap:
                best_overlap = overlap
                best_match = header_name
        
        return best_match
    
    def _detect_field_format(self, header_name: Optional[str], 
                            label: Optional[str], cell_text: Optional[str] = None) -> Tuple[Optional[str], Dict]:
        """Determine field format from header/label text.
        
        Args:
            header_name: Column header text
            label: Row label text
            cell_text: Optional raw cell text to check for $ symbol
        
        Returns:
            Tuple of (format_type, format_options_dict)
        """
        check_text = ((header_name or '') + ' ' + (label or '')).lower().replace('_', ' ')
        all_text = (check_text + ' ' + (cell_text or '').lower()).replace('_', ' ')
        
        # SPECIAL CASE: If cell contains standalone "$" or "$:" it's a currency field
        if cell_text:
            stripped = cell_text.strip()
            if stripped == '$' or stripped == '$:' or stripped.startswith('$'):
                # Check if label/header indicates this should NOT be currency
                currency_exclusions = self.FORMAT_PATTERNS['currency'].get('exclude', [])
                if not any(ex in check_text for ex in currency_exclusions):
                    return 'currency', {'maxlen': 12, 'has_dollar_in_cell': True}
        
        for fmt, config in self.FORMAT_PATTERNS.items():
            # Check keywords in header/label AND cell text
            if any(kw in all_text for kw in config['keywords']):
                # Check exclusions
                if not any(ex in all_text for ex in config.get('exclude', [])):
                    opts = {'maxlen': config.get('maxlen', 0)}
                    # For currency format, track whether $ symbol is present in cell/label area
                    if fmt == 'currency':
                        # Check if $ is in the cell text, header, or label
                        has_dollar = '$' in (cell_text or '') or '$' in (header_name or '') or '$' in (label or '')
                        opts['has_dollar_in_cell'] = has_dollar
                    return fmt, opts
        
        return None, {}
    
    def _cell_overlaps_checkbox(self, page_num: int, x0: float, y0: float, 
                               x1: float, y1: float) -> bool:
        """Check if cell area significantly overlaps any checkbox."""
        for cb in self.checkbox_positions:
            if cb['page'] != page_num:
                continue
            # Calculate overlap amount
            x_overlap = min(cb['x1'], x1) - max(cb['x0'], x0)
            y_overlap = min(cb['y1'], y1) - max(cb['y0'], y0)
            # Only consider it an overlap if it's substantial (>3pt in both directions)
            if x_overlap > 3 and y_overlap > 3:
                return True
        return False
    
    def _is_skip_column(self, x0: float, skip_columns: set) -> bool:
        """Check if column should be skipped."""
        for (sx0, sx1) in skip_columns:
            if abs(x0 - sx0) < 5:
                return True
        return False
    
    def _position_has_field(self, page_num: int, x0: float, y0: float, 
                           x1: float, y1: float, debug: bool = False) -> bool:
        """Check if position already has a field. Uses 1pt tolerance."""
        TOLERANCE = 1.0  # 1pt tolerance for floating point edge cases
        for field in self.text_fields:
            if field['page'] != page_num:
                continue
            # Check for overlap with tolerance - fields must overlap by at least TOLERANCE
            # This prevents adjacent lines from being considered overlapping
            if (field['x0'] < x1 - TOLERANCE and field['x1'] > x0 + TOLERANCE and
                field['y0'] < y1 - TOLERANCE and field['y1'] > y0 + TOLERANCE):
                if debug:
                    src = field.get('source', 'unknown')
                    print(f"      OVERLAP: {field['name']} ({field['x0']:.2f}-{field['x1']:.2f}, {field['y0']:.2f}-{field['y1']:.2f}) [source={src}]")
                    print(f"               check: ({x0:.2f}-{x1:.2f}, {y0:.2f}-{y1:.2f})")
                return True
        return False
    
    # ========== OUTPUT ==========

    
    def _print_header(self):
        """Print startup header."""
        print(f"\n{'='*60}")
        print(f"  Universal PDF Fillable Detector v1.0")
        print(f"  Input:  {self.input_pdf.name}")
        print(f"  Output: {self.output_pdf.name}")
        print(f"{'='*60}\n")
    
    def _print_summary(self):
        """Print final summary."""
        print(f"\n{'='*60}")
        print(f"  SUMMARY")
        print(f"{'='*60}")
        print(f"  Checkboxes: {len(self.checkboxes)}")
        print(f"  Text fields: {len(self.text_fields)}")
        print(f"  Radio groups: {len(self.radio_groups)}")
        
        # Fields by source
        by_source = defaultdict(int)
        for f in self.text_fields:
            by_source[f.get('source', 'unknown')] += 1
        
        print(f"\n  Fields by detection source:")
        for source, count in sorted(by_source.items(), key=lambda x: -x[1]):
            print(f"    {source}: {count}")
        
        print(f"\n  Output: {self.output_pdf}")
        print(f"{'='*60}\n")

# =============================================================================
# v20: INSPECTION PASS SYSTEM
# =============================================================================

    def _extend_fields_to_line_endpoints(self):
        """
        v21.5: Extend text fields to reach form line endpoints.
        """
        if not self.text_fields:
            return
        
        # v21.7: Initialize vertical lines for divider checks
        # v31.06: Use a local map for horizontal lines to avoid KeyError: 'y'
        h_map = {} 
        page_v_lines = {}
        
        # Verticals are already cached in a simpler way or we can extract from page_lines
        for page_num in range(self.page_count):
            v_lines = []
            # v21.7: Include page_edges in extension boundary checks
            for item in (self.page_lines.get(page_num, []) + 
                         self.page_rects.get(page_num, []) + 
                         self.page_edges.get(page_num, [])):
                lx0 = float(item.get('x0', item.get('x', 0)))
                lx1 = float(item.get('x1', item.get('x', lx0) + item.get('width', 0)))
                ly0 = float(item.get('top', item.get('y0', 0)))
                ly1 = float(item.get('bottom', item.get('y1', 0)))
                
                w = abs(lx1 - lx0)
                h = abs(ly1 - ly0)
                
                if w < 3 and h > 5:
                    v_lines.append({'x': (lx0 + lx1) / 2, 'y0': ly0, 'y1': ly1})
            page_v_lines[page_num] = v_lines
        
        # v21.7: Merge collinear horizontal lines ONLY if no vertical line crosses the junction
        for page_num in range(self.page_count):
            v_lines = page_v_lines.get(page_num, [])
            all_lines = self.page_lines.get(page_num, [])
            
            # Extract horizontal lines and normalize with 'y' key
            h_lines = []
            for line in all_lines:
                height = abs(line.get('y1', line['bottom']) - line.get('y0', line['top']))
                if height < 1: # Horizontal
                    l_h = line.copy()
                    l_h['y'] = float(line['top'])
                    h_lines.append(l_h)
            
            if not h_lines:
                h_map[page_num] = []
                continue
                
            lines = sorted(h_lines, key=lambda l: (round(l['y'], 1), l['x0']))
            
            merged = []
            if lines:
                current = lines[0]
                for next_line in lines[1:]:
                    # Check if same y (within 0.5pt) and touching/overlapping (within 2.1pt)
                    if abs(next_line['y'] - current['y']) < 0.5 and next_line['x0'] <= current['x1'] + 2.1:
                        # Check if a vertical line is at this x junction
                        junction_x = next_line['x0']
                        y = current['y']
                        has_v_divider = any(
                            abs(vl['x'] - junction_x) < 2 and vl['y0'] - 2 <= y <= vl['y1'] + 2
                            for vl in v_lines
                        )
                        
                        if not has_v_divider:
                            current['x1'] = max(current['x1'], next_line['x1'])
                        else:
                            merged.append(current)
                            current = next_line
                    else:
                        merged.append(current)
                        current = next_line
                merged.append(current)
            h_map[page_num] = merged
        
        extended_count = 0
        
        for field in self.text_fields:
            page_num = field['page']
            field_source = field.get('source', '')
            
            # v21.5: Skip fields created from underscore patterns - they already have
            # the correct width from the underscore text boundaries
            if 'underscore' in field_source or 'embedded_underscore' in field_source:
                continue

            # v22.13: Don't extend fields explicitly marked as skip_snapping (e.g. table fields)
            if field.get('skip_snapping'):
                continue
            
            # v21.6: Lowered threshold to 10pt (from 80pt) to allow small fields 
            # like "City", "State", and "ZIP" to extend to their underlines.
            if field['x1'] - field['x0'] < 10:
                continue
            
            field_x0 = field['x0']
            field_x1 = field['x1']
            # v22.45: skip vertical expansion for strict_grid fields (they are already centered)
            if field.get('source') == 'strict_grid':
                continue
                
            field_y0 = field['y0']
            field_y1 = field['y1']
            field_center_y = (field_y0 + field_y1) / 2
            
            # Find matching line for this field in our normalized local map
            best_line = None
            for line in h_map.get(page_num, []):
                line_y = line['y']
                
                # Line must be within 10pt above or 15pt below field center
                y_diff = line_y - field_center_y
                if not (-10 < y_diff < 15):
                    continue
                
                # Line must contain field's x0 position (with some tolerance)
                if not (line['x0'] <= field_x0 + 10 and line['x1'] > field_x0 + 20):
                    continue
                
                # Line must extend further right than field currently does
                if line['x1'] <= field_x1 + 5:
                    continue
                
                # Found a matching line - check if it's better than previous
                if best_line is None or line['x1'] > best_line['x1']:
                    best_line = line
            
            if not best_line:
                continue
            
            # v21.42: Block image box and box entry (Comb) extension
            # Photo fields and Account boxes must maintain their precise geometry
            # v21.42: Block image box and box entry (Comb) extension
            # Photo fields and Account boxes must maintain their precise geometry
            if field.get('is_image_box') or field_source == 'box_entry' or field_source == 'split_field':
                continue

            # Check if extending would overlap with another field on same row
            potential_x1 = best_line['x1'] - 2  # Small margin from line end
            
            # v22.5: BLOCK Extension if it crosses a vertical divider (line/rect/edge)
            # This is critical for tables where dividers were missed in initial detection
            v_divs = page_v_lines.get(page_num, [])
            for div in v_divs:
                # Is divider between current field end and potential extension?
                if field_x1 - 1 <= div['x'] <= potential_x1 + 1:
                    # Does it overlap vertically with the field?
                    # v22.7: Relaxed tolerance (from 2 to 5) to catch dividers that are slightly offset
                    if div['y0'] - 5 <= field_center_y <= div['y1'] + 5:
                        # Stop extension AT the divider
                        potential_x1 = min(potential_x1, div['x'] - 3)

            # v21.42 (Restored): Label-aware extension logic
            # Scan for any text words that lie between current field end and potential extension
            # to avoid overlapping subsequent labels (e.g., City: ____ State: ____)
            words = self.page_words.get(page_num, [])
            for word in words:
                wx0 = float(word['x0'])
                # Word must be horizontally between field end and potential extension end
                if field_x1 + 2 < wx0 < potential_x1 + 2:
                    # Word must be vertically near the field center
                    wy0 = float(word['top'])
                    wy1 = float(word['bottom'])
                    # v22.6: Relaxed tolerance (12pt) to catch Page 9 labels offset from lines
                    if abs((wy0 + wy1)/2 - field_center_y) < 12:
                        # This word is a collision (label). Stop extension BEFORE it.
                        if '_' not in word['text'] and len(word['text'].strip()) > 1:
                            potential_x1 = min(potential_x1, wx0 - 5)

            would_overlap = False
            
            for other_field in self.text_fields:
                if other_field is field:
                    continue
                if other_field['page'] != page_num:
                    continue
                
                # Check if on same row (within 10pt vertically)
                if abs(other_field['y0'] - field_y0) > 10:
                    continue
                
                # Check if the other field is to the right of our current field
                if other_field['x0'] > field_x1:
                    # Would extending to potential_x1 overlap with this field?
                    if potential_x1 > other_field['x0'] - 5:
                        # Clip to just before the other field
                        potential_x1 = other_field['x0'] - 5
                        if potential_x1 <= field_x1 + 10:
                            # Not enough room to extend meaningfully
                            would_overlap = True
                            break
            
            if would_overlap:
                continue
            
            # Extend the field if we gained at least 20pt
            if potential_x1 > field_x1 + 20:
                old_width = field_x1 - field_x0
                field['x1'] = potential_x1
                new_width = potential_x1 - field_x0
                extended_count += 1
        
        if extended_count > 0:
            print(f"   Extended {extended_count} fields to form line endpoints")

    def _snap_fields_to_visual_boxes(self):
        """
        v22.12: Snap fields to surrounding visual boxes (filled rects).
        Solves "Half Height" issues where a field is detected from text/lines 
        but sits inside a larger colored box that represents the true field area.
        """
        snapped_count = 0
        candidates_for_snapping = []
        for page_num in self.page_rects:
            rects = self.page_rects.get(page_num, [])
            # Filter for likely field background boxes:
            # - height > 12pt (relaxed from 18pt)
            # - width > 20pt
            # - width < 600pt (Exclude giant header/page background rects)
            boxes = [r for r in rects if 12 < r.get('height', 0) < 500 and 20 < r.get('width', 0) < 600]

            # v22.22: Add curves as potential boxes if they look like large rects
            for c in self.page_curves.get(page_num, []):
                if c.get('width', 0) > 100 and c.get('height', 0) > 20:
                    boxes.append(c)
            # Page 1 blocks often don't have explicit fill/stroke in pdfplumber if they are just clipping paths or white
            # Let's be less strict about fill/stroke for snapping if it's a large rect
            boxes = [r for r in boxes if (r.get('fill') or r.get('stroke')) or (r.get('width', 0) > 100)]
            
            if not boxes:
                print(f"   [DEBUG_Snap] Page {page_num}: 0 candidate boxes found out of {len(rects)} total rects")
                continue
            
            print(f"   [DEBUG_Snap] Page {page_num}: {len(boxes)} candidate boxes found")
            
            for field in self.text_fields:
                if field['page'] != page_num:
                    continue
                # v22.12: Don't re-snap fields that were already snapped
                if field.get('snapped_to_box'):
                    continue
                
                # v22.13: Don't snap fields explicitly marked as skip_snapping (e.g. table fields)
                if field.get('skip_snapping'):
                    continue
                
                # Skip fields that are already tall
                if field['y1'] - field['y0'] > 25:
                   continue
                
                # Skip Checkboxes
                if field.get('format') == 'checkbox':
                    continue

                fx0, fy0, fx1, fy1 = field['x0'], field['y0'], field['x1'], field['y1']
                f_mid_x = (fx0 + fx1) / 2
                f_mid_y = (fy0 + fy1) / 2
                f_h = fy1 - fy0
                
                best_box = None
                
                for box in boxes:
                    bx0 = float(box['x0'])
                    bx1 = bx0 + float(box['width'])
                    
                    # v22.31: Maintain TOP-DOWN coordinates (Visual)
                    by0 = float(box['top'])
                    by1 = float(box['bottom'])
                    
                    b_h = by1 - by0

                    # SAFETY CHECK FOR GENERIC FIX:
                    # Don't snap small fields (<25pt) to huge section boxes (>60pt)
                    if f_h < 25 and b_h > 60:
                        continue
                    
                    # Check containment with relaxed tolerance
                    if (bx0 - 15 <= fx0 and bx1 + 15 >= fx1 and
                        by0 - 5 <= fy0 and by1 + 5 >= fy1):
                         
                        # Check rough alignment - field center must be within box
                        # v22.21: Relaxed tolerance for lines at the very bottom of the box
                        if not (by0 - 5 <= f_mid_y <= by1 + 2):
                            continue
                        
                        # Found a candidate box
                        # Store candidate
                        candidates_for_snapping.append({
                            'field': field,
                            'box': (by0, by1, bx0, bx1),
                            'box_id': f"{page_num}_{by0}_{bx0}" # Unique ID for box
                        })
                        break
        
        # Process candidates
        # Group by box_id
        box_groups = {}
        for c in candidates_for_snapping:
            bid = c['box_id']
            if bid not in box_groups:
                box_groups[bid] = []
            box_groups[bid].append(c)
            
        for bid, group in box_groups.items():
            # If multiple fields map to same box, it's likely a section background
            # UNLESS the fields are small and the box is huge? 
            # Or maybe we just skip snapping to avoid merging fields
            if len(group) > 1:
                # print(f"DEBUG SNAP: Skipping box {bid} with {len(group)} fields (likely section bg)")
                continue
                
            # If unique mapping, snap it
            c = group[0]
            field = c['field']
            by0, by1, bx0, bx1 = c['box']
            fy0, fy1 = field['y0'], field['y1']
            
            new_h = by1 - by0
            old_h = fy1 - fy0
            
            if new_h > old_h + 5:
                field['y0'] = by0 + 2
                field['y1'] = by1 - 2
                field['snapped_to_box'] = True
                snapped_count += 1

        if snapped_count > 0:
            print(f"   Snapped {snapped_count} fields to visual background boxes")

    def _infer_missing_table_rows_from_labels(self):
        """
        Pass 6E: Infer missing table rows by aligning label text with input columns.
        Generic fix for tables where grid lines are missing/partial.
        Uses Left-Alignment Heuristic: If Text A has a field, and Text B (same alignment) doesn't, create one.
        
        v22.76: DISABLED - This function was creating garbage fields over existing text content.
        The "inference" approach doesn't work well with complex table layouts where cells
        contain descriptive text rather than form inputs.
        """
        # DISABLED - causing too many false positives over text content
        print("   [DISABLED] Skipping inferred table row creation (v22.76)")
        return
        
        import pdfplumber
        inferred_count = 0
        
        try:
            with pdfplumber.open(self.input_pdf) as pdf:
                for page_num, page in enumerate(pdf.pages):
                    # Filter processing to typical form pages (skip extremely dense text pages?)
                    # For now just process.
                    
                    page_fields = [f for f in self.text_fields if f['page'] == page_num]
                    if not page_fields:
                        continue
                        
                    words = page.extract_words()
                    
                    # v22.75: Skip extremely text-dense pages (instruction/info pages, not forms)
                    # Pages with >800 words are likely instruction pages, not form pages
                    if len(words) > 800:
                        continue
                    
                    # 1. Map Text Rows -> Existing Fields
                    # Find text lines that effectively "own" a field to their right.
                    
                    # Sort words by Y
                    # Group words into lines (simple Y clustering)
                    lines = []
                    current_line = []
                    last_y = -999
                    words_sorted = sorted(words, key=lambda w: float(w['top']))
                    
                    for w in words_sorted:
                        mid_y = (float(w['top']) + float(w['bottom'])) / 2
                        if abs(mid_y - last_y) > 5: # New line threshold
                            if current_line:
                                lines.append(current_line)
                            current_line = []
                            last_y = mid_y
                        current_line.append(w)
                    if current_line:
                        lines.append(current_line)

                    # Analyze lines
                    # Valid Line: Has Text + Has Field to right (same Y, > X)
                    
                    aligned_lines_with_fields = {} # x0 -> count
                    input_col_x_ranges = {} # label_x0 -> [field_x0_list]
                    
                    line_info = [] # Store metadata for pass 2
                    
                    for line in lines:
                        # Get line bounds
                        l_x0 = min(float(w['x0']) for w in line)
                        l_x1 = max(float(w['x1']) for w in line)
                        l_y_mid = sum((float(w['top']) + float(w['bottom']))/2 for w in line) / len(line)
                        text_str = " ".join(w['text'] for w in line)
                        
                        # Look for field to the right
                        # Field must be roughly same Y, and to right
                        has_field = False
                        field_x_start = 0
                        
                        for f in page_fields:
                             fy_mid = (f['y0'] + f['y1']) / 2
                             # Invert Y for pdfplumber (page height - y)
                             # Wait, pdfplumber coords (top-down) were used for `lines`.
                             # self.text_fields using bottom-up.
                             # Need conversion.
                             ph = self.page_heights.get(page_num, 792)
                             fy_mid_topdown = ph - fy_mid
                             
                             if abs(fy_mid_topdown - l_y_mid) < 10:
                                 # Vertically aligned.
                                 if f['x0'] > l_x1 + 10: # To the right
                                     has_field = True
                                     field_x_start = f['x0']
                                     field_width = f['x1'] - f['x0']
                                     field_height = f['y1'] - f['y0']
                                     break
                        
                        l_y1 = max(float(w['bottom']) for w in line) # Top-Down Bottom
                        
                        line_info.append({
                            'text': text_str,
                            'x0': l_x0,
                            'y_mid': l_y_mid,
                            'y1': l_y1,
                            'has_field': has_field,
                            'field_x': field_x_start,
                            'field_w': field_width if has_field else 0,
                            'field_h': field_height if has_field else 0
                        })
                        
                        if has_field:
                            # Record this alignment
                            # Round X0 to nearest 5pt to group indents
                            rounded_x0 = round(l_x0 / 5) * 5
                            aligned_lines_with_fields[rounded_x0] = aligned_lines_with_fields.get(rounded_x0, 0) + 1
                            if rounded_x0 not in input_col_x_ranges:
                                input_col_x_ranges[rounded_x0] = []
                            input_col_x_ranges[rounded_x0].append(field_x_start)

                    # 2. Find Missing Rows
                    # Look for lines with NO field, whose X0 matches a "productive" alignment
                    
                    for info in line_info:
                        if info['has_field']:
                            continue
                            
                        # Check alignment
                        rounded_x0 = round(info['x0'] / 5) * 5
                        
                        # v22.31: Consistent suppression with _cleanup_field_boundaries
                        label_text = info['text']
                        bad_labels = ["A.", "B.", "C.", "D.", "Program", "Application", "Date Submitted", "v1.0", "Page",
                                      "Distributor Information", "General Program Design", "Program Application", 
                                      "HVAC Measure Type", "Efficiency Rating", "Quantity of Units Sold", 
                                      "Sales Projections", "Projections in New", "Projections in New York", 
                                      "2018 Sales Projections", "2017 Sales Totals"]
                        if any(label_text.strip().startswith(bl) for bl in bad_labels) or label_text.strip() in ["A", "B", "C", "D"]:
                            continue

                        # P1 blocks are long instructions; P2 rows are short.

                        char_limit = 250
                        if len(label_text) > char_limit:
                            continue

                        # v22.75: Filter garbage labels that create noise fields
                        # Skip lines containing checkbox symbols (■■ patterns in form text)
                        if '■' in label_text:
                            continue
                        
                        # Skip lines starting with continuation words (not field labels)
                        continuation_words = ('and ', 'or ', 'the ', 'for ', 'to ', 'with ', 'a ', 'an ', 'by ', 'from ')
                        if label_text.strip().lower().startswith(continuation_words):
                            continue
                        
                        # Skip if the line looks like an instruction (has sentence structure)
                        # Indicators: ends with period, has "you", "your", "will", "can"
                        instruction_words = ['you ', 'your ', 'will ', 'can ', 'may ', 'should ', 'must ']
                        if any(iw in label_text.lower() for iw in instruction_words):
                            continue

                        # Identify candidate:
                        # - Matches a productive alignment (e.g. > 1 existing fields with this indent)
                        # - Input column is clearly defined
                        
                        if aligned_lines_with_fields.get(rounded_x0, 0) >= 2:
                            # Candidate!
                            # Determine where to put the field
                            # Use median X of existing fields for this alignment
                            target_xs = input_col_x_ranges[rounded_x0]
                            target_x0 = sorted(target_xs)[len(target_xs)//2]
                            
                            # Determine width/height
                            # Use typical width/height
                            target_w = 140 # Default
                            target_h = 14
                            
                            # v22.28: Standardize to TOP-DOWN internally.
                            # Conversion to Bottom-Up happens in _create_fillable_v1.
                            y_center = info['y_mid']
                            
                            new_y0 = y_center - (target_h / 2)
                            new_y1 = y_center + (target_h / 2)
                            
                            # v22.75: Global header protection - skip header area on ALL pages
                            # Page headers are typically y < 80 (top-down coords)
                            if new_y0 < 80:
                                continue
                            
                            # Final overlap check: Don't create if covered
                            is_overlap = False
                            for f in page_fields:
                                if f['page'] == page_num:
                                     # strict overlap
                                     if not (f['x1'] < target_x0 or f['x0'] > target_x0 + target_w or f['y1'] < new_y0 or f['y0'] > new_y1):
                                          is_overlap = True
                                          break
                            if is_overlap:
                                continue

                            # Generic Inference Logic (Page-Agnostic)
                            # Determine if this is a "Quantitative" field (right-side, short) or "Narrative" field (wide)
                            
                            label_lower = label_text.strip().lower()
                            
                            # 1. Quantitative/Value Keywords (e.g. HVAC ratings, counts)
                            # Typically short fields on the right side
                            quant_keywords = ["seer", "eer", "hspf", "sales", "quantity", "rating", "units", "gallons", "factor", "pump", "heater", "fan", "system", "thermostat", "ecobee", "nest", "honeywell", "qualifying", "units sold", "amount", "total", "cost", "price"]
                            is_quant = any(k in label_lower for k in quant_keywords)
                            
                            # 2. Narrative/Descriptive Keywords (e.g. "Describe", "Indicate")
                            # Typically wide fields
                            narrative_keywords = ["brands", "describe", "addresses", "indicate", "explanation", "please list"]
                            is_narrative = any(k in label_lower for k in narrative_keywords)
                            
                            # Filter: inferred fields must match one of these types to avoid noise
                            if not is_quant and not is_narrative:
                                continue
                                
                            # Surgical Exclusion: Kill if "please" or "participate" is in the text (instructions)
                            if any(k in label_lower for k in ['participate', 'anticipate']):
                                continue
                                
                            # Geometric Logic:
                            # Verify layout matches expectation
                            if is_quant:
                                # Quantitative fields usually right-aligned or specific columns
                                # If it's on the far left, it might be a label for a row, but if we are here, there is NO field.
                                # Let's assume standard sizing.
                                target_w = 140
                                target_h = 14
                            elif is_narrative:
                                # Narrative fields should be wide
                                target_w = 540
                                # Move field BELOW the text for narrative prompts
                                if len(label_text) > 20:
                                    new_y0 = info['y1'] + 2
                                    target_h = 24
                                    new_y1 = new_y0 + target_h
                                    # Correction: start X at left margin for narrative
                                    target_x0 = 36 # approx left margin



                            # Generic narrative suppression: skip long sentence-like labels
                            # Prevents inferred fields over paragraph instructions.
                            label_words = label_text.split()
                            if (len(label_text) > 60 and ('.' in label_text or len(label_words) > 12)):
                                continue

                            # Create
                            fname = self._generate_unique_name(f"Inferred_{info['text'][:20]}", page_num)
                            self.text_fields.append({
                                'page': page_num,
                                'x0': target_x0,
                                'y0': new_y0,
                                'x1': target_x0 + target_w,
                                'y1': new_y1,
                                'name': fname,
                                'label': info['text'],
                                'format': 'text',
                                'format_opts': {},
                                'source': 'inferred_table_row'
                            })
                            inferred_count += 1
                            
        except Exception as e:
            print(f"   Warning: Could not infer table rows: {e}")
            
        if inferred_count > 0:
            print(f"   Inferred {inferred_count} missing table fields based on label alignment")

    def _expand_fields_vertically_based_on_spacing(self):
        """
        Pass 6F: Expand fields downwards to fill empty vertical space.
        Generic fix for "Half Height" fields.
        """
        expanded_count = 0
        for page_num in range(10): # Arbitrary max page? No, use detected pages
             # Get fields on this page sorted by Y (top to bottom inverted? No, y0 is bottom)
             # Sort by Y-top (y1) DESCENDING (Top to Bottom visual)
             fields = [f for f in self.text_fields if f['page'] == page_num]
             fields.sort(key=lambda f: f['y1'], reverse=True)
             
             for i, field in enumerate(fields):
                 # Skip checkboxes
                 if field.get('format') == 'checkbox':
                     continue
                 
                 # Check if recently snapped (don't mess with snapped fields)
                 if field.get('snapped_to_box'):
                     continue
                 
                 # Look for next field below (visually below)
                 # Must overlap horizontally significantly
                 fx0, fx1 = field['x0'], field['x1']
                 fy0 = field['y0']
                 
                 closest_below_y = -1
                 
                 # Scan for field below
                 for other in fields[i+1:]:
                     ox0, ox1 = other['x0'], other['x1']
                     oy1 = other['y1']
                     
                     # Check horizontal overlap
                     interaction = min(fx1, ox1) - max(fx0, ox0)
                     if interaction > 0:
                         # Found field below.
                         closest_below_y = oy1
                         break
                 
                 # If no field below, limit is page bottom margin (e.g. 50)
                 if closest_below_y == -1:
                     closest_below_y = 50
                 
                 # Calculate Gap
                 gap = fy0 - closest_below_y
                 
                 # If Gap is reasonable (e.g. 5pt to 30pt), expand!
                 # If Gap is huge, it's a section break, don't expand too much.
                 # Max expansion: 15pt?
                 
                 field_h = field['y1'] - field['y0']
                 
                 if 5 < gap < 30:
                     # Expand to fill gap, leaving padding
                     # New y0 = closest_below_y + 4
                     new_y0 = closest_below_y + 4
                     if new_y0 < fy0:
                        field['y0'] = new_y0
                        field['expanded_vertically'] = True
                        expanded_count += 1
                        
        if expanded_count > 0:
             print(f"   Vertically expanded {expanded_count} fields to fill gaps")

    def _split_fields_on_separators(self):
        """
        Pass 5C: Split fields that are intersected by separator characters like -, =, x, +.
        This handles cases where a single form line covers multiple logical fields separated by text.
        """
        separators = ['-', '=', 'x', '+', '/', '÷', '–', '—']
        fields_to_add = []
        fields_to_remove = set()
        
        # Debug file
        
        # Debug file removed

        
        for i, field in enumerate(self.text_fields):
            page_num = field['page']
            
            # Only check likely candidates (wide fields)
            if field['x1'] - field['x0'] < 40:
                continue
            # Skip if already removed
            if i in fields_to_remove:
                continue
                
            # v33.82: Protect embedded_underscore fields from further splitting
            if field.get('source') == 'embedded_underscore':
                continue

            page_num = field['page']
            words = self.page_words.get(page_num, [])
            
            # Find separators inside this field
            field_separators = []
            for word in words:
                text = word['text'].strip()
                if text not in separators:
                    continue
                
                # Check overlap
                wx0, wx1 = float(word['x0']), float(word['x1'])
                wy0, wy1 = float(word['top']), float(word['bottom'])
                
                # Separator must be horizontally INSIDE the field (with some buffer)
                if wx0 > field['x0'] + 10 and wx1 < field['x1'] - 10:
                    # Vertical alignment (looser tolerance)
                    fy_center = (field['y0'] + field['y1']) / 2
                    wy_center = (wy0 + wy1) / 2
                    if abs(fy_center - wy_center) < 15:
                        field_separators.append((wx0, wx1, text))
            
            if not field_separators:
                continue
                
            # Sort separators x
            field_separators.sort(key=lambda x: x[0])
            
            # Split field
            current_x = field['x0']
            original_label = field.get('label')
            
            created_fields = []
            
            for sx0, sx1, sep_char in field_separators:
                # Segment from current_x to sx0
                seg_width = sx0 - current_x
                if seg_width > 15: # Min width
                    new_field = field.copy() # Copy props
                    new_field['x0'] = current_x
                    new_field['x1'] = sx0 - 3 # Padding
                    new_field['name'] = self._generate_unique_name("Field", page_num) # Re-generate name
                    new_field['source'] = 'split_field' # Protect from re-merge/re-extend
                    # Preserve label for first segment only
                    if not created_fields and original_label:
                        new_field['label'] = original_label
                    else:
                        new_field['label'] = None
                        
                    created_fields.append(new_field)
                
                current_x = sx1 + 3 # Padding
            
            # Last segment
            if field['x1'] - current_x > 15:
                new_field = field.copy()
                new_field['x0'] = current_x
                new_field['x1'] = field['x1']
                new_field['name'] = self._generate_unique_name("Field", page_num)
                new_field['source'] = 'split_field' # Protect
                if not created_fields and original_label:
                    new_field['label'] = original_label
                elif created_fields:
                     new_field['label'] = None
                created_fields.append(new_field)
                
            if len(created_fields) > 1:
                fields_to_add.extend(created_fields)
                fields_to_remove.add(i)
        # Update fields
        if fields_to_remove:
            self.text_fields = [f for i, f in enumerate(self.text_fields) if i not in fields_to_remove]
            self.text_fields.extend(fields_to_add)

    def _merge_adjacent_fields(self):
        """
        Merge adjacent text fields that are likely parts of the same broken line.
        
        Refines detection by combining fields that:
        1. Are on the same page
        2. Are vertically aligned (same Y)
        3. Are horizontally close (small gap)
        4. Have compatible names/labels (or one is generic)
        """
        if not self.text_fields:
            return
            
        # Group detection by page
        by_page = defaultdict(list)
        for i, f in enumerate(self.text_fields):
            f['_original_index'] = i
            by_page[f['page']].append(f)
            
        # v21.7: Use structural cache for gap-aware merging
        page_v_dividers = {}
        for page_num in range(self.page_count):
            # v22.5: Include page_edges in gap divider checks
            page_v_dividers[page_num] = (self.page_rects.get(page_num, []) + 
                                         self.page_lines.get(page_num, []) + 
                                         self.page_edges.get(page_num, []))
            # Filter for verticality
            v_divs = []
            for item in page_v_dividers[page_num]:
                w = item.get('width', item.get('x1', 0) - item.get('x0', 0))
                h = item.get('height', abs(item.get('bottom', 0) - item.get('top', 0)))
                if w < 3 and h > 5:
                    v_divs.append({'x': float(item.get('x0', item.get('x', 0))), 'y0': float(item['top']), 'y1': float(item['bottom'])})
            page_v_dividers[page_num] = v_divs
            
        merged_count = 0
        final_fields = []
        
        for page_num in sorted(by_page.keys()):
            page_fields = by_page[page_num]
            # Sort by Y then X
            page_fields.sort(key=lambda f: (round(f['y0'], 1), f['x0']))
            
            merged = []
            if not page_fields:
                continue
                
            current = page_fields[0]
            
            for next_field in page_fields[1:]:
                # Check if mergeable with current
                # 1. Vertically aligned (bottoms match within 2pt)
                y_aligned = abs(current['y1'] - next_field['y1']) < 2
                
                # 2. Horizontally close (gap < 10pt)
                gap = next_field['x0'] - current['x1']
                x_close = -2 <= gap <= 10  # Allow slight overlap (-2) or small gap (+10)
                
                # 3. Source type check - DO NOT merge table cells or protected fields
                do_not_merge_sources = ['table', 'table_cell', 'table_data_row', 'box_entry', 'colon_cell', 'image_box', 'empty_box', 'underscore', 'embedded_underscore', 'general_underscore', 'label_below_underscore', 'split_field']
                current_in_protected = (current.get('source') in do_not_merge_sources or current.get('is_image_box'))
                next_in_protected = (next_field.get('source') in do_not_merge_sources or next_field.get('is_image_box'))
                src_compatible = not (current_in_protected or next_in_protected)
                
                # 4. Dimension protection - DO NOT merge large boxes (tall fields)
                is_large_box = (current['y1'] - current['y0']) > 25 or (next_field['y1'] - next_field['y0']) > 25
                
                # 5. Divider protection - DO NOT merge if a vertical divider exists in the gap
                has_gap_divider = False
                if x_close and gap > 1:
                    mid_y = (current['y0'] + current['y1']) / 2
                    v_divs = page_v_dividers.get(page_num, [])
                    for div in v_divs:
                        if (current['x1'] - 1 <= div['x'] <= next_field['x0'] + 1 and
                            div['y0'] - 2 <= mid_y <= div['y1'] + 2):
                            has_gap_divider = True
                            break

                if y_aligned and x_close and src_compatible and not is_large_box and not has_gap_divider:
                    # MERGE
                    # Extend width
                    current['x1'] = max(current['x1'], next_field['x1'])
                    
                    # Update name/label if current is generic and next is specific
                    curr_is_generic = 'Field' in current['name'] or 'Other' in current['name']
                    next_is_specific = 'Field' not in next_field['name'] and 'Other' not in next_field['name']
                    
                    if curr_is_generic and next_is_specific:
                        current['name'] = next_field['name']
                        current['label'] = next_field['label']
                    
                    merged_count += 1
                else:
                    # Push current and start new
                    merged.append(current)
                    current = next_field
            
            merged.append(current)
            final_fields.extend(merged)
            
        self.text_fields = final_fields
        if merged_count > 0:
            print(f"   🧩 Merged {merged_count} fragmented field segments")

    def _get_field_rank(self, f_type, f_data):
        """Calculate dynamic rank for a field."""
        source = f_data.get('source', '')
        base_rank = self.SOURCE_RANKS.get(source, 0)
        
        # v33.43: Dynamic Rank Adjustments
        if f_type == 'cb':
            # v33.52: Protected labels for real checkboxes
            label = (f_data.get('label') or f_data.get('name') or '').lower()
            protected_cbs = ['yes', 'no', 'complete', 'satisfactory', 'other:', 'agree', 'contractor', 'retailer']
            
            if any(p in label for p in protected_cbs):
                base_rank += 50
            else:
                anti_kws = ['apt', 'unit', 'suite', 'city', 'state', 'zip', 'no.', 'number', 'amount', 'manufacturer', 'model #', 'serial #', 'license']
                for kw in anti_kws:
                    if kw in label:
                        base_rank -= 600 # Sink it!
                        break
            
            text_only_headers = ['account number', 'account holder'] # Removed manufacturer/model as they overlap Yes/No columns
            if any(h in label for h in text_only_headers):
                base_rank -= 300 # Lower rank so text field can win
                
        # If a text field is from a table and has a specific label, boost it
        if f_type == 'text' and source in ['strict_grid', 'table']:
            if f_data.get('label') and 'Field' not in f_data.get('name', ''):
                base_rank += 20
                
        return base_rank

    def _resolve_all_conflicts(self):
        """
        RESOLVE ALL CONFLICTS (Tiered Engine)
        
        Every field (Text or Checkbox) is assigned a RANK based on its detection source.
        If two fields overlap significantly (>40%), the one with higher rank WINS.
        If ranks are equal, the WIDER one wins.
        """
        # 1. Merge Text fields that should be unified first
        # We call this manually because Pass 6 was replaced by Pass 6 (Tiered)
        self._merge_adjacent_fields()
        
        all_fields = []
        # Wrap text fields
        for i, f in enumerate(self.text_fields):
            all_fields.append({'type': 'text', 'data': f, 'rank': self._get_field_rank('text', f)})
        # Wrap checkboxes
        for i, c in enumerate(self.checkboxes):
            all_fields.append({'type': 'cb', 'data': c, 'rank': self._get_field_rank('cb', c)})
            
        if not all_fields: return
        
        print(f"   🛡️ Conflict Resolver starting with {len(self.text_fields)} Tx, {len(self.checkboxes)} CB")
        # DEBUG: Count image boxes BEFORE conflict resolution
        pre_ib_count = sum(1 for f in self.text_fields if f.get('is_image_box'))
        from collections import Counter
        pre_page_counts = Counter(f['page'] for f in self.text_fields if f.get('is_image_box'))
        print(f"   [DEBUG BEFORE] Image boxes: {pre_ib_count} total, per page: {dict(sorted(pre_page_counts.items()))}")
        indices_to_remove = set()
        
        # Group by page to optimize
        by_page = defaultdict(list)
        for i, f_ext in enumerate(all_fields):
            by_page[f_ext['data']['page']].append((i, f_ext))
            
        for page_num, page_fields in by_page.items():
            for i, (idx_a, f_a) in enumerate(page_fields):
                if idx_a in indices_to_remove: continue
                
                # Get geometry for A
                if f_a['type'] == 'text':
                    ax0, ay0, ax1, ay1 = f_a['data']['x0'], f_a['data']['y0'], f_a['data']['x1'], f_a['data']['y1']
                else:
                    d = f_a['data']
                    ax0, ay0, ax1, ay1 = d['x'], d['y'], d['x'] + d['width'], d['y'] + d['height']
                
                for j, (idx_b, f_b) in enumerate(page_fields):
                    if j <= i or idx_b in indices_to_remove: continue
                    
                    # Get geometry for B
                    if f_b['type'] == 'text':
                        bx0, by0, bx1, by1 = f_b['data']['x0'], f_b['data']['y0'], f_b['data']['x1'], f_b['data']['y1']
                    else:
                        d = f_b['data']
                        bx0, by0, bx1, by1 = d['x'], d['y'], d['x'] + d['width'], d['y'] + d['height']
                    
                    # Check for significant overlap
                    inter_x0 = max(ax0, bx0)
                    inter_y0 = max(ay0, by0)
                    inter_x1 = min(ax1, bx1)
                    inter_y1 = min(ay1, by1)
                    
                    if inter_x1 > inter_x0 and inter_y1 > inter_y0:
                        inter_area = (inter_x1 - inter_x0) * (inter_y1 - inter_y0)
                        # v33.47: Overlap Threshold Calibration
                        area_a = (ax1 - ax0) * (ay1 - ay0)
                        area_b = (bx1 - bx0) * (by1 - by0)
                        
                        thresh = 0.4
                        # v35.3: FIX - CB vs CB should NEVER trigger Force-Conflict removal
                        # Two overlapping checkboxes are duplicates, not conflicts
                        if f_a['type'] == 'cb' and f_b['type'] == 'cb':
                            # CB vs CB: Deduplicate - keep ONE, prefer character detection
                            overlap_a = inter_area / area_a
                            overlap_b = inter_area / area_b
                            if max(overlap_a, overlap_b) > 0.5:  # 50%+ overlap = duplicate
                                # Prefer character detection > curve detection > vector detection
                                source_priority = {'char': 3, 'curve': 2, 'vector': 1, 'square_o': 0}
                                prio_a = source_priority.get(f_a['data'].get('source', ''), 0)
                                prio_b = source_priority.get(f_b['data'].get('source', ''), 0)
                                if prio_a >= prio_b:
                                    indices_to_remove.add(idx_b)
                                else:
                                    indices_to_remove.add(idx_a)
                                    break
                                continue
                            else:
                                # Not enough overlap - not duplicates, keep both
                                continue
                                
                        elif f_a['type'] == 'cb' or f_b['type'] == 'cb':
                            # Checkboxes and text fields often collide at borders.
                            # If a checkbox is INSIDE a larger text field, it's likely noise or a bullet.
                            overlap_a = inter_area / area_a
                            overlap_b = inter_area / area_b
                            
                            if max(overlap_a, overlap_b) > 0.15: # Significant collision
                                # If one is a checkbox and the other is a text field, 
                                # and the checkbox is > 80% contained in the text field, 
                                # we should ALWAYS treat it as a conflict.
                                if (f_a['type'] == 'cb' and f_b['type'] == 'text' and overlap_a > 0.8) or \
                                   (f_b['type'] == 'cb' and f_a['type'] == 'text' and overlap_b > 0.8):
                                    thresh = 0.05 # Aggressive removal - CB inside text field
                                    name_a = f_a['data'].get('name') or f_a['data'].get('label', 'Unnamed')
                                    name_b = f_b['data'].get('name') or f_b['data'].get('label', 'Unnamed')
                                    print(f"      [TRACER] Force-Conflict: CB contained in Text. {name_a}({f_a['type']}) vs {name_b}({f_b['type']})")
                                else:
                                    thresh = 0.15
                            
                        if inter_area > thresh * min(area_a, area_b):
                            # v33.62: Radio group children are immune AND actively kill conflicting text fields
                            # v35.6: EXCEPT when the text field is an IMAGE BOX!
                            # Image boxes should never be removed by any checkbox, even radio children
                            
                            # If A is radio child, remove B (text) - UNLESS B is an image box
                            if f_a['data'].get('is_radio_group_child'):
                                if f_b['type'] == 'text' and f_b['data'].get('is_image_box'):
                                    # Don't remove image box - continue to check next rule
                                    print(f"      [TRACER] Radio Child vs Image Box: PROTECTING Image Box B from Radio Child A")
                                else:
                                    print(f"      [TRACER] Conflict Resolved: Radio Child {f_a['data'].get('name', 'Unnamed')} WINS vs Text {f_b['data'].get('name', 'Unnamed')}")
                                    indices_to_remove.add(idx_b)
                                    continue
                            
                            # If B is radio child, remove A (text) - UNLESS A is an image box
                            if f_b['data'].get('is_radio_group_child'):
                                if f_a['type'] == 'text' and f_a['data'].get('is_image_box'):
                                    # Don't remove image box - continue to check next rule
                                    print(f"      [TRACER] Radio Child vs Image Box: PROTECTING Image Box A from Radio Child B")
                                else:
                                    print(f"      [TRACER] Conflict Resolved: Radio Child {f_b['data'].get('name', 'Unnamed')} WINS vs Text {f_a['data'].get('name', 'Unnamed')}")
                                    indices_to_remove.add(idx_a)
                                    break # A is removed, stop processing A

                            # v35.2: Image boxes WIN against checkboxes inside them
                            # Image boxes are large upload areas that may contain placeholder icons that
                            # get incorrectly detected as checkboxes. Image boxes should always win.
                            # v35.3: ONLY remove checkbox if it's mostly INSIDE the image box (80%+)
                            # This prevents checkboxes near image boxes from being incorrectly removed
                            if f_a['data'].get('is_image_box') and f_b['type'] == 'cb':
                                cb_overlap = inter_area / area_b  # How much of checkbox is inside image box
                                if cb_overlap > 0.8:  # Checkbox is 80%+ inside image box
                                    ib_name = f_a['data'].get('name', 'Unknown')
                                    ib_page = f_a['data'].get('page', '?')
                                    print(f"      [TRACER] Conflict Resolved: Image Box A ({ib_name} pg={ib_page}) WINS vs Checkbox B ({cb_overlap*100:.0f}% inside)")
                                    indices_to_remove.add(idx_b)
                                    continue
                                # If less than 80%, DON'T remove the checkbox
                            if f_b['data'].get('is_image_box') and f_a['type'] == 'cb':
                                cb_overlap = inter_area / area_a  # How much of checkbox is inside image box
                                if cb_overlap > 0.8:  # Checkbox is 80%+ inside image box
                                    print(f"      [TRACER] Conflict Resolved: Image Box B WINS vs Checkbox A ({cb_overlap*100:.0f}% inside)")
                                    indices_to_remove.add(idx_a)
                                    break
                                # If less than 80%, DON'T remove the checkbox

                            # v33.66: ALL Checkboxes WIN against Text Fields (except image boxes handled above)
                            # Type values are 'cb' and 'text' (not 'checkbox' and 'text_content')
                            # v35.4: FIX - SKIP this rule if the text field is an image box!
                            # Image boxes should NOT be removed by checkboxes, even if the checkbox is only
                            # partially inside. The image box detection logic is reliable.
                            if f_a['type'] == 'cb' and f_b['type'] == 'text':
                                if f_b['data'].get('is_image_box'):
                                    # Don't remove image box - skip to keep both
                                    print(f"      [TRACER] PROTECTING Image Box B from Checkbox A")
                                    continue
                                else:
                                    print(f"      [TRACER] Conflict Resolved: Checkbox A WINS vs Text B (removing text field)")
                                    indices_to_remove.add(idx_b)
                                    continue
                            if f_b['type'] == 'cb' and f_a['type'] == 'text':
                                is_ib = f_a['data'].get('is_image_box')
                                name = f_a['data'].get('name', 'Unknown')
                                pg = f_a['data'].get('page', '?')
                                # Always debug when is_image_box is True
                                if is_ib:
                                    print(f"      [DEBUG-IB-FOUND] Checkbox vs Text: A='{name}' pg={pg} is_image_box={is_ib}")
                                if is_ib:
                                    # Don't remove image box - skip to keep both
                                    print(f"      [TRACER] PROTECTING Image Box A from Checkbox B")
                                    continue
                                else:
                                    # Show which field is being removed
                                    print(f"      [TRACER] Conflict Resolved: Checkbox B WINS vs Text A (removing text field '{name}' pg={pg}, is_ib={is_ib})")
                                    indices_to_remove.add(idx_a)
                                    break

                            # CONFLICT DETECTED!
                            print(f"      [TRACER] Conflict Detected! A:{f_a.get('name', 'Unnamed')}({f_a.get('type')}:{f_a.get('source')}) vs B:{f_b.get('name', 'Unnamed')}({f_b.get('type')}:{f_b.get('source')})")
                            
                            # v35.5: PROTECT IMAGE BOXES - they should NEVER be removed by checkboxes
                            # regardless of rank. Image box detection is reliable, and these large
                            # boxes are intended for image upload, not checkbox functionality.
                            a_is_image_box = f_a['data'].get('is_image_box')
                            b_is_image_box = f_b['data'].get('is_image_box')
                            
                            if a_is_image_box and f_b['type'] == 'cb':
                                # Image box A wins over checkbox B
                                print(f"      [TRACER] Protected: Image Box A wins over Checkbox B (FORCED)")
                                indices_to_remove.add(idx_b)
                                continue
                            if b_is_image_box and f_a['type'] == 'cb':
                                # Image box B wins over checkbox A
                                print(f"      [TRACER] Protected: Image Box B wins over Checkbox A (FORCED)")
                                indices_to_remove.add(idx_a)
                                break
                            
                            # Compare ranks
                            if f_a['rank'] > f_b['rank']:
                                print(f"   [Conflict] A({f_a['type']} {f_a['data'].get('name') if f_a['type']=='text' else f_a['data'].get('label')} rank={f_a['rank']}) wins over B({f_b['type']} {f_b['data'].get('name') if f_b['type']=='text' else f_b['data'].get('label')} rank={f_b['rank']})")
                                indices_to_remove.add(idx_b)
                            elif f_b['rank'] > f_a['rank']:
                                print(f"   [Conflict] B({f_b['type']} {f_b['data'].get('name') if f_b['type']=='text' else f_b['data'].get('label')} rank={f_b['rank']}) wins over A({f_a['type']} {f_a['data'].get('name') if f_a['type']=='text' else f_a['data'].get('label')} rank={f_a['rank']})")
                                indices_to_remove.add(idx_a)
                                break # A is gone, skip to next A
                            else:
                                # Tied ranks: keep wider or higher priority source string
                                w_a = ax1 - ax0
                                w_b = bx1 - bx0
                                if w_a >= w_b:
                                    indices_to_remove.add(idx_b)
                                else:
                                    indices_to_remove.add(idx_a)
                                    break
        
        # Apply removals
        new_text = []
        new_cb = []
        
        removed_txt = 0
        removed_cb = 0
        
        for i, f_ext in enumerate(all_fields):
            if i in indices_to_remove:
                if f_ext['type'] == 'text': 
                    removed_txt += 1
                    # DEBUG: Show removed image boxes
                    if f_ext['data'].get('is_image_box'):
                        print(f"   [DEBUG-REMOVED] ImageBox removed: {f_ext['data'].get('name')} pg={f_ext['data']['page']}")
                else: removed_cb += 1
                continue
                
            if f_ext['type'] == 'text':
                new_text.append(f_ext['data'])
            else:
                new_cb.append(f_ext['data'])
                
        self.text_fields = new_text
        self.checkboxes = new_cb
        
        # DEBUG: Count image boxes after conflict resolution
        image_box_count = sum(1 for f in self.text_fields if f.get('is_image_box'))
        print(f"   [DEBUG] After conflict resolution: {image_box_count} fields have is_image_box=True")
        # Count per page
        from collections import Counter
        page_counts = Counter(f['page'] for f in self.text_fields if f.get('is_image_box'))
        print(f"   [DEBUG] Image boxes per page: {dict(sorted(page_counts.items()))}")
        
        # DEBUG: Show names of image boxes on pages 3-7
        for f in self.text_fields:
            if f.get('is_image_box') and f['page'] in [3, 4, 5, 6, 7]:
                print(f"   [DEBUG-P3-7] IB: {f.get('name')} pg={f['page']}")
        
        # Sync checkbox_positions
        self.checkbox_positions = []
        for cb in self.checkboxes:
             self.checkbox_positions.append({
                'page': cb['page'],
                'x0': cb['x'] - 2, 'y0': cb['y'] - 2,
                'x1': cb['x'] + cb['width'] + 2, 'y1': cb['y'] + cb['height'] + 2,
                'source': cb.get('source')
            })
            
        print(f"   🛡️ Conflict Resolution: Kept {len(new_text)} Tx, {len(new_cb)} CB. Removed {removed_txt} Tx conflict, {removed_cb} CB conflict.")

    def _remove_overlap_duplicates(self):
        """Deprecated: Replaced by _resolve_all_conflicts."""
        pass

    def _remove_text_checkbox_overlaps(self):
        """Deprecated: Replaced by _resolve_all_conflicts."""
        pass


class InspectionPass:
    """
    Staged inspection pass for validating detected fields.
    
    Runs 5 checks in stages, yielding progress updates to prevent timeouts.
    Each stage outputs JSON to stdout for Electron to parse.
    """
    
    MIN_FIELD_WIDTH = 15
    MIN_FIELD_HEIGHT = 8
    MIN_CELL_COVERAGE = 0.70  # Fields should cover 70% of cell width
    MIN_RADIO_OPTIONS = 2
    
    def __init__(self, detector: UniversalPDFFillable):
        self.detector = detector
        self.issues = []
        self.fix_suggestions = {}
        
    def run_staged_inspection(self) -> Generator[Dict, None, None]:
        """
        Run inspection in stages, yielding JSON for each stage.
        
        Yields dicts that should be serialized as JSON lines to stdout.
        """
        stages = [
            (1, "Field Size Check", self._check_field_sizes),
            (2, "Cell Coverage Check", self._check_cell_coverage),
            (3, "Radio Group Check", self._check_radio_groups),
            (4, "Overlap Check", self._check_overlaps),
            (5, "Naming Check", self._check_naming),
        ]
        
        total_stages = len(stages)
        
        for stage_num, stage_name, check_func in stages:
            # Emit stage start
            yield {
                "type": "stage_start",
                "stage": stage_num,
                "name": stage_name,
                "progress": int((stage_num - 1) / total_stages * 100)
            }
            
            # Run the check
            stage_issues = check_func()
            self.issues.extend(stage_issues)
            
            # Emit stage complete with issues found
            yield {
                "type": "stage_complete",
                "stage": stage_num,
                "name": stage_name,
                "issues": stage_issues,
                "issue_count": len(stage_issues),
                "progress": int(stage_num / total_stages * 100)
            }
        
        # Emit final summary
        fixable_count = sum(1 for i in self.issues if i.get('fixable', False))
        yield {
            "type": "inspection_complete",
            "total_issues": len(self.issues),
            "fixable_count": fixable_count,
            "progress": 100
        }
    
    def _check_field_sizes(self) -> List[Dict]:
        """Stage 1: Check for fields that are too small."""
        issues = []
        for idx, field in enumerate(self.detector.text_fields):
            width = field['x1'] - field['x0']
            height = field['y1'] - field['y0']
            
            if width < self.MIN_FIELD_WIDTH:
                issues.append({
                    'id': f'size_width_{idx}',
                    'type': 'field_too_narrow',
                    'severity': 'warning',
                    'field_name': field.get('name', 'Unknown'),
                    'page': field['page'] + 1,
                    'current_width': round(width, 1),
                    'min_width': self.MIN_FIELD_WIDTH,
                    'message': f"Field '{field.get('name')}' is only {width:.1f}pt wide (min: {self.MIN_FIELD_WIDTH}pt)",
                    'fixable': True,
                    'fix_action': 'expand_width'
                })
            
            if height < self.MIN_FIELD_HEIGHT:
                issues.append({
                    'id': f'size_height_{idx}',
                    'type': 'field_too_short',
                    'severity': 'warning',
                    'field_name': field.get('name', 'Unknown'),
                    'page': field['page'] + 1,
                    'current_height': round(height, 1),
                    'min_height': self.MIN_FIELD_HEIGHT,
                    'message': f"Field '{field.get('name')}' is only {height:.1f}pt tall (min: {self.MIN_FIELD_HEIGHT}pt)",
                    'fixable': True,
                    'fix_action': 'expand_height'
                })
        
        return issues
    
    def _check_cell_coverage(self) -> List[Dict]:
        """Stage 2: Check fields that don't cover enough of their cell."""
        issues = []
        
        # Group cells by page for faster lookup
        cells_by_page = defaultdict(list)
        for cell in self.detector.table_cells:
            cells_by_page[cell['page']].append(cell)
            
        for idx, field in enumerate(self.detector.text_fields):
            page_num = field['page']
            if page_num not in cells_by_page:
                continue
                
            # v21: Skip colon_separated fields from coverage check (they are intentionally split)
            # v21.51: Also skip underscore-based fields - they have precise widths from underscore text
            # and should NOT be expanded to fill the entire cell
            protected_sources = ['colon_separated', 'embedded_underscore', 'general_underscore', 
                               'label_below_underscore', 'underscore', 'split_field']
            if field.get('source') in protected_sources:
                continue
                
            field_cx = (field['x0'] + field['x1']) / 2
            field_cy = (field['y0'] + field['y1']) / 2
            
            # Find the cell containing this field
            enclosing_cell = None
            for cell in cells_by_page[page_num]:
                # Check if field center is within cell with small tolerance
                if (cell['x0'] - 2 <= field_cx <= cell['x1'] + 2 and 
                    cell['y0'] - 2 <= field_cy <= cell['y1'] + 2):
                    enclosing_cell = cell
                    break
            
            if enclosing_cell:
                cell_width = enclosing_cell['x1'] - enclosing_cell['x0']
                field_width = field['x1'] - field['x0']
                
                # Check coverage (skip tiny cells where ratio might be misleading)
                if cell_width > 20:
                    coverage = field_width / cell_width
                    
                    if coverage < self.MIN_CELL_COVERAGE:
                        # Check if there are OTHER fields on the same Y position to the RIGHT
                        # This indicates an inline pattern like "City: ___ State: ___ ZIP: ___"
                        # where we should NOT expand the field to fill the whole cell
                        has_inline_sibling = False
                        field_y_mid = (field['y0'] + field['y1']) / 2
                        for other_field in self.detector.text_fields:
                            if other_field is field:
                                continue
                            if other_field.get('page', 0) != page_num:
                                continue
                            other_y_mid = (other_field['y0'] + other_field['y1']) / 2
                            # Check if on same Y (within 5pt) and to the right
                            if abs(other_y_mid - field_y_mid) < 5 and other_field['x0'] > field['x1']:
                                has_inline_sibling = True
                                break
                        
                        if has_inline_sibling:
                            # Skip - don't expand inline fields like City/State/ZIP
                            continue
                        
                        # It's a narrow field in a cell - likely should fill the cell
                        issues.append({
                            'id': f'coverage_{idx}',
                            'type': 'poor_cell_coverage',
                            'severity': 'warning',
                            'field_name': field.get('name', 'Unknown'),
                            'page': page_num + 1,
                            'coverage_pct': int(coverage * 100),
                            'message': f"Field '{field.get('name')}' covers only {int(coverage*100)}% of table cell",
                            'fixable': True,
                            'fix_action': 'expand_to_cell',
                            'target_x1': enclosing_cell['x1'] - 2  # Leave 2pt padding
                        })
        
        return issues
    
    def _check_radio_groups(self) -> List[Dict]:
        """Stage 3: Check radio groups with fewer than 2 options."""
        issues = []
        for idx, group in enumerate(self.detector.radio_groups):
            options = group.get('options', [])
            if len(options) < self.MIN_RADIO_OPTIONS:
                issues.append({
                    'id': f'radio_{idx}',
                    'type': 'radio_insufficient_options',
                    'severity': 'warning',
                    'group_name': group.get('name', 'Unknown'),
                    'page': group.get('page', 0) + 1,
                    'option_count': len(options),
                    'min_options': self.MIN_RADIO_OPTIONS,
                    'message': f"Radio group '{group.get('name')}' has only {len(options)} option(s) (min: {self.MIN_RADIO_OPTIONS})",
                    'fixable': False,  # Can't auto-fix missing options
                    'fix_action': None
                })
        
        return issues
    
    def _check_overlaps(self) -> List[Dict]:
        """Stage 4: Check for fields overlapping each other."""
        issues = []
        fields = self.detector.text_fields
        
        for i, field_a in enumerate(fields):
            for j, field_b in enumerate(fields[i+1:], i+1):
                if field_a['page'] != field_b['page']:
                    continue
                
                # Check for overlap
                if (field_a['x0'] < field_b['x1'] and field_a['x1'] > field_b['x0'] and
                    field_a['y0'] < field_b['y1'] and field_a['y1'] > field_b['y0']):
                    
                    # Calculate overlap area
                    ox0 = max(field_a['x0'], field_b['x0'])
                    oy0 = max(field_a['y0'], field_b['y0'])
                    ox1 = min(field_a['x1'], field_b['x1'])
                    oy1 = min(field_a['y1'], field_b['y1'])
                    overlap_area = (ox1 - ox0) * (oy1 - oy0)
                    
                    # Only report if overlap is significant (> 5 sq pt)
                    if overlap_area > 5:
                        issues.append({
                            'id': f'overlap_{i}_{j}',
                            'type': 'field_overlap',
                            'severity': 'error',
                            'field_a': field_a.get('name', 'Unknown'),
                            'field_b': field_b.get('name', 'Unknown'),
                            'page': field_a['page'] + 1,
                            'overlap_area': round(overlap_area, 1),
                            'message': f"Fields '{field_a.get('name')}' and '{field_b.get('name')}' overlap by {overlap_area:.1f} sq pt",
                            'fixable': True,
                            'fix_action': 'remove_smaller'
                        })
        
        return issues
    
    def _check_naming(self) -> List[Dict]:
        """Stage 5: Check for duplicate or generic field names."""
        issues = []
        name_counts = defaultdict(list)
        
        for idx, field in enumerate(self.detector.text_fields):
            name = field.get('name', '')
            name_counts[name].append((idx, field))
        
        for name, occurrences in name_counts.items():
            if len(occurrences) > 1:
                # Duplicate names
                for idx, field in occurrences[1:]:  # Skip first occurrence
                    issues.append({
                        'id': f'name_dup_{idx}',
                        'type': 'duplicate_name',
                        'severity': 'warning',
                        'field_name': name,
                        'page': field['page'] + 1,
                        'message': f"Duplicate field name: '{name}'",
                        'fixable': True,
                        'fix_action': 'rename_unique'
                    })
            
            # Generic names
            generic_names = ['field', 'text', 'input', 'box', 'check box', 'field_']
            if any(name.lower().startswith(g) for g in generic_names):
                if len(occurrences) >= 5:  # Only warn if many generic names
                    issues.append({
                        'id': f'name_generic_{name}',
                        'type': 'generic_name',
                        'severity': 'info',
                        'field_name': name,
                        'count': len(occurrences),
                        'message': f"Many generic field names: '{name}' ({len(occurrences)} occurrences)",
                        'fixable': False,
                        'fix_action': None
                    })
        
        return issues
    
    def apply_fixes(self, issue_ids: List[str]) -> Dict:
        """
        Apply fixes for the specified issue IDs.
        
        Returns a summary of what was fixed.
        """
        fixed = []
        skipped = []
        
        for issue in self.issues:
            if issue['id'] not in issue_ids:
                continue
            
            if not issue.get('fixable', False):
                skipped.append(issue['id'])
                continue
            
            action = issue.get('fix_action')
            
            if action == 'expand_width':
                # Find and expand the field
                for field in self.detector.text_fields:
                    if field.get('name') == issue.get('field_name'):
                        old_width = field['x1'] - field['x0']
                        field['x1'] = field['x0'] + max(old_width, self.MIN_FIELD_WIDTH + 5)
                        fixed.append(issue['id'])
                        break
            
            elif action == 'expand_height':
                for field in self.detector.text_fields:
                    if field.get('name') == issue.get('field_name'):
                        old_height = field['y1'] - field['y0']
                        field['y1'] = field['y0'] + max(old_height, self.MIN_FIELD_HEIGHT + 2)
                        fixed.append(issue['id'])
                        break

            elif action == 'expand_to_cell':
                # Expand field width to fill calculation cell
                for field in self.detector.text_fields:
                    if field.get('name') == issue.get('field_name'):
                        target_x1 = issue.get('target_x1')
                        if target_x1:
                            field['x1'] = target_x1
                            fixed.append(issue['id'])
                        break
            
            elif action == 'remove_smaller':
                # Remove the smaller of the two overlapping fields
                field_a_name = issue.get('field_a')
                field_b_name = issue.get('field_b')
                
                field_a = next((f for f in self.detector.text_fields if f.get('name') == field_a_name), None)
                field_b = next((f for f in self.detector.text_fields if f.get('name') == field_b_name), None)
                
                if field_a and field_b:
                    area_a = (field_a['x1'] - field_a['x0']) * (field_a['y1'] - field_a['y0'])
                    area_b = (field_b['x1'] - field_b['x0']) * (field_b['y1'] - field_b['y0'])
                    
                    if area_a < area_b:
                        self.detector.text_fields.remove(field_a)
                    else:
                        self.detector.text_fields.remove(field_b)
                    fixed.append(issue['id'])
            
            elif action == 'rename_unique':
                # Add suffix to make name unique
                for field in self.detector.text_fields:
                    if field.get('name') == issue.get('field_name'):
                        field['name'] = self.detector._generate_unique_name(field['name'], field['page'])
                        fixed.append(issue['id'])
                        break
        
        return {
            'fixed_count': len(fixed),
            'fixed_ids': fixed,
            'skipped_count': len(skipped),
            'skipped_ids': skipped
        }




    
def main():
    parser = argparse.ArgumentParser(description='Generate smart fillable PDF with specific output format v20',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python smart_fillable_v20.py form.pdf                     # Normal: detect + generate PDF
    python smart_fillable_v20.py form.pdf --inspect           # Inspect: detect + run inspection, output JSON
    python smart_fillable_v20.py form.pdf --fix id1,id2       # Fix: apply fixes and generate PDF
    python smart_fillable_v20.py form.pdf --skip-inspection   # Skip: generate PDF without inspection
        """
    )
    parser.add_argument('input_pdf', help='Input PDF file')
    parser.add_argument('output_pdf', nargs='?', help='Output PDF file (optional)')
    parser.add_argument('--inspect', action='store_true', 
                        help='Run staged inspection and output JSON (no PDF generated)')
    parser.add_argument('--fix', type=str, metavar='IDS',
                        help='Comma-separated list of issue IDs to fix before generating PDF')
    parser.add_argument('--skip-inspection', action='store_true',
                        help='Skip inspection and generate PDF directly')
    parser.add_argument('--auto-calibrate', action='store_true',
                        help='Automatically analyze PDF structure and optimize detection settings')
    parser.add_argument('--settings', type=str, metavar='JSON',
                        help='JSON string containing settings overrides')
    parser.add_argument('--enhanced', choices=['off','conservative','aggressive'], default='off',
                        help='Enable enhanced detection mode (off, conservative, aggressive)')
    parser.add_argument('--qa-overlay', type=str, metavar='FILE',
                        help='Write QA overlay PDF showing proposed enhanced fields')
    parser.add_argument('--candidate-json', type=str, metavar='FILE',
                        help='Write JSON with proposed enhanced field candidates')
    
    args = parser.parse_args()
    
    # Parse settings if provided
    settings = {}
    if args.settings:
        try:
            settings = json.loads(args.settings)
            print(f"   📋 Loaded {len(settings)} settings from app")
            # Show key detection settings that affect output
            detection_settings = {
                'min_empty_box_width': settings.get('min_empty_box_width', 100),
                'min_empty_box_height': settings.get('min_empty_box_height', 30),
                'min_underscore_length': settings.get('min_underscore_length', 3),
                'skip_page_edges': settings.get('skip_page_edges', False),
                'page_edge_margin': settings.get('page_edge_margin', 30),
                'detection_sensitivity': settings.get('detection_sensitivity', 'standard'),
            }
            print(f"   🎛️  Detection settings: {detection_settings}")
        except json.JSONDecodeError as e:
            print(f"   ⚠️ Warning: Could not parse settings JSON: {e}")
    
    detector = UniversalPDFFillable(args.input_pdf, args.output_pdf, settings=settings, auto_calibrate=args.auto_calibrate)

    # Wire CLI-enhanced options into detector
    detector.enhanced_mode = args.enhanced
    detector.qa_overlay = args.qa_overlay
    detector.candidate_json = args.candidate_json
    
    # v21.7: Open handle and preload for all CLI modes
    detector.pdf = pdfplumber.open(str(detector.input_pdf))
    try:
        detector._preload_structural_data()
        
        if args.inspect:
            # INSPECTION MODE: Detect fields, run staged inspection, output JSON
            print("🔍 Running advanced layout analysis...", file=sys.stderr)
            
            # Run detection passes (no PDF generation)
            detector._print_header()
            
            # v21.7: Added missing PASS 0 for text-only detection
            detector._detect_text_only_pages()
            
            detector._detect_checkboxes_vector()
            detector._detect_checkboxes_character()
            detector._detect_checkboxes_curves()  # v33.30: Fix missing checkbox detection
            detector._detect_checkboxes_line_boxes()  # v33.30: Fix missing checkbox detection (critical for 29174)
            detector._detect_table_cells()
            detector._detect_signature_fields()
            detector._detect_special_label_fields()
            detector._detect_inline_label_fields()
            detector._detect_general_underscore_fields()
            detector._detect_colon_separated_fields()
            detector._detect_empty_boxes()
            detector._detect_form_lines()
            # v22.3: Use box_entry detection instead of comb_fields_from_lines
            detector._detect_box_entry_fields()
            detector._cleanup_field_boundaries()
            detector._enrich_checkbox_labels_from_column_headers()
            detector._enrich_checkbox_labels_from_beside_text()
            detector._identify_radio_groups()
            detector._refine_horizontal_checkbox_labels()
            detector._assign_header_based_checkbox_names()
            
            # v21.7: Added missing PASS 6, 6B, 6C
            detector._merge_adjacent_fields()
            detector._remove_overlap_duplicates()
            detector._extend_fields_to_line_endpoints()
            
            # Run staged inspection
            inspector = InspectionPass(detector)
            for stage_result in inspector.run_staged_inspection():
                # Output JSON to stdout (one line per stage)
                print(json.dumps(stage_result), flush=True)
            
            print("✅ Inspection complete!", file=sys.stderr)
            
        elif args.fix:
            # FIX MODE: Detect fields, apply fixes, generate PDF
            issue_ids = [id.strip() for id in args.fix.split(',')]
            print(f"🔧 Applying {len(issue_ids)} layout adjustments...", file=sys.stderr)
            
            # Run detection passes
            detector._print_header()
            
            # v21.7: Added missing PASS 0 for text-only detection
            detector._detect_text_only_pages()
            
            detector._detect_checkboxes_vector()
            detector._detect_checkboxes_character()
            detector._detect_checkboxes_curves()  # v33.30: Fix missing checkbox detection
            detector._detect_checkboxes_line_boxes()  # v33.30: Fix missing checkbox detection (critical for 29174)
            detector._detect_table_cells()
            detector._detect_signature_fields()
            detector._detect_special_label_fields()
            detector._detect_inline_label_fields()
            detector._detect_general_underscore_fields()
            detector._detect_colon_separated_fields()
            detector._detect_empty_boxes()
            detector._detect_form_lines()
            # v22.3: Use box_entry detection instead of comb_fields_from_lines
            detector._detect_box_entry_fields()
            detector._cleanup_field_boundaries()
            detector._enrich_checkbox_labels_from_column_headers()
            detector._enrich_checkbox_labels_from_beside_text()
            detector._identify_radio_groups()
            detector._refine_horizontal_checkbox_labels()
            detector._assign_header_based_checkbox_names()
            
            # v21.7: Added missing PASS 6, 6B, 6C
            detector._merge_adjacent_fields()
            
            # Conditionally remove duplicates
            if detector.settings.get('enable_duplicate_fields', False) == True:
                 print("   [Clean] Skipping duplicate removal (enable_duplicate_fields=True)")
            else:
                 detector._remove_overlap_duplicates()
                 
            detector._extend_fields_to_line_endpoints()
            
            # Run inspection to collect issues
            inspector = InspectionPass(detector)
            all_issues = []
            for stage_result in inspector.run_staged_inspection():
                # Accumulate issues from each stage (stage_result may include 'issues')
                stage_issues = stage_result.get('issues', []) if isinstance(stage_result, dict) else []
                all_issues.extend(stage_issues)

            # Special convenience token: if user passed --fix auto_expand_coverage
            # then automatically collect all coverage issues and fix them
            if len(issue_ids) == 1 and issue_ids[0].lower() == 'auto_expand_coverage':
                auto_ids = [iss['id'] for iss in all_issues if iss.get('fix_action') == 'expand_to_cell']
                print(f"   [AutoFix] Found {len(auto_ids)} coverage issues to expand", file=sys.stderr)
                issue_ids = auto_ids

            # Apply fixes
            fix_result = inspector.apply_fixes(issue_ids)
            print(json.dumps({"type": "fixes_applied", **fix_result}), flush=True)
            
            # Generate PDF with fixed fields
            detector._create_fillable_pdf()
            detector._fix_font_encoding()
            detector._create_radio_groups()
            detector._generate_overlays()
            detector._print_summary()
            detector._add_javascript_formatting()
            detector._fix_font_encoding()
            
            print(f"✅ Done! Output: {detector.output_pdf}")
            
        elif args.skip_inspection:
            # SKIP MODE: Normal PDF generation without inspection
            output = detector.run()
            print(f"✅ Done! Output: {output}")
            
        else:
            # NORMAL MODE: Full inspection + ask user (via frontend)
            # For CLI usage without --skip-inspection, run normally
            output = detector.run()
            print(f"✅ Done! Output: {output}")

    finally:
        if detector.pdf:
            detector.pdf.close()
            detector.pdf = None


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)

