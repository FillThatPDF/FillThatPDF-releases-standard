"""
Inline label field detector.

Ported from v23's _detect_inline_label_fields().

Detects inline label patterns like "City: ___ State: ___ ZIP: ___" on form
lines.  These are rows of words containing known field-label keywords
(City, State, ZIP, Phone, etc.) that are NOT in table cells.

Source tag: 'inline_label'

Contract (v24 modular):
    * Receives read-only PageModel objects.
    * Returns List[FieldCandidate] -- never mutates shared state.
    * Does NOT check for overlaps with other detectors (the resolver handles that).
"""

from collections import defaultdict
from typing import List, Dict, Optional

from ..models import PageModel, FieldCandidate, FieldType
from ..helpers import clean_field_name, detect_field_format
from .base import BaseDetector


# Known inline labels (lowercase, no colons)
_INLINE_LABELS = frozenset([
    'city', 'state', 'zip', 'phone', 'fax', 'email', 'e-mail',
    'name', 'address', 'company', 'title', 'county', 'contact', 'person',
    'date', 'application', 'street', 'account', 'electric', 'completion',
    'received', 'approved', 'project', 'manager', 'rep', 'incentive',
    'final', 'pre', 'telephone', 'customer',
])


class InlineLabelDetector(BaseDetector):
    """Detect City/State/ZIP-style inline label fields."""

    def detect(self, pages: List[PageModel]) -> List[FieldCandidate]:
        candidates: List[FieldCandidate] = []

        for page_model in pages:
            candidates.extend(self._detect_inline_labels(page_model))

        return candidates

    # ------------------------------------------------------------------
    # Core detection
    # ------------------------------------------------------------------

    def _detect_inline_labels(self, page_model: PageModel) -> List[FieldCandidate]:
        results: List[FieldCandidate] = []
        words = page_model.words
        lines = page_model.lines
        page_num = page_model.page_num
        page_width = page_model.width

        # Collect horizontal line segments
        h_lines: List[Dict] = []
        for line in lines:
            y = float(line.get('top', line.get('y0', 0)))
            lx0 = float(line.get('x0', 0))
            lx1 = float(line.get('x1', 0))
            if (lx1 - lx0) > 20:
                h_lines.append({'y': y, 'x0': lx0, 'x1': lx1})

        # Group words by Y position (5pt buckets)
        by_row: Dict[float, List[Dict]] = defaultdict(list)
        for word in words:
            row_y = round(float(word['top']) / 5) * 5
            by_row[row_y].append(word)

        sorted_row_ys = sorted(by_row.keys())

        for row_idx, row_y in enumerate(sorted_row_ys):
            row_words = sorted(by_row[row_y], key=lambda w: float(w['x0']))

            # Find label words matching known inline labels
            labels_on_row: List[Dict] = []
            for i, word in enumerate(row_words):
                text = word['text'].strip()
                text_lower = text.lower().replace(':', '')

                if text_lower not in _INLINE_LABELS:
                    continue

                # Build full label by collecting preceding words up to previous label
                full_label_parts = []
                prev_label_end = labels_on_row[-1]['index'] + 1 if labels_on_row else 0
                for j in range(prev_label_end, i + 1):
                    wt = row_words[j]['text'].strip()
                    if wt:
                        full_label_parts.append(wt.replace(':', ''))

                full_label = ' '.join(full_label_parts)

                # If label is alone on row, add context from previous row
                if len(full_label_parts) == 1 and row_idx > 0:
                    prev_row_y = sorted_row_ys[row_idx - 1]
                    prev_words = sorted(by_row[prev_row_y], key=lambda w: float(w['x0']))
                    prev_context = []
                    for pw in prev_words:
                        pw_text = pw['text'].strip().replace(':', '')
                        prev_context.append(pw_text)
                        if pw_text.lower() in ('address', 'information', 'details'):
                            break
                    if prev_context:
                        context_str = ' '.join(prev_context[:2])
                        full_label = f"{context_str} {full_label}"

                labels_on_row.append({
                    'word': word,
                    'index': i,
                    'x0': float(word['x0']),
                    'x1': float(word['x1']),
                    'y': float(word['top']),
                    'label': text.replace(':', '').strip(),
                    'full_label': full_label,
                })

            # Need at least 2 labels on the same row
            if len(labels_on_row) < 2:
                continue

            # Skip rows with too many words — these are body text paragraphs
            # (Terms & Conditions, fine print), not form label rows.
            # Form label rows typically have ≤10 words (e.g. "City  State  ZIP")
            if len(row_words) > 12:
                continue

            # Find line near this row of labels
            label_y = labels_on_row[0]['y']
            row_lines = [
                (ly, lx0, lx1) for ly, lx0, lx1 in
                [(h['y'], h['x0'], h['x1']) for h in h_lines]
                if 3 < (ly - label_y) < 20
            ]
            lines_above = [
                (ly, lx0, lx1) for ly, lx0, lx1 in
                [(h['y'], h['x0'], h['x1']) for h in h_lines]
                if -15 < (ly - label_y) < 0
            ]

            # Create fields between labels
            for i, label in enumerate(labels_on_row):
                label_text = label['label'].lower()

                # Try to find a matching underline
                matching_line = self._find_matching_line(
                    label, labels_on_row, i, row_lines, lines_above
                )

                # Default field height from settings (same as FormLineDetector)
                default_fh = int(self.settings.get('field_height', 14))

                if matching_line:
                    line_y, line_x0, line_x1 = matching_line
                    field_x0 = max(line_x0, label['x1'] + 2)

                    if i + 1 >= len(labels_on_row):
                        field_x1 = page_width - 27
                    else:
                        field_x1 = line_x1

                    # Anchor field ON the underline, not below the label
                    field_y1 = line_y
                    field_y0 = field_y1 - default_fh
                else:
                    # No matching underline — skip this field.  Without an
                    # underline the position is unreliable and the
                    # FormLineDetector will handle the label on its actual
                    # form line instead.
                    continue

                field_width = field_x1 - field_x0

                # Handle narrow gaps
                if field_width < 20 and i + 1 < len(labels_on_row):
                    next_label = labels_on_row[i + 1]
                    field_x0 = label['x0'] - 5
                    field_x1 = next_label['x0'] - 3
                    label_y_top = label['y']
                    field_y1 = label_y_top + default_fh
                    field_y0 = label_y_top
                    field_width = field_x1 - field_x0

                max_width = 350 if label_text == 'city' else 250
                if field_width < 15 or field_width > max_width:
                    continue

                field_format, format_opts = detect_field_format(None, label['label'])

                results.append(FieldCandidate(
                    page=page_num,
                    x0=field_x0,
                    y0=field_y0,
                    x1=field_x1,
                    y1=field_y1,
                    field_type=FieldType.TEXT,
                    source='inline_label',
                    name_hint=clean_field_name(label['label']),
                    label=label.get('full_label', label['label']),
                    format_hint=field_format,
                    format_options=format_opts if format_opts else None,
                ))

        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_matching_line(
        label: Dict,
        labels_on_row: List[Dict],
        idx: int,
        row_lines: List,
        lines_above: List,
    ) -> Optional[tuple]:
        """Find the best matching underline for this label."""
        for line_y, lx0, lx1 in row_lines:
            label_x1 = label['x1']
            label_x0 = label.get('x0', label_x1 - 50)

            if lx0 > label_x1 - 10 or (lx0 <= label_x0 + 10 and lx1 > label_x1):
                if idx + 1 < len(labels_on_row):
                    next_label = labels_on_row[idx + 1]
                    if lx1 < next_label['x0'] + 10:
                        return (line_y, lx0, lx1)
                else:
                    if lx1 > 500:
                        return (line_y, lx0, lx1)

        # Try lines above (Avangrid pattern)
        for line_y, lx0, lx1 in lines_above:
            label_x1 = label['x1']
            label_x0 = label.get('x0', label_x1 - 50)

            if lx0 > label_x1 - 10 or (lx0 <= label_x0 + 10 and lx1 > label_x1):
                if idx + 1 < len(labels_on_row):
                    next_label = labels_on_row[idx + 1]
                    if lx1 < next_label['x0'] + 10:
                        return (line_y, lx0, lx1)
                else:
                    if lx1 > 500:
                        return (line_y, lx0, lx1)

        return None
