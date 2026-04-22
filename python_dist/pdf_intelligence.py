#!/usr/bin/env python3
"""
PDF Intelligence Layer — Template Matching + ML Post-Processing + Auto-Learning

This module adds three intelligence features to smart_fillable_v23.py:
1. Template Matching: Recognize previously-processed PDFs and reuse cached field positions
2. ML Post-Processing: CNN-based false positive filtering using MobileNetV3-Small
3. Auto-Learning: Auto-save templates after every successful processing

Integration: Called from smart_fillable_v23.py via 3 hooks (pre_detection, post_detection, on_success).
Fail-safe: Any failure silently falls back to normal v23 behavior.

Version: 1.0
"""

import hashlib
import json
import os
import sys
import time
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Set
from collections import defaultdict

# Optional ML imports — graceful degradation if not available
try:
    import torch
    import torch.nn as nn
    from torchvision import transforms, models
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False

# Optional PyMuPDF for field cropping
try:
    import fitz  # PyMuPDF
    FITZ_AVAILABLE = True
except ImportError:
    FITZ_AVAILABLE = False

# pdfplumber for fingerprinting (always available — v23 depends on it)
try:
    import pdfplumber
    PDFPLUMBER_AVAILABLE = True
except ImportError:
    PDFPLUMBER_AVAILABLE = False

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


# ============================================================================
# LAYOUT FINGERPRINTER
# ============================================================================

class LayoutFingerprinter:
    """
    Generates structural fingerprints of PDFs for template matching.

    A fingerprint captures the PDF's visual structure (lines, tables, text anchors)
    in normalized coordinates, so it matches across minor version changes where
    the form layout stays the same but content (dates, numbers) changes.
    """

    # Grid sampling parameters for text anchors
    GRID_ROWS = 5
    GRID_COLS = 5
    MIN_ANCHOR_LEN = 4      # Minimum word length to be an anchor
    ANCHORS_PER_CELL = 2    # Max anchors per grid cell

    def generate(self, pdf_path: str) -> dict:
        """
        Generate a layout fingerprint for a PDF.

        Returns dict with structural information for comparison.
        """
        pdf_path = str(pdf_path)
        fingerprint = {
            'page_count': 0,
            'page_dimensions': [],
            'text_anchors': {},
            'line_signature': {},
            'table_signature': {},
            'word_density': {},
        }

        try:
            pdf = pdfplumber.open(pdf_path)
        except Exception as e:
            print(f"   [Fingerprint] Could not open PDF: {e}")
            return fingerprint

        try:
            fingerprint['page_count'] = len(pdf.pages)

            for page_num, page in enumerate(pdf.pages):
                pw = float(page.width)
                ph = float(page.height)
                fingerprint['page_dimensions'].append([pw, ph])

                # Text anchors via grid sampling
                anchors = self._extract_text_anchors(page, pw, ph)
                if anchors:
                    fingerprint['text_anchors'][str(page_num)] = anchors

                # Line geometry (normalized)
                lines = self._extract_line_geometry(page, pw, ph)
                if lines:
                    fingerprint['line_signature'][str(page_num)] = lines

                # Table structure
                tables = self._extract_table_structure(page, pw, ph)
                if tables:
                    fingerprint['table_signature'][str(page_num)] = tables

                # Word density
                words = page.extract_words() or []
                area = pw * ph if pw * ph > 0 else 1
                fingerprint['word_density'][str(page_num)] = len(words) / area

        finally:
            pdf.close()

        return fingerprint

    def compare(self, fp_a: dict, fp_b: dict) -> float:
        """
        Compare two fingerprints. Returns similarity score 0.0 - 1.0.

        Weighting:
        - Page dimensions match: 20%
        - Line geometry overlap: 30%
        - Text anchor Jaccard: 30%
        - Table structure match: 20%
        """
        scores = []
        weights = []

        # 1. Page dimensions (20%)
        dim_score = self._compare_dimensions(fp_a, fp_b)
        scores.append(dim_score)
        weights.append(0.20)

        # 2. Line geometry (30%)
        line_score = self._compare_line_signatures(
            fp_a.get('line_signature', {}),
            fp_b.get('line_signature', {}),
            tolerance=0.02
        )
        scores.append(line_score)
        weights.append(0.30)

        # 3. Text anchor Jaccard (30%)
        texts_a = set()
        texts_b = set()
        for anchors in fp_a.get('text_anchors', {}).values():
            texts_a.update(a['text'].lower() for a in anchors)
        for anchors in fp_b.get('text_anchors', {}).values():
            texts_b.update(a['text'].lower() for a in anchors)

        union = texts_a | texts_b
        jaccard = len(texts_a & texts_b) / len(union) if union else 0.0
        scores.append(jaccard)
        weights.append(0.30)

        # 4. Table structure (20%)
        table_score = self._compare_table_signatures(
            fp_a.get('table_signature', {}),
            fp_b.get('table_signature', {})
        )
        scores.append(table_score)
        weights.append(0.20)

        total = sum(s * w for s, w in zip(scores, weights))
        return min(1.0, max(0.0, total))

    def _extract_text_anchors(self, page, pw: float, ph: float) -> List[dict]:
        """Extract stable text anchors using grid sampling."""
        words = page.extract_words() or []
        if not words:
            return []

        cell_w = pw / self.GRID_COLS
        cell_h = ph / self.GRID_ROWS
        anchors = []

        for row in range(self.GRID_ROWS):
            for col in range(self.GRID_COLS):
                cell_x0 = col * cell_w
                cell_y0 = row * cell_h
                cell_x1 = cell_x0 + cell_w
                cell_y1 = cell_y0 + cell_h

                # Find long alphabetic words in this grid cell
                candidates = []
                for w in words:
                    wx = (float(w['x0']) + float(w['x1'])) / 2
                    wy = (float(w['top']) + float(w['bottom'])) / 2
                    if cell_x0 <= wx < cell_x1 and cell_y0 <= wy < cell_y1:
                        text = w['text'].strip()
                        # Only keep long alphabetic words (stable across versions)
                        if len(text) >= self.MIN_ANCHOR_LEN and text.isalpha():
                            candidates.append({
                                'text': text,
                                'x': round(wx / pw, 4),  # Normalized 0-1
                                'y': round(wy / ph, 4),
                            })

                # Keep the longest words per cell (most distinctive)
                candidates.sort(key=lambda c: len(c['text']), reverse=True)
                anchors.extend(candidates[:self.ANCHORS_PER_CELL])

        return anchors

    def _extract_line_geometry(self, page, pw: float, ph: float) -> List[dict]:
        """Extract normalized positions of horizontal and vertical lines."""
        lines_out = []

        # pdfplumber lines
        for line in (page.lines or []):
            x0 = float(line.get('x0', 0))
            y0 = float(line.get('top', line.get('y0', 0)))
            x1 = float(line.get('x1', 0))
            y1 = float(line.get('bottom', line.get('y1', 0)))

            dx = abs(x1 - x0)
            dy = abs(y1 - y0)

            if dx > dy and dx > 20:  # Horizontal line, > 20pt
                lines_out.append({
                    'orientation': 'h',
                    'pos': round((y0 + y1) / 2 / ph, 4),
                    'start': round(min(x0, x1) / pw, 4),
                    'end': round(max(x0, x1) / pw, 4),
                })
            elif dy > dx and dy > 20:  # Vertical line, > 20pt
                lines_out.append({
                    'orientation': 'v',
                    'pos': round((x0 + x1) / 2 / pw, 4),
                    'start': round(min(y0, y1) / ph, 4),
                    'end': round(max(y0, y1) / ph, 4),
                })

        # pdfplumber rects (table borders)
        for rect in (page.rects or []):
            x0 = float(rect.get('x0', 0))
            y0 = float(rect.get('top', rect.get('y0', 0)))
            x1 = float(rect.get('x1', 0))
            y1 = float(rect.get('bottom', rect.get('y1', 0)))
            w = x1 - x0
            h = y1 - y0

            if w > 20 and h < 3:  # Thin horizontal rect (line-like)
                lines_out.append({
                    'orientation': 'h',
                    'pos': round((y0 + y1) / 2 / ph, 4),
                    'start': round(x0 / pw, 4),
                    'end': round(x1 / pw, 4),
                })
            elif h > 20 and w < 3:  # Thin vertical rect (line-like)
                lines_out.append({
                    'orientation': 'v',
                    'pos': round((x0 + x1) / 2 / pw, 4),
                    'start': round(y0 / ph, 4),
                    'end': round(y1 / ph, 4),
                })

        return lines_out

    def _extract_table_structure(self, page, pw: float, ph: float) -> List[dict]:
        """Extract table cell counts and positions."""
        tables_out = []
        try:
            tables = page.find_tables() or []
        except Exception:
            return []

        for table in tables:
            try:
                bbox = table.bbox  # (x0, top, x1, bottom)
                rows = len(table.rows) if hasattr(table, 'rows') else 0
                cells = table.cells if hasattr(table, 'cells') else []

                # Count unique column positions
                col_positions = set()
                for cell in cells:
                    if cell and len(cell) >= 4:
                        col_positions.add(round(cell[0], 1))
                        col_positions.add(round(cell[2], 1))
                cols = max(0, len(col_positions) - 1)

                if rows > 0 or cols > 0:
                    tables_out.append({
                        'cols': cols,
                        'rows': rows,
                        'x0': round(bbox[0] / pw, 4),
                        'y0': round(bbox[1] / ph, 4),
                        'x1': round(bbox[2] / pw, 4),
                        'y1': round(bbox[3] / ph, 4),
                    })
            except Exception:
                continue

        return tables_out

    def _compare_dimensions(self, fp_a: dict, fp_b: dict) -> float:
        """Compare page counts and dimensions."""
        if fp_a['page_count'] != fp_b['page_count']:
            # Different page counts — penalize but don't zero out
            # (some versions add/remove an appendix page)
            page_ratio = min(fp_a['page_count'], fp_b['page_count']) / \
                         max(fp_a['page_count'], fp_b['page_count'])
            return page_ratio * 0.5  # Significant penalty

        if not fp_a['page_dimensions'] or not fp_b['page_dimensions']:
            return 0.0

        dim_diffs = []
        for (wa, ha), (wb, hb) in zip(fp_a['page_dimensions'], fp_b['page_dimensions']):
            total = wa + ha
            if total > 0:
                diff = (abs(wa - wb) + abs(ha - hb)) / total
                dim_diffs.append(1.0 - min(1.0, diff))
            else:
                dim_diffs.append(0.0)

        return sum(dim_diffs) / len(dim_diffs) if dim_diffs else 0.0

    def _compare_line_signatures(self, sig_a: dict, sig_b: dict, tolerance: float = 0.02) -> float:
        """Compare line geometry between two fingerprints."""
        if not sig_a and not sig_b:
            return 1.0  # Both empty = match
        if not sig_a or not sig_b:
            return 0.0

        # Collect all lines across all pages
        lines_a = []
        for page_lines in sig_a.values():
            lines_a.extend(page_lines)
        lines_b = []
        for page_lines in sig_b.values():
            lines_b.extend(page_lines)

        if not lines_a and not lines_b:
            return 1.0
        if not lines_a or not lines_b:
            return 0.0

        # Match lines from A to B within tolerance
        matched = 0
        used_b = set()

        for la in lines_a:
            for j, lb in enumerate(lines_b):
                if j in used_b:
                    continue
                if la['orientation'] != lb['orientation']:
                    continue
                if abs(la['pos'] - lb['pos']) <= tolerance:
                    # Position matches — check overlap
                    overlap_start = max(la['start'], lb['start'])
                    overlap_end = min(la['end'], lb['end'])
                    if overlap_end > overlap_start:
                        matched += 1
                        used_b.add(j)
                        break

        total = max(len(lines_a), len(lines_b))
        return matched / total if total > 0 else 1.0

    def _compare_table_signatures(self, sig_a: dict, sig_b: dict) -> float:
        """Compare table structures between fingerprints."""
        if not sig_a and not sig_b:
            return 1.0
        if not sig_a or not sig_b:
            return 0.3  # One has tables, other doesn't — low but non-zero

        tables_a = []
        for page_tables in sig_a.values():
            tables_a.extend(page_tables)
        tables_b = []
        for page_tables in sig_b.values():
            tables_b.extend(page_tables)

        if not tables_a and not tables_b:
            return 1.0
        if not tables_a or not tables_b:
            return 0.3

        # Match tables by position overlap
        matched = 0
        used_b = set()

        for ta in tables_a:
            for j, tb in enumerate(tables_b):
                if j in used_b:
                    continue
                # Check position overlap (normalized coords)
                ov_x0 = max(ta['x0'], tb['x0'])
                ov_y0 = max(ta['y0'], tb['y0'])
                ov_x1 = min(ta['x1'], tb['x1'])
                ov_y1 = min(ta['y1'], tb['y1'])

                if ov_x1 > ov_x0 and ov_y1 > ov_y0:
                    ov_area = (ov_x1 - ov_x0) * (ov_y1 - ov_y0)
                    ta_area = (ta['x1'] - ta['x0']) * (ta['y1'] - ta['y0'])
                    if ta_area > 0 and ov_area / ta_area > 0.5:
                        # Check structural similarity (col/row counts)
                        col_match = 1.0 - abs(ta['cols'] - tb['cols']) / max(ta['cols'], tb['cols'], 1)
                        row_match = 1.0 - abs(ta['rows'] - tb['rows']) / max(ta['rows'], tb['rows'], 1)
                        if (col_match + row_match) / 2 > 0.5:
                            matched += 1
                            used_b.add(j)
                            break

        total = max(len(tables_a), len(tables_b))
        return matched / total if total > 0 else 1.0


# ============================================================================
# TEMPLATE MANAGER
# ============================================================================

class TemplateManager:
    """Store, retrieve, and match PDF templates."""

    EXACT_MATCH = 'exact'
    LAYOUT_MATCH = 'layout'
    PARTIAL_MATCH = 'partial'
    NO_MATCH = 'none'

    LAYOUT_THRESHOLD = 0.85
    PARTIAL_THRESHOLD = 0.70

    def __init__(self, templates_dir: Optional[str] = None):
        self.templates_dir = Path(templates_dir) if templates_dir else \
                             Path.home() / '.FillThatPDF' / 'templates'
        self.templates_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.templates_dir / 'templates_index.json'
        self._index = self._load_index()
        self._fingerprinter = LayoutFingerprinter()
        self._engine_hash = self._compute_engine_hash()

    # ---- Engine code hash for auto-invalidation ---------------------------

    # Key source files whose changes should invalidate cached templates.
    _ENGINE_HASH_FILES = [
        'fillable_engine/models.py',
        'fillable_engine/resolver.py',
        'fillable_engine/postprocess/height_standardization.py',
    ]

    def _compute_engine_hash(self) -> str:
        """Compute a short hash of the key engine source files.

        Any change to these files will produce a different hash,
        automatically invalidating templates built by the old code.
        """
        h = hashlib.sha256()
        base = Path(__file__).parent
        for rel in self._ENGINE_HASH_FILES:
            fp = base / rel
            if fp.is_file():
                h.update(fp.read_bytes())
        return 'engine_' + h.hexdigest()[:12]

    def find_match(self, pdf_path: str, fingerprint: dict) -> Tuple[str, Optional[dict], float]:
        """
        Find the best matching template for a PDF.

        Returns: (match_type, template_data, similarity_score)
        """
        content_hash = self._get_content_hash(pdf_path)

        # 1. Check for exact hash match (same file content)
        for tmpl_id, tmpl_info in self._index.get('templates', {}).items():
            if tmpl_info.get('content_hash') == content_hash:
                template = self._load_template(tmpl_id)
                if template:
                    # Auto-invalidate if engine code changed since template was saved
                    stored_ver = template.get('metadata', {}).get('engine_version', '')
                    if stored_ver != self._engine_hash:
                        print(f"   [Intelligence] Template {tmpl_id} invalidated "
                              f"(engine changed: {stored_ver[:20]} -> {self._engine_hash[:20]})")
                        continue
                    print(f"   [Intelligence] Exact hash match: {tmpl_info.get('pdf_name', tmpl_id)}")
                    return (self.EXACT_MATCH, template, 1.0)

        # 2. Check for layout matches via fingerprint comparison
        best_match = None
        best_score = 0.0
        best_id = None

        for tmpl_id, tmpl_info in self._index.get('templates', {}).items():
            # Quick pre-filter: page count must be similar
            if abs(tmpl_info.get('page_count', 0) - fingerprint.get('page_count', 0)) > 2:
                continue

            template = self._load_template(tmpl_id)
            if not template or 'fingerprint' not in template:
                continue

            score = self._fingerprinter.compare(fingerprint, template['fingerprint'])
            if score > best_score:
                # Skip templates from old engine versions
                stored_ver = template.get('metadata', {}).get('engine_version', '')
                if stored_ver != self._engine_hash:
                    continue
                best_score = score
                best_match = template
                best_id = tmpl_id

        if best_score >= self.LAYOUT_THRESHOLD:
            print(f"   [Intelligence] Layout match ({best_score:.1%}): "
                  f"{best_match.get('metadata', {}).get('pdf_name', best_id)}")
            return (self.LAYOUT_MATCH, best_match, best_score)
        elif best_score >= self.PARTIAL_THRESHOLD:
            print(f"   [Intelligence] Partial match ({best_score:.1%}): "
                  f"{best_match.get('metadata', {}).get('pdf_name', best_id)}")
            return (self.PARTIAL_MATCH, best_match, best_score)

        return (self.NO_MATCH, None, best_score)

    def save_template(self, pdf_path: str, fingerprint: dict,
                      text_fields: List[dict], checkboxes: List[dict],
                      radio_groups: List[dict], stats: dict,
                      engine_version: str = "v23") -> Optional[str]:
        """Save a template after successful processing. Returns template_id."""
        try:
            content_hash = self._get_content_hash(pdf_path)
            pdf_name = Path(pdf_path).name

            # Check if we already have a template for this exact file
            for tmpl_id, tmpl_info in self._index.get('templates', {}).items():
                if tmpl_info.get('content_hash') == content_hash:
                    # Update existing template
                    return self._update_template(tmpl_id, fingerprint,
                                                  text_fields, checkboxes,
                                                  radio_groups, stats, engine_version)

            # Create new template
            template_id = "tmpl_" + hashlib.md5(
                f"{content_hash}_{time.time()}".encode()
            ).hexdigest()[:8]

            # Build anchor offsets for each field
            text_with_anchors = self._add_anchor_offsets(text_fields, pdf_path, 'text')
            cb_with_anchors = self._add_anchor_offsets(checkboxes, pdf_path, 'checkbox')

            template = {
                'template_id': template_id,
                'metadata': {
                    'pdf_name': pdf_name,
                    'content_hash': content_hash,
                    'page_count': fingerprint.get('page_count', 0),
                    'created': time.strftime('%Y-%m-%dT%H:%M:%S'),
                    'engine_version': engine_version,
                    'processing_time': stats.get('processing_time', 0),
                    'stats': {
                        'text_fields': len(text_fields),
                        'checkboxes': len(checkboxes),
                        'radio_groups': len(radio_groups),
                    }
                },
                'fingerprint': fingerprint,
                'fields': {
                    'text_fields': self._serialize_fields(text_with_anchors, 'text'),
                    'checkboxes': self._serialize_fields(cb_with_anchors, 'checkbox'),
                    'radio_groups': [self._serialize_field(r) for r in radio_groups],
                }
            }

            # Save template file
            template_file = self.templates_dir / f"{template_id}.json"
            self._atomic_write_json(template_file, template)

            # Update index
            self._index.setdefault('templates', {})[template_id] = {
                'pdf_name': pdf_name,
                'content_hash': content_hash,
                'page_count': fingerprint.get('page_count', 0),
                'field_count': len(text_fields) + len(checkboxes),
                'file': f"{template_id}.json",
                'created': template['metadata']['created'],
            }
            self._save_index()

            print(f"   [Intelligence] Template saved: {template_id} "
                  f"({len(text_fields)} text + {len(checkboxes)} checkboxes)")
            return template_id

        except Exception as e:
            print(f"   [Intelligence] Template save failed: {e}")
            return None

    def apply_template(self, template: dict, target_pdf_path: str,
                       target_fingerprint: dict) -> Tuple[List[dict], List[dict], List[dict]]:
        """
        Apply a template's fields to a new PDF with coordinate adjustments.

        Computes per-page shifts using text anchor landmark matching,
        then applies those shifts to all field coordinates.
        """
        fields = template.get('fields', {})
        src_fp = template.get('fingerprint', {})

        # Compute per-page coordinate shifts using anchor matching
        page_shifts = self._compute_page_shifts(src_fp, target_fingerprint)

        # Apply shifts to text fields
        text_fields = []
        for f in fields.get('text_fields', []):
            shifted = dict(f)
            page = f.get('page', 0)
            dx, dy = page_shifts.get(page, (0.0, 0.0))
            shifted['x0'] = f['x0'] + dx
            shifted['y0'] = f['y0'] + dy
            shifted['x1'] = f['x1'] + dx
            shifted['y1'] = f['y1'] + dy
            shifted['source'] = f.get('source', 'template_match')
            text_fields.append(shifted)

        # Apply shifts to checkboxes
        checkboxes = []
        for cb in fields.get('checkboxes', []):
            shifted = dict(cb)
            page = cb.get('page', 0)
            dx, dy = page_shifts.get(page, (0.0, 0.0))
            shifted['x'] = cb['x'] + dx
            shifted['y'] = cb['y'] + dy
            shifted['source'] = cb.get('source', 'template_match')
            checkboxes.append(shifted)

        radio_groups = fields.get('radio_groups', [])

        return text_fields, checkboxes, radio_groups

    def _compute_page_shifts(self, src_fp: dict, tgt_fp: dict) -> Dict[int, Tuple[float, float]]:
        """
        Compute per-page (dx, dy) coordinate shifts between source and target fingerprints.
        Uses median of matching text anchor position deltas.
        """
        shifts = {}
        src_anchors = src_fp.get('text_anchors', {})
        tgt_anchors = tgt_fp.get('text_anchors', {})

        src_dims = src_fp.get('page_dimensions', [])
        tgt_dims = tgt_fp.get('page_dimensions', [])

        for page_str in src_anchors:
            page_num = int(page_str)

            # Get page dimensions for denormalization
            if page_num < len(src_dims) and page_num < len(tgt_dims):
                src_pw, src_ph = src_dims[page_num]
                tgt_pw, tgt_ph = tgt_dims[page_num]
            else:
                shifts[page_num] = (0.0, 0.0)
                continue

            src_page_anchors = src_anchors.get(page_str, [])
            tgt_page_anchors = tgt_anchors.get(page_str, [])

            if not src_page_anchors or not tgt_page_anchors:
                shifts[page_num] = (0.0, 0.0)
                continue

            # Build text → position map for target
            tgt_map = {}
            for a in tgt_page_anchors:
                text = a['text'].lower()
                # Denormalize to points
                tgt_map[text] = (a['x'] * tgt_pw, a['y'] * tgt_ph)

            # Find matching anchors and compute deltas
            dx_list = []
            dy_list = []
            for a in src_page_anchors:
                text = a['text'].lower()
                if text in tgt_map:
                    src_x = a['x'] * src_pw
                    src_y = a['y'] * src_ph
                    tgt_x, tgt_y = tgt_map[text]
                    dx_list.append(tgt_x - src_x)
                    dy_list.append(tgt_y - src_y)

            if dx_list and dy_list:
                # Use median to be robust against outliers
                dx_list.sort()
                dy_list.sort()
                mid = len(dx_list) // 2
                shifts[page_num] = (dx_list[mid], dy_list[mid])
            else:
                shifts[page_num] = (0.0, 0.0)

        return shifts

    def _add_anchor_offsets(self, fields: List[dict], pdf_path: str,
                            field_type: str) -> List[dict]:
        """Add nearest-text anchor offsets to fields for robust template transfer."""
        # For now, just pass through — anchor offsets are a future enhancement
        # The per-page shift mechanism handles coordinate transfer adequately
        return fields

    def _serialize_fields(self, fields: List[dict], field_type: str) -> List[dict]:
        """Serialize field list to JSON-safe format."""
        return [self._serialize_field(f) for f in fields]

    def _serialize_field(self, field: dict) -> dict:
        """Convert a single field dict to JSON-serializable format."""
        safe = {}
        for key, value in field.items():
            if isinstance(value, (str, int, float, bool, type(None))):
                safe[key] = value
            elif isinstance(value, (list, tuple)):
                safe[key] = list(value)
            elif isinstance(value, dict):
                safe[key] = self._serialize_field(value)
            else:
                safe[key] = str(value)  # Fallback: stringify
        return safe

    def _update_template(self, tmpl_id: str, fingerprint: dict,
                         text_fields: List[dict], checkboxes: List[dict],
                         radio_groups: List[dict], stats: dict,
                         engine_version: str) -> str:
        """Update an existing template with new field data."""
        template = self._load_template(tmpl_id)
        if not template:
            return tmpl_id

        template['metadata']['engine_version'] = engine_version
        template['metadata']['updated'] = time.strftime('%Y-%m-%dT%H:%M:%S')
        template['metadata']['stats'] = {
            'text_fields': len(text_fields),
            'checkboxes': len(checkboxes),
            'radio_groups': len(radio_groups),
        }
        template['fingerprint'] = fingerprint
        template['fields'] = {
            'text_fields': self._serialize_fields(text_fields, 'text'),
            'checkboxes': self._serialize_fields(checkboxes, 'checkbox'),
            'radio_groups': [self._serialize_field(r) for r in radio_groups],
        }

        template_file = self.templates_dir / f"{tmpl_id}.json"
        self._atomic_write_json(template_file, template)

        # Update index
        self._index.setdefault('templates', {})[tmpl_id]['field_count'] = \
            len(text_fields) + len(checkboxes)
        self._save_index()

        return tmpl_id

    def _get_content_hash(self, pdf_path: str) -> str:
        """Generate content hash from first/last 8KB + file size."""
        try:
            file_size = os.path.getsize(pdf_path)
            with open(pdf_path, 'rb') as f:
                head = f.read(8192)
                if file_size > 8192:
                    f.seek(-8192, 2)
                    tail = f.read(8192)
                else:
                    tail = b''
            data = head + tail + str(file_size).encode()
            return hashlib.md5(data).hexdigest()[:16]
        except Exception:
            return hashlib.md5(str(time.time()).encode()).hexdigest()[:16]

    def _load_index(self) -> dict:
        """Load the template index file."""
        if self.index_path.exists():
            try:
                with open(self.index_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return {'version': '1.0', 'templates': {}}

    def _save_index(self):
        """Persist the template index atomically."""
        self._atomic_write_json(self.index_path, self._index)

    def _load_template(self, tmpl_id: str) -> Optional[dict]:
        """Load a single template file."""
        tmpl_info = self._index.get('templates', {}).get(tmpl_id, {})
        filename = tmpl_info.get('file', f"{tmpl_id}.json")
        path = self.templates_dir / filename

        if path.exists():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return None

    def _atomic_write_json(self, path: Path, data: dict):
        """Write JSON file atomically using temp file + rename."""
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent), suffix='.tmp', prefix='.'
        )
        try:
            with os.fdopen(tmp_fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, str(path))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


# ============================================================================
# ML POST-PROCESSOR
# ============================================================================

class MLPostProcessor:
    """CNN-based false positive filter using MobileNetV3-Small."""

    CLASS_NAMES = ["text", "checkbox", "radio", "dropdown", "not_a_field"]
    DEFAULT_THRESHOLD = 0.95
    DPI = 150
    CROP_PADDING = 3
    INPUT_SIZE = (64, 128)  # Height, Width — matches train_classifier.py

    def __init__(self, model_path: Optional[str] = None,
                 confidence_threshold: float = 0.95):
        self.model = None
        self.transform = None
        self.device = None
        self.confidence_threshold = confidence_threshold
        self.available = False

        if not TORCH_AVAILABLE:
            print("   [ML] PyTorch not installed — ML post-processing disabled")
            return

        if not FITZ_AVAILABLE:
            print("   [ML] PyMuPDF not installed — ML post-processing disabled")
            return

        if not PIL_AVAILABLE:
            print("   [ML] Pillow not installed — ML post-processing disabled")
            return

        # Find model
        self.model_path = model_path or self._find_model()
        if not self.model_path or not Path(self.model_path).exists():
            print("   [ML] No classifier model found — ML post-processing disabled")
            return

        try:
            self._load_model()
            self.available = True
            print(f"   [ML] Classifier loaded: {Path(self.model_path).name}")
        except Exception as e:
            print(f"   [ML] Could not load classifier: {e}")

    def filter_false_positives(self, pdf_path: str,
                                text_fields: List[dict],
                                checkboxes: List[dict]
                                ) -> Tuple[List[dict], List[dict], List[dict]]:
        """
        Run each detected field through the CNN classifier.
        Remove fields classified as 'not_a_field' with confidence > threshold.

        Returns: (filtered_text_fields, filtered_checkboxes, removed_fields)
        """
        if not self.available:
            return text_fields, checkboxes, []

        removed = []
        doc = None

        try:
            doc = fitz.open(pdf_path)

            # Filter text fields
            # v52.3: Sources that are carefully positioned and should not be ML-filtered
            ml_exempt_sources = {
                'horizontal_line_table_subrow',  # Positioned from actual underlines
                'box_entry',                     # Detected from visible box outlines
                'comb_box_grid',                 # Detected from comb-style boxes
                'signature',                     # Signature fields are always valid
            }
            filtered_text = []
            for field in text_fields:
                # v52.3: Skip ML filtering for exempt sources
                if field.get('source') in ml_exempt_sources:
                    filtered_text.append(field)
                    continue
                # v52.7: Skip ML filtering for fields explicitly marked as
                # exempt (e.g., HLineTable fields positioned on inline
                # underscore text like "____CFM" or "____IWC").
                if field.get('ml_exempt'):
                    filtered_text.append(field)
                    continue
                keep, pred_class, confidence = self._should_keep_field(doc, field, 'text')
                if keep:
                    filtered_text.append(field)
                else:
                    removed.append({
                        'type': 'text',
                        'name': field.get('name', '?'),
                        'page': field.get('page', -1),
                        'ml_class': pred_class,
                        'ml_confidence': confidence,
                    })

            # Filter checkboxes
            filtered_cb = []
            for cb in checkboxes:
                # Convert checkbox coords to bbox format
                cb_bbox = {
                    'page': cb.get('page', 0),
                    'x0': cb.get('x', 0),
                    'y0': cb.get('y', 0),
                    'x1': cb.get('x', 0) + cb.get('width', 10),
                    'y1': cb.get('y', 0) + cb.get('height', 10),
                }
                keep, pred_class, confidence = self._should_keep_field(doc, cb_bbox, 'checkbox')
                if keep:
                    filtered_cb.append(cb)
                else:
                    removed.append({
                        'type': 'checkbox',
                        'label': cb.get('label', '?'),
                        'page': cb.get('page', -1),
                        'ml_class': pred_class,
                        'ml_confidence': confidence,
                    })

        except Exception as e:
            print(f"   [ML] Error during filtering: {e}")
            return text_fields, checkboxes, []
        finally:
            if doc:
                doc.close()

        if removed:
            print(f"   [ML] Removed {len(removed)} false positives "
                  f"(>{self.confidence_threshold:.0%} not_a_field confidence)")
            for r in removed[:5]:  # Show first 5
                print(f"        - P{r['page']+1} {r['type']} '{r.get('name', r.get('label', '?'))}' "
                      f"({r['ml_confidence']:.1%})")
            if len(removed) > 5:
                print(f"        ... and {len(removed) - 5} more")

        return filtered_text, filtered_cb, removed

    def _should_keep_field(self, doc, field: dict, field_type: str) -> Tuple[bool, str, float]:
        """Classify a single field. Returns (should_keep, predicted_class, confidence)."""
        try:
            img = self._crop_field_from_doc(doc, field)
            if img is None or img.width < 3 or img.height < 3:
                return (True, 'unknown', 0.0)  # Can't crop → keep

            pred_class, confidence = self._classify(img)

            if pred_class == 'not_a_field' and confidence >= self.confidence_threshold:
                return (False, pred_class, confidence)

            return (True, pred_class, confidence)

        except Exception:
            return (True, 'error', 0.0)  # Error → keep the field

    def _crop_field_from_doc(self, doc, field: dict) -> Optional['Image.Image']:
        """Crop field region from PDF using PyMuPDF."""
        page_num = field.get('page', 0)
        if page_num >= len(doc):
            return None

        page = doc[page_num]

        x0 = max(0, field['x0'] - self.CROP_PADDING)
        y0 = max(0, field['y0'] - self.CROP_PADDING)
        x1 = min(page.rect.width, field['x1'] + self.CROP_PADDING)
        y1 = min(page.rect.height, field['y1'] + self.CROP_PADDING)

        if x1 <= x0 or y1 <= y0:
            return None

        clip_rect = fitz.Rect(x0, y0, x1, y1)
        mat = fitz.Matrix(self.DPI / 72, self.DPI / 72)

        try:
            pix = page.get_pixmap(matrix=mat, clip=clip_rect)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            return img
        except Exception:
            return None

    def _classify(self, img: 'Image.Image') -> Tuple[str, float]:
        """Classify a cropped field image. Returns (class_name, confidence)."""
        img_tensor = self.transform(img).unsqueeze(0).to(self.device)

        with torch.no_grad():
            outputs = self.model(img_tensor)
            probs = torch.softmax(outputs, dim=1)
            pred_idx = probs.argmax(dim=1).item()
            confidence = probs[0, pred_idx].item()

        class_name = self.CLASS_NAMES[pred_idx] if pred_idx < len(self.CLASS_NAMES) else 'unknown'
        return class_name, confidence

    def _load_model(self):
        """Load MobileNetV3-Small with trained weights."""
        # Determine device
        if torch.backends.mps.is_available():
            self.device = torch.device('mps')
        elif torch.cuda.is_available():
            self.device = torch.device('cuda')
        else:
            self.device = torch.device('cpu')

        # Create model architecture
        self.model = models.mobilenet_v3_small(weights=None)
        num_classes = len(self.CLASS_NAMES)

        # Replace classifier head
        in_features = self.model.classifier[-1].in_features
        self.model.classifier[-1] = nn.Linear(in_features, num_classes)

        # Load weights
        checkpoint = torch.load(self.model_path, map_location=self.device, weights_only=True)

        if isinstance(checkpoint, dict) and 'model_state_dict' in checkpoint:
            self.model.load_state_dict(checkpoint['model_state_dict'])
        elif isinstance(checkpoint, dict) and any(k.startswith('classifier') or k.startswith('features') for k in checkpoint):
            self.model.load_state_dict(checkpoint)
        else:
            # Try loading directly
            self.model.load_state_dict(checkpoint)

        self.model.to(self.device)
        self.model.eval()

        # Setup transform (matches train_classifier.py)
        self.transform = transforms.Compose([
            transforms.Resize(self.INPUT_SIZE),
            transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406],
                               [0.229, 0.224, 0.225]),
        ])

    def _find_model(self) -> Optional[str]:
        """Search for classifier_model.pt in standard locations."""
        search_paths = [
            Path(__file__).parent / 'classifier_model.pt',
            Path.home() / '.FillThatPDF' / 'classifier_model.pt',
        ]
        for p in search_paths:
            if p.exists():
                return str(p)
        return None


# ============================================================================
# PDF INTELLIGENCE ORCHESTRATOR
# ============================================================================

class PDFIntelligence:
    """
    Orchestrator for the PDF Intelligence Layer.
    Single entry point called from smart_fillable_v23.py.

    Three hooks:
    1. pre_detection(pdf_path) — check template library before running detection
    2. post_detection(pdf_path, fields) — ML false-positive filtering after detection
    3. on_success(pdf_path, fields, stats) — auto-save template after successful processing
    """

    VERSION = "1.0"

    def __init__(self, settings: dict = None):
        self.settings = settings or {}
        self.enabled = self.settings.get('enable_intelligence_layer', True)
        self.enable_templates = self.settings.get('enable_template_matching', True)
        self.enable_ml = self.settings.get('enable_ml_postprocessor', True)
        self.enable_auto_save = self.settings.get('enable_auto_save_templates', True)

        self.template_manager = None
        self.ml_processor = None
        self.fingerprinter = None
        self._cached_fingerprint = None  # Cache fingerprint for reuse in on_success

        if not self.enabled:
            return

        print("\n🧠 PDF Intelligence Layer v{} initializing...".format(self.VERSION))

        # Initialize fingerprinter (always needed for templates)
        if self.enable_templates or self.enable_auto_save:
            self.fingerprinter = LayoutFingerprinter()

        # Initialize template manager
        if self.enable_templates or self.enable_auto_save:
            try:
                templates_dir = self.settings.get('templates_dir', None)
                self.template_manager = TemplateManager(templates_dir)
                tmpl_count = len(self.template_manager._index.get('templates', {}))
                print(f"   [Templates] Library loaded: {tmpl_count} templates")
            except Exception as e:
                print(f"   [Templates] Init failed: {e}")

        # Initialize ML processor
        if self.enable_ml:
            try:
                model_path = self.settings.get('ml_model_path', None)
                threshold = float(self.settings.get('ml_confidence_threshold', 0.95))
                self.ml_processor = MLPostProcessor(model_path, threshold)
            except Exception as e:
                print(f"   [ML] Init failed: {e}")

    def pre_detection(self, pdf_path: str) -> Optional[dict]:
        """
        Called BEFORE v23 detection passes.
        Checks for template matches.

        Returns:
            None — no match, proceed with normal detection
            dict with:
                'match_type': 'exact' | 'layout' | 'partial'
                'text_fields': List[dict]
                'checkboxes': List[dict]
                'radio_groups': List[dict]
                'similarity': float
                'skip_detection': bool  (True for exact/layout match)
        """
        if not self.enabled or not self.enable_templates:
            return None

        if not self.template_manager or not self.fingerprinter:
            return None

        try:
            print("\n🧠 INTELLIGENCE: Pre-detection template check...")

            # Generate fingerprint for this PDF
            fingerprint = self.fingerprinter.generate(pdf_path)
            self._cached_fingerprint = fingerprint  # Cache for on_success

            # Search for matching template
            match_type, template, similarity = \
                self.template_manager.find_match(pdf_path, fingerprint)

            if match_type == TemplateManager.NO_MATCH:
                print(f"   [Intelligence] No template match found (best: {similarity:.1%})")
                return None

            if match_type == TemplateManager.EXACT_MATCH:
                # v51.1d: Always run fresh detection to use latest code improvements.
                # Cached templates can lock in bugs from older detection logic.
                # Templates are still saved (on_success) for future use once we add
                # proper engine-version invalidation.
                fields = template.get('fields', {})
                text_fields = fields.get('text_fields', [])
                checkboxes = fields.get('checkboxes', [])

                print(f"   [Intelligence] Exact match found ({len(text_fields)} text + "
                      f"{len(checkboxes)} checkboxes in cache) — running fresh detection for accuracy")
                return None  # Run fresh detection

            if match_type == TemplateManager.LAYOUT_MATCH:
                # v51.1d: Always run fresh detection — layout-shifted cached fields
                # may not reflect improvements in detection logic.
                print(f"   [Intelligence] Layout match ({similarity:.1%}) found — "
                      f"running fresh detection for accuracy")
                return None  # Run fresh detection

            if match_type == TemplateManager.PARTIAL_MATCH:
                # Don't skip detection, but provide hints for validation
                fields = template.get('fields', {})
                print(f"   [Intelligence] ⚡ Partial match ({similarity:.1%}) — "
                      f"running full detection with validation hints")

                return {
                    'match_type': 'partial',
                    'text_fields': fields.get('text_fields', []),
                    'checkboxes': fields.get('checkboxes', []),
                    'radio_groups': fields.get('radio_groups', []),
                    'similarity': similarity,
                    'skip_detection': False,
                }

        except Exception as e:
            print(f"   [Intelligence] Pre-detection error: {e}")

        return None

    def post_detection(self, pdf_path: str,
                       text_fields: List[dict],
                       checkboxes: List[dict],
                       radio_groups: List[dict],
                       validation_hints: Optional[dict] = None
                       ) -> Tuple[List[dict], List[dict], List[dict]]:
        """
        Called AFTER v23 detection + cleanup passes, BEFORE PDF creation.
        Runs ML false-positive filtering.

        Returns: (text_fields, checkboxes, radio_groups)
        """
        if not self.enabled:
            return text_fields, checkboxes, radio_groups

        # ML false positive filtering
        if self.enable_ml and self.ml_processor and self.ml_processor.available:
            try:
                print("\n🧠 INTELLIGENCE: ML post-processing...")
                text_fields, checkboxes, removed = \
                    self.ml_processor.filter_false_positives(
                        pdf_path, text_fields, checkboxes)

                if not removed:
                    print("   [ML] No false positives detected — all fields kept")

            except Exception as e:
                print(f"   [ML] Post-processing error: {e}")

        # Validation hints from partial template match
        if validation_hints and validation_hints.get('match_type') == 'partial':
            try:
                hint_text = validation_hints.get('text_fields', [])
                hint_cb = validation_hints.get('checkboxes', [])
                total_hints = len(hint_text) + len(hint_cb)
                total_detected = len(text_fields) + len(checkboxes)

                diff = abs(total_detected - total_hints)
                if diff > total_hints * 0.2:
                    print(f"   [Intelligence] ⚠️ Partial match validation: detected {total_detected} "
                          f"fields vs template hint of {total_hints} (Δ{diff})")
                else:
                    print(f"   [Intelligence] ✅ Partial match validation: field count consistent "
                          f"({total_detected} detected vs {total_hints} template)")

            except Exception as e:
                print(f"   [Intelligence] Validation error: {e}")

        return text_fields, checkboxes, radio_groups

    def on_success(self, pdf_path: str,
                   text_fields: List[dict],
                   checkboxes: List[dict],
                   radio_groups: List[dict],
                   stats: dict):
        """
        Called AFTER successful PDF creation.
        Auto-saves the result as a template for future matching.
        """
        if not self.enabled or not self.enable_auto_save:
            return

        if not self.template_manager:
            return

        try:
            # Use cached fingerprint if available, otherwise generate
            fingerprint = self._cached_fingerprint
            if not fingerprint and self.fingerprinter:
                fingerprint = self.fingerprinter.generate(pdf_path)

            if not fingerprint:
                return

            self.template_manager.save_template(
                pdf_path, fingerprint,
                text_fields, checkboxes, radio_groups,
                stats, engine_version=self.template_manager._engine_hash
            )

        except Exception as e:
            print(f"   [Intelligence] Template auto-save error: {e}")
