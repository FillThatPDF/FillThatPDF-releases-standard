"""
Box entry / comb field detector.

Ported from v23's _detect_box_entry_fields().

Detects comb/box entry fields -- rows of small boxes for single-digit entry,
commonly used for account numbers, phone numbers, SSNs, and reference codes.

Detection strategy:
1. Find short vertical lines (10-35pt height) from lines, rects, and edges.
2. Group by Y position (same row).
3. Identify sequences with uniform spacing (8-25pt).
4. Split into separate groups at gaps.
5. Create comb-style FieldCandidate with is_comb=True and comb_count.

Source tag: 'box_entry'

Contract (v24 modular):
    * Receives read-only PageModel objects.
    * Returns List[FieldCandidate] -- never mutates shared state.
    * Does NOT check for overlaps with other detectors (the resolver handles that).
"""

import re
from collections import defaultdict
from typing import List, Dict, Optional

from ..models import PageModel, FieldCandidate, FieldType
from ..helpers import clean_field_name
from .base import BaseDetector


class BoxEntryDetector(BaseDetector):
    """Detect comb/box entry fields formed by sequences of vertical lines."""

    def detect(self, pages: List[PageModel]) -> List[FieldCandidate]:
        candidates: List[FieldCandidate] = []

        for page_model in pages:
            candidates.extend(self._detect_box_entries(page_model))

        return candidates

    # ------------------------------------------------------------------
    # Core detection
    # ------------------------------------------------------------------

    def _detect_box_entries(self, page_model: PageModel) -> List[FieldCandidate]:
        results: List[FieldCandidate] = []
        page_num = page_model.page_num

        # Step 1: Collect short vertical lines from lines, rects, and edges
        v_lines = self._collect_vertical_lines(page_model)

        if len(v_lines) < 4:
            return results

        # Deduplicate vertical lines at the same X position (within 0.1pt)
        v_lines = self._dedup_vertical_lines(v_lines)

        if len(v_lines) < 4:
            return results

        # Step 2: Group vertical lines by Y position
        v_groups: Dict[int, List[Dict]] = defaultdict(list)
        for line in v_lines:
            y_key = round(line['y0'] / 3) * 3
            v_groups[y_key].append(line)

        # Step 3: Process each potential box row
        for y_key, vlines in v_groups.items():
            if len(vlines) < 4:
                continue

            vlines.sort(key=lambda l: l['x'])

            spacings = [
                vlines[i + 1]['x'] - vlines[i]['x']
                for i in range(len(vlines) - 1)
            ]

            # Find the most common spacing in comb range (8-25pt)
            spacing_counts: Dict[int, int] = defaultdict(int)
            for s in spacings:
                rounded = round(s)
                if 8 <= rounded <= 25:
                    spacing_counts[rounded] += 1

            if not spacing_counts:
                continue

            box_spacing = max(spacing_counts.keys(), key=lambda k: spacing_counts[k])

            # Need at least 8 consistent spacings
            if spacing_counts[box_spacing] < 8:
                continue

            # Step 4: Extract contiguous uniformly-spaced line groups
            box_groups = self._extract_uniform_groups(vlines, spacings, box_spacing)

            # Step 5: Create one COMB field per group
            for group in box_groups:
                x_start = group[0]['x']
                x_end = group[-1]['x']
                num_boxes = len(group) - 1

                if num_boxes < 4:
                    continue

                # Use median height to avoid tall borders
                sorted_by_len = sorted(group, key=lambda l: l['length'])
                median_line = sorted_by_len[len(sorted_by_len) // 2]
                box_height = median_line['length']
                y_bottom = median_line['y1']
                y_top = y_bottom - box_height

                # Find label
                label = self._find_box_row_label(page_model, x_start, y_top, x_end)
                base_name = self._make_field_name(label, page_num, y_top)

                results.append(FieldCandidate(
                    page=page_num,
                    x0=x_start,
                    y0=y_top,
                    x1=x_end,
                    y1=y_bottom,
                    field_type=FieldType.COMB,
                    source='box_entry',
                    name_hint=base_name,
                    label=label if label else "Entry boxes",
                    is_comb=True,
                    comb_count=num_boxes,
                    max_length=num_boxes,
                ))

        return results

    # ------------------------------------------------------------------
    # Vertical line collection
    # ------------------------------------------------------------------

    @staticmethod
    def _collect_vertical_lines(page_model: PageModel) -> List[Dict]:
        """Collect short vertical lines from lines, thin rects, and edges."""
        v_lines: List[Dict] = []

        # From regular lines
        source_items = list(page_model.lines)

        # From thin rects (width < 3pt)
        for r in page_model.rects:
            if abs(float(r.get('x0', 0)) - float(r.get('x1', 0))) < 3:
                source_items.append(r)

        # From edges
        for e in page_model.edges:
            if e.get('orientation') == 'v':
                source_items.append(e)

        for item in source_items:
            x0 = float(item.get('x0', 0))
            x1 = float(item.get('x1', 0))
            y0 = float(item.get('top', item.get('y0', 0)))
            y1 = float(item.get('bottom', item.get('y1', 0)))

            # Vertical: nearly zero horizontal extent
            if abs(x0 - x1) < 2:
                length = abs(y1 - y0)
                # Box sides: 10-35pt tall
                if 10 < length < 35:
                    v_lines.append({
                        'x': (x0 + x1) / 2,
                        'y0': min(y0, y1),
                        'y1': max(y0, y1),
                        'length': length,
                    })

        return v_lines

    @staticmethod
    def _dedup_vertical_lines(v_lines: List[Dict]) -> List[Dict]:
        """Deduplicate vertical lines at the same X (within 0.1pt) that overlap in Y."""
        v_lines.sort(key=lambda l: (l['x'], l['y0']))
        dedup = []
        if not v_lines:
            return dedup

        curr = v_lines[0].copy()
        curr_orig_y1 = curr['y1']

        for nex in v_lines[1:]:
            same_x = abs(nex['x'] - curr['x']) < 0.1
            y_adjacent = nex['y0'] < curr_orig_y1 + 2
            y_gap = nex['y0'] - curr_orig_y1
            reasonable_gap = y_gap < 10

            if same_x and y_adjacent and reasonable_gap:
                curr['y1'] = max(curr['y1'], nex['y1'])
                curr['length'] = curr['y1'] - curr['y0']
            else:
                dedup.append(curr)
                curr = nex.copy()
                curr_orig_y1 = curr['y1']

        dedup.append(curr)
        return dedup

    # ------------------------------------------------------------------
    # Uniform spacing extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_uniform_groups(
        vlines: List[Dict],
        spacings: List[float],
        box_spacing: int,
    ) -> List[List[Dict]]:
        """Extract contiguous groups of uniformly-spaced vertical lines."""
        tolerance = max(3, box_spacing * 0.2)
        box_groups: List[List[Dict]] = []

        i = 0
        while i < len(spacings):
            if abs(spacings[i] - box_spacing) <= tolerance:
                current_group = [vlines[i]]
                while i < len(spacings) and abs(spacings[i] - box_spacing) <= tolerance:
                    current_group.append(vlines[i + 1])
                    i += 1

                # Bridge small internal gaps (dashes in account numbers)
                while i < len(spacings) - 1:
                    gap = spacings[i]
                    if gap <= box_spacing * 2.5 and i + 1 < len(spacings):
                        next_spacing = spacings[i + 1]
                        if abs(next_spacing - box_spacing) <= tolerance:
                            current_group.append(vlines[i + 1])
                            i += 1
                            while i < len(spacings) and abs(spacings[i] - box_spacing) <= tolerance:
                                current_group.append(vlines[i + 1])
                                i += 1
                        else:
                            break
                    else:
                        break

                if len(current_group) >= 8:
                    box_groups.append(current_group)
            else:
                i += 1

        return box_groups

    # ------------------------------------------------------------------
    # Label finding
    # ------------------------------------------------------------------

    @staticmethod
    def _find_box_row_label(
        page_model: PageModel,
        x_start: float,
        y_top: float,
        x_end: float,
    ) -> Optional[str]:
        """Find the label text above or to the left of a box row."""
        candidates: List[Dict] = []

        for word in page_model.words:
            wx0 = float(word['x0'])
            wx1 = float(word['x1'])
            wy = float(word['top'])
            wbot = float(word['bottom'])

            # Label should be above boxes (within 60pt)
            if wbot > y_top + 5:
                continue
            if wy < y_top - 60:
                continue

            # Horizontally aligned or to the left
            if wx0 > x_end + 20:
                continue

            text = word['text'].strip()
            if len(text) < 2:
                continue

            # Score keywords
            text_lower = text.lower()
            score = 0.0
            if 'account' in text_lower or 'number' in text_lower:
                score += 10
            if 'utility' in text_lower or 'gas' in text_lower or 'electric' in text_lower:
                score += 5
            if 'phone' in text_lower or 'ssn' in text_lower or 'ein' in text_lower:
                score += 10

            y_dist = abs(wy - y_top)
            x_dist = max(0, x_start - wx1)
            proximity_score = 100 - y_dist - (x_dist * 0.5)

            candidates.append({
                'text': text,
                'score': score + proximity_score,
                'x0': wx0,
                'y': wy,
            })

        if not candidates:
            return None

        candidates.sort(key=lambda c: c['score'], reverse=True)
        best = candidates[0]
        label_parts = [best['text']]

        for c in candidates[1:6]:
            if abs(c['y'] - best['y']) < 5:
                if c['x0'] < best['x0']:
                    label_parts.insert(0, c['text'])
                else:
                    label_parts.append(c['text'])

        return ' '.join(label_parts)

    @staticmethod
    def _make_field_name(label: Optional[str], page_num: int, y_top: float) -> str:
        """Generate a field name from label or fallback coordinates."""
        if label:
            clean_label = re.sub(r'[^\w\s]', '', label)
            clean_label = re.sub(r'\s+', '_', clean_label.strip())
            return clean_label[:40]
        return f"Box_P{page_num + 1}_Y{int(y_top)}"
