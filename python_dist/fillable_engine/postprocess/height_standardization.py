"""
Post-processor: Field height standardisation.

Runs AFTER the resolver commits fields and BEFORE the writer creates widgets.

Two functions:
  A. **Global single-line height override** — when the user enables
     ``enable_global_single_line_height``, ALL single-line text fields are
     resized to the configured target height (bottom-anchored).  Dense-grid
     fields and multiline fields are excluded.
     Ported from v23 ``_apply_global_single_line_height``.

  B. **Y-band normalisation** — fields in the same horizontal row are grown
     to the mode height of that row so the result looks uniform.
     Ported from v23 ``_normalize_grid_column_field_heights``.
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

        for _page_num, page_fields in by_page.items():
            self._normalise_page(page_fields)

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
    def _normalise_page(page_fields: List[ResolvedField]) -> None:
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

            # Determine the mode height (rounded to 0.5pt for stability)
            height_counts: Counter = Counter()
            for f in band:
                h = round((f.y1 - f.y0) * 2) / 2  # nearest 0.5pt
                height_counts[h] += 1

            mode_height = height_counts.most_common(1)[0][0]

            # Ensure we never target below the standard minimum
            target_height = max(mode_height, STANDARD_FIELD_HEIGHT)

            for f in band:
                current_h = f.y1 - f.y0
                if current_h < target_height:
                    # Grow upward to reach target (bottom-anchor).
                    # y1 was snapped to a form line by LineSnapper; moving
                    # it would detach the field from its line.
                    f.y0 = f.y1 - target_height
