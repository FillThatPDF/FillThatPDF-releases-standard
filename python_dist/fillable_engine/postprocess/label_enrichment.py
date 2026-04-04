"""
Post-processor: Checkbox label enrichment.

Runs AFTER the resolver commits fields and BEFORE the writer creates widgets.
Enriches checkbox labels by looking at column headers above and text beside
the checkbox, so that radio-group identification (which runs next) has
accurate labels to work with.

Ported from v23 _enrich_checkbox_labels_from_column_headers and
_enrich_checkbox_labels_from_beside_text.
"""

from typing import Dict, List, Optional

from ..models import ResolvedField, FieldType, PageModel
from ..helpers import clean_field_name

# Re-use the canonical keyword map (same as radio_groups module but kept
# self-contained so label enrichment can run independently).
RADIO_COLUMN_KEYWORDS: Dict[str, str] = {
    'pass': 'Pass',
    'fail': 'Fail',
    'yes': 'Yes',
    'no': 'No',
    'n/a': 'NA',
    'na': 'NA',
    'not applicable': 'Not Applicable',
    'maybe': 'Maybe',
    "don't know": "Don't Know",
    'unknown': 'Unknown',
    'approved': 'Approved',
    'denied': 'Denied',
    'approve': 'Approve',
    'deny': 'Deny',
    'accept': 'Accept',
    'reject': 'Reject',
    'complete': 'Complete',
    'incomplete': 'Incomplete',
    'satisfactory': 'Satisfactory',
    'unsatisfactory': 'Unsatisfactory',
    'natural gas': 'Natural Gas',
    'naturalgas': 'Natural Gas',
    'gas': 'Gas',
    'electric': 'Electric',
    'propane': 'Propane',
    'oil': 'Oil',
    'solar': 'Solar',
    'wood': 'Wood',
    'none': 'None',
    'other': 'Other',
}

# Characters that represent checkbox glyphs -- skip when scanning beside text
from ..page_analyzer import _UNICODE_CHECKBOX_CHARS
_CHECKBOX_GLYPHS = _UNICODE_CHECKBOX_CHARS | frozenset([''])


class LabelEnricher:
    """
    Enrich checkbox labels using contextual text from the page.

    Two enrichment passes:
    1. **Column headers** -- look for radio-keyword text (Pass / Fail / Yes /
       No / N/A ...) that is horizontally aligned above the checkbox (up to
       500pt above).
    2. **Beside text** -- look for radio-keyword text immediately to the
       right of the checkbox (within 30pt horizontal, 12pt vertical).

    Only checkboxes whose current label is *not* already a radio keyword
    are candidates for enrichment.  Multi-word labels are never overwritten
    by beside-text (prevents "Electric Resistance" -> "Electric").
    """

    def process(
        self,
        fields: List[ResolvedField],
        pages: List[PageModel],
    ) -> List[ResolvedField]:
        """Enrich labels in-place.  Returns the same *fields* list."""
        pages_by_num = {p.page_num: p for p in pages}

        cb_fields = [f for f in fields if f.field_type == FieldType.CHECKBOX]
        if not cb_fields:
            return fields

        for f in cb_fields:
            page = pages_by_num.get(f.page)
            if page is None:
                continue

            current_label = (f.label or '').lower().strip()

            # Skip if already has a radio-keyword label
            if current_label in RADIO_COLUMN_KEYWORDS:
                continue

            # PASS 1 -- column header enrichment
            header = self._find_column_header(f, page)
            if header:
                f.label = header
                f.name = clean_field_name(header)
                continue  # header wins; no need for beside-text

            # PASS 2 -- beside-text enrichment
            # Skip if the checkbox already has a meaningful multi-word label
            if current_label and len(current_label.split()) >= 2:
                continue

            beside = self._find_beside_text(f, page)
            if beside:
                f.label = beside
                f.name = clean_field_name(beside)

        return fields

    # -- column header lookup -----------------------------------------------

    @staticmethod
    def _find_column_header(
        field: ResolvedField,
        page: PageModel,
    ) -> Optional[str]:
        """
        Search upward from *field* for a radio-keyword column header.

        Returns the properly-capitalised keyword if found, else None.
        """
        cb_cx = (field.x0 + field.x1) / 2
        cb_y = field.y0

        best: Optional[tuple] = None
        for w in page.words:
            text = w.get('text', '').strip().lower()
            if text not in RADIO_COLUMN_KEYWORDS:
                continue
            wx0 = float(w.get('x0', 0))
            wx1 = float(w.get('x1', 0))
            wy = float(w.get('top', 0))
            wcx = (wx0 + wx1) / 2

            y_diff = cb_y - wy
            if not (10 <= y_diff <= 500):
                continue
            if abs(wcx - cb_cx) > 20:
                continue
            if best is None or y_diff < best[0]:
                best = (y_diff, RADIO_COLUMN_KEYWORDS[text])

        return best[1] if best else None

    # -- beside-text lookup -------------------------------------------------

    @staticmethod
    def _find_beside_text(
        field: ResolvedField,
        page: PageModel,
    ) -> Optional[str]:
        """
        Look for a radio keyword immediately to the RIGHT of the checkbox.

        Only enriches if the radio keyword is the closest word (within 2pt)
        to prevent overwriting with a more distant keyword when descriptive
        text is closer.

        Returns the properly-capitalised keyword if found, else None.
        """
        cb_right = field.x1
        cb_center_y = (field.y0 + field.y1) / 2

        best_match: Optional[str] = None
        best_dist = float('inf')
        closest_any_dist = float('inf')

        for w in page.words:
            wx0 = float(w.get('x0', 0))
            wy = float(w.get('top', 0))
            text = w.get('text', '').strip()
            text_lower = text.lower()

            # Must be to the right, within 30pt
            if wx0 < cb_right or wx0 > cb_right + 30:
                continue

            # Must be on the same line (within 12pt vertically)
            if abs(wy - cb_center_y) > 12:
                continue

            # Skip checkbox glyphs
            if text_lower in _CHECKBOX_GLYPHS or text == '':
                continue

            dist = wx0 - cb_right

            if dist < closest_any_dist:
                closest_any_dist = dist

            if text_lower in RADIO_COLUMN_KEYWORDS and dist < best_dist:
                best_dist = dist
                best_match = RADIO_COLUMN_KEYWORDS[text_lower]

        # Only accept if the keyword is the closest word (within 2pt tolerance)
        if best_match and best_dist <= closest_any_dist + 2:
            return best_match
        return None
