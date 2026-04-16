"""
Special label field detector.

Ported from v23's _detect_special_label_fields().

Finds labels ending with ':' that have a nearby underline (vector line) below
them, then creates a field on the underline to the right of the label.

Also handles:
- Multiple colon-anchored labels on the same line (e.g. "A: B:")
- Stacked lines below keywords like "Name:", "Address:"
- "Label:" patterns with a nearby horizontal line

Source tags: 'special_label', 'special_structural_header', 'special_structural'

Contract (v24 modular):
    * Receives read-only PageModel objects.
    * Returns List[FieldCandidate] -- never mutates shared state.
    * Does NOT check for overlaps with other detectors (the resolver handles that).
"""

from typing import List, Dict, Optional

from ..models import PageModel, FieldCandidate, FieldType
from ..helpers import clean_field_name, detect_field_format
from .base import BaseDetector


class SpecialLabelDetector(BaseDetector):
    """Detect fields from 'Label:' patterns with nearby underlines."""

    def detect(self, pages: List[PageModel]) -> List[FieldCandidate]:
        candidates: List[FieldCandidate] = []

        for page_model in pages:
            candidates.extend(self._detect_structural_header_fields(page_model))
            candidates.extend(self._detect_colon_line_fields(page_model))
            candidates.extend(self._detect_stacked_line_fields(page_model))

        return candidates

    # ------------------------------------------------------------------
    # Pattern 1: Structural header -- "Label:" with underline below
    # ------------------------------------------------------------------

    def _detect_structural_header_fields(self, page_model: PageModel) -> List[FieldCandidate]:
        """Labels ending ':' that sit on top of a visual underline segment."""
        results: List[FieldCandidate] = []
        words = page_model.words
        lines = page_model.lines
        page_num = page_model.page_num

        for i, word in enumerate(words):
            w_txt = word['text']
            if not w_txt.endswith(':') or len(w_txt) <= 3:
                continue

            word_y = float(word['top'])
            word_x1 = float(word['x1'])
            word_bottom = float(word['bottom'])

            # Search for an underline at or below the word (within 20pt)
            # Extend search 5pt above word bottom for fill-in underlines at baseline
            nearby_underline = [
                l for l in lines
                if abs(float(l.get('y0', l.get('top', 0))) - float(l.get('y1', l.get('bottom', 0)))) < 2
                and float(l.get('top', l.get('y0', 0))) > word_bottom - 5
                and float(l.get('top', l.get('y0', 0))) < word_bottom + 20
                and float(l.get('x0', 0)) < word_x1 + 60
                and (float(l.get('x1', 0)) - float(l.get('x0', 0))) > 50
            ]

            # Skip full-page-width lines (>400pt)
            nearby_underline = [
                l for l in nearby_underline
                if (float(l.get('x1', 0)) - float(l.get('x0', 0))) < 400
            ]

            if not nearby_underline:
                continue

            line = min(nearby_underline, key=lambda l: float(l.get('top', l.get('y0', 0))))
            line_x0 = float(line.get('x0', 0))
            line_x1 = float(line.get('x1', 0))
            # Use line CENTER (not top edge) so field y1 matches what
            # LineSnapper targets — consistent with form_lines/inline_underlines.
            _lt = float(line.get('top', line.get('y0', 0)))
            _lb = float(line.get('bottom', line.get('y1', _lt)))
            line_y = (_lt + _lb) / 2

            field_x0 = max(word_x1 + 3, line_x0)
            field_x1 = line_x1
            field_y0 = line_y - 13
            field_y1 = line_y

            # Safety: check for intervening text between label and line
            intervening_text = False
            if line_y - word_bottom > 5:
                for w_chk in words:
                    if w_chk is word:
                        continue
                    wc_top = float(w_chk['top'])
                    wc_bot = float(w_chk['bottom'])
                    if wc_top > word_bottom + 2 and wc_bot < line_y - 2:
                        wc_x0 = float(w_chk['x0'])
                        wc_x1 = float(w_chk['x1'])
                        if wc_x0 < word_x1 + 10 and wc_x1 > float(word['x0']) - 10:
                            intervening_text = True
                            break

            if intervening_text:
                continue

            # Build full label from preceding text on same line
            row_words = [
                w for w in words
                if abs(float(w['top']) - word_y) < 3 and float(w['x1']) <= word_x1
            ]
            full_label = ' '.join(
                w['text'] for w in sorted(row_words, key=lambda w: float(w['x0']))
            ).rstrip(':')

            # Handle multiple colon-separated labels on the same line
            colon_words = [
                w for w in sorted(row_words, key=lambda w: float(w['x0']))
                if w['text'].endswith(':')
            ]

            if len(colon_words) >= 2:
                # Collect all non-colon words on the same line for
                # intervening-text truncation
                non_colon_row = [
                    w for w in sorted(row_words, key=lambda w: float(w['x0']))
                    if not w['text'].endswith(':')
                ]

                for idx, cw in enumerate(colon_words):
                    label_txt = cw['text'].rstrip(':')
                    anchor_x1 = float(cw['x1'])
                    next_anchor_x0 = (
                        float(colon_words[idx + 1]['x0'])
                        if idx + 1 < len(colon_words)
                        else line_x1
                    )
                    f_x0 = anchor_x1 + 3
                    f_x1 = next_anchor_x0 - 3

                    # Truncate at first intervening non-colon word
                    # e.g. "CFDA: _____ Application Org.:" → field ends
                    # before "Application"
                    for nw in non_colon_row:
                        nw_x0 = float(nw['x0'])
                        if nw_x0 > f_x0 + 5 and nw_x0 < f_x1:
                            f_x1 = nw_x0 - 3
                            break

                    if f_x1 - f_x0 <= 5:
                        continue
                    results.append(FieldCandidate(
                        page=page_num,
                        x0=f_x0, y0=field_y0, x1=f_x1, y1=field_y1,
                        field_type=FieldType.TEXT,
                        source='special_structural_header',
                        name_hint=clean_field_name(label_txt),
                        label=label_txt,
                    ))
            else:
                label_text = full_label.strip().rstrip(':')
                if field_x1 - field_x0 > 5:
                    results.append(FieldCandidate(
                        page=page_num,
                        x0=field_x0, y0=field_y0, x1=field_x1, y1=field_y1,
                        field_type=FieldType.TEXT,
                        source='special_structural_header',
                        name_hint=clean_field_name(label_text),
                        label=label_text,
                    ))

        return results

    # ------------------------------------------------------------------
    # Pattern 2: "Label:" with nearby horizontal line to the right
    # ------------------------------------------------------------------

    def _detect_colon_line_fields(self, page_model: PageModel) -> List[FieldCandidate]:
        """'Label:' followed by 100+ pt of empty space or a nearby line."""
        results: List[FieldCandidate] = []
        words = page_model.words
        lines = page_model.lines
        page_num = page_model.page_num

        for i, word in enumerate(words):
            w_txt = word['text']
            if not w_txt.endswith(':') or len(w_txt) <= 3:
                continue

            word_y = float(word['top'])
            word_x0 = float(word['x0'])
            word_x1 = float(word['x1'])

            # Skip multi-word label phrases (2+ words to left on same line)
            preceding = [
                w for w in words
                if abs(float(w['top']) - word_y) < 4
                and float(w['x1']) < word_x0 + 2
                and w is not word
            ]
            if len(preceding) >= 2:
                continue

            # Find nearby horizontal lines
            nearby_lines = [
                l for l in lines
                if abs(float(l.get('y0', l.get('top', 0))) - float(l.get('y1', l.get('bottom', 0)))) < 2
                and abs(float(l.get('top', l.get('y0', 0))) - word_y) < 12
                and float(l.get('x0', 0)) > word_x1 - 5
            ]

            if not nearby_lines:
                continue

            line = min(nearby_lines, key=lambda l: float(l.get('x0', 0)))
            field_x0 = word_x1 + 3
            field_x1 = float(line.get('x1', 0))
            # Use line CENTER for consistency with other detectors
            _lt2 = float(line.get('top', line.get('y0', 0)))
            _lb2 = float(line.get('bottom', line.get('y1', _lt2)))
            _lc2 = (_lt2 + _lb2) / 2
            field_y0 = _lc2 - 13
            field_y1 = _lc2

            if field_x1 - field_x0 < 10:
                continue

            label_txt = w_txt.strip().rstrip(':')

            # Source line bounds for snapping clamping
            src_x0 = float(line.get('x0', field_x0))
            src_x1 = float(line.get('x1', field_x1))

            results.append(FieldCandidate(
                page=page_num,
                x0=field_x0, y0=field_y0, x1=field_x1, y1=field_y1,
                field_type=FieldType.TEXT,
                source='special_label',
                name_hint=clean_field_name(label_txt),
                label=label_txt,
                source_line_x0=src_x0,
                source_line_x1=src_x1,
            ))

        return results

    # ------------------------------------------------------------------
    # Pattern 3: Stacked lines below keyword labels
    # ------------------------------------------------------------------

    def _detect_stacked_line_fields(self, page_model: PageModel) -> List[FieldCandidate]:
        """Keywords like 'Name:', 'Address:' with 3+ stacked lines below."""
        results: List[FieldCandidate] = []
        words = page_model.words
        lines = page_model.lines
        page_num = page_model.page_num

        stack_keywords = ['Name:', 'Address:', 'Payee:', 'Contractor:']

        for word in words:
            if word['text'] not in stack_keywords:
                continue

            label_bottom = float(word['bottom'])
            stacked_lines = [
                l for l in lines
                if abs(float(l.get('y0', l.get('top', 0))) - float(l.get('y1', l.get('bottom', 0)))) < 2
                and float(l.get('top', l.get('y0', 0))) > label_bottom + 2
                and float(l.get('top', l.get('y0', 0))) < label_bottom + 120
                and (float(l.get('x1', 0)) - float(l.get('x0', 0))) > 150
            ]

            if len(stacked_lines) < 3:
                continue

            line_ys = sorted(set(
                round(float(l.get('top', l.get('y0', 0))))
                for l in stacked_lines
            ))
            label_base = word['text'].rstrip(':')

            for ly in line_ys:
                matching = [
                    l for l in stacked_lines
                    if round(float(l.get('top', l.get('y0', 0)))) == ly
                ]
                f_x0 = min(float(l.get('x0', 0)) for l in matching)
                f_x1 = max(float(l.get('x1', 0)) for l in matching)
                # Use average line center for field positioning
                _avg_center = sum(
                    (float(l.get('top', 0)) + float(l.get('bottom', float(l.get('top', 0))))) / 2
                    for l in matching
                ) / len(matching)

                results.append(FieldCandidate(
                    page=page_num,
                    x0=f_x0, y0=_avg_center - 14, x1=f_x1, y1=_avg_center,
                    field_type=FieldType.TEXT,
                    source='special_label',
                    name_hint=clean_field_name(f"{label_base}_Stack"),
                    label=label_base,
                    source_line_x0=f_x0,
                    source_line_x1=f_x1,
                ))

        return results
