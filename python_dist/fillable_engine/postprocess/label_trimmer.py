"""
Post-processor: Trim (or remove) fields that overlap printed label text.

Many compact forms place a label like "Company Name:" at the left of a cell
and expect the user to write in the remaining blank space.  Detectors see
the whole cell as one field, so the fillable highlight covers the label —
making the form hard to read.

This post-processor:
  1. Checks every text/comb/signature field for PDF text inside its bbox.
  2. Computes where the text sits (left edge, top edge, or filling the cell).
  3. Trims the field to the right of the text (most common) or below it.
  4. Removes the field entirely when no usable blank area remains.

Runs LAST in the post-processor chain so snapping/height normalisation have
already positioned the fields correctly.
"""

from collections import defaultdict
from typing import List, Tuple

from ..models import ResolvedField, FieldType, PageModel


# --- tunables -----------------------------------------------------------

# A word's x0 must be within this many points of the field's x0 (or
# above it) to count as "at the left edge".
EDGE_TOL = 6.0

# Padding between the text cluster's right/bottom edge and the new
# field boundary.
PAD_X = 3.0
PAD_Y = 2.0

# Minimum remaining width / height for the trimmed field to be kept.
# Anything smaller is too narrow to be a useful fill area.
MIN_REMAIN_W = 20.0
MIN_REMAIN_H = 8.0


class LabelTrimmer:
    """Trim fields so they never overlap printed label text."""

    def process(
        self,
        fields: List[ResolvedField],
        pages: List[PageModel],
    ) -> List[ResolvedField]:
        pages_by_num = {p.page_num: p for p in pages}
        to_remove: set = set()
        trimmed = 0
        removed = 0

        for idx, f in enumerate(fields):
            if f.field_type not in (FieldType.TEXT, FieldType.COMB,
                                    FieldType.SIGNATURE):
                continue

            page = pages_by_num.get(f.page)
            if page is None:
                continue

            words = page.get_words_in_bbox(
                (f.x0, f.y0, f.x1, f.y1), 0.5,
            )
            if not words:
                continue  # no label text inside — nothing to do

            # Filter out underscore-heavy "fill" words (e.g. "______",
            # "________________IWC").  These represent the blank fill
            # area, not label text that should be protected.
            label_words = []
            for w in words:
                txt = w.get('text', '')
                if txt and txt.count('_') >= len(txt) * 0.5:
                    continue  # Fill indicator — skip
                label_words.append(w)

            if not label_words:
                continue  # Only fill-indicator text — nothing to trim

            # Compute text cluster bbox, clipped to the field edges.
            text_x0 = max(f.x0, min(w.get('x0', 0) for w in label_words))
            text_x1 = min(f.x1, max(w.get('x1', 0) for w in label_words))
            text_y0 = max(f.y0, min(w.get('top', 0) for w in label_words))
            text_y1 = min(f.y1, max(w.get('bottom', 0) for w in label_words))

            # ----------------------------------------------------------
            # Strategy 1: trim the LEFT edge rightward past the text
            # ----------------------------------------------------------
            text_at_left = (text_x0 - f.x0) < EDGE_TOL
            if text_at_left:
                new_x0 = text_x1 + PAD_X
                if (f.x1 - new_x0) >= MIN_REMAIN_W:
                    f.x0 = new_x0
                    trimmed += 1
                    continue  # success

            # ----------------------------------------------------------
            # Strategy 2: trim the TOP edge downward past the text
            # ----------------------------------------------------------
            text_at_top = (text_y0 - f.y0) < EDGE_TOL
            if text_at_top:
                new_y0 = text_y1 + PAD_Y
                if (f.y1 - new_y0) >= MIN_REMAIN_H:
                    f.y0 = new_y0
                    trimmed += 1
                    continue  # success

            # ----------------------------------------------------------
            # Strategy 3: trim the RIGHT edge leftward before the text
            # (handles unit labels like "Btuh", "%", "°F" at right edge)
            # ----------------------------------------------------------
            text_at_right = (f.x1 - text_x1) < EDGE_TOL
            if text_at_right:
                new_x1 = text_x0 - PAD_X
                if (new_x1 - f.x0) >= MIN_REMAIN_W:
                    f.x1 = new_x1
                    trimmed += 1
                    continue  # success

            # ----------------------------------------------------------
            # Strategy 4: trim the BOTTOM edge upward before the text
            # (handles labels near the bottom of a cell)
            # ----------------------------------------------------------
            text_at_bottom = (f.y1 - text_y1) < EDGE_TOL
            if text_at_bottom:
                new_y1 = text_y0 - PAD_Y
                if (new_y1 - f.y0) >= MIN_REMAIN_H:
                    f.y1 = new_y1
                    trimmed += 1
                    continue  # success

            # ----------------------------------------------------------
            # Fallbacks — try all four trim directions regardless of
            # where the text sits, picking whatever leaves enough room.
            # ----------------------------------------------------------
            # Right-of-text (move x0 past text)
            new_x0 = text_x1 + PAD_X
            if (f.x1 - new_x0) >= MIN_REMAIN_W:
                f.x0 = new_x0
                trimmed += 1
                continue

            # Below-text (move y0 past text)
            new_y0 = text_y1 + PAD_Y
            if (f.y1 - new_y0) >= MIN_REMAIN_H:
                f.y0 = new_y0
                trimmed += 1
                continue

            # Left-of-text (move x1 before text)
            new_x1 = text_x0 - PAD_X
            if (new_x1 - f.x0) >= MIN_REMAIN_W:
                f.x1 = new_x1
                trimmed += 1
                continue

            # Above-text (move y1 before text)
            new_y1 = text_y0 - PAD_Y
            if (new_y1 - f.y0) >= MIN_REMAIN_H:
                f.y1 = new_y1
                trimmed += 1
                continue

            # No usable blank area — this field is entirely a label cell.
            # But protect fields with certain keywords (contact person,
            # title, signature) — these ARE real form fields even though
            # their labels fill the cell area.
            _PROTECTED_KWS = {'contact', 'person', 'title', 'signature'}
            fname_lower = (f.name or '').lower()
            flabel_lower = (f.label or '').lower()
            if any(kw in fname_lower or kw in flabel_lower
                   for kw in _PROTECTED_KWS):
                continue  # Protected — keep this field
            to_remove.add(idx)
            removed += 1

        if to_remove:
            fields = [f for i, f in enumerate(fields) if i not in to_remove]

        if trimmed or removed:
            print(f"   LabelTrimmer: trimmed {trimmed}, "
                  f"removed {removed} label-only fields")

        return fields
