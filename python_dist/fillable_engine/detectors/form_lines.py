"""
Form-line detector — detects horizontal vector lines that indicate form fill areas.

Ported from v23's _detect_form_lines() (lines 14601-15384).

Purpose:
    Detects horizontal lines (vector lines from pdfplumber, NOT text underscores)
    that indicate fill-in areas.  Fields are positioned with their BOTTOM edge
    sitting ON the line.

Detection pipeline per page:
    1. Collect horizontal lines (from page.lines and thin rects) and vertical lines.
    2. Detect inline labels (city, state, zip, etc.) and pre-split lines at keywords.
    3. Deduplicate: pre-split segments take precedence over unsplit lines.
    4. For each line/segment:
       Phase 1 - Find label (left / above / below)
       Phase 2 - Find right boundary (collision with next text)
       Phase 3 - Create FieldCandidate (validate dimensions)

Contract (v24 modular):
    * Receives read-only PageModel objects.
    * Returns List[FieldCandidate] -- never mutates shared state.
    * Does NOT check for overlaps with other detectors (the resolver handles that).
"""

import re
from typing import List, Dict, Optional, Tuple, Set

from ..models import PageModel, FieldCandidate, FieldType
from ..helpers import clean_field_name, detect_field_format
from .base import BaseDetector


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_INLINE_KEYWORDS = frozenset([
    'city', 'state', 'zip', 'phone', 'fax', 'email',
    'county', 'country', 'date',
])

# Labels that indicate instructional/descriptive text rather than field labels.
# Lines near these labels are skipped.
_INSTRUCTION_KILLS = frozenset([
    'service territory', 'qualifying measure', 'consumer promotion',
    'meeting frequency', 'participate', 'anticipate', 'percentage',
    'please estimate', 'contractors purchasing', 'territory',
    'additional documentation', 'documentation to submit',
    'please list', 'please detail', 'interested in being',
    'electronic signature', 'submit', 'mail to',
])

# Filler / hint words that should NOT terminate a field (collision detection)
_HINT_WORDS = frozenset([
    'print', 'sign', 'here', 'please', 'optional',
    'required', 'if', 'applicable',
])

# Filler words used during label scoring (lower priority)
_FILLER_WORDS = frozenset({
    'as', 'that', 'apply', 'check', 'all', 'select', 'one', 'shown',
    'on', 'your', 'be', 'must', 'if', 'yes', 'no', 'to',
})

# Words that begin sentences (not field labels)
_SENTENCE_PREFIXES = (
    'the ', 'a ', 'an ', 'for ', 'to ', 'by ', 'in ', 'on ',
)

# Thick-line signature keywords — allow thick lines (>3pt) when nearby
_SIG_KEYWORDS = frozenset([
    'signature', 'date', 'name', 'title', 'inspector',
    'contractor', 'authorized', 'technician',
])


class FormLineDetector(BaseDetector):
    """Detect horizontal vector lines that indicate form fill areas."""

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def detect(self, pages: List[PageModel]) -> List[FieldCandidate]:
        candidates: List[FieldCandidate] = []
        for page_model in pages:
            candidates.extend(self._detect_page(page_model))
        return candidates

    # ------------------------------------------------------------------
    # Per-page detection
    # ------------------------------------------------------------------

    def _detect_page(self, page: PageModel) -> List[FieldCandidate]:
        results: List[FieldCandidate] = []
        base_field_height = int(self.settings.get('field_height', 14))

        # --- Step 1: Collect horizontal and vertical lines -----------------
        h_lines, v_lines = self._collect_lines(page)
        if not h_lines:
            return results

        # --- Step 2: Inline label detection & line splitting ---------------
        split_lines = self._split_lines_at_inline_labels(page, h_lines)

        # --- Step 3: Deduplication (pre-split takes precedence) ------------
        deduped = self._deduplicate_lines(split_lines)

        # --- Step 3.5: Detect structural multi-segment borders -----------
        # If 4+ segments at the same Y span >60% of page width, they are
        # table/header borders, not form fill lines.
        structural_ys = self._find_structural_border_ys(deduped, page.width, v_lines)

        # --- Step 3.6: Build sorted Y list for adaptive height calc ------
        # Collect all anchor Y values (line Ys, word bottoms) so we can
        # calculate the space above each form line and expand the field
        # height to fill it (for spaced-out line-based forms).
        all_line_ys = sorted(set(ld[1] for ld in deduped))
        word_bottoms = sorted(set(
            float(w['bottom']) for w in (page.words or [])
        ))

        # --- Step 4: Process each line/segment ----------------------------
        for line_data in deduped:
            x0, y, x1, lw, inline_label = line_data

            # --- Adaptive field height ---
            # Find the nearest content above this line (another form line
            # or the bottom of a text line).  If the gap is large, expand
            # the field height upward so it fills toward the content above,
            # leaving a small pad.  Capped at 30pt to avoid oversized fields.
            field_height = base_field_height
            nearest_above = None
            for ly in reversed(all_line_ys):
                if ly < y - 4:
                    nearest_above = ly
                    break
            for wb in reversed(word_bottoms):
                if wb < y - 4:
                    if nearest_above is None or wb > nearest_above:
                        nearest_above = wb
                    break
            if nearest_above is not None:
                gap = y - nearest_above
                if gap > 18:
                    # Expand height, leave 4pt pad above, cap at 30pt
                    field_height = min(int(gap - 4), 30)

            # Skip lines inside header bars
            if self._is_inside_header_bar(page, x0, y, x1):
                continue

            # Skip structural multi-segment borders (table/header dividers)
            if any(abs(y - sy) < 3 for sy in structural_ys):
                continue

            # Skip lines that are table borders (inside visual grid cells)
            if self._is_table_border(page, x0, y, x1):
                continue

            # Skip lines that sit inside a visual grid cell (internal dividers)
            if self._is_inside_grid_cell(page, x0, y, x1):
                continue

            # --- Phase 1: Find label ---
            label, label_is_colon = self._find_label(
                page, x0, y, x1, inline_label,
            )

            # Apply label-based filters
            if label:
                if self._is_instruction_label(label):
                    continue
                if self._is_sentence_label(label, label_is_colon):
                    continue

            # --- Pre-split shortcut ---
            if inline_label is not None:
                name_hint = clean_field_name(label) if label else 'Line_Field'
                fmt_hint, fmt_opts = detect_field_format(None, label)
                width = x1 - x0
                if width < 20:
                    continue
                results.append(FieldCandidate(
                    page=page.page_num,
                    x0=x0,
                    y0=y - field_height,
                    x1=x1,
                    y1=y,
                    field_type=FieldType.TEXT,
                    source='form_line_presplit',
                    name_hint=name_hint,
                    label=label,
                    format_hint=fmt_hint,
                    format_options=fmt_opts if fmt_opts else None,
                    source_line_x0=x0,
                    source_line_x1=x1,
                ))
                continue

            # --- Segment iteration (handles multi-field lines) ---
            seg_results = self._process_line_segments(
                page, x0, y, x1, label, field_height,
            )
            results.extend(seg_results)

        return results

    # ==================================================================
    # Step 1: Line collection
    # ==================================================================

    def _collect_lines(
        self, page: PageModel,
    ) -> Tuple[List[Tuple[float, float, float, float]], List[Tuple[float, float, float]]]:
        """
        Gather horizontal lines and vertical lines from the page.

        Horizontal lines: (x0, y, x1, linewidth)
        Vertical lines:   (x, top, bottom)
        """
        h_lines: List[Tuple[float, float, float, float]] = []
        v_lines: List[Tuple[float, float, float]] = []
        words = page.words

        for line in page.lines:
            lx0 = float(line.get('x0', 0))
            lx1 = float(line.get('x1', 0))
            ltop = float(line.get('top', 0))
            lbottom = float(line.get('bottom', 0))
            line_width = float(line.get('linewidth', line.get('width', 1)))

            # Vertical line
            if abs(lx0 - lx1) < 2:
                v_lines.append((lx0, ltop, lbottom))
                continue

            # Horizontal line (same Y within 2pt)
            if abs(ltop - lbottom) < 2:
                line_length = abs(lx1 - lx0)
                y_pos = (ltop + lbottom) / 2

                if line_length < 15:
                    continue

                if line_width > 3:
                    # Allow thick lines near signature-related words
                    if line_length > 60 and self._has_nearby_sig_words(
                        words, min(lx0, lx1), max(lx0, lx1), y_pos,
                    ):
                        pass  # allowed
                    else:
                        continue

                h_lines.append((min(lx0, lx1), y_pos, max(lx0, lx1), line_width))

        # Also check rects (lines often drawn as thin rectangles)
        for rect in page.rects:
            rw = float(rect.get('width', 0))
            rh = float(rect.get('height', 0))

            # Vertical thin rect -> vertical line
            if rw < 3 and rh > 10:
                rx0 = float(rect.get('x0', 0))
                rtop = float(rect.get('top', 0))
                rbot = float(rect.get('bottom', 0))
                v_lines.append((rx0, rtop, rbot))

            # Horizontal thin rect -> horizontal line
            if rh < 3 and rw > 15:
                rx0 = float(rect.get('x0', 0))
                rx1 = float(rect.get('x1', 0))
                rtop = float(rect.get('top', 0))
                rbot = float(rect.get('bottom', 0))
                y_pos = (rtop + rbot) / 2
                h_lines.append((min(rx0, rx1), y_pos, max(rx0, rx1), rh))

        return h_lines, v_lines

    @staticmethod
    def _has_nearby_sig_words(
        words: List[Dict], x0: float, x1: float, y: float,
    ) -> bool:
        """Check whether signature-related words appear near a thick line."""
        for w in words:
            wy = float(w['top'])
            if abs(wy - y) > 18:
                continue
            wx0 = float(w['x0'])
            wx1 = float(w['x1'])
            if wx1 < x0 - 10 or wx0 > x1 + 10:
                continue
            if any(k in w['text'].lower() for k in _SIG_KEYWORDS):
                return True
        return False

    # ==================================================================
    # Step 2: Inline label detection & line splitting
    # ==================================================================

    def _split_lines_at_inline_labels(
        self,
        page: PageModel,
        h_lines: List[Tuple[float, float, float, float]],
    ) -> List[Tuple[float, float, float, float, Optional[str]]]:
        """
        For each horizontal line, search for inline keywords (city, state, etc.)
        near it. If found, split the line into segments at each keyword.

        Returns list of (x0, y, x1, linewidth, label_or_None).
        label_or_None is a str for pre-split segments, None for unsplit lines.
        """
        all_split: List[Tuple[float, float, float, float, Optional[str]]] = []
        words = page.words

        for x0, y, x1, lw in h_lines:
            labels_on_line = self._find_inline_labels(words, x0, y, x1)

            # If the only inline keyword sits at the line start, it is a
            # below-line label FOR the field, not a mid-line split point.
            # Drop it so the line stays unsplit.
            if (len(labels_on_line) == 1
                    and abs(labels_on_line[0]['x0'] - x0) < 5):
                labels_on_line = []

            if labels_on_line:
                # --- Leading segment (before first inline keyword) ---
                first_label = labels_on_line[0]
                if first_label['x0'] > x0 + 50:
                    leading_x1 = first_label['x0'] - 5
                    if leading_x1 - x0 > 30:
                        all_split.append((x0, y, leading_x1, lw, None))

                # --- Keyword segments ---
                for i, lbl in enumerate(labels_on_line):
                    # Below-line labels (label top is below the line):
                    # field spans from label x0.  Inline labels: field
                    # starts after the label text.
                    if lbl['y'] > y + 2:
                        field_x0 = lbl['x0']
                    else:
                        field_x0 = lbl['x1'] + 3
                    if i < len(labels_on_line) - 1:
                        field_x1 = labels_on_line[i + 1]['x0'] - 5
                    else:
                        field_x1 = x1

                    if field_x1 - field_x0 > 20:
                        all_split.append((
                            field_x0, y, field_x1, lw,
                            lbl['text'].capitalize(),
                        ))
            else:
                # No inline keywords — keep full line
                all_split.append((x0, y, x1, lw, None))

        return all_split

    @staticmethod
    def _find_inline_labels(
        words: List[Dict], x0: float, y: float, x1: float,
    ) -> List[Dict]:
        """
        Find inline keyword labels (city, state, zip, ...) that sit on or
        near (within 8pt) a horizontal line.
        """
        labels: List[Dict] = []

        for word in words:
            word_text = word['text'].strip().lower().replace(':', '')
            if word_text not in _INLINE_KEYWORDS:
                continue

            word_y = float(word['top'])
            word_bottom = float(word['bottom'])
            word_x0 = float(word['x0'])
            word_x1 = float(word['x1'])

            # Must be within 8pt of the line vertically
            if abs(y - word_y) > 8 and abs(y - word_bottom) > 8:
                continue

            # Must be within the line's X span (with tolerance)
            if word_x1 < x0 - 20 or word_x0 > x1 + 20:
                continue

            # Build the full label by finding preceding/following words
            full_label = word['text'].strip()
            preceding_x0 = word_x0
            following_x1 = word_x1

            # Look for preceding words (e.g. "Installation Completion" before "Date")
            search_x0 = word_x0
            while True:
                found_prev = None
                for prev_word in words:
                    px0 = float(prev_word['x0'])
                    px1 = float(prev_word['x1'])
                    py = float(prev_word['top'])
                    if abs(py - word_y) > 3:
                        continue
                    gap = search_x0 - px1
                    if 0 <= gap < 20:
                        prev_text = prev_word['text'].strip()
                        if prev_text.lower().replace(':', '') not in _INLINE_KEYWORDS:
                            found_prev = prev_word
                            break
                if found_prev is None:
                    break
                prev_text = found_prev['text'].strip()
                full_label = prev_text + ' ' + full_label
                preceding_x0 = float(found_prev['x0'])
                search_x0 = preceding_x0

            # Look for one following word (e.g. "Address" after "Email")
            for next_word in words:
                if next_word is word:
                    continue
                nx0 = float(next_word['x0'])
                nx1 = float(next_word['x1'])
                ny = float(next_word['top'])
                if abs(ny - word_y) > 3:
                    continue
                gap = nx0 - following_x1
                if 0 <= gap < 20:
                    next_text = next_word['text'].strip()
                    if next_text.lower().replace(':', '') not in _INLINE_KEYWORDS:
                        full_label = full_label + ' ' + next_text
                        following_x1 = nx1
                        break

            label_clean = full_label.rstrip(':')

            labels.append({
                'text': label_clean,
                'x0': preceding_x0,
                'x1': following_x1,
                'y': word_y,
            })

        labels.sort(key=lambda l: l['x0'])
        return labels

    # ==================================================================
    # Step 3: Deduplication
    # ==================================================================

    @staticmethod
    def _deduplicate_lines(
        split_lines: List[Tuple[float, float, float, float, Optional[str]]],
    ) -> List[Tuple[float, float, float, float, Optional[str]]]:
        """
        Pre-split segments (label is not None) take precedence.
        Remove unsplit lines (label is None) that overlap a pre-split segment.
        """
        # Collect positions of pre-split segments (use floats to avoid
        # rounding-induced false overlaps with leading segments)
        presplit_positions: List[Tuple[float, float, float]] = []
        for x0, y, x1, lw, label in split_lines:
            if label is not None:
                presplit_positions.append((y, x0, x1))

        deduped: List[Tuple[float, float, float, float, Optional[str]]] = []
        for line_data in split_lines:
            x0, y, x1, lw, label = line_data

            if label is None:
                # Check overlap with any pre-split segment
                overlaps = False
                for py, px0, px1 in presplit_positions:
                    if abs(y - py) < 5:
                        if not (x1 <= px0 - 5 or x0 >= px1 + 5):
                            overlaps = True
                            break
                if overlaps:
                    continue

            deduped.append(line_data)

        return deduped

    # ==================================================================
    # Step 4 helpers
    # ==================================================================

    @staticmethod
    def _is_inside_header_bar(
        page: PageModel, x0: float, y: float, x1: float,
    ) -> bool:
        """Check if a line sits inside a header bar (should be skipped)."""
        for bar in page.header_bars:
            if bar['y0'] - 2 <= y <= bar['y1'] + 2:
                if not (x1 < bar['x0'] or x0 > bar['x1']):
                    return True
        return False

    @staticmethod
    def _is_table_border(
        page: PageModel, x0: float, y: float, x1: float,
    ) -> bool:
        """Check if a line is a table border (runs along the edge of grid cells).

        A line is considered a table border if it closely matches the top or
        bottom edge of visual grid cells.  This prevents form-line detection
        from firing on every row divider inside a pdfplumber table.

        Two criteria (either triggers True):
        1. Line matches 2+ grid cell edges (multi-column tables).
        2. Line matches 1 wide cell edge (single-row spanning cell, e.g. a
           signature row at the bottom of a table).
        """
        if not page.visual_grid_cells:
            return False

        matches = 0
        has_wide_cell_match = False
        for cx0, cy0, cx1, cy1 in page.visual_grid_cells:
            # Check if the line's Y aligns with a cell top or bottom (within 3pt)
            y_matches_top = abs(y - cy0) < 3
            y_matches_bot = abs(y - cy1) < 3
            if y_matches_top or y_matches_bot:
                # Check horizontal overlap
                ov = min(x1, cx1) - max(x0, cx0)
                if ov > 0:
                    matches += 1
                    cell_w = cx1 - cx0
                    if cell_w > 200:
                        has_wide_cell_match = True
        # 2+ cell edges matched, or 1 wide cell edge matched
        return matches >= 2 or has_wide_cell_match

    @staticmethod
    def _is_inside_grid_cell(
        page: PageModel, x0: float, y: float, x1: float,
    ) -> bool:
        """Check if a line sits inside a visual grid cell (internal row divider).

        Only considers lines that span >70% of the cell width as internal
        dividers.  Shorter lines inside cells are typically form fill indicators
        (e.g. signature/date lines inside a wide table row) and should be kept.
        """
        if not page.visual_grid_cells:
            return False
        line_len = x1 - x0
        for cx0, cy0, cx1, cy1 in page.visual_grid_cells:
            cell_w = cx1 - cx0
            # Line must be inside cell (not on its edges)
            if x0 >= cx0 - 2 and x1 <= cx1 + 2 and cy0 + 3 < y < cy1 - 3:
                # Only treat as internal divider if line spans most of the cell
                if cell_w > 0 and line_len > cell_w * 0.7:
                    return True
        return False

    @staticmethod
    def _find_structural_border_ys(
        lines: List[Tuple[float, float, float, float, Optional[str]]],
        page_width: float,
        v_lines: List[Tuple[float, float, float]] = None,
    ) -> List[float]:
        """Identify Y positions with 4+ segments spanning >60% of page width.

        These are structural borders (table column dividers, header separators)
        rather than form fill lines.  To avoid filtering out multi-column form
        underlines (e.g. Name: Last / First / Middle / Maiden), require that
        vertical lines exist near the segment boundaries — a true grid/table
        border is accompanied by vertical strokes at column edges.
        """
        from collections import defaultdict

        by_y: Dict[int, List[Tuple[float, float]]] = defaultdict(list)
        for x0, y, x1, lw, label in lines:
            y_bucket = round(y)
            by_y[y_bucket].append((x0, x1))

        if v_lines is None:
            v_lines = []

        structural_ys: List[float] = []
        for y_bucket, segments in by_y.items():
            if len(segments) < 4:
                continue
            # Calculate total span coverage
            total_len = sum(x1 - x0 for x0, x1 in segments)
            if total_len <= page_width * 0.6:
                continue

            # Require at least 2 vertical lines near segment endpoints
            # at this Y.  This ensures we're looking at a grid/table border
            # rather than multi-column form underlines.
            v_hits = 0
            segment_edges = set()
            for sx0, sx1 in segments:
                segment_edges.add(sx0)
                segment_edges.add(sx1)

            for vx, vtop, vbot in v_lines:
                # Vertical line must cross this Y (within 5pt)
                if vtop > y_bucket + 5 or vbot < y_bucket - 5:
                    continue
                # And its x must be near a segment endpoint
                for edge in segment_edges:
                    if abs(vx - edge) < 5:
                        v_hits += 1
                        break

            if v_hits >= 2:
                structural_ys.append(float(y_bucket))

        return structural_ys

    @staticmethod
    def _is_instruction_label(label: str) -> bool:
        """Return True if the label matches instructional phrases to skip."""
        lower = label.lower()
        return any(k in lower for k in _INSTRUCTION_KILLS)

    @staticmethod
    def _is_sentence_label(label: str, label_is_colon: bool) -> bool:
        """
        Return True if the label looks like a sentence rather than a field label.
        Colon-ending labels and question-ending labels are exempt
        (strong field-label signal — they explicitly ask for input).
        """
        import re

        label_lower = label.lower().strip()
        label_stripped = label.strip()
        has_colon = (
            label_stripped.endswith(':')
            or label_stripped.endswith('):')
            or label_is_colon
        )
        # Questions ending with '?' are form-field labels asking for input
        # (e.g. "Number of children you hope to serve?")
        has_question = label_stripped.endswith('?')

        # Colon or question mark = strong field-label signal → never filter
        if has_colon or has_question:
            return False

        # Strip parenthetical notes for word counting — table column
        # headers frequently include clarifications like "(include major)"
        # that inflate the word count without making the label a sentence.
        label_for_count = re.sub(r'\([^)]*\)', '', label).strip()
        word_count = len(label_for_count.split())

        # Too many words without a colon/question ending
        if word_count > 5:
            return True

        # Starts with an article / preposition (sentence-like)
        if label_lower.startswith(_SENTENCE_PREFIXES):
            return True

        # Compound phrase with "and" (>5 words, no colon/question).
        # Short phrases like "Name and address" (4 words) are legitimate
        # field labels (table column headers), so require >5 words.
        if ' and ' in label_lower and word_count > 5:
            return True

        return False

    # ------------------------------------------------------------------
    # Phase 1: Label finding
    # ------------------------------------------------------------------

    def _find_label(
        self,
        page: PageModel,
        x0: float,
        y: float,
        x1: float,
        inline_label: Optional[str],
    ) -> Tuple[Optional[str], bool]:
        """
        Find the best label for a line. Checks inline, left, above, below.

        Returns (label_text, is_colon).
        """
        if inline_label:
            return inline_label, inline_label.endswith(':')

        words = page.words

        # Gather candidates from each direction
        candidates = []

        left_res = self._find_label_left(words, x0, y)
        if left_res:
            candidates.append(('left', left_res))

        above_res = self._find_label_above(words, page.lines, x0, y)
        if above_res:
            candidates.append(('above', above_res))

        below_res = self._find_label_below(words, page.lines, x0, y)
        if below_res:
            candidates.append(('below', below_res))

        if not candidates:
            return None, False

        # Score and select best
        scored = []
        for direction, res in candidates:
            text, is_colon, score = res[0], res[1], res[2]
            adjusted_score = score
            # Bonus for 'left' within reasonable distance
            if direction == 'left' and score < 100:
                adjusted_score -= 50
            scored.append((adjusted_score, direction, res))

        scored.sort(key=lambda s: s[0])
        best_score, best_dir, best_res = scored[0]
        return best_res[0], best_res[1]

    # ------------------------------------------------------------------
    # _find_label_left  (ported from v23 _find_nearby_text direction='left')
    # ------------------------------------------------------------------

    def _find_label_left(
        self, words: List[Dict], x: float, y: float, max_dist: float = 250.0,
    ) -> Optional[Tuple[str, bool, float, float, float]]:
        """
        Find label to the left of the line start.
        Returns (text, is_colon, score, phrase_x0, phrase_x1) or None.
        """
        candidates = []

        for word in words:
            wx0 = float(word['x0'])
            wx1 = float(word['x1'])
            wy = float(word['top'])

            # Must start before the line (or very close)
            if wx0 > x + 5:
                continue

            # Must be at or above the line (not below)
            if wy > y + 3:
                continue

            # Same visual line (within 14pt above)
            y_diff = y - wy
            if y_diff > 14 or y_diff < -3:
                continue

            text = word['text'].strip()
            is_colon = text.endswith(':')

            # Filter filler / instructions
            text_clean = text.lower().replace(':', '').strip('().,')
            is_instruction = (
                text.startswith('(') or text.endswith(')')
                or text_clean in _FILLER_WORDS
                or 'apply' in text_clean or 'check' in text_clean
            )
            is_symbol = (len(text) <= 1 and not text.isalnum())

            dist = x - wx1
            score = (
                dist
                + (0 if is_colon else 50)
                + (300 if is_instruction else 0)
                + (500 if is_symbol else 0)
            )
            candidates.append((score, text, is_colon, wy, wx0, wx1))

        if not candidates:
            return None

        candidates.sort()
        best = candidates[0]
        best_text, best_is_colon, best_top = best[1], best[2], best[3]
        b_x0, b_x1 = best[4], best[5]
        best_word = {'text': best_text, 'x0': b_x0, 'x1': b_x1, 'top': best_top}

        # Aggregate words into a full phrase
        phrase_words = [best_word]
        gap_limit = 15

        # Extend LEFT
        for _ in range(10):
            leftmost = phrase_words[0]
            target_x0 = float(leftmost['x0'])
            target_y = float(leftmost['top'])

            found_prev = None
            for word in words:
                if word in phrase_words:
                    continue
                wx1 = float(word['x1'])
                wy = float(word['top'])
                if abs(wy - target_y) > 2.0:
                    continue
                gap = target_x0 - wx1
                if 0 <= gap < gap_limit:
                    found_prev = word
                    break

            if found_prev:
                phrase_words.insert(0, found_prev)
            else:
                break

        # Extend RIGHT (up to the line start)
        for _ in range(5):
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
                if abs(wy - target_y) > 2.0:
                    continue
                gap = wx0 - target_x1
                if 0 <= gap < gap_limit:
                    if wx0 < x + 5:
                        if gap < best_gap:
                            best_gap = gap
                            found_next = word

            if found_next:
                phrase_words.append(found_next)
            else:
                break

        full_text = ' '.join(w['text'].strip() for w in phrase_words)

        # Deduplicate adjacent repeated words
        word_list = full_text.split()
        clean = []
        for wd in word_list:
            if not clean or wd != clean[-1]:
                clean.append(wd)
        label_clean = ' '.join(clean).strip().rstrip(':')

        phrase_x0 = float(phrase_words[0]['x0'])
        phrase_x1 = float(phrase_words[-1]['x1'])
        return (label_clean, best_is_colon, best[0], phrase_x0, phrase_x1)

    # ------------------------------------------------------------------
    # _find_label_above  (ported from v23 _find_nearby_text direction='above')
    # ------------------------------------------------------------------

    def _find_label_above(
        self,
        words: List[Dict],
        page_lines: List[Dict],
        x: float,
        y: float,
        max_dist: float = 25.0,
    ) -> Optional[Tuple[str, bool, float, float, float]]:
        """
        Find label above the line.
        Returns (text, is_colon, score, label_x0, label_x1) or None.
        """
        search_max = max(max_dist, 30)
        candidates = []

        for word in words:
            wx0 = float(word['x0'])
            wx1 = float(word['x1'])
            w_bottom = float(word['bottom'])
            w_top = float(word['top'])

            y_dist = y - w_bottom
            if y_dist < -2 or y_dist > search_max:
                continue

            if wx1 < x - 20 or wx0 > x + 150:
                continue

            text = word['text'].strip()
            is_colon = text.endswith(':')

            text_clean = text.lower().strip('().,')
            is_instruction = (
                text.startswith('(') or text.endswith(')')
                or text_clean in _FILLER_WORDS
                or 'apply' in text_clean or 'check' in text_clean
            )

            score = y_dist + (0 if is_colon else 50) + (300 if is_instruction else 0) + 300
            candidates.append((score, text, is_colon, w_top, wx0, wx1))

        if not candidates:
            return None

        candidates.sort()
        best = candidates[0]
        best_text, best_is_colon, best_top = best[1], best[2], best[3]
        b_x0, b_x1 = best[4], best[5]

        # Aggregate multi-line label above
        sorted_lines = sorted(page_lines, key=lambda l: float(l.get('top', 0)))
        label_lines: Dict[float, List[Tuple[float, str]]] = {}

        for word in words:
            wt = float(word['top'])
            if abs(wt - best_top) < 3 or 3 <= (best_top - wt) < 12:
                # Boundary check
                has_boundary = False
                if wt < best_top - 3:
                    for l in sorted_lines:
                        l_top = float(l.get('top', 0))
                        if wt + 1 < l_top < best_top - 1:
                            if float(l.get('x0', 0)) <= x + 50 and float(l.get('x1', 0)) >= x - 10:
                                has_boundary = True
                                break
                if has_boundary:
                    continue

                wx0 = float(word['x0'])
                wx1 = float(word['x1'])
                if wx1 >= x - 20 and wx0 <= x + 150:
                    bucket = round(wt, 1)
                    if bucket not in label_lines:
                        label_lines[bucket] = []
                    label_lines[bucket].append((wx0, word['text']))

        # Join lines top to bottom
        sorted_keys = sorted(label_lines.keys(), reverse=True)
        lines_to_keep: List[str] = []
        for t in sorted_keys:
            line_words_list = sorted(label_lines[t])
            line_text = ' '.join(lw[1] for lw in line_words_list)
            lines_to_keep.insert(0, line_text)

            lt_clean = line_text.strip().lower()
            if lt_clean.endswith('?') or lt_clean.startswith('if ') or lt_clean.startswith('if,'):
                break
            if len(lines_to_keep) > 1 and ':' in line_text:
                break
            if len(lines_to_keep) >= 3:
                break

        full_label = ' '.join(lines_to_keep).strip().rstrip(':')

        # Deduplicate adjacent repeated words
        word_list = full_label.split()
        clean = []
        for wd in word_list:
            if not clean or wd != clean[-1]:
                clean.append(wd)
        full_label = ' '.join(clean)

        return (full_label, best_is_colon, best[0], b_x0, b_x1)

    # ------------------------------------------------------------------
    # _find_label_below  (ported from v23 _find_nearby_text direction='below')
    # ------------------------------------------------------------------

    def _find_label_below(
        self,
        words: List[Dict],
        page_lines: List[Dict],
        x: float,
        y: float,
        max_dist: float = 10.0,
    ) -> Optional[Tuple[str, bool, float, float, float]]:
        """
        Find label below the line.
        Returns (text, is_colon, score, label_x0, label_x1) or None.
        """
        candidates = []

        for word in words:
            wx0 = float(word['x0'])
            wx1 = float(word['x1'])
            w_top = float(word['top'])

            y_dist = w_top - y
            if y_dist < 0 or y_dist > 12:
                continue

            if wx0 > x + 100 or wx1 < x - 10:
                continue

            text = word['text'].strip()
            is_colon = text.endswith(':')

            text_clean = text.lower().replace(':', '').strip('().,')
            is_instruction = (
                text.startswith('(') or text.endswith(')')
                or text_clean in _FILLER_WORDS
                or 'apply' in text_clean or 'check' in text_clean
            )

            score = y_dist + (0 if is_colon else 50) + (200 if is_instruction else 0) + 300
            candidates.append((score, text, is_colon, w_top, wx0, wx1))

        if not candidates:
            return None

        candidates.sort()
        best = candidates[0]
        best_text, best_is_colon, best_top = best[1], best[2], best[3]
        b_x0, b_x1 = best[4], best[5]

        # Aggregate multi-line label below
        sorted_lines = sorted(page_lines, key=lambda l: float(l.get('top', 0)))
        label_lines: Dict[float, List[Tuple[float, str]]] = {}

        for word in words:
            wt = float(word['top'])
            if abs(wt - best_top) < 3 or 3 <= (wt - best_top) < 12:
                # Boundary check
                has_boundary = False
                if wt > best_top + 3:
                    for l in sorted_lines:
                        l_top = float(l.get('top', 0))
                        if best_top + 1 < l_top < wt - 1:
                            if float(l.get('x0', 0)) <= x + 50 and float(l.get('x1', 0)) >= x - 10:
                                has_boundary = True
                                break
                if has_boundary:
                    continue

                wx0 = float(word['x0'])
                wx1 = float(word['x1'])
                if wx0 <= x + 150 and wx1 >= x - 20:
                    bucket = round(wt, 1)
                    if bucket not in label_lines:
                        label_lines[bucket] = []
                    label_lines[bucket].append((wx0, word['text']))

        # Join lines top to bottom
        sorted_keys = sorted(label_lines.keys(), reverse=True)
        lines_to_keep: List[str] = []
        for t in sorted_keys:
            line_words_list = sorted(label_lines[t])
            line_text = ' '.join(lw[1] for lw in line_words_list)
            lines_to_keep.insert(0, line_text)

            lt_clean = line_text.strip().lower()
            if lt_clean.endswith('?') or lt_clean.startswith('if ') or lt_clean.startswith('if,'):
                break
            if len(lines_to_keep) > 1 and ':' in line_text:
                break
            if len(lines_to_keep) >= 3:
                break

        full_label = ' '.join(lines_to_keep).strip().rstrip(':')

        # Deduplicate adjacent repeated words
        word_list = full_label.split()
        clean = []
        for wd in word_list:
            if not clean or wd != clean[-1]:
                clean.append(wd)
        full_label = ' '.join(clean)

        return (full_label, best_is_colon, best[0], b_x0, b_x1)

    # ------------------------------------------------------------------
    # Segment iteration  (Phase 2-4 of v23 per-line loop)
    # ------------------------------------------------------------------

    def _process_line_segments(
        self,
        page: PageModel,
        x0: float,
        y: float,
        x1: float,
        initial_label: Optional[str],
        field_height: int,
    ) -> List[FieldCandidate]:
        """
        Iterate horizontally across a single line, consuming label text and
        emitting field candidates at each gap.

        Handles multi-field lines like:
            "Company Name _______ City _______ State _______"
        """
        results: List[FieldCandidate] = []
        words = page.words

        # Collect words near this line
        line_words = self._collect_line_words(words, y, x0, x1)

        cursor = x0
        iter_count = 0

        while cursor < x1 - 5 and iter_count < 10:
            iter_count += 1

            # --- Consume label text at cursor ---
            consumed_words: List[str] = []
            consumed_first_y: Optional[float] = None
            current_push = cursor

            for word in line_words:
                wx0 = float(word['x0'])
                wx1 = float(word['x1'])
                wy = float(word['top'])

                if wx1 <= current_push:
                    continue

                gap = wx0 - current_push
                if gap < 30:
                    text = word['text'].strip()
                    # Hit an underscore block -> stop consuming
                    if '_' in text and len(text) > 1:
                        break

                    if consumed_first_y is None:
                        consumed_first_y = wy
                    if abs(wy - consumed_first_y) <= 3:
                        consumed_words.append(text)

                    current_push = max(current_push, wx1)
                else:
                    break

            # Only add a small gap after consumed label text; when the
            # field starts right at the line edge (no label), use x0 exactly
            # so the field aligns with the visible line start.
            field_x0 = current_push + 3 if consumed_words else current_push

            # If we consumed past the end of the line, try full-line fallback
            if field_x0 >= x1:
                fallback = self._try_full_line_fallback(
                    page, line_words, x0, y, x1, field_height, consumed_words,
                )
                if fallback:
                    results.append(fallback)
                break

            # --- Phase 2: Find collision (end of field) ---
            collision_x, next_cursor = self._find_collision(
                line_words, field_x0, x1,
            )

            if collision_x:
                field_x1 = collision_x - 5
            else:
                field_x1 = x1
                next_cursor = x1

            # --- Phase 3: Create field if valid ---
            field_width = field_x1 - field_x0
            if field_width > 12:
                seg_label = self._resolve_segment_label(
                    consumed_words, initial_label, line_words, field_x0,
                )

                # Instruction filter on segment label
                if seg_label and self._is_instruction_label(seg_label):
                    cursor = next_cursor
                    continue

                name_hint = clean_field_name(seg_label) if seg_label else 'Line_Field'
                fmt_hint, fmt_opts = detect_field_format(None, seg_label)

                # Upgrade source for signature/date labels so they get
                # higher priority in the resolver (beat HLT sub-cells).
                seg_source = 'form_line_segment'
                if seg_label:
                    seg_lower = seg_label.lower()
                    if 'signature' in seg_lower or 'sign here' in seg_lower:
                        seg_source = 'signature'
                    elif 'date' == seg_lower.strip() or seg_lower.strip().startswith('date'):
                        seg_source = 'date'

                results.append(FieldCandidate(
                    page=page.page_num,
                    x0=field_x0,
                    y0=y - field_height,
                    x1=field_x1,
                    y1=y,
                    field_type=FieldType.SIGNATURE if seg_source == 'signature' else FieldType.TEXT,
                    source=seg_source,
                    name_hint=name_hint,
                    label=seg_label,
                    format_hint=fmt_hint,
                    format_options=fmt_opts if fmt_opts else None,
                    source_line_x0=field_x0,
                    source_line_x1=field_x1,
                ))

            # --- Phase 4: Advance cursor ---
            cursor = next_cursor

        return results

    # ------------------------------------------------------------------
    # Helpers for segment iteration
    # ------------------------------------------------------------------

    @staticmethod
    def _collect_line_words(
        words: List[Dict], y: float, x0: float, x1: float,
    ) -> List[Dict]:
        """
        Collect words that sit on or very near a horizontal line.
        Excludes text strictly below the line (next row).
        """
        line_words: List[Dict] = []
        for word in words:
            w_bot = float(word['bottom'])
            w_top = float(word['top'])

            # Exclude text strictly below the line
            if w_top > y + 2:
                continue

            # Within 12pt vertically
            if abs(w_bot - y) < 12 or abs(w_top - y) < 12:
                line_words.append(word)

        line_words.sort(key=lambda w: float(w['x0']))
        return line_words

    @staticmethod
    def _find_collision(
        line_words: List[Dict], field_x0: float, line_x1: float,
    ) -> Tuple[Optional[float], float]:
        """
        Scan for the next text/label on the line after field_x0.

        Returns (collision_x, next_cursor).
        collision_x is None if no collision found; next_cursor is where the
        outer loop should advance to.
        """
        for word in line_words:
            wx0 = float(word['x0'])
            wx1 = float(word['x1'])

            if wx0 <= field_x0:
                continue

            text = word['text'].strip()

            # Ignore parenthetical hints
            if text.startswith('(') or text.endswith(')'):
                continue
            if text.lower() in _HINT_WORDS:
                continue
            if all(c in '.,:;-\'"' for c in text):
                continue

            # Real collision — text without underscores, longer than 1 char
            if '_' not in text and len(text) > 1:
                return wx0, wx0

        return None, line_x1

    def _resolve_segment_label(
        self,
        consumed_words: List[str],
        initial_label: Optional[str],
        line_words: List[Dict],
        field_x0: float,
    ) -> Optional[str]:
        """
        Determine the best label for a segment, trying multiple fallbacks:
        1. Consumed words from cursor advance
        2. Left-side words within 150pt
        3. Initial label from Phase 1
        """
        seg_label = ' '.join(consumed_words).strip().rstrip(':') if consumed_words else ''

        if self._is_meaningful_label(seg_label):
            return seg_label

        # Fallback: words to the left within 150pt
        left_candidates = []
        for word in line_words:
            wx1 = float(word['x1'])
            if wx1 < field_x0 and (field_x0 - wx1) < 150:
                left_candidates.append(word)

        if left_candidates:
            left_candidates.sort(key=lambda w: float(w['x0']))
            left_label = ' '.join(w['text'] for w in left_candidates).strip()
            if self._is_meaningful_label(left_label):
                return left_label

        # Fallback: initial label from Phase 1
        if initial_label and self._is_meaningful_label(initial_label):
            return initial_label

        return seg_label or None

    def _is_meaningful_label(self, label: Optional[str]) -> bool:
        """Check if a label is meaningful (not empty, not just 'Field')."""
        if not label:
            return False
        cleaned = clean_field_name(label)
        return bool(cleaned) and cleaned != 'Field' and len(cleaned) > 1

    def _try_full_line_fallback(
        self,
        page: PageModel,
        line_words: List[Dict],
        x0: float,
        y: float,
        x1: float,
        field_height: int,
        consumed_words: List[str],
    ) -> Optional[FieldCandidate]:
        """
        When label text consumes the entire line, the line itself is a fill-in
        area with the label sitting ON TOP. Create a field spanning the full line.
        """
        # Build label from words whose X range overlaps the line
        on_line_words: List[str] = []
        first_on_line_y: Optional[float] = None
        for word in sorted(line_words, key=lambda w: float(w['x0'])):
            lwx0 = float(word['x0'])
            lwy = float(word['top'])
            if lwx0 >= x0 - 2 and lwx0 < x1 + 2:
                if first_on_line_y is None:
                    first_on_line_y = lwy
                if abs(lwy - first_on_line_y) <= 3:
                    on_line_words.append(word['text'].strip())

        full_line_label = (
            ' '.join(on_line_words).strip().rstrip(':')
            if on_line_words
            else ' '.join(consumed_words).strip().rstrip(':')
        )

        line_len = x1 - x0
        if full_line_label and 20 <= line_len < 200:
            name_hint = clean_field_name(full_line_label)
            fmt_hint, fmt_opts = detect_field_format(None, full_line_label)
            return FieldCandidate(
                page=page.page_num,
                x0=x0,
                y0=y - field_height,
                x1=x1,
                y1=y,
                field_type=FieldType.TEXT,
                source='form_line_segment',
                name_hint=name_hint,
                label=full_line_label,
                format_hint=fmt_hint,
                format_options=fmt_opts if fmt_opts else None,
                source_line_x0=x0,
                source_line_x1=x1,
            )

        return None
