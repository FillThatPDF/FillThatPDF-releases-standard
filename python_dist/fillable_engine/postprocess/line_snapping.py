"""
Post-processor: Snap field edges to nearby form lines.

Runs AFTER the resolver commits fields and BEFORE the writer creates widgets.
Snaps field edges to the nearest horizontal/vertical page line within a
tolerance — in BOTH directions (extend toward or contract back to a line).
Uses pair-based snapping: for the top/bottom edges it finds the best
**pair** of distinct lines (one for each edge) so that the resulting
height is valid.  This ensures fields sit precisely between form lines
rather than floating above or below them.
"""

from collections import defaultdict
from typing import List, Optional, Tuple

from ..models import ResolvedField, FieldType, PageModel

# Maximum distance (in points) between a field edge and a page line
# for snapping to kick in.
# H-lines (top/bottom edges): conservative — prevents fields from
#   jumping between adjacent rows.
# V-lines (left/right edges): more generous — form labels often have
#   left padding so the text x0 can be 10-18 pt inset from the column
#   boundary.  A wider tolerance lets the snapper align the field to
#   the actual column edge.
SNAP_TOLERANCE_Y = 10.0
SNAP_TOLERANCE_X = 10.0

# Extended tolerance for thin fields.  Very thin fields (from
# empty_box detection etc.) can sit mid-cell — e.g. a 4pt-tall field
# whose bottom edge is on an H-line but whose top edge is 14pt from
# the H-line above.  The normal 10pt tolerance misses the cell
# boundary; the extended tolerance finds it and pair-snaps the field
# to fill the correct grid cell.
THIN_FIELD_THRESHOLD = 10.0    # fields thinner than this get extended search
EXTENDED_SNAP_TOL_Y = 20.0     # wider tolerance for the pair search

# Small tolerance (pts) for the X/Y overlap check between a field and
# a line.  Form lines and field edges may be off by a fraction of a
# point due to rounding, so we add this fuzz when deciding whether a
# line is "horizontally overlapping" a field.
OVERLAP_FUZZ = 3.0

# Minimum field height / width after snapping — never shrink below this.
MIN_HEIGHT = 8.0
MIN_WIDTH = 10.0

# Sources that were detected sitting ON a horizontal line.  These get
# bottom-priority Y-snapping: only the bottom edge snaps to the nearest
# line, and the detection height is preserved.  All other sources (grid,
# table, empty_box, etc.) keep full pair-based Y-snapping so they can
# fill their enclosing cells.
#
# ``label_entry_below`` is included because these fields are positioned
# below their label text by design — pair-based snapping would expand
# them upward past the label to fill the entire cell, covering it.
LINE_BASED_SOURCES = {
    'form_line_presplit', 'form_line_segment',
    'signature', 'date',
    'special_label', 'special_structural_header',
    'inline_fill_underline',
    'general_underscore', 'inline_label',
    'label_below_underscore',
    'embedded_underscore',
    'label_entry_below',
}


class LineSnapper:
    """Snap field edges to nearby horizontal and vertical form lines."""

    def process(
        self,
        fields: List[ResolvedField],
        pages: List[PageModel],
    ) -> List[ResolvedField]:
        """Snap field edges in-place.  Returns the same *fields* list."""
        pages_by_num = {p.page_num: p for p in pages}

        candidates = [
            f for f in fields
            if f.field_type in (FieldType.TEXT, FieldType.COMB, FieldType.SIGNATURE)
        ]
        if not candidates:
            return fields

        by_page: dict[int, List[ResolvedField]] = defaultdict(list)
        for f in candidates:
            by_page[f.page].append(f)

        for page_num, page_fields in by_page.items():
            page = pages_by_num.get(page_num)
            if page is None:
                continue
            self._snap_page(page_fields, page)

        # Post-snap overlap de-duplication.
        # After snapping, fields from different detectors may now share the
        # same line pair (y0, y1) with heavily overlapping x-ranges.  Keep
        # the wider field and discard the narrower duplicate.
        fields = self._dedup_post_snap(fields)

        return fields

    # -- internals -----------------------------------------------------------

    @staticmethod
    def _dedup_post_snap(fields: List[ResolvedField]) -> List[ResolvedField]:
        """Remove smaller text fields that heavily overlap a larger one.

        After pair-based snapping, two fields from different detectors can
        land on the exact same (y0, y1) line pair with nearly identical
        x-ranges.  The narrower one is a detection artefact — remove it.
        """
        DEDUP_TYPES = (FieldType.TEXT, FieldType.COMB, FieldType.SIGNATURE)
        to_remove: set = set()

        by_page: dict[int, List[Tuple[int, ResolvedField]]] = defaultdict(list)
        for idx, f in enumerate(fields):
            if f.field_type in DEDUP_TYPES:
                by_page[f.page].append((idx, f))

        for _pg, page_fields in by_page.items():
            n = len(page_fields)
            for a in range(n):
                i_a, f_a = page_fields[a]
                if i_a in to_remove:
                    continue
                for b in range(a + 1, n):
                    i_b, f_b = page_fields[b]
                    if i_b in to_remove:
                        continue

                    # Intersection
                    ox0 = max(f_a.x0, f_b.x0)
                    oy0 = max(f_a.y0, f_b.y0)
                    ox1 = min(f_a.x1, f_b.x1)
                    oy1 = min(f_a.y1, f_b.y1)
                    if ox1 <= ox0 or oy1 <= oy0:
                        continue

                    ov_area = (ox1 - ox0) * (oy1 - oy0)
                    area_a = max(1, (f_a.x1 - f_a.x0) * (f_a.y1 - f_a.y0))
                    area_b = max(1, (f_b.x1 - f_b.x0) * (f_b.y1 - f_b.y0))
                    min_area = min(area_a, area_b)

                    if ov_area / min_area > 0.50:
                        # Discard the smaller (narrower) field
                        if area_a >= area_b:
                            to_remove.add(i_b)
                        else:
                            to_remove.add(i_a)

        if to_remove:
            return [f for i, f in enumerate(fields) if i not in to_remove]
        return fields

    @staticmethod
    def _snap_page(page_fields: List[ResolvedField], page: PageModel) -> None:
        """Snap all fields on one page to that page's lines."""
        h_lines = page.h_lines
        v_lines = page.v_lines

        for f in page_fields:
            # --- horizontal-line snapping (affects y0 / y1) -----------------
            field_h = f.y1 - f.y0
            field_source = (f.source or '').strip()
            is_line_based = field_source in LINE_BASED_SOURCES

            # Collect bottom-edge candidate snap lines (needed for both modes).
            bot_candidates: List[Tuple[float, float]] = []   # (line_y, dist)

            for hl in h_lines:
                line_y = hl.get('y', (hl.get('top', 0) + hl.get('bottom', 0)) / 2)
                line_x0 = hl.get('x0', 0)
                line_x1 = hl.get('x1', 0)

                # The field must horizontally overlap the line (with fuzz)
                if f.x1 < line_x0 - OVERLAP_FUZZ or f.x0 > line_x1 + OVERLAP_FUZZ:
                    continue

                dist_bot = abs(f.y1 - line_y)
                if dist_bot <= SNAP_TOLERANCE_Y:
                    bot_candidates.append((line_y, dist_bot))

            bot_candidates.sort(key=lambda x: x[1])

            if is_line_based:
                # ---- Bottom-priority snapping for line-based fields --------
                # These fields were detected with y1 sitting ON a horizontal
                # line.  Snap y1 to the nearest line (fine-tune), then
                # preserve the original detection height by adjusting y0.
                # Do NOT pair-snap — independent top-edge snapping would
                # change the height arbitrarily based on nearby grid lines.
                if bot_candidates:
                    best_bot = bot_candidates[0][0]
                    f.y1 = best_bot
                    f.y0 = f.y1 - field_h   # preserve original height
            else:
                # ---- Pair-based snapping for grid/table/box fields ---------
                # These fields should fill their enclosing cells, so snap
                # both top and bottom edges independently to find the best
                # pair of lines.
                snap_tol_y = SNAP_TOLERANCE_Y
                if field_h < THIN_FIELD_THRESHOLD:
                    snap_tol_y = EXTENDED_SNAP_TOL_Y

                # Collect top-edge candidates (only for pair-based mode)
                top_candidates: List[Tuple[float, float]] = []
                for hl in h_lines:
                    line_y = hl.get('y', (hl.get('top', 0) + hl.get('bottom', 0)) / 2)
                    line_x0 = hl.get('x0', 0)
                    line_x1 = hl.get('x1', 0)
                    if f.x1 < line_x0 - OVERLAP_FUZZ or f.x0 > line_x1 + OVERLAP_FUZZ:
                        continue
                    dist_top = abs(f.y0 - line_y)
                    if dist_top <= snap_tol_y:
                        top_candidates.append((line_y, dist_top))
                    # Also re-check bot with extended tolerance for thin fields
                    if snap_tol_y > SNAP_TOLERANCE_Y:
                        dist_bot = abs(f.y1 - line_y)
                        if dist_bot <= snap_tol_y and dist_bot > SNAP_TOLERANCE_Y:
                            bot_candidates.append((line_y, dist_bot))

                top_candidates.sort(key=lambda x: x[1])
                bot_candidates.sort(key=lambda x: x[1])

                snapped_y = False
                best_pair_y = None
                best_disp_y = float('inf')
                for top_y, d_top in top_candidates:
                    for bot_y, d_bot in bot_candidates:
                        if bot_y > top_y and bot_y - top_y >= MIN_HEIGHT:
                            disp = d_top + d_bot
                            if disp < best_disp_y:
                                best_disp_y = disp
                                best_pair_y = (top_y, bot_y)
                if best_pair_y is not None:
                    f.y0, f.y1 = best_pair_y
                    snapped_y = True

                if not snapped_y:
                    if bot_candidates:
                        best_bot = bot_candidates[0][0]
                        if best_bot - f.y0 >= MIN_HEIGHT:
                            f.y1 = best_bot
                    if top_candidates:
                        best_top = top_candidates[0][0]
                        if f.y1 - best_top >= MIN_HEIGHT:
                            f.y0 = best_top

            # --- vertical-line snapping (affects x0 / x1) -------------------
            left_candidates: List[Tuple[float, float]] = []
            right_candidates: List[Tuple[float, float]] = []

            for vl in v_lines:
                line_x = (vl.get('x0', 0) + vl.get('x1', 0)) / 2
                line_top = vl.get('top', 0)
                line_bot = vl.get('bottom', 0)

                if f.y1 < line_top - OVERLAP_FUZZ or f.y0 > line_bot + OVERLAP_FUZZ:
                    continue

                dist_left = abs(f.x0 - line_x)
                if dist_left <= SNAP_TOLERANCE_X:
                    left_candidates.append((line_x, dist_left))

                dist_right = abs(f.x1 - line_x)
                if dist_right <= SNAP_TOLERANCE_X:
                    right_candidates.append((line_x, dist_right))

            left_candidates.sort(key=lambda x: x[1])
            right_candidates.sort(key=lambda x: x[1])

            # Find best pair (left < right, valid width) — minimum displacement
            snapped_x = False
            best_pair_x = None
            best_disp_x = float('inf')
            for left_x, d_left in left_candidates:
                for right_x, d_right in right_candidates:
                    if right_x > left_x and right_x - left_x >= MIN_WIDTH:
                        disp = d_left + d_right
                        if disp < best_disp_x:
                            best_disp_x = disp
                            best_pair_x = (left_x, right_x)
            if best_pair_x is not None:
                f.x0, f.x1 = best_pair_x
                snapped_x = True

            if not snapped_x:
                if left_candidates:
                    best_left = left_candidates[0][0]
                    if f.x1 - best_left >= MIN_WIDTH:
                        f.x0 = best_left
                if right_candidates:
                    best_right = right_candidates[0][0]
                    if best_right - f.x0 >= MIN_WIDTH:
                        f.x1 = best_right

            # --- source-line clamping (post X-snap) ---------------------------
            # Fields from form_line / inline_underline detectors carry the
            # original source line's x0/x1.  After snapping to vertical grid
            # lines the field may have stretched beyond the line it came from.
            # Clamp it back so the field never exceeds its source line bounds.
            if f.source_line_x0 is not None and f.source_line_x1 is not None:
                if f.x0 < f.source_line_x0 - 1.0:
                    f.x0 = f.source_line_x0
                if f.x1 > f.source_line_x1 + 1.0:
                    f.x1 = f.source_line_x1
                # Safety net: if clamping made the field too narrow, restore
                # to full source line bounds
                if f.x1 - f.x0 < MIN_WIDTH:
                    f.x0 = f.source_line_x0
                    f.x1 = f.source_line_x1
