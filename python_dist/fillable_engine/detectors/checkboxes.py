"""
Checkbox detector — ports ALL checkbox detection from v23.

Detection sources:
  1. vector   — square-ish rects on the page
  2. character — pre-detected checkboxes from page_analyzer (existing_checkboxes)
  3. curve    — square-ish curves (rounded checkboxes)
  4. line_box — reconstructed boxes from horizontal + vertical line segments

Each source produces FieldCandidate(field_type=CHECKBOX).
Self-deduplication is performed within this detector only;
cross-detector overlap is handled by the resolver.
"""

from typing import List, Dict, Optional, Set, Tuple
from collections import defaultdict

from ..models import PageModel, FieldCandidate, FieldType
from ..helpers import clean_field_name
from .base import BaseDetector


class CheckboxDetector(BaseDetector):
    """Detect checkbox fields from multiple visual cues."""

    # Size bounds (points)
    # v23 defaults: min=7, max=14; we use slightly wider range
    # to handle larger curve-drawn checkboxes (like DTE's 18pt ones).
    CHECKBOX_MIN_SIZE = 5
    CHECKBOX_MAX_SIZE = 28
    CHECKBOX_SIZE_TOLERANCE = 6  # max abs(width - height)

    # Label search anti-keywords — words that indicate the nearby text
    # is a *field label for something else*, not a checkbox option.
    _ANTI_KEYWORDS = frozenset({
        'apt', 'unit', 'suite', 'city', 'state', 'zip',
        'number', 'amount', 'manufacturer', 'model', 'serial', 'license',
    })

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def detect(self, pages: List[PageModel]) -> List[FieldCandidate]:
        """Run all four sub-detectors, self-deduplicate, and attach labels."""
        # Allow auto-calibration to override class defaults
        if 'checkbox_size_min' in self.settings:
            self.CHECKBOX_MIN_SIZE = self.settings['checkbox_size_min']
        if 'checkbox_size_max' in self.settings:
            self.CHECKBOX_MAX_SIZE = self.settings['checkbox_size_max']

        all_candidates: List[FieldCandidate] = []

        for page in pages:
            page_candidates: List[FieldCandidate] = []

            # Run each sub-detector
            vector_hits = self._detect_vector(page, page.page_num)
            char_hits = self._detect_character(page, page.page_num)
            curve_hits = self._detect_curves(page, page.page_num)
            linebox_hits = self._detect_line_boxes(page, page.page_num)

            # Merge with self-deduplication
            for candidate in vector_hits + char_hits + curve_hits + linebox_hits:
                if not self._is_duplicate(candidate, page_candidates):
                    page_candidates.append(candidate)

            # Assign labels from nearby text
            for candidate in page_candidates:
                label = self._find_label(
                    page, candidate.x0, candidate.y0, candidate.x1, candidate.y1,
                )
                if label:
                    candidate.label = label
                    candidate.name_hint = clean_field_name(label)

            all_candidates.extend(page_candidates)

        return all_candidates

    # -----------------------------------------------------------------------
    # Sub-detector: vector rectangles
    # -----------------------------------------------------------------------

    def _detect_vector(self, page: PageModel, page_num: int) -> List[FieldCandidate]:
        """Detect checkboxes from square-ish rects drawn on the page."""
        if page.is_tally_sheet:
            return []

        candidates: List[FieldCandidate] = []
        for rect in page.rects:
            x0 = rect.get('x0', 0)
            y0 = rect.get('top', rect.get('y0', 0))
            x1 = rect.get('x1', 0)
            y1 = rect.get('bottom', rect.get('y1', 0))

            w = abs(x1 - x0)
            h = abs(y1 - y0)

            # Must be within size bounds
            if not (self.CHECKBOX_MIN_SIZE <= w <= self.CHECKBOX_MAX_SIZE):
                continue
            if not (self.CHECKBOX_MIN_SIZE <= h <= self.CHECKBOX_MAX_SIZE):
                continue
            # Must be roughly square
            if abs(w - h) > self.CHECKBOX_SIZE_TOLERANCE:
                continue

            # Skip rects that ARE visual grid cells (table cells, not checkboxes)
            if self._is_grid_cell(page, x0, y0, x1, y1):
                continue

            # Skip rects that contain text words — these are text
            # containers (e.g. boxes around "Yes" / "No" labels),
            # not standalone checkbox outlines.
            has_text = False
            for word in page.words:
                wx0 = float(word['x0'])
                wy0 = float(word['top'])
                wx1 = float(word['x1'])
                wy1 = float(word['bottom'])
                text = word.get('text', '').strip()
                if len(text) < 2:
                    continue
                # Word must be substantially inside the rect
                if (wx0 >= x0 - 2 and wx1 <= x1 + 2
                        and wy0 >= y0 - 2 and wy1 <= y1 + 2):
                    has_text = True
                    break
            if has_text:
                continue

            candidates.append(FieldCandidate(
                page=page_num,
                x0=x0, y0=y0, x1=x1, y1=y1,
                field_type=FieldType.CHECKBOX,
                source='vector',
                checkbox_width=w,
                checkbox_height=h,
            ))

        return candidates

    # -----------------------------------------------------------------------
    # Sub-detector: character-based (pre-existing checkboxes)
    # -----------------------------------------------------------------------

    def _detect_character(self, page: PageModel, page_num: int) -> List[FieldCandidate]:
        """Create candidates from checkboxes already identified by page_analyzer."""
        candidates: List[FieldCandidate] = []
        for entry in page.existing_checkboxes:
            x0 = entry.get('x0', 0)
            y0 = entry.get('top', entry.get('y0', 0))
            x1 = entry.get('x1', 0)
            y1 = entry.get('bottom', entry.get('y1', 0))

            w = abs(x1 - x0)
            h = abs(y1 - y0)

            candidates.append(FieldCandidate(
                page=page_num,
                x0=x0, y0=y0, x1=x1, y1=y1,
                field_type=FieldType.CHECKBOX,
                source='character',
                checkbox_width=w,
                checkbox_height=h,
            ))

        return candidates

    # -----------------------------------------------------------------------
    # Sub-detector: curves (rounded checkboxes)
    # -----------------------------------------------------------------------

    def _detect_curves(self, page: PageModel, page_num: int) -> List[FieldCandidate]:
        """Detect checkboxes from square-ish curve objects.

        Filtering:
        - Skip curves in header area (y0 < 55pt) — typically logos/decorative elements.
        - Skip filled curves (non_stroking_color set) — usually logo shapes, not checkbox outlines.
        - Skip if many tiny curves cluster at the same Y (logo/decoration pattern).
        """
        candidates: List[FieldCandidate] = []
        for curve in page.curves:
            x0 = curve.get('x0', 0)
            y0 = curve.get('top', curve.get('y0', 0))
            x1 = curve.get('x1', 0)
            y1 = curve.get('bottom', curve.get('y1', 0))

            w = abs(x1 - x0)
            h = abs(y1 - y0)

            if not (self.CHECKBOX_MIN_SIZE <= w <= self.CHECKBOX_MAX_SIZE):
                continue
            if not (self.CHECKBOX_MIN_SIZE <= h <= self.CHECKBOX_MAX_SIZE):
                continue
            if abs(w - h) > self.CHECKBOX_SIZE_TOLERANCE:
                continue

            # Skip curves in the top header area (logos, decorative elements)
            if y0 < 55:
                continue

            # Skip curves inside header bars (section headers, not checkboxes)
            if page.is_in_header_bar(x0, y0, x1, y1):
                continue

            # Skip curves that match visual grid cells (table structure)
            if self._is_grid_cell(page, x0, y0, x1, y1):
                continue

            candidates.append(FieldCandidate(
                page=page_num,
                x0=x0, y0=y0, x1=x1, y1=y1,
                field_type=FieldType.CHECKBOX,
                source='curve',
                checkbox_width=w,
                checkbox_height=h,
            ))

        return candidates

    # -----------------------------------------------------------------------
    # Sub-detector: line-box reconstruction
    # -----------------------------------------------------------------------

    def _detect_line_boxes(self, page: PageModel, page_num: int) -> List[FieldCandidate]:
        """
        Reconstruct checkbox outlines from horizontal + vertical line segments.

        Strategy:
        1. Group horizontal lines by Y position (3 pt tolerance).
        2. Find Y-group pairs whose segment counts are within 50% of each other
           and whose vertical separation is 6-40 pt (checkbox-sized row).
        3. Verify vertical lines exist near the left/right boundaries of each
           segment, confirming a closed box.
        """
        h_lines = page.h_lines
        v_lines = page.v_lines

        if not h_lines or not v_lines:
            return []

        # --- Step 1: group horizontal lines by Y position ----------------
        y_groups: Dict[float, List[Dict]] = defaultdict(list)
        for hl in h_lines:
            y_val = hl.get('y', (hl.get('top', 0) + hl.get('bottom', 0)) / 2)
            # Snap to nearest group within 3 pt
            matched = False
            for key_y in list(y_groups.keys()):
                if abs(y_val - key_y) <= 3:
                    y_groups[key_y].append(hl)
                    matched = True
                    break
            if not matched:
                y_groups[y_val].append(hl)

        sorted_ys = sorted(y_groups.keys())
        if len(sorted_ys) < 2:
            return []

        # --- Step 2: find Y-pairs forming checkbox-height rows -----------
        candidates: List[FieldCandidate] = []

        for i, top_y in enumerate(sorted_ys):
            top_segs = y_groups[top_y]
            for j in range(i + 1, len(sorted_ys)):
                bot_y = sorted_ys[j]
                row_height = bot_y - top_y

                # Row height must be checkbox-sized
                if row_height < self.CHECKBOX_MIN_SIZE or row_height > self.CHECKBOX_MAX_SIZE:
                    if row_height > self.CHECKBOX_MAX_SIZE:
                        break  # sorted, so all further pairs are even taller
                    continue

                bot_segs = y_groups[bot_y]

                # Segment counts should be similar (within 50%)
                cnt_top = len(top_segs)
                cnt_bot = len(bot_segs)
                if cnt_top == 0 or cnt_bot == 0:
                    continue
                ratio = min(cnt_top, cnt_bot) / max(cnt_top, cnt_bot)
                if ratio < 0.5:
                    continue

                # --- Step 3: match segments to form boxes ----------------
                for ts in top_segs:
                    ts_x0 = ts.get('x0', 0)
                    ts_x1 = ts.get('x1', 0)
                    seg_w = ts_x1 - ts_x0

                    # Segment width must be checkbox-sized
                    if seg_w < self.CHECKBOX_MIN_SIZE or seg_w > self.CHECKBOX_MAX_SIZE:
                        continue

                    # Aspect: must be roughly square
                    if abs(seg_w - row_height) > self.CHECKBOX_SIZE_TOLERANCE:
                        continue

                    # Check that a bottom segment covers a similar X range
                    has_bottom = False
                    for bs in bot_segs:
                        bs_x0 = bs.get('x0', 0)
                        bs_x1 = bs.get('x1', 0)
                        if abs(bs_x0 - ts_x0) <= 5 and abs(bs_x1 - ts_x1) <= 5:
                            has_bottom = True
                            break

                    if not has_bottom:
                        continue

                    # Check for vertical lines near left and right edges
                    has_left = False
                    has_right = False
                    for vl in v_lines:
                        vl_x = (vl.get('x0', 0) + vl.get('x1', 0)) / 2
                        vl_top = vl.get('top', 0)
                        vl_bot = vl.get('bottom', 0)

                        # Vertical line must span most of the row height
                        if vl_top > top_y + 3 or vl_bot < bot_y - 3:
                            continue

                        if abs(vl_x - ts_x0) <= 5:
                            has_left = True
                        if abs(vl_x - ts_x1) <= 5:
                            has_right = True

                    if has_left and has_right:
                        # Skip line boxes that match visual grid cells (table cells)
                        if self._is_grid_cell(page, ts_x0, top_y, ts_x1, bot_y):
                            continue

                        candidates.append(FieldCandidate(
                            page=page_num,
                            x0=ts_x0, y0=top_y, x1=ts_x1, y1=bot_y,
                            field_type=FieldType.CHECKBOX,
                            source='line_box',
                            checkbox_width=seg_w,
                            checkbox_height=row_height,
                        ))

        return candidates

    # -----------------------------------------------------------------------
    # Label finder
    # -----------------------------------------------------------------------

    def _find_label(
        self, page: PageModel,
        x0: float, y0: float, x1: float, y1: float,
    ) -> Optional[str]:
        """
        Look for nearby text to use as a checkbox label.

        Search order:
          1. RIGHT — closest word on the same Y line, within 18 pt to the right.
          2. ABOVE — word 5-30 pt above, horizontally aligned.
          3. BELOW — word below the checkbox.
        """
        cb_cx = (x0 + x1) / 2
        cb_cy = (y0 + y1) / 2

        best_label: Optional[str] = None
        best_dist: float = float('inf')

        for word in page.words:
            wx0 = word.get('x0', 0)
            wy0 = word.get('top', 0)
            wx1 = word.get('x1', 0)
            wy1 = word.get('bottom', 0)
            text = word.get('text', '').strip()

            if not text:
                continue

            w_cy = (wy0 + wy1) / 2

            # ----- 1. RIGHT: same Y line, within 18 pt to the right -----
            if abs(w_cy - cb_cy) <= 6 and wx0 >= x1 and (wx0 - x1) <= 18:
                dist = wx0 - x1
                if dist < best_dist:
                    label = self._collect_line_text(page, word)
                    if not self._is_anti_keyword(label):
                        best_label = label
                        best_dist = dist

            # ----- 2. ABOVE: 5-30 pt above, horizontally aligned --------
            elif wy1 <= y0 and (y0 - wy1) >= 5 and (y0 - wy1) <= 30:
                # Horizontally aligned: word must overlap the checkbox X range
                if wx1 >= x0 - 5 and wx0 <= x1 + 5:
                    dist = y0 - wy1 + 100  # bias so RIGHT wins if both exist
                    if dist < best_dist:
                        label = text
                        if not self._is_anti_keyword(label):
                            best_label = label
                            best_dist = dist

            # ----- 3. BELOW: below the checkbox --------------------------
            elif wy0 >= y1 and (wy0 - y1) <= 20:
                if wx1 >= x0 - 5 and wx0 <= x1 + 5:
                    dist = (wy0 - y1) + 200  # bias so RIGHT and ABOVE win
                    if dist < best_dist:
                        label = text
                        if not self._is_anti_keyword(label):
                            best_label = label
                            best_dist = dist

        return best_label

    def _collect_line_text(self, page: PageModel, start_word: Dict) -> str:
        """
        Collect text from start_word and subsequent words on the same line
        (to capture multi-word labels like "Owner Occupied").
        """
        sy0 = start_word.get('top', 0)
        sy1 = start_word.get('bottom', 0)
        s_cy = (sy0 + sy1) / 2

        # Gather words on the same Y line that follow start_word
        line_words = []
        for w in page.words:
            w_cy = (w.get('top', 0) + w.get('bottom', 0)) / 2
            if abs(w_cy - s_cy) <= 4 and w.get('x0', 0) >= start_word.get('x0', 0):
                line_words.append(w)

        # Sort left-to-right
        line_words.sort(key=lambda w: w.get('x0', 0))

        # Collect contiguous words (gap <= 12 pt)
        parts = []
        prev_x1 = start_word.get('x0', 0)
        for w in line_words:
            gap = w.get('x0', 0) - prev_x1
            if parts and gap > 12:
                break
            parts.append(w.get('text', ''))
            prev_x1 = w.get('x1', 0)

        return ' '.join(parts).strip()

    def _is_anti_keyword(self, label: str) -> bool:
        """
        Check if a label consists of or starts with an anti-keyword,
        unless the keyword appears as part of an allowed compound word.
        """
        if not label:
            return False

        lower = label.lower().strip()

        for kw in self._ANTI_KEYWORDS:
            if kw not in lower:
                continue

            # Allow 'city' inside 'electricity'
            if kw == 'city' and 'electricity' in lower:
                continue
            # Allow 'unit' inside compound words like 'multi-family'
            if kw == 'unit' and 'multi' in lower:
                continue

            # If the label IS the keyword (or very short), reject it
            words = lower.split()
            if words and words[0] == kw:
                return True

        return False

    # -----------------------------------------------------------------------
    # Grid cell exclusion
    # -----------------------------------------------------------------------

    @staticmethod
    def _is_grid_cell(page: PageModel, x0: float, y0: float,
                      x1: float, y1: float, tol: float = 5.0) -> bool:
        """Return True if bbox closely matches a visual_grid_cell (table cell).

        A rect that aligns with a detected table grid cell is almost certainly
        part of the table structure, not a standalone checkbox.
        """
        for gx0, gy0, gx1, gy1 in page.visual_grid_cells:
            if (abs(x0 - gx0) <= tol and abs(y0 - gy0) <= tol and
                    abs(x1 - gx1) <= tol and abs(y1 - gy1) <= tol):
                return True
        return False

    # -----------------------------------------------------------------------
    # Color classification
    # -----------------------------------------------------------------------

    @staticmethod
    def _is_colored_fill(fill_color) -> bool:
        """Return True if fill color is *chromatic* (not white/gray).

        Checkboxes can have light gray backgrounds, so we only reject fills
        with clear hue (like orange, blue, green).

        Handles tuples of length 1 (gray), 3 (RGB), 4 (CMYK).
        """
        if not fill_color:
            return False

        try:
            vals = tuple(float(v) for v in fill_color)
        except (TypeError, ValueError):
            return False

        if len(vals) == 1:
            # Grayscale — never "colored"
            return False
        elif len(vals) == 3:
            # RGB — colored if channels differ significantly
            r, g, b = vals
            spread = max(vals) - min(vals)
            return spread > 0.25 and max(vals) > 0.15
        elif len(vals) == 4:
            # CMYK — colored if C, M, or Y has significant ink
            c, m, y, k = vals
            return max(c, m, y) > 0.25
        return False

    # -----------------------------------------------------------------------
    # Deduplication
    # -----------------------------------------------------------------------

    @staticmethod
    def _is_duplicate(candidate: FieldCandidate, existing: List[FieldCandidate]) -> bool:
        """Check if a checkbox at approximately the same position already exists.

        Uses center-distance matching so that differently-sized detections
        of the same visual checkbox (e.g. VECTOR wider rect vs CHAR glyph)
        are still caught as duplicates.
        """
        cx = (candidate.x0 + candidate.x1) / 2
        cy = (candidate.y0 + candidate.y1) / 2
        for ex in existing:
            ex_cx = (ex.x0 + ex.x1) / 2
            ex_cy = (ex.y0 + ex.y1) / 2
            if abs(cx - ex_cx) <= 18 and abs(cy - ex_cy) <= 10:
                return True
        return False
