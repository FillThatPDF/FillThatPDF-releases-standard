#!/usr/bin/env python3
"""
Smart Fillable PDF - Ensemble Engine v2.0 (Tuned)
==================================================

IMPROVEMENTS over v1:
1. Stricter overlap threshold (0.4 → 0.5 IoU) to reduce duplicates
2. Weighted voting by strategy reliability
3. Better size filtering (eliminates tiny/huge false positives)
4. Confidence-based final filtering
5. Smarter type assignment based on dimensions
6. Caching for faster multi-strategy processing

Usage:
    python smart_fillable_ensemble_v2.py input.pdf [output.pdf]
    
    # Tune min votes (default 2)
    python smart_fillable_ensemble_v2.py input.pdf --min-votes 3
    
    # Lower confidence threshold
    python smart_fillable_ensemble_v2.py input.pdf --min-confidence 0.4

Author: FillThatPDF Team  
Date: February 2026
"""

import sys
import os
import math
import re
import json
import logging
from pathlib import Path
from collections import defaultdict, Counter
from typing import List, Dict, Tuple, Optional, Any, Set
from dataclasses import dataclass, field
from enum import Enum
import argparse

import pdfplumber
import pikepdf
import cv2
import numpy as np

# Optional faster rendering
try:
    from pdf2image import convert_from_path
    PDF2IMAGE_AVAILABLE = True
except ImportError:
    PDF2IMAGE_AVAILABLE = False

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)
logging.getLogger('pdfminer').setLevel(logging.WARNING)
logging.getLogger('pdfplumber').setLevel(logging.WARNING)


# =============================================================================
# TUNED PARAMETERS
# =============================================================================

class TunedParams:
    """Centralized tuning parameters based on testing."""
    
    # Voting thresholds
    MIN_VOTES_DEFAULT = 2            # Minimum strategies that must agree
    OVERLAP_IOU_THRESHOLD = 0.5      # IoU for considering fields "same"
    
    # Strategy reliability weights (higher = more trusted)
    STRATEGY_WEIGHTS = {
        "vector_squares": 1.2,       # Very reliable for checkboxes
        "unicode_checkbox": 1.5,     # Almost certain (explicit chars)
        "table_structure": 0.9,      # Good but some false positives
        "text_pattern": 0.8,         # Helpful but context-dependent
        "underline": 0.7,            # Many false positives from decoration
        "visual_grid": 0.6,          # Lowest - CV can be noisy
    }
    
    # Size constraints (in PDF points)
    CHECKBOX = {
        "min_size": 4, "max_size": 18,
        "max_aspect": 1.5  # Must be square-ish
    }
    TEXT_FIELD = {
        "min_width": 20, "max_width": 500,
        "min_height": 8, "max_height": 50,
    }
    
    # Confidence thresholds
    MIN_CONFIDENCE = 0.5             # Below this, reject field
    HIGH_CONFIDENCE = 0.8            # Single strategy can pass
    
    # Label patterns that indicate fields
    LABEL_PATTERNS = [
        r'.*:\s*$',                  # Ends with colon
        r'.*\?\s*$',                 # Ends with question mark
        r'(?i)(name|date|address|phone|email|sign|title|city|state|zip)',
    ]


# =============================================================================
# DATA STRUCTURES
# =============================================================================

class FieldType(Enum):
    TEXT = "text"
    CHECKBOX = "checkbox"
    RADIO = "radio"
    DROPDOWN = "dropdown"
    SIGNATURE = "signature"
    DATE = "date"
    UNKNOWN = "unknown"


class Strategy(Enum):
    VECTOR_SQUARES = "vector_squares"
    UNICODE_CHECKBOX = "unicode_checkbox"
    TABLE_STRUCTURE = "table_structure"
    TEXT_PATTERN = "text_pattern"
    UNDERLINE = "underline"
    VISUAL_GRID = "visual_grid"


@dataclass 
class DetectedField:
    """A candidate form field."""
    x0: float
    y0: float  # Bottom-up PDF coords
    x1: float
    y1: float
    page: int
    field_type: FieldType = FieldType.UNKNOWN
    label: str = ""
    confidence: float = 0.5
    strategies: Set[Strategy] = field(default_factory=set)
    metadata: Dict = field(default_factory=dict)
    
    @property
    def width(self) -> float:
        return abs(self.x1 - self.x0)
    
    @property
    def height(self) -> float:
        return abs(self.y1 - self.y0)
    
    @property
    def area(self) -> float:
        return self.width * self.height
    
    @property
    def center(self) -> Tuple[float, float]:
        return ((self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2)
    
    @property
    def weighted_votes(self) -> float:
        """Vote count weighted by strategy reliability."""
        return sum(
            TunedParams.STRATEGY_WEIGHTS.get(s.value, 1.0) 
            for s in self.strategies
        )
    
    def iou(self, other: 'DetectedField') -> float:
        """Calculate IoU with another field."""
        if self.page != other.page:
            return 0.0
        
        ix0 = max(self.x0, other.x0)
        iy0 = max(self.y0, other.y0)
        ix1 = min(self.x1, other.x1)
        iy1 = min(self.y1, other.y1)
        
        if ix1 <= ix0 or iy1 <= iy0:
            return 0.0
        
        intersection = (ix1 - ix0) * (iy1 - iy0)
        union = self.area + other.area - intersection
        
        return intersection / union if union > 0 else 0.0


# =============================================================================
# PAGE DATA CACHE
# =============================================================================

class PageCache:
    """Cache page data to avoid repeated PDF parsing."""
    
    def __init__(self, pdf_path: str):
        self.pdf_path = Path(pdf_path)
        self.pages: Dict[int, Dict] = {}
        self._load_all()
    
    def _load_all(self):
        """Load all page data once."""
        with pdfplumber.open(self.pdf_path) as pdf:
            for i, page in enumerate(pdf.pages):
                self.pages[i] = {
                    "width": float(page.width),
                    "height": float(page.height),
                    "words": page.extract_words(x_tolerance=3, y_tolerance=3),
                    "chars": page.chars or [],
                    "lines": page.lines or [],
                    "rects": page.rects or [],
                    "curves": page.curves or [],
                }
    
    def __getitem__(self, page_num: int) -> Dict:
        return self.pages.get(page_num, {})
    
    def __len__(self) -> int:
        return len(self.pages)


# =============================================================================
# DETECTION STRATEGIES
# =============================================================================

class VectorStrategy:
    """Detect checkboxes/radios from vector rectangles."""
    
    STRATEGY = Strategy.VECTOR_SQUARES
    
    def detect(self, cache: PageCache) -> List[DetectedField]:
        fields = []
        params = TunedParams.CHECKBOX
        
        for page_num, data in cache.pages.items():
            page_h = data["height"]
            
            for rect in data["rects"]:
                x0 = float(rect.get("x0", 0))
                x1 = float(rect.get("x1", 0))
                top = float(rect.get("top", 0))
                bottom = float(rect.get("bottom", 0))
                
                w = abs(x1 - x0)
                h = abs(bottom - top)
                
                # Checkbox filter
                if params["min_size"] <= w <= params["max_size"]:
                    if params["min_size"] <= h <= params["max_size"]:
                        aspect = max(w, h) / min(w, h) if min(w, h) > 0 else 99
                        if aspect < params["max_aspect"]:
                            y0 = page_h - bottom
                            y1 = page_h - top
                            
                            fields.append(DetectedField(
                                x0=x0, y0=y0, x1=x1, y1=y1,
                                page=page_num,
                                field_type=FieldType.CHECKBOX,
                                confidence=0.85,
                                strategies={self.STRATEGY}
                            ))
            
            # Curves (potential radios)
            for curve in data["curves"]:
                pts = curve.get("pts", [])
                if len(pts) >= 4:
                    xs = [p[0] for p in pts]
                    ys = [p[1] for p in pts]
                    x0, x1 = min(xs), max(xs)
                    top, bottom = min(ys), max(ys)
                    w, h = x1 - x0, bottom - top
                    
                    if 4 <= w <= 16 and 4 <= h <= 16:
                        aspect = max(w, h) / min(w, h) if min(w, h) > 0 else 99
                        if aspect < 1.3:
                            y0 = page_h - bottom
                            y1 = page_h - top
                            
                            fields.append(DetectedField(
                                x0=x0, y0=y0, x1=x1, y1=y1,
                                page=page_num,
                                field_type=FieldType.RADIO,
                                confidence=0.75,
                                strategies={self.STRATEGY}
                            ))
        
        return fields


class UnicodeCheckboxStrategy:
    """Detect Unicode checkbox characters (☐ ☑ ☒)."""
    
    STRATEGY = Strategy.UNICODE_CHECKBOX
    CHECKBOX_CHARS = {'☐', '☑', '☒', '□', '■', '◻', '◼', '○', '●', '◯'}
    
    def detect(self, cache: PageCache) -> List[DetectedField]:
        fields = []
        
        for page_num, data in cache.pages.items():
            page_h = data["height"]
            
            for char in data["chars"]:
                if char.get("text", "") in self.CHECKBOX_CHARS:
                    x0 = float(char["x0"])
                    x1 = float(char["x1"])
                    top = float(char["top"])
                    bottom = float(char["bottom"])
                    
                    y0 = page_h - bottom
                    y1 = page_h - top
                    
                    fields.append(DetectedField(
                        x0=x0, y0=y0, x1=x1, y1=y1,
                        page=page_num,
                        field_type=FieldType.CHECKBOX,
                        confidence=0.95,  # Very high - explicit symbols
                        strategies={self.STRATEGY}
                    ))
        
        return fields


class TableStrategy:
    """Detect empty table cells as text fields."""
    
    STRATEGY = Strategy.TABLE_STRUCTURE
    
    def detect(self, cache: PageCache) -> List[DetectedField]:
        fields = []
        params = TunedParams.TEXT_FIELD
        
        with pdfplumber.open(cache.pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages):
                page_h = float(page.height)
                
                tables = page.find_tables(table_settings={
                    "vertical_strategy": "lines",
                    "horizontal_strategy": "lines",
                    "snap_tolerance": 5,
                    "join_tolerance": 5,
                })
                
                for table in tables:
                    for row in table.cells:
                        cells = row if isinstance(row, list) else [row]
                        for cell in cells:
                            if not cell or len(cell) < 4:
                                continue
                            
                            x0, top, x1, bottom = cell[:4]
                            w = x1 - x0
                            h = bottom - top
                            
                            # Size filter
                            if not (params["min_width"] <= w <= params["max_width"]):
                                continue
                            if not (params["min_height"] <= h <= params["max_height"]):
                                continue
                            
                            # Check emptiness
                            try:
                                text = page.within_bbox((x0, top, x1, bottom)).extract_text() or ""
                            except:
                                text = ""
                            
                            is_empty = len(text.strip()) < 3
                            
                            y0 = page_h - bottom
                            y1 = page_h - top
                            
                            if is_empty or w > 80:
                                fields.append(DetectedField(
                                    x0=x0, y0=y0, x1=x1, y1=y1,
                                    page=page_num,
                                    field_type=FieldType.TEXT,
                                    confidence=0.75 if is_empty else 0.55,
                                    strategies={self.STRATEGY}
                                ))
        
        return fields


class TextPatternStrategy:
    """Detect fields after label patterns like 'Name:'."""
    
    STRATEGY = Strategy.TEXT_PATTERN
    
    def detect(self, cache: PageCache) -> List[DetectedField]:
        fields = []
        
        for page_num, data in cache.pages.items():
            page_h = data["height"]
            page_w = data["width"]
            words = data["words"]
            
            for i, word in enumerate(words):
                text = word.get("text", "")
                
                # Label patterns
                if not (text.endswith(":") or text.endswith("?")):
                    continue
                
                wx1 = float(word["x1"])
                wtop = float(word["top"])
                wbot = float(word["bottom"])
                
                # Find field space
                field_x0 = wx1 + 5
                field_x1 = page_w - 36
                
                for j in range(i + 1, min(i + 4, len(words))):
                    nw = words[j]
                    if abs(float(nw["top"]) - wtop) < 10:
                        gap = float(nw["x0"]) - wx1
                        if gap > 25:
                            field_x1 = float(nw["x0"]) - 5
                            break
                        elif gap < 25:
                            field_x1 = 0
                            break
                
                if field_x1 > field_x0 + 30:
                    y0 = page_h - wbot - 2
                    y1 = page_h - wtop + 2
                    
                    label = text.rstrip(":?")
                    ftype = FieldType.DATE if "date" in label.lower() else FieldType.TEXT
                    
                    fields.append(DetectedField(
                        x0=field_x0, y0=y0, x1=field_x1, y1=y1,
                        page=page_num,
                        field_type=ftype,
                        label=label,
                        confidence=0.7,
                        strategies={self.STRATEGY}
                    ))
        
        return fields


class UnderlineStrategy:
    """Detect signature/date lines from horizontal rules."""
    
    STRATEGY = Strategy.UNDERLINE
    SIGNATURE_KEYWORDS = {"signature", "sign", "signed", "print", "date", "name"}
    
    def detect(self, cache: PageCache) -> List[DetectedField]:
        fields = []
        
        for page_num, data in cache.pages.items():
            page_h = data["height"]
            words = data["words"]
            
            for line in data["lines"]:
                x0 = float(line.get("x0", 0))
                x1 = float(line.get("x1", 0))
                y_top = float(line.get("top", line.get("y0", 0)))
                y_bot = float(line.get("bottom", line.get("y1", y_top)))
                
                w = x1 - x0
                h = abs(y_bot - y_top)
                
                # Horizontal line: wide, thin
                if w > 80 and h < 4:
                    label = ""
                    ftype = FieldType.TEXT
                    
                    # Check for keywords above
                    for word in words:
                        word_bot = float(word["bottom"])
                        word_x0 = float(word["x0"])
                        word_x1 = float(word["x1"])
                        
                        if word_bot < y_top and y_top - word_bot < 30:
                            if word_x0 < x1 and word_x1 > x0:
                                wtext = word.get("text", "").lower()
                                for kw in self.SIGNATURE_KEYWORDS:
                                    if kw in wtext:
                                        label = word.get("text", "").title()
                                        ftype = FieldType.DATE if "date" in wtext else FieldType.SIGNATURE
                                        break
                    
                    y0 = page_h - y_bot - 2
                    y1 = page_h - y_top + 14
                    
                    fields.append(DetectedField(
                        x0=x0, y0=y0, x1=x1, y1=y1,
                        page=page_num,
                        field_type=ftype,
                        label=label,
                        confidence=0.65,
                        strategies={self.STRATEGY}
                    ))
        
        return fields


class VisualGridStrategy:
    """Detect fields using OpenCV grid detection."""
    
    STRATEGY = Strategy.VISUAL_GRID
    
    def detect(self, cache: PageCache, dpi: int = 100) -> List[DetectedField]:
        """Uses rendered images to find grid lines."""
        if not PDF2IMAGE_AVAILABLE:
            return []
        
        fields = []
        
        try:
            images = convert_from_path(str(cache.pdf_path), dpi=dpi)
        except Exception as e:
            logger.warning(f"Visual grid: Could not render PDF: {e}")
            return []
        
        for page_num, pil_img in enumerate(images):
            page_data = cache[page_num]
            if not page_data:
                continue
            
            page_w = page_data["width"]
            page_h = page_data["height"]
            
            # Convert to OpenCV
            img = np.array(pil_img)
            gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
            img_h, img_w = gray.shape
            
            scale_x = page_w / img_w
            scale_y = page_h / img_h
            
            # Edge detection
            edges = cv2.Canny(gray, 50, 150, apertureSize=3)
            
            # Hough lines
            lines = cv2.HoughLinesP(
                edges, 1, np.pi/180, 
                threshold=60,
                minLineLength=40,
                maxLineGap=10
            )
            
            if lines is None:
                continue
            
            # Find horizontal lines
            h_lines = []
            for line in lines:
                x1, y1, x2, y2 = line[0]
                if abs(y2 - y1) < 5:  # Horizontal
                    h_lines.append((min(x1, x2), y1, max(x1, x2), y2))
            
            # Cluster and create fields
            for hline in h_lines[:50]:  # Limit processing
                lx0, ly, lx1, _ = hline
                w = lx1 - lx0
                
                if w > 50:
                    # Convert to PDF coords
                    px0 = lx0 * scale_x
                    px1 = lx1 * scale_x
                    py = page_h - (ly * scale_y)
                    
                    fields.append(DetectedField(
                        x0=px0, y0=py - 2, x1=px1, y1=py + 14,
                        page=page_num,
                        field_type=FieldType.TEXT,
                        confidence=0.55,
                        strategies={self.STRATEGY}
                    ))
        
        return fields


# =============================================================================
# VOTE AGGREGATOR
# =============================================================================

class VoteAggregator:
    """Merge overlapping fields and count votes."""
    
    def __init__(self, iou_threshold: float = None):
        self.iou_threshold = iou_threshold or TunedParams.OVERLAP_IOU_THRESHOLD
    
    def aggregate(self, all_fields: List[DetectedField]) -> List[DetectedField]:
        """Merge overlapping fields and combine their strategy votes."""
        if not all_fields:
            return []
        
        # Group by page
        by_page = defaultdict(list)
        for f in all_fields:
            by_page[f.page].append(f)
        
        merged = []
        
        for page_num, page_fields in by_page.items():
            # Sort by confidence
            page_fields.sort(key=lambda x: x.confidence, reverse=True)
            used = [False] * len(page_fields)
            
            for i, f in enumerate(page_fields):
                if used[i]:
                    continue
                
                # Find and merge overlapping
                for j in range(i + 1, len(page_fields)):
                    if used[j]:
                        continue
                    
                    if f.iou(page_fields[j]) >= self.iou_threshold:
                        other = page_fields[j]
                        
                        # Merge strategies
                        f.strategies = f.strategies.union(other.strategies)
                        
                        # Average bounds
                        f.x0 = (f.x0 + other.x0) / 2
                        f.y0 = (f.y0 + other.y0) / 2
                        f.x1 = (f.x1 + other.x1) / 2
                        f.y1 = (f.y1 + other.y1) / 2
                        
                        # Keep best confidence and type
                        if other.confidence > f.confidence:
                            if other.field_type != FieldType.UNKNOWN:
                                f.field_type = other.field_type
                            f.confidence = other.confidence
                        
                        # Take label if missing
                        if not f.label and other.label:
                            f.label = other.label
                        
                        used[j] = True
                
                used[i] = True
                merged.append(f)
        
        return merged


# =============================================================================
# CONFIDENCE CALCULATOR  
# =============================================================================

class ConfidenceCalculator:
    """Calculate final confidence based on multiple factors."""
    
    def calculate(self, field: DetectedField) -> float:
        """Calculate final confidence for a field."""
        base_conf = field.confidence
        
        # Boost for weighted votes
        weighted = field.weighted_votes
        vote_boost = min(0.2, (weighted - 1) * 0.1)
        
        # Boost for explicit label
        label_boost = 0.1 if field.label else 0.0
        
        # Penalty for very small or large fields
        size_penalty = 0.0
        if field.area < 50:
            size_penalty = 0.15
        elif field.area > 50000:
            size_penalty = 0.1
        
        final = base_conf + vote_boost + label_boost - size_penalty
        return max(0.0, min(1.0, final))


# =============================================================================
# LABEL FINDER
# =============================================================================

class LabelFinder:
    """Find text labels for detected fields."""
    
    def __init__(self, cache: PageCache):
        self.cache = cache
        self.words_pdf = {}
        
        for page_num, data in cache.pages.items():
            page_h = data["height"]
            self.words_pdf[page_num] = [
                {
                    "text": w["text"],
                    "x0": float(w["x0"]),
                    "x1": float(w["x1"]),
                    "y0": page_h - float(w["bottom"]),
                    "y1": page_h - float(w["top"]),
                }
                for w in data["words"]
            ]
    
    def find_label(self, field: DetectedField) -> str:
        if field.label:
            return field.label
        
        words = self.words_pdf.get(field.page, [])
        if not words:
            return "Field"
        
        fy_center = (field.y0 + field.y1) / 2
        candidates = []
        
        for w in words:
            wy_center = (w["y0"] + w["y1"]) / 2
            text = w["text"].strip()
            
            if not text or len(text) < 2:
                continue
            
            # Same row, to the left
            if abs(wy_center - fy_center) < 12:
                gap = field.x0 - w["x1"]
                if 0 < gap < 150:
                    score = gap
                    if text.endswith(":"):
                        score *= 0.3
                    candidates.append((text.rstrip(":?"), score))
            
            # Above
            elif w["y0"] < field.y1 and field.y1 - w["y0"] < 25:
                overlap_x = min(field.x1, w["x1"]) - max(field.x0, w["x0"])
                if overlap_x > 0:
                    score = (field.y1 - w["y0"]) * 1.5 + 50
                    candidates.append((text.rstrip(":?"), score))
        
        if not candidates:
            return "Field"
        
        candidates.sort(key=lambda x: x[1])
        return candidates[0][0]


# =============================================================================
# PDF GENERATOR
# =============================================================================

class FillablePDFGenerator:
    """Generate final fillable PDF."""
    
    def __init__(self, input_path: str, output_path: str):
        self.input_path = input_path
        self.output_path = output_path
        self.name_counter = defaultdict(int)
    
    def _unique_name(self, label: str) -> str:
        clean = re.sub(r'[^A-Za-z0-9_]', '', label.replace(' ', '_'))
        if not clean:
            clean = "Field"
        
        self.name_counter[clean] += 1
        count = self.name_counter[clean]
        return f"{clean}_{count}" if count > 1 else clean
    
    def generate(self, fields: List[DetectedField]) -> str:
        with pikepdf.open(self.input_path) as pdf:
            if '/AcroForm' not in pdf.Root:
                pdf.Root.AcroForm = pikepdf.Dictionary({
                    '/Fields': pikepdf.Array([]),
                    '/NeedAppearances': True
                })
            
            acroform = pdf.Root.AcroForm
            acroform.NeedAppearances = True
            acroform.Fields = pikepdf.Array([])
            
            for f in fields:
                page = pdf.pages[f.page]
                name = self._unique_name(f.label)
                
                widget = {
                    '/Type': pikepdf.Name('/Annot'),
                    '/Subtype': pikepdf.Name('/Widget'),
                    '/T': pikepdf.String(name),
                    '/Rect': pikepdf.Array([f.x0, f.y0, f.x1, f.y1]),
                    '/F': 4,
                    '/MK': pikepdf.Dictionary({
                        '/BC': pikepdf.Array([0, 0, 0]),
                        '/BG': pikepdf.Array([1, 1, 1]),
                    })
                }
                
                if f.field_type == FieldType.CHECKBOX:
                    widget['/FT'] = pikepdf.Name('/Btn')
                    widget['/Ff'] = 0
                    widget['/V'] = pikepdf.Name('/Off')
                    widget['/DA'] = pikepdf.String("/ZapfDingbats 0 Tf 0 g")
                elif f.field_type == FieldType.RADIO:
                    widget['/FT'] = pikepdf.Name('/Btn')
                    widget['/Ff'] = 32768
                    widget['/DA'] = pikepdf.String("/ZapfDingbats 0 Tf 0 g")
                elif f.field_type == FieldType.DROPDOWN:
                    widget['/FT'] = pikepdf.Name('/Ch')
                    widget['/Ff'] = 131072
                    widget['/DA'] = pikepdf.String("/Helv 10 Tf 0 g")
                else:
                    widget['/FT'] = pikepdf.Name('/Tx')
                    widget['/DA'] = pikepdf.String("/Helv 10 Tf 0 g")
                
                annot = pdf.make_indirect(pikepdf.Dictionary(widget))
                
                if '/Annots' not in page:
                    page.Annots = pdf.make_indirect(pikepdf.Array())
                page.Annots.append(annot)
                acroform.Fields.append(annot)
            
            pdf.save(self.output_path)
        
        return self.output_path


# =============================================================================
# MAIN ENGINE
# =============================================================================

class EnsembleEngine:
    """Main ensemble detection engine v2."""
    
    def __init__(self, pdf_path: str, output_path: str = None,
                 min_votes: int = None, min_confidence: float = None,
                 skip_visual: bool = False):
        self.pdf_path = Path(pdf_path)
        self.output_path = output_path or str(
            self.pdf_path.parent / f"{self.pdf_path.stem}_fillable.pdf"
        )
        self.min_votes = min_votes if min_votes is not None else TunedParams.MIN_VOTES_DEFAULT
        self.min_confidence = min_confidence if min_confidence is not None else TunedParams.MIN_CONFIDENCE
        self.skip_visual = skip_visual
        
        self.cache = None
        self.aggregator = VoteAggregator()
        self.confidence_calc = ConfidenceCalculator()
    
    def process(self) -> str:
        """Run full ensemble pipeline."""
        logger.info(f"\n🚀 Ensemble Engine v2 - Processing: {self.pdf_path.name}")
        logger.info("=" * 60)
        
        # Load and cache
        logger.info("\n📄 Loading PDF...")
        self.cache = PageCache(str(self.pdf_path))
        logger.info(f"   {len(self.cache)} pages loaded")
        
        # Run strategies
        logger.info("\n🔍 Running detection strategies...")
        all_fields = []
        
        strategies = [
            ("Vector", VectorStrategy()),
            ("Unicode", UnicodeCheckboxStrategy()),
            ("Table", TableStrategy()),
            ("TextPattern", TextPatternStrategy()),
            ("Underline", UnderlineStrategy()),
        ]
        
        if not self.skip_visual and PDF2IMAGE_AVAILABLE:
            strategies.append(("VisualGrid", VisualGridStrategy()))
        
        for name, strategy in strategies:
            try:
                fields = strategy.detect(self.cache)
                all_fields.extend(fields)
                logger.info(f"   [{name}] {len(fields)} candidates")
            except Exception as e:
                logger.warning(f"   [{name}] Error: {e}")
        
        logger.info(f"\n   Total candidates: {len(all_fields)}")
        
        # Aggregate votes
        logger.info("\n🗳️ Aggregating votes...")
        merged = self.aggregator.aggregate(all_fields)
        logger.info(f"   After merging: {len(merged)}")
        
        # Filter by votes
        voted = [f for f in merged if f.weighted_votes >= self.min_votes]
        logger.info(f"   After vote filter (≥{self.min_votes}): {len(voted)}")
        
        # Calculate confidence
        for f in voted:
            f.confidence = self.confidence_calc.calculate(f)
        
        # Filter by confidence
        confident = [f for f in voted if f.confidence >= self.min_confidence]
        logger.info(f"   After confidence filter (≥{self.min_confidence}): {len(confident)}")
        
        # Find labels
        logger.info("\n📝 Finding labels...")
        labeler = LabelFinder(self.cache)
        for f in confident:
            if not f.label:
                f.label = labeler.find_label(f)
        
        # Generate PDF
        logger.info("\n💾 Generating fillable PDF...")
        generator = FillablePDFGenerator(str(self.pdf_path), self.output_path)
        output = generator.generate(confident)
        
        # Summary
        type_counts = Counter(f.field_type.value for f in confident)
        logger.info(f"\n✅ Complete! Generated {len(confident)} fields:")
        for ft, count in sorted(type_counts.items()):
            logger.info(f"   - {ft}: {count}")
        
        logger.info(f"\n📄 Output: {output}")
        return output


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Ensemble PDF Field Detector v2 (Tuned)",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("input_pdf", help="Input PDF file")
    parser.add_argument("output_pdf", nargs="?", help="Output PDF (optional)")
    parser.add_argument("--min-votes", type=int, default=2,
                        help="Minimum strategy votes required (default: 2)")
    parser.add_argument("--min-confidence", type=float, default=0.5,
                        help="Minimum confidence threshold (default: 0.5)")
    parser.add_argument("--skip-visual", action="store_true",
                        help="Skip visual grid detection (faster)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Verbose output")
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    engine = EnsembleEngine(
        args.input_pdf,
        output_path=args.output_pdf,
        min_votes=args.min_votes,
        min_confidence=args.min_confidence,
        skip_visual=args.skip_visual
    )
    
    output = engine.process()
    print(f"\n✅ Success: {output}")


if __name__ == "__main__":
    main()
