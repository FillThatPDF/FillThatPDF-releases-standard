"""
Post-processor: Remove fields from informational table columns.

Multi-column tables often mix fillable columns (with underscored lines,
checkboxes, or empty cells for user input) and informational columns (with
pre-printed text describing requirements, conditions, or standards).
Detectors can't always distinguish the two, so this post-processor:

  1. Identifies the page's column structure from tall V-lines.
  2. Groups pages that share the same column layout (multi-page tables).
  3. Classifies each column as *fillable* (has underscore fill indicators
     or checkboxes) or *informational* (dense text distributed across many
     rows, with no fill indicators).  For multi-page tables, word/zone
     counts are aggregated across all pages in the group so that sparse
     pages inherit the classification of the group.
  4. Removes text fields whose centre falls in an informational column.
  5. Clips text fields that extend from a fillable column into an
     informational column at the V-line boundary.

Runs AFTER GridGapFill (so gap-fill fields exist) and BEFORE
HeightStandardizer and LabelTrimmer.
"""

from collections import defaultdict
from typing import List, Tuple, Dict, Set

from ..models import ResolvedField, FieldType, PageModel


# --- tunables ---------------------------------------------------------------

# V-line must span at least this many points vertically to be
# considered a column boundary (filters out header decorations).
MIN_VLINE_HEIGHT = 80.0

# Need at least this many column boundaries to activate (≥2 columns).
MIN_COL_BOUNDARIES = 3

# Clustering tolerance for V-line X positions.
COL_CLUSTER_TOL = 5.0

# A column needs BOTH of these to be classified as "informational":
#   - at least MIN_TEXT_WORDS non-fill words, AND
#   - text distributed across at least MIN_TEXT_Y_ZONES vertical zones.
# For multi-page tables, these thresholds are applied PER-PAGE using
# the average counts across the group.
MIN_TEXT_WORDS = 15
MIN_TEXT_Y_ZONES = 9

# Y-zone granularity: words within this vertical distance are in the
# same zone (prevents counting multi-line paragraphs as many zones).
Y_ZONE_GRANULARITY = 12.0

# Minimum word length (characters) to count as a "real" word.
MIN_WORD_LEN = 2

# Minimum underscore length to count as a fill indicator.
MIN_FILL_LEN = 3

# Minimum field width after clipping — narrower fields are removed.
MIN_CLIPPED_WIDTH = 15.0

# Column layout matching tolerance for grouping pages.
COL_LAYOUT_TOL = 3.0

# Minimum pages in a group to enable cross-page aggregation.
MIN_GROUP_PAGES = 2


class TextColumnFilter:
    """Remove/clip fields that fall in informational (non-fillable) columns."""

    def process(
        self,
        fields: List[ResolvedField],
        pages: List[PageModel],
    ) -> List[ResolvedField]:
        to_remove: Set[int] = set()
        removed = 0
        clipped = 0

        # Pre-compute informational columns per page, using cross-page
        # aggregation for multi-page tables.
        page_info_cols = self._compute_all_info_cols(pages, fields)

        for page in pages:
            info_cols = page_info_cols.get(page.page_num, [])
            if not info_cols:
                continue

            for idx, f in enumerate(fields):
                if f.page != page.page_num:
                    continue
                # Never remove checkboxes — they are explicit user inputs
                if f.field_type == FieldType.CHECKBOX:
                    continue

                # Protect high-confidence table-structure sources.
                # These fields were explicitly detected from table geometry
                # (H-line splits, cell analysis) and should not be removed
                # just because they sit in a text-heavy column (e.g. "$:"
                # dollar-amount entry fields next to label text).
                if f.source in (
                    'split_cell_multi_row',
                    'label_entry_cell',
                    'label_entry_below',
                ):
                    continue

                # CLIP FIRST, then check if the remaining field is viable.
                # This handles fields that straddle fillable/informational
                # column boundaries — the resolver may have committed a wide
                # candidate (e.g. HLT_subrow spanning multiple columns) that
                # displaced a narrower column-local candidate.  Clipping
                # keeps the fillable portion instead of removing everything.
                orig_x0, orig_x1 = f.x0, f.x1

                for cx0, cx1 in info_cols:
                    # Field extends RIGHT into info column
                    if f.x1 > cx0 + 2 and f.x0 < cx0:
                        f.x1 = cx0
                        clipped += 1
                    # Field extends LEFT into info column
                    if f.x0 < cx1 - 2 and f.x1 > cx1:
                        f.x0 = cx1
                        clipped += 1

                # After clipping, check if field centre is in an info column
                fcx = (f.x0 + f.x1) / 2
                in_info = any(
                    cx0 - 2 <= fcx <= cx1 + 2
                    for cx0, cx1 in info_cols
                )

                # Also remove if clipping made the field too narrow
                too_narrow = (f.x1 - f.x0) < MIN_CLIPPED_WIDTH

                if in_info or too_narrow:
                    # Restore original coords (field will be removed entirely)
                    f.x0, f.x1 = orig_x0, orig_x1
                    to_remove.add(idx)
                    removed += 1

        if to_remove:
            fields = [f for i, f in enumerate(fields) if i not in to_remove]

        if removed or clipped:
            print(f"   TextColumnFilter: removed {removed}, "
                  f"clipped {clipped} in informational columns")

        return fields

    # --------------------------------------------------------------------- #

    def _compute_all_info_cols(
        self,
        pages: List[PageModel],
        fields: List[ResolvedField],
    ) -> Dict[int, List[Tuple[float, float]]]:
        """Compute informational columns for all pages.

        Groups pages that share the same column layout (multi-page tables)
        and aggregates word/zone counts across the group before classifying.
        """
        # Step 1: get column boundaries per page
        page_cols: Dict[int, List[float]] = {}
        for page in pages:
            col_xs = self._get_main_column_xs(page)
            if len(col_xs) >= MIN_COL_BOUNDARIES:
                page_cols[page.page_num] = col_xs

        if not page_cols:
            return {}

        # Step 2: group pages by column layout (same X positions ± tolerance)
        groups: List[List[int]] = []  # list of [page_num, ...]
        for pnum, cols in sorted(page_cols.items()):
            placed = False
            for group in groups:
                ref_cols = page_cols[group[0]]
                if self._layouts_match(cols, ref_cols):
                    group.append(pnum)
                    placed = True
                    break
            if not placed:
                groups.append([pnum])

        # Step 3: for each group, classify columns
        result: Dict[int, List[Tuple[float, float]]] = {}

        page_map = {p.page_num: p for p in pages}

        for group in groups:
            ref_cols = page_cols[group[0]]
            n_cols = len(ref_cols) - 1  # number of column intervals

            if len(group) < MIN_GROUP_PAGES:
                # Single page — use per-page classification
                for pnum in group:
                    page = page_map[pnum]
                    info = self._find_informational_columns_single(
                        page, ref_cols, fields
                    )
                    if info:
                        result[pnum] = info
            else:
                # Multi-page group — aggregate across pages
                info = self._find_informational_columns_group(
                    group, ref_cols, page_map, fields
                )
                if info:
                    for pnum in group:
                        result[pnum] = info

        return result

    # --------------------------------------------------------------------- #

    def _find_informational_columns_single(
        self,
        page: PageModel,
        col_xs: List[float],
        fields: List[ResolvedField],
    ) -> List[Tuple[float, float]]:
        """Classify columns for a single page (no cross-page aggregation)."""
        info: List[Tuple[float, float]] = []
        for i in range(len(col_xs) - 1):
            cx0, cx1 = col_xs[i], col_xs[i + 1]
            if cx1 - cx0 < 15:
                continue
            if not self._column_is_fillable(cx0, cx1, page, fields):
                info.append((cx0, cx1))
        return info

    # --------------------------------------------------------------------- #

    def _find_informational_columns_group(
        self,
        group: List[int],
        col_xs: List[float],
        page_map: Dict[int, PageModel],
        fields: List[ResolvedField],
    ) -> List[Tuple[float, float]]:
        """Classify columns using aggregated data from all pages in group.

        A column is informational if:
          - NO page in the group has fill indicators or fillable sources, AND
          - The AVERAGE word count across group pages >= MIN_TEXT_WORDS, AND
          - The AVERAGE zone count across group pages >= MIN_TEXT_Y_ZONES.
        """
        info: List[Tuple[float, float]] = []

        for i in range(len(col_xs) - 1):
            cx0, cx1 = col_xs[i], col_xs[i + 1]
            if cx1 - cx0 < 15:
                continue

            # Check if ANY page has fill indicators for this column
            any_fillable = False
            total_words = 0
            total_zones = 0

            for pnum in group:
                page = page_map[pnum]

                # Check fill indicators on this page
                if self._column_has_fill_signal(cx0, cx1, page, fields):
                    any_fillable = True
                    break

                # Accumulate word/zone counts
                words, zones = self._count_words_and_zones(cx0, cx1, page)
                total_words += words
                total_zones += zones

            if any_fillable:
                continue  # column is fillable — don't mark as info

            # Average counts across group
            n_pages = len(group)
            avg_words = total_words / n_pages
            avg_zones = total_zones / n_pages

            if avg_words >= MIN_TEXT_WORDS and avg_zones >= MIN_TEXT_Y_ZONES:
                info.append((cx0, cx1))

        return info

    # --------------------------------------------------------------------- #

    @staticmethod
    def _layouts_match(cols_a: List[float], cols_b: List[float]) -> bool:
        """Check if two column layouts match (same number of columns,
        same X positions within tolerance)."""
        if len(cols_a) != len(cols_b):
            return False
        return all(
            abs(a - b) <= COL_LAYOUT_TOL
            for a, b in zip(cols_a, cols_b)
        )

    # --------------------------------------------------------------------- #

    @staticmethod
    def _get_main_column_xs(page: PageModel) -> List[float]:
        """Cluster X positions of tall V-lines (column separators)."""
        xs: List[float] = []
        for vl in page.v_lines:
            height = vl.get('bottom', 0) - vl.get('top', 0)
            if height < MIN_VLINE_HEIGHT:
                continue
            x = (vl.get('x0', 0) + vl.get('x1', 0)) / 2
            xs.append(x)

        if not xs:
            return []

        # Cluster nearby X values (same logic as GridGapFill)
        xs = sorted(xs)
        clusters: List[List[float]] = [[xs[0]]]
        for v in xs[1:]:
            if v - clusters[-1][-1] <= COL_CLUSTER_TOL:
                clusters[-1].append(v)
            else:
                clusters.append([v])
        return [sum(c) / len(c) for c in clusters]

    # --------------------------------------------------------------------- #

    @staticmethod
    def _column_has_fill_signal(
        cx0: float, cx1: float,
        page: PageModel,
        fields: List[ResolvedField],
    ) -> bool:
        """Check if a column has explicit fillability signals (underscore
        text, checkboxes, or detector sources that indicate fillable content).
        """
        # Search the entire column band for words
        all_words = page.get_words_in_bbox(
            (cx0, 0, cx1, page.height), 0.2,
        )

        # Check for underscore fill indicators
        for w in all_words:
            txt = w.get('text', '')
            if len(txt) >= MIN_FILL_LEN and txt.count('_') >= len(txt) * 0.5:
                return True

        # Check for checkboxes in this column
        for f in fields:
            if (f.page == page.page_num
                    and f.field_type == FieldType.CHECKBOX
                    and cx0 <= (f.x0 + f.x1) / 2 <= cx1):
                return True

        # Check for fillable detector sources.
        # split_cell_multi_row / label_entry_cell specifically detect
        # fill areas next to labels (e.g. "$:" dollar-amount entry cells).
        # Their presence in a column is a strong signal the column is
        # fillable, not informational.
        fillable_sources = {
            'embedded_underscore', 'date', 'underscore_field',
            'split_cell_multi_row', 'label_entry_cell',
        }
        for f in fields:
            if (f.page == page.page_num
                    and f.source in fillable_sources
                    and cx0 <= (f.x0 + f.x1) / 2 <= cx1):
                return True

        return False

    # --------------------------------------------------------------------- #

    @staticmethod
    def _count_words_and_zones(
        cx0: float, cx1: float,
        page: PageModel,
    ) -> Tuple[int, int]:
        """Count text words and distinct Y-zones in a column."""
        all_words = page.get_words_in_bbox(
            (cx0, 0, cx1, page.height), 0.2,
        )

        text_words = [
            w for w in all_words
            if len(w.get('text', '')) >= MIN_WORD_LEN
        ]
        n_words = len(text_words)

        y_zones: Set[int] = set()
        for w in text_words:
            y = w.get('top', w.get('y0', 0))
            y_zones.add(int(y / Y_ZONE_GRANULARITY))
        n_zones = len(y_zones)

        return n_words, n_zones

    # --------------------------------------------------------------------- #

    @staticmethod
    def _column_is_fillable(
        cx0: float, cx1: float,
        page: PageModel,
        fields: List[ResolvedField],
    ) -> bool:
        """Legacy per-page classification (used for single-page groups).

        A column is fillable if it has underscore fill indicators or
        checkboxes.  Otherwise, if it has dense text distributed across
        many rows, it's informational.
        """
        # Search the entire column band for words
        all_words = page.get_words_in_bbox(
            (cx0, 0, cx1, page.height), 0.2,
        )

        # Check for underscore fill indicators
        for w in all_words:
            txt = w.get('text', '')
            if len(txt) >= MIN_FILL_LEN and txt.count('_') >= len(txt) * 0.5:
                return True  # has fill indicators → fillable

        # Check for checkboxes in this column
        for f in fields:
            if (f.page == page.page_num
                    and f.field_type == FieldType.CHECKBOX
                    and cx0 <= (f.x0 + f.x1) / 2 <= cx1):
                return True

        # Check for fillable detector sources (same set as _column_has_fill_signal)
        fillable_sources = {
            'embedded_underscore', 'date', 'underscore_field',
            'split_cell_multi_row', 'label_entry_cell',
        }
        for f in fields:
            if (f.page == page.page_num
                    and f.source in fillable_sources
                    and cx0 <= (f.x0 + f.x1) / 2 <= cx1):
                return True

        # Count non-fill, non-trivial words and their vertical zones
        text_words = [
            w for w in all_words
            if len(w.get('text', '')) >= MIN_WORD_LEN
        ]
        n_text_words = len(text_words)

        # Count distinct vertical zones (Y positions grouped by granularity)
        y_zones: Set[int] = set()
        for w in text_words:
            y = w.get('top', w.get('y0', 0))
            y_zones.add(int(y / Y_ZONE_GRANULARITY))
        n_y_zones = len(y_zones)

        # Column is informational only if BOTH conditions are met:
        if n_text_words >= MIN_TEXT_WORDS and n_y_zones >= MIN_TEXT_Y_ZONES:
            return False  # dense, distributed text → informational

        # Sparse, concentrated, or empty → fillable
        return True
