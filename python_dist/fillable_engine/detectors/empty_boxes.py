"""
Empty box detector.

Ported from v23's _detect_empty_boxes().

Detects large empty rectangular boxes (formed by rects/curves on the page)
that should become text areas.  These boxes:
1. Meet minimum dimension thresholds (default 100pt x 15pt).
2. Are mostly empty (little or no text content).
3. Often appear under headers like "Customer's additional needs beyond scope".

Contract (v24 modular):
    * Receives read-only PageModel objects.
    * Returns List[FieldCandidate] -- never mutates shared state.
    * Does NOT check for overlaps with other detectors (the resolver handles that).
"""

from typing import List, Dict, Optional

from ..models import PageModel, FieldCandidate, FieldType
from ..helpers import clean_field_name, detect_field_format, bbox_overlap_area
from .base import BaseDetector


class EmptyBoxDetector(BaseDetector):
    """Detect large empty rectangular boxes that should become text fill areas."""

    def detect(self, pages: List[PageModel]) -> List[FieldCandidate]:
        candidates: List[FieldCandidate] = []

        min_box_width = float(self.settings.get('min_empty_box_width', 100))
        min_box_height = float(self.settings.get('min_empty_box_height', 15))

        for page_model in pages:
            candidates.extend(
                self._detect_rect_boxes(page_model, min_box_width, min_box_height)
            )
            candidates.extend(
                self._detect_line_formed_boxes(page_model, min_box_width, min_box_height)
            )

        return candidates

    # ------------------------------------------------------------------
    # Detect boxes from page rects / curves
    # ------------------------------------------------------------------

    def _detect_rect_boxes(
        self,
        page_model: PageModel,
        min_box_width: float,
        min_box_height: float,
    ) -> List[FieldCandidate]:
        results: List[FieldCandidate] = []
        page_num = page_model.page_num
        page_width = page_model.width
        page_height = page_model.height

        # Merge rects + curves as candidate shapes
        candidate_shapes = list(page_model.rects) + list(page_model.curves)

        # Build merged horizontal lines for interior split detection
        v_lines = self._get_v_lines(page_model)

        for rect in candidate_shapes:
            x0 = float(rect.get('x0', 0))
            y0 = float(rect.get('top', rect.get('y0', 0)))
            x1 = float(rect.get('x1', 0))
            y1 = float(rect.get('bottom', rect.get('y1', 0)))

            # Skip header-region shapes
            if y0 < 90:
                continue

            width = x1 - x0
            height = y1 - y0

            # Skip too small
            if width < 50 or height < 12:
                continue

            # Skip if extends beyond page
            if x0 < 0 or x1 > page_width or y0 < 0 or y1 > page_height:
                continue

            # Skip giant background shapes
            max_box_width = page_width * 0.95
            max_box_height = page_height * 0.80
            if width > max_box_width or height > max_box_height:
                continue

            if width < min_box_width or height < min_box_height:
                continue

            # Skip if inside a header bar
            if page_model.is_in_header_bar(x0, y0, x1, y1):
                continue

            # Check text inside the box
            text_inside_words = self._words_inside_box(page_model, x0, y0, x1, y1)
            total_text_str = ' '.join(w['text'] for w in text_inside_words).strip()

            # Detect internal label in top 60% of box
            # (labels like "Comments/recommendations for GMO approval:"
            #  can appear in the middle of a comment box, not just the top)
            internal_label = None
            field_y0 = y0

            if text_inside_words:
                sorted_words = sorted(text_inside_words, key=lambda w: float(w['top']))
                top_text_words = [
                    w for w in sorted_words
                    if float(w['top']) < y0 + height * 0.6
                ]
                if top_text_words:
                    internal_label = ' '.join(
                        w['text'] for w in top_text_words
                    ).strip().rstrip(':').strip()
                    field_y0 = max(field_y0, max(float(w['bottom']) for w in top_text_words))

            # Decision: is the box mostly empty or has just an internal label?
            is_mostly_empty = len(total_text_str) < 20
            room_below = (y1 - field_y0) if internal_label else (y1 - y0)
            has_room_below = room_below > (height * 0.35)
            is_internal_label = bool(internal_label) and (
                (len(total_text_str) < 150 and
                 (':' in total_text_str or len(internal_label) > len(total_text_str) * 0.7))
                or (has_room_below and len(internal_label) > 0)
            )

            if not (is_mostly_empty or is_internal_label):
                continue

            # Skip dense text boxes (> 50 words)
            if len(text_inside_words) > 50:
                continue

            # Determine label
            if internal_label:
                label_text = internal_label
            else:
                label_text = self._find_label_above(page_model, x0, y0)
                if not label_text:
                    label_text = "Notes"

            # Skip bad labels (section headers, etc.)
            bad_labels = [
                "A.", "B.", "C.", "D.", "A ", "B ", "C ", "D ",
                "Program", "Application", "Date Submitted",
                "Information", "General", "Type", "Rating",
                "Quantity", "Sales", "Projections", "Page", "Totals",
            ]
            lt_strip = label_text.strip()
            if any(lt_strip.startswith(bl) for bl in bad_labels) or lt_strip in ["A", "B", "C", "D"]:
                continue

            # Skip short noise labels
            if len(lt_strip) < 3:
                continue

            # Heuristic: skip full-width section headers or long single-line instructions
            if width > page_width * 0.8 and label_text:
                words_parts = label_text.split()
                is_short_upper = (height <= 24 and label_text == label_text.upper() and len(words_parts) <= 6)
                is_long_banner = (len(words_parts) > 15 and height <= 60)
                if is_short_upper or is_long_banner:
                    continue

            base_name = clean_field_name(label_text)

            # Split box by interior vertical lines if large
            boxes_to_create = [(x0, y0, x1, y1)]
            if width > 100:
                interior_v_lines = []
                for v in v_lines:
                    vx = v['x']
                    if x0 + 10 < vx < x1 - 10:
                        # Check if line spans most of box height
                        vy0 = v['y0']
                        vy1_val = v['y1']
                        ref_y0 = field_y0 if internal_label else y0
                        v_overlap = min(vy1_val, y1) - max(vy0, ref_y0)
                        box_h = y1 - ref_y0
                        if box_h > 0 and v_overlap > box_h * 0.7:
                            interior_v_lines.append(vx)

                if interior_v_lines:
                    interior_v_lines.sort()
                    boxes_to_create = []
                    prev_x = x0
                    for vx in interior_v_lines:
                        boxes_to_create.append((prev_x, y0, vx, y1))
                        prev_x = vx
                    boxes_to_create.append((prev_x, y0, x1, y1))

            padding = self.table_cell_padding
            for box_idx, (bx0, by0, bx1, by1) in enumerate(boxes_to_create):
                if (bx1 - bx0) < 30:
                    continue

                if len(boxes_to_create) > 1:
                    seg_name = f"{base_name}_{box_idx + 1}"
                else:
                    seg_name = base_name

                results.append(FieldCandidate(
                    page=page_num,
                    x0=bx0 + padding,
                    y0=(field_y0 + padding) if internal_label else (by0 + padding),
                    x1=bx1 - padding,
                    y1=by1 - padding,
                    field_type=FieldType.TEXT,
                    source='empty_box',
                    name_hint=seg_name,
                    label=label_text,
                ))

        return results

    # ------------------------------------------------------------------
    # Detect boxes formed by intersecting H and V lines
    # ------------------------------------------------------------------

    def _detect_line_formed_boxes(
        self,
        page_model: PageModel,
        min_box_width: float,
        min_box_height: float,
    ) -> List[FieldCandidate]:
        results: List[FieldCandidate] = []
        page_num = page_model.page_num
        page_width = page_model.width

        h_lines = self._get_merged_h_lines(page_model)
        v_lines = self._get_v_lines(page_model)

        for h1 in h_lines:
            for h2 in h_lines:
                if h2['y'] <= h1['y']:
                    continue

                height = h2['y'] - h1['y']
                if height < min_box_height or height > 400:
                    continue

                x_left = max(h1['x0'], h2['x0'])
                x_right = min(h1['x1'], h2['x1'])
                width = x_right - x_left

                if width < min_box_width:
                    continue

                # Check for vertical line connections on left and right edges
                has_left = any(
                    abs(v['x'] - x_left) < 5
                    and v['y0'] < h1['y'] + 5
                    and v['y1'] > h2['y'] - 5
                    for v in v_lines
                )
                has_right = any(
                    abs(v['x'] - x_right) < 5
                    and v['y0'] < h1['y'] + 5
                    and v['y1'] > h2['y'] - 5
                    for v in v_lines
                )

                if not (has_left and has_right):
                    continue

                box_y0 = h1['y']
                box_y1 = h2['y']

                # Skip header bars
                if page_model.is_in_header_bar(x_left, box_y0, x_right, box_y1):
                    continue

                # Check text content
                text_inside = page_model.get_text_in_bbox((x_left, box_y0, x_right, box_y1), 0.15)
                if len(text_inside) > 20:
                    continue

                # Find label above
                label_text = self._find_label_above(page_model, x_left, box_y0)
                if not label_text:
                    label_text = "Notes"

                if len(label_text.strip()) < 3:
                    continue

                base_name = clean_field_name(label_text)
                padding = self.table_cell_padding

                results.append(FieldCandidate(
                    page=page_num,
                    x0=x_left + padding,
                    y0=box_y0 + padding,
                    x1=x_right - padding,
                    y1=box_y1 - padding,
                    field_type=FieldType.TEXT,
                    source='empty_box_lines',
                    name_hint=base_name,
                    label=label_text,
                ))

        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _words_inside_box(
        page_model: PageModel,
        x0: float, y0: float, x1: float, y1: float,
    ) -> List[Dict]:
        """Find words whose center falls inside the given box."""
        result = []
        for w in page_model.words:
            wx0, wy0 = float(w['x0']), float(w['top'])
            wx1, wy1 = float(w['x1']), float(w['bottom'])
            cx = (wx0 + wx1) / 2
            cy = (wy0 + wy1) / 2
            if x0 < cx < x1 and y0 < cy < y1:
                result.append(w)
        return result

    @staticmethod
    def _find_label_above(page_model: PageModel, x0: float, y0: float) -> Optional[str]:
        """Find text just above the box as a label (within 60pt)."""
        best_label = None
        best_dist = 999.0
        for w in page_model.words:
            wbot = float(w['bottom'])
            wx0 = float(w['x0'])
            wx1 = float(w['x1'])
            if wbot > y0 + 2:
                continue
            if wbot < y0 - 60:
                continue
            # Horizontal overlap
            if wx1 < x0 - 10:
                continue
            dist = y0 - wbot
            if dist < best_dist:
                best_dist = dist
                best_label = w['text']
        return best_label

    @staticmethod
    def _get_merged_h_lines(page_model: PageModel) -> List[Dict]:
        """Get horizontal lines merged when on the same Y and adjacent."""
        raw = []
        for line in page_model.lines:
            lx0, lx1 = float(line.get('x0', 0)), float(line.get('x1', 0))
            ly0 = float(line.get('top', line.get('y0', 0)))
            ly1 = float(line.get('bottom', line.get('y1', 0)))
            if abs(ly0 - ly1) < 1 and abs(lx1 - lx0) > 50:
                raw.append({'y': (ly0 + ly1) / 2, 'x0': min(lx0, lx1), 'x1': max(lx0, lx1)})

        if not raw:
            return []

        raw.sort(key=lambda l: (l['y'], l['x0']))
        merged = []
        curr = raw[0].copy()
        for nxt in raw[1:]:
            if abs(nxt['y'] - curr['y']) < 0.5 and nxt['x0'] <= curr['x1'] + 5:
                curr['x1'] = max(curr['x1'], nxt['x1'])
            else:
                merged.append(curr)
                curr = nxt.copy()
        merged.append(curr)
        return merged

    @staticmethod
    def _get_v_lines(page_model: PageModel) -> List[Dict]:
        """Collect vertical lines from page lines."""
        v_lines = []
        for line in page_model.lines:
            lx0, lx1 = float(line.get('x0', 0)), float(line.get('x1', 0))
            ly0 = float(line.get('top', line.get('y0', 0)))
            ly1 = float(line.get('bottom', line.get('y1', 0)))
            if abs(lx0 - lx1) < 1 and abs(ly1 - ly0) > 10:
                v_lines.append({
                    'x': (lx0 + lx1) / 2,
                    'y0': min(ly0, ly1),
                    'y1': max(ly0, ly1),
                })
        return v_lines
