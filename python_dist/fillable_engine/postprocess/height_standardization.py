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
    'split_cell_multi_row', 'grid_gap_fill', 'grid_line_fill',
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

        # --- Part G: Tiling-rect / labeled-header overlap removal ----------
        fields = self._remove_tiling_in_labeled_headers(fields)

        # --- Part H: Tiling-rect column-boundary clipping ------------------
        self._clip_tiling_rects_to_header_columns(fields, pages)

        # --- Part I: Grid column gap filler --------------------------------
        self._fill_grid_column_gaps(fields, pages)

        # --- Part J: Header entry propagation ------------------------------
        self._propagate_header_entries(fields, pages)

        # --- Part K: Grid reconstruction from h-lines + v-lines -----------
        self._fill_grid_from_lines(fields, pages)

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
        from dataclasses import replace as _dc_replace

        pages_by_num = {p.page_num: p for p in pages}
        count = 0
        split_count = 0
        to_remove: set = set()
        new_fields: List[ResolvedField] = []

        # Snapshot original Y-extents — splits mutate f.y1 in-place, which
        # would make later confirmed-line checks fail for fields processed
        # afterwards.  Use the snapshot for Method 1 cross-column checks.
        original_y = {id(f): (f.y0, f.y1) for f in fields}

        for idx, f in enumerate(fields):
            if f.field_type not in (FieldType.TEXT, FieldType.COMB):
                continue
            h = f.y1 - f.y0
            if h < TALL_CELL_THRESHOLD:
                continue

            page = pages_by_num.get(f.page)
            if not page:
                continue

            is_tiling = (getattr(f, 'source', '') or '').strip() == 'tiling_rect_table'

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

            if not interior_lines:
                continue

            if is_tiling:
                # --- tiling_rect_table: only split at confirmed grid ---
                # Check each interior h-line to see if it also passes
                # through ANOTHER tiling_rect_table field in a DIFFERENT
                # column on this page.  If so, it is a real grid row
                # separator and the cell should be split.  If no interior
                # line is confirmed, fall through to the normal reposition
                # path (the field is just a tall form-entry cell, not a
                # merged grid cell).
                #
                # ALSO accept lines whose page-wide h-line segments
                # collectively cover ≥ 80% of the grid width (these are
                # real table-row separators even if no adjacent
                # tiling_rect_table field exists).
                confirmed_lines = []

                # Pre-compute grid x-extent on this page
                page_fields_x = [
                    (o.x0, o.x1) for o in fields
                    if o.page == f.page
                    and o.field_type in (FieldType.TEXT, FieldType.COMB)
                ]
                if page_fields_x:
                    grid_x0 = min(x0 for x0, _ in page_fields_x)
                    grid_x1 = max(x1 for _, x1 in page_fields_x)
                    grid_w = max(1.0, grid_x1 - grid_x0)
                else:
                    grid_w = 0.0

                for ly in sorted(set(interior_lines)):
                    # Method 1: another tiling_rect field in a different column
                    found = False
                    for other in fields:
                        if other is f or other.page != f.page:
                            continue
                        if (getattr(other, 'source', '') or '').strip() != 'tiling_rect_table':
                            continue
                        if other.field_type not in (FieldType.TEXT, FieldType.COMB):
                            continue
                        # Must be in a different column (no significant x overlap)
                        x_ov = min(f.x1, other.x1) - max(f.x0, other.x0)
                        if x_ov > 5:
                            continue
                        # Use ORIGINAL y-extents so earlier splits don't
                        # invalidate later checks.
                        oy0, oy1 = original_y.get(id(other), (other.y0, other.y1))
                        if oy0 + 5 < ly < oy1 - 5:
                            found = True
                            break

                    # Method 2: page h-lines at this y span ≥ 80% of grid
                    if not found and grid_w > 50:
                        covered = 0.0
                        for hl in page.h_lines:
                            hly = float(hl.get('y', 0))
                            if abs(hly - ly) > 2:
                                continue
                            covered += float(hl.get('x1', 0)) - float(hl.get('x0', 0))
                        if covered / grid_w >= 0.80:
                            found = True

                    if found:
                        confirmed_lines.append(ly)

                if confirmed_lines:
                    sorted_lines = sorted(set(confirmed_lines))
                    boundaries = [f.y0] + sorted_lines + [f.y1]

                    def _evict_overlapping(sy0, sy1):
                        """Mark wider overlapping fields for removal."""
                        for oi, other in enumerate(fields):
                            if other is f or other.page != f.page:
                                continue
                            if other.field_type not in (FieldType.TEXT, FieldType.COMB):
                                continue
                            x_ov = min(f.x1, other.x1) - max(f.x0, other.x0)
                            min_w = min(f.x1 - f.x0, other.x1 - other.x0)
                            if min_w <= 0 or x_ov / min_w < 0.5:
                                continue
                            y_ov = min(sy1, other.y1) - max(sy0, other.y0)
                            if y_ov > 3:
                                to_remove.add(oi)

                    # Resize original field to first sub-row
                    first_y1 = boundaries[1]
                    _evict_overlapping(f.y0, first_y1)
                    f.y1 = first_y1
                    split_count += 1

                    # Create new fields for subsequent sub-rows
                    for i in range(1, len(boundaries) - 1):
                        sub_y0 = boundaries[i]
                        sub_y1 = boundaries[i + 1]
                        if sub_y1 - sub_y0 < 5:
                            continue
                        _evict_overlapping(sub_y0, sub_y1)
                        new_f = _dc_replace(
                            f,
                            y0=sub_y0,
                            y1=sub_y1,
                            name=f"{f.name}_r{i + 1}",
                        )
                        new_fields.append(new_f)
                        split_count += 1
                    continue  # Skip reposition below

            # --- Reposition onto lowest interior line ---
            # (non-tiling fields, or tiling fields without confirmed
            #  grid row separators)
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

        if new_fields:
            fields.extend(new_fields)

        if split_count:
            print(f"   [TallCellFix] Split {split_count} tall tiling_rect "
                  f"field(s) into sub-row fields")

        if count:
            print(f"   [TallCellFix] Repositioned {count} tall field(s) "
                  f"onto interior lines")

        # --- Cap extremely tall entry-type fields --------------------------
        # label_entry_cell and adjacent_empty_cell should never be very tall
        # (they are single-line entry fields).  If one spans an entire tall
        # rect (e.g. h > 80pt), cap it to the mode height of similar fields
        # at the same x-position.
        # EXCEPTION: if h-lines exist at both the top and bottom edges of
        # the field, it fills a real table cell — leave it alone.
        _MAX_ENTRY_H = 80.0
        _HLINE_TOL = 5.0
        capped = 0
        for f in fields:
            if f.field_type not in (FieldType.TEXT, FieldType.COMB):
                continue
            fh = f.y1 - f.y0
            if fh <= _MAX_ENTRY_H:
                continue
            src = (getattr(f, 'source', '') or '').strip()
            if src not in ('label_entry_cell', 'adjacent_empty_cell'):
                continue

            # Check if h-lines bracket both the top and bottom of this
            # field (confirming it fills a genuine table cell).
            page = pages_by_num.get(f.page)
            if page:
                has_top_line = False
                has_bot_line = False
                fw = f.x1 - f.x0
                for hl in page.h_lines:
                    ly = float(hl.get('y', 0))
                    lx0 = float(hl.get('x0', 0))
                    lx1 = float(hl.get('x1', 0))
                    x_ov = min(lx1, f.x1) - max(lx0, f.x0)
                    if x_ov < fw * 0.25:
                        continue
                    if abs(ly - f.y0) <= _HLINE_TOL:
                        has_top_line = True
                    if abs(ly - f.y1) <= _HLINE_TOL:
                        has_bot_line = True
                if has_top_line and has_bot_line:
                    continue  # real cell — don't cap

            # Find other same-source fields at similar x0 on this page
            similar_h = [
                o.y1 - o.y0 for o in fields
                if o is not f
                and o.page == f.page
                and abs(o.x0 - f.x0) < 5
                and (getattr(o, 'source', '') or '').strip() == src
                and o.y1 - o.y0 < _MAX_ENTRY_H
            ]
            if similar_h:
                mode_h = Counter(round(sh) for sh in similar_h).most_common(1)[0][0]
            else:
                mode_h = 18.0
            f.y1 = f.y0 + max(mode_h, STANDARD_FIELD_HEIGHT)
            capped += 1
        if capped:
            print(f"   [TallCellFix] Capped {capped} extremely tall "
                  f"entry field(s)")

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

    # -- Part G: Tiling-rect / labeled-header overlap removal ---------------

    @staticmethod
    def _remove_tiling_in_labeled_headers(
        fields: List[ResolvedField],
    ) -> List[ResolvedField]:
        """
        Remove tiling_rect_table fields that share the same Y-band as
        labeled header fields (label_entry_cell, special_structural_header)
        on the same page.

        On tally-sheet pages (P18-style), the last grid row's
        tiling_rect cells extend 40pt into the Building Address /
        Number of Floors / Building Number header section.  These
        overlapping tiling fields are structural artefacts — the
        header fields are the correct fillable areas.

        A tiling_rect field is removed when its y-range overlaps
        significantly (>30%) with any labeled header field's y-range
        on the same page.
        """
        _LABEL_HEADER_SOURCES = {
            'label_entry_cell', 'special_structural_header',
        }

        # Collect labeled header fields per page
        header_fields_by_page: dict = defaultdict(list)
        for f in fields:
            if (getattr(f, 'source', '') or '').strip() in _LABEL_HEADER_SOURCES:
                header_fields_by_page[f.page].append(f)

        if not header_fields_by_page:
            return fields

        to_remove: set = set()
        for idx, f in enumerate(fields):
            if (getattr(f, 'source', '') or '').strip() != 'tiling_rect_table':
                continue
            page_headers = header_fields_by_page.get(f.page)
            if not page_headers:
                continue
            fh = max(1.0, f.y1 - f.y0)
            for hf in page_headers:
                # Check vertical overlap — same Y-band
                y_ov = min(f.y1, hf.y1) - max(f.y0, hf.y0)
                if y_ov <= 0:
                    continue
                # Must also horizontally overlap — a tiling field in a
                # different column that merely shares a Y-band must not
                # be removed.
                x_ov = min(f.x1, hf.x1) - max(f.x0, hf.x0)
                if x_ov <= 0:
                    continue
                # Significant vertical overlap (>30% of tiling field)
                if y_ov / fh > 0.30:
                    to_remove.add(idx)
                    break

        if to_remove:
            removed = len(to_remove)
            fields = [f for i, f in enumerate(fields)
                      if i not in to_remove]
            print(f"   [TilingHeaderCleanup] Removed {removed} "
                  f"tiling_rect field(s) overlapping labeled headers")

        return fields

    # -- Part H: Tiling-rect column-boundary clipping -----------------------

    @staticmethod
    def _clip_tiling_rects_to_header_columns(
        fields: List[ResolvedField],
        pages: List[PageModel],
    ) -> None:
        """
        Clip tiling_rect_table and adjacent_empty_cell fields that cross
        column boundaries defined by colored header rects.

        On P13-style pages, tiling_rect cells span e.g. 430-516 (two
        columns: "# of Units" + "Total Area...") because no V-line
        exists in their Y range.  The colored header rects above them
        define the real column boundaries.  Split such fields at those
        boundaries.

        IMPORTANT: boundaries are scoped per grid section.  Each page
        may contain multiple grid sections with different column
        layouts.  A field is only split using boundaries from the
        header row directly above it (the nearest header row whose
        bottom edge is above the field's top).
        """
        from dataclasses import replace as _dc_replace

        pages_by_num = {p.page_num: p for p in pages}
        _CLIPPABLE_SOURCES = {'tiling_rect_table', 'adjacent_empty_cell',
                              'grid_fallback', 'horizontal_line_table'}
        _HEADER_Y_GROUP_TOL = 5.0  # merge header rects within 5pt Y

        new_fields: List[ResolvedField] = []
        to_remove: set = set()
        clip_count = 0

        # ---- Build per-page header rows (grouped by Y-band) ----
        # Each header row = (y_top, y_bot, set_of_x_boundaries)
        page_header_rows: dict = {}  # page_num -> list of (ytop, ybot, sorted_boundaries)

        for p in pages:
            # Collect colored header rects
            header_rects = []
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
                if rh < 10 or rw < 30 or rh > 80:
                    continue
                header_rects.append((ry0, ry1, rx0, rx1))

            if not header_rects:
                continue

            # Group header rects into rows by Y-band proximity
            header_rects.sort(key=lambda t: t[0])
            rows = []
            cur_y0 = header_rects[0][0]
            cur_y1 = header_rects[0][1]
            cur_bounds = set()
            for ry0, ry1, rx0, rx1 in header_rects:
                if ry0 - cur_y0 > _HEADER_Y_GROUP_TOL:
                    # Start a new row
                    rows.append((cur_y0, cur_y1, sorted(cur_bounds)))
                    cur_y0 = ry0
                    cur_y1 = ry1
                    cur_bounds = set()
                cur_y1 = max(cur_y1, ry1)
                cur_bounds.add(round(rx0, 1))
                cur_bounds.add(round(rx1, 1))
            rows.append((cur_y0, cur_y1, sorted(cur_bounds)))

            page_header_rows[p.page_num] = rows

        if not page_header_rows:
            return

        # Pre-compute per-page v-line column structure.  When real
        # v-lines already define column boundaries on a page, the
        # detectors produce correctly-sized fields — splitting them
        # again at header-rect boundaries creates bad fragments.
        _V_COL_TOL = 8.0
        pages_with_vline_cols: dict = {}  # page_num -> set of v-line X
        for p in pages:
            v_xs_raw = []
            for vl in p.v_lines:
                x = (float(vl.get('x0', 0)) + float(vl.get('x1', 0))) / 2
                v_xs_raw.append(round(x, 1))
            # Cluster into unique column positions
            vcols: list = []
            for xv in sorted(set(v_xs_raw)):
                if not vcols or xv - vcols[-1] > _V_COL_TOL:
                    vcols.append(xv)
            pages_with_vline_cols[p.page_num] = vcols

        for idx, f in enumerate(fields):
            if f.field_type not in (FieldType.TEXT, FieldType.COMB):
                continue
            src = (getattr(f, 'source', '') or '').strip()
            if src not in _CLIPPABLE_SOURCES:
                continue

            rows = page_header_rows.get(f.page)
            if not rows:
                continue

            fw = f.x1 - f.x0
            if fw < 30:
                continue

            # Skip pages where real v-lines already define ≥3 column
            # boundaries — the detectors already produced correctly-
            # sized fields, so header-rect splitting is redundant.
            vcols = pages_with_vline_cols.get(f.page, [])
            if len(vcols) >= 3:
                continue

            # Find the nearest header row ABOVE this field
            # (header row bottom <= field top + small tolerance)
            best_row = None
            best_gap = float('inf')
            for row_y0, row_y1, bounds in rows:
                gap = f.y0 - row_y1  # distance from header bottom to field top
                if gap < -5:
                    continue  # header is below the field
                if gap < best_gap:
                    best_gap = gap
                    best_row = (row_y0, row_y1, bounds)

            if best_row is None or best_gap > 150:
                continue

            _, _, col_bounds = best_row

            # Find column boundaries that fall INSIDE this field
            interior_bounds = [
                bx for bx in col_bounds
                if f.x0 + 10 < bx < f.x1 - 10
            ]

            if not interior_bounds:
                continue

            # Split the field at interior boundaries
            edges = sorted(set([f.x0] + interior_bounds + [f.x1]))
            to_remove.add(idx)
            for i in range(len(edges) - 1):
                sx0 = edges[i]
                sx1 = edges[i + 1]
                if sx1 - sx0 < 10:
                    continue
                new_f = _dc_replace(
                    f,
                    x0=sx0,
                    x1=sx1,
                    name=f.name if i == 0 else f"{f.name}_c{i + 1}",
                )
                new_fields.append(new_f)
                clip_count += 1

        if to_remove:
            fields[:] = [fld for i, fld in enumerate(fields)
                         if i not in to_remove]

        if new_fields:
            fields.extend(new_fields)

        if clip_count:
            print(f"   [ColumnClip] Split {clip_count} field(s) "
                  f"at colored header column boundaries")

    # -- Part I: Grid column gap filler -------------------------------------

    @staticmethod
    def _fill_grid_column_gaps(
        fields: List[ResolvedField],
        pages: List[PageModel],
    ) -> None:
        """
        After Part D splits tiling_rect_table cells into sub-rows, some
        grid columns may have cells only in a subset of rows (e.g. 3 of 4
        columns present in each row).  Fill in the missing cells.

        Algorithm:
          1. Group tiling_rect_table + grid_fallback fields by page.
          2. Cluster field edges into column-boundaries and row-boundaries.
          3. For each row × column intersection with no field, create one.
        """
        from dataclasses import replace as _dc_replace

        _GRID_SOURCES = {'tiling_rect_table', 'grid_fallback',
                         'strict_grid', 'grid_gap_fill'}
        _COL_TOL = 4.0
        _ROW_TOL = 4.0

        new_count = 0

        pages_present = set(f.page for f in fields)
        for pg in sorted(pages_present):
            grid_fields = [
                f for f in fields
                if f.page == pg
                and (getattr(f, 'source', '') or '').strip() in _GRID_SOURCES
                and f.field_type in (FieldType.TEXT, FieldType.COMB)
            ]
            if len(grid_fields) < 6:
                continue

            # Cluster x0 values into column-left positions
            x0_vals = sorted(set(round(gf.x0, 1) for gf in grid_fields))
            col_lefts = []
            for xv in x0_vals:
                if not col_lefts or xv - col_lefts[-1] > _COL_TOL:
                    col_lefts.append(xv)

            if len(col_lefts) < 3:
                continue

            # Cluster y0 values into row-top positions
            y0_vals = sorted(set(round(gf.y0, 1) for gf in grid_fields))
            row_tops = []
            for yv in y0_vals:
                if not row_tops or yv - row_tops[-1] > _ROW_TOL:
                    row_tops.append(yv)

            if len(row_tops) < 3:
                continue

            # Map each grid field to its (col_idx, row_idx)
            def _snap(val, refs, tol):
                for i, rv in enumerate(refs):
                    if abs(val - rv) <= tol:
                        return i
                return -1

            occupied = set()
            col_x1 = {}  # col_idx -> typical x1
            row_y1 = {}  # row_idx -> typical y1
            for gf in grid_fields:
                ci = _snap(round(gf.x0, 1), col_lefts, _COL_TOL)
                ri = _snap(round(gf.y0, 1), row_tops, _ROW_TOL)
                if ci >= 0 and ri >= 0:
                    occupied.add((ci, ri))
                    col_x1.setdefault(ci, []).append(gf.x1)
                    row_y1.setdefault(ri, []).append(gf.y1)

            # mode x1 per column, mode y1 per row
            for ci in col_x1:
                vals = col_x1[ci]
                col_x1[ci] = Counter(round(v, 1) for v in vals).most_common(1)[0][0]
            for ri in row_y1:
                vals = row_y1[ri]
                row_y1[ri] = Counter(round(v, 1) for v in vals).most_common(1)[0][0]

            # For each column, count how many rows it appears in
            col_coverage = Counter(ci for ci, ri in occupied)
            # Only consider columns present in ≥ 50% of rows
            min_rows = len(row_tops) * 0.5
            valid_cols = {ci for ci, cnt in col_coverage.items()
                          if cnt >= min_rows}

            # For each row, count how many columns it appears in
            row_coverage = Counter(ri for ci, ri in occupied)
            min_cols = len(col_lefts) * 0.4
            valid_rows = {ri for ri, cnt in row_coverage.items()
                          if cnt >= min_cols}

            # Fill gaps: for each valid row × valid column with no field
            ref_field = grid_fields[0]
            for ci in sorted(valid_cols):
                if ci not in col_x1:
                    continue
                for ri in sorted(valid_rows):
                    if ri not in row_y1:
                        continue
                    if (ci, ri) in occupied:
                        continue
                    nx0 = col_lefts[ci]
                    nx1 = col_x1[ci]
                    ny0 = row_tops[ri]
                    ny1 = row_y1[ri]
                    if nx1 - nx0 < 10 or ny1 - ny0 < 5:
                        continue
                    # Check no existing field covers this position
                    overlap = False
                    for ef in fields:
                        if ef.page != pg:
                            continue
                        if ef.field_type not in (FieldType.TEXT, FieldType.COMB):
                            continue
                        x_ov = min(nx1, ef.x1) - max(nx0, ef.x0)
                        y_ov = min(ny1, ef.y1) - max(ny0, ef.y0)
                        if x_ov > 5 and y_ov > 3:
                            overlap = True
                            break
                    if overlap:
                        continue
                    new_f = _dc_replace(
                        ref_field,
                        page=pg,
                        x0=nx0, x1=nx1, y0=ny0, y1=ny1,
                        source='grid_gap_fill',
                        name=f"Cell_{int(nx0)}_{int(ny0)}",
                        label=None, tooltip=None,
                        format_type=None, format_options=None,
                        max_length=None, default_value=None,
                        is_comb=False, comb_count=None,
                        is_image_box=False,
                    )
                    fields.append(new_f)
                    new_count += 1

        if new_count:
            print(f"   [GridColumnFill] Created {new_count} field(s) "
                  f"in missing grid cells")

    # -- Part J: Header entry propagation -----------------------------------

    @staticmethod
    def _propagate_header_entries(
        fields: List[ResolvedField],
        pages: List[PageModel],
    ) -> None:
        """
        On pages with repeating header sections (same labeled headers at
        different Y positions), propagate missing entry fields from the
        most-complete section to incomplete sections.

        Example: P18 of 57603 has 4 identical header sections.  Section 1
        has Building_Address + Number_of_Floors + Building_Number fields,
        but sections 2-4 only have Building_Address.  This method
        replicates the missing fields.

        Algorithm:
          1. Identify header-entry fields (label_entry_cell,
             special_structural_header).
          2. Group them into Y-bands (header sections).
          3. Find the richest section (most fields).
          4. For each poorer section, create copies of missing fields.
        """
        from dataclasses import replace as _dc_replace

        _HEADER_SOURCES = {'label_entry_cell', 'special_structural_header'}
        _BAND_TOL = 25.0  # fields within 25pt Y are same section

        pages_by_num = {p.page_num: p for p in pages}
        new_count = 0

        for page_num in sorted(pages_by_num.keys()):
            # Gather header-type fields on this page
            hdr_fields = [
                f for f in fields
                if f.page == page_num
                and (getattr(f, 'source', '') or '').strip() in _HEADER_SOURCES
                and f.field_type in (FieldType.TEXT, FieldType.COMB)
            ]
            if len(hdr_fields) < 2:
                continue

            # Group into Y-bands
            bands = []
            for f in sorted(hdr_fields, key=lambda f: f.y0):
                mid = (f.y0 + f.y1) / 2
                placed = False
                for band in bands:
                    band_mid = sum((g.y0 + g.y1) / 2 for g in band) / len(band)
                    if abs(mid - band_mid) < _BAND_TOL:
                        band.append(f)
                        placed = True
                        break
                if not placed:
                    bands.append([f])

            if len(bands) < 2:
                continue

            # Find richest band (must have ≥ 3 fields to be a real
            # multi-field header, not coincidentally grouped entries)
            richest = max(bands, key=lambda b: len(b))
            if len(richest) < 3:
                continue

            for band in bands:
                if band is richest or not band:
                    continue

                # Find a matching anchor field between richest and this band
                # (same x-range field that exists in both)
                dy = None
                anchor_rich = None
                anchor_poor = None
                for rf in richest:
                    for bf in band:
                        x_ov = min(rf.x1, bf.x1) - max(rf.x0, bf.x0)
                        min_w = min(rf.x1 - rf.x0, bf.x1 - bf.x0)
                        if min_w > 0 and x_ov / min_w > 0.3:
                            anchor_rich = rf
                            anchor_poor = bf
                            # If the poorer anchor is taller, bottom-align
                            # so fields sit ON the line, not float at top
                            rich_h = rf.y1 - rf.y0
                            poor_h = bf.y1 - bf.y0
                            if poor_h > rich_h + 2:
                                dy = bf.y1 - rf.y1
                                # Also cap the oversized anchor to match
                                bf.y0 = bf.y1 - rich_h
                            else:
                                dy = bf.y0 - rf.y0
                            break
                    if dy is not None:
                        break

                if dy is None:
                    continue

                # Find x-ranges already covered in this band
                for ref in richest:
                    # Check if this x-range is already covered
                    covered = False
                    for existing in band:
                        x_ov = min(ref.x1, existing.x1) - max(ref.x0, existing.x0)
                        min_w = min(ref.x1 - ref.x0, existing.x1 - existing.x0)
                        if min_w > 0 and x_ov / min_w > 0.3:
                            covered = True
                            break
                    if covered:
                        continue

                    new_y0 = ref.y0 + dy
                    new_y1 = ref.y1 + dy

                    # Check no existing field covers this position
                    overlap = False
                    for ef in fields:
                        if ef.page != page_num:
                            continue
                        x_ov = min(ref.x1, ef.x1) - max(ref.x0, ef.x0)
                        y_ov = min(new_y1, ef.y1) - max(new_y0, ef.y0)
                        if x_ov > 5 and y_ov > 3:
                            overlap = True
                            break
                    if overlap:
                        continue

                    new_f = _dc_replace(
                        ref,
                        y0=new_y0,
                        y1=new_y1,
                        name=f"{ref.name}_s{new_count + 1}",
                    )
                    fields.append(new_f)
                    new_count += 1

        if new_count:
            print(f"   [HeaderPropagation] Created {new_count} field(s) "
                  f"in repeated header sections")

    # -- Part K: Grid reconstruction from h-lines + v-lines ----------------

    @staticmethod
    def _fill_grid_from_lines(
        fields: List[ResolvedField],
        pages: List[PageModel],
    ) -> None:
        """
        On pages with clear h-line + v-line grids, fill empty cells
        that other detectors missed (transparent fills, etc.).

        Conservative guards prevent false-positive fills on dense text
        tables that happen to have line grids:
          - ≥50% of grid cells must already have detected fields
          - Cells containing text words are skipped
          - Per-page cap of 20 new fields
          - New fields capped at 30% of already-occupied cell count
        """
        from dataclasses import replace as _dc_replace

        _COL_TOL = 3.0   # clustering tolerance for v-line x positions
        _ROW_TOL = 3.0   # clustering tolerance for h-line y positions
        _MIN_VLINES = 4   # minimum unique v-line positions to qualify
        _MIN_HLINES = 8   # minimum unique h-line positions to qualify
        _MIN_CELL_W = 15  # minimum cell width (pts)
        _MIN_CELL_H = 10  # minimum cell height (pts)
        _MAX_CELL_H = 40  # maximum cell height (skip very tall cells)
        _COLOR_THRESH = 0.15  # CMYK/RGB component threshold for headers
        _MIN_COVERAGE = 0.50  # at least 50% of cells must have fields
        _MAX_COVERAGE = 0.70  # skip if > 70% occupied (few gaps)
        _MAX_PER_PAGE = 20   # safety cap per page
        _MAX_NEW_RATIO = 0.30  # new fields ≤ 30% of occupied count

        # Sources whose presence indicates the grid is already well-
        # covered by dedicated detectors — no need for Part K to fill.
        _GRID_DETECTOR_SOURCES = {
            'tiling_rect_table', 'strict_grid', 'horizontal_line_table',
            'grid_gap_fill', 'grid_fallback',
        }

        pages_by_num = {p.page_num: p for p in pages}
        new_count = 0

        for page_num in sorted(pages_by_num.keys()):
            page = pages_by_num[page_num]

            # ---- collect unique h-line y positions ----
            h_ys = set()
            for hl in page.h_lines:
                y = round(float(hl.get('y', hl.get('top', 0))), 1)
                h_ys.add(y)

            # ---- collect unique v-line x positions ----
            v_xs = set()
            for vl in page.v_lines:
                x = round(
                    (float(vl.get('x0', 0)) + float(vl.get('x1', 0))) / 2,
                    1,
                )
                v_xs.add(x)

            if len(v_xs) < _MIN_VLINES or len(h_ys) < _MIN_HLINES:
                continue

            # ---- cluster into column / row edges ----
            sorted_xs = sorted(v_xs)
            col_edges: list[float] = [sorted_xs[0]]
            for x in sorted_xs[1:]:
                if x - col_edges[-1] > _COL_TOL:
                    col_edges.append(x)

            sorted_ys = sorted(h_ys)
            row_edges: list[float] = [sorted_ys[0]]
            for y in sorted_ys[1:]:
                if y - row_edges[-1] > _ROW_TOL:
                    row_edges.append(y)

            if len(col_edges) < _MIN_VLINES or len(row_edges) < _MIN_HLINES:
                continue

            # ---- gather page data ----
            page_fields = [f for f in fields if f.page == page_num]
            rects = page.rects or []

            # Skip pages already well-covered by grid-aware detectors.
            # If dedicated grid detectors (tiling_rect_table, strict_grid,
            # etc.) already placed fields, Part K would only add
            # redundant or incorrect fills.
            grid_det_count = sum(
                1 for pf in page_fields
                if (getattr(pf, 'source', '') or '').strip()
                in _GRID_DETECTOR_SOURCES
                and pf.field_type in (FieldType.TEXT, FieldType.COMB)
            )
            if grid_det_count >= 6:
                continue

            ref_field = next(
                (f for f in page_fields
                 if f.field_type in (FieldType.TEXT, FieldType.COMB)),
                None,
            )
            if ref_field is None:
                continue

            # ---- two-pass: enumerate eligible cells, measure coverage ----
            # Pass 1: collect eligible cells and classify as occupied/empty
            eligible_cells: list[tuple[float, float, float, float]] = []
            occupied_cells = 0

            for i in range(len(col_edges) - 1):
                cx0 = col_edges[i]
                cx1 = col_edges[i + 1]
                if cx1 - cx0 < _MIN_CELL_W:
                    continue

                for j in range(len(row_edges) - 1):
                    cy0 = row_edges[j]
                    cy1 = row_edges[j + 1]
                    cell_h = cy1 - cy0
                    if cell_h < _MIN_CELL_H or cell_h > _MAX_CELL_H:
                        continue

                    # -- skip colored header cells --
                    is_header = False
                    for r in rects:
                        rx0 = float(r.get('x0', 0))
                        ry0 = float(r.get('top', r.get('y0', 0)))
                        rx1 = float(r.get('x1', 0))
                        ry1 = float(r.get('bottom', r.get('y1', 0)))
                        if (rx0 <= cx0 + 2 and rx1 >= cx1 - 2
                                and ry0 <= cy0 + 2 and ry1 >= cy1 - 2):
                            fill = r.get('non_stroking_color')
                            if fill and isinstance(fill, (list, tuple)):
                                vals = [abs(float(v)) for v in fill]
                                if len(vals) >= 3 and max(vals) > _COLOR_THRESH:
                                    is_header = True
                                    break
                    if is_header:
                        continue

                    # -- check if any existing field overlaps --
                    has_field = False
                    cell_w = cx1 - cx0
                    for ef in page_fields:
                        if ef.field_type == FieldType.CHECKBOX:
                            continue
                        x_ov = min(cx1, ef.x1) - max(cx0, ef.x0)
                        y_ov = min(cy1, ef.y1) - max(cy0, ef.y0)
                        if x_ov > cell_w * 0.4 and y_ov > 3:
                            has_field = True
                            break

                    if has_field:
                        occupied_cells += 1
                    else:
                        eligible_cells.append((cx0, cy0, cx1, cy1))

            total_cells = occupied_cells + len(eligible_cells)
            if total_cells < 12:
                continue

            coverage = occupied_cells / total_cells if total_cells > 0 else 0

            # Coverage gate: skip if too few fields (not a real form table)
            # or if too many fields (few gaps to fill)
            if coverage < _MIN_COVERAGE or coverage > _MAX_COVERAGE:
                continue

            # Ratio cap: don't add more than 30% of occupied count
            ratio_cap = max(1, int(occupied_cells * _MAX_NEW_RATIO))
            page_cap = min(_MAX_PER_PAGE, ratio_cap)

            # Pass 2: create fields in truly empty cells
            page_new = 0
            for (cx0, cy0, cx1, cy1) in eligible_cells:
                if page_new >= page_cap:
                    break

                # Skip cells that contain text (data/label cells)
                cell_words = page.get_words_in_bbox(
                    (cx0 + 2, cy0 + 1, cx1 - 2, cy1 - 1), 0.3)
                if cell_words:
                    continue

                # Re-check overlap (fields may have been added this pass)
                overlap = False
                for ef in page_fields:
                    x_ov = min(cx1, ef.x1) - max(cx0, ef.x0)
                    y_ov = min(cy1, ef.y1) - max(cy0, ef.y0)
                    if ef.field_type == FieldType.CHECKBOX:
                        if x_ov > 0 and y_ov > 0:
                            overlap = True
                            break
                    elif x_ov > 5 and y_ov > 3:
                        overlap = True
                        break
                if overlap:
                    continue

                new_f = _dc_replace(
                    ref_field,
                    page=page_num,
                    x0=cx0, x1=cx1, y0=cy0, y1=cy1,
                    source='grid_line_fill',
                    name=f"Cell_{int(cx0)}_{int(cy0)}",
                    label=None, tooltip=None,
                    format_type=None, format_options=None,
                    max_length=None, default_value=None,
                    is_comb=False, comb_count=None,
                    is_image_box=False,
                )
                fields.append(new_f)
                page_fields.append(new_f)
                page_new += 1
                new_count += 1

        if new_count:
            print(f"   [GridLineFill] Created {new_count} field(s) "
                  f"from grid lines")
