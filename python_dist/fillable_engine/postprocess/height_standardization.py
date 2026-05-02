"""
Post-processor: Field height standardisation.

Runs AFTER the resolver commits fields and BEFORE the writer creates widgets.

Five functions:
  A. **Global single-line height override** — when the user enables
     ``enable_global_single_line_height``, ALL single-line text fields are
     resized to the configured target height (bottom-anchored).  Dense-grid
     fields and multiline fields are excluded.
     Ported from v23 ``_apply_global_single_line_height``.

  B. **Y-band normalisation** — fields in the same horizontal row are grown
     to the mode height of that row so the result looks uniform.
     Ported from v23 ``_normalize_grid_column_field_heights``.

  C. **Form-line sequence equalisation** — detects sequences of 3+
     form-line-sourced fields and normalises them all to
     ``STANDARD_FIELD_HEIGHT``, ensuring visually uniform rows.

  D. **Tall-cell interior-line repositioning** — very tall text fields
     (h > 40pt) that contain an interior horizontal line are resized to
     sit ON that line (14pt, bottom-anchored), so user input sits on the
     fill line rather than floating in an oversized cell.

  E. **Form-line width validation** — clips form-line fields whose width
     far exceeds the actual underlying horizontal line, preventing fields
     from stretching past short underlines.
"""

from collections import Counter, defaultdict
from typing import Dict, List

from ..models import ResolvedField, FieldType, PageModel

# Minimum sensible field height -- never shrink below this.
STANDARD_FIELD_HEIGHT = 14.0

# Maximum vertical distance between field midpoints that counts as
# "same Y-band" (in points).
Y_BAND_TOLERANCE = 5.0

# Dense-grid source tags that should NOT receive the global height override.
# These are tightly-packed data grids where uniform row height matters.
DENSE_GRID_SOURCES = {
    'strict_grid', 'grid_fallback', 'table_data_row', 'sub_table_data_row',
    'comb_box_grid', 'image_grid_cell', 'grid_fallback_table_cell',
    'visual_in_cell_fallback', 'grid_reconstruction',
    'split_cell_multi_row', 'grid_gap_fill',
}

# Sources that originate from form-line or underline-based detection.
# Used by Part C (form-line sequence equalisation).
FORM_LINE_SOURCES = {
    'form_line_presplit', 'form_line_segment',
    'horizontal_line_table',
    'special_label', 'special_structural_header',
    'inline_label', 'inline_fill_underline',
    'general_underscore', 'embedded_underscore',
    'label_below_underscore',
}

# Maximum gap (pts) between one field's y1 and the next field's y0
# for them to be considered part of the same form-line sequence.
FORM_LINE_SEQ_GAP = 30.0

# Minimum number of fields to form a "sequence" (avoids normalising
# isolated table cells that happen to share a form-line source).
MIN_SEQ_LEN = 3

# Fields taller than this are candidates for interior-line repositioning.
TALL_CELL_THRESHOLD = 40.0


class HeightStandardizer:
    """
    Standardise text-field heights.

    Part A — Global single-line height override (when enabled):
      For every single-line text field NOT in a dense grid, bottom-anchor
      it to the configured target height.

    Part B — Y-band normalisation:
      1. Group text fields by page.
      2. Within each page, bucket fields into Y-bands (midpoints within
         ``Y_BAND_TOLERANCE``).
      3. Inside each Y-band, determine the *most common* height (mode).
      4. Grow any field whose height is less than the mode to match it
         (expand downward by default).  Fields are **never shrunk**;
         they only grow.
      5. If the mode itself is below ``STANDARD_FIELD_HEIGHT``, the
         standard height is used as the target instead.
    """

    def __init__(self, settings: Dict = None):
        self.settings = settings or {}

    def process(
        self,
        fields: List[ResolvedField],
        pages: List[PageModel],
    ) -> List[ResolvedField]:
        """Normalise heights in-place.  Returns the same *fields* list."""

        # --- Part A: Global single-line height override --------------------
        self._apply_global_single_line_height(fields)

        # --- Part B: Y-band normalisation ----------------------------------
        # Only operate on text-type fields (TEXT and COMB).
        text_fields = [
            f for f in fields
            if f.field_type in (FieldType.TEXT, FieldType.COMB)
        ]
        if not text_fields:
            return fields

        # Group by page
        by_page: dict[int, List[ResolvedField]] = defaultdict(list)
        for f in text_fields:
            by_page[f.page].append(f)

        pages_by_num = {p.page_num: p for p in pages}
        for _page_num, page_fields in by_page.items():
            self._normalise_page(page_fields,
                                 pages_by_num.get(_page_num))

        # --- Part B2: h-line grid row expansion for form-line fields --------
        self._expand_to_hline_rows(text_fields, pages_by_num)

        # --- Part C: Form-line sequence equalisation -----------------------
        self._equalize_form_line_heights(fields, pages)

        # --- Part D: Tall-cell interior-line repositioning -----------------
        self._reposition_tall_cells(fields, pages)

        # --- Part E: Form-line width validation ----------------------------
        self._validate_form_line_widths(fields, pages)

        # --- Part F: Colored header cell removal ---------------------------
        fields = self._remove_fields_in_colored_headers(fields, pages)

        return fields

    # -- Part A: global height override -------------------------------------

    def _apply_global_single_line_height(
        self, fields: List[ResolvedField]
    ) -> None:
        """
        Apply the user's global single-line height override.

        When ``enable_global_single_line_height`` is True in settings,
        every qualifying text field is bottom-anchored to the target
        height (``global_single_line_height`` in inches × 72 = points).

        Fields are excluded if they are:
        - Checkboxes, radio buttons, or image boxes
        - Comb / box-entry fields
        - Multiline (height exceeds the multiline threshold from settings)
        - From a dense-grid detector source
        """
        enable = self.settings.get('enable_global_single_line_height', False)
        if not enable:
            return

        # Convert inches → points
        val = self.settings.get('global_single_line_height', 0.25)
        try:
            target_h = float(val) * 72
        except (TypeError, ValueError):
            target_h = 18.0

        # Fields taller than this are considered multiline and should
        # not be forced to the single-line target height.
        multiline_threshold = float(
            self.settings.get('multiline_height_threshold', 25)
        )

        count = 0
        for f in fields:
            # Only text fields
            if f.field_type not in (FieldType.TEXT,):
                continue

            # Skip comb / image
            if f.is_comb or f.is_image_box:
                continue

            # Skip radio children
            if f.is_radio_child:
                continue

            h = f.y1 - f.y0

            # Skip multiline fields — any field already taller than the
            # multiline threshold is intentionally tall (description box,
            # address block, etc.) and must not be squished.
            if h > multiline_threshold:
                continue

            # Skip dense-grid sources
            source = (f.source or '').strip()
            if source in DENSE_GRID_SOURCES:
                continue

            # Bottom-anchor: keep y1 (line/bottom edge) fixed, grow upward.
            # y1 was snapped to a form line by LineSnapper; moving it would
            # detach the field from its snapped position.
            f.y0 = f.y1 - target_h
            count += 1

        if count > 0:
            print(f"   [GlobalHeight] Adjusted {count} fields to "
                  f"{target_h:.1f}pt ({val}in)")

    # -- Part B: Y-band normalisation internals -----------------------------

    @staticmethod
    def _normalise_page(page_fields: List[ResolvedField],
                        page: 'PageModel | None' = None) -> None:
        """Normalise field heights for one page."""
        # Sort by vertical midpoint so band detection is stable
        page_fields.sort(key=lambda f: (f.y0 + f.y1) / 2)

        # Build Y-bands via a simple sweep: any field whose midpoint is
        # within Y_BAND_TOLERANCE of the band's running average joins
        # that band; otherwise a new band is started.
        bands: List[List[ResolvedField]] = []
        current_band: List[ResolvedField] = []
        band_y_sum = 0.0

        for f in page_fields:
            mid_y = (f.y0 + f.y1) / 2
            if current_band:
                band_avg = band_y_sum / len(current_band)
                if abs(mid_y - band_avg) <= Y_BAND_TOLERANCE:
                    current_band.append(f)
                    band_y_sum += mid_y
                    continue
                # Commit current band
                bands.append(current_band)
            current_band = [f]
            band_y_sum = mid_y

        if current_band:
            bands.append(current_band)

        # --- Secondary pass: merge single-field bands into nearby bands
        # that share the same bottom edge (y1).  This catches fields
        # like Middle_Initial (midpoint=119.2) sitting beside First_Name
        # (midpoint=112.65) — they share the same h-line row (y1) but
        # their midpoints differ by >5pt because one is much shorter.
        Y1_MERGE_TOL = 3.0  # y1 match tolerance
        MAX_MERGE_GAP = 15.0  # max midpoint distance for y1 merge
        merged_bands: List[List[ResolvedField]] = []
        skip = set()
        for i, band in enumerate(bands):
            if i in skip:
                continue
            if len(band) == 1:
                # Try to merge this singleton into an adjacent multi-field band
                f = band[0]
                f_mid = (f.y0 + f.y1) / 2
                best_idx = -1
                best_dist = float('inf')
                f_h = f.y1 - f.y0
                for j, other_band in enumerate(bands):
                    if j == i or j in skip or len(other_band) < 2:
                        continue
                    ob_mid = sum((o.y0 + o.y1) / 2 for o in other_band) / len(other_band)
                    if abs(f_mid - ob_mid) > MAX_MERGE_GAP:
                        continue
                    # Don't merge when the band's fields are much taller
                    # than the singleton (sub-cell vs full-row mismatch).
                    ob_max_h = max(o.y1 - o.y0 for o in other_band)
                    if f_h > 15 and ob_max_h > f_h * 1.5:
                        continue
                    # Check if any field in other_band shares y1
                    for o in other_band:
                        if abs(f.y1 - o.y1) <= Y1_MERGE_TOL:
                            dist = abs(f_mid - ob_mid)
                            if dist < best_dist:
                                best_dist = dist
                                best_idx = j
                            break
                if best_idx >= 0:
                    bands[best_idx].append(f)
                    skip.add(i)
                    continue
            merged_bands.append(band)
        bands = merged_bands

        # Normalise heights inside each band
        for band in bands:
            if len(band) < 2:
                # Single-field band: still enforce minimum height so
                # isolated fields don't stay undersized (e.g. 10pt
                # embedded_underscore fields should grow to 14pt).
                f = band[0]
                current_h = f.y1 - f.y0
                if current_h < STANDARD_FIELD_HEIGHT:
                    f.y0 = f.y1 - STANDARD_FIELD_HEIGHT
                continue

            # Determine the mode and max heights (rounded to 0.5pt)
            height_counts: Counter = Counter()
            for f in band:
                h = round((f.y1 - f.y0) * 2) / 2  # nearest 0.5pt
                height_counts[h] += 1

            mode_height = height_counts.most_common(1)[0][0]
            max_height = max(f.y1 - f.y0 for f in band)

            # When the tallest field in the band is within single-line
            # range (≤ 30pt), use it as the target so all fields in the
            # same row match (e.g. a 19pt table cell + 11pt inline label
            # both grow to 19pt).  For taller outliers (multiline fields)
            # fall back to the mode to avoid over-expanding.
            if max_height <= 30:
                target_height = max(max_height, STANDARD_FIELD_HEIGHT)
            else:
                target_height = max(mode_height, STANDARD_FIELD_HEIGHT)

            for f in band:
                # Never resize COMB fields — their dimensions are
                # precisely set by BoxEntryDetector to match the box grid.
                if f.field_type == FieldType.COMB:
                    continue

                current_h = f.y1 - f.y0
                if current_h < target_height:
                    new_y0 = f.y1 - target_height

                    # For label_entry_below/cell fields, allow upward
                    # expansion but cap at the label text bottom so the
                    # field never covers its label.
                    src = getattr(f, 'source', '') or ''
                    if src in ('label_entry_below', 'label_entry_cell'):
                        if page is not None:
                            # Check for text in the expansion zone
                            words = page.get_words_in_bbox(
                                (f.x0, new_y0, f.x1, f.y0), 0.3)
                            label_words = [
                                w for w in words
                                if w.get('text', '').count('_')
                                < len(w.get('text', '')) * 0.5
                            ]
                            if label_words:
                                # Cap y0 just below the label text
                                text_bot = max(
                                    w.get('bottom', w.get('y1', f.y0))
                                    for w in label_words)
                                new_y0 = max(new_y0, text_bot + 0.5)
                            # Only apply if it actually grows the field
                            if new_y0 >= f.y0:
                                continue
                        else:
                            continue

                    # Grow upward to reach target (bottom-anchor).
                    # y1 was snapped to a form line by LineSnapper; moving
                    # it would detach the field from its line.
                    f.y0 = new_y0

    # -- Part B2: h-line grid row expansion ----------------------------------

    @staticmethod
    def _expand_to_hline_rows(
        text_fields: List[ResolvedField],
        pages_by_num: dict,
    ) -> None:
        """
        Expand form_line_presplit fields to fill their h-line grid row.

        When form_line_presplit fields sit on an h-line grid (bottom edge near
        an h-line) but are shorter than the grid row height, expand upward to
        fill the row.  This fixes cases where Phone/Email fields are 14pt
        while neighboring cells in the same grid are 22pt.
        """
        by_page: dict[int, List[ResolvedField]] = defaultdict(list)
        all_by_page: dict[int, List[ResolvedField]] = defaultdict(list)

        for f in text_fields:
            all_by_page[f.page].append(f)
            src = getattr(f, 'source', '') or ''
            if src == 'form_line_presplit' and f.y1 - f.y0 < 20:
                by_page[f.page].append(f)

        count = 0
        for pg_num, fields in by_page.items():
            page = pages_by_num.get(pg_num)
            if not page:
                continue

            for f in fields:
                field_w = max(1.0, f.x1 - f.x0)

                # Find h-line near bottom of field (within 3pt),
                # overlapping the field's x-range
                bottom_hl = None
                for hl in page.h_lines:
                    hy = float(hl.get('y', hl.get('top', 0)))
                    hx0 = float(hl.get('x0', 0))
                    hx1 = float(hl.get('x1', 0))
                    overlap = min(hx1, f.x1) - max(hx0, f.x0)
                    if abs(hy - f.y1) < 3 and overlap > field_w * 0.3:
                        bottom_hl = hy
                        break

                if bottom_hl is None:
                    continue

                # Find nearest h-line above the field
                top_hl = None
                for hl in page.h_lines:
                    hy = float(hl.get('y', hl.get('top', 0)))
                    hx0 = float(hl.get('x0', 0))
                    hx1 = float(hl.get('x1', 0))
                    overlap = min(hx1, f.x1) - max(hx0, f.x0)
                    if (hy < f.y0 - 1
                            and overlap > field_w * 0.3
                            and (hx1 - hx0) > 30):
                        if top_hl is None or hy > top_hl:
                            top_hl = hy

                if top_hl is None:
                    continue

                row_height = bottom_hl - top_hl
                if row_height < 15 or row_height > 40:
                    continue

                if f.y1 - f.y0 >= row_height - 1:
                    continue  # Already fills the row

                new_y0 = top_hl

                # Check for label text in the expansion zone
                words = page.get_words_in_bbox(
                    (f.x0, new_y0, f.x1, f.y0), 0.3)
                label_words = [
                    w for w in words
                    if w.get('text', '').count('_')
                    < len(w.get('text', '')) * 0.5
                ]
                if label_words:
                    text_bot = max(
                        w.get('bottom', w.get('y1', f.y0))
                        for w in label_words)
                    new_y0 = max(new_y0, text_bot + 0.5)

                if new_y0 < f.y0 - 0.5:
                    f.y0 = new_y0
                    count += 1

        if count:
            print(f"   [HLineGridExpand] Expanded {count} form-line field(s) "
                  f"to fill h-line grid rows")

    # -- Part C: form-line sequence equalisation -----------------------------

    @staticmethod
    def _equalize_form_line_heights(
        fields: List[ResolvedField],
        pages: List[PageModel],
    ) -> None:
        """
        Find sequences of 3+ form-line-sourced text fields on the same page
        and normalise them all to STANDARD_FIELD_HEIGHT, bottom-anchored.

        This ensures that rows like a–j on a single-section form all share
        the same visual height, regardless of which detector produced them.
        """
        # Collect eligible fields by page
        by_page: dict[int, List[ResolvedField]] = defaultdict(list)
        for f in fields:
            if (f.field_type in (FieldType.TEXT, FieldType.COMB)
                    and (f.source or '').strip() in FORM_LINE_SOURCES):
                by_page[f.page].append(f)

        count = 0
        for _pg, page_fields in by_page.items():
            if len(page_fields) < MIN_SEQ_LEN:
                continue

            page_fields.sort(key=lambda f: f.y0)

            # Build sequences: track running max y1 to bridge checkbox rows
            sequences: List[List[ResolvedField]] = []
            current: List[ResolvedField] = [page_fields[0]]
            max_y1 = page_fields[0].y1

            for f in page_fields[1:]:
                gap = f.y0 - max_y1
                if gap < FORM_LINE_SEQ_GAP:
                    current.append(f)
                    max_y1 = max(max_y1, f.y1)
                else:
                    if len(current) >= MIN_SEQ_LEN:
                        sequences.append(current)
                    current = [f]
                    max_y1 = f.y1

            if len(current) >= MIN_SEQ_LEN:
                sequences.append(current)

            # Normalise each sequence
            for seq in sequences:
                for f in seq:
                    h = f.y1 - f.y0
                    if h < STANDARD_FIELD_HEIGHT - 0.5:
                        f.y0 = f.y1 - STANDARD_FIELD_HEIGHT
                        count += 1

        if count:
            print(f"   [FormLineEq] Normalised {count} form-line fields "
                  f"to {STANDARD_FIELD_HEIGHT:.0f}pt")

    # -- Part D: tall-cell interior-line repositioning -----------------------

    @staticmethod
    def _reposition_tall_cells(
        fields: List[ResolvedField],
        pages: List[PageModel],
    ) -> None:
        """
        Very tall text fields (h > TALL_CELL_THRESHOLD) that sit inside a
        table cell may contain an interior horizontal line where the user
        is expected to write (e.g. "$____" cells).  Reposition them to sit
        ON that interior line instead of filling the whole cell.

        If another field already occupies the repositioned area (e.g. a
        sub-cell field from the visual grid), drop the tall field instead
        of creating an overlap.
        """
        pages_by_num = {p.page_num: p for p in pages}
        count = 0
        to_remove: set = set()

        for idx, f in enumerate(fields):
            if f.field_type not in (FieldType.TEXT, FieldType.COMB):
                continue
            h = f.y1 - f.y0
            if h < TALL_CELL_THRESHOLD:
                continue

            page = pages_by_num.get(f.page)
            if not page:
                continue

            # Find interior h-lines (not at the field edges)
            interior_lines: List[float] = []
            field_w = max(1.0, f.x1 - f.x0)
            for hl in page.h_lines:
                ly = float(hl.get('y', 0))
                lx0 = float(hl.get('x0', 0))
                lx1 = float(hl.get('x1', 0))
                # Must be well inside the field (not edge lines)
                if ly <= f.y0 + 5 or ly >= f.y1 - 5:
                    continue
                # Must horizontally overlap the field by ≥25% of field
                # width (prevents sub-pt overshoot from adjacent columns)
                overlap = min(lx1, f.x1) - max(lx0, f.x0)
                if overlap < field_w * 0.25:
                    continue
                interior_lines.append(ly)

            if interior_lines:
                # Position on the lowest interior line (fill area)
                best_line = max(interior_lines)
                new_y1 = best_line
                new_y0 = best_line - STANDARD_FIELD_HEIGHT

                # Check if another field already occupies the new position
                already_covered = False
                for other in fields:
                    if other is f or other.page != f.page:
                        continue
                    if other.field_type not in (FieldType.TEXT, FieldType.COMB):
                        continue
                    # Check horizontal overlap
                    x_ov = min(f.x1, other.x1) - max(f.x0, other.x0)
                    min_w = min(f.x1 - f.x0, other.x1 - other.x0)
                    if min_w <= 0 or x_ov / min_w < 0.5:
                        continue
                    # Check vertical overlap with the NEW position
                    y_ov = min(new_y1, other.y1) - max(new_y0, other.y0)
                    if y_ov > 3:
                        already_covered = True
                        break

                if already_covered:
                    # Another field (e.g. sub-cell) already covers this
                    # position — remove the tall field instead of
                    # repositioning it into an overlap.
                    to_remove.add(idx)
                else:
                    f.y1 = new_y1
                    f.y0 = new_y0
                    count += 1

        if to_remove:
            fields[:] = [f for i, f in enumerate(fields)
                         if i not in to_remove]
            print(f"   [TallCellFix] Removed {len(to_remove)} tall field(s) "
                  f"that overlapped existing sub-cell fields")

        if count:
            print(f"   [TallCellFix] Repositioned {count} tall field(s) "
                  f"onto interior lines")

    # -- Part E: form-line width validation ----------------------------------

    @staticmethod
    def _validate_form_line_widths(
        fields: List[ResolvedField],
        pages: List[PageModel],
    ) -> None:
        """
        Clip form-line-sourced fields whose width greatly exceeds the
        actual horizontal line at their bottom edge.  This prevents fields
        from stretching past short underlines (e.g. "Customer Initials ___"
        where the underline is only 50pt but the field extends 300+ pt).
        """
        pages_by_num = {p.page_num: p for p in pages}
        count = 0

        for f in fields:
            if f.field_type not in (FieldType.TEXT, FieldType.COMB):
                continue
            src = (f.source or '').strip()
            if src not in FORM_LINE_SOURCES:
                continue

            page = pages_by_num.get(f.page)
            if not page:
                continue

            field_w = f.x1 - f.x0

            # Find h-lines near the field's bottom edge
            best_line = None
            best_dist = float('inf')
            for hl in page.h_lines:
                ly = float(hl.get('y', 0))
                lx0 = float(hl.get('x0', 0))
                lx1 = float(hl.get('x1', 0))
                dist = abs(ly - f.y1)
                if dist > 3.0:
                    continue
                # Line must start near the field's left edge
                if abs(lx0 - f.x0) > 5.0:
                    continue
                line_w = lx1 - lx0
                # Only clip when line is much shorter than field
                if line_w < field_w * 0.5 and line_w >= 20:
                    if dist < best_dist:
                        best_dist = dist
                        best_line = (lx0, lx1)

            if best_line:
                f.x1 = best_line[1]
                count += 1

        if count:
            print(f"   [FormLineClip] Clipped {count} field(s) to actual "
                  f"line extent")

    # -- Part F: colored header cell removal ---------------------------------

    @staticmethod
    def _is_strong_color(fill) -> bool:
        """Return True when *fill* is a strongly-coloured (non-white) value."""
        if not fill or not isinstance(fill, (list, tuple)):
            return False
        vals = [float(v) for v in fill]
        if len(vals) == 4:
            # CMYK: (C, M, Y, K) — white is (0,0,0,0)
            return sum(vals[:3]) > 0.3
        if len(vals) == 3:
            # RGB: (R, G, B) — white is (1,1,1)
            return sum(vals) < 2.4
        if len(vals) == 1:
            # Greyscale — white is 1 (or 0 in some schemes)
            return vals[0] < 0.7
        return False

    # Maximum height for a rect to be considered a header row cell.
    # Header bars / table header rows are narrow (10–80 pt).  Large
    # background fills (covering the whole form body) are much taller
    # and must NOT be treated as headers.
    _MAX_HEADER_RECT_H = 80.0

    # Y-tolerance for grouping coloured rects into the same header row.
    _HEADER_ROW_Y_TOL = 5.0

    @staticmethod
    def _remove_fields_in_colored_headers(
        fields: List[ResolvedField],
        pages: List[PageModel],
    ) -> List[ResolvedField]:
        """
        Remove text fields that sit inside strongly-coloured header rows.

        Detection is colour-agnostic: any non-white fill (CMYK, RGB, or
        greyscale) is a candidate.  Two strategies identify headers:

        1. **Row grouping** — if 2+ coloured rects share the same Y-band
           (i.e. they sit side-by-side in a table header row), ALL of them
           are header zones regardless of text coverage.
        2. **Single-rect high coverage** — a lone coloured rect whose text
           spans > 55 % of the rect width is also a header zone.

        Large background fills (h > 80 pt) are excluded up-front so that
        forms using a full-page coloured background are not affected.

        A field is removed when its vertical extent is mostly (> 70 %)
        inside a header zone and it has any horizontal overlap.
        """

        # -- Step 1: collect candidate coloured rects per page -------------
        # Each entry: (rx0, ry0, rx1, ry1, text_coverage)
        _CandRect = tuple  # (rx0, ry0, rx1, ry1, coverage)
        page_cands: dict[int, list] = defaultdict(list)

        for p in pages:
            for r in p.rects:
                fill = r.get('non_stroking_color', None)
                if not HeightStandardizer._is_strong_color(fill):
                    continue
                ry0 = float(r.get('top', 0))
                ry1 = float(r.get('bottom', 0))
                rx0 = float(r.get('x0', 0))
                rx1 = float(r.get('x1', 0))
                rh = ry1 - ry0
                rw = rx1 - rx0
                # Skip tiny decorative elements and large background fills
                if rh < 10 or rw < 40:
                    continue
                if rh > HeightStandardizer._MAX_HEADER_RECT_H:
                    continue
                # Must contain at least some text to be a header label
                words = p.get_words_in_bbox(
                    (rx0, ry0, rx1, ry1), 0.3,
                )
                if not words:
                    continue
                txt_x0 = min(float(w.get('x0', 0)) for w in words)
                txt_x1 = max(float(w.get('x1', 0)) for w in words)
                text_span = txt_x1 - txt_x0
                coverage = text_span / rw if rw > 0 else 0
                page_cands[p.page_num].append(
                    (rx0, ry0, rx1, ry1, coverage))

        if not page_cands:
            return fields

        # -- Step 2: group rects into Y-band rows and decide headers -------
        header_zones: dict[int, list] = defaultdict(list)
        y_tol = HeightStandardizer._HEADER_ROW_Y_TOL

        for pg_num, cands in page_cands.items():
            # Sort by top edge for grouping
            cands.sort(key=lambda c: c[1])

            # Build Y-band groups
            groups: List[List[tuple]] = []
            cur_group: List[tuple] = [cands[0]]
            for c in cands[1:]:
                if abs(c[1] - cur_group[0][1]) <= y_tol:
                    cur_group.append(c)
                else:
                    groups.append(cur_group)
                    cur_group = [c]
            groups.append(cur_group)

            for grp in groups:
                if len(grp) >= 2:
                    # Multi-rect row → all are header cells
                    for (rx0, ry0, rx1, ry1, _cov) in grp:
                        header_zones[pg_num].append(
                            (rx0, ry0, rx1, ry1))
                else:
                    # Single rect → require high text coverage
                    rx0, ry0, rx1, ry1, cov = grp[0]
                    if cov > 0.55:
                        header_zones[pg_num].append(
                            (rx0, ry0, rx1, ry1))

        if not header_zones:
            return fields

        # -- Step 3: remove fields sitting inside header zones -------------
        to_remove: set = set()
        for idx, f in enumerate(fields):
            if f.field_type not in (FieldType.TEXT, FieldType.COMB,
                                    FieldType.SIGNATURE):
                continue

            page_zones = header_zones.get(f.page)
            if not page_zones:
                continue

            field_h = max(1.0, f.y1 - f.y0)

            for (rx0, ry0, rx1, ry1) in page_zones:
                # Vertical containment
                vy_top = max(f.y0, ry0)
                vy_bot = min(f.y1, ry1)
                vy_overlap = vy_bot - vy_top
                if vy_overlap <= 0:
                    continue
                vy_ratio = vy_overlap / field_h

                # Any horizontal overlap
                hx_overlap = min(f.x1, rx1) - max(f.x0, rx0)
                if hx_overlap <= 0:
                    continue

                if vy_ratio > 0.7:
                    to_remove.add(idx)
                    break

        if to_remove:
            removed = len(to_remove)
            fields = [f for i, f in enumerate(fields)
                      if i not in to_remove]
            print(f"   [ColoredHeaderFilter] Removed {removed} field(s) "
                  f"inside colored header cells")

        return fields
