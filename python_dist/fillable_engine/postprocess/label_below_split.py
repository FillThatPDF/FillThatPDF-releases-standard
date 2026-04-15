"""
Label-Below Splitter — split wide form-line fields using labels below.

When a form has full-width horizontal lines with labels beneath them
(e.g. "Business Name          Title"), this post-processor detects distinct
label groups below the field and splits the single wide field into multiple
fields aligned to each label group.

Runs **twice** in the pipeline:
  • Pass 1 (after LabelEnricher, before RadioGroupIdentifier): splits wide
    ``form_line_segment`` fields.
  • Pass 2 (after GridGapFill): splits wide ``grid_gap_fill`` fields that
    were created in h-line-only rows.  Also removes duplicate
    ``inline_label`` fields that sit just below a split field.  Normalizes
    heights and x-positions of all line-based fields to match grid cells.

Only targets fields wider than 250 pt.
"""

from typing import List, Dict, Tuple, Optional, Set

from ..models import PageModel, ResolvedField, FieldType
from ..helpers import clean_field_name, detect_field_format


# Minimum width (pt) for a field to be considered for splitting.
_MIN_SPLIT_WIDTH = 250

# Minimum horizontal gap (pt) between two word clusters to treat them
# as separate label groups.  Multi-word labels like "Installation
# completion date" have inter-word gaps of ~2 pt, while separate
# label groups (e.g. "Business Name" vs "Title") have gaps > 40 pt.
_LABEL_GROUP_GAP = 40

# Maximum vertical distance (pt) below the field bottom edge to search
# for label words.
_MAX_BELOW_DIST = 12

# Maximum gap between consecutive words to treat them as part of the
# same label group.
_WORD_GAP = 20

# Sources eligible for splitting.
_SPLITTABLE_SOURCES = {'form_line_segment', 'grid_gap_fill'}

# Maximum vertical distance below a split field to search for
# duplicate inline_label fields to remove.
_DUPLICATE_BELOW_DIST = 25

# Minimum viable field width after splitting.  If a split would produce
# a field narrower than this, the group is merged with its neighbour.
# Tuned to catch partial-label fragments (~37 pt "installation completion")
# while keeping legitimate narrow fields (State ~42 pt, ZIP ~39 pt).
_MIN_VIABLE_SPLIT = 38

# Threshold for merging narrow presplit fields with row neighbours.
# Used to fix FormLineDetector over-splits where inline keywords create
# fields that are too narrow to be useful on their own.
_NARROW_MERGE_THRESHOLD = 36

# Sources whose height & x-position should be normalised to match
# the grid-cell dimensions on h-line-only pages.
_NORMALIZABLE_SOURCES = {
    'form_line_segment', 'form_line_presplit', 'label_below_split',
}

# Maximum height (pt) for a field to be considered "short" and eligible
# for height normalisation.
_MAX_SHORT_HEIGHT = 15


class LabelBelowSplitter:
    """Split wide form-line fields based on distinct label groups below."""

    def process(
        self,
        fields: List[ResolvedField],
        pages: List[PageModel],
    ) -> List[ResolvedField]:
        page_map: Dict[int, PageModel] = {p.page_num: p for p in pages}
        used_names: Set[str] = {f.name for f in fields}

        result: List[ResolvedField] = []
        split_count = 0
        new_split_fields: List[ResolvedField] = []

        for field in fields:
            # Only consider wide splittable fields
            if field.source not in _SPLITTABLE_SOURCES or field.width < _MIN_SPLIT_WIDTH:
                result.append(field)
                continue

            page = page_map.get(field.page)
            if page is None:
                result.append(field)
                continue

            label_groups = self._find_label_groups_below(field, page)

            # Merge groups that would produce too-narrow fields
            label_groups = self._merge_small_groups(label_groups, field)

            if len(label_groups) < 2:
                # Single label group (or none) → no split needed
                result.append(field)
                continue

            # --- Split the field at each label group boundary ---
            splits = self._split_field(field, label_groups, used_names)
            result.extend(splits)
            new_split_fields.extend(splits)
            split_count += 1

        # --- Remove duplicate inline_label fields below split fields ---
        # When a grid_gap_fill field is split, the original inline_label
        # fields (placed at the text baseline below the h-line) become
        # duplicates.  Remove them so there's no double-highlight.
        if new_split_fields:
            dup_ids = self._find_duplicate_fields(result, new_split_fields)
            if dup_ids:
                result = [f for f in result if id(f) not in dup_ids]
                print(f"   LabelBelowSplitter: removed {len(dup_ids)} "
                      f"duplicate fields below split fields")

        if split_count:
            print(f"   LabelBelowSplitter: split {split_count} wide field(s) "
                  f"into {len(new_split_fields)} fields")

        # --- Merge narrow presplit fields with row neighbours ---
        # FormLineDetector can over-split lines on inline keywords, producing
        # fields too narrow to be useful (e.g. "installation" at 34 pt).
        # Merge those into their right neighbour.
        merge_count = self._merge_narrow_row_fields(result)
        if merge_count:
            print(f"   LabelBelowSplitter: merged {merge_count} narrow "
                  f"field(s) with neighbours")

        # --- Normalize heights & x-positions for line-based fields ---
        # Only runs effectively in the 2nd pass (after GridGapFill) when
        # tall grid_gap_fill fields exist to provide a target height.
        norm_count = self._normalize_line_fields(result, pages)
        if norm_count:
            print(f"   LabelBelowSplitter: normalized {norm_count} field "
                  f"heights/positions")

        # --- Normalize signature field heights to match grid ---
        # Signature fields are detected at their natural (short) height
        # but should match the surrounding grid cell height for visual
        # consistency in table layouts.
        sig_count = self._normalize_signature_heights(result, pages)
        if sig_count:
            print(f"   LabelBelowSplitter: normalized {sig_count} signature "
                  f"field height(s)")

        # --- Absorb adjacent unlabelled gap-fills into presplit fields ---
        # On pages where h-lines are segmented by invisible vertical cell
        # boundaries, form_line_presplit fields end at a segment boundary
        # instead of the row edge.  GridGapFill then fills the remaining
        # segments with unlabelled (None) fields.  Absorb those fragments
        # back into the labelled presplit field so Email / Phone etc.
        # span the full expected width.
        absorb_count = self._absorb_adjacent_gap_fills(result, pages)
        if absorb_count:
            print(f"   LabelBelowSplitter: absorbed {absorb_count} gap-fill "
                  f"field(s) into presplit neighbours")

        # --- Align field edges to consistent column positions ---
        # Snap left-column x0 to h-line edge and right-column x0 to
        # the dominant split point so fields look neatly aligned.
        align_count = self._align_columns(result, pages)
        if align_count:
            print(f"   LabelBelowSplitter: aligned {align_count} field "
                  f"edge(s) to column positions")

        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_label_groups_below(
        field: ResolvedField,
        page: PageModel,
    ) -> List[List[Dict]]:
        """
        Find distinct label groups in the text below a field.

        Returns a list of groups, where each group is a list of word dicts
        sorted left-to-right.  Groups are separated by horizontal gaps
        >= _LABEL_GROUP_GAP.
        """
        # Collect words below the field (within _MAX_BELOW_DIST pt)
        below_words: List[Dict] = []
        for w in page.words:
            w_top = float(w['top'])
            w_x0 = float(w['x0'])
            w_x1 = float(w['x1'])

            # Must be below the field bottom edge
            dist_below = w_top - field.y1
            if dist_below < -2 or dist_below > _MAX_BELOW_DIST:
                continue

            # Must overlap the field's horizontal span (with some tolerance)
            if w_x1 < field.x0 - 5 or w_x0 > field.x1 + 5:
                continue

            below_words.append(w)

        if not below_words:
            return []

        # Sort by x position
        below_words.sort(key=lambda w: float(w['x0']))

        # Filter to words on the same text line (same y within 3pt of the
        # majority y).  This avoids picking up stray words from a different
        # row that happen to be close vertically.
        if below_words:
            # Find the most common y bucket (rounded to nearest pt)
            from collections import Counter
            y_buckets = Counter(round(float(w['top'])) for w in below_words)
            dominant_y = y_buckets.most_common(1)[0][0]
            below_words = [
                w for w in below_words
                if abs(float(w['top']) - dominant_y) <= 3
            ]

        if not below_words:
            return []

        # Guard: if the "below" words sit just above another horizontal
        # line, they are labels for THAT line — not split-labels for the
        # current field.  For example:
        #   a. Customer Name: ________________________
        #   b. Customer Email: ________  Phone: ______
        # "Customer Email" sits 6pt below line a's field, but it belongs
        # to line b.  Detect this by checking for an h_line within 20pt
        # below the dominant_y of the below-words.
        words_bottom = max(float(w.get('bottom', w.get('top', 0) + 10))
                           for w in below_words)
        for hl in getattr(page, 'h_lines', []):
            hl_y = float(hl.get('y', hl.get('top', 0)))
            hl_w = float(hl.get('x1', 0)) - float(hl.get('x0', 0))
            if hl_w < 40:
                continue  # skip short lines
            # h_line is within 20pt below the words' bottom edge
            # and below the current field's bottom
            dist_to_line = hl_y - words_bottom
            if 0 <= dist_to_line <= 20 and hl_y > field.y1 + 5:
                return []  # words belong to the next form row

        # Group consecutive words (gap < _WORD_GAP) into clusters
        groups: List[List[Dict]] = []
        current_group: List[Dict] = [below_words[0]]

        for w in below_words[1:]:
            prev_x1 = float(current_group[-1]['x1'])
            cur_x0 = float(w['x0'])
            gap = cur_x0 - prev_x1

            if gap >= _LABEL_GROUP_GAP:
                # Large gap → start a new group
                groups.append(current_group)
                current_group = [w]
            elif gap >= _WORD_GAP:
                # Medium gap — could be a separate group or just a wide space.
                # Check if the previous group's text looks complete.
                groups.append(current_group)
                current_group = [w]
            else:
                # Small gap → same group
                current_group.append(w)

        groups.append(current_group)

        return groups

    @staticmethod
    def _merge_small_groups(
        groups: List[List[Dict]],
        field: ResolvedField,
    ) -> List[List[Dict]]:
        """Merge label groups that would produce fields narrower than
        _MIN_VIABLE_SPLIT.  A narrow split usually means words from the
        same multi-word label were over-separated."""
        if len(groups) < 2:
            return groups

        result = [g[:] for g in groups]  # shallow copy each group

        changed = True
        while changed and len(result) >= 2:
            changed = False
            for i in range(len(result)):
                # Calculate the width of the field this group would produce
                group_x0 = float(result[i][0]['x0'])
                split_x0 = max(field.x0, group_x0)

                if i < len(result) - 1:
                    next_x0 = float(result[i + 1][0]['x0'])
                    split_x1 = next_x0 - 3
                else:
                    split_x1 = field.x1

                split_w = split_x1 - split_x0

                if split_w < _MIN_VIABLE_SPLIT:
                    # Merge with right neighbour (usually same label)
                    if i < len(result) - 1:
                        result[i + 1] = result[i] + result[i + 1]
                        result.pop(i)
                    elif i > 0:
                        # Last group is narrow → merge with left neighbour
                        result[i - 1] = result[i - 1] + result[i]
                        result.pop(i)
                    else:
                        # Single narrow group remaining, cannot merge
                        break
                    changed = True
                    break

        return result

    def _split_field(
        self,
        field: ResolvedField,
        label_groups: List[List[Dict]],
        used_names: Set[str],
    ) -> List[ResolvedField]:
        """
        Split a wide field into N fields based on label groups below it.
        """
        splits: List[ResolvedField] = []

        for i, group in enumerate(label_groups):
            # Field x0: start at the label group's first word x0
            # (but not before the original field's x0)
            group_x0 = float(group[0]['x0'])
            split_x0 = max(field.x0, group_x0)

            # Field x1: end at the next group's x0 - 3pt padding,
            # or at the original field's x1 for the last group
            if i < len(label_groups) - 1:
                next_group_x0 = float(label_groups[i + 1][0]['x0'])
                split_x1 = next_group_x0 - 3
            else:
                split_x1 = field.x1

            # Minimum width check
            if split_x1 - split_x0 < 15:
                continue

            # Build label from group words
            label_text = ' '.join(
                w['text'].strip() for w in group
            ).strip().rstrip(':')

            name = self._unique_name(
                clean_field_name(label_text) or 'Field',
                used_names,
            )
            used_names.add(name)

            fmt_hint, fmt_opts = detect_field_format(None, label_text)

            # Detect signature/date for source upgrade
            source = 'label_below_split'
            label_lower = label_text.lower()
            if 'signature' in label_lower:
                source = 'signature'
            elif label_lower.strip() == 'date' or label_lower.strip().startswith('date'):
                source = 'date'

            splits.append(ResolvedField(
                page=field.page,
                x0=split_x0,
                y0=field.y0,
                x1=split_x1,
                y1=field.y1,
                field_type=FieldType.TEXT,
                source=source,
                name=name,
                label=label_text,
                format_type=fmt_hint,
                format_options=fmt_opts if fmt_opts else None,
                source_line_x0=split_x0,
                source_line_x1=split_x1,
            ))

        return splits

    @staticmethod
    def _find_duplicate_fields(
        all_fields: List[ResolvedField],
        new_splits: List[ResolvedField],
    ) -> Set[int]:
        """
        Find inline_label / form_line fields that duplicate the new splits.

        A field is a duplicate if:
          - its source is NOT label_below_split / grid_gap_fill / signature / date
          - it is on the same page as a split field
          - its vertical centre is within _DUPLICATE_BELOW_DIST pt below the
            split field's bottom edge
          - its horizontal span overlaps at least 50% with the split field
        """
        _KEEP_SOURCES = {
            'label_below_split', 'grid_gap_fill', 'signature', 'date',
            'label_entry_below', 'label_entry_cell', 'split_cell_multi_row',
        }
        dup_ids: Set[int] = set()

        for sf in new_splits:
            sf_cx_start = sf.x0
            sf_cx_end = sf.x1
            sf_w = sf_cx_end - sf_cx_start

            for f in all_fields:
                if id(f) in dup_ids:
                    continue
                if f.source in _KEEP_SOURCES:
                    continue
                if f.page != sf.page:
                    continue
                if f.field_type != FieldType.TEXT:
                    continue

                # Must be vertically overlapping or just below the split
                # field.  Either the field's centre is within the split
                # field's Y span (overlapping duplicate) or within
                # _DUPLICATE_BELOW_DIST pt of its bottom edge (inline dup).
                fcy = (f.y0 + f.y1) / 2
                if fcy < sf.y0 - 3 or fcy > sf.y1 + _DUPLICATE_BELOW_DIST:
                    continue

                # Check horizontal overlap
                overlap_x0 = max(sf_cx_start, f.x0)
                overlap_x1 = min(sf_cx_end, f.x1)
                if overlap_x1 <= overlap_x0:
                    continue
                overlap_w = overlap_x1 - overlap_x0
                f_w = f.x1 - f.x0
                # At least 50% of the smaller field's width must overlap
                min_w = min(sf_w, f_w)
                if min_w > 0 and overlap_w / min_w >= 0.50:
                    dup_ids.add(id(f))

        return dup_ids

    # ------------------------------------------------------------------
    # Narrow presplit field merging
    # ------------------------------------------------------------------

    @staticmethod
    def _merge_narrow_row_fields(
        fields: List[ResolvedField],
    ) -> int:
        """Merge narrow form-line fields into their right row-neighbour.

        FormLineDetector can over-split lines at inline keywords (e.g.
        "installation"), producing a fragment that is too narrow to be a
        useful standalone field.  This method finds such fragments and
        absorbs them into the adjacent field on the same row.

        Only targets TEXT fields from line-based sources narrower than
        ``_NARROW_MERGE_THRESHOLD``.
        """
        remove_ids: Set[int] = set()

        # Build list of candidate narrow fields
        narrow = [
            f for f in fields
            if f.field_type == FieldType.TEXT
            and f.source in _NORMALIZABLE_SOURCES
            and f.width < _NARROW_MERGE_THRESHOLD
        ]
        if not narrow:
            return 0

        # Index all line-based fields by page for fast neighbour lookup
        by_page: Dict[int, List[ResolvedField]] = {}
        for f in fields:
            if f.field_type == FieldType.TEXT and f.source in _NORMALIZABLE_SOURCES:
                by_page.setdefault(f.page, []).append(f)

        for nf in narrow:
            if id(nf) in remove_ids:
                continue

            # Find same-row neighbours (within 5 pt vertically)
            row = sorted(
                [f for f in by_page.get(nf.page, [])
                 if abs(f.y0 - nf.y0) <= 5 and id(f) not in remove_ids],
                key=lambda f: f.x0,
            )
            idx = next((i for i, f in enumerate(row) if id(f) == id(nf)), None)
            if idx is None:
                continue

            # Prefer merging into the RIGHT neighbour
            if idx < len(row) - 1:
                right = row[idx + 1]
                right.x0 = nf.x0
                remove_ids.add(id(nf))
            elif idx > 0:
                left = row[idx - 1]
                left.x1 = nf.x1
                remove_ids.add(id(nf))

        if remove_ids:
            fields[:] = [f for f in fields if id(f) not in remove_ids]

        return len(remove_ids)

    # ------------------------------------------------------------------
    # Signature height normalisation
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_signature_heights(
        fields: List[ResolvedField],
        pages: List[PageModel],
    ) -> int:
        """Expand short signature fields to match the grid cell height.

        Signature fields are detected at their natural (short) height
        from the form-line detector, but in table/grid layouts they
        should match the surrounding cell height for visual consistency.

        For each short signature field (h < 15 pt), finds the median
        tall-field height on the page and expands the field's y0
        upward while keeping y1 (bottom edge) unchanged — so the field
        fills the full cell above the h-line.
        """
        _PADDING = 1.5
        adjusted = 0

        for page in pages:
            # Find target height from tall grid-aware fields on this page
            tall_heights = sorted(
                f.height for f in fields
                if f.page == page.page_num
                and f.source in ('grid_gap_fill', 'label_below_split')
                and f.height >= _MAX_SHORT_HEIGHT
            )
            if not tall_heights:
                continue

            target_h = tall_heights[len(tall_heights) // 2]  # median

            # Find short signature fields on this page
            sig_fields = [
                f for f in fields
                if f.page == page.page_num
                and f.source == 'signature'
                and f.height < _MAX_SHORT_HEIGHT
            ]
            if not sig_fields:
                continue

            # Collect h-line y positions for snapping
            h_lines_info: List[Tuple[float, float, float]] = []
            for hl in getattr(page, 'h_lines', []):
                h_y = float(hl.get('top', hl.get('y0', 0)))
                h_x0 = float(hl.get('x0', 0))
                h_x1 = float(hl.get('x1', 0))
                if h_x1 - h_x0 >= 200:
                    h_lines_info.append((h_y, h_x0, h_x1))
            h_lines_info.sort()
            h_ys = [h[0] for h in h_lines_info]

            for f in sig_fields:
                # Find nearest h-line below the field
                best_hl_y: Optional[float] = None
                best_dist = float('inf')
                for h_y in h_ys:
                    dist = abs(f.y1 - h_y)
                    if dist < best_dist:
                        best_dist = dist
                        best_hl_y = h_y

                if best_hl_y is None or best_dist > 20:
                    # No nearby h-line — just expand upward by target_h
                    f.y0 = f.y1 - target_h
                    adjusted += 1
                    continue

                # Snap to grid: position just above the h-line
                new_y1 = best_hl_y - _PADDING
                new_y0 = new_y1 - target_h

                # Clamp y0 so we don't overlap the previous h-line
                for h_y in h_ys:
                    if h_y < best_hl_y and h_y > new_y0 - 5:
                        new_y0 = max(new_y0, h_y + _PADDING)

                f.y0 = new_y0
                f.y1 = new_y1
                adjusted += 1

        return adjusted

    # ------------------------------------------------------------------
    # Gap-fill absorption
    # ------------------------------------------------------------------

    @staticmethod
    def _absorb_adjacent_gap_fills(
        fields: List[ResolvedField],
        pages: List[PageModel] = None,
    ) -> int:
        """Absorb unlabelled grid_gap_fill fields into adjacent presplit fields.

        On pages where horizontal lines are segmented by invisible vertical
        cell boundaries, ``form_line_presplit`` fields end at a segment
        boundary instead of spanning the full row.  ``GridGapFill`` then
        fills the remaining segments with unlabelled ``(None)`` fields.

        This method finds each labelled presplit field and looks for an
        unlabelled ``grid_gap_fill`` whose left edge touches the presplit's
        right edge **and** whose vertical extent overlaps.  When found,
        the presplit field is extended to cover the gap-fill, and the
        gap-fill is removed.  The process repeats to absorb consecutive
        gap-fill segments.

        Absorption is blocked when the presplit carries an address-type
        label (City/State/ZIP) and the gap-fill area contains a *different*
        address label on the page — this prevents State from swallowing
        the adjacent ZIP column.
        """
        _ADDR_LABELS = {'city', 'state', 'zip'}

        # Quick check: any gap-fill fields at all?
        has_gap_fill = any(f.source == 'grid_gap_fill' for f in fields)
        if not has_gap_fill:
            return 0

        # Build page model index
        pages_by_num: Dict[int, PageModel] = {}
        if pages:
            pages_by_num = {p.page_num: p for p in pages}

        remove_ids: Set[int] = set()

        # Index unlabelled gap-fill fields by page
        gf_by_page: Dict[int, List[ResolvedField]] = {}
        for f in fields:
            if f.source == 'grid_gap_fill' and not f.label:
                gf_by_page.setdefault(f.page, []).append(f)

        for f in fields:
            # Only consider labelled presplit text fields
            if (f.source != 'form_line_presplit'
                    or not f.label
                    or f.field_type != FieldType.TEXT):
                continue

            page_gfs = gf_by_page.get(f.page, [])
            if not page_gfs:
                continue

            # Determine if this presplit has an address-type label
            f_label_low = (f.label or '').lower()
            f_addr_kw = next((kw for kw in _ADDR_LABELS
                              if kw in f_label_low), None)

            # Iteratively absorb adjacent gap-fills to the right
            changed = True
            while changed:
                changed = False
                for gf in page_gfs:
                    if id(gf) in remove_ids:
                        continue
                    # Left edge of gap-fill must touch right edge of presplit
                    if abs(gf.x0 - f.x1) >= 3:
                        continue
                    # Require at least 5 pt of genuine vertical overlap —
                    # prevents absorbing gap-fills from the row below that
                    # merely touch at a shared h-line boundary.
                    overlap = min(gf.y1, f.y1) - max(gf.y0, f.y0)
                    if overlap < 5:
                        continue

                    # Block absorption when the gap-fill area holds a
                    # different address label (e.g. "ZIP" next to "State").
                    if f_addr_kw and pages_by_num:
                        page_m = pages_by_num.get(f.page)
                        if page_m:
                            zone_words = page_m.get_words_in_bbox((
                                gf.x0, gf.y0 - 5, gf.x1, gf.y1 + 15))
                            other_kws = {
                                w.get('text', '').lower()
                                for w in zone_words
                            } & (_ADDR_LABELS - {f_addr_kw})
                            if other_kws:
                                break  # don't absorb — different addr col

                    # Absorb: extend presplit to cover gap-fill (x and y)
                    f.x1 = gf.x1
                    f.y0 = min(f.y0, gf.y0)
                    f.y1 = max(f.y1, gf.y1)
                    remove_ids.add(id(gf))
                    changed = True
                    break  # restart loop since x1 changed

        if remove_ids:
            fields[:] = [f for f in fields if id(f) not in remove_ids]

        return len(remove_ids)

    # ------------------------------------------------------------------
    # Height & x-position normalisation
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_line_fields(
        fields: List[ResolvedField],
        pages: List[PageModel],
    ) -> int:
        """Normalize height and x-position of line-based fields.

        For short (< 15 pt) fields from form_line_segment, form_line_presplit,
        or label_below_split sources:
          • Expand height to match the median grid-cell field height on the page.
          • Position the field just above the h-line it sits on, filling the
            row (consistent with grid_gap_fill field placement).
          • Extend x0 to the h-line's left edge for the first field in a row.
          • Fill x-gaps between adjacent fields in the same row.

        Only activates when tall reference fields (grid_gap_fill or
        label_below_split with height ≥ 15 pt) exist on the page — this
        naturally limits it to the 2nd pass (after GridGapFill).

        Returns the number of fields adjusted.
        """
        _PADDING = 1.5
        adjusted = 0

        for page in pages:
            # Collect wide h-lines on this page
            h_lines_info: List[Tuple[float, float, float]] = []
            for hl in getattr(page, 'h_lines', []):
                h_y = float(hl.get('top', hl.get('y0', 0)))
                h_x0 = float(hl.get('x0', 0))
                h_x1 = float(hl.get('x1', 0))
                if h_x1 - h_x0 >= 200:
                    h_lines_info.append((h_y, h_x0, h_x1))
            h_lines_info.sort()

            if len(h_lines_info) < 2:
                continue

            h_ys = [h[0] for h in h_lines_info]

            # Find target height from tall grid-aware fields on this page
            tall_heights = sorted(
                f.height for f in fields
                if f.page == page.page_num
                and f.source in ('grid_gap_fill', 'label_below_split')
                and f.height >= _MAX_SHORT_HEIGHT
            )
            if not tall_heights:
                continue  # no reference height available (1st pass)

            target_h = tall_heights[len(tall_heights) // 2]  # median

            # Find short line-based fields to normalise
            short_fields = [
                f for f in fields
                if f.page == page.page_num
                and f.source in _NORMALIZABLE_SOURCES
                and f.height < _MAX_SHORT_HEIGHT
            ]
            if not short_fields:
                continue

            # Group by row (same y0 within 5 pt)
            short_fields.sort(key=lambda f: (round(f.y0), f.x0))
            rows: List[List[ResolvedField]] = [[short_fields[0]]]
            for f in short_fields[1:]:
                if abs(f.y0 - rows[-1][0].y0) <= 5:
                    rows[-1].append(f)
                else:
                    rows.append([f])

            # Collect ALL fields on this page (any source) for overlap checks
            all_page_fields = [
                f for f in fields
                if f.page == page.page_num
            ]

            for row in rows:
                row.sort(key=lambda f: f.x0)

                # Find nearest h-line to this row's vertical centre
                row_cy = sum(f.y0 + f.y1 for f in row) / (2 * len(row))
                best_idx: Optional[int] = None
                best_dist = float('inf')
                for idx, h_y in enumerate(h_ys):
                    dist = abs(row_cy - h_y)
                    if dist < best_dist:
                        best_dist = dist
                        best_idx = idx

                if best_idx is None or best_dist > 20:
                    continue

                hl_y, hl_x0, hl_x1 = h_lines_info[best_idx]

                # Position fields just above the h-line
                new_y1 = hl_y - _PADDING
                new_y0 = new_y1 - target_h

                # Clamp y0 to stay below previous h-line
                if best_idx > 0:
                    prev_hl_y = h_ys[best_idx - 1]
                    new_y0 = max(new_y0, prev_hl_y + _PADDING)

                for f in row:
                    f.y0 = new_y0
                    f.y1 = new_y1
                    adjusted += 1

                # Find non-normalizable fields on the same row (e.g. signature)
                # that we must NOT overlap when extending x-positions.
                row_ids = {id(f) for f in row}
                row_others = [
                    f for f in all_page_fields
                    if id(f) not in row_ids
                    and f.y1 > new_y0 - 5 and f.y0 < new_y1 + 5
                    and f.x1 > hl_x0 and f.x0 < hl_x1
                ]

                # Extend first field's x0 to h-line's left edge,
                # but stop at any other field's right edge
                if row[0].x0 > hl_x0 + 5:
                    limit_x0 = hl_x0
                    for of in row_others:
                        if of.x1 <= row[0].x0 + 5 and of.x1 > limit_x0:
                            limit_x0 = of.x1 + 3  # leave 3pt gap
                    row[0].x0 = limit_x0

                # Fill x-gaps between adjacent fields in the row
                for i in range(1, len(row)):
                    prev_x1 = row[i - 1].x1
                    if row[i].x0 > prev_x1 + 5:
                        row[i].x0 = prev_x1

                # Extend last field's x1 to h-line's right edge,
                # but stop at any other field's left edge
                if row[-1].x1 < hl_x1 - 5:
                    limit_x1 = hl_x1
                    for of in row_others:
                        if of.x0 >= row[-1].x1 - 5 and of.x0 < limit_x1:
                            limit_x1 = of.x0 - 3  # leave 3pt gap
                    row[-1].x1 = limit_x1

        return adjusted

    # ------------------------------------------------------------------
    # Column alignment
    # ------------------------------------------------------------------

    @staticmethod
    def _align_columns(
        fields: List[ResolvedField],
        pages: List[PageModel],
    ) -> int:
        """Align field edges to consistent column positions across rows.

        For pages with multiple h-lines sharing the same left/right edges:
          • Snap left-column field x0 to the h-line left edge.
          • Find the dominant right-column split point and snap right-column
            field x0 to it (adjusting the preceding field's x1 to match).
          • Snap the last field's x1 to the h-line right edge.

        Only affects TEXT fields on rows that belong to the main table
        (first field x0 within 10 pt of the dominant h-line left edge).
        """
        from collections import Counter

        aligned = 0

        for page in pages:
            # Collect wide h-lines on this page
            h_lines = []
            for hl in getattr(page, 'h_lines', []):
                hx0 = float(hl.get('x0', 0))
                hx1 = float(hl.get('x1', 0))
                if hx1 - hx0 >= 200:
                    h_lines.append((hx0, hx1))
            if len(h_lines) < 2:
                continue

            # Dominant left / right edges
            left_counts = Counter(round(x0) for x0, _ in h_lines)
            dominant_left = float(left_counts.most_common(1)[0][0])
            right_counts = Counter(round(x1) for _, x1 in h_lines)
            dominant_right = float(right_counts.most_common(1)[0][0])

            # Get all TEXT fields on this page
            page_fields = [
                f for f in fields
                if f.page == page.page_num
                and f.field_type == FieldType.TEXT
            ]
            if len(page_fields) < 2:
                continue

            # Group by row (y0 within 8 pt — wide enough to catch
            # signature/date fields that aren't height-normalised but
            # sit on the same h-line as normalised fields).
            page_fields.sort(key=lambda f: (round(f.y0 / 8) * 8, f.x0))
            rows: List[List[ResolvedField]] = [[page_fields[0]]]
            for f in page_fields[1:]:
                if abs(f.y0 - rows[-1][0].y0) <= 8:
                    rows[-1].append(f)
                else:
                    rows.append([f])

            # Collect main-column split points (first right field x0 per row)
            split_xs: List[float] = []
            for row in rows:
                row.sort(key=lambda f: f.x0)
                # Only rows belonging to the main table
                if abs(row[0].x0 - dominant_left) > 10:
                    continue
                if len(row) >= 2:
                    for f in row[1:]:
                        if f.x0 > dominant_left + 100:
                            split_xs.append(f.x0)
                            break

            # Find dominant split point
            target_split: Optional[float] = None
            if len(split_xs) >= 2:
                rounded = Counter(round(sp / 3) * 3 for sp in split_xs)
                top_rounded = float(rounded.most_common(1)[0][0])
                near = [sp for sp in split_xs if abs(sp - top_rounded) < 20]
                if near:
                    target_split = sorted(near)[len(near) // 2]

            # Apply alignment
            for row in rows:
                row.sort(key=lambda f: f.x0)

                # Skip rows not on the main table h-lines
                if abs(row[0].x0 - dominant_left) > 10:
                    continue

                # Left column: snap x0 to h-line left edge
                if row[0].x0 != dominant_left:
                    row[0].x0 = dominant_left
                    aligned += 1

                # Right column: snap first right field to dominant split
                if target_split is not None and len(row) >= 2:
                    for j in range(1, len(row)):
                        if row[j].x0 > dominant_left + 100:
                            if abs(row[j].x0 - target_split) < 50:
                                if row[j].x0 != target_split:
                                    row[j].x0 = target_split
                                    row[j - 1].x1 = target_split - 3
                                    aligned += 1
                            break

                # Last field: snap x1 to h-line right edge
                if abs(row[-1].x1 - dominant_right) <= 10:
                    if row[-1].x1 != dominant_right:
                        row[-1].x1 = dominant_right
                        aligned += 1

        return aligned

    @staticmethod
    def _unique_name(base: str, used: Set[str]) -> str:
        """Generate a unique field name, appending _2, _3, etc. if needed."""
        if base not in used:
            return base
        for i in range(2, 1000):
            candidate = f"{base}_{i}"
            if candidate not in used:
                return candidate
        return f"{base}_x"
