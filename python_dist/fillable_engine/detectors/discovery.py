"""
Discovery detector -- final pass for missing labeled fields.

Ported from v23's _discovery_pass_missing_labeled_fields().

This is a "self-review" scan that finds labels that do not yet have an
associated field.  It looks for:

Case A: A colon-ending label with a horizontal underline after it.
Case B: A colon-ending label with a significant empty gap (>40pt) after it.
Case C: A question-mark-ending label with an empty gap after it.
Case D: Aligned column gaps — if 3+ rows share the same label-end X
        position with gaps after them, those gaps are fill-in fields.

All cases verify the discovered area is truly empty (no text inside).

Source tags:
    'discovery_discovery_line'  (Case A)
    'discovery_discovery_gap'   (Case B)
    'discovery_question_gap'    (Case C)
    'discovery_aligned_gap'     (Case D)

Contract (v24 modular):
    * Receives read-only PageModel objects.
    * Returns List[FieldCandidate] -- never mutates shared state.
    * Does NOT check for overlaps with other detectors (the resolver handles that).
"""

from collections import defaultdict
from typing import List, Dict, Optional, Tuple

from ..models import PageModel, FieldCandidate, FieldType
from ..helpers import clean_field_name
from .base import BaseDetector


class DiscoveryDetector(BaseDetector):
    """Final discovery pass that finds labeled fields other detectors missed."""

    def detect(self, pages: List[PageModel]) -> List[FieldCandidate]:
        candidates: List[FieldCandidate] = []

        for page_model in pages:
            candidates.extend(self._discover_missing_fields(page_model))
            candidates.extend(self._discover_whitespace_fields(page_model))

        return candidates

    # ------------------------------------------------------------------
    # Core detection
    # ------------------------------------------------------------------

    def _discover_missing_fields(self, page_model: PageModel) -> List[FieldCandidate]:
        results: List[FieldCandidate] = []
        words = page_model.words
        lines = page_model.lines
        page_num = page_model.page_num
        page_width = page_model.width

        # Group words by row for context lookups
        words_by_row: Dict[int, List[Dict]] = defaultdict(list)
        for w in words:
            ry = round(float(w['top']) / 3) * 3
            words_by_row[ry].append(w)

        for word in words:
            w_txt = word['text']
            if not w_txt.endswith(':'):
                continue
            if len(w_txt) < 3:
                continue

            wx0 = float(word['x0'])
            wy0 = float(word['top'])
            wx1 = float(word['x1'])
            wy1 = float(word['bottom'])

            # Find bounds: next word on same line or page edge
            row_y = round(wy0 / 3) * 3
            row_words = sorted(words_by_row.get(row_y, []), key=lambda x: float(x['x0']))

            # Skip instruction/paragraph text: if >3 words precede the
            # colon-word on the same row, it's probably mid-sentence text
            # like "...for statistical purposes only:" rather than a
            # standalone form label like "Name:" or "Address:".
            preceding_count = sum(
                1 for w in row_words
                if float(w['x1']) < wx0 - 1 and w is not word
            )
            if preceding_count > 3:
                continue
            next_word = next(
                (w for w in row_words if float(w['x0']) > wx1 + 5),
                None,
            )
            limit_x = float(next_word['x0']) if next_word else page_width - 40

            # Case A: Horizontal underline after label
            segment_lines = [
                l for l in lines
                if abs(float(l.get('y0', l.get('top', 0))) - float(l.get('y1', l.get('bottom', 0)))) < 2
                and abs(float(l.get('top', l.get('y0', 0))) - wy1) < 15
                and float(l.get('x0', 0)) < limit_x
                and float(l.get('x1', 0)) > wx1
            ]

            discovered_f: Optional[Tuple[float, float, float, float, str]] = None

            if segment_lines:
                line = min(segment_lines, key=lambda l: float(l.get('top', l.get('y0', 0))))
                line_x0 = float(line.get('x0', 0))
                line_x1 = float(line.get('x1', 0))
                line_y = float(line.get('top', line.get('y0', 0)))

                f_x0 = max(wx1 + 3, line_x0)
                f_x1 = min(limit_x - 3, line_x1)
                f_y0 = line_y - 13
                f_y1 = line_y

                if (f_x1 - f_x0) > 15:
                    discovered_f = (f_x0, f_y0, f_x1, f_y1, 'discovery_line')
            else:
                # Case B: Significant empty gap (no underline found)
                gap_width = limit_x - wx1
                # Cap gap width: if the gap spans >55% of page width,
                # it's likely just trailing whitespace after a section
                # header, not a fill area.
                if gap_width > page_width * 0.55:
                    continue
                if gap_width > 40:
                    discovered_f = (wx1 + 5, wy0 - 10, limit_x - 5, wy1 + 2, 'discovery_gap')

            if discovered_f:
                f_x0, f_y0, f_x1, f_y1, src = discovered_f

                # Ensure discovered area is truly empty (no text)
                inner_words = page_model.get_words_in_bbox(
                    (f_x0, f_y0, f_x1, f_y1), overlap_threshold=0.1
                )
                if inner_words:
                    continue

                label_txt = w_txt.rstrip(':')

                results.append(FieldCandidate(
                    page=page_num,
                    x0=f_x0,
                    y0=f_y0,
                    x1=f_x1,
                    y1=f_y1,
                    field_type=FieldType.TEXT,
                    source=f'discovery_{src}',
                    name_hint=clean_field_name(label_txt),
                    label=label_txt,
                ))

        return results

    # ------------------------------------------------------------------
    # Enhanced whitespace-gap detection (Cases C & D)
    # ------------------------------------------------------------------

    def _discover_whitespace_fields(
        self, page_model: PageModel,
    ) -> List[FieldCandidate]:
        """
        Case C: Question-mark labels followed by empty gaps.
        Case D: Aligned column gaps — 3+ rows sharing the same label-end X.
        """
        results: List[FieldCandidate] = []
        words = page_model.words
        page_num = page_model.page_num
        page_width = page_model.width

        if not words:
            return results

        # Skip text-only pages — they have no form fields
        if page_model.is_text_only:
            return results

        # Group words by row (same bucketing as existing code)
        words_by_row: Dict[int, List[Dict]] = defaultdict(list)
        for w in words:
            ry = round(float(w['top']) / 3) * 3
            words_by_row[ry].append(w)

        # --- Case C: Question-mark labels ---
        for word in words:
            w_txt = word['text']
            if not w_txt.endswith('?'):
                continue
            if len(w_txt) < 4:
                continue

            wx1 = float(word['x1'])
            wy0 = float(word['top'])
            wy1 = float(word['bottom'])
            row_y = round(wy0 / 3) * 3
            row_words = sorted(
                words_by_row.get(row_y, []),
                key=lambda x: float(x['x0']),
            )

            # Find next word after the question mark
            next_word = next(
                (w for w in row_words if float(w['x0']) > wx1 + 5),
                None,
            )
            limit_x = float(next_word['x0']) if next_word else page_width - 40

            gap_width = limit_x - wx1
            if gap_width > page_width * 0.55:
                continue
            if gap_width < 50:
                continue

            f_x0 = wx1 + 5
            f_y0 = wy0 - 10
            f_x1 = limit_x - 5
            f_y1 = wy1 + 2

            if f_x1 - f_x0 < 30:
                continue

            # Verify emptiness
            inner = page_model.get_words_in_bbox(
                (f_x0, f_y0, f_x1, f_y1), overlap_threshold=0.1)
            if inner:
                continue

            # Build label from question words (walk left up to 6 words)
            wx0 = float(word['x0'])
            label_parts = [w_txt.rstrip('?')]
            for w in reversed(row_words):
                if float(w['x1']) > wx0 - 1:
                    continue
                if w is word:
                    continue
                label_parts.insert(0, w['text'])
                if len(label_parts) >= 6:
                    break

            label_txt = ' '.join(label_parts).strip()

            results.append(FieldCandidate(
                page=page_num,
                x0=f_x0, y0=f_y0, x1=f_x1, y1=f_y1,
                field_type=FieldType.TEXT,
                source='discovery_question_gap',
                name_hint=clean_field_name(label_txt),
                label=label_txt,
            ))

        # --- Case D: Aligned column gaps ---
        # Collect label-end positions where text ends before a >60pt gap
        label_end_groups: Dict[int, List[Dict]] = defaultdict(list)

        for ry, row_words_list in words_by_row.items():
            if not row_words_list:
                continue
            sorted_rw = sorted(row_words_list, key=lambda w: float(w['x0']))

            for i, word in enumerate(sorted_rw):
                wx0 = float(word['x0'])
                wx1 = float(word['x1'])
                wy0 = float(word['top'])
                wy1 = float(word['bottom'])

                # Skip words inside visual grid cells (table data, not labels)
                inside_grid = False
                for cx0, cy0, cx1, cy1 in page_model.visual_grid_cells:
                    if (wx0 >= cx0 - 3 and wy0 >= cy0 - 3
                            and wx1 <= cx1 + 3 and wy1 <= cy1 + 3):
                        inside_grid = True
                        break
                if inside_grid:
                    continue

                # Next word on this row
                next_w = sorted_rw[i + 1] if i + 1 < len(sorted_rw) else None
                limit_x = float(next_w['x0']) if next_w else page_width - 40

                gap = limit_x - wx1
                if gap < 60:
                    continue
                if gap > page_width * 0.55:
                    continue

                # Must not have too many preceding words (>4 = paragraph)
                preceding = sum(
                    1 for w in sorted_rw
                    if float(w['x1']) < wx0 - 1
                )
                if preceding > 4:
                    continue

                # Round x1 to nearest 5pt for column alignment
                rounded_x = round(wx1 / 5) * 5
                label_end_groups[rounded_x].append({
                    'word': word,
                    'row_y': ry,
                    'limit_x': limit_x,
                })

        # Create fields for aligned columns (3+ rows at same label-end X)
        for rounded_x, group in label_end_groups.items():
            if len(group) < 3:
                continue

            for entry in group:
                word = entry['word']
                wx1 = float(word['x1'])
                wy0 = float(word['top'])
                wy1 = float(word['bottom'])
                limit_x = entry['limit_x']

                f_x0 = wx1 + 5
                f_y0 = wy0 - 10
                f_x1 = limit_x - 5
                f_y1 = wy1 + 2

                if f_x1 - f_x0 < 30:
                    continue

                # Skip if the proposed field overlaps a visual grid cell
                field_in_grid = False
                for cx0, cy0, cx1, cy1 in page_model.visual_grid_cells:
                    if (f_x0 < cx1 and f_x1 > cx0
                            and f_y0 < cy1 and f_y1 > cy0):
                        field_in_grid = True
                        break
                if field_in_grid:
                    continue

                # Verify emptiness
                inner = page_model.get_words_in_bbox(
                    (f_x0, f_y0, f_x1, f_y1), overlap_threshold=0.1)
                if inner:
                    continue

                label_txt = word['text'].rstrip(':').rstrip('?').strip()
                if not label_txt:
                    continue

                results.append(FieldCandidate(
                    page=page_num,
                    x0=f_x0, y0=f_y0, x1=f_x1, y1=f_y1,
                    field_type=FieldType.TEXT,
                    source='discovery_aligned_gap',
                    name_hint=clean_field_name(label_txt),
                    label=label_txt,
                    confidence=0.7,
                ))

        return results
