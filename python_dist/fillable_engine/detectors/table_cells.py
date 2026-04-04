"""
Table Cell detector — the largest and most complex detector.

Ported from v23's _detect_table_cells() (lines 4673-7037) and the
_analyze_cell_for_field() helper (lines 9469-10390).

Handles TWO main detection tracks:

TRACK A: Strict Visual Grid (when page has >5 visual_grid_cells)
  - Grid cell classification (checkbox vs. text)
  - Prefilled column detection
  - Split cell multi-row (label+entry within wide cells)
  - Grid fallback for empty cells
  - Checkbox creation for small square cells
  - Embedded underscore detection
  - Full cell analysis for remaining cells

TRACK B: pdfplumber Table Processing
  - Header row detection (single spanning + multi-cell)
  - Empty data rows under column headers
  - Sub-table header detection
  - Prefilled column guard
  - Image upload column detection
  - Header bar exclusion (with colon/height exceptions)
  - Column clipping to header width
  - First column label skip
  - Adjacent empty cell detection
  - Label + entry cell pattern
  - Full cell analysis (_analyze_cell)

Sources emitted:
    - 'grid_fallback'                 (empty grid cells -> text fields)
    - 'grid_fallback_table_cell'      (checkbox grid cells -> text fields when disable_checkboxes)
    - 'split_line_gap'                (multi-label lines: "Name: ___ Email: ___")
    - 'split_cell_multi_row'          (single label + trailing space)
    - 'table_data_row'                (empty rows under column headers)
    - 'sub_table_data_row'            (empty rows under sub-table headers)
    - 'label_entry_cell'              (label + empty right area in same cell)
    - 'adjacent_empty_cell'           (label cell with empty neighbor)
    - 'label_entry_below'             (label at top, field below in same cell)
    - 'label_entry_adjacent_cell'     (label cell -> field in adjacent empty cell)
    - 'colored_split_entry'           (label+entry in colored header cell)
    - 'structural_box'                (generic analyzed cell)
    - 'table_col_fallback'            (checkbox column -> text fallback)
    - 'grid_region_fallback'          (square grid region -> text fallback)
    - 'visual_in_cell_fallback'       (visual checkbox cell -> text fallback)
    - 'image_grid_cell'               (image placeholder in grid cell)
    - 'image_column'                  (image upload column cell)
    - 'embedded_underscore'           (cells with "Volts ___ x Amps ___")
"""

import re
from typing import List, Dict, Optional, Set, Tuple
from collections import defaultdict

from ..models import PageModel, FieldCandidate, FieldType
from ..helpers import (
    clean_field_name, detect_field_format,
    bbox_overlap_area, bbox_area, bbox_overlap_ratio,
)
from .base import BaseDetector


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Form field label keywords that should not be treated as prefilled data
_FORM_LABEL_KEYWORDS = frozenset({
    'name', 'address', 'city', 'state', 'zip', 'phone', 'email',
    'date', 'fax', 'signature', 'license', 'number', 'company', 'contact',
    'apt', 'unit', 'suite', 'county', 'country', 'title', 'account',
    'cell', 'work', 'home', 'mobile', 'employer', 'occupation',
})

# Labels that indicate form fields (protected from dense-text skip)
_PROTECTED_LABEL_KEYWORDS = frozenset({
    'rebate', 'amount', 'total', 'electric', 'gas', 'email', 'phone',
    'account', 'fax', 'address', 'name', 'apt', 'city', 'state', 'zip', 'unit',
})

# Labels for long-data fields that need extra space
_LONG_DATA_PATTERNS = [
    r'\b(model|serial|mfg|manufacturer|brand|make)\s*(number|#|no\.?)?',
    r'\bmodel\s*[#/]?\s*(serial|number|no\.?)?',
    r'\bserial\s*[#/]?\s*(number|no\.?)?',
    r'\b(manufacturer|brand|make)\s*(and|[/&])?\s*(serial|model|name)?',
]

# Extended long-data patterns for under-label placement decisions
_LONG_DATA_PATTERNS_EXTENDED = [
    r'\b(first|last|full|middle|company|technician|account\s*holder)\s*name',
    r'\bname\s*:?\s*$',
    r'\b(applicant|customer|owner|contact|employee)\s*(name)?',
    r'\b(street\s*)?address',
    r'\bcity\b', r'\bstate\b', r'\bzip\s*(code)?',
    r'\b(home|cell|work|office|phone|fax)\s*(phone|number|#)?',
    r'\be-?mail\s*(address)?', r'\bphone\s*:?\s*$',
    r'\baccount\s*(number|#|no\.?)?',
    r'\b(organization|agency|community)\s*(name)?',
    r'\brelationship',
    r'\b(model|serial|mfg|manufacturer|brand|make)\s*(number|#|no\.?)?',
]

# Instruction-like text suppression keywords
_INSTRUCTION_KEYWORDS = frozenset({
    'failure to', 'required documentation', 'delay or disqualify',
    'questions please call', 'submit required', 'not be processed',
    'must be completed', 'must be submitted', 'will not be accepted',
})

_INSTRUCTION_KILLS = frozenset({
    'service territory', 'qualifying measure', 'consumer promotion',
    'percentage', 'please estimate', 'list', 'detail', 'hvac measure type',
})

# Exact-match labels for section headers that should be skipped
_SKIP_LABELS_EXACT = frozenset({
    'combustion efficiency', 'maintenance tasks', 'equipment information',
    'pre-service', 'post-service',
})

# Column-header semantic classification keywords.
# 'read-only' columns contain pre-printed reference/descriptive data.
# 'fillable' columns are where users should enter values.
# NOTE: These are matched as WHOLE WORDS (word-boundary), not substrings.
_READONLY_HEADER_WORDS = frozenset({
    # Reference identifiers (removed 'id'/'ref' — too short, match substrings)
    'reference', 'code', 'item', 'item #', 'item no',
    # Descriptions / specifications
    'description', 'type', 'equipment', 'category', 'model',
    'specification', 'spec', 'standard', 'task',
    'requirements', 'requirement', 'criteria', 'condition', 'documentation',
    # Pre-printed values
    'incentive', 'rate', 'price', 'cost',
    'rebate',
    # Status / fixed
    'status', 'eligible', 'eligibility',
    # Ambiguous — in BOTH lists so tiebreak always activates
    'measure', 'amount',
})

_FILLABLE_HEADER_WORDS = frozenset({
    # Quantity / count
    'qty', 'quantity', 'count', '# of', 'number of',
    # Totals (user-computed)
    'total', 'subtotal', 'sum', 'amount',
    # User input
    'date', 'name', 'enter', 'value', 'notes', 'comments',
    'signature', 'installed', 'existing', 'proposed',
    # Reference numbers to fill in (user writes these)
    'reference number', 'ref #', 'ref no',
    # Contextual — 'measure' in BOTH lists triggers tiebreak
    'measure', 'unit', 'units',
})


def _header_matches_keywords(header_lower: str, keywords: frozenset) -> bool:
    """Check if header text matches any keyword using word-boundary logic.

    Multi-word keywords (e.g. '# of', 'item #') use substring matching.
    Single-word keywords use word-boundary matching via regex.
    """
    for kw in keywords:
        if ' ' in kw or '#' in kw:
            # Multi-word / symbol keyword: use substring matching
            if kw in header_lower:
                return True
        else:
            # Single-word keyword: use word-boundary matching
            if re.search(r'\b' + re.escape(kw) + r'\b', header_lower):
                return True
    return False


def _is_header_like_text(text: str) -> bool:
    """Check if text looks like a section header (ALL CAPS, Roman numeral prefix, etc.)."""
    if not text:
        return False
    if text.isupper() and len(text) > 10:
        return True
    if re.match(r'^[IVX]+\.?\s*[A-Z][A-Z\s/&-]{6,}$', text):
        return True
    return False


def _is_long_data_label(text: str, patterns=None) -> bool:
    """Check if text matches a long-data label pattern."""
    if patterns is None:
        patterns = _LONG_DATA_PATTERNS
    lower = text.lower()
    return any(re.search(p, lower) for p in patterns)


def _cell_overlap_ratio(a, b):
    """Overlap of a relative to its own area."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0 = max(ax0, bx0)
    iy0 = max(ay0, by0)
    ix1 = min(ax1, bx1)
    iy1 = min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    area = (ax1 - ax0) * (ay1 - ay0)
    return inter / area if area > 0 else 0.0


# =========================================================================
# Main Detector
# =========================================================================

class TableCellDetector(BaseDetector):
    """
    Detect fields inside table cells — both visual grid cells and pdfplumber tables.

    This is the largest detector, handling complex multi-track logic for
    strict visual grids and pdfplumber-detected tables. The resolver will
    handle deduplication across tracks and with other detectors.
    """

    def detect(self, pages: List[PageModel]) -> List[FieldCandidate]:
        candidates: List[FieldCandidate] = []
        for pm in pages:
            page_candidates = self._detect_page(pm)
            candidates.extend(page_candidates)
        return candidates

    # ==================================================================
    # Top-level per-page dispatcher
    # ==================================================================

    def _detect_page(self, pm: PageModel) -> List[FieldCandidate]:
        """Run table cell detection on a single page."""
        results: List[FieldCandidate] = []
        page_num = pm.page_num

        # Build vertical lines from raw geometry for cell splitting
        v_lines = self._collect_vertical_lines(pm)

        # Build a global headers map from pdfplumber tables (for naming)
        global_headers_map = self._build_global_headers_map(pm, v_lines)

        # ------------------------------------------------------------------
        # TRACK A: Strict Visual Grid
        # ------------------------------------------------------------------
        grid_cells = list(pm.visual_grid_cells)  # list of (x0, y0, x1, y1) tuples

        # Backfill any pdfplumber table cells not covered by the visual grid
        if pm.tables:
            grid_tuples = list(grid_cells)
            for table in pm.tables:
                raw_cells = getattr(table, 'cells', None) or []
                for cell in raw_cells:
                    if not isinstance(cell, (list, tuple)) or len(cell) != 4:
                        continue
                    cx0, cy0, cx1, cy1 = cell
                    if (cx1 - cx0) < 20 or (cy1 - cy0) < 12:
                        continue
                    if any(_cell_overlap_ratio(cell, gc) > 0.8 for gc in grid_tuples):
                        continue
                    grid_cells.append((cx0, cy0, cx1, cy1))

        if len(grid_cells) > 5:
            grid_results = self._process_visual_grid(pm, grid_cells, v_lines, global_headers_map)
            results.extend(grid_results)

        # ------------------------------------------------------------------
        # TRACK B: pdfplumber Table Processing
        # ------------------------------------------------------------------
        table_results = self._process_pdfplumber_tables(pm, v_lines, global_headers_map)
        results.extend(table_results)

        return results

    # ==================================================================
    # Geometry helpers
    # ==================================================================

    def _collect_vertical_lines(self, pm: PageModel) -> List[Tuple[float, float, float]]:
        """Collect vertical line segments as (center_x, y0, y1)."""
        v_lines = []
        raw = pm.lines + pm.rects + pm.edges
        for l in raw:
            lx0 = float(l.get('x0', l.get('x', 0)))
            lx1 = float(l.get('x1', l.get('x', lx0) + l.get('width', 0)))
            ly0 = float(l.get('top', l.get('y0', 0)))
            ly1 = float(l.get('bottom', l.get('y1', 0)))
            w = abs(lx1 - lx0)
            h = abs(ly1 - ly0)
            if w < 3 and h > 5:
                v_lines.append(((lx0 + lx1) / 2, ly0, ly1))
        return v_lines

    def _split_cells_by_v_lines(self, cells, v_lines):
        """Split cells that contain vertical line dividers."""
        final = []
        for cell in cells:
            cx0, cy0, cx1, cy1 = cell
            cell_h = cy1 - cy0
            if (cx1 - cx0) < 30:
                final.append(cell)
                continue
            splits = sorted([
                vx for vx, vy0, vy1 in v_lines
                if cx0 + 5 < vx < cx1 - 5
                and max(0, min(cy1, vy1) - max(cy0, vy0)) > cell_h * 0.6
            ])
            if splits:
                prev_x = cx0
                for sx in splits:
                    final.append((prev_x, cy0, sx, cy1))
                    prev_x = sx
                final.append((prev_x, cy0, cx1, cy1))
            else:
                final.append(cell)
        return final

    def _build_global_headers_map(self, pm: PageModel, v_lines) -> Dict:
        """Build a page-wide headers map from the first row of each pdfplumber table."""
        global_headers_map: Dict = {}
        for table in pm.tables:
            raw_cells = getattr(table, 'cells', None) or []
            if not raw_cells:
                continue
            t_cells = self._split_cells_by_v_lines(raw_cells, v_lines)
            if not t_cells:
                continue
            y_vals = sorted(set(c[1] for c in t_cells))
            if not y_vals:
                continue
            header_row_y = y_vals[0]
            header_cells = [c for c in t_cells if abs(c[1] - header_row_y) < 2]
            hmap = self._analyze_header_row_simple(pm, header_cells)
            if hmap:
                global_headers_map.update(hmap)
        return global_headers_map

    def _analyze_header_row_simple(self, pm: PageModel, header_cells) -> Dict:
        """Simplified header analysis: map cell x0 -> header text."""
        hmap = {}
        for cell in header_cells:
            cx0, cy0, cx1, cy1 = cell
            text = pm.get_text_in_bbox((cx0 - 1, cy0 - 1, cx1 + 1, cy1 + 1), 0.20)
            if text and len(text.strip()) < 60:
                hmap[round(cx0)] = text.strip()
        return hmap

    def _get_column_header(self, x0, x1, headers_map: Dict) -> Optional[str]:
        """Find matching column header from headers_map for a given cell x-range."""
        for hx, hname in headers_map.items():
            hx_val = hx if isinstance(hx, (int, float)) else (hx[0] if isinstance(hx, tuple) else hx)
            if abs(x0 - hx_val) < 20:
                return hname
        return None

    # ==================================================================
    # Prefilled column detection
    # ==================================================================

    def _detect_prefilled_columns(self, pm: PageModel, cells, header_y=None) -> Set[Tuple[float, float, float, float]]:
        """
        Detect columns where >=85% of data rows already have text content.
        These are static/prefilled columns where we should NOT create fields.
        Returns set of (col_x0, col_x1, fill_y0, fill_y1).
        """
        prefilled = set()
        col_buckets: Dict[Tuple[float, float], List] = {}
        for c in cells:
            col_key = (round(c[0], 0), round(c[2], 0))
            col_buckets.setdefault(col_key, []).append(c)

        for col_key, col_cells in col_buckets.items():
            # Exclude header row
            data_cells = [c for c in col_cells if header_y is None or round(c[1], 0) != header_y]

            # Skip columns too wide (likely spanning rows)
            col_width = col_key[1] - col_key[0]
            if col_width > pm.width * 0.8:
                continue

            # Need at least 4 data rows to declare "prefilled"
            is_too_tall = any((dc[3] - dc[1]) > 100 for dc in data_cells)
            if is_too_tall or len(data_cells) < 4:
                continue

            filled_count = 0
            single_char_count = 0
            for dc in data_cells:
                txt = pm.get_text_in_bbox((dc[0] + 1, dc[1] + 1, dc[2] - 1, dc[3] - 1), 0.20)
                if txt:
                    # Label/placeholder indicators don't count as filled
                    if ':' in txt or '_' in txt or txt == '$':
                        continue
                    filled_count += 1
                    if len(txt) <= 3 or txt.upper() in ('N/A', 'TBD', 'N/C', 'YES', 'NO'):
                        single_char_count += 1

            if len(data_cells) == 0:
                continue
            fill_ratio = filled_count / len(data_cells)

            if fill_ratio >= 0.85:
                # Exempt columns where >50% of "filled" cells are short placeholders
                if filled_count > 0 and single_char_count / filled_count > 0.50:
                    continue

                # Exempt columns where >40% of filled cells look like form labels
                label_like = 0
                for dc in data_cells:
                    txt = pm.get_text_in_bbox((dc[0] + 1, dc[1] + 1, dc[2] - 1, dc[3] - 1), 0.20)
                    if txt:
                        txt_lower = txt.lower()
                        if any(kw in txt_lower for kw in _FORM_LABEL_KEYWORDS):
                            label_like += 1
                        elif len(txt.split()) <= 3 and txt.replace(' ', '').replace('.', '').replace('-', '').isalpha():
                            label_like += 1
                if filled_count > 0 and label_like / filled_count > 0.40:
                    continue

                # Record the vertical span of filled cells
                filled_ys = []
                for dc in data_cells:
                    txt = pm.get_text_in_bbox((dc[0] + 1, dc[1] + 1, dc[2] - 1, dc[3] - 1), 0.20)
                    if txt:
                        filled_ys.extend([dc[1], dc[3]])
                if filled_ys:
                    prefilled.add((col_key[0], col_key[1], min(filled_ys), max(filled_ys)))

        return prefilled

    def _is_in_prefilled_column(self, x0, y0, x1, y1, prefilled_cols) -> bool:
        """Check if a cell falls within a known prefilled column range."""
        col_key = (round(x0, 0), round(x1, 0))
        for px0, px1, py0, py1 in prefilled_cols:
            if col_key == (px0, px1) and py0 - 5 <= y0 <= py1 + 5:
                return True
        return False

    # ==================================================================
    # TRACK A: Strict Visual Grid
    # ==================================================================

    def _process_visual_grid(self, pm: PageModel, cells, v_lines, global_headers_map) -> List[FieldCandidate]:
        """Process a strict visual grid (>5 cells)."""
        results: List[FieldCandidate] = []
        page_num = pm.page_num
        is_tally = pm.is_tally_sheet

        # Classify cells
        total_cells = len(cells)
        small_cells = []
        empty_cells = []
        for c in cells:
            cw = c[2] - c[0]
            ch = c[3] - c[1]
            if 6 <= cw <= 60 and 6 <= ch <= 60 and abs(cw - ch) <= max(6, 0.12 * max(cw, ch)):
                small_cells.append(c)
            txt = pm.get_text_in_bbox((c[0] + 1, c[1] + 1, c[2] - 1, c[3] - 1), 0.20)
            if not txt:
                empty_cells.append(c)

        small_frac = len(small_cells) / total_cells if total_cells else 0.0
        grid_prefers_checkboxes = small_frac >= 0.75

        if is_tally:
            grid_prefers_checkboxes = False

        # A2: Prefilled column detection
        all_y = sorted(set(round(c[1], 0) for c in cells))
        header_y = all_y[0] if all_y else None
        prefilled_cols = self._detect_prefilled_columns(pm, cells, header_y)

        # Store into page_model for use by other components
        pm.prefilled_col_ranges |= prefilled_cols

        # Column-header semantic classification
        grid_col_class = {}
        if global_headers_map:
            grid_col_class = self._classify_columns(pm, global_headers_map, cells, header_y)
            pm.column_classifications.update(grid_col_class)

        # ------------------------------------------------------------------
        # A2-A4: Text grid path (tally sheet or non-checkbox grid)
        # ------------------------------------------------------------------
        if is_tally or not grid_prefers_checkboxes:
            results.extend(self._grid_text_path(pm, cells, prefilled_cols, v_lines, global_headers_map, grid_col_class))

        # ------------------------------------------------------------------
        # A5: Checkbox creation for grids that prefer checkboxes
        # ------------------------------------------------------------------
        if grid_prefers_checkboxes:
            results.extend(self._grid_checkbox_path(pm, cells))

        # ------------------------------------------------------------------
        # A6-A7: Cell analysis for remaining grid cells
        # ------------------------------------------------------------------
        results.extend(self._grid_analyze_cells(pm, cells, v_lines, global_headers_map, grid_prefers_checkboxes, prefilled_cols, grid_col_class))

        return results

    # ------------------------------------------------------------------
    # A2-A4 text grid path
    # ------------------------------------------------------------------

    def _grid_text_path(self, pm, cells, prefilled_cols, v_lines, global_headers_map, column_classifications=None) -> List[FieldCandidate]:
        """Handle the text-only grid path: split cells and grid fallback."""
        results: List[FieldCandidate] = []
        page_num = pm.page_num
        padding = self.table_cell_padding
        col_class = column_classifications or {}

        for c in cells:
            cx0, cy0, cx1, cy1 = c
            cell_width = cx1 - cx0
            cell_height = cy1 - cy0

            if cell_width < 15 or cell_height < 10:
                continue

            # Column classification guard
            if self._get_column_classification(cx0, col_class) == 'read-only':
                inner_peek = pm.get_text_in_bbox((cx0, cy0, cx1, cy1), 0.15) or ''
                if ':' not in inner_peek and '___' not in inner_peek:
                    # Exempt cells where text sits at the TOP with ≥14pt
                    # of blank entry space below (form label + entry
                    # pattern, e.g. "Date Installed" with writing space
                    # below).  Pre-printed values are centered/lower.
                    if not self._is_label_entry_cell(
                            pm, cx0, cy0, cx1, cy1, cell_height):
                        continue

            # Skip cells in image upload regions
            if pm.is_in_image_upload_region(cx0, cy0, cx1, cy1, 0.3):
                continue
            # Skip narrow gap cells flanked by image regions
            if cell_width < 35 and cell_height > cell_width * 2.5:
                left_flanked = pm.is_in_image_upload_region(cx0 - 5, cy0, cx0, cy1, 0.0)
                right_flanked = pm.is_in_image_upload_region(cx1, cy0, cx1 + 5, cy1, 0.0)
                if left_flanked and right_flanked:
                    continue

            # ---- A3: Split cell multi-row ----
            words = pm.get_words_in_bbox((cx0, cy0, cx1, cy1), overlap_threshold=0.15)

            if words and cell_width > 100:
                split_results = self._split_cell_multi_row(pm, cx0, cy0, cx1, cy1, words, v_lines)
                if split_results:
                    results.extend(split_results)
                    continue

            # ---- A4: Grid fallback for empty cells ----
            # Check for bare $ placeholder
            cell_is_dollar = False
            if words:
                word_texts = [w['text'].strip() for w in words]
                if word_texts == ['$']:
                    cell_is_dollar = True
                elif word_texts:
                    continue  # Has non-dollar text, skip fallback
            else:
                # Double check with text extraction
                txt = pm.get_text_in_bbox((cx0 + 1, cy0 + 1, cx1 - 1, cy1 - 1), 0.20)
                if txt:
                    continue

            # Adjust vertical padding for standard table rows
            v_pad = padding
            if 15 < cell_height < 25:
                v_pad = max(0.5, padding * 0.4)

            # For $ placeholder, start field after the $ sign
            if cell_is_dollar and words:
                dollar_words = [w for w in words if w['text'].strip() == '$']
                if dollar_words:
                    field_x0 = float(dollar_words[0]['x1']) + 2
                else:
                    field_x0 = cx0 + padding
            else:
                field_x0 = cx0 + padding

            results.append(FieldCandidate(
                page=page_num,
                x0=field_x0, y0=cy0 + v_pad,
                x1=cx1 - padding, y1=cy1 - v_pad,
                field_type=FieldType.TEXT,
                source='grid_fallback',
                name_hint=f"Cell_{int(cx0)}_{int(cy0)}",
                confidence=0.7,
            ))

        return results

    # ------------------------------------------------------------------
    # A3: Split cell multi-row
    # ------------------------------------------------------------------

    def _split_cell_multi_row(self, pm, cx0, cy0, cx1, cy1, words, v_lines) -> List[FieldCandidate]:
        """
        Split wide cells with text into multiple fields:
        Pattern A: "Label: ___ Label: ___" (split_line_gap)
        Pattern B: "Label:" + trailing space (split_cell_multi_row)
        """
        results: List[FieldCandidate] = []
        page_num = pm.page_num

        # Group words by line (4pt tolerance)
        bounded_words = [w for w in words
                         if float(w['x0']) >= cx0 - 1.0 and float(w['x1']) <= cx1 + 1.0
                         and float(w['top']) >= cy0 - 1.0 and float(w['bottom']) <= cy1 + 1.0]
        use_words = bounded_words if bounded_words else words
        sorted_words = sorted(use_words, key=lambda w: (float(w['top']), float(w['x0'])))

        lines_data = []
        for w in sorted_words:
            t = float(w['top'])
            b = float(w['bottom'])
            if not lines_data or abs(t - lines_data[-1]['top']) > 4:
                lines_data.append({'top': t, 'bottom': b, 'words': [w]})
            else:
                lines_data[-1]['words'].append(w)
                lines_data[-1]['bottom'] = max(lines_data[-1]['bottom'], b)

        for line_idx, line_group in enumerate(lines_data):
            l_words = sorted(line_group['words'], key=lambda w: float(w['x0']))
            l_txt = " ".join(w['text'] for w in l_words)
            l_txt_clean = re.sub(r'\s+', ' ', l_txt).strip()

            # Skip long instructional text unless it has 2+ colon labels
            if len(l_txt_clean.split()) > 6:
                colon_label_count = sum(1 for w in l_words if w['text'].endswith(':'))
                if colon_label_count < 2:
                    continue

            # --- Pattern A: Multiple labels in one line ---
            labels_in_line = []
            curr_parts = []
            curr_x0 = None
            for w in l_words:
                if curr_x0 is None:
                    curr_x0 = float(w['x0'])
                curr_parts.append(w['text'])
                if w['text'].endswith(':') or w['text'].lower().rstrip(':') in ('city', 'state', 'zip', '$'):
                    labels_in_line.append({
                        'x0': curr_x0,
                        'x1': float(w['x1']),
                        'text': " ".join(curr_parts),
                    })
                    curr_parts = []
                    curr_x0 = None

            if len(labels_in_line) >= 2:
                for idx, lbl in enumerate(labels_in_line):
                    g_x0 = lbl['x1'] + 5
                    g_x1 = (labels_in_line[idx + 1]['x0'] - 5) if idx + 1 < len(labels_in_line) else (cx1 - 5)

                    # Clip gap to underscore boundaries
                    for gw in l_words:
                        if '___' not in gw['text']:
                            continue
                        gw_x0 = float(gw['x0'])
                        gw_x1 = float(gw['x1'])
                        if gw_x0 < g_x0 - 5 or gw_x0 >= g_x1:
                            continue
                        gsuffix = gw['text'].lstrip('_')
                        if gsuffix and len(gsuffix) < len(gw['text']):
                            gavg = (gw_x1 - gw_x0) / max(len(gw['text']), 1)
                            g_x1 = min(g_x1, gw_x1 - len(gsuffix) * gavg - 1)
                        elif not gsuffix:
                            g_x1 = min(g_x1, gw_x1)
                        break

                    if g_x1 - g_x0 >= 30:
                        # Check gap does not contain text
                        if not any(float(w['x0']) > g_x0 + 1 and float(w['x1']) < g_x1 - 1 for w in l_words):
                            # Check gap is not heavily overlapped by page words
                            gap_has_text = False
                            for pw in pm.words:
                                if abs(float(pw['top']) - line_group['top']) > 5:
                                    continue
                                ov_x0 = max(g_x0, float(pw['x0']))
                                ov_x1 = min(g_x1, float(pw['x1']))
                                if ov_x1 > ov_x0:
                                    gap_w = g_x1 - g_x0
                                    if gap_w > 0 and (ov_x1 - ov_x0) / gap_w > 0.5:
                                        gap_has_text = True
                                        break
                            if gap_has_text:
                                continue

                            l_clean = re.sub(r'[^a-zA-Z0-9\s]', '', lbl['text']).strip()
                            results.append(FieldCandidate(
                                page=page_num,
                                x0=g_x0, y0=line_group['top'] - 2,
                                x1=g_x1, y1=line_group['bottom'] + 2,
                                field_type=FieldType.TEXT,
                                source='split_line_gap',
                                name_hint=l_clean[:30] if l_clean else "Entry",
                                label=lbl['text'],
                                confidence=0.85,
                            ))
                continue  # Line handled by gap logic

            # --- Pattern B: Single label + trailing space ---
            is_protected = any(kw in l_txt_clean.lower() for kw in (
                'email', 'phone', 'account', 'fax', 'address', 'name',
                'apt', 'city', 'state', 'zip', 'unit',
            ))
            if ':' in l_txt_clean or (is_protected and len(l_txt_clean) < 30):
                label_words = [w for w in l_words if '_' not in w['text']]
                if not label_words:
                    continue
                l_x1 = max(float(w['x1']) for w in label_words)
                empty_right = cx1 - l_x1

                # Skip header-like text
                if l_txt_clean.isupper() and len(l_txt_clean) > 10:
                    continue

                if empty_right > 30:
                    # Skip long-data labels with narrow space (prefer below-label)
                    if _is_long_data_label(l_txt_clean) and empty_right < 80:
                        continue

                    # Skip if this is a section header with detail labels below
                    if len(lines_data) >= 2 and line_group is lines_data[0]:
                        has_detail_below = False
                        for other_lg in lines_data[1:]:
                            if sum(1 for w in other_lg['words'] if w['text'].endswith(':')) >= 2:
                                has_detail_below = True
                                break
                        if has_detail_below:
                            continue

                    # Skip centered header text
                    if empty_right > 150 and (l_x1 - cx0) > 100:
                        continue

                    # Skip if in header bar — but exempt short form labels
                    # (alternating row shading can be misdetected as headers;
                    #  real section headers are typically longer text)
                    if pm.is_in_header_bar(cx0, line_group['top'], cx1, line_group['bottom']):
                        if len(l_txt_clean.split()) > 3:
                            continue

                    # Skip section title patterns
                    cell_width = cx1 - cx0
                    cell_height = cy1 - cy0
                    is_section_title = (':' not in l_txt_clean) and len(l_txt_clean) > 8 and cell_width > 400
                    if is_section_title:
                        continue
                    if cell_height < 22 and cell_width > 400:
                        continue

                    # Skip very tall cells — a tiny line-based entry field
                    # in a large cell (>60pt) gets distorted by postprocessors
                    # (LineSnapper/HeightStandardizer).  Let label_entry_cell
                    # handle these; it creates a properly sized cell-filling field.
                    if cell_height > 60:
                        continue

                    # Compute target field bounds
                    target_x0 = l_x1 + 6
                    target_x1 = cx1 - self.table_cell_padding

                    # Clip to next word on same line
                    for w in label_words:
                        wx0 = float(w['x0'])
                        if wx0 > l_x1 + 10:
                            target_x1 = min(target_x1, wx0 - 5)
                            break

                    if target_x1 > target_x0 + 8:
                        l_clean = re.sub(r'[^a-zA-Z0-9\s]', '', l_txt_clean.split(':')[0]).strip()
                        results.append(FieldCandidate(
                            page=page_num,
                            x0=target_x0, y0=line_group['top'] - 1,
                            x1=target_x1, y1=line_group['bottom'] + 1,
                            field_type=FieldType.TEXT,
                            source='split_cell_multi_row',
                            name_hint=l_clean[:30] if l_clean else "Entry",
                            label=l_txt_clean,
                            confidence=0.85,
                        ))

        return results

    # ------------------------------------------------------------------
    # A5: Grid checkbox path
    # ------------------------------------------------------------------

    def _grid_checkbox_path(self, pm, cells) -> List[FieldCandidate]:
        """Create checkboxes (or text fallback) for small square empty cells in checkbox-preferring grids."""
        results: List[FieldCandidate] = []
        page_num = pm.page_num
        padding = self.table_cell_padding
        disable_cb = self.settings.get('disable_checkboxes_in_table_cells', True)

        for c in cells:
            cx0, cy0, cx1, cy1 = c
            cw = cx1 - cx0
            ch = cy1 - cy0
            if not (6 <= cw <= 60 and 6 <= ch <= 60):
                continue
            txt = pm.get_text_in_bbox((cx0 + 1, cy0 + 1, cx1 - 1, cy1 - 1), 0.20)
            if txt:
                continue

            if disable_cb:
                results.append(FieldCandidate(
                    page=page_num,
                    x0=cx0 + padding, y0=cy0 + padding,
                    x1=cx1 - padding, y1=cy1 - padding,
                    field_type=FieldType.TEXT,
                    source='grid_fallback_table_cell',
                    name_hint=f"Cell_{int(cx0)}_{int(cy0)}",
                    confidence=0.6,
                ))
            else:
                size = min(cw, ch) * 0.85
                cb_x = cx0 + (cw - size) / 2
                cb_y = cy0 + (ch - size) / 2
                results.append(FieldCandidate(
                    page=page_num,
                    x0=cb_x, y0=cb_y,
                    x1=cb_x + size, y1=cb_y + size,
                    field_type=FieldType.CHECKBOX,
                    source='table_cell',
                    checkbox_width=size, checkbox_height=size,
                    confidence=0.65,
                ))

        return results

    # ------------------------------------------------------------------
    # A6-A7: Analyze remaining grid cells
    # ------------------------------------------------------------------

    def _grid_analyze_cells(self, pm, cells, v_lines, global_headers_map,
                            grid_prefers_checkboxes, prefilled_cols,
                            column_classifications=None) -> List[FieldCandidate]:
        """Full cell analysis for remaining grid cells (non-empty, non-split)."""
        results: List[FieldCandidate] = []
        page_num = pm.page_num
        padding = self.table_cell_padding
        disable_cb = self.settings.get('disable_checkboxes_in_table_cells', True)
        col_class = column_classifications or {}

        # Detect checkbox columns from header labels
        checkbox_columns = []
        header_row_y = None
        row_cells_map: Dict[float, List] = {}
        for c in cells:
            row_cells_map.setdefault(round(c[1], 1), []).append(c)

        # Rating-scale keywords — narrow columns with these headers are
        # almost always checkbox/radio columns, not text entry.
        _RATING_SCALE_KEYWORDS = frozenset({
            'yes', 'no', 'n/a', 'na', 'pass', 'fail', 'y', 'n',
            'type', 'measure', 'system', 'primary', 'cooling', 'heating',
            # Rating / Likert scale words
            'difficult', 'easy', 'excellent', 'good', 'fair', 'poor',
            'agree', 'disagree', 'neutral', 'satisfied', 'unsatisfied',
            'always', 'never', 'sometimes', 'often', 'rarely',
            'strong', 'weak', 'moderate', 'high', 'medium', 'low',
            'very', 'somewhat', 'not', 'applicable',
        })

        for y_key in sorted(row_cells_map.keys()):
            row = row_cells_map[y_key]
            if len(row) < 3:
                continue
            labeled = []
            for rc in row:
                rt = pm.get_text_in_bbox(rc, 0.20)
                if rt:
                    labeled.append((rc, rt))
            if len(labeled) >= 2:
                header_row_y = y_key
                for rc, rt in labeled:
                    short = rt.strip().lower()
                    rt_lower = rt.lower()
                    is_numeric_header = ('# of' in rt or 'number of' in rt_lower or
                                         'qty' in short or 'quantity' in short or
                                         'count' in short or '# fixtures' in rt_lower or
                                         'score' in rt_lower or 'point' in rt_lower)
                    if is_numeric_header:
                        continue

                    # Normalize spaced-out text (e.g. "V e r y") for matching
                    collapsed = re.sub(r'(?<=\w)\s+(?=\w)', '', short)

                    # Check: short header (≤3 chars), known keyword, or
                    # narrow column whose collapsed header contains a
                    # rating-scale keyword.
                    is_checkbox_header = (
                        len(short) <= 3
                        or short in _RATING_SCALE_KEYWORDS
                        or any(kw in collapsed for kw in _RATING_SCALE_KEYWORDS)
                    )
                    if is_checkbox_header:
                        if (rc[2] - rc[0]) <= 60:
                            checkbox_columns.append((rc[0], rc[2]))
                break

        for cell in cells:
            cx0, cy0, cx1, cy1 = cell
            cw = cx1 - cx0
            ch = cy1 - cy0

            # Check if this cell falls in a detected checkbox column
            in_detected_cb_col = (checkbox_columns and
                any(not (cx1 < c0 + 2 or cx0 > c1 - 2) for c0, c1 in checkbox_columns))

            # Minimum dimension guard — relax for checkbox-column cells
            if in_detected_cb_col:
                if cw < 15 or ch < 8:
                    continue
            else:
                if cw < 30 or ch < 12:
                    continue

            # Column classification guard
            if self._get_column_classification(cx0, col_class) == 'read-only':
                inner_peek = pm.get_text_in_bbox((cx0, cy0, cx1, cy1), 0.15) or ''
                if ':' not in inner_peek and '___' not in inner_peek:
                    # Exempt cells with text at the TOP + blank entry
                    # space below (form label + entry pattern).
                    if not self._is_label_entry_cell(
                            pm, cx0, cy0, cx1, cy1, ch):
                        continue

            # Detect quantity columns
            col_is_quantity = False
            if header_row_y is not None:
                for hc in row_cells_map.get(header_row_y, []):
                    if abs(cx0 - hc[0]) < 5 and abs(cx1 - hc[2]) < 5:
                        ht = pm.get_text_in_bbox(hc, 0.20)
                        if ht and ('# of' in ht or 'qty' in ht.lower() or 'quantity' in ht.lower()):
                            col_is_quantity = True
                            break

            # Get cell text
            inner_text = pm.get_text_in_bbox((cx0, cy0, cx1, cy1), 0.15)
            inner_words = pm.get_words_in_bbox((cx0, cy0, cx1, cy1), 0.15)

            # Dense text detection: skip cells with >70% text fill
            if inner_words and len(inner_text) > 5:
                txt_x0 = min(float(w['x0']) for w in inner_words)
                txt_x1 = max(float(w['x1']) for w in inner_words)
                fill_ratio = (txt_x1 - txt_x0) / cw if cw > 0 else 0
                has_fill = ('___' in inner_text or
                            (':' in inner_text and cx1 - txt_x1 > 25) or
                            any(c in inner_text for c in ['☐', '☑', '□', '⬜', '\u20DE', '☒', '❑', '❒']))
                if fill_ratio > 0.70 and not has_fill and len(inner_text.split()) >= 3:
                    continue

            # Header bar skip (with exceptions for field cues)
            if pm.is_in_header_bar(cx0, cy0, cx1, cy1):
                txt_lower = inner_text.lower() if inner_text else ""
                has_field_cues = (':' in inner_text or '___' in inner_text or
                                  any(x in txt_lower for x in ('yes', 'no', 'n/a')))
                if inner_words:
                    txt_x1_w = max(float(w['x1']) for w in inner_words)
                    is_full_width = (cx1 - cx0) > 400
                    is_likely_input = (len(inner_text) > 5 and cx1 - txt_x1_w > 35 and not is_full_width)
                else:
                    is_likely_input = False

                if not (has_field_cues or is_likely_input):
                    continue

            # Generic header detection (text density & centering)
            cell_text = pm.get_text_in_bbox((cx0 - 0.5, cy0 - 0.5, cx1 + 0.5, cy1 + 0.5), 0.15)
            words = pm.get_words_in_bbox((cx0 - 0.5, cy0 - 0.5, cx1 + 0.5, cy1 + 0.5), 0.15)

            if words and cell_text.strip():
                wx0 = min(float(w['x0']) for w in words)
                wx1 = max(float(w['x1']) for w in words)
                txt_w = wx1 - wx0
                width_fill = txt_w / cw if cw > 0 else 0
                height_fill = (max(float(w['bottom']) for w in words) - min(float(w['top']) for w in words)) / ch if ch > 0 else 0
                has_colon = ':' in cell_text
                is_protected = any(kw in cell_text.lower() for kw in _PROTECTED_LABEL_KEYWORDS)
                has_underscore_fill = '____' in cell_text

                if '_' not in cell_text and not has_colon and not is_protected:
                    if width_fill > 0.85 or height_fill > 0.6:
                        continue

                if not has_colon and not is_protected and not has_underscore_fill:
                    margin_left = wx0 - cx0
                    margin_right = cx1 - wx1
                    if width_fill > 0.4 and abs(margin_left - margin_right) < 10:
                        continue

                # Skip small Yes/No label cells
                cleanup_text = cell_text.replace('\n', ' ').strip()
                if cleanup_text.lower() in ('yes', 'no') and cw < 40:
                    continue

            # Checkbox column handling — when a column has been
            # semantically identified as a checkbox/rating column via
            # its header, always create checkboxes regardless of the
            # disable_checkboxes_in_table_cells setting.  That setting
            # is for generic small grid cells, not for columns with
            # explicit checkbox semantics.
            if header_row_y is not None and cy0 > header_row_y + 2 and checkbox_columns and not col_is_quantity:
                in_cb_col = any(not (cx1 < c0 + 2 or cx0 > c1 - 2) for c0, c1 in checkbox_columns)
                if in_cb_col and not cell_text.strip() and not words:
                    # If a small vector checkbox rect exists inside the
                    # cell, match its exact size and position so the
                    # fillable widget aligns with the printed checkbox.
                    cb_rect = self._find_checkbox_rect_in_cell(
                        pm, cx0, cy0, cx1, cy1)
                    if cb_rect is not None:
                        rx0, ry0, rx1, ry1 = cb_rect
                        rw = rx1 - rx0
                        rh = ry1 - ry0
                        results.append(FieldCandidate(
                            page=page_num,
                            x0=rx0, y0=ry0,
                            x1=rx1, y1=ry1,
                            field_type=FieldType.CHECKBOX,
                            source='table_col',
                            checkbox_width=rw, checkbox_height=rh,
                            confidence=0.85,
                        ))
                        continue

                    # No visual checkbox rect — fall through to normal
                    # text-field processing.  Only cells with a visible
                    # drawn square get checkboxes.

            # Small square cells -> checkbox or text fallback
            is_small_square = (6 <= cw <= 40 and 6 <= ch <= 40 and
                               abs(cw - ch) <= max(6, 0.12 * max(cw, ch)))
            if is_small_square and grid_prefers_checkboxes and not cell_text.strip() and not words and not col_is_quantity:
                if disable_cb:
                    results.append(FieldCandidate(
                        page=page_num,
                        x0=cx0 + padding, y0=cy0 + padding,
                        x1=cx1 - padding, y1=cy1 - padding,
                        field_type=FieldType.TEXT,
                        source='grid_region_fallback',
                        name_hint=f"Cell_{int(cx0)}_{int(cy0)}",
                        confidence=0.55,
                    ))
                else:
                    size = min(cw, ch)
                    cb_x = cx0 + (cw - size) / 2
                    cb_y = cy0 + (ch - size) / 2
                    results.append(FieldCandidate(
                        page=page_num,
                        x0=cb_x, y0=cb_y,
                        x1=cb_x + size, y1=cb_y + size,
                        field_type=FieldType.CHECKBOX,
                        source='table_cell',
                        checkbox_width=size, checkbox_height=size,
                        confidence=0.6,
                    ))
                continue

            # Visual checkbox in otherwise non-checkbox cell -> text fallback
            if not cell_text.strip() and not words and cw > 10 and ch > 10:
                if disable_cb:
                    results.append(FieldCandidate(
                        page=page_num,
                        x0=cx0 + padding, y0=cy0 + padding,
                        x1=cx1 - padding, y1=cy1 - padding,
                        field_type=FieldType.TEXT,
                        source='visual_in_cell_fallback',
                        name_hint=f"Cell_{int(cx0)}_{int(cy0)}",
                        confidence=0.5,
                    ))
                    continue

            # A6: Embedded underscore detection
            if cell_text and '__' in cell_text and not cell_text.lower().startswith('name'):
                underscore_fields = self._detect_embedded_underscores(pm, cx0, cy0, cx1, cy1)
                if underscore_fields:
                    results.extend(underscore_fields)
                    continue

            # A7: Full cell analysis
            is_large = ch > 25
            analyzed = self._analyze_cell(
                pm, cx0, cy0, cx1, cy1, cell_text, words,
                global_headers_map, prefer_below=is_large,
                source_type='strict_grid', force_field_if_empty=True,
            )
            if analyzed:
                results.append(analyzed)

        # -----------------------------------------------------------
        # Table-level layout consistency: if the majority of labeled
        # cells chose under-label placement, convert the minority
        # beside-label ones to under-label so the table looks uniform.
        # -----------------------------------------------------------
        below_results = [r for r in results if r.source == 'label_entry_below']
        beside_results = [r for r in results if r.source == 'label_entry_cell']

        if len(below_results) >= 2 and beside_results and len(below_results) > len(beside_results):
            margin = self.table_cell_padding
            converted = 0
            for r in beside_results:
                # Recover the original cell by searching for label words.
                # label_entry_cell: x0 = word_x1 + 4, so label is to the left.
                # Search up to 20pt above r.y0 — in tall cells the label
                # sits at the cell top while the beside-field sits near
                # the bottom, so r.y0-2 misses the label text.
                label_words = pm.get_words_in_bbox(
                    (r.x0 - 200, r.y0 - 20, r.x0, r.y1 + 2), 0.3,
                )
                if not label_words:
                    continue
                # Find the cell's full left edge (leftmost word x0 minus margin)
                cell_x0 = min(float(w['x0']) for w in label_words) - margin
                text_bottom = max(float(w['bottom']) for w in label_words)
                entry_y0 = text_bottom + 0.5
                entry_y1 = r.y1  # keep the same bottom (= cell bottom - margin)
                if entry_y1 - entry_y0 > 9:
                    r.x0 = cell_x0 + margin
                    r.y0 = entry_y0
                    r.source = 'label_entry_below'
                    r.label_position = 'above'
                    converted += 1
            if converted:
                print(f"   [TableCells] Consistency: converted {converted} beside->below")

        return results

    # ------------------------------------------------------------------
    # Helper: find a small vector checkbox rect inside a table cell
    # ------------------------------------------------------------------

    @staticmethod
    def _find_checkbox_rect_in_cell(
        pm: 'PageModel',
        cx0: float, cy0: float, cx1: float, cy1: float,
        min_size: float = 3.0,
        max_size: float = 16.0,
    ) -> 'Optional[Tuple[float, float, float, float]]':
        """Search page rects for a small square rect inside the cell bounds.

        Some PDFs draw a small checkbox rectangle inside each table cell
        (e.g. 5.6×5.6pt orange boxes).  When we find one, the fillable
        widget should match its exact size and position rather than
        filling the whole cell.

        Returns (rx0, ry0, rx1, ry1) of the best matching rect, or None.
        """
        best = None
        best_area = 0.0
        tol = 2.0  # cell containment tolerance

        for rect in pm.rects:
            rx0 = rect.get('x0', 0)
            ry0 = rect.get('top', rect.get('y0', 0))
            rx1 = rect.get('x1', 0)
            ry1 = rect.get('bottom', rect.get('y1', 0))

            rw = abs(rx1 - rx0)
            rh = abs(ry1 - ry0)

            # Must be within size bounds
            if not (min_size <= rw <= max_size):
                continue
            if not (min_size <= rh <= max_size):
                continue
            # Must be roughly square (tolerance 3pt)
            if abs(rw - rh) > 3.0:
                continue
            # Must be inside the cell (with tolerance)
            if rx0 < cx0 - tol or ry0 < cy0 - tol:
                continue
            if rx1 > cx1 + tol or ry1 > cy1 + tol:
                continue

            # Prefer the largest qualifying rect in the cell
            area = rw * rh
            if area > best_area:
                best_area = area
                best = (rx0, ry0, rx1, ry1)

        return best

    # ==================================================================
    # TRACK B: pdfplumber Table Processing
    # ==================================================================

    def _process_pdfplumber_tables(self, pm: PageModel, v_lines, global_headers_map) -> List[FieldCandidate]:
        """Process all pdfplumber-detected tables on a page."""
        results: List[FieldCandidate] = []
        page_num = pm.page_num

        for table in pm.tables:
            raw_cells = getattr(table, 'cells', None) or []
            if not raw_cells:
                continue

            # Split cells by vertical lines
            cells = self._split_cells_by_v_lines(raw_cells, v_lines)
            if not cells:
                continue

            # Skip tables beyond page boundaries
            bbox = getattr(table, 'bbox', None)
            if bbox:
                tx0, ty0, tx1, ty1 = bbox
                if tx0 < 0 or tx1 > pm.width or ty0 < 0 or ty1 > pm.height:
                    continue

            table_width = (bbox[2] - bbox[0]) if bbox else 500

            # ----------------------------------------------------------
            # B1: Header row detection
            # ----------------------------------------------------------
            y_values = sorted(set(c[1] for c in cells))
            header_y_values = set()
            first_data_row_idx = None
            column_header_row_idx = None

            for row_idx, row_y in enumerate(y_values):
                row_cells = [c for c in cells if abs(c[1] - row_y) < 2]
                row_width = sum(c[2] - c[0] for c in row_cells)
                is_true_header = False

                # TYPE 1: Single spanning cell (section header/title)
                if len(row_cells) == 1 and row_width > table_width * 0.8:
                    cell = row_cells[0]
                    cell_text = pm.get_text_in_bbox((cell[0] - 1, cell[1] - 1, cell[2] + 1, cell[3] + 1), 0.20)
                    if cell_text:
                        is_short_title = (len(cell_text) < 50 and ':' not in cell_text and
                                          '\n' not in cell_text and
                                          not any(c.isdigit() for c in cell_text[:5]))
                        is_section_header = (
                            cell_text.startswith(('I.', 'II.', 'III.', 'IV.', 'V.', 'VI.', 'VII.', 'VIII.'))
                            or cell_text.isupper()
                            or len(cell_text) > 100
                            or is_short_title
                            or (len(row_cells) == 1 and row_width > table_width * 0.9 and ':' not in cell_text)
                        )
                        is_true_header = is_section_header
                    else:
                        is_true_header = True

                # TYPE 2: Multi-cell column header row
                elif len(row_cells) >= 2:
                    cells_with_text = 0
                    cells_empty = 0
                    cells_with_colon = 0
                    cells_are_data = 0
                    cells_are_form_inputs = 0

                    for rc in row_cells:
                        rc_w = rc[2] - rc[0]
                        if rc_w < 10:
                            continue
                        rc_text = pm.get_text_in_bbox((rc[0] - 1, rc[1] - 1, rc[2] + 1, rc[3] + 1), 0.20)
                        if rc_text:
                            cells_with_text += 1
                            if ':' in rc_text:
                                cells_with_colon += 1
                            rc_lower = rc_text.lower()
                            is_checkbox_p = any(p in rc_lower for p in ('yes', 'no', 'n/a', 'pass', 'fail'))
                            is_numeric = rc_text.replace('.', '').replace(',', '').replace('-', '').isdigit()
                            if is_checkbox_p or is_numeric:
                                cells_are_data += 1
                            # Form input detection
                            clean_t = rc_text.replace(' ', '').replace('\n', '')
                            rc_h = rc[3] - rc[1]
                            first_line = rc_text.split('\n')[0].strip()
                            est_w = len(first_line.replace(' ', '')) * 6
                            text_area_ratio = (est_w * 12) / (rc_w * rc_h) if rc_w * rc_h > 0 else 1
                            is_form_input = (rc_h > 18 and (
                                first_line.lower().startswith('name') or
                                (len(clean_t) < 25 and (rc_w > est_w * 1.5 or text_area_ratio < 0.4))
                            ))
                            if is_form_input:
                                cells_are_form_inputs += 1
                        else:
                            cells_empty += 1

                    total_wide = cells_with_text + cells_empty

                    # Peek ahead: check if next row has empty cells
                    has_empty_below = False
                    if row_idx + 1 < len(y_values):
                        next_y = y_values[row_idx + 1]
                        next_cells = [c for c in cells if abs(c[1] - next_y) < 2]
                        next_empty = 0
                        for nc in next_cells:
                            nt = pm.get_text_in_bbox((nc[0] - 1, nc[1] - 1, nc[2] + 1, nc[3] + 1), 0.20)
                            if nt:
                                nt_clean = nt.replace('_', '').strip()
                                if not nt_clean:
                                    next_empty += 1
                            else:
                                next_empty += 1
                        if next_empty > 0 and len(next_cells) >= 2:
                            has_empty_below = True

                    is_standard = (total_wide >= 2 and cells_empty == 0 and
                                   cells_with_colon == 0 and cells_are_data == 0 and
                                   cells_are_form_inputs < total_wide * 0.5)
                    is_peek = (has_empty_below and total_wide >= 2 and
                               cells_empty == 0 and cells_are_data == 0)

                    if is_standard or is_peek:
                        is_true_header = True
                        column_header_row_idx = row_idx

                if is_true_header:
                    header_y_values.add(row_y)
                elif first_data_row_idx is None:
                    first_data_row_idx = row_idx

            # Determine header row for naming
            header_row_idx = column_header_row_idx if column_header_row_idx is not None else 0
            header_row_y = y_values[header_row_idx] if header_row_idx < len(y_values) else (y_values[0] if y_values else 0)
            header_cells_list = [c for c in cells if abs(c[1] - header_row_y) < 2]

            # Analyze header row
            first_row_is_header, headers_map, skip_columns = self._analyze_header_row(
                pm, header_cells_list, cells, y_values,
            )
            if first_row_is_header:
                header_y_values.add(header_row_y)

            # Column-header semantic classification
            column_classifications = {}
            if headers_map:
                column_classifications = self._classify_columns(
                    pm, headers_map, cells,
                    round(header_row_y, 0) if y_values else None
                )
                pm.column_classifications.update(column_classifications)

            # ----------------------------------------------------------
            # B2: Empty data rows under column headers
            # ----------------------------------------------------------
            if first_row_is_header and headers_map:
                empty_row_results = self._process_empty_data_rows(
                    pm, cells, header_cells_list, headers_map, v_lines, y_values, header_row_y,
                    column_classifications,
                )
                results.extend(empty_row_results)

            # Sub-table detection (when no empty data rows found)
            sub_results = self._process_sub_tables(pm, cells)
            results.extend(sub_results)

            # Prefilled column detection for this table
            prefilled_cols = self._detect_prefilled_columns(pm, cells, round(header_row_y, 0) if y_values else None)
            pm.prefilled_col_ranges |= prefilled_cols

            # Decide placement preference for table (below vs. beside label)
            cells_can_below = 0
            cells_total = 0
            for cell in cells:
                cw = cell[2] - cell[0]
                ch = cell[3] - cell[1]
                if cw < 30 or ch < 12:
                    continue
                cells_total += 1
                if ch > 25:
                    cells_can_below += 1
            section_prefers_below = cells_total > 0 and (cells_can_below / cells_total) >= 0.5

            # ----------------------------------------------------------
            # B3: Cell analysis loop
            # ----------------------------------------------------------
            min_cell_x0 = min(c[0] for c in cells) if cells else 0

            for cell in cells:
                x0, y0, x1, y1 = cell
                cw = x1 - x0
                ch = y1 - y0

                # Skip too-small cells
                if cw < 30 or ch < 12:
                    continue
                # Skip zero-area
                if x1 <= x0 + 0.5 or y1 <= y0 + 0.5:
                    continue

                # Prefilled column guard
                if self._is_in_prefilled_column(x0, y0, x1, y1, prefilled_cols):
                    txt = pm.get_text_in_bbox((x0 + 1, y0 + 1, x1 - 1, y1 - 1), 0.20)
                    if txt != '$':
                        continue

                # Column classification guard (read-only columns)
                col_class = self._get_column_classification(x0, column_classifications)
                if col_class == 'read-only':
                    cell_peek = pm.get_text_in_bbox((x0, y0, x1, y1), 0.20) or ''
                    if ':' not in cell_peek and '___' not in cell_peek:
                        # Exempt cells with text at the TOP + blank entry
                        # space below (form label + entry pattern).
                        cell_h = y1 - y0
                        if not self._is_label_entry_cell(
                                pm, x0, y0, x1, y1, cell_h):
                            continue

                # Image upload column detection
                # (skipped for brevity — the resolver handles image_upload_regions)

                # Header bar exclusion
                if pm.is_in_header_bar(x0, y0, x1, y1):
                    cell_peek = pm.get_text_in_bbox((x0, y0, x1, y1), 0.20)
                    if not (':' in (cell_peek or '') or ch > 25):
                        continue

                # Column clipping to header width
                cell_text_check = pm.get_text_in_bbox((x0, y0, x1, y1), 0.20) or ""
                cell_text_lower = cell_text_check.lower()
                form_kw = ('customer', 'signature', 'name', 'title', 'address', 'city', 'state',
                           'zip', 'email', 'phone', 'company', 'account', 'date', 'inspector',
                           'organization', 'applicant', 'type', 'year', 'program')
                is_tall_label = ch > 25 and ':' in cell_text_lower
                is_large_text = ch > 100
                is_form_label_cell = any(kw in cell_text_lower for kw in form_kw) or is_tall_label or is_large_text

                if y0 > header_row_y + 5 and not is_form_label_cell:
                    all_hx = sorted([
                        k if isinstance(k, (int, float)) else (k[0] if isinstance(k, tuple) else k)
                        for k in headers_map.keys()
                    ])
                    for hx in all_hx:
                        if hx > x0 + 30 and hx < x1 - 10:
                            has_divider = False
                            for vx, vy0, vy1 in v_lines:
                                if abs(vx - hx) < 5:
                                    ov = max(0, min(y1, vy1) - max(y0, vy0))
                                    if ov > (y1 - y0) * 0.5:
                                        has_divider = True
                                        break
                            if has_divider:
                                x1 = hx - 4
                                break

                # Header row skip
                header_row = any(abs(y0 - hy) < 5 for hy in header_y_values)

                # Skip skip_columns
                if self._is_skip_column(x0, skip_columns):
                    continue

                # Extract cell text
                cell_text = pm.get_text_in_bbox((x0 - 1, y0 - 1, x1 + 1, y1 + 1), 0.20) or ""
                words = pm.get_words_in_bbox((x0 - 1, y0 - 1, x1 + 1, y1 + 1), 0.20)

                # Adjacent empty cell detection (label + empty neighbor)
                if cell_text and words:
                    label_clean = re.sub(r'\s+', ' ', cell_text).strip()
                    if not _is_header_like_text(label_clean):
                        adj_result = self._try_adjacent_empty_cell(pm, cells, x0, y0, x1, y1, label_clean)
                        if adj_result:
                            results.append(adj_result)
                            continue

                # Label + entry cell (text on left, empty right)
                if words and cell_text:
                    entry_result = self._try_label_entry_cell(pm, cells, x0, y0, x1, y1, cell_text, words, headers_map, v_lines)
                    if entry_result:
                        results.append(entry_result)
                        continue

                # Deferred header row skip (after label+entry detection)
                if header_row:
                    if not ('____' in cell_text_lower or cell_text_lower.startswith('name') or not cell_text.strip()):
                        continue

                # Skip text-heavy cells
                if len(cell_text) > 80 or cell_text.count('\n') > 4:
                    is_large_tf = ('____' in cell_text or
                                   'describe:' in cell_text.lower() or
                                   'notes:' in cell_text.lower() or
                                   'comments:' in cell_text.lower() or
                                   cell_text.lower().startswith('name'))
                    if not is_large_tf:
                        continue

                # First column label skip
                is_first_col = abs(x0 - min_cell_x0) < 5
                starts_with_dollar = cell_text.startswith('$')
                cell_width = x1 - x0
                cell_height = y1 - y0
                text_at_top = False
                if words and cell_height > 20:
                    max_text_y = max(float(w.get('bottom', w.get('y1', y0))) for w in words)
                    text_at_top = max_text_y < y0 + cell_height / 2 + 5

                is_form_field = (cell_height > 15 and
                                 (cell_width > 80 or cell_height > 20) and
                                 (len(cell_text.replace(' ', '')) < 50 or text_at_top))

                if (is_first_col and cell_text and ':' not in cell_text and
                        not starts_with_dollar and not cell_text.lower().startswith('name')):
                    if not is_form_field:
                        continue

                # Skip exact section header labels
                cell_clean = cell_text.strip().lower()
                if cell_clean in _SKIP_LABELS_EXACT:
                    continue

                # Embedded underscore detection
                if '__' in cell_text and not cell_text.lower().startswith('name'):
                    underscore_fields = self._detect_embedded_underscores(pm, x0, y0, x1, y1)
                    if underscore_fields:
                        results.extend(underscore_fields)
                        continue

                # Row label for naming
                row_label = None
                if not is_first_col:
                    same_row = [c for c in cells if abs(c[1] - y0) < 3]
                    if same_row:
                        leftmost = min(same_row, key=lambda c: c[0])
                        if abs(leftmost[0] - min_cell_x0) < 5:
                            left_text = pm.get_text_in_bbox(leftmost, 0.20)
                            if left_text and ':' in left_text:
                                row_label = left_text

                # Full cell analysis
                analyzed = self._analyze_cell(
                    pm, x0, y0, x1, y1, cell_text, words,
                    headers_map, prefer_below=section_prefers_below,
                    row_label=row_label, source_type='table_data_row',
                )
                if analyzed:
                    results.append(analyzed)

        # -----------------------------------------------------------
        # Table-level layout consistency (same as Track A)
        # -----------------------------------------------------------
        below_results = [r for r in results if r.source == 'label_entry_below']
        beside_results = [r for r in results if r.source == 'label_entry_cell']

        if len(below_results) >= 2 and beside_results and len(below_results) > len(beside_results):
            margin = self.table_cell_padding
            converted = 0
            for r in beside_results:
                # Search up to 20pt above r.y0 (same fix as Track A)
                label_words = pm.get_words_in_bbox(
                    (r.x0 - 200, r.y0 - 20, r.x0, r.y1 + 2), 0.3,
                )
                if not label_words:
                    continue
                cell_x0 = min(float(w['x0']) for w in label_words) - margin
                text_bottom = max(float(w['bottom']) for w in label_words)
                entry_y0 = text_bottom + 0.5
                entry_y1 = r.y1
                if entry_y1 - entry_y0 > 9:
                    r.x0 = cell_x0 + margin
                    r.y0 = entry_y0
                    r.source = 'label_entry_below'
                    r.label_position = 'above'
                    converted += 1
            if converted:
                print(f"   [TableCells] Consistency: converted {converted} beside->below")

        return results

    # ------------------------------------------------------------------
    # B1 helpers: Header row analysis
    # ------------------------------------------------------------------

    def _analyze_header_row(self, pm, header_cells, all_cells, y_values):
        """
        Analyze a potential header row.
        Returns (is_header: bool, headers_map: dict, skip_columns: list).
        """
        headers_map = {}
        skip_columns = []
        if not header_cells:
            return False, headers_map, skip_columns

        # Build headers_map from header cells
        for cell in header_cells:
            cx0, cy0, cx1, cy1 = cell
            text = pm.get_text_in_bbox((cx0 - 1, cy0 - 1, cx1 + 1, cy1 + 1), 0.20)
            if text:
                text_clean = text.strip()
                headers_map[round(cx0)] = text_clean
                # Detect skip columns (Pass/Fail/Yes/No short labels)
                if text_clean.lower() in ('pass', 'fail', 'pass/fail', 'p/f', 'y/n'):
                    skip_columns.append(round(cx0))

        # Determine if this is a header row
        if len(header_cells) == 1:
            cell = header_cells[0]
            text = pm.get_text_in_bbox((cell[0] - 1, cell[1] - 1, cell[2] + 1, cell[3] + 1), 0.20)
            if text:
                is_header = (text.isupper() or len(text) > 100 or
                             text.startswith(('I.', 'II.', 'III.', 'IV.', 'V.')))
                return is_header, headers_map, skip_columns

        # Multi-cell: check if all cells have text
        cells_with_text = sum(1 for c in header_cells
                              if pm.get_text_in_bbox((c[0] - 1, c[1] - 1, c[2] + 1, c[3] + 1), 0.20))
        cells_empty = len(header_cells) - cells_with_text
        is_header = (cells_with_text >= 2 and cells_empty == 0)

        return is_header, headers_map, skip_columns

    def _is_skip_column(self, x0, skip_columns):
        """Check if x0 falls in a skip column."""
        return any(abs(x0 - sc) < 20 for sc in skip_columns)

    # ------------------------------------------------------------------
    # Column-header semantic classification
    # ------------------------------------------------------------------

    def _classify_columns(self, pm, headers_map, cells, header_y=None):
        """
        Classify each column as 'fillable', 'read-only', or 'unknown'
        based on header text semantics and cell content analysis.

        Key safety: ALWAYS verify with cell content before marking read-only.
        If all data cells are empty, the column is almost certainly fillable
        (a tally sheet), so we use 'unknown' to avoid suppressing fields.

        Returns: Dict mapping round(x0) -> classification string.
        """
        classifications = {}

        for hx, header_text in headers_map.items():
            ht_lower = header_text.lower().strip()

            # Check against keyword lists using word-boundary matching
            is_fillable = _header_matches_keywords(ht_lower, _FILLABLE_HEADER_WORDS)
            is_readonly = _header_matches_keywords(ht_lower, _READONLY_HEADER_WORDS)

            if is_fillable and not is_readonly:
                classifications[hx] = 'fillable'
            elif is_readonly and not is_fillable:
                # Keyword says read-only, but VERIFY with cell content.
                # If most data cells are empty, this is likely a tally sheet
                # where the "read-only-sounding" header is actually fillable.
                classifications[hx] = self._tiebreak_column_by_content(
                    pm, hx, cells, header_y
                )
            elif is_fillable and is_readonly:
                # Ambiguous — tiebreak by checking cell content
                classifications[hx] = self._tiebreak_column_by_content(
                    pm, hx, cells, header_y
                )
            elif not header_text.strip():
                # Blank header = likely fillable
                classifications[hx] = 'fillable'
            else:
                classifications[hx] = 'unknown'

        return classifications

    def _tiebreak_column_by_content(self, pm, col_x0, cells, header_y=None):
        """
        Verify column classification by checking data cell content.
        If >=50% of data cells have pre-printed text → 'read-only'.
        Otherwise → 'unknown' (safe default: never blocks legitimate fields).
        """
        col_cells = [c for c in cells if abs(round(c[0]) - col_x0) < 20]
        data_cells = [c for c in col_cells
                      if header_y is None or abs(round(c[1]) - header_y) > 5]

        if len(data_cells) < 2:
            return 'unknown'

        filled = 0
        sample = data_cells[:10]
        for dc in sample:
            txt = pm.get_text_in_bbox(
                (dc[0] + 1, dc[1] + 1, dc[2] - 1, dc[3] - 1), 0.20
            )
            if txt and txt.strip() and txt.strip() != '$':
                filled += 1

        ratio = filled / len(sample)
        return 'read-only' if ratio >= 0.50 else 'unknown'

    def _get_column_classification(self, x0, classifications):
        """Look up column classification with tolerance matching."""
        rx = round(x0)
        if rx in classifications:
            return classifications[rx]
        # Fuzzy match within 20px
        for hx, cls in classifications.items():
            if abs(rx - hx) < 20:
                return cls
        return 'unknown'

    @staticmethod
    def _is_label_entry_cell(pm, x0, y0, x1, y1, cell_h):
        """Check if a cell has a form label at the top with blank entry
        space below (≥ 14pt).

        Returns True for cells like "Date Installed" where text sits in
        the top portion and the bottom is blank for user entry.
        Returns False for empty cells and cells where text fills or is
        centered in the cell (pre-printed data values).
        """
        if cell_h <= 20:
            return False
        words = pm.get_words_in_bbox((x0, y0, x1, y1), 0.3)
        if not words:
            return False  # empty cell — not a label+entry
        text_bottom = max(
            w.get('bottom', w.get('y1', y0)) for w in words
        )
        blank_below = y1 - text_bottom
        return blank_below >= 14

    # ------------------------------------------------------------------
    # B2: Empty data rows
    # ------------------------------------------------------------------

    def _process_empty_data_rows(self, pm, cells, header_cells, headers_map,
                                 v_lines, y_values, header_row_y,
                                 column_classifications=None) -> List[FieldCandidate]:
        """Create numbered fields under column headers when >50% rows are empty.

        Uses per-column classification: read-only columns are ignored when
        checking emptiness, so mixed rows (pre-printed + empty fillable cells)
        are correctly handled.
        """
        results: List[FieldCandidate] = []
        page_num = pm.page_num
        padding = self.table_cell_padding
        col_class = column_classifications or {}

        data_rows = [y for y in y_values if y > header_row_y + 5]
        if not data_rows:
            return results

        # Sample first 10 rows — per-column emptiness check
        sample_empty = 0
        sample_total = 0
        for ry in data_rows[:10]:
            row_cells = [c for c in cells if abs(c[1] - ry) < 2]
            if len(row_cells) < 2:
                continue
            sample_total += 1
            fillable_cols_empty = True
            for rc in row_cells:
                rc_class = self._get_column_classification(rc[0], col_class)
                if rc_class == 'read-only':
                    continue  # ignore pre-printed columns
                txt = pm.get_text_in_bbox((rc[0] - 1, rc[1] - 1, rc[2] + 1, rc[3] + 1), 0.20)
                if txt:
                    fillable_cols_empty = False
                    break
            if fillable_cols_empty:
                sample_empty += 1

        if sample_total == 0 or sample_empty / sample_total <= 0.5:
            return results

        # Collect rows where fillable columns are empty
        empty_rows = []
        for ry in data_rows:
            row_cells = [c for c in cells if abs(c[1] - ry) < 2]
            if len(row_cells) < 2:
                continue
            fillable_cols_empty = True
            for rc in row_cells:
                rc_class = self._get_column_classification(rc[0], col_class)
                if rc_class == 'read-only':
                    continue
                txt = pm.get_text_in_bbox((rc[0] - 1, rc[1] - 1, rc[2] + 1, rc[3] + 1), 0.20)
                if txt:
                    fillable_cols_empty = False
                    break
            if fillable_cols_empty and len(row_cells) >= 2:
                empty_rows.append((ry, row_cells))

        if len(empty_rows) < 2:
            return results

        # Build local headers_map from rows above first empty row
        first_empty_y = min(ry for ry, _ in empty_rows)
        local_headers: Dict[int, str] = {}
        header_area_cells = [c for c in cells if first_empty_y - 50 < c[1] < first_empty_y - 5]
        for hc in header_area_cells:
            hx0, hy0, hx1, hy1 = hc
            if (hx1 - hx0) > 100:
                continue
            txt = pm.get_text_in_bbox((hx0 - 1, hy0 - 1, hx1 + 1, hy1 + 1), 0.20)
            if txt and not txt.lower().startswith('note') and 'eligibility' not in txt.lower():
                primary = txt.strip().replace('\n', ' ').split('(')[0].strip()
                if primary and len(primary) < 30:
                    local_headers[round(hx0)] = primary

        for row_idx, (ry, row_cells) in enumerate(empty_rows, start=1):
            for rc in row_cells:
                cx0, cy0, cx1, cy1 = rc
                if (cx1 - cx0) < 30 or (cy1 - cy0) < 10:
                    continue

                # Skip read-only columns
                rc_class = self._get_column_classification(cx0, col_class)
                if rc_class == 'read-only':
                    continue

                # Find column header
                header_name = None
                for hx, hname in local_headers.items():
                    if abs(cx0 - hx) < 20:
                        header_name = hname
                        break
                if not header_name:
                    header_name = self._get_column_header(cx0, cx1, headers_map) or "Field"

                base_name = clean_field_name(header_name)
                fmt, fmt_opts = detect_field_format(None, header_name)

                results.append(FieldCandidate(
                    page=page_num,
                    x0=cx0 + padding, y0=cy0 + padding,
                    x1=cx1 - padding, y1=cy1 - padding,
                    field_type=FieldType.TEXT,
                    source='table_data_row',
                    name_hint=f"{base_name}_{row_idx}",
                    label=header_name,
                    column_header=header_name,
                    format_hint=fmt,
                    format_options=fmt_opts or {},
                    confidence=0.75,
                ))

        return results

    # ------------------------------------------------------------------
    # Sub-table detection
    # ------------------------------------------------------------------

    def _process_sub_tables(self, pm, cells) -> List[FieldCandidate]:
        """Detect sub-table headers with (Table X) patterns followed by empty rows."""
        results: List[FieldCandidate] = []
        page_num = pm.page_num
        padding = self.table_cell_padding

        potential_headers = []
        cells_by_row: Dict[int, List] = defaultdict(list)

        for cell in cells:
            text = pm.get_text_in_bbox((cell[0] - 1, cell[1] - 1, cell[2] + 1, cell[3] + 1), 0.20)
            if not text:
                text = ""
            row_key = round(cell[1] / 5) * 5
            cells_by_row[row_key].append((cell[1], cell[0], cell[2], text))
            if '(Table' in text:
                potential_headers.append((cell[1], cell[0], cell[2], text))

        headers_by_row: Dict[int, List] = defaultdict(list)
        for y, x0, x1, text in potential_headers:
            row_key = round(y / 5) * 5
            headers_by_row[row_key].append((y, x0, x1, text))

        # Expand rows with 3+ (Table X) headers to include all cells from that row
        for row_key in list(headers_by_row.keys()):
            if len(headers_by_row[row_key]) >= 3:
                existing_x0s = {h[1] for h in headers_by_row[row_key]}
                for y, x0, x1, text in cells_by_row.get(row_key, []):
                    if x0 not in existing_x0s and text.strip():
                        headers_by_row[row_key].append((y, x0, x1, text))

        for row_key in sorted(headers_by_row.keys()):
            local_headers = headers_by_row[row_key]
            if len(local_headers) < 3:
                continue

            header_y = local_headers[0][0]
            local_hmap: Dict[Tuple[float, float], str] = {}
            for _, hx0, hx1, htext in local_headers:
                local_hmap[(hx0, hx1)] = clean_field_name(htext)

            rows_below = sorted(set(c[1] for c in cells if c[1] > header_y + 5))
            local_empty = []
            for rb in rows_below[:6]:
                row_cells = [c for c in cells if abs(c[1] - rb) < 2]
                if len(row_cells) < 3:
                    break
                all_empty = True
                for rc in row_cells:
                    txt = pm.get_text_in_bbox(rc, 0.20)
                    if txt:
                        all_empty = False
                        break
                if all_empty:
                    local_empty.append((rb, row_cells))
                else:
                    break

            if len(local_empty) >= 2:
                for ridx, (ry, row_cells) in enumerate(local_empty, start=1):
                    for rc in row_cells:
                        cx0, cy0, cx1, cy1 = rc
                        if (cx1 - cx0) < 30 or (cy1 - cy0) < 8:
                            continue
                        hname = None
                        for (hx0, hx1), hn in local_hmap.items():
                            if abs(cx0 - hx0) < 20:
                                hname = hn
                                break
                        if not hname:
                            hname = "Field"
                        fmt, fmt_opts = detect_field_format(None, hname)
                        results.append(FieldCandidate(
                            page=page_num,
                            x0=cx0 + padding, y0=cy0 + padding,
                            x1=cx1 - padding, y1=cy1 - padding,
                            field_type=FieldType.TEXT,
                            source='sub_table_data_row',
                            name_hint=f"{clean_field_name(hname)}_{ridx}",
                            label=hname,
                            column_header=hname,
                            format_hint=fmt,
                            format_options=fmt_opts or {},
                            confidence=0.7,
                        ))

        return results

    # ------------------------------------------------------------------
    # B3 helpers
    # ------------------------------------------------------------------

    def _try_adjacent_empty_cell(self, pm, cells, x0, y0, x1, y1, label_text) -> Optional[FieldCandidate]:
        """If cell has label text and an adjacent empty cell to the right, create field there."""
        page_num = pm.page_num
        padding = self.table_cell_padding

        for other in cells:
            if other == (x0, y0, x1, y1):
                continue
            ox0, oy0, ox1, oy1 = other
            if abs(oy0 - y0) < 3 and abs(oy1 - y1) < 3 and abs(ox0 - x1) < 3:
                if (ox1 - ox0) >= 20 and (oy1 - oy0) >= 10:
                    adj_text = pm.get_text_in_bbox((ox0 - 1, oy0 - 1, ox1 + 1, oy1 + 1), 0.20)
                    if not adj_text:
                        return FieldCandidate(
                            page=page_num,
                            x0=ox0 + padding, y0=oy0 + padding,
                            x1=ox1 - padding, y1=oy1 - padding,
                            field_type=FieldType.TEXT,
                            source='adjacent_empty_cell',
                            name_hint=clean_field_name(label_text)[:30],
                            label=label_text,
                            confidence=0.8,
                        )
        return None

    def _try_label_entry_cell(self, pm, cells, x0, y0, x1, y1,
                              cell_text, words, headers_map, v_lines) -> Optional[FieldCandidate]:
        """
        Try to create a label_entry_cell field (label on left, field on right).
        Returns a FieldCandidate if successful, None otherwise.
        """
        page_num = pm.page_num
        padding = self.table_cell_padding
        cw = x1 - x0
        ch = y1 - y0

        bounded_words = [w for w in words
                         if float(w['x0']) >= x0 - 0.5 and float(w['x1']) <= x1 + 0.5
                         and float(w['top']) >= y0 - 0.5 and float(w['bottom']) <= y1 + 0.5]
        use_words = bounded_words if bounded_words else words
        if not use_words:
            return None

        word_x1 = max(float(w['x1']) for w in use_words)
        empty_right = x1 - word_x1
        label_text = re.sub(r'\s+', ' ', cell_text).strip()

        if _is_header_like_text(label_text):
            return None

        # Skip long-data labels with narrow beside space
        if _is_long_data_label(label_text) and empty_right < 80:
            return None

        # Check for under-label possibility first
        try:
            text_top = min(float(w['top']) for w in use_words)
            text_bottom = max(float(w['bottom']) for w in use_words)
            room_below = y1 - text_bottom
        except Exception:
            text_top = y0
            room_below = 0

        # If label at top with usable room below, prefer under-label
        if (text_top - y0) < 8 and room_below >= 8:
            return None  # Let _analyze_cell handle under-label placement

        if empty_right <= 35 or (word_x1 - x0) >= (cw * 0.8):
            return None

        # Skip if inside header bar
        if pm.is_in_header_bar(x0, y0, x1, y1):
            return None
        # Skip thin spanning cells
        if ch < 22 and cw > 400:
            return None
        # Skip table header cells (high fill ratio, no colon, short)
        text_width = word_x1 - min(float(w['x0']) for w in use_words)
        text_fill = text_width / cw if cw > 0 else 0
        if text_fill > 0.6 and ':' not in label_text and ch < 25:
            return None

        gap_x0 = word_x1 + 4
        gap_words = pm.get_words_in_bbox((gap_x0, y0, x1, y1), 0.15)
        if gap_words:
            return None

        # Skip cells with interior row-separating h-lines.
        # Each h-line marks a table row boundary — a label_entry_cell
        # should not span multiple rows.  Use overlap (not containment)
        # because table-wide h-lines extend beyond individual columns.
        interior_hlines = 0
        cell_w = max(1, x1 - x0)
        for hl in pm.h_lines:
            hy = float(hl.get('y0', hl.get('top', 0)))
            hx0 = float(hl.get('x0', 0))
            hx1 = float(hl.get('x1', 0))
            if (y0 + 5 < hy < y1 - 5
                    and (hx1 - hx0) > 30
                    and min(hx1, x1) - max(hx0, x0) > cell_w * 0.5):
                interior_hlines += 1
        if interior_hlines >= 2:
            return None

        label_clean = re.sub(r'[^a-zA-Z0-9\s]', '', cell_text).strip()
        return FieldCandidate(
            page=page_num,
            x0=gap_x0, y0=y0 + padding,
            x1=x1 - padding, y1=y1 - padding,
            field_type=FieldType.TEXT,
            source='label_entry_cell',
            name_hint=label_clean[:30] if label_clean else f"Cell_{int(gap_x0)}_{int(y0)}",
            label=label_text,
            confidence=0.8,
        )

    # ==================================================================
    # Embedded underscore detection
    # ==================================================================

    def _detect_embedded_underscores(self, pm, x0, y0, x1, y1) -> List[FieldCandidate]:
        """
        Detect underscore fill-in patterns like "Volts ___ x Amps ___" within a cell.
        Returns field candidates for each underscore region.
        """
        results: List[FieldCandidate] = []
        page_num = pm.page_num
        words = pm.get_words_in_bbox((x0, y0, x1, y1), 0.15)
        if not words:
            return results

        # Group words by line
        sorted_words = sorted(words, key=lambda w: (float(w['top']), float(w['x0'])))
        lines_data = []
        for w in sorted_words:
            t = float(w['top'])
            b = float(w['bottom'])
            if not lines_data or abs(t - lines_data[-1]['top']) > 4:
                lines_data.append({'top': t, 'bottom': b, 'words': [w]})
            else:
                lines_data[-1]['words'].append(w)
                lines_data[-1]['bottom'] = max(lines_data[-1]['bottom'], b)

        for line_group in lines_data:
            l_words = sorted(line_group['words'], key=lambda w: float(w['x0']))

            for i, w in enumerate(l_words):
                wtext = w['text']
                if '__' not in wtext:
                    continue

                w_x0 = float(w['x0'])
                w_x1 = float(w['x1'])
                w_top = line_group['top']
                w_bot = line_group['bottom']

                # Find label text before this underscore
                label_parts = []
                for j in range(i - 1, -1, -1):
                    prev = l_words[j]
                    if '__' in prev['text']:
                        break
                    label_parts.insert(0, prev['text'])
                    if len(label_parts) >= 3:
                        break
                label = " ".join(label_parts).strip().rstrip(':')

                # Calculate underscore field bounds
                # Strip non-underscore prefix/suffix
                us_start = wtext.index('_')
                us_end = len(wtext) - len(wtext.rstrip('_'))
                if us_end == 0:
                    us_end = len(wtext)
                else:
                    us_end = len(wtext)

                # Use the word's full bounds for the underscore region
                field_x0 = w_x0
                field_x1 = w_x1

                # If the underscore word ends with trailing label text
                # (e.g., "____S/N" or "____Tons"), DON'T extend to the
                # next underscore word — the trailing text acts as a
                # label separator between two distinct fields.
                # Also trim field_x1 so it ends where underscores end,
                # not where the trailing label text ends.
                ends_with_label = not wtext.rstrip().endswith('_')

                if ends_with_label:
                    # Trim field_x1: interpolate where underscores end
                    # within the word, excluding the trailing label chars.
                    stripped = wtext.rstrip()
                    # Find last underscore position in the stripped text
                    last_us = len(stripped) - 1
                    while last_us >= 0 and stripped[last_us] != '_':
                        last_us -= 1
                    if last_us > 0 and len(stripped) > 0:
                        # Fraction of word width occupied by underscores
                        us_frac = (last_us + 1) / len(stripped)
                        word_w = w_x1 - w_x0
                        field_x1 = w_x0 + us_frac * word_w
                else:
                    # Extend to include consecutive underscore words
                    k = i + 1
                    while k < len(l_words) and '__' in l_words[k]['text']:
                        field_x1 = float(l_words[k]['x1'])
                        k += 1

                # Use a distinct sub-source for trailing-label splits
                # so AdjacentFieldMerger won't re-merge them.
                src = ('embedded_underscore_split' if ends_with_label
                       else 'embedded_underscore')

                if field_x1 - field_x0 >= 15:
                    results.append(FieldCandidate(
                        page=page_num,
                        x0=field_x0, y0=w_top - 1,
                        x1=field_x1, y1=w_bot + 1,
                        field_type=FieldType.TEXT,
                        source=src,
                        name_hint=clean_field_name(label)[:30] if label else "Entry",
                        label=label or None,
                        confidence=0.85,
                        source_line_x0=field_x0,
                        source_line_x1=field_x1,
                    ))

        return results

    # ==================================================================
    # Core cell analysis (_analyze_cell)
    # ==================================================================

    def _analyze_cell(self, pm: PageModel, x0, y0, x1, y1,
                      cell_text, words, headers_map,
                      prefer_below=False, row_label=None,
                      force_field_if_empty=False,
                      source_type='table_data_row') -> Optional[FieldCandidate]:
        """
        Analyze a single cell and return a FieldCandidate if it should become a field.

        This is the port of v23's _analyze_cell_for_field(), implementing:
        - Static value detection (prices, N/A)
        - Prefilled column guard
        - Header bar overlap detection
        - Label+entry positioning (beside or below)
        - Coordinate calculation with text shunning
        - Format detection and naming
        """
        page_num = pm.page_num
        margin = self.table_cell_padding
        cw = x1 - x0
        ch = y1 - y0
        clean_text = (cell_text or "").strip().replace('  ', ' ')
        clean_lower = clean_text.lower()

        # ---- FILTERS & SUPPRESSION ----

        # Static monetary value guard
        if clean_text:
            stripped = clean_text.strip()
            if re.match(r'^\$[\d,]+(\.\d{1,2})?$', stripped):
                return None
            if stripped.upper() in ('N/A', 'NA', 'N / A'):
                return None

        # Prefilled column guard
        if clean_text and not clean_text.strip().endswith(':') and clean_text.strip() != '$':
            if self._is_in_prefilled_column(x0, y0, x1, y1, pm.prefilled_col_ranges):
                return None

        # Image overlap detection: skip cells overlapping images
        for img in pm.images:
            img_x0 = float(img.get('x0', 0))
            img_y0 = float(img.get('y0', img.get('top', 0)))
            img_x1 = float(img.get('x1', 0))
            img_y1 = float(img.get('y1', img.get('bottom', 0)))
            if not (x1 < img_x0 or x0 > img_x1 or y1 < img_y0 or y0 > img_y1):
                return None

        # Pre-filled data value detection (narrow cells with prices/units)
        if clean_text and cw < 120:
            stripped = clean_text.strip()
            # Monetary values
            if re.match(r'^\$[\d,]+(\.\d{2})?$', stripped):
                return None
            # Pre-printed unit/equipment words
            data_units = ('fixture', 'lamp', 'ton', 'sensor', 'unit', 'each',
                          'sqft', 'sq ft', 'heat pump', 'thermostat', 'boiler',
                          'furnace', 'led', 'cfm', 'kwh', 'kw', 'btu')
            if clean_lower in data_units:
                return None
            # Pure numeric values: "12345", "0.95", "15%"
            if re.match(r'^\d[\d.,]*%?$', stripped):
                return None
            # Alphanumeric identifiers: "CA-L1", "R12", "A1", "EE-1"
            if re.match(r'^[A-Z]{1,4}[-]?\d{1,5}[A-Z]?$', stripped):
                return None
            # Percentage values: "95 %", "15.5%"
            if re.match(r'^\d+\.?\d*\s*%$', stripped):
                return None
            # Title-case multi-word names (2-4 words, each capitalized,
            # no fill indicators — catches "Heat Pump", "Gas Furnace")
            words_split = stripped.split()
            if (2 <= len(words_split) <= 4
                    and all(w[0].isupper() for w in words_split if w)
                    and '___' not in stripped and ':' not in stripped
                    and not any(kw in clean_lower for kw in (
                        'name', 'date', 'sign', 'enter', 'total',
                        'address', 'city', 'email', 'phone'))):
                return None

        # Instruction box suppression (cells with instructional text)
        if cw > 200 and len(clean_text) > 30:
            if any(k in clean_lower for k in _INSTRUCTION_KEYWORDS):
                return None

        # Header bar overlap guard
        if pm.is_in_header_bar(x0, y0, x1, y1):
            has_colon = ':' in clean_text
            # Allow cells with colons or tall cells
            if not has_colon and ch <= 25 and clean_text.strip():
                return None

        # Instruction keyword suppression
        if any(k in clean_lower for k in _INSTRUCTION_KILLS) and ch < 65:
            if y0 >= 250:
                return None

        # Full sentence detection: informational text ending with
        # sentence punctuation and having 8+ words is not fillable
        if clean_text and clean_text.rstrip()[-1:] in ('.', '?', '!'):
            word_count = len(clean_text.split())
            if word_count >= 8:
                has_fill = ('___' in clean_text or
                            any(kw in clean_lower for kw in (
                                'name:', 'date:', 'sign:', 'enter')))
                if not has_fill:
                    return None

        # Colon-but-informational: text with colons where the text after
        # the colon is a long explanation (not a fill indicator)
        if ':' in cell_text and words and len(words) > 3 and ch < 65:
            colon_pos = clean_text.rfind(':')
            after_colon = clean_text[colon_pos + 1:].strip()
            after_colon_words = after_colon.split()
            has_fill_indicators = ('___' in clean_text or
                                   any(w.endswith('_') for w in after_colon_words))
            if len(after_colon_words) > 6 and not has_fill_indicators:
                return None

        # No-colon paragraph kill (skip text-heavy cells without colons)
        if ':' not in cell_text and words and len(words) > 3 and ch < 65:
            label_kw = ('name', 'address', 'city', 'state', 'zip', 'phone',
                        'email', 'date', 'contact', 'contractor', 'license')
            is_label = any((k in clean_lower and (len(clean_lower) < 15 or clean_lower.startswith(k)))
                           for k in label_kw)
            is_hero = y0 < 150
            if words:
                text_bottom = max(float(w.get('bottom', 0)) for w in words)
                room_below_check = y1 - text_bottom
            else:
                room_below_check = ch
            is_narrative = (cw > 180 and room_below_check > 8 and ch >= 14)
            if not is_label and not is_hero and not is_narrative:
                return None

        # ---- LABEL+ENTRY POSITIONING ----

        # Try label-at-top with field below (under-label placement)
        if words and clean_text:
            text_top = min(float(w['top']) for w in words)
            text_bottom = max(float(w['bottom']) for w in words)
            room_below = y1 - text_bottom
            label_text_clean = re.sub(r'\s+', ' ', clean_text).strip()

            if not _is_header_like_text(label_text_clean):
                # Determine if we should prefer under-label placement
                bounded_ws = [w for w in words
                              if float(w['x0']) >= x0 - 0.5 and float(w['x1']) <= x1 + 0.5
                              and float(w['top']) >= y0 - 0.5 and float(w['bottom']) <= y1 + 0.5]
                use_ws = bounded_ws if bounded_ws else words
                word_x1 = max(float(w['x1']) for w in use_ws) if use_ws else x0
                empty_right = x1 - word_x1
                right_area = max(0, empty_right) * max(0, ch)
                below_area = max(0, cw) * max(0, room_below)

                is_long = _is_long_data_label(label_text_clean, _LONG_DATA_PATTERNS_EXTENDED)
                prefer_under = room_below >= 8 and (
                    below_area >= right_area * 1.15
                    or (room_below >= 14 and below_area >= right_area * 0.7)
                )
                prefer_beside = empty_right > 35 and (word_x1 - x0) < (cw * 0.85)

                if is_long and room_below >= 8:
                    prefer_under = True
                    prefer_beside = False

                # Under-label placement
                if not _is_header_like_text(label_text_clean) and (text_top - y0) < 8 and room_below > 6 and prefer_under:
                    entry_y0 = text_bottom + 0.5
                    entry_y1 = y1 - 1
                    if entry_y1 - entry_y0 > 8:
                        gap_words = pm.get_words_in_bbox((x0, entry_y0, x1, entry_y1), 0.15)
                        if not gap_words:
                            label_clean = re.sub(r'[^a-zA-Z0-9\s]', '', clean_text).strip()
                            return FieldCandidate(
                                page=page_num,
                                x0=x0 + margin, y0=entry_y0,
                                x1=x1 - margin, y1=entry_y1,
                                field_type=FieldType.TEXT,
                                source='label_entry_below',
                                name_hint=label_clean[:30] if label_clean else f"Cell_{int(x0)}_{int(entry_y0)}",
                                label=label_text_clean,
                                label_position='above',
                                confidence=0.85,
                            )

                # Beside-label placement
                if prefer_beside and not _is_header_like_text(label_text_clean):
                    # Skip cells crossing multiple h-line row boundaries
                    _int_hl = 0
                    _cw = max(1, x1 - x0)
                    for _hl in pm.h_lines:
                        _hy = float(_hl.get('y', _hl.get('top', 0)))
                        _hx0 = float(_hl.get('x0', 0))
                        _hx1 = float(_hl.get('x1', 0))
                        if (y0 + 5 < _hy < y1 - 5
                                and (_hx1 - _hx0) > 30
                                and min(_hx1, x1) - max(_hx0, x0) > _cw * 0.5):
                            _int_hl += 1
                    if _int_hl >= 2:
                        pass  # Spans multiple rows — skip beside-label
                    else:
                        entry_x0 = word_x1 + 4
                        gap_words = pm.get_words_in_bbox((entry_x0, y0, x1, y1), 0.15)
                        if not gap_words:
                            label_clean = re.sub(r'[^a-zA-Z0-9\s]', '', clean_text).strip()
                            return FieldCandidate(
                                page=page_num,
                                x0=entry_x0, y0=y0 + margin,
                                x1=x1 - margin, y1=y1 - margin,
                                field_type=FieldType.TEXT,
                                source='label_entry_cell',
                                name_hint=label_clean[:30] if label_clean else f"Cell_{int(entry_x0)}_{int(y0)}",
                                label=label_text_clean,
                                label_position='left',
                                confidence=0.8,
                            )

        # ---- COORDINATE CALCULATION ----

        field_margin_x = margin
        field_margin_y = max(0.8, margin * 0.6)
        field_x0 = x0 + field_margin_x
        field_y0 = y0 + field_margin_y
        field_x1 = x1 - field_margin_x
        field_y1 = y1 - field_margin_y
        is_label_at_top = False
        current_source = source_type

        # Text shunning: place field beside or below existing text
        if words:
            tx0 = min(float(w['x0']) for w in words)
            ty0 = min(float(w['top']) for w in words)
            tx1 = max(float(w['x1']) for w in words)
            ty1 = max(float(w['bottom']) for w in words)
            room_right = x1 - tx1
            room_below = y1 - ty1
            has_money = '$' in (cell_text or '')

            can_fit_under = room_below > 10
            can_fit_beside = room_right > 20

            if can_fit_under and not has_money:
                field_y0 = max(field_y0, ty1 + 2.0)
                field_x0 = x0 + field_margin_x
                is_label_at_top = True
                current_source = 'structural_box'
            elif can_fit_beside:
                field_x0 = max(field_x0, tx1 + 4.5)
                current_source = 'structural_box'
            else:
                field_y0 = max(field_y0, ty1 + 1.0)
                is_label_at_top = True

            # Data value protection for small numeric cells
            if not is_label_at_top and len(clean_text) < 5 and ch < 18:
                if room_right > 15:
                    field_x0 = max(field_x0, tx1 + 4.0)
                elif tx0 - x0 > 15:
                    field_x1 = min(field_x1, tx0 - 4.0)
                elif cw < 60:
                    return None
        elif source_type in ('strict_grid', 'table', 'table_data_row') and not words:
            current_source = 'structural_box'

        # ---- NAMING ----

        label_text = cell_text or ""
        if not label_text.strip() or label_text.lower().startswith('field'):
            # Search for label above the box
            above_words = pm.get_words_in_bbox((x0, max(0, y0 - 45), x1, y0 + 1), 0.15)
            if above_words:
                meaningful = [w for w in above_words if len(w['text']) > 1 or w['text'].isalnum()]
                if meaningful:
                    lines_map: Dict[float, List] = {}
                    for w in meaningful:
                        ry = round(float(w['top']), 1)
                        lines_map.setdefault(ry, []).append(w)
                    sorted_lines = [lines_map[k] for k in sorted(lines_map.keys())]
                    parts = []
                    for line in sorted_lines:
                        parts.append(" ".join(w['text'] for w in sorted(line, key=lambda w: float(w['x0']))))
                    label_text = " ".join(parts)

        field_name = clean_field_name(label_text.strip())
        field_name = re.sub(r'^[A-Z]\.\s+', '', field_name)

        if not field_name or field_name.lower().startswith('field') or field_name.lower().startswith('cell'):
            col_header = self._get_column_header(x0, x1, headers_map)
            if col_header:
                field_name = clean_field_name(col_header)
            else:
                field_name = "Field"

        # Truncate long names
        if len(field_name.split('_')) > 12:
            field_name = "_".join(field_name.split('_')[:12])

        fmt, fmt_opts = detect_field_format(cell_text, field_name)

        # ---- SIZE POLISH ----

        is_standard_row = (15 < ch < 25) and current_source == 'structural_box' and not is_label_at_top
        is_narrative = ch >= 25
        is_bottom = y1 > (pm.height - 80)

        if is_standard_row:
            h_target = ch - (2 * margin)
            center_y = (y0 + y1) / 2
            field_y0 = center_y - (h_target / 2)
            field_y1 = y1 - margin
        elif is_bottom and not (current_source == 'structural_box' and not words):
            field_y1 = y1 - margin
            field_y0 = max(y0 + margin, field_y1 - 24.0)

        # Boundary clipping
        clip = margin
        field_x0 = max(field_x0, x0 + clip)
        field_x1 = min(field_x1, x1 - clip)
        field_y0 = max(field_y0, y0 + clip)
        field_y1 = min(field_y1, y1 - clip)

        # Text-shunning safety check
        field_words = pm.get_words_in_bbox((field_x0, field_y0, field_x1, field_y1), 0.1)
        if field_words:
            label_ids = {id(w) for w in words} if words else set()
            foreign = [w for w in field_words if id(w) not in label_ids]
            if foreign:
                l_cands = [float(fw['x1']) + 5 for fw in foreign if float(fw['x1']) < field_x0 + 60]
                r_cands = [float(fw['x0']) - 5 for fw in foreign if float(fw['x0']) > field_x1 - 60]
                if l_cands:
                    field_x0 = max(field_x0, max(l_cands))
                if r_cands:
                    field_x1 = min(field_x1, min(r_cands))
                if len(foreign) > 3:
                    return None

        # Minimum dimension validation
        final_w = field_x1 - field_x0
        final_h = field_y1 - field_y0
        if cw < 14 or ch < 6:
            return None
        if final_w < 14 and cw >= 14:
            field_x0 = x0 + 1
            field_x1 = x1 - 1
            final_w = field_x1 - field_x0
        if final_h < 6 and ch >= 6:
            field_y0 = y0 + 0.5
            field_y1 = y1 - 0.5
            final_h = field_y1 - field_y0
        if final_w <= 0 or final_h <= 0:
            return None
        if final_w > 0 and final_h > 0 and final_h / final_w > 6.0:
            return None

        # Determine if this is an image upload box
        # Only standalone photo-placeholder boxes (not table grid cells) should
        # become image upload buttons.  Grid cells that happen to be large and
        # empty are regular data-entry cells.
        is_image_box = False
        if current_source == 'structural_box' and pm.is_in_image_upload_region(x0, y0, x1, y1, 0.3):
            ratio = final_w / final_h if final_h > 0 else 0
            if (final_w > 80 and final_h > 80 and final_w < 600 and final_h < 500
                    and 0.3 < ratio < 3.5):
                # Skip if this cell is part of a visual grid (table cell)
                is_grid = any(
                    abs(x0 - gc[0]) < 3 and abs(y0 - gc[1]) < 3
                    and abs(x1 - gc[2]) < 3 and abs(y1 - gc[3]) < 3
                    for gc in pm.visual_grid_cells
                )
                if not is_grid:
                    is_image_box = True

        return FieldCandidate(
            page=page_num,
            x0=field_x0, y0=field_y0,
            x1=field_x1, y1=field_y1,
            field_type=FieldType.IMAGE_UPLOAD if is_image_box else FieldType.TEXT,
            source=current_source,
            name_hint=field_name,
            label=label_text.strip() if label_text else None,
            label_position='above' if is_label_at_top else None,
            format_hint=fmt,
            format_options=fmt_opts or {},
            is_image_box=is_image_box,
            row_label=row_label,
            confidence=0.7 if current_source == 'structural_box' else 0.75,
        )
