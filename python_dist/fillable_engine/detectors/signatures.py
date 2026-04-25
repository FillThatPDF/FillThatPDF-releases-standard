"""
Signature / date / label-below-underscore detector.

Ported from v23's _detect_signature_fields() and _detect_label_below_underscore_fields().

Two complementary patterns are detected:

Pattern 1  (Label + Underscore text)
    "Signature: ____________"   "Date: ________"
    "Authorized Representative Name: ________"
    Scans words on every page for known fill-in labels followed by 4+ underscores
    (either in a subsequent word or embedded in the same word).

Pattern 2  (Label-below-underscore)
    ________________________________________
    Contractor signature                     Date
    Finds 10+ underscore blocks, then looks 0-18 pt below for a recognized label
    that is horizontally aligned.

Contract (v24 modular):
    * Receives read-only PageModel objects.
    * Returns List[FieldCandidate] -- never mutates shared state.
    * Does NOT check for overlaps with other detectors (the resolver handles that).
"""

from typing import List, Dict, Optional

from ..models import PageModel, FieldCandidate, FieldType
from ..helpers import clean_field_name, detect_field_format
from .base import BaseDetector


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Labels with trailing colon that indicate "fill here" underscore patterns
_FILL_LABELS = [
    'signature:', 'date:', 'name:', 'title:',
    'manager:', 'lead:', 'representative:', 'technician:',
    'supervisor:', 'inspector:', 'contractor:',
]

# Label patterns for the "label BELOW underscore" pass (no colon expected)
_LABEL_BELOW_PATTERNS: Dict[str, Dict] = {
    'date':                   {'format': 'date', 'format_opts': {'maxlen': 10}},
    'signature':              {'format': None,   'format_opts': {}},
    'technician signature':   {'format': None,   'format_opts': {}},
    'technician name':        {'format': None,   'format_opts': {}},
    'contractor signature':   {'format': None,   'format_opts': {}},
    'customer signature':     {'format': None,   'format_opts': {}},
    'name':                   {'format': None,   'format_opts': {}},
    'title':                  {'format': None,   'format_opts': {}},
    'print name':             {'format': None,   'format_opts': {}},
}


class SignatureDetector(BaseDetector):
    """Detect signature, date, name, and title fields from underscore TEXT patterns."""

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def detect(self, pages: List[PageModel]) -> List[FieldCandidate]:
        candidates: List[FieldCandidate] = []

        for page_model in pages:
            candidates.extend(self._detect_label_underscore_fields(page_model))
            candidates.extend(self._detect_label_below_underscore_fields(page_model))

        return candidates

    # ------------------------------------------------------------------
    # Pattern 1: Label + Underscore text  ("Signature: ____________")
    # ------------------------------------------------------------------

    def _detect_label_underscore_fields(self, page_model: PageModel) -> List[FieldCandidate]:
        """Scan words for fill-in labels followed (or containing) underscore runs."""
        results: List[FieldCandidate] = []
        words = page_model.words
        page_num = page_model.page_num
        used_underscore_indices: set = set()

        for i, word in enumerate(words):
            text = word['text'].strip().lower()

            # Check for any fill-in label
            is_fill_label = any(label in text for label in _FILL_LABELS)
            if not is_fill_label:
                continue

            label_y = float(word['top'])
            label_x0 = float(word['x0'])
            label_x1 = float(word['x1'])
            raw_text = word['text']

            # -------------------------------------------------------
            # Case A: Embedded label+underscore  ("Signature:_____")
            # -------------------------------------------------------
            if '____' in raw_text:
                candidate = self._handle_embedded_underscore(
                    words, i, word, text, label_y, label_x0, label_x1,
                    raw_text, page_num,
                )
                if candidate is not None:
                    results.append(candidate)
                    used_underscore_indices.add(i)
                    continue  # Don't also scan ahead

            # -------------------------------------------------------
            # Case B: Look ahead up to 8 words for underscore text
            # -------------------------------------------------------
            for j in range(i + 1, min(i + 8, len(words))):
                if j in used_underscore_indices:
                    continue

                next_word = words[j]
                next_text = next_word['text']
                next_x0 = float(next_word['x0'])
                next_y = float(next_word['top'])

                # Same line (within 5pt) and at least 4 underscores
                if abs(next_y - label_y) < 5 and '____' in next_text:
                    used_underscore_indices.add(j)

                    underscore_start = next_x0
                    underscore_end = float(next_word['x1'])
                    underscore_bottom = float(next_word['bottom'])
                    field_height = float(self.settings.get('field_height', 14))

                    # Determine label, source, format
                    display_label, name_hint, source, fmt_hint, fmt_opts = \
                        self._classify_fill_label(words, i, text, label_y)

                    # Use +1 padding on y1 (consistent with embedded_underscore detector)
                    # so the writer's underscore shift compensates correctly.
                    results.append(FieldCandidate(
                        page=page_num,
                        x0=underscore_start,
                        y0=underscore_bottom + 1 - field_height,
                        x1=underscore_end,
                        y1=underscore_bottom + 1,
                        field_type=FieldType.TEXT,
                        source=source,
                        name_hint=name_hint,
                        label=display_label,
                        format_hint=fmt_hint,
                        format_options=fmt_opts if fmt_opts else None,
                        source_line_x0=underscore_start,
                        source_line_x1=underscore_end,
                    ))
                    break  # Only take the first matching underscore block

        return results

    # ------------------------------------------------------------------
    # Pattern 2: Label below underscore
    # ------------------------------------------------------------------

    def _detect_label_below_underscore_fields(self, page_model: PageModel) -> List[FieldCandidate]:
        """Find underscore blocks with recognized labels 0-18 pt below."""
        results: List[FieldCandidate] = []
        words = page_model.words
        page_num = page_model.page_num

        # Collect all underscore blocks (10+ underscore chars)
        underscore_blocks: List[Dict] = []
        for i, word in enumerate(words):
            if '____' in word['text'] and len(word['text']) >= 10:
                underscore_blocks.append({
                    'index': i,
                    'x0': float(word['x0']),
                    'x1': float(word['x1']),
                    'top': float(word['top']),
                    'bottom': float(word['bottom']),
                    'used': False,
                })

        for uscore in underscore_blocks:
            if uscore['used']:
                continue

            # Scan every word looking for a label below this underscore
            for word in words:
                word_text = word['text'].lower().strip()
                word_y = float(word['top'])
                word_x0 = float(word['x0'])
                word_x1 = float(word['x1'])

                # Must be 0-18 pt below the underscore bottom
                y_diff = word_y - uscore['bottom']
                if y_diff < 0 or y_diff > 18:
                    continue

                # Horizontally aligned: label center within underscore x-range (+/- 10pt)
                word_center = (word_x0 + word_x1) / 2
                if word_center < uscore['x0'] - 10 or word_center > uscore['x1'] + 10:
                    continue

                # Build full label from all same-line words under the underscore span
                same_line_words = [
                    w for w in words
                    if abs(float(w['top']) - word_y) < 3
                    and uscore['x0'] - 10 <= (float(w['x0']) + float(w['x1'])) / 2 <= uscore['x1'] + 10
                ]
                same_line_words.sort(key=lambda w: float(w['x0']))
                full_label = ' '.join(w['text'].lower().strip() for w in same_line_words)

                # Match against known label patterns
                matched_pattern: Optional[str] = None
                field_format: Optional[str] = None
                format_opts: Dict = {}

                for pattern, config in _LABEL_BELOW_PATTERNS.items():
                    if pattern in full_label:
                        matched_pattern = pattern
                        field_format = config['format']
                        format_opts = config['format_opts']
                        break

                if not matched_pattern:
                    continue

                # Build candidate on the underscore line
                field_height = float(self.settings.get('field_height', 14))
                field_x0 = uscore['x0']
                field_x1 = uscore['x1']
                # Use +1 padding on y1 (consistent with embedded_underscore detector)
                field_y1 = uscore['bottom'] + 1
                field_y0 = field_y1 - field_height

                clean_label = matched_pattern.replace(' ', '_').title()
                name_hint = clean_field_name(clean_label)

                results.append(FieldCandidate(
                    page=page_num,
                    x0=field_x0,
                    y0=field_y0,
                    x1=field_x1,
                    y1=field_y1,
                    field_type=FieldType.TEXT,
                    source='label_below_underscore',
                    name_hint=name_hint,
                    label=matched_pattern.title(),
                    format_hint=field_format,
                    format_options=format_opts if format_opts else None,
                    source_line_x0=field_x0,
                    source_line_x1=field_x1,
                ))

                uscore['used'] = True
                break  # One label per underscore block

        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _handle_embedded_underscore(
        self,
        words: List[Dict],
        i: int,
        word: Dict,
        text_lower: str,
        label_y: float,
        label_x0: float,
        label_x1: float,
        raw_text: str,
        page_num: int,
    ) -> Optional[FieldCandidate]:
        """Handle a word that contains both the label and underscores (e.g. 'Signature:_____')."""
        underscore_idx = raw_text.find('____')

        # Estimate where underscores start within the combined word.
        # We don't have page.chars in the modular architecture, so use the
        # character-ratio estimation that v23 falls back to.
        ratio = underscore_idx / max(1, len(raw_text))
        underscore_start_x = label_x0 + (label_x1 - label_x0) * ratio
        underscore_end_x = label_x1
        underscore_width = underscore_end_x - underscore_start_x

        if underscore_width <= 20:
            return None  # Too narrow to be a real field

        # Classify
        display_label, name_hint, source, fmt_hint, fmt_opts = \
            self._classify_fill_label(words, i, text_lower, label_y)

        wb = float(word['bottom'])

        return FieldCandidate(
            page=page_num,
            x0=underscore_start_x,
            y0=wb - 13,
            x1=underscore_end_x,
            y1=wb + 1,
            field_type=FieldType.TEXT,
            source=source,
            name_hint=name_hint,
            label=display_label,
            format_hint=fmt_hint,
            format_options=fmt_opts if fmt_opts else None,
            source_line_x0=underscore_start_x,
            source_line_x1=underscore_end_x,
        )

    def _classify_fill_label(
        self,
        words: List[Dict],
        idx: int,
        text_lower: str,
        label_y: float,
    ):
        """
        From the matched label word, determine:
            display_label  - human-readable label text
            name_hint      - cleaned field name hint
            source         - 'signature', 'date', or 'signature' (generic fill)
            fmt_hint       - format string or None
            fmt_opts       - format options dict
        """

        if 'signature' in text_lower:
            full_label = self._build_lookback_label(words, idx, label_y, lookback=4)
            if not full_label:
                full_label = "Signature"
            return full_label, clean_field_name(full_label), 'signature', None, {}

        if 'date' in text_lower:
            return "Date", "Date", 'date', 'date', {'maxlen': 10}

        if 'name' in text_lower:
            full_label = self._build_lookback_label(words, idx, label_y, lookback=3)
            if not full_label:
                full_label = "Name"
            return full_label, clean_field_name(full_label), 'signature', None, {}

        if 'title' in text_lower:
            return "Title", "Title", 'signature', None, {}

        if 'manager' in text_lower:
            full_label = self._build_lookback_label(words, idx, label_y, lookback=2)
            if not full_label:
                full_label = "Manager"
            return full_label, clean_field_name(full_label), 'signature', None, {}

        if 'lead' in text_lower:
            full_label = self._build_lookback_label(words, idx, label_y, lookback=3)
            if not full_label:
                full_label = "Lead"
            return full_label, clean_field_name(full_label), 'signature', None, {}

        # Generic role labels: representative, technician, supervisor, inspector, contractor
        for role in ('representative', 'technician', 'supervisor', 'inspector', 'contractor'):
            if role in text_lower:
                full_label = self._build_lookback_label(words, idx, label_y, lookback=2)
                if not full_label:
                    full_label = text_lower.split(':')[0].strip().title()
                return full_label, clean_field_name(full_label), 'signature', None, {}

        # Fallback for any other matched fill label
        raw_label = text_lower.split(':')[0].strip().title()
        return raw_label, clean_field_name(raw_label), 'signature', None, {}

    @staticmethod
    def _build_lookback_label(
        words: List[Dict],
        idx: int,
        label_y: float,
        lookback: int = 4,
    ) -> str:
        """
        Build a multi-word label by looking back up to *lookback* words from the
        current word, collecting same-line text.  For example, from the word
        'Signature:' at index 5, looking back 4 words could yield
        'Authorized Representative Signature'.
        """
        parts: List[str] = []
        start = max(0, idx - lookback)
        for k in range(start, idx + 1):
            w = words[k]
            if abs(float(w['top']) - label_y) < 3:
                cleaned = w['text'].split(':')[0].rstrip(':').strip()
                if cleaned:
                    parts.append(cleaned)
        return ' '.join(parts) if parts else ""
