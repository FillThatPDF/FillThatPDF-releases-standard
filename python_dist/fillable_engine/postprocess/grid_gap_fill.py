"""
Post-processor: Fill empty grid cells with text fields.

Many compact forms (e.g. TAM HVAC Commissioning Report) have dense grids
formed by horizontal and vertical lines, but existing detectors miss many
cells.  This post-processor reconstructs the grid from page lines,
enumerates every cell, and creates fields in the empty ones.

**Per-row grid construction:**  Different sections of a form often have
different column layouts.  Instead of building one global grid from ALL V-line
X positions, we build a local grid for each row using only the V-lines that
actually exist in that row's Y range.  This prevents phantom cells from
mismatched cross-section boundaries while still capturing all real cells —
including wide underline-style rows that have H-lines but no V-line sub-
divisions.

When creating a proper field in a cell, any thin text fields (height < 50%
of cell height) whose centre falls inside the new field are **removed** —
they were placed at a text baseline by inline-label / form-line detectors
and do not represent the full entry area.

LabelTrimmer runs after this and will handle any label overlap.

Runs AFTER LineSnapper (so existing fields are snapped) and BEFORE
HeightStandardizer and LabelTrimmer.
"""

from collections import defaultdict
from typing import List, Tuple, Dict, Optional

from ..models import ResolvedField, FieldType, PageModel
from ..helpers import clean_field_name, detect_field_format


# --- tunables ---------------------------------------------------------------

# Merge tolerance: lines within this many points share a grid coordinate.
MERGE_TOL_H = 3.0   # horizontal lines (Y axis)
MERGE_TOL_V = 4.0   # vertical lines (X axis)

# Cell size limits
MIN_CELL_W = 15.0    # narrower → not fillable
MIN_CELL_H = 8.0     # shorter → not fillable
MAX_CELL_H = 60.0    # taller → probably a section header, not a data cell

# Edge-validation fuzz: a line segment must extend within this many points
# of the cell boundary to count as "spanning" that edge.
EDGE_FUZZ = 6.0

# Overlap threshold: if an existing field covers this fraction of a cell,
# the cell is considered "already has a field".
COVERAGE_THRESHOLD = 0.30

# Label-only detection: if text fills more than this fraction of cell width,
# the cell is label-only and should be skipped.
LABEL_TEXT_RATIO = 0.60

# Sources that already produce correctly-positioned fields.  These should
# NOT be skipped when checking cell occupancy on h-line-only pages, and
# should NOT be removed/superseded by grid gap-fill fields.
_GRID_AWARE_SOURCES = {
    'grid_gap_fill', 'label_entry_below', 'label_entry_cell',
    'split_cell_multi_row', 'label_below_split', 'form_line_presplit',
    'signature', 'date',
}


class GridGapFill:
    """Create fields in empty grid cells that detectors missed."""

    def process(
        self,
        fields: List[ResolvedField],
        pages: List[PageModel],
    ) -> List[ResolvedField]:
        new_fields: List[ResolvedField] = []
        h_line_only_pages: set = set()
        page_grid_cells: Dict[int, List[Tuple[float, float, float, float]]] = {}
        all_h_line_only: set = set()  # ALL h-line-only pages (even no-grid ones)

        for page in pages:
            # Skip informational pages — no fillable areas expected
            if page.is_info_page:
                continue

            # Determine if this page has real V-lines or is
            # h-line-only (where inline_label / form_line fields
            # should be superseded by proper grid cells).
            v_unique_xs = set(
                round((vl.get('x0', 0) + vl.get('x1', 0)) / 2)
                for vl in page.v_lines
            )
            page_has_v_lines = (
                len(v_unique_xs) > 2
                or any(
                    (vl.get('bottom', 0) - vl.get('top', 0)) > 30
                    for vl in page.v_lines
                )
            )
            is_h_line_only = not page_has_v_lines
            if is_h_line_only:
                all_h_line_only.add(page.page_num)

            grid_cells = self._build_grid(page)
            if not grid_cells:
                continue

            skip_thin = is_h_line_only
            if skip_thin:
                h_line_only_pages.add(page.page_num)
                page_grid_cells[page.page_num] = grid_cells

            empty = self._find_empty_cells(
                grid_cells, fields, page, skip_thin=skip_thin)
            created = self._create_fields(
                empty, page, grid_cells, fields, skip_thin=skip_thin)
            new_fields.extend(created)

        # On h-line-only pages, remove non-grid-aware text fields whose
        # centre falls inside any grid cell.  GGF has created proper
        # grid-aligned fields for every non-label cell; the old
        # detector fields (inline_label, form_line, etc.) are
        # artefacts that don't align with the h-line grid structure.
        # Sources that already produce grid-cell-aligned fields
        # (label_entry_below, label_entry_cell, split_cell_multi_row)
        # are kept as they are already correctly sized to cells.
        superseded_ids: set = set()
        for ef in fields:
            if ef.page not in h_line_only_pages:
                continue
            if ef.field_type != FieldType.TEXT:
                continue
            if ef.source in _GRID_AWARE_SOURCES:
                continue
            fcx = (ef.x0 + ef.x1) / 2
            fcy = (ef.y0 + ef.y1) / 2
            for cx0, cy0, cx1, cy1 in page_grid_cells.get(ef.page, []):
                if (cx0 - 2 <= fcx <= cx1 + 2
                        and cy0 - 2 <= fcy <= cy1 + 2):
                    superseded_ids.add(id(ef))
                    break

        if superseded_ids:
            fields[:] = [f for f in fields if id(f) not in superseded_ids]
            print(f"   GridGapFill: removed {len(superseded_ids)} non-grid "
                  f"text fields inside grid cells on h-line-only pages")

        if new_fields:
            print(f"   GridGapFill: created {len(new_fields)} fields "
                  f"in empty grid cells")
            fields.extend(new_fields)

        # --- Infographic / cover page cleanup ---
        # Only on predominantly h-line-only PDFs (≥80% of non-info
        # pages are h-line-only), pages where GGF created ZERO
        # fields and that have ≥5 checkboxes are infographic/cover
        # pages (e.g. workflow diagrams with circled step numbers).
        # The ratio guard prevents this from triggering on mixed
        # PDFs (DTE, 56380) where some h-line-only pages still have
        # legitimate form fields.
        #
        # Zone-aware: if the page has a header bar, only count and
        # remove fields ABOVE the first header bar (the infographic
        # zone).  Fields below the first header bar are legitimate
        # form elements (e.g. 57026 Avangrid P1 has real checkboxes
        # at the bottom of page 1 below the header bars).
        non_info_count = sum(1 for p in pages if not p.is_info_page)
        h_line_ratio = len(all_h_line_only) / max(1, non_info_count)
        page_map_for_cleanup = {pg.page_num: pg for pg in pages}
        if h_line_only_pages and h_line_ratio >= 0.80:
            ggf_pages = set(f.page for f in new_fields)
            no_ggf_pages = all_h_line_only - ggf_pages
            infographic_ids: set = set()
            for pg_num in no_ggf_pages:
                pg_fields = [f for f in fields if f.page == pg_num]

                # Determine the infographic zone boundary: if the page
                # has header bars, only consider fields above the first
                # header bar as potential infographic false-positives.
                pg_model = page_map_for_cleanup.get(pg_num)
                zone_y_limit = None
                if pg_model and pg_model.header_bars:
                    first_hbar_y = min(
                        hb.get('y0', hb.get('top', 999))
                        for hb in pg_model.header_bars)
                    if 100 <= first_hbar_y <= 500:
                        zone_y_limit = first_hbar_y

                if zone_y_limit is not None:
                    # Zone-aware: only count/remove fields above header
                    zone_fields = [f for f in pg_fields
                                   if f.y0 < zone_y_limit]
                    # Character and table_col checkboxes are real form
                    # elements, not decorative circles.  Exclude them
                    # from the infographic count.
                    _REAL_CB_SOURCES = {'character', 'table_col'}
                    zone_cb = sum(1 for f in zone_fields
                                 if f.field_type == FieldType.CHECKBOX
                                 and f.source not in _REAL_CB_SOURCES)
                    if zone_cb >= 5:
                        for f in zone_fields:
                            if f.source in _REAL_CB_SOURCES:
                                continue  # keep real form checkboxes
                            infographic_ids.add(id(f))
                else:
                    # No header bar → whole-page guard (original logic)
                    # Character and table_col checkboxes are real form
                    # elements, not decorative circles.  Exclude them
                    # from the infographic count.
                    _REAL_CB_SOURCES2 = {'character', 'table_col'}
                    cb_count = sum(1 for f in pg_fields
                                   if f.field_type == FieldType.CHECKBOX
                                   and f.source not in _REAL_CB_SOURCES2)
                    text_count = sum(1 for f in pg_fields
                                     if f.field_type == FieldType.TEXT)
                    if cb_count >= 5 and text_count <= 5:
                        for f in pg_fields:
                            if f.source in _REAL_CB_SOURCES2:
                                continue  # keep real form checkboxes
                            infographic_ids.add(id(f))
            if infographic_ids:
                fields[:] = [f for f in fields
                             if id(f) not in infographic_ids]
                print(f"   GridGapFill: removed {len(infographic_ids)} "
                      f"fields from infographic/cover pages")

        # --- Infographic ZONE guard (per-page, pre-header area) ---
        # Some pages (e.g. 57026 Avangrid P1) have a decorative
        # infographic zone above the first header bar with colored
        # circles/shapes.  Remove TEXT fields in that zone but keep
        # checkboxes.  This is different from the whole-page guard
        # above — it only removes fields in the top zone, not the
        # entire page.
        page_map_for_infographic = {pg.page_num: pg for pg in pages}
        infographic_zone_ids: set = set()
        for pg in pages:
            if not pg.header_bars:
                continue
            first_hbar_y = min(
                hb.get('y0', hb.get('top', 999)) for hb in pg.header_bars)
            if first_hbar_y < 100:
                continue  # Header too close to top — no infographic zone
            if first_hbar_y > 300:
                continue  # Zone too large — header bar is a section header,
                          # not an infographic boundary (e.g. 52304 P0)

            # Count colored CURVES (not rects) above the first header bar.
            # Curves indicate decorative infographic elements (logos,
            # circles, icons).  Rects are usually functional (table cells,
            # boxes) and should NOT trigger this guard.
            colored_shapes = 0
            for obj_type in ('curves',):
                for obj in getattr(pg, obj_type, []):
                    obj_top = float(obj.get('top', obj.get('y0', 0)))
                    if obj_top >= first_hbar_y:
                        continue
                    obj_w = float(obj.get('x1', 0)) - float(obj.get('x0', 0))
                    obj_h = abs(float(obj.get('bottom', obj.get('y1', 0)))
                                - obj_top)
                    if obj_w < 5 or obj_h < 5:
                        continue
                    color = obj.get('non_stroking_color', [])
                    if isinstance(color, (int, float)):
                        color = [color]
                    if not color:
                        continue
                    is_colored = False
                    if len(color) == 1 and color[0] < 0.95:
                        is_colored = True
                    elif len(color) == 3 and (
                            color[0] < 0.95 or color[1] < 0.95
                            or color[2] < 0.95):
                        is_colored = True
                    elif len(color) == 4 and sum(color) > 0.05:
                        is_colored = True
                    if is_colored:
                        colored_shapes += 1

            if colored_shapes >= 5:
                for f in fields:
                    if (f.page == pg.page_num
                            and f.y0 < first_hbar_y
                            and f.field_type == FieldType.TEXT):
                        infographic_zone_ids.add(id(f))
        if infographic_zone_ids:
            fields[:] = [f for f in fields
                         if id(f) not in infographic_zone_ids]
            print(f"   GridGapFill: removed {len(infographic_zone_ids)} "
                  f"text fields from infographic zones")

        # --- Merge adjacent GGF fields in the same row ---
        # On h-line-only pages, h_line junctions sometimes create
        # sub-columns that split what should be one entry field into
        # multiple narrow fragments.  Merge consecutive GGF text
        # fields in the same row when the right field's cell area
        # contains no label text (empty continuation cells).
        if h_line_only_pages:
            page_map = {pg.page_num: pg for pg in pages}
            self._merge_adjacent_ggf_fields(fields, page_map,
                                            h_line_only_pages)

        return fields

    # -- Stage 1: per-row grid construction ----------------------------------

    @staticmethod
    def _cluster_values(values: List[float], tol: float) -> List[float]:
        """Cluster nearby values and return sorted list of cluster means."""
        if not values:
            return []
        values = sorted(values)
        clusters: List[List[float]] = [[values[0]]]
        for v in values[1:]:
            if v - clusters[-1][-1] <= tol:
                clusters[-1].append(v)
            else:
                clusters.append([v])
        return [sum(c) / len(c) for c in clusters]

    def _build_grid(
        self, page: PageModel,
    ) -> List[Tuple[float, float, float, float]]:
        """Build grid cells using per-row grid construction.

        For each row (pair of consecutive H-line Y values), we find the V-line
        X positions that actually exist in that row's Y range and use them as
        column boundaries.  Rows with no V-lines get one full-width cell.
        Only cells whose top and bottom H-lines span the full cell width are
        kept.

        Returns list of (x0, y0, x1, y1) tuples — one per valid cell.
        """
        h_lines = page.h_lines
        v_lines = page.v_lines

        # Include header-bar top/bottom edges as synthetic H-lines.
        # Header bars (coloured section headers) sit between data rows;
        # their edges are legitimate row boundaries that often don't
        # appear in the raw h_line list.  Adding them prevents rows
        # that straddle a header bar from exceeding MAX_CELL_H.
        # Cells that fall *inside* the header bar are later excluded
        # by _find_empty_cells → is_in_header_bar().
        synth_h = list(h_lines)
        for hb in getattr(page, 'header_bars', []):
            hb_x0 = hb.get('x0', 0)
            hb_x1 = hb.get('x1', 0)
            hb_y0 = hb.get('y0', 0)
            hb_y1 = hb.get('y1', 0)
            synth_h.append({
                'x0': hb_x0, 'x1': hb_x1,
                'y': hb_y0, 'top': hb_y0, 'bottom': hb_y0,
            })
            synth_h.append({
                'x0': hb_x0, 'x1': hb_x1,
                'y': hb_y1, 'top': hb_y1, 'bottom': hb_y1,
            })
        h_lines = synth_h

        if len(h_lines) < 2:
            return []

        # Cluster H-line Y values into canonical row boundaries
        h_ys = []
        for hl in h_lines:
            y = hl.get('y', (hl.get('top', 0) + hl.get('bottom', 0)) / 2)
            h_ys.append(y)
        row_ys = self._cluster_values(h_ys, MERGE_TOL_H)

        if len(row_ys) < 2:
            return []

        cells = []

        # Check if this page has real V-lines in the content area.
        # H-line junctions are only used as column boundaries on
        # pages with NO real V-lines (horizontal-line-only forms).
        # Detection: pages with form V-lines have them at >2 unique
        # X positions (page border marks are at only 2: left+right).
        # Also catch pages with just a few long V-lines (>30pt).
        v_unique_xs = set(
            round((vl.get('x0', 0) + vl.get('x1', 0)) / 2)
            for vl in v_lines
        )
        page_has_v_lines = (
            len(v_unique_xs) > 2
            or any(
                (vl.get('bottom', 0) - vl.get('top', 0)) > 30
                for vl in v_lines
            )
        )

        for i in range(len(row_ys) - 1):
            y0, y1 = row_ys[i], row_ys[i + 1]
            h = y1 - y0
            if h < MIN_CELL_H or h > MAX_CELL_H:
                continue

            # Find the X extent of H-lines at this row's top boundary.
            # This tells us how wide this row actually is.
            row_x0, row_x1 = self._get_row_h_extent(y0, h_lines)
            if row_x1 - row_x0 < MIN_CELL_W:
                continue

            # Find V-lines that exist in this row's Y range
            local_v_xs = self._get_local_v_xs(y0, y1, v_lines)

            # When the page has NO real V-lines, infer column
            # boundaries from H-line segment junctions.  Many forms
            # (e.g. Avangrid Clean Heat) use horizontal lines only;
            # the column boundaries are implicit in the segment
            # endpoints: [57-263], [263-399], [399-498] → junctions
            # at x=263 and x=399.
            # Prefer junctions present at BOTH boundaries, but fall
            # back to one side's junctions when the other is a
            # header-bar edge (which spans full width, no segments).
            if not local_v_xs and not page_has_v_lines:
                top_junc = self._get_h_line_junctions(
                    y0, h_lines, row_x0, row_x1)
                bot_junc = self._get_h_line_junctions(
                    y1, h_lines, row_x0, row_x1)
                shared = []
                for tx in top_junc:
                    for bx in bot_junc:
                        if abs(tx - bx) <= MERGE_TOL_V:
                            shared.append((tx + bx) / 2)
                            break

                # Header-bar fallback: if one side has no junctions
                # because it's a header-bar edge (synthetic full-width
                # H-line), use the other side's junctions alone.
                if not shared:
                    header_bars = getattr(page, 'header_bars', [])
                    if not top_junc and bot_junc:
                        for hb in header_bars:
                            if abs(y0 - hb.get('y1', 0)) <= EDGE_FUZZ:
                                shared = bot_junc
                                break
                    elif top_junc and not bot_junc:
                        for hb in header_bars:
                            if abs(y1 - hb.get('y0', 0)) <= EDGE_FUZZ:
                                shared = top_junc
                                break

                # Section-transition fallback: both boundaries have
                # junctions but none match — the column layout changes
                # across this row (e.g. 57026 P3: 5-column header above,
                # 2-column signature row below).  Use the side with FEWER
                # junctions (coarser layout) as the column structure for
                # this row.  _has_h_edge validation will reject invalid
                # cells where the other boundary's segments don't span.
                if not shared and top_junc and bot_junc:
                    if len(bot_junc) <= len(top_junc):
                        shared = bot_junc
                    else:
                        shared = top_junc

                local_v_xs = shared

            # Build column boundaries: row edges + local V-line Xs
            boundaries = sorted(set([row_x0] + local_v_xs + [row_x1]))

            # Enumerate cells for this row
            for j in range(len(boundaries) - 1):
                x0, x1 = boundaries[j], boundaries[j + 1]
                w = x1 - x0
                if w < MIN_CELL_W:
                    continue

                # Validate: both top and bottom H-lines must span this cell
                if (self._has_h_edge(y0, x0, x1, h_lines) and
                        self._has_h_edge(y1, x0, x1, h_lines)):
                    cells.append((x0, y0, x1, y1))

        return cells

    @staticmethod
    def _get_row_h_extent(
        y: float, h_lines: List[Dict],
    ) -> Tuple[float, float]:
        """Get the full X extent of H-lines at a given Y coordinate."""
        x0_min = float('inf')
        x1_max = float('-inf')
        for hl in h_lines:
            ly = hl.get('y', (hl.get('top', 0) + hl.get('bottom', 0)) / 2)
            if abs(ly - y) <= EDGE_FUZZ:
                x0_min = min(x0_min, hl.get('x0', 0))
                x1_max = max(x1_max, hl.get('x1', 0))
        if x0_min == float('inf'):
            return (0.0, 0.0)
        return (x0_min, x1_max)

    def _get_local_v_xs(
        self, y0: float, y1: float, v_lines: List[Dict],
    ) -> List[float]:
        """Find V-line X positions that have segments in the given Y range."""
        xs = []
        for vl in v_lines:
            lx = (vl.get('x0', 0) + vl.get('x1', 0)) / 2
            lt = vl.get('top', 0)
            lb = vl.get('bottom', 0)
            # V-line must overlap this row's Y range
            if lt <= y0 + EDGE_FUZZ and lb >= y1 - EDGE_FUZZ:
                xs.append(lx)
        return self._cluster_values(xs, MERGE_TOL_V)

    def _get_h_line_junctions(
        self, y: float, h_lines: List[Dict],
        row_x0: float, row_x1: float,
    ) -> List[float]:
        """Extract implicit column boundaries from h_line segment junctions.

        When a PDF has table rows defined by horizontal lines but no
        vertical lines, the column boundaries are implicit in the h_line
        segment endpoints.  For example, segments [57-263], [263-399],
        [399-498] share junction points at x=263 and x=399 that are
        column boundaries.

        Only returns *interior* junctions (not the row edges).
        """
        xs: set = set()
        for hl in h_lines:
            ly = hl.get('y', (hl.get('top', 0) + hl.get('bottom', 0)) / 2)
            if abs(ly - y) > EDGE_FUZZ:
                continue
            x0 = hl.get('x0', 0)
            x1 = hl.get('x1', 0)
            # Interior endpoints — not the row edges
            if x0 > row_x0 + EDGE_FUZZ:
                xs.add(x0)
            if x1 < row_x1 - EDGE_FUZZ:
                xs.add(x1)
        return self._cluster_values(sorted(xs), MERGE_TOL_V)

    @staticmethod
    def _has_h_edge(
        y: float, x0: float, x1: float, h_lines: List[Dict],
    ) -> bool:
        """Check if H-line segments at Y collectively span from x0 to x1.

        H-lines on compact forms are often fragmented into segments at
        column boundaries from other sections.  Instead of requiring one
        segment to cover the full cell width, we collect all segments at
        this Y and check whether their union covers [x0, x1].
        """
        # Collect all H-line segments at this Y
        segs: List[Tuple[float, float]] = []
        for hl in h_lines:
            ly = hl.get('y', (hl.get('top', 0) + hl.get('bottom', 0)) / 2)
            if abs(ly - y) > EDGE_FUZZ:
                continue
            segs.append((hl.get('x0', 0), hl.get('x1', 0)))

        if not segs:
            return False

        # Check single-segment fast path
        for sx0, sx1 in segs:
            if sx0 <= x0 + EDGE_FUZZ and sx1 >= x1 - EDGE_FUZZ:
                return True

        # Merge overlapping/adjacent segments and check composite coverage
        segs.sort()
        merged_x0, merged_x1 = segs[0]
        for sx0, sx1 in segs[1:]:
            if sx0 <= merged_x1 + EDGE_FUZZ:  # adjacent or overlapping
                merged_x1 = max(merged_x1, sx1)
            else:
                # Gap — check if we already cover enough
                if merged_x0 <= x0 + EDGE_FUZZ and merged_x1 >= x1 - EDGE_FUZZ:
                    return True
                merged_x0, merged_x1 = sx0, sx1

        return merged_x0 <= x0 + EDGE_FUZZ and merged_x1 >= x1 - EDGE_FUZZ

    # -- Stage 3: find empty cells -------------------------------------------

    def _find_empty_cells(
        self,
        grid_cells: List[Tuple[float, float, float, float]],
        fields: List[ResolvedField],
        page: PageModel,
        skip_thin: bool = False,
    ) -> List[Tuple[float, float, float, float]]:
        """Return grid cells that have no existing field and are not excluded."""
        page_fields = [f for f in fields if f.page == page.page_num]

        empty = []
        for cx0, cy0, cx1, cy1 in grid_cells:
            # --- Check for existing field coverage ---
            if self._cell_has_field(cx0, cy0, cx1, cy1, page_fields,
                                    skip_thin=skip_thin):
                continue

            # --- Footer zone exclusion ---
            if page.footer_y > 0 and cy0 >= page.footer_y:
                continue

            # --- Inter-table gap exclusion ---
            if page.is_in_table_gap(cx0, cy0, cx1, cy1, 30):
                continue

            # --- Exclusion zones ---
            if page.is_in_header_bar(cx0, cy0, cx1, cy1):
                continue

            if page.is_in_image_upload_region(cx0, cy0, cx1, cy1, 0.3):
                continue

            if self._in_box_entry(cx0, cy0, cx1, cy1, page):
                continue

            if self._in_square_grid(cx0, cy0, cx1, cy1, page):
                continue

            # --- Small-table adjacency guard ---
            # Cells immediately below or above very small pdfplumber
            # tables (< 50pt tall) are structural artefacts, not
            # fillable areas (e.g. comb-box header tables).
            _near_small_tbl = False
            for tbl in page.table_bboxes:
                tx0, ty0, tx1, ty1 = tbl
                if (ty1 - ty0) < 50:
                    # Check horizontal overlap
                    if min(cx1, tx1) - max(cx0, tx0) > 5:
                        # Adjacent: cell top touches table bottom, or
                        # cell bottom touches table top (within 5pt).
                        if abs(cy0 - ty1) < 5 or abs(cy1 - ty0) < 5:
                            _near_small_tbl = True
                            break
                        # Or cell is inside the small table
                        if cy0 >= ty0 - 3 and cy1 <= ty1 + 3:
                            _near_small_tbl = True
                            break
            if _near_small_tbl:
                continue

            # --- Column classification guard ---
            cell_h = cy1 - cy0
            if page.column_classifications:
                col_cls = page.column_classifications.get(round(cx0))
                if col_cls is None:
                    # Fuzzy match within 20px
                    for hx, cls in page.column_classifications.items():
                        if abs(round(cx0) - hx) < 20:
                            col_cls = cls
                            break
                if col_cls == 'read-only':
                    # Exempt cells where text sits at the TOP with
                    # ≥ 14pt of blank entry space below (form label
                    # + entry pattern).  Empty cells and cells with
                    # centered/lower text stay blocked.
                    if not self._is_label_entry_cell(
                            cx0, cy0, cx1, cy1, cell_h, page):
                        continue

            # --- Label-only check ---
            if self._is_label_only(cx0, cy0, cx1, cy1, page,
                                   strict_label=skip_thin):
                # On h-line-only pages, wide label-only cells may contain
                # column-header labels with fill gaps between them (e.g.
                # "Service Address   City   State   ZIP" spread across a
                # 540pt row).  Split the cell at label cluster boundaries
                # and add the gap sub-cells as fill areas.
                if skip_thin:
                    gap_cells = self._split_column_header_gaps(
                        cx0, cy0, cx1, cy1, page)
                    if gap_cells:
                        empty.extend(gap_cells)
                continue

            # --- Checkbox overlap check ---
            if self._overlaps_checkbox(cx0, cy0, cx1, cy1, page_fields):
                continue

            empty.append((cx0, cy0, cx1, cy1))

        return empty

    @staticmethod
    def _cell_has_field(
        cx0: float, cy0: float, cx1: float, cy1: float,
        page_fields: List[ResolvedField],
        skip_thin: bool = False,
    ) -> bool:
        """Check if any existing field covers this cell."""
        cell_area = max(1.0, (cx1 - cx0) * (cy1 - cy0))
        cell_h = cy1 - cy0

        for f in page_fields:
            # H-line-only page takeover: skip most text fields so GGF
            # creates its own grid-aligned fields.  The old detector
            # fields (inline_label, form_line, etc.) are removed later
            # by the superseded-field cleanup pass.  However, fields
            # from grid-aware sources (label_below_split, presplit,
            # signature, date) are already correctly positioned and
            # should block GGF from creating overlapping cells.
            if skip_thin and f.field_type == FieldType.TEXT:
                if f.source not in _GRID_AWARE_SOURCES:
                    continue

            f_area = max(1.0, f.width * f.height)

            # Center containment check — only count if the field is
            # a reasonable size relative to the cell.  Tiny fields
            # (e.g. 7pt inline_label inside a 29pt payee cell) should
            # not block gap-fill from creating a proper entry field.
            fcx = (f.x0 + f.x1) / 2
            fcy = (f.y0 + f.y1) / 2
            if cx0 <= fcx <= cx1 and cy0 <= fcy <= cy1:
                if f_area / cell_area > 0.15:
                    return True

            # Area overlap check
            ix0 = max(cx0, f.x0)
            iy0 = max(cy0, f.y0)
            ix1 = min(cx1, f.x1)
            iy1 = min(cy1, f.y1)
            if ix1 > ix0 and iy1 > iy0:
                inter = (ix1 - ix0) * (iy1 - iy0)
                if inter / cell_area > COVERAGE_THRESHOLD:
                    return True

        return False

    @staticmethod
    def _is_label_only(
        cx0: float, cy0: float, cx1: float, cy1: float,
        page: PageModel,
        strict_label: bool = False,
    ) -> bool:
        """Check if a cell is entirely a label (text fills most of its width).

        Underscore/fill-indicator words ("____", "___IWC", etc.) are excluded
        from the text extent calculation because they represent the blank
        fill area, not label text.  This prevents underline-style cells
        like "Measured Air Volume: _____" from being mistakenly skipped.

        When *strict_label* is True (h-line-only pages), short cells
        (h ≤ 30) use the text-width ratio to decide: if label text
        fills > LABEL_TEXT_RATIO of the cell width it is label-only;
        otherwise the cell has blank entry space to the right of the
        label (e.g. "City: [____]") and should receive a field.
        Tall cells (h > 30) fall through to normal logic.
        """
        words = page.get_words_in_bbox((cx0, cy0, cx1, cy1), 0.3)
        if not words:
            return False

        cell_w = cx1 - cx0
        cell_h = cy1 - cy0
        if cell_w <= 0:
            return True

        # Strict label mode (h-line-only pages): in short cells,
        # use text-width ratio rather than the multi-word height
        # check which misidentifies "label + right-entry" cells as
        # having "entry space below".
        if strict_label and cell_h <= 30:
            label_ws = [w for w in words
                        if w.get('text', '')
                        and w['text'].count('_') < len(w['text']) * 0.5]
            if not label_ws:
                return False  # Only underscores → fill area
            text_x0 = min(w.get('x0', cx1) for w in label_ws)
            text_x1 = max(w.get('x1', cx0) for w in label_ws)
            text_w = max(0, text_x1 - text_x0)
            # Slightly lower threshold than normal (0.55 vs 0.60)
            # to catch cells like "Street Address" (0.56) where the
            # entry is in the NEXT cell, not in the remaining space.
            return (text_w / cell_w) > 0.55

        # Filter out words that are predominantly underscores / fill indicators.
        # A word is a "fill word" if ≥50% of its characters are underscores
        # or if it's entirely non-alpha fill (e.g. "____", "___°F").
        label_words = []
        has_fill_words = False
        for w in words:
            txt = w.get('text', '')
            if not txt:
                continue
            n_underscore = txt.count('_')
            if n_underscore >= len(txt) * 0.5:
                has_fill_words = True
                continue  # Mostly underscores — this is fill area, not label
            label_words.append(w)

        if not label_words:
            return False  # Only underscores → cell IS a fill area, not label

        # If underscore fill words exist anywhere in the cell, the cell has
        # fill areas.  It is NOT label-only — LabelTrimmer will handle
        # trimming the field to avoid the label text.
        if has_fill_words:
            return False

        # If multiple label words exist without any fill indicators,
        # this cell contains informational text — not a fill target.
        # (Cells with a label + fill area would have has_fill_words=True
        # and would have returned False above.)
        #
        # Exception: if all text sits in the top portion of a tall cell,
        # the bottom is blank entry space (form label + entry pattern,
        # e.g. "Date Installed" with writing space below).
        if len(label_words) >= 2:
            cell_h = cy1 - cy0
            if cell_h > 20:
                text_bottom = max(
                    w.get('bottom', w.get('y1', cy0)) for w in label_words
                )
                # Text occupies only top 55% → entry space below
                if (text_bottom - cy0) / cell_h < 0.55:
                    return False
            return True

        # Compute text extent width from non-underscore words only
        text_x0 = min(w.get('x0', cx1) for w in label_words)
        text_x1 = max(w.get('x1', cx0) for w in label_words)
        text_w = max(0, text_x1 - text_x0)

        return (text_w / cell_w) > LABEL_TEXT_RATIO

    @staticmethod
    def _is_label_entry_cell(
        cx0: float, cy0: float, cx1: float, cy1: float,
        cell_h: float, page: PageModel,
    ) -> bool:
        """Check if a cell has a form label at the top with blank entry
        space below (≥ 14pt).  Returns False for empty cells and cells
        where text fills or is centered in the cell."""
        if cell_h <= 20:
            return False
        words = page.get_words_in_bbox((cx0, cy0, cx1, cy1), 0.3)
        if not words:
            return False
        text_bottom = max(
            w.get('bottom', w.get('y1', cy0)) for w in words
        )
        blank_below = cy1 - text_bottom
        return blank_below >= 14

    @staticmethod
    def _split_column_header_gaps(
        cx0: float, cy0: float, cx1: float, cy1: float,
        page: PageModel,
    ) -> List[Tuple[float, float, float, float]]:
        """Split a wide label-only cell at column-header label gaps.

        On h-line-only pages, a single wide cell may contain multiple
        label words spread across it like column headers (e.g.
        "Service Address   City   State   ZIP" across a 540pt row).
        The text *extent* spans most of the cell width, so
        ``_is_label_only`` marks it as label-only.  But the actual text
        *coverage* is low (~20%) — the large gaps between label
        clusters are where fill areas should go.

        Returns gap sub-cells as (x0, y0, x1, y1) tuples, or an empty
        list if the cell doesn't match the column-header pattern.
        """
        words = page.get_words_in_bbox((cx0, cy0, cx1, cy1), 0.3)
        if not words:
            return []

        # Only non-underscore label words
        label_ws = [w for w in words
                    if w.get('text', '')
                    and w['text'].count('_') < len(w['text']) * 0.5]
        if len(label_ws) < 3:
            return []

        # Cluster label words by proximity (words within 30pt are
        # treated as one cluster, e.g. "Service" + "Address").
        label_ws.sort(key=lambda w: w.get('x0', 0))
        _CLUSTER_GAP = 30.0
        clusters: List[List[dict]] = [[label_ws[0]]]
        for w in label_ws[1:]:
            if w['x0'] - clusters[-1][-1].get('x1', 0) > _CLUSTER_GAP:
                clusters.append([w])
            else:
                clusters[-1].append(w)

        if len(clusters) < 3:
            return []  # Need ≥3 distinct label clusters

        # Check actual text coverage is low (scattered column headers,
        # not a dense label row).
        cell_w = cx1 - cx0
        total_text_w = sum(
            max(w.get('x1', 0) for w in c)
            - min(w.get('x0', 0) for w in c)
            for c in clusters
        )
        if total_text_w / cell_w > 0.50:
            return []

        # Create sub-cells in the gaps after each label cluster.
        gaps: List[Tuple[float, float, float, float]] = []
        for i, cluster in enumerate(clusters):
            cluster_x1 = max(w.get('x1', 0) for w in cluster)
            if i + 1 < len(clusters):
                next_x0 = min(w.get('x0', 0) for w in clusters[i + 1])
            else:
                next_x0 = cx1  # Trailing gap after last cluster
            gap_w = next_x0 - cluster_x1
            if gap_w >= MIN_CELL_W:
                gaps.append((cluster_x1, cy0, next_x0, cy1))

        return gaps

    @staticmethod
    def _overlaps_checkbox(
        cx0: float, cy0: float, cx1: float, cy1: float,
        page_fields: List[ResolvedField],
    ) -> bool:
        """Check if any checkbox field overlaps this cell."""
        for f in page_fields:
            if f.field_type != FieldType.CHECKBOX:
                continue
            ix0 = max(cx0, f.x0)
            iy0 = max(cy0, f.y0)
            ix1 = min(cx1, f.x1)
            iy1 = min(cy1, f.y1)
            if ix1 > ix0 and iy1 > iy0:
                return True
        return False

    @staticmethod
    def _in_box_entry(
        cx0: float, cy0: float, cx1: float, cy1: float,
        page: PageModel,
    ) -> bool:
        """Check if cell overlaps a box entry area."""
        cell_area = max(1, (cx1 - cx0) * (cy1 - cy0))
        for area in page.box_entry_areas:
            ax0 = area.get('x0', 0)
            ay0 = area.get('y0', 0)
            ax1 = area.get('x1', 0)
            ay1 = area.get('y1', 0)
            ix0 = max(cx0, ax0)
            iy0 = max(cy0, ay0)
            ix1 = min(cx1, ax1)
            iy1 = min(cy1, ay1)
            if ix1 > ix0 and iy1 > iy0:
                inter = (ix1 - ix0) * (iy1 - iy0)
                if inter / cell_area > 0.3:
                    return True
        return False

    @staticmethod
    def _in_square_grid(
        cx0: float, cy0: float, cx1: float, cy1: float,
        page: PageModel,
    ) -> bool:
        """Check if cell is inside a square grid region."""
        for sgr in page.square_grid_regions:
            sx0 = sgr.get('x0', 0)
            sy0 = sgr.get('y0', 0)
            sx1 = sgr.get('x1', 0)
            sy1 = sgr.get('y1', 0)
            if cx0 >= sx0 and cy0 >= sy0 and cx1 <= sx1 and cy1 <= sy1:
                return True
        return False

    # -- Stage 5: create fields ----------------------------------------------

    def _create_fields(
        self,
        empty_cells: List[Tuple[float, float, float, float]],
        page: PageModel,
        grid_cells: List[Tuple[float, float, float, float]],
        fields: List[ResolvedField],
        skip_thin: bool = False,
    ) -> List[ResolvedField]:
        """Create ResolvedField objects for empty cells with intelligent names.

        For cells that contain inline "Label: ____" patterns (fill words mixed
        with label text), we split the cell into sub-regions at each fill area
        rather than creating one field for the whole cell.  This prevents
        LabelTrimmer from collapsing multi-fill cells into tiny fields.
        """
        if not empty_cells:
            return []

        # Build row/column label maps for naming
        row_labels = self._build_row_labels(grid_cells, page)
        col_headers = self._build_col_headers(grid_cells, page)

        # Collect existing names to avoid collisions
        existing_names = {f.name for f in fields}
        page_fields = [f for f in fields if f.page == page.page_num]

        created = []
        name_counter: Dict[str, int] = defaultdict(int)

        for cx0, cy0, cx1, cy1 in empty_cells:
            # Split the cell into sub-regions if it has inline fill patterns
            sub_regions = self._get_fill_sub_regions(
                cx0, cy0, cx1, cy1, page,
            )

            for sx0, sy0, sx1, sy1 in sub_regions:
                # Skip if a committed field already covers this sub-region
                if self._cell_has_field(sx0, sy0, sx1, sy1, page_fields,
                                        skip_thin=skip_thin):
                    continue

                # Position field below label text in tall cells.
                # On forms with label-at-top / entry-below patterns
                # (e.g. "Authorized Signature" with writing space
                # below), shift the field's top edge to just below
                # the label text so the field doesn't cover the label.
                fy0 = sy0
                cell_h = sy1 - sy0
                if cell_h > 30:
                    cell_words = page.get_words_in_bbox(
                        (sx0, sy0, sx1, sy1), 0.3)
                    lbl_words = [
                        w for w in cell_words
                        if w.get('text', '').count('_')
                        < len(w.get('text', '')) * 0.5
                    ]
                    if lbl_words:
                        text_bottom = max(
                            w.get('bottom', w.get('y1', sy0))
                            for w in lbl_words
                        )
                        if (text_bottom - sy0) / cell_h < 0.55:
                            adj = text_bottom + 2
                            if (sy1 - adj) >= MIN_CELL_H:
                                fy0 = adj

                row_label = row_labels.get(round(sy0, 1), '')
                col_header = col_headers.get(round(sx0, 1), '')

                name = self._make_name(
                    row_label, col_header, page.page_num,
                    sy0, sx0, existing_names, name_counter,
                )
                existing_names.add(name)

                rf = ResolvedField(
                    page=page.page_num,
                    x0=sx0, y0=fy0, x1=sx1, y1=sy1,
                    field_type=FieldType.TEXT,
                    source='grid_gap_fill',
                    name=name,
                )

                # Detect format from column header (e.g. "Total Rebate"
                # columns → currency).  Also check cell words for "$"
                # so the writer knows whether to add "$" prefix.
                cell_words = page.get_words_in_bbox(
                    (sx0, cy0, sx1, cy1), 0.3)
                cell_dollar = any(
                    w.get('text', '').strip() in ('$', '$:')
                    for w in cell_words)
                cell_text = '$' if cell_dollar else None
                fmt, fmt_opts = detect_field_format(
                    col_header or None, row_label or None, cell_text)
                if fmt:
                    rf.format_type = fmt
                    rf.format_options = fmt_opts

                created.append(rf)

        return created

    @staticmethod
    def _get_fill_sub_regions(
        cx0: float, cy0: float, cx1: float, cy1: float,
        page: PageModel,
    ) -> List[Tuple[float, float, float, float]]:
        """Split a cell into fill sub-regions for inline "Label: ____" patterns.

        If the cell contains both label words and underscore fill words, we
        find the gaps between label-word clusters, then snap each gap's field
        boundaries to the actual fill-word extent (not the gap/cell edge).
        This prevents fields from extending past the end of underlines.

        Returns list of (x0, y0, x1, y1) sub-region bboxes.
        """
        words = page.get_words_in_bbox((cx0, cy0, cx1, cy1), 0.3)
        if not words:
            return [(cx0, cy0, cx1, cy1)]  # Empty cell → whole cell

        # Classify words
        label_words = []
        fill_words = []
        for w in words:
            txt = w.get('text', '')
            if not txt:
                continue
            if txt.count('_') >= len(txt) * 0.5:
                fill_words.append(w)
            else:
                label_words.append(w)

        if not fill_words or not label_words:
            return [(cx0, cy0, cx1, cy1)]  # No fill pattern → whole cell

        # Sort by x position
        label_words.sort(key=lambda w: w['x0'])
        fill_words.sort(key=lambda w: w['x0'])

        # Build occupied intervals from label words, merging nearby ones
        LABEL_MERGE_GAP = 5.0  # merge label words within 5pt
        FILL_PAD = 2.0          # small buffer past fill word edges
        occupied: List[Tuple[float, float]] = []
        for w in label_words:
            lx0 = w['x0'] - 2
            lx1 = w['x1'] + 2
            if occupied and lx0 <= occupied[-1][1] + LABEL_MERGE_GAP:
                occupied[-1] = (occupied[-1][0], max(occupied[-1][1], lx1))
            else:
                occupied.append((lx0, lx1))

        # Find gaps between occupied label intervals, then snap to fill words
        regions: List[Tuple[float, float, float, float]] = []
        prev_end = cx0
        for occ_start, occ_end in occupied:
            gap_start = prev_end
            gap_end = occ_start

            if gap_end - gap_start >= MIN_CELL_W:
                # Find fill words whose extent overlaps this gap
                gap_fills = [fw for fw in fill_words
                             if fw['x1'] > gap_start - 3 and
                             fw['x0'] < gap_end + 3]
                if gap_fills:
                    # Snap right edge to fill word extent (not gap end)
                    fill_x1 = max(fw['x1'] for fw in gap_fills) + FILL_PAD
                    fx1 = min(gap_end, fill_x1)
                    if fx1 - gap_start >= MIN_CELL_W:
                        regions.append((gap_start, cy0, fx1, cy1))

            prev_end = occ_end

        # Trailing gap after last label cluster
        gap_start = prev_end
        if cx1 - gap_start >= MIN_CELL_W:
            # Only create a field if there are fill words in the trailing gap
            trail_fills = [fw for fw in fill_words
                           if fw['x1'] > gap_start - 3]
            if trail_fills:
                # Snap right edge to rightmost fill word (not cell edge)
                fill_x1 = max(fw['x1'] for fw in trail_fills) + FILL_PAD
                fx1 = min(cx1, fill_x1)
                if fx1 - gap_start >= MIN_CELL_W:
                    regions.append((gap_start, cy0, fx1, cy1))

        return regions if regions else [(cx0, cy0, cx1, cy1)]

    @staticmethod
    def _build_row_labels(
        grid_cells: List[Tuple[float, float, float, float]],
        page: PageModel,
    ) -> Dict[float, str]:
        """Map each row's y0 to the text in the leftmost cell of that row."""
        rows: Dict[float, List[Tuple[float, float, float, float]]] = defaultdict(list)
        for cx0, cy0, cx1, cy1 in grid_cells:
            rows[round(cy0, 1)].append((cx0, cy0, cx1, cy1))

        labels = {}
        for row_y, cells in rows.items():
            cells.sort(key=lambda c: c[0])
            leftmost = cells[0]
            text = page.get_text_in_bbox(leftmost, 0.3).strip()
            if text:
                labels[row_y] = text
        return labels

    @staticmethod
    def _build_col_headers(
        grid_cells: List[Tuple[float, float, float, float]],
        page: PageModel,
    ) -> Dict[float, str]:
        """Map each column's x0 to the text in the topmost cell of that column."""
        cols: Dict[float, List[Tuple[float, float, float, float]]] = defaultdict(list)
        for cx0, cy0, cx1, cy1 in grid_cells:
            cols[round(cx0, 1)].append((cx0, cy0, cx1, cy1))

        headers = {}
        for col_x, cells in cols.items():
            cells.sort(key=lambda c: c[1])
            topmost = cells[0]
            text = page.get_text_in_bbox(topmost, 0.3).strip()
            if text:
                headers[col_x] = text
        return headers

    @staticmethod
    def _make_name(
        row_label: str, col_header: str, page_num: int,
        y0: float, x0: float,
        existing_names: set, name_counter: Dict[str, int],
    ) -> str:
        """Generate a unique field name from row/column labels."""
        parts = []
        if row_label:
            words = row_label.split()[:3]
            parts.append('_'.join(words))
        if col_header:
            words = col_header.split()[:3]
            parts.append('_'.join(words))

        if parts:
            base = '_'.join(parts)
        else:
            base = f"Field_{page_num + 1}_{int(y0)}"

        base = clean_field_name(base)
        if not base:
            base = f"Field_{page_num + 1}_{int(y0)}_{int(x0)}"

        name_counter[base] += 1
        if name_counter[base] == 1 and base not in existing_names:
            return base

        for suffix in range(2, 999):
            candidate = f"{base}_{suffix}"
            if candidate not in existing_names:
                return candidate

        return f"{base}_{int(x0)}"

    # -- Adjacent field merging -----------------------------------------------

    @staticmethod
    def _merge_adjacent_ggf_fields(
        fields: List[ResolvedField],
        page_map: Dict[int, PageModel],
        h_line_only_pages: set,
    ) -> None:
        """Merge consecutive GGF text fields in the same row when the
        right field's area contains no label text.

        On h-line-only pages, h_line junctions from HEADER rows often
        create sub-columns in DATA rows that should be one entry field.
        For example, "Mobile Phone" creates fields at [326-399],
        [399-498], [498-597] — the last two are empty continuation
        cells that should merge into one [326-597] field.

        The merge is done in-place: the left field's x1 is extended
        and the absorbed right field is removed from the list.
        """
        # Collect GGF text fields on h-line-only pages, grouped by row
        rows: Dict[Tuple[int, int, int], List[ResolvedField]] = defaultdict(list)
        for f in fields:
            if f.page not in h_line_only_pages:
                continue
            if f.source != 'grid_gap_fill':
                continue
            if f.field_type != FieldType.TEXT:
                continue
            key = (f.page, round(f.y0), round(f.y1))
            rows[key].append(f)

        absorbed_ids: set = set()

        for (pg, ry0, ry1), row_fields in rows.items():
            if len(row_fields) < 2:
                continue
            row_fields.sort(key=lambda f: f.x0)
            page = page_map.get(pg)
            if page is None:
                continue

            # Find the cell's original top boundary.  On tall cells,
            # _create_fields pushes fy0 below the label text; the merge
            # step needs to look at the full cell area (including labels
            # above fy0) to correctly detect separate column headers.
            #
            # However, if the field starts right at an h-line (within 3pt),
            # that h-line IS the cell top — don't look above it or we'll
            # pick up labels from the row above.
            cell_top = ry0
            field_at_hline = any(
                abs(hl.get('y', (hl.get('top', 0) + hl.get('bottom', 0)) / 2) - ry0) < 3
                for hl in page.h_lines
            )
            if not field_at_hline:
                # Field was pushed down inside a tall cell — find the
                # nearest h-line above to include the label zone.
                hl_candidates = []
                for hl in page.h_lines:
                    ly = hl.get('y', (hl.get('top', 0) + hl.get('bottom', 0)) / 2)
                    if ly < ry0 - 2 and ly > ry0 - 50:
                        hl_candidates.append(ly)
                if hl_candidates:
                    cell_top = max(hl_candidates)

            i = 0
            while i < len(row_fields) - 1:
                fa = row_fields[i]
                fb = row_fields[i + 1]

                # Check if nearly contiguous (gap < 5pt)
                gap = fb.x0 - fa.x1
                if gap > 5:
                    i += 1
                    continue

                # Only merge when the LEFT field has label text to
                # its left in the same row — confirms "label + entry"
                # pattern (e.g. "Mobile Phone: [field1][field2]").
                # Independent data columns have no label immediately
                # left of each data field → don't merge them.
                # Extend up to 50pt into fa so labels that START at
                # fa.x0 (inside the field's cell) are found — e.g.
                # "Mobile Phone" occupying x=263-326 inside [263-399].
                # Use cell_top (not ry0) to catch labels above the
                # adjusted field y0 in tall cells.
                right_edge = fa.x0 + min(50, (fa.x1 - fa.x0) * 0.5)
                left_bbox = (max(fa.x0 - 150, 0), cell_top, right_edge, ry1)
                left_words = page.get_words_in_bbox(left_bbox, 0.3)
                left_labels = [
                    w for w in left_words
                    if w.get('text', '')
                    and w['text'].count('_') < len(w['text']) * 0.5
                ]
                if not left_labels:
                    # Fallback: merge empty+empty pairs.  On h-line-only
                    # pages, consecutive 0-gap GGF fields that are BOTH
                    # completely empty (no text) are split artefacts from
                    # h-line junctions — merge unconditionally.  Fields
                    # with data text (even short) stay separate.
                    fa_words = page.get_words_in_bbox(
                        (fa.x0, cell_top, fa.x1 + 2, ry1), 0.3)
                    fa_has_text = any(
                        w.get('text', '')
                        and w['text'].count('_') < len(w['text']) * 0.5
                        for w in fa_words
                    )
                    if fa_has_text:
                        i += 1
                        continue
                    # fa is empty — fall through to right-cell check
                    # (if fb is also empty → merge; if fb has label → skip)

                # Check if the right field's cell area has label text.
                # Use a slightly expanded bbox to catch labels that
                # sit at the edges.  Use cell_top for full cell area.
                cell_words = page.get_words_in_bbox(
                    (fb.x0 - 2, cell_top, fb.x1 + 2, ry1), 0.3)
                label_ws = [
                    w for w in cell_words
                    if w.get('text', '')
                    and w['text'].count('_') < len(w['text']) * 0.5
                ]
                if label_ws:
                    # Right cell has label text — don't merge
                    i += 1
                    continue

                # Merge: extend left field, absorb right field
                fa.x1 = fb.x1
                absorbed_ids.add(id(fb))
                row_fields.pop(i + 1)
                # Don't increment i — check the next field against
                # the now-extended fa

        if absorbed_ids:
            fields[:] = [f for f in fields if id(f) not in absorbed_ids]
            print(f"   GridGapFill: merged {len(absorbed_ids)} adjacent "
                  f"fields in same row")
