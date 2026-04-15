#!/usr/bin/env python3
"""
Hybrid PDF Fillable Field Detector v1.0
========================================

Combines rule-based detection (from smart_fillable_v22) with 
ML-based field classification for maximum accuracy.

ARCHITECTURE:
┌─────────────────────────────────────────────────────────────────┐
│                  HYBRID DETECTION SYSTEM                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  STAGE 1: RULE-BASED DETECTION (High Recall)                   │
│  ─────────────────────────────────────────────                 │
│  → Vector detection (checkboxes, squares)                      │
│  → Table structure analysis (pdfplumber)                       │
│  → Text pattern matching ("Label:")                            │
│  → Underline detection (signatures)                            │
│  → Unicode checkbox detection (☐ ☑)                            │
│                                                                 │
│  STAGE 2: ML CLASSIFIER (High Precision)                       │
│  ─────────────────────────────────────────                     │
│  → Crops each candidate field                                  │
│  → Classifies: text/checkbox/radio/dropdown/not_a_field        │
│  → Filters false positives from Stage 1                        │
│                                                                 │
│  STAGE 3: VOTING & REFINEMENT                                  │
│  ─────────────────────────────────────                         │
│  → Cross-validates rules vs ML                                 │
│  → Resolves conflicts                                          │
│  → Final confidence scoring                                    │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘

Usage:
    # Without ML (rules-only mode):
    python smart_fillable_hybrid.py input.pdf output.pdf
    
    # With ML classifier:
    python smart_fillable_hybrid.py input.pdf output.pdf --classifier ./classifier_model.pt

Author: FillThatPDF Team
Date: February 2026
"""

import sys
import os
import re
import json
import argparse
import logging
from pathlib import Path
from collections import defaultdict, Counter
from typing import List, Dict, Tuple, Optional, Set, Any
from dataclasses import dataclass, field
from enum import Enum
import math

import pdfplumber
import pikepdf
import cv2
import numpy as np

# Optional ML imports
try:
    import torch
    import torch.nn as nn
    from torchvision import transforms, models
    ML_AVAILABLE = True
except ImportError:
    ML_AVAILABLE = False

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)
logging.getLogger('pdfminer').setLevel(logging.WARNING)
logging.getLogger('pdfplumber').setLevel(logging.WARNING)


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
    NOT_A_FIELD = "not_a_field"
    UNKNOWN = "unknown"


class DetectionSource(Enum):
    VECTOR = "vector"
    TABLE = "table"
    TEXT_PATTERN = "text_pattern"
    UNDERLINE = "underline"
    UNICODE_CHECKBOX = "unicode_checkbox"
    ML_CLASSIFIER = "ml_classifier"


@dataclass
class CandidateField:
    """A candidate form field from rule-based detection."""
    x0: float
    y0: float  # Bottom-up PDF coordinates
    x1: float
    y1: float
    page: int
    field_type: FieldType = FieldType.UNKNOWN
    label: str = ""
    confidence: float = 0.5
    sources: Set[DetectionSource] = field(default_factory=set)
    ml_prediction: Optional[FieldType] = None
    ml_confidence: float = 0.0
    metadata: Dict = field(default_factory=dict)
    
    @property
    def width(self) -> float:
        return abs(self.x1 - self.x0)
    
    @property
    def height(self) -> float:
        return abs(self.y1 - self.y0)
    
    @property
    def center(self) -> Tuple[float, float]:
        return ((self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2)
    
    @property
    def area(self) -> float:
        return self.width * self.height
    
    @property
    def final_confidence(self) -> float:
        """Combined confidence from rules + ML."""
        if self.ml_confidence > 0:
            # Weighted average: 40% rules, 60% ML
            return 0.4 * self.confidence + 0.6 * self.ml_confidence
        return self.confidence
    
    def overlaps(self, other: 'CandidateField', threshold: float = 0.3) -> bool:
        """Check if two fields overlap significantly."""
        if self.page != other.page:
            return False
        
        ix0 = max(self.x0, other.x0)
        iy0 = max(self.y0, other.y0)
        ix1 = min(self.x1, other.x1)
        iy1 = min(self.y1, other.y1)
        
        if ix1 <= ix0 or iy1 <= iy0:
            return False
        
        intersection = (ix1 - ix0) * (iy1 - iy0)
        union = self.area + other.area - intersection
        
        return (intersection / union) >= threshold if union > 0 else False


# =============================================================================
# RULE-BASED DETECTOR
# =============================================================================

class RuleBasedDetector:
    """
    Rule-based field detection using PDF structure analysis.
    
    This is the PRIMARY detection engine - designed for HIGH RECALL.
    We'd rather detect too many candidates than miss real fields.
    The ML classifier will filter out false positives.
    """
    
    def __init__(self, pdf_path: str):
        self.pdf_path = Path(pdf_path)
        self.candidates: List[CandidateField] = []
        self.page_data = {}  # Cache for page metadata
        
    def detect_all(self) -> List[CandidateField]:
        """Run all detection strategies."""
        logger.info("📋 Stage 1: Rule-Based Detection")
        
        with pdfplumber.open(self.pdf_path) as pdf:
            # Cache page data
            for i, page in enumerate(pdf.pages):
                self.page_data[i] = {
                    "width": float(page.width),
                    "height": float(page.height),
                    "words": page.extract_words(x_tolerance=3, y_tolerance=3),
                    "lines": page.lines or [],
                    "rects": page.rects or [],
                    "curves": page.curves or [],
                    "chars": page.chars or [],
                }
            
            # Run detectors
            self._detect_vector_checkboxes()
            self._detect_unicode_checkboxes()
            self._detect_table_cells()
            self._detect_text_patterns()
            self._detect_underlines()
        
        logger.info(f"   → Found {len(self.candidates)} candidates")
        return self.candidates
    
    def _detect_vector_checkboxes(self):
        """Detect checkboxes/radios from vector rectangles."""
        count = 0
        
        for page_num, data in self.page_data.items():
            page_h = data["height"]
            
            for rect in data["rects"]:
                x0 = float(rect.get("x0", 0))
                x1 = float(rect.get("x1", 0))
                top = float(rect.get("top", 0))
                bottom = float(rect.get("bottom", 0))
                
                w = abs(x1 - x0)
                h = abs(bottom - top)
                
                # Checkbox heuristics: 4-18pt square
                if 4 <= w <= 18 and 4 <= h <= 18:
                    aspect = max(w, h) / min(w, h) if min(w, h) > 0 else 99
                    if aspect < 1.5:  # Square-ish
                        # Convert to bottom-up
                        y0 = page_h - bottom
                        y1 = page_h - top
                        
                        self.candidates.append(CandidateField(
                            x0=x0, y0=y0, x1=x1, y1=y1,
                            page=page_num,
                            field_type=FieldType.CHECKBOX,
                            confidence=0.85,
                            sources={DetectionSource.VECTOR}
                        ))
                        count += 1
            
            # Check curves for circles (radio buttons)
            for curve in data["curves"]:
                pts = curve.get("pts", [])
                if len(pts) >= 4:
                    xs = [p[0] for p in pts]
                    ys = [p[1] for p in pts]
                    x0, x1 = min(xs), max(xs)
                    top, bottom = min(ys), max(ys)
                    w = x1 - x0
                    h = bottom - top
                    
                    if 4 <= w <= 16 and 4 <= h <= 16:
                        aspect = max(w, h) / min(w, h) if min(w, h) > 0 else 99
                        if aspect < 1.3:  # Circle-ish
                            y0_pdf = page_h - bottom
                            y1_pdf = page_h - top
                            
                            self.candidates.append(CandidateField(
                                x0=x0, y0=y0_pdf, x1=x1, y1=y1_pdf,
                                page=page_num,
                                field_type=FieldType.RADIO,
                                confidence=0.7,
                                sources={DetectionSource.VECTOR}
                            ))
                            count += 1
        
        logger.info(f"   [Vector] {count} checkboxes/radios")
    
    def _detect_unicode_checkboxes(self):
        """Detect Unicode checkbox characters (☐ ☑ ☒ □ ■)."""
        count = 0
        checkbox_chars = {'☐', '☑', '☒', '□', '■', '◻', '◼', '○', '●', '◯'}
        
        for page_num, data in self.page_data.items():
            page_h = data["height"]
            
            for char in data["chars"]:
                if char.get("text", "") in checkbox_chars:
                    x0 = float(char["x0"])
                    x1 = float(char["x1"])
                    top = float(char["top"])
                    bottom = float(char["bottom"])
                    
                    y0 = page_h - bottom
                    y1 = page_h - top
                    
                    self.candidates.append(CandidateField(
                        x0=x0, y0=y0, x1=x1, y1=y1,
                        page=page_num,
                        field_type=FieldType.CHECKBOX,
                        confidence=0.9,
                        sources={DetectionSource.UNICODE_CHECKBOX}
                    ))
                    count += 1
        
        logger.info(f"   [Unicode] {count} checkbox chars")
    
    def _detect_table_cells(self):
        """Detect empty table cells as potential text fields."""
        count = 0
        
        with pdfplumber.open(self.pdf_path) as pdf:
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
                            if cell is None or len(cell) < 4:
                                continue
                            
                            x0, top, x1, bottom = cell[:4]
                            w = x1 - x0
                            h = bottom - top
                            
                            # Filter reasonable sizes
                            if w < 15 or h < 8 or w > 400 or h > 80:
                                continue
                            
                            # Check if cell has content
                            try:
                                cell_text = page.within_bbox((x0, top, x1, bottom)).extract_text() or ""
                            except:
                                cell_text = ""
                            
                            is_empty = len(cell_text.strip()) < 3
                            
                            # Convert to bottom-up
                            y0 = page_h - bottom
                            y1 = page_h - top
                            
                            if is_empty or w > 60:  # Empty or wide = likely fillable
                                self.candidates.append(CandidateField(
                                    x0=x0, y0=y0, x1=x1, y1=y1,
                                    page=page_num,
                                    field_type=FieldType.TEXT,
                                    confidence=0.7 if is_empty else 0.5,
                                    sources={DetectionSource.TABLE},
                                    metadata={"cell_text": cell_text[:30]}
                                ))
                                count += 1
        
        logger.info(f"   [Table] {count} table cells")
    
    def _detect_text_patterns(self):
        """Detect fields after label patterns like 'Name:' or 'Date?'."""
        count = 0
        
        for page_num, data in self.page_data.items():
            page_h = data["height"]
            page_w = data["width"]
            words = data["words"]
            
            for i, word in enumerate(words):
                text = word.get("text", "")
                
                # Check for label patterns
                if not (text.endswith(":") or text.endswith("?")):
                    continue
                
                wx1 = float(word["x1"])
                wtop = float(word["top"])
                wbot = float(word["bottom"])
                
                # Look for space after label
                field_x0 = wx1 + 3
                field_x1 = page_w - 36  # Default to page margin
                
                # Find next word on same line
                for j in range(i + 1, min(i + 5, len(words))):
                    nw = words[j]
                    if abs(float(nw["top"]) - wtop) < 8:
                        gap = float(nw["x0"]) - wx1
                        if gap > 20:
                            field_x1 = float(nw["x0"]) - 3
                            break
                        elif gap < 20:
                            # Next word too close, not a field
                            field_x1 = 0
                            break
                
                if field_x1 > field_x0 + 20:
                    y0 = page_h - wbot - 2
                    y1 = page_h - wtop + 2
                    
                    label = text.rstrip(":?")
                    field_type = FieldType.DATE if "date" in label.lower() else FieldType.TEXT
                    
                    self.candidates.append(CandidateField(
                        x0=field_x0, y0=y0, x1=field_x1, y1=y1,
                        page=page_num,
                        field_type=field_type,
                        label=label,
                        confidence=0.75,
                        sources={DetectionSource.TEXT_PATTERN}
                    ))
                    count += 1
        
        logger.info(f"   [TextPattern] {count} label-based fields")
    
    def _detect_underlines(self):
        """Detect signature/date lines from horizontal rules."""
        count = 0
        signature_keywords = {"signature", "sign", "signed", "print name", "printed name", "date"}
        
        for page_num, data in self.page_data.items():
            page_h = data["height"]
            words = data["words"]
            
            for line in data["lines"]:
                x0 = float(line.get("x0", 0))
                x1 = float(line.get("x1", 0))
                y_top = float(line.get("top", line.get("y0", 0)))
                y_bot = float(line.get("bottom", line.get("y1", y_top)))
                
                w = x1 - x0
                h = abs(y_bot - y_top)
                
                # Horizontal line: wide and thin
                if w > 60 and h < 4:
                    # Check for signature keywords nearby
                    is_signature = False
                    label = ""
                    field_type = FieldType.TEXT
                    
                    for word in words:
                        word_bot = float(word["bottom"])
                        word_x0 = float(word["x0"])
                        word_x1 = float(word["x1"])
                        
                        # Above the line, horizontally overlapping
                        if word_bot < y_top and y_top - word_bot < 25:
                            if word_x0 < x1 and word_x1 > x0:
                                word_text = word.get("text", "").lower()
                                for kw in signature_keywords:
                                    if kw in word_text:
                                        is_signature = True
                                        label = word.get("text", "").title()
                                        if "date" in word_text:
                                            field_type = FieldType.DATE
                                        else:
                                            field_type = FieldType.SIGNATURE
                                        break
                    
                    y0 = page_h - y_bot - 2
                    y1 = page_h - y_top + 14
                    
                    self.candidates.append(CandidateField(
                        x0=x0, y0=y0, x1=x1, y1=y1,
                        page=page_num,
                        field_type=field_type,
                        label=label,
                        confidence=0.8 if is_signature else 0.5,
                        sources={DetectionSource.UNDERLINE}
                    ))
                    count += 1
        
        logger.info(f"   [Underline] {count} signature/date lines")


# =============================================================================
# ML FIELD CLASSIFIER
# =============================================================================

class FieldClassifier:
    """
    ML-based field type classifier.
    
    Takes cropped images of candidate fields and classifies them as:
    - text, checkbox, radio, dropdown, not_a_field
    
    This is used for PRECISION - filtering out false positives.
    """
    
    CLASS_NAMES = ["text", "checkbox", "radio", "dropdown", "not_a_field"]
    
    def __init__(self, model_path: Optional[str] = None):
        self.model = None
        self.transform = None
        self.device = "cpu"
        
        if model_path and ML_AVAILABLE:
            self._load_model(model_path)
    
    def _load_model(self, model_path: str):
        """Load trained classifier model."""
        try:
            self.device = "mps" if torch.backends.mps.is_available() else "cpu"
            
            # Create model architecture
            self.model = models.mobilenet_v3_small(weights=None)
            self.model.classifier[-1] = nn.Linear(
                self.model.classifier[-1].in_features,
                len(self.CLASS_NAMES)
            )
            
            # Load weights
            state_dict = torch.load(model_path, map_location=self.device)
            self.model.load_state_dict(state_dict)
            self.model.to(self.device)
            self.model.eval()
            
            # Transform
            self.transform = transforms.Compose([
                transforms.ToPILImage(),
                transforms.Resize((64, 128)),
                transforms.ToTensor(),
                transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
            ])
            
            logger.info(f"   ✓ Loaded classifier: {model_path}")
        except Exception as e:
            logger.warning(f"   ⚠️ Could not load classifier: {e}")
            self.model = None
    
    @property
    def is_available(self) -> bool:
        return self.model is not None
    
    def classify(self, image: np.ndarray) -> Tuple[FieldType, float]:
        """
        Classify a cropped field image.
        
        Returns: (field_type, confidence)
        """
        if not self.is_available:
            return FieldType.UNKNOWN, 0.0
        
        try:
            # Ensure RGB
            if len(image.shape) == 2:
                image = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
            elif image.shape[2] == 4:
                image = cv2.cvtColor(image, cv2.COLOR_BGRA2RGB)
            elif image.shape[2] == 3:
                image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            
            # Transform
            tensor = self.transform(image).unsqueeze(0).to(self.device)
            
            # Predict
            with torch.no_grad():
                outputs = self.model(tensor)
                probs = torch.softmax(outputs, dim=1)
                conf, pred_idx = probs.max(1)
            
            class_name = self.CLASS_NAMES[pred_idx.item()]
            confidence = conf.item()
            
            # Map to FieldType
            type_map = {
                "text": FieldType.TEXT,
                "checkbox": FieldType.CHECKBOX,
                "radio": FieldType.RADIO,
                "dropdown": FieldType.DROPDOWN,
                "not_a_field": FieldType.NOT_A_FIELD
            }
            
            return type_map.get(class_name, FieldType.UNKNOWN), confidence
            
        except Exception as e:
            logger.debug(f"Classification error: {e}")
            return FieldType.UNKNOWN, 0.0


# =============================================================================
# CANDIDATE MERGER & REFINER
# =============================================================================

class CandidateMerger:
    """Merge overlapping candidates and combine their sources."""
    
    def __init__(self, overlap_threshold: float = 0.3):
        self.overlap_threshold = overlap_threshold
    
    def merge(self, candidates: List[CandidateField]) -> List[CandidateField]:
        """Merge overlapping candidates."""
        if not candidates:
            return []
        
        # Group by page
        by_page = defaultdict(list)
        for c in candidates:
            by_page[c.page].append(c)
        
        merged = []
        
        for page_num, page_candidates in by_page.items():
            # Sort by confidence
            page_candidates.sort(key=lambda x: x.confidence, reverse=True)
            used = [False] * len(page_candidates)
            
            for i, c in enumerate(page_candidates):
                if used[i]:
                    continue
                
                # Merge with overlapping
                for j in range(i + 1, len(page_candidates)):
                    if used[j]:
                        continue
                    
                    other = page_candidates[j]
                    if c.overlaps(other, self.overlap_threshold):
                        # Merge sources
                        c.sources = c.sources.union(other.sources)
                        # Keep best type/confidence
                        if other.confidence > c.confidence:
                            if other.field_type != FieldType.UNKNOWN:
                                c.field_type = other.field_type
                            c.confidence = other.confidence
                        # Average bounds
                        c.x0 = (c.x0 + other.x0) / 2
                        c.y0 = (c.y0 + other.y0) / 2
                        c.x1 = (c.x1 + other.x1) / 2
                        c.y1 = (c.y1 + other.y1) / 2
                        # Take label if we don't have one
                        if not c.label and other.label:
                            c.label = other.label
                        used[j] = True
                
                used[i] = True
                merged.append(c)
        
        return merged


# =============================================================================
# LABEL FINDER
# =============================================================================

class LabelFinder:
    """Find text labels for detected fields."""
    
    def __init__(self, pdf_path: str):
        self.pdf_path = pdf_path
        self.words_by_page = {}
        self._load_words()
    
    def _load_words(self):
        with pdfplumber.open(self.pdf_path) as pdf:
            for i, page in enumerate(pdf.pages):
                page_h = float(page.height)
                words = page.extract_words(x_tolerance=3, y_tolerance=3)
                self.words_by_page[i] = [
                    {
                        "text": w["text"],
                        "x0": float(w["x0"]),
                        "x1": float(w["x1"]),
                        "y0": page_h - float(w["bottom"]),
                        "y1": page_h - float(w["top"]),
                    }
                    for w in words
                ]
    
    def find_label(self, field: CandidateField) -> str:
        """Find best label for a field."""
        if field.label:
            return field.label
        
        words = self.words_by_page.get(field.page, [])
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
            elif w["y0"] < field.y1 and w["y0"] > field.y1 - 25:
                overlap_x = min(field.x1, w["x1"]) - max(field.x0, w["x0"])
                if overlap_x > 0:
                    score = (field.y1 - w["y0"]) * 1.5 + 50
                    candidates.append((text.rstrip(":?"), score))
        
        if not candidates:
            return "Field"
        
        candidates.sort(key=lambda x: x[1])
        return candidates[0][0]
    
    def label_all(self, fields: List[CandidateField]) -> List[CandidateField]:
        for f in fields:
            if not f.label:
                f.label = self.find_label(f)
        return fields


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
    
    def generate(self, fields: List[CandidateField]) -> str:
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
                if f.field_type == FieldType.NOT_A_FIELD:
                    continue
                
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
# MAIN HYBRID ENGINE
# =============================================================================

class HybridEngine:
    """
    Main orchestrator for hybrid detection.
    """
    
    def __init__(self, pdf_path: str, output_path: str = None, 
                 classifier_path: str = None,
                 min_confidence: float = 0.5):
        self.pdf_path = Path(pdf_path)
        self.output_path = output_path or str(self.pdf_path.parent / f"{self.pdf_path.stem}_fillable.pdf")
        self.min_confidence = min_confidence
        
        # Components
        self.detector = RuleBasedDetector(str(pdf_path))
        self.classifier = FieldClassifier(classifier_path) if classifier_path else None
        self.merger = CandidateMerger()
        self.labeler = LabelFinder(str(pdf_path))
    
    def _crop_field_image(self, page_image: np.ndarray, field: CandidateField,
                          page_width: float, page_height: float,
                          padding: int = 5) -> Optional[np.ndarray]:
        """Crop field region for ML classification."""
        img_h, img_w = page_image.shape[:2]
        scale_x = img_w / page_width
        scale_y = img_h / page_height
        
        # Convert to image coords
        ix0 = int(field.x0 * scale_x) - padding
        ix1 = int(field.x1 * scale_x) + padding
        iy0 = int((page_height - field.y1) * scale_y) - padding
        iy1 = int((page_height - field.y0) * scale_y) + padding
        
        # Clamp
        ix0 = max(0, ix0)
        iy0 = max(0, iy0)
        ix1 = min(img_w, ix1)
        iy1 = min(img_h, iy1)
        
        if ix1 <= ix0 or iy1 <= iy0:
            return None
        
        return page_image[iy0:iy1, ix0:ix1]
    
    def process(self) -> str:
        """Run the full hybrid pipeline."""
        logger.info(f"\n🚀 Hybrid Engine Processing: {self.pdf_path.name}")
        logger.info("=" * 60)
        
        # Stage 1: Rule-based detection
        candidates = self.detector.detect_all()
        
        # Stage 2: Merge overlapping
        logger.info("\n🔗 Merging overlapping candidates...")
        merged = self.merger.merge(candidates)
        logger.info(f"   → {len(candidates)} → {len(merged)} after merging")
        
        # Stage 3: ML Classification (if available)
        if self.classifier and self.classifier.is_available:
            logger.info("\n🧠 Stage 2: ML Classification")
            
            # Render pages for cropping
            from pdf2image import convert_from_path
            images = convert_from_path(str(self.pdf_path), dpi=150)
            
            # Get page dimensions
            with pdfplumber.open(self.pdf_path) as pdf:
                page_dims = [(float(p.width), float(p.height)) for p in pdf.pages]
            
            classified_count = 0
            rejected_count = 0
            
            for field in merged:
                if field.page >= len(images):
                    continue
                
                page_img = np.array(images[field.page])
                page_w, page_h = page_dims[field.page]
                
                crop = self._crop_field_image(page_img, field, page_w, page_h)
                if crop is None or crop.size == 0:
                    continue
                
                ml_type, ml_conf = self.classifier.classify(crop)
                field.ml_prediction = ml_type
                field.ml_confidence = ml_conf
                
                if ml_type == FieldType.NOT_A_FIELD and ml_conf > 0.7:
                    field.field_type = FieldType.NOT_A_FIELD
                    rejected_count += 1
                elif ml_conf > 0.6:
                    field.field_type = ml_type
                    classified_count += 1
            
            logger.info(f"   → {classified_count} classified, {rejected_count} rejected")
        
        # Stage 4: Filter by confidence
        logger.info(f"\n📊 Filtering by confidence (min={self.min_confidence})...")
        filtered = [f for f in merged 
                    if f.final_confidence >= self.min_confidence 
                    and f.field_type != FieldType.NOT_A_FIELD]
        logger.info(f"   → {len(merged)} → {len(filtered)} after filtering")
        
        # Stage 5: Label assignment
        logger.info("\n📝 Assigning labels...")
        labeled = self.labeler.label_all(filtered)
        
        # Stage 6: Generate PDF
        logger.info("\n💾 Generating fillable PDF...")
        generator = FillablePDFGenerator(str(self.pdf_path), self.output_path)
        output = generator.generate(labeled)
        
        # Summary
        type_counts = Counter(f.field_type.value for f in labeled)
        logger.info(f"\n✅ Complete! Generated {len(labeled)} fields:")
        for ft, count in sorted(type_counts.items()):
            logger.info(f"   - {ft}: {count}")
        logger.info(f"\n📄 Output: {output}")
        
        return output


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Hybrid PDF Fillable Field Detector",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("input_pdf", help="Input PDF file")
    parser.add_argument("output_pdf", nargs="?", help="Output PDF path")
    parser.add_argument("--classifier", "-c", help="Path to ML classifier model (.pt)")
    parser.add_argument("--min-confidence", type=float, default=0.5,
                        help="Minimum confidence threshold (default: 0.5)")
    parser.add_argument("--verbose", "-v", action="store_true")
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    engine = HybridEngine(
        args.input_pdf,
        output_path=args.output_pdf,
        classifier_path=args.classifier,
        min_confidence=args.min_confidence
    )
    
    output = engine.process()
    print(f"\n✅ Success: {output}")


if __name__ == "__main__":
    main()
