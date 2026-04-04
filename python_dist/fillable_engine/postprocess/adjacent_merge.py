"""
Post-processor: Merge horizontally adjacent fields from the same source.

Some PDFs draw table lines with segmented strokes, breaking each H-line
at every column breakpoint — even columns that don't exist in that row.
Detectors treat each segment as a separate cell boundary, producing
multiple narrow fields where only one should exist.

This post-processor finds pairs of text fields that are:
  • From the same source
  • Horizontally adjacent (x1 of one ≈ x0 of the next, gap ≤ 3pt)
  • On the same Y band (matching y0 and y1 within 2pt)
  • NOT separated by a real V-line on the page

and merges them into a single field, preserving the leftmost x0 and the
rightmost x1.

Runs AFTER LineSnapper (fields are snapped) and BEFORE HeightStandardizer
so merged fields are normalised together.
"""

from collections import defaultdict
from typing import List, Optional

from ..models import ResolvedField, FieldType, PageModel


# --- tunables -----------------------------------------------------------

MERGE_GAP_X = 3.0       # Max horizontal gap to merge (pt)
MERGE_TOL_Y = 2.0       # Max Y-coordinate difference to be same band
VLINE_CHECK_TOL = 2.0    # Search radius for blocking V-lines

# Junction-dot detection tunables
_MAX_DOT_SIZE = 3.0      # Max width/height for a rect to be a junction dot
_DOT_GROUP_TOL = 1.5     # X-tolerance for grouping dots into a column
_MIN_DOTS_FOR_BOUNDARY = 2  # Min dots at same X to constitute a boundary


class AdjacentFieldMerger:
    """Merge horizontally adjacent same-source fields with no V-line between."""

    def process(
        self,
        fields: List[ResolvedField],
        pages: List[PageModel],
    ) -> List[ResolvedField]:
        pages_by_num = {p.page_num: p for p in pages}
        merged_count = 0

        # Group by page
        by_page: dict = defaultdict(list)
        other: List[ResolvedField] = []
        for f in fields:
            if f.field_type in (FieldType.TEXT, FieldType.COMB):
                by_page[f.page].append(f)
            else:
                other.append(f)

        result: List[ResolvedField] = list(other)

        for pg_num in sorted(by_page.keys()):
            page = pages_by_num.get(pg_num)
            page_fields = by_page[pg_num]
            merged = self._merge_page(page_fields, page)
            merged_count += len(page_fields) - len(merged)
            result.extend(merged)

        if merged_count:
            print(f"   AdjacentFieldMerger: merged {merged_count} fragments")

        return result

    # -----------------------------------------------------------------
    @staticmethod
    def _find_junction_boundaries(page: PageModel) -> List[float]:
        """Find column-boundary X positions from small junction-dot rects.

        Some PDFs draw table intersections as tiny filled squares (typ.
        ≤ 3 pt) at each column/row junction instead of full vertical
        lines.  When 2+ such dots share the same X coordinate they
        mark a structural column boundary that should block merging.
        """
        rects = getattr(page, 'rects', [])
        dot_xs: List[float] = []
        for r in rects:
            w = r.get('width', 0)
            h = r.get('height', 0)
            if not w:
                w = abs(r.get('x1', 0) - r.get('x0', 0))
            if not h:
                h = abs(r.get('y1', 0) - r.get('y0', 0))
            if w > _MAX_DOT_SIZE or h > _MAX_DOT_SIZE:
                continue
            dot_xs.append(r.get('x0', 0))

        if len(dot_xs) < _MIN_DOTS_FOR_BOUNDARY:
            return []

        # Group dot X positions within tolerance
        dot_xs.sort()
        groups: List[List[float]] = [[dot_xs[0]]]
        for x in dot_xs[1:]:
            if x - groups[-1][-1] <= _DOT_GROUP_TOL:
                groups[-1].append(x)
            else:
                groups.append([x])

        # X positions with enough dots are column boundaries
        return [sum(g) / len(g) for g in groups
                if len(g) >= _MIN_DOTS_FOR_BOUNDARY]

    # -----------------------------------------------------------------
    def _merge_page(
        self,
        fields: List[ResolvedField],
        page: Optional[PageModel] = None,
    ) -> List[ResolvedField]:
        """Merge adjacent fields on a single page."""
        if len(fields) < 2:
            return fields

        # Pre-compute junction column boundaries from small rects
        junction_xs = self._find_junction_boundaries(page) if page else []

        # Sort left-to-right, top-to-bottom
        fields = sorted(fields, key=lambda f: (round(f.y0, 1), f.x0))

        absorbed: set = set()  # indices of fields merged into another
        for i, fa in enumerate(fields):
            if i in absorbed:
                continue
            # Try to extend fa rightward by absorbing adjacent fields
            changed = True
            while changed:
                changed = False
                for j, fb in enumerate(fields):
                    if j <= i or j in absorbed:
                        continue
                    if fb.source != fa.source:
                        continue
                    # Same Y band?
                    if (abs(fa.y0 - fb.y0) > MERGE_TOL_Y or
                            abs(fa.y1 - fb.y1) > MERGE_TOL_Y):
                        continue
                    # Horizontally adjacent? (fa is left, fb is right)
                    gap = fb.x0 - fa.x1
                    if gap < -1 or gap > MERGE_GAP_X:
                        # Also check reverse (fb is left, fa is right)
                        gap_rev = fa.x0 - fb.x1
                        if gap_rev < -1 or gap_rev > MERGE_GAP_X:
                            continue
                    # Both fields have distinct labels → they are
                    # intentionally separate form fields, not fragments
                    # of a single field from segmented H-lines.
                    if fa.label and fb.label:
                        continue
                    # V-line or grid-cell boundary between them?
                    mid_x = (min(fa.x1, fb.x1) + max(fa.x0, fb.x0)) / 2
                    if page and self._has_vline_between(
                        page, mid_x, fa.y0, fa.y1, fa.x1, fb.x0
                    ):
                        continue
                    if page and self._has_grid_cell_boundary(
                        page, fa, fb
                    ):
                        continue
                    # Junction-dot column boundary between them?
                    if junction_xs:
                        junc_left = min(fa.x1, fb.x1)
                        junc_right = max(fa.x0, fb.x0)
                        if any(junc_left - VLINE_CHECK_TOL <= bx
                               <= junc_right + VLINE_CHECK_TOL
                               for bx in junction_xs):
                            continue
                    # Merge: expand fa to cover fb
                    fa.x0 = min(fa.x0, fb.x0)
                    fa.x1 = max(fa.x1, fb.x1)
                    fa.y0 = min(fa.y0, fb.y0)
                    fa.y1 = max(fa.y1, fb.y1)
                    absorbed.add(j)
                    changed = True

        return [f for i, f in enumerate(fields) if i not in absorbed]

    # -----------------------------------------------------------------
    @staticmethod
    def _has_vline_between(
        page: PageModel,
        mid_x: float,
        y0: float,
        y1: float,
        left_x1: float,
        right_x0: float,
    ) -> bool:
        """Check if a V-line or vertical edge exists between two fields."""
        check_x0 = left_x1 - VLINE_CHECK_TOL
        check_x1 = right_x0 + VLINE_CHECK_TOL
        field_h = y1 - y0

        # Check explicit v_lines
        for vl in page.v_lines:
            vx = vl.get('x', (vl.get('x0', 0) + vl.get('x1', 0)) / 2)
            if not (check_x0 <= vx <= check_x1):
                continue
            vy0 = vl.get('y0', vl.get('top', 0))
            vy1 = vl.get('y1', vl.get('bottom', 0))
            overlap_y0 = max(y0, vy0)
            overlap_y1 = min(y1, vy1)
            if overlap_y1 - overlap_y0 > field_h * 0.4:
                return True

        # Also check vertical edges from rectangles (some PDFs draw
        # grid borders as filled rects rather than stroked lines;
        # pdfplumber exposes them as rect_edge objects in page.edges).
        for edge in getattr(page, 'edges', []):
            if edge.get('orientation') != 'v':
                continue
            vx = edge.get('x0', edge.get('x', 0))
            if not (check_x0 <= vx <= check_x1):
                continue
            vy0 = edge.get('y0', edge.get('top', 0))
            vy1 = edge.get('y1', edge.get('bottom', 0))
            if vy0 > vy1:
                vy0, vy1 = vy1, vy0
            overlap_y0 = max(y0, vy0)
            overlap_y1 = min(y1, vy1)
            if overlap_y1 - overlap_y0 > field_h * 0.4:
                return True

        return False

    # -----------------------------------------------------------------
    @staticmethod
    def _has_grid_cell_boundary(
        page: PageModel,
        fa: ResolvedField,
        fb: ResolvedField,
    ) -> bool:
        """Check if fa and fb sit in different visual grid cells.

        If two adjacent fields each align with a separate grid cell,
        the boundary between them is structural and the merge should
        be blocked.
        """
        cells = getattr(page, 'visual_grid_cells', [])
        if not cells:
            return False

        # Determine the junction x: where fa ends and fb begins
        if fa.x1 <= fb.x0 + MERGE_GAP_X:
            junc_x = (fa.x1 + fb.x0) / 2
        else:
            junc_x = (fb.x1 + fa.x0) / 2

        mid_y = (fa.y0 + fa.y1) / 2
        tol = 3.0

        for cell in cells:
            if isinstance(cell, tuple):
                cx0, cy0, cx1, cy1 = cell
            else:
                cx0 = cell.get('x0', 0)
                cy0 = cell.get('y0', 0)
                cx1 = cell.get('x1', 0)
                cy1 = cell.get('y1', 0)

            # Cell must overlap the fields' y range
            if mid_y < cy0 - tol or mid_y > cy1 + tol:
                continue

            # Does this cell's right edge align with the junction?
            if abs(cx1 - junc_x) < tol:
                return True
            # Does this cell's left edge align with the junction?
            if abs(cx0 - junc_x) < tol:
                return True

        return False
