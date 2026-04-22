"""
Horizontal Line Table detector — the most critical detector for DTE-style forms.

Ported from v23's _detect_horizontal_line_tables() (lines 7363-8166).

Detects fields in tables that use horizontal divider lines:
- Tables have horizontal lines separating rows
- Vertical lines may or may not define columns
- Labels are inline with colons (e.g., "Account Name:")
- Fields span from label end to next vertical line, next label, or line right edge

Sources emitted:
    - 'horizontal_line_table'           (standard inline-label fields)
    - 'horizontal_line_table_utility'   (checkbox-list-label adjacent fields)
    - 'horizontal_line_table_subrow'    (fields in dense multi-line rows)
"""

from typing import List, Dict, Optional, Tuple

from ..models import PageModel, FieldCandidate, FieldType
from ..helpers import detect_field_format, clean_field_name
from .base import BaseDetector


class HorizontalLineTableDetector(BaseDetector):
    """
    Detect fields defined by horizontal divider lines with inline colon labels.

    Algorithm overview:
        1. Find horizontal lines spanning >55% page width (plus shorter utility/signature lines)
        2. Group by Y, merge segments, validate shorter line groups
        3. Add header bar boundaries as row dividers
        4. Between each pair of lines define a row; find labels + vertical separators
        5. For dense rows (>15 words), split into sub-rows and process each
        6. For normal rows, find colon labels, create fields to their right
        7. Gap-fill pass for empty column cells defined by vertical lines
    """

    def detect(self, pages: List[PageModel]) -> List[FieldCandidate]:
        candidates: List[FieldCandidate] = []

        for page_model in pages:
            if page_model.is_text_only:
                continue
            page_candidates = self._detect_page(page_model)
            candidates.extend(page_candidates)

            # NEW: detect data fields in tables whose column structure is defined
            # by tiling colored header rectangles (no explicit vertical lines).
            # Only adds candidates for cells not already covered by _detect_page.
            tiling_candidates = self._detect_tiling_rect_table_rows(page_model)
            for c in tiling_candidates:
                already_covered = any(
                    e.page == c.page
                    and abs(e.x0 - c.x0) < 8
                    and abs(e.y0 - c.y0) < 8
                    for e in page_candidates
                )
                if not already_covered:
                    candidates.append(c)

        return candidates

    # ------------------------------------------------------------------
    # Private implementation
    # ------------------------------------------------------------------

    def _detect_page(self, pm: PageModel) -> List[FieldCandidate]:
        """Run the full horizontal-line-table algorithm on one page."""
        results: List[FieldCandidate] = []

        page_width = pm.width
        page_height = pm.height
        page_num = pm.page_num

        # ============================================================
        # STEP 1: Find horizontal lines using raw pdfplumber lines
        # ============================================================
        all_lines = pm.lines

        # Filter horizontal lines (top and bottom Y are very close)
        h_lines_raw = [l for l in all_lines if abs(l.get('top', l.get('y0', 0)) - l.get('bottom', l.get('y1', 0))) < 2]

        # Filter vertical lines (left and right X are very close)
        v_lines_raw = [l for l in all_lines if abs(l.get('x0', 0) - l.get('x1', 0)) < 2]

        # --- Find header bar boundaries from page.rects ---
        # Wide (>50% page), thin (10-25pt), colored rectangles act as row dividers.
        header_bar_boundaries: List[Dict] = []
        for r in pm.rects:
            rx0 = r.get('x0', 0)
            rx1 = r.get('x1', 0)
            rtop = r.get('top', r.get('y0', 0))
            rbot = r.get('bottom', r.get('y1', 0))
            w = rx1 - rx0
            h = rbot - rtop
            if w > page_width * 0.5 and 10 < h < 25:
                fill = r.get('non_stroking_color')
                if fill and (isinstance(fill, tuple) and len(fill) >= 3):
                    # Top edge = row above ends, Bottom edge = row below starts
                    header_bar_boundaries.append({
                        'y': rtop,
                        'x0': rx0,
                        'x1': rx1,
                        'width': w,
                        'is_header_bar': True,
                    })
                    header_bar_boundaries.append({
                        'y': rbot,
                        'x0': rx0,
                        'x1': rx1,
                        'width': w,
                        'is_header_bar': True,
                    })

        if len(h_lines_raw) < 2:
            return results

        # --- Group horizontal lines by Y (3pt tolerance), merge segments ---
        y_groups: Dict[float, List[Dict]] = {}
        for line in h_lines_raw:
            y = line.get('top', line.get('y0', 0))
            x0 = min(line.get('x0', 0), line.get('x1', 0))
            x1 = max(line.get('x0', 0), line.get('x1', 0))

            found_group = None
            for y_key in y_groups:
                if abs(y - y_key) < 3:
                    found_group = y_key
                    break

            if found_group is not None:
                y_groups[found_group].append({'x0': x0, 'x1': x1})
            else:
                y_groups[y] = [{'x0': x0, 'x1': x1}]

        # --- For each Y, compute total coverage and decide whether to keep ---
        line_groups: List[Dict] = []
        words = pm.words  # pre-extracted words from PageModel

        for y, segments in y_groups.items():
            min_x = min(seg['x0'] for seg in segments)
            max_x = max(seg['x1'] for seg in segments)
            total_width = sum(seg['x1'] - seg['x0'] for seg in segments)

            is_checkbox_list_underline = False
            is_signature_underline = False

            frac = total_width / page_width if page_width > 0 else 0

            # Short lines (25-60%) — check for checkbox-list or signature labels
            if 0.25 < frac < 0.6:
                for word in words:
                    word_y = float(word.get('top', word.get('y0', 0)))
                    if abs(word_y - y) < 15:
                        word_text = word.get('text', '').replace(':', '').strip()
                        if self._is_checkbox_list_label(pm, word_text,
                                float(word.get('x0', 0)), float(word.get('top', word.get('y0', 0))),
                                float(word.get('x1', 0)), float(word.get('bottom', word.get('y1', 0)))):
                            is_checkbox_list_underline = True
                            break
                        # Signature/form row detection for lines >50% page width
                        if frac > 0.5:
                            if word_text.lower() in ['signature', 'name', 'technician',
                                                     'print', 'date', 'authorized']:
                                is_signature_underline = True
                                break

            # Keep if >55% OR utility/signature underline
            if total_width > page_width * 0.55 or is_checkbox_list_underline or is_signature_underline:
                line_groups.append({
                    'y': y,
                    'x0': min_x,
                    'x1': max_x,
                    'width': total_width,
                    'is_header_bar': False,
                })
            else:
                # Shorter lines (30-55%) — need group validation later
                if total_width > page_width * 0.30:
                    line_groups.append({
                        'y': y,
                        'x0': min_x,
                        'x1': max_x,
                        'width': total_width,
                        'is_header_bar': False,
                        'needs_group_validation': True,
                    })

        # --- Validate shorter lines: keep only if 2+ qualifying neighbours ---
        if line_groups:
            validated: List[Dict] = []
            for lg in line_groups:
                if not lg.get('needs_group_validation'):
                    validated.append(lg)
                else:
                    neighbors = [
                        g for g in line_groups
                        if not g.get('needs_group_validation')
                        and not g.get('is_header_bar')
                        and abs(g['y'] - lg['y']) < 50
                        and abs(g['x1'] - lg['x1']) < 30
                    ]
                    if len(neighbors) >= 2:
                        lg.pop('needs_group_validation', None)
                        validated.append(lg)
            line_groups = validated

        # Add header bar boundaries — skip any whose y is already covered by
        # an existing h_line in line_groups (within 3pt tolerance).  When a
        # colored header-bar rect's edges are already captured as vector lines
        # the duplicate entry creates a zero-height phantom row that breaks the
        # prev_row_valid guard and causes the label from the header bar to bleed
        # incorrectly into the following content row.
        for hb in header_bar_boundaries:
            if not any(abs(hb['y'] - lg['y']) < 3 for lg in line_groups):
                line_groups.append(hb)

        if len(line_groups) < 2:
            return results

        # Sort by Y
        line_groups.sort(key=lambda l: l['y'])

        # ============================================================
        # STEP 2 + 3 + 4 + 5: Process rows between consecutive lines
        # ============================================================
        v_line_x_positions_global = sorted(set(
            round(min(v.get('x0', 0), v.get('x1', 0)))
            for v in v_lines_raw
        ))

        for i in range(len(line_groups) - 1):
            line_top = line_groups[i]
            line_bottom = line_groups[i + 1]

            row_y1 = line_top['y']
            row_y2 = line_bottom['y']
            row_height = row_y2 - row_y1

            # Skip very thin (< 12pt) or very tall (> 80pt)
            if row_height < 12 or row_height > 80:
                continue

            # Find vertical lines that genuinely span into this row.
            # A v-line must overlap the row interior by at least
            # min(4pt, 25% of row height) to count as a column
            # boundary.  This prevents v-lines from an adjacent row
            # (that merely touch the shared border) from creating
            # spurious column splits in merged-cell rows like
            # "Account Name:" which spans the full table width.
            min_v_overlap = min(4, row_height * 0.25)
            row_v_lines = []
            for v in v_lines_raw:
                vtop = float(v.get('top', v.get('y0', 0)))
                vbot = float(v.get('bottom', v.get('y1', 0)))
                overlap = min(vbot, row_y2) - max(vtop, row_y1)
                if overlap >= min_v_overlap:
                    row_v_lines.append(v)
            v_line_x_positions = sorted(set(
                round(min(v.get('x0', 0), v.get('x1', 0)))
                for v in row_v_lines
            ))

            # Find text in this row
            row_words = [
                w for w in words
                if row_y1 < float(w.get('top', w.get('y0', 0))) < row_y2
            ]

            # --- Look for labels ABOVE the row (within 15pt) ---
            label_words_above: List[Dict] = []
            prev_line_y = line_groups[i - 1]['y'] if i > 0 else None
            prev_row_height = (row_y1 - prev_line_y) if prev_line_y is not None else 999
            prev_row_valid = prev_line_y is not None and 12 <= prev_row_height <= 80

            for w in words:
                w_top = float(w.get('top', w.get('y0', 0)))
                if row_y1 - 15 < w_top <= row_y1:
                    if prev_row_valid and w_top > prev_line_y + 2:
                        continue
                    w_x0 = float(w.get('x0', 0))
                    w_x1 = float(w.get('x1', 0))
                    line_x0 = line_top.get('x0', 0)
                    line_x1 = line_top.get('x1', 1000)
                    if w_x0 >= line_x0 - 100 and w_x1 <= line_x1 + 100:
                        label_words_above.append(w)

            if not row_words and not label_words_above:
                continue

            # Only use above-labels if the row has NO colon labels of its own
            has_own_colon_labels = any(
                w.get('text', '').strip().endswith(':') for w in row_words
            )
            if label_words_above and not has_own_colon_labels:
                row_words = label_words_above + row_words

            # ============================================================
            # STEP 3: Dense rows (>15 words) — split into sub-rows
            # ============================================================
            if len(row_words) > 15:
                if row_height > 25:
                    sub_y_groups: Dict[float, List[Dict]] = {}
                    for w in row_words:
                        wy = round(float(w.get('top', w.get('y0', 0))) / 3) * 3
                        sub_y_groups.setdefault(wy, []).append(w)

                    if len(sub_y_groups) >= 2:
                        self._process_subrows(
                            pm, results, sub_y_groups, all_lines,
                            line_top, line_bottom, row_y1, row_y2,
                            i, page_width,
                        )
                        continue  # Skip normal processing
                continue  # >15 words but not multi-line => skip

            # ============================================================
            # STEP 4: Find labels in normal rows
            # ============================================================
            labels = self._find_labels(pm, row_words)

            # When labels come from both above and in-row, prefer in-row labels
            if len(labels) > 1 and label_words_above:
                labels_in_row = [l for l in labels if l['y0'] > row_y1 + 2]
                labels_from_above = [l for l in labels if l['y0'] <= row_y1 + 2]
                if labels_in_row and labels_from_above:
                    labels = labels_in_row

            # ============================================================
            # STEP 5: Create fields to the right of each label
            # ============================================================
            sorted_words = sorted(row_words, key=lambda w: w.get('x0', 0))
            for li_idx, label in enumerate(labels):
                fc = self._create_field_for_label(
                    pm, label, li_idx, labels,
                    v_line_x_positions, line_top, line_bottom,
                    row_y1, row_y2, row_height,
                    sorted_words, i, page_width,
                )
                if fc is not None:
                    results.append(fc)

            # ============================================================
            # STEP 6: Gap-fill pass for empty column cells
            # ============================================================
            if len(v_line_x_positions) >= 2:
                gap_fields = self._gap_fill(
                    pm, results, v_line_x_positions,
                    line_top, line_bottom,
                    row_y1, row_y2, row_words, i, page_width,
                )
                results.extend(gap_fields)

        return results

    # ------------------------------------------------------------------
    # Sub-row processing (STEP 3)
    # ------------------------------------------------------------------

    def _process_subrows(
        self,
        pm: PageModel,
        results: List[FieldCandidate],
        sub_y_groups: Dict[float, List[Dict]],
        all_lines: List[Dict],
        line_top: Dict,
        line_bottom: Dict,
        row_y1: float,
        row_y2: float,
        row_index: int,
        page_width: float,
    ) -> None:
        """Process dense rows by splitting into sub-rows and finding labels in each."""

        skip_kws = ['documentation', 'submit', 'mail to', 'send to',
                     'fax to', 'attn', 'attention']

        for _sub_y, sub_words in sorted(sub_y_groups.items()):
            sub_labels: List[Dict] = []
            sub_sorted = sorted(sub_words, key=lambda w: w.get('x0', 0))
            si = 0
            while si < len(sub_sorted):
                sw = sub_sorted[si]
                st = sw.get('text', '').strip()

                if st.endswith(':'):
                    # Forward-merge multi-word label
                    slabel_words = [sw]
                    sj = si + 1
                    while sj < len(sub_sorted):
                        nsw = sub_sorted[sj]
                        label_so_far = ' '.join(w.get('text', '') for w in slabel_words)
                        if (not label_so_far.endswith(':')
                                and (float(nsw.get('x0', 0)) - float(slabel_words[-1].get('x1', 0)) < 10)):
                            slabel_words.append(nsw)
                            sj += 1
                            if nsw.get('text', '').strip().endswith(':'):
                                break
                        else:
                            break
                    full_t = ' '.join(w.get('text', '') for w in slabel_words)
                    sub_labels.append({
                        'text': full_t,
                        'x0': float(slabel_words[0].get('x0', 0)),
                        'x1': float(slabel_words[-1].get('x1', 0)),
                        'y0': min(float(w.get('top', w.get('y0', 0))) for w in slabel_words),
                        'y1': max(float(w.get('bottom', w.get('y1', 0))) for w in slabel_words),
                    })
                    si = sj
                else:
                    # Check for forward-merge to a colon word
                    # Skip underscore-only words
                    if '____' in st:
                        si += 1
                        continue
                    merged = False
                    if si + 1 < len(sub_sorted):
                        look_words = [sw]
                        lk = si + 1
                        while lk < len(sub_sorted) and lk - si < 12:
                            nw = sub_sorted[lk]
                            if '____' in nw.get('text', ''):
                                break
                            if float(nw.get('x0', 0)) - float(look_words[-1].get('x1', 0)) > 10:
                                break
                            look_words.append(nw)
                            if nw.get('text', '').strip().endswith(':'):
                                full_t = ' '.join(w.get('text', '') for w in look_words)
                                sub_labels.append({
                                    'text': full_t,
                                    'x0': float(look_words[0].get('x0', 0)),
                                    'x1': float(look_words[-1].get('x1', 0)),
                                    'y0': min(float(w.get('top', w.get('y0', 0))) for w in look_words),
                                    'y1': max(float(w.get('bottom', w.get('y1', 0))) for w in look_words),
                                })
                                si = lk + 1
                                merged = True
                                break
                            lk += 1
                    if not merged:
                        si += 1

            # Create fields for each sub-label
            for li, label in enumerate(sub_labels):
                label_text = label['text'].strip().rstrip(':')

                if any(kw in label_text.lower() for kw in skip_kws):
                    continue

                padding = max(3, self.table_cell_padding)
                field_x0 = label['x1'] + padding

                # Determine field right boundary
                if li + 1 < len(sub_labels):
                    field_x1 = sub_labels[li + 1]['x0'] - padding
                else:
                    # Look for an inline underline near this label
                    label_y_bot = label['y1']
                    label_x_end = label['x1']
                    best_underline_x1: Optional[float] = None

                    for al in all_lines:
                        al_x0 = float(al.get('x0', 0))
                        al_x1 = float(al.get('x1', 0))
                        al_y = float(al.get('top', al.get('y0', 0)))
                        al_y1 = float(al.get('bottom', al.get('y1', 0)))
                        al_w = abs(al_x1 - al_x0)
                        al_h = abs(al_y1 - al_y)
                        if al_w < 15 or al_h > 3:
                            continue
                        if abs(al_y - label_y_bot) > 5:
                            continue
                        if al_x0 < label_x_end - 5:
                            continue
                        if al_w > page_width * 0.4:
                            continue
                        if best_underline_x1 is None or al_x0 < best_underline_x1:
                            best_underline_x1 = al_x1

                    if best_underline_x1 is not None:
                        field_x1 = best_underline_x1
                    else:
                        sub_line_x1 = min(
                            line_top.get('x1', 9999),
                            line_bottom.get('x1', 9999),
                        )
                        field_x1 = sub_line_x1 - self.table_cell_padding

                # Use sub-row Y range
                field_y0 = label['y0'] - 1
                field_y1 = label['y1'] + 1
                field_width = field_x1 - field_x0
                field_height = field_y1 - field_y0

                if field_width < 20 or field_height < 6:
                    continue

                name_hint = clean_field_name(label_text)
                fmt, fmt_opts = detect_field_format(None, label_text)

                fc = FieldCandidate(
                    page=pm.page_num,
                    x0=field_x0,
                    y0=field_y0,
                    x1=field_x1,
                    y1=field_y1,
                    field_type=FieldType.TEXT,
                    source='horizontal_line_table_subrow',
                    confidence=1.0,
                    name_hint=name_hint,
                    label=label_text,
                    format_hint=fmt,
                    format_options=fmt_opts if fmt_opts else None,
                    table_index=row_index,
                )
                results.append(fc)

    # ------------------------------------------------------------------
    # Label detection (STEP 4)
    # ------------------------------------------------------------------

    def _find_labels(self, pm: PageModel, row_words: List[Dict]) -> List[Dict]:
        """Find colon-ending labels and checkbox-list labels in a row's words."""

        labels: List[Dict] = []
        sorted_words = sorted(row_words, key=lambda w: w.get('x0', 0))

        idx = 0
        while idx < len(sorted_words):
            word = sorted_words[idx]
            text = word.get('text', '').strip()

            # Generic checkbox-list-label detection
            is_utility_label = self._is_checkbox_list_label(
                pm, text,
                float(word.get('x0', 0)),
                float(word.get('top', word.get('y0', 0))),
                float(word.get('x1', 0)),
                float(word.get('bottom', word.get('y1', 0))),
            )

            # Check for label prefix (e.g., "Technician" before "Signature:")
            is_label_prefix = False
            clean_txt = text.replace(':', '').strip()
            prefix_words = {
                'Technician', 'Print', 'Authorized', 'Contractor',
                'Customer', 'Inspector', 'Homeowner', 'Owner',
                'Property', 'Account', 'Program', 'Project', 'Building',
            }
            if clean_txt in prefix_words:
                if idx + 1 < len(sorted_words):
                    next_word = sorted_words[idx + 1]
                    if next_word.get('text', '').strip().endswith(':'):
                        is_label_prefix = True

            if text.endswith(':') or is_utility_label or is_label_prefix:
                label_words = [word]

                # Backward merge for multi-word labels ending with ':'
                if text.endswith(':') and not is_label_prefix and idx > 0:
                    k = idx - 1
                    while k >= 0:
                        prev_w = sorted_words[k]
                        prev_txt = prev_w.get('text', '').strip()
                        gap = float(label_words[0].get('x0', 0)) - float(prev_w.get('x1', 0))
                        if gap > 10 or gap < 0 or prev_txt.endswith(':'):
                            break
                        if len(prev_txt) < 2 or not any(c.isalpha() for c in prev_txt):
                            break
                        # Stop if already part of a previous label
                        if any(abs(float(prev_w.get('x0', 0)) - l['x0']) < 2 for l in labels):
                            break
                        label_words.insert(0, prev_w)
                        k -= 1

                # Forward merge
                j = idx + 1
                while j < len(sorted_words):
                    next_w = sorted_words[j]
                    next_txt = next_w.get('text', '').strip()
                    label_so_far = ' '.join(w.get('text', '') for w in label_words)
                    if (not label_so_far.endswith(':')
                            and (float(next_w.get('x0', 0)) - float(label_words[-1].get('x1', 0)) < 10)):
                        label_words.append(next_w)
                        j += 1
                        if next_txt.endswith(':'):
                            break
                    else:
                        break

                full_txt = ' '.join(w.get('text', '') for w in label_words)
                labels.append({
                    'text': full_txt,
                    'x0': float(label_words[0].get('x0', 0)),
                    'x1': float(label_words[-1].get('x1', 0)),
                    'y0': min(float(w.get('top', w.get('y0', 0))) for w in label_words),
                    'y1': max(float(w.get('bottom', w.get('y1', 0))) for w in label_words),
                })
                idx = j
            else:
                idx += 1

        # Sort labels left to right
        if labels:
            labels.sort(key=lambda l: l['x0'])

        return labels

    # ------------------------------------------------------------------
    # Field creation for a single label (STEP 5)
    # ------------------------------------------------------------------

    def _create_field_for_label(
        self,
        pm: PageModel,
        label: Dict,
        li_idx: int,
        labels: List[Dict],
        v_line_x_positions: List[int],
        line_top: Dict,
        line_bottom: Dict,
        row_y1: float,
        row_y2: float,
        row_height: float,
        sorted_words: List[Dict],
        row_index: int,
        page_width: float,
    ) -> Optional[FieldCandidate]:
        """Create a FieldCandidate to the right of a label, or None if invalid."""

        label_text = label['text'].strip().rstrip(':')

        # Classify source type
        if self._is_checkbox_list_label(
            pm, label_text,
            label['x0'], label.get('top', label['y0']),
            label['x1'], label.get('bottom', label['y1']),
        ):
            source_type = 'horizontal_line_table_utility'
        else:
            source_type = 'horizontal_line_table'

        padding = max(3, self.table_cell_padding)

        # Field starts after label
        field_x0 = label['x1'] + padding

        # Find next vertical line after label end
        next_v_lines = [x for x in v_line_x_positions if x > label['x1'] + 10]

        # Next label by sorted index
        next_labels = labels[li_idx + 1:] if li_idx + 1 < len(labels) else []

        # Actual underline endpoint (min of top/bottom line x1)
        actual_line_x1 = min(line_top.get('x1', 9999), line_bottom.get('x1', 9999))

        # Determine field right edge
        if next_v_lines and (not next_labels or next_v_lines[0] < next_labels[0]['x0']):
            field_x1 = min(next_v_lines[0], actual_line_x1) - self.table_cell_padding
        elif next_labels:
            field_x1 = next_labels[0]['x0'] - self.table_cell_padding
        else:
            field_x1 = actual_line_x1 - self.table_cell_padding

        # --- Clip to inline underscore text boundaries ---
        _uw_in_field = [
            sw for sw in sorted_words
            if '___' in sw.get('text', '')
            and float(sw.get('x0', 0)) >= field_x0 - 5
            and float(sw.get('x0', 0)) < field_x1
        ]
        if _uw_in_field:
            _uw = _uw_in_field[0]
            _uw_text = _uw.get('text', '')
            _uw_x0v = float(_uw.get('x0', 0))
            _uw_x1v = float(_uw.get('x1', 0))
            _suffix = _uw_text.lstrip('_')
            if _suffix and len(_suffix) < len(_uw_text):
                # Has text suffix (e.g., "________________IWC")
                _avg_cw = (_uw_x1v - _uw_x0v) / max(len(_uw_text), 1)
                _suffix_w = len(_suffix) * _avg_cw
                _underscore_end = _uw_x1v - _suffix_w - 1
                field_x1 = min(field_x1, _underscore_end)
            elif not _suffix:
                # Pure underscore word
                field_x1 = min(field_x1, _uw_x1v)

        # --- Anchor to BOTTOM underline for standard height ---
        label_y_center = (label['y0'] + label['y1']) / 2
        row_y_center = (row_y1 + row_y2) / 2
        standard_field_h = 14.0

        if row_height > 20 and abs(label_y_center - row_y_center) > 5:
            if label_y_center < row_y_center:
                # Label in upper half (from label_words_above)
                field_y1 = row_y2 - self.table_cell_padding
                field_y0 = max(row_y1, field_y1 - standard_field_h)
            else:
                # Label in lower half
                field_y0 = max(row_y_center, label['y0'] - 5)
                field_y1 = row_y2 - self.table_cell_padding
        elif row_height > standard_field_h + 4:
            # Row taller than standard — anchor to bottom underline
            field_y1 = row_y2 - self.table_cell_padding
            field_y0 = field_y1 - standard_field_h
        else:
            field_y0 = row_y1 + self.table_cell_padding
            field_y1 = row_y2 - self.table_cell_padding

        # Ensure minimum 0.5pt gap from row boundaries
        field_y0 = max(field_y0, row_y1 + 0.5)
        field_y1 = min(field_y1, row_y2 - 0.5)

        # --- Validate field dimensions ---
        field_width = field_x1 - field_x0
        field_height = field_y1 - field_y0

        if field_width < 20:
            raw_right = (line_top.get('x1', 9999) if not next_v_lines else next_v_lines[0])
            if next_labels:
                raw_right = min(raw_right, next_labels[0]['x0'])
            raw_w = raw_right - label['x1'] - 6
            if raw_w >= 20:
                field_x1 = field_x0 + raw_w
                field_width = raw_w
            else:
                return None

        if field_height < 8:
            raw_h = row_y2 - row_y1
            if raw_h >= 8:
                field_y0 = row_y1 + 0.5
                field_y1 = row_y2 - 0.5
                field_height = field_y1 - field_y0
            else:
                return None

        # Build the candidate
        name_hint = clean_field_name(label_text)
        fmt, fmt_opts = detect_field_format(None, label_text)

        return FieldCandidate(
            page=pm.page_num,
            x0=field_x0,
            y0=field_y0,
            x1=field_x1,
            y1=field_y1,
            field_type=FieldType.TEXT,
            source=source_type,
            confidence=1.0,
            name_hint=name_hint,
            label=label_text,
            format_hint=fmt,
            format_options=fmt_opts if fmt_opts else None,
            table_index=row_index,
        )

    # ------------------------------------------------------------------
    # Gap-fill pass (STEP 6)
    # ------------------------------------------------------------------

    def _gap_fill(
        self,
        pm: PageModel,
        existing_results: List[FieldCandidate],
        v_line_x_positions: List[int],
        line_top: Dict,
        line_bottom: Dict,
        row_y1: float,
        row_y2: float,
        row_words: List[Dict],
        row_index: int,
        page_width: float,
    ) -> List[FieldCandidate]:
        """Fill empty column cells defined by vertical lines."""

        gap_fields: List[FieldCandidate] = []

        row_left = line_top.get('x0', 0)
        row_right = line_top.get('x1', page_width)
        col_edges = sorted(set([row_left] + list(v_line_x_positions) + [row_right]))

        padding = max(2, self.table_cell_padding)
        fh = float(self.settings.get('field_height', 14))

        for ci in range(len(col_edges) - 1):
            cx0 = col_edges[ci] + padding
            cx1 = col_edges[ci + 1] - padding
            cw = cx1 - cx0
            if cw < 20:
                continue

            # Check if any existing result already covers this cell
            cell_has_field = False
            for ef in existing_results:
                if ef.page != pm.page_num:
                    continue
                emx = (ef.x0 + ef.x1) / 2
                emy = (ef.y0 + ef.y1) / 2
                if cx0 - 5 <= emx <= cx1 + 5 and row_y1 - 2 <= emy <= row_y2 + 2:
                    cell_has_field = True
                    break
            if cell_has_field:
                continue

            # Check text content — skip cells with non-label text
            cell_words = [
                w for w in row_words
                if float(w.get('x0', 0)) >= col_edges[ci] - 2
                and float(w.get('x1', 0)) <= col_edges[ci + 1] + 2
            ]
            cell_text = ' '.join(w.get('text', '') for w in cell_words).strip()
            if cell_text:
                if cell_text.rstrip().endswith(':'):
                    # Check if the label fills most of the cell — if so,
                    # this is a pure label column (entry area is to the
                    # right), NOT a label-over-fill cell.
                    if cell_words:
                        tw_x0 = min(float(w.get('x0', 0)) for w in cell_words)
                        tw_x1 = max(float(w.get('x1', 0)) for w in cell_words)
                        text_span = tw_x1 - tw_x0
                        cell_span = max(1.0, cx1 - cx0)
                        if text_span / cell_span > 0.65:
                            continue  # Label fills the cell — skip
                else:
                    continue  # Non-label text — skip

            # Create gap-fill field
            fy1 = row_y2 - padding
            fy0 = fy1 - fh
            if fy0 < row_y1 + 1:
                fy0 = row_y1 + 1
            fy0 = max(fy0, row_y1 + 0.5)
            fy1 = min(fy1, row_y2 - 0.5)

            # Generate name hint
            if cell_text:
                gap_name = clean_field_name(cell_text.rstrip(':'))
            else:
                gap_name = f"Field_{int(cx0)}_{int(fy0)}"

            gap_format, gap_opts = detect_field_format(None, cell_text)

            gap_fields.append(FieldCandidate(
                page=pm.page_num,
                x0=cx0,
                y0=fy0,
                x1=cx1,
                y1=fy1,
                field_type=FieldType.TEXT,
                source='horizontal_line_table',
                confidence=0.8,
                name_hint=gap_name,
                label=cell_text.rstrip(':') if cell_text else '',
                format_hint=gap_format,
                format_options=gap_opts if gap_opts else None,
                table_index=row_index,
            ))

        return gap_fields

    # ------------------------------------------------------------------
    # Checkbox-list-label heuristic (simplified from v23)
    # ------------------------------------------------------------------

    def _is_checkbox_list_label(
        self,
        pm: PageModel,
        text: str,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
    ) -> bool:
        """
        Detect whether a text item is part of a horizontal list of checkbox labels.

        These are short text items (1-3 words, <30 chars) near a checkbox-like rect,
        with 2+ other short text items at a similar Y position. They should NOT have
        text fields created on them — they are option labels, not fill-in labels.
        """
        clean = text.strip()
        word_count = len(clean.split())
        if word_count > 3 or len(clean) > 30:
            return False
        if ':' in clean:
            return False

        y_mid = (y0 + y1) / 2

        # Check 1: Is there a checkbox-like rect nearby?
        # Look at page rects for small squares (checkbox indicators)
        has_nearby_cb = False
        for r in pm.rects:
            rw = abs(r.get('x1', 0) - r.get('x0', 0))
            rh = abs(r.get('bottom', r.get('y1', 0)) - r.get('top', r.get('y0', 0)))
            # Checkbox-like: roughly square, small (6-20pt)
            if not (5 < rw < 22 and 5 < rh < 22):
                continue
            if abs(rw - rh) > 5:
                continue
            rx_mid = (r.get('x0', 0) + r.get('x1', 0)) / 2
            ry_mid = (r.get('top', r.get('y0', 0)) + r.get('bottom', r.get('y1', 0))) / 2
            if abs(ry_mid - y_mid) < 20 and abs(rx_mid - (x0 + x1) / 2) < 100:
                has_nearby_cb = True
                break

        # Also check existing_checkboxes on the PageModel
        if not has_nearby_cb:
            for cb in pm.existing_checkboxes:
                cb_y = (cb.get('y0', 0) + cb.get('y1', 0)) / 2
                cb_x = (cb.get('x0', 0) + cb.get('x1', 0)) / 2
                if abs(cb_y - y_mid) < 20 and abs(cb_x - (x0 + x1) / 2) < 100:
                    has_nearby_cb = True
                    break

        if not has_nearby_cb:
            return False

        # Check 2: Are there 2+ other short text items at a similar Y?
        similar_y_count = 0
        for w in pm.words:
            wy = (float(w.get('top', w.get('y0', 0))) + float(w.get('bottom', w.get('y1', 0)))) / 2
            if abs(wy - y_mid) > 8:
                continue
            wt = w.get('text', '').strip()
            if wt == clean:
                continue  # Skip self
            if len(wt.split()) <= 3 and len(wt) < 30 and ':' not in wt:
                similar_y_count += 1
                if similar_y_count >= 2:
                    return True

        return False

    # ------------------------------------------------------------------
    # Tiling colored-rect table detection (add-on pass)
    # ------------------------------------------------------------------

    def _detect_tiling_rect_table_rows(self, pm: PageModel) -> List[FieldCandidate]:
        """
        Detect input fields in tables whose column structure is defined by
        tiling colored header rectangles — not explicit vertical lines.

        Pattern recognised:
          - 3+ colored rectangles that share the same top & bottom Y-coordinate
            and tile horizontally end-to-end across a significant page span.
          - The rectangles form a table header row; their bottom-Y is the top
            boundary of the first data row.
          - One or more horizontal lines below define the subsequent row boundaries.
          - Empty cells in those data rows become TEXT field candidates.

        Safety:
          - Cells that already contain pre-filled text (words covering >40% of
            cell width) are skipped — they are read-only columns.
          - Only well-formed row heights (8-80 pt) are processed.
          - Returns empty list when no qualifying group is found, so no change
            for PDFs that don't match this pattern.
        """
        if not pm.rects:
            return []

        candidates: List[FieldCandidate] = []
        padding = 1.5

        # ---- Step 1: group colored rects by shared (approx top, approx bottom) ----
        from collections import defaultdict as _dd
        rect_groups: Dict[Tuple[int, int], List[Dict]] = _dd(list)

        for r in pm.rects:
            top = float(r.get('top', r.get('y0', 0)))
            bot = float(r.get('bottom', r.get('y1', 0)))
            x0 = float(r.get('x0', 0))
            x1 = float(r.get('x1', 0))

            h = bot - top
            w = x1 - x0
            if not (8 < h < 60) or w < 10:
                continue

            # Must be non-white, non-transparent colored fill
            fill = r.get('non_stroking_color') or r.get('fill')
            if fill is None:
                continue
            if isinstance(fill, (int, float)):
                if fill > 0.92:          # near-white grayscale
                    continue
            elif isinstance(fill, (list, tuple)):
                if len(fill) == 1 and fill[0] > 0.92:
                    continue
                elif len(fill) == 3 and all(c > 0.90 for c in fill):
                    continue             # near-white RGB
                elif len(fill) == 4 and sum(fill) < 0.05:
                    continue             # near-white CMYK

            rect_groups[(round(top), round(bot))].append(
                {'x0': x0, 'x1': x1, 'top': top, 'bottom': bot}
            )

        # ---- Step 2: find groups that tile horizontally (3+ contiguous rects) ----
        for (tk, bk), rects in rect_groups.items():
            if len(rects) < 3:
                continue

            rects_sorted = sorted(rects, key=lambda r: r['x0'])

            # Verify rects tile (each rect's x1 ≈ next rect's x0, gap ≤ 5 pt)
            tiling = True
            for i in range(len(rects_sorted) - 1):
                gap = rects_sorted[i + 1]['x0'] - rects_sorted[i]['x1']
                if gap > 5:
                    tiling = False
                    break
            if not tiling:
                continue

            # Collect all column boundary x-positions
            col_x = sorted(set(
                [r['x0'] for r in rects_sorted] + [r['x1'] for r in rects_sorted]
            ))
            if len(col_x) < 4:           # need at least 3 columns (4 boundaries)
                continue

            header_bottom = float(bk)    # bottom of header = top of first data row
            table_x0 = rects_sorted[0]['x0']
            table_x1 = rects_sorted[-1]['x1']
            table_span = table_x1 - table_x0

            if table_span < pm.width * 0.25:   # table must span ≥25% page width
                continue

            # ---- Step 3: find h_lines below header_bottom that bound data rows ----
            # Group segments by Y (1pt bucket) and test whether the group
            # collectively covers both table edges — a row-separator line is
            # valid even when it is split into non-contiguous segments (e.g.
            # the middle column has no divider but the left and right edges do).
            _SPAN_TOL = 20.0
            _segs_by_y: Dict[int, List[Dict]] = _dd(list)
            for hl in pm.h_lines:
                hy = float(hl.get('y', hl.get('top', 0)))
                if header_bottom + 5 < hy < header_bottom + 200:
                    _segs_by_y[round(hy)].append(hl)
            below_ys = sorted(
                float(yk)
                for yk, hls in _segs_by_y.items()
                if any(float(hl.get('x0', 0)) <= table_x0 + _SPAN_TOL
                       for hl in hls)
                and any(float(hl.get('x1', 0)) >= table_x1 - _SPAN_TOL
                        for hl in hls)
            )

            if not below_ys:
                continue

            # ---- Guard: skip when vertical lines already define interior
            # columns in the data area.  That means a proper grid detector
            # (table_cells / grid_gap_fill) will produce sub-column fields;
            # tiling_rect candidates would be wider and crowd them out in
            # the resolver.  Only fire when there are NO interior v-lines
            # (the pattern this detector was designed for).
            _VLINE_TOL = 8.0
            data_y0 = header_bottom
            data_y1 = below_ys[-1] if below_ys else header_bottom + 80
            interior_vx: set = set()
            for vl in pm.v_lines:
                vx = float(vl.get('x0', vl.get('x', 0)))
                vy0 = float(vl.get('top', vl.get('y0', 0)))
                vy1 = float(vl.get('bottom', vl.get('y1', 0)))
                # v-line must be inside the table span (not at borders)
                if vx <= table_x0 + _VLINE_TOL or vx >= table_x1 - _VLINE_TOL:
                    continue
                # v-line must overlap the data row region
                if vy1 < data_y0 - 5 or vy0 > data_y1 + 5:
                    continue
                interior_vx.add(round(vx))
            if len(interior_vx) >= 1:
                continue  # proper grid with v-line columns — skip

            # Build row (top_y, bottom_y) pairs
            row_bounds: List[Tuple[float, float]] = []
            prev_y = header_bottom
            for hy in below_ys:
                rh = hy - prev_y
                if 8 < rh < 80:
                    row_bounds.append((prev_y, hy))
                prev_y = hy

            if not row_bounds:
                continue

            # ---- Step 4: extract header label text per column ----
            # Used to give each candidate a non-empty label so that
            # AdjacentFieldMerger sees them as intentionally distinct
            # fields (not segmented-line fragments that should be joined).
            header_top = min(r['top'] for r in rects_sorted)
            col_labels: Dict[float, str] = {}
            for ci in range(len(col_x) - 1):
                cx0_h, cx1_h = col_x[ci], col_x[ci + 1]
                hdr_words = [
                    w for w in pm.words
                    if float(w.get('x0', 0)) >= cx0_h - 3
                    and float(w.get('x1', 0)) <= cx1_h + 3
                    and float(w.get('top', 0)) >= header_top - 3
                    and float(w.get('top', 0)) <= header_bottom + 3
                ]
                col_labels[cx0_h] = ' '.join(
                    w.get('text', '') for w in hdr_words
                ).strip()

            # ---- Step 5: create text fields for empty cells in each data row ----
            for row_y0, row_y1 in row_bounds:
                for ci in range(len(col_x) - 1):
                    cell_x0 = col_x[ci]
                    cell_x1 = col_x[ci + 1]
                    cell_w = cell_x1 - cell_x0
                    if cell_w < 12:
                        continue

                    # Find words in this cell area
                    cell_words = [
                        w for w in pm.words
                        if float(w.get('x0', 0)) >= cell_x0 - 3
                        and float(w.get('x1', 0)) <= cell_x1 + 3
                        and float(w.get('top', 0)) > row_y0
                        and float(w.get('top', 0)) < row_y1
                    ]

                    if cell_words:
                        # Skip if text spans >40% of cell width (pre-filled column)
                        text_x0 = min(float(w.get('x0', 0)) for w in cell_words)
                        text_x1 = max(float(w.get('x1', 0)) for w in cell_words)
                        text_span = text_x1 - text_x0
                        if text_span / max(1.0, cell_w) > 0.40:
                            continue    # pre-filled — skip

                    # Create text field for this empty (or sparse) cell
                    fx0 = cell_x0 + padding
                    fy0 = row_y0 + padding
                    fx1 = cell_x1 - padding
                    fy1 = row_y1 - padding

                    name_hint = 'Cell_%d_%d' % (int(cell_x0), int(row_y0))

                    candidates.append(FieldCandidate(
                        page=pm.page_num,
                        x0=fx0, y0=fy0, x1=fx1, y1=fy1,
                        field_type=FieldType.TEXT,
                        source='tiling_rect_table',
                        confidence=0.85,
                        name_hint=name_hint,
                        label=col_labels.get(cell_x0, ''),
                    ))

        return candidates
