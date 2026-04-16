"""
Post-processor: Same-cell beside / below deduplication.

Some forms have table cells where two different detectors fire
independently:

  - ``split_cell_multi_row`` creates a narrow field BESIDE the label
    text (occupying the right portion of the label row).
  - ``label_entry_below`` creates a full-width field BELOW the label
    text (the entire line beneath the label).

When both exist in the same cell the user sees two overlapping
highlight areas, which is confusing.  This post-processor detects the
pattern and removes the narrower "beside" field, keeping the wider
"below" field.

Detection uses shared right-edge matching (both fields end at the same
cell boundary) and vertical adjacency.

Runs early in the post-processor chain (after LineSnapper, before
AdjacentFieldMerger) so downstream steps don't operate on the
duplicate.
"""

from collections import defaultdict
from typing import List

from ..models import ResolvedField, FieldType, PageModel

# Sources that produce "beside the label" fields.
_BESIDE_SOURCES = {
    'split_cell_multi_row',
    'label_entry_cell',
}

# Sources that produce "below the label" fields.
_BELOW_SOURCES = {
    'label_entry_below',
}

# Right-edge tolerance — two fields whose x1 values differ by less
# than this are considered to share the same cell right boundary.
_X1_TOL = 5.0

# Maximum vertical gap between the beside field's bottom and the
# below field's top for them to be considered part of the same cell.
_Y_GAP_MAX = 15.0


class SameCellDedup:
    """Remove 'beside' fields when a 'below' field exists in the same cell."""

    def process(
        self,
        fields: List[ResolvedField],
        pages: List[PageModel],
    ) -> List[ResolvedField]:
        # Partition into beside / below / other
        beside: List[tuple] = []   # (index, field)
        below: List[tuple] = []    # (index, field)

        for idx, f in enumerate(fields):
            if f.field_type not in (FieldType.TEXT, FieldType.COMB):
                continue
            src = (f.source or '').strip()
            if src in _BESIDE_SOURCES:
                beside.append((idx, f))
            elif src in _BELOW_SOURCES:
                below.append((idx, f))

        if not beside or not below:
            return fields

        # Group below fields by page for fast lookup
        below_by_page: dict[int, List[ResolvedField]] = defaultdict(list)
        for _idx, f in below:
            below_by_page[f.page].append(f)

        to_remove: set = set()

        for idx_b, fb in beside:
            page_below = below_by_page.get(fb.page)
            if not page_below:
                continue

            for fw in page_below:
                # Same cell: right edges match within tolerance
                if abs(fb.x1 - fw.x1) > _X1_TOL:
                    continue
                # The beside field must be narrower (its x0 is further
                # right) — it covers only the area right of the label.
                if fb.x0 <= fw.x0:
                    continue
                # Vertical adjacency: beside field sits above (or
                # overlaps) the below field.
                if fb.y0 > fw.y0:
                    continue
                y_gap = fw.y0 - fb.y1
                if y_gap > _Y_GAP_MAX:
                    continue

                # Match — the beside field is redundant.
                to_remove.add(idx_b)
                break

        if to_remove:
            removed = len(to_remove)
            fields = [f for i, f in enumerate(fields)
                      if i not in to_remove]
            print(f"   SameCellDedup: removed {removed} 'beside' field(s) "
                  f"in favour of 'below' placement")

        return fields
