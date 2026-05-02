"""
General underscore field detector.

Ported from v23's _detect_general_underscore_fields().

Finds text words containing 3+ consecutive underscores (e.g. "Name: ____________")
and creates text fields at the underscore positions.  Also handles embedded
underscore patterns within text (e.g. "Volts____").

Source tag: 'general_underscore'

Contract (v24 modular):
    * Receives read-only PageModel objects.
    * Returns List[FieldCandidate] -- never mutates shared state.
    * Does NOT check for overlaps with other detectors (the resolver handles that).
"""

import re
from typing import List, Dict, Optional, Set

from ..models import PageModel, FieldCandidate, FieldType
from ..helpers import clean_field_name, detect_field_format
from .base import BaseDetector


class UnderscoreFieldDetector(BaseDetector):
    """Detect fill-in fields from underscore TEXT patterns on the page."""

    def detect(self, pages: List[PageModel]) -> List[FieldCandidate]:
        candidates: List[FieldCandidate] = []

        for page_model in pages:
            candidates.extend(self._detect_underscore_fields(page_model))

        return candidates

    # ------------------------------------------------------------------
    # Core detection
    # ------------------------------------------------------------------

    def _detect_underscore_fields(self, page_model: PageModel) -> List[FieldCandidate]:
        results: List[FieldCandidate] = []
        words = page_model.words
        page_num = page_model.page_num
        page_width = page_model.width
        page_height = page_model.height
        used_indices: Set[int] = set()

        for i, word in enumerate(words):
            if i in used_indices:
                continue

            text = word['text']

            # Need at least 4 consecutive underscores
            if '____' not in text:
                continue

            underscore_x0 = float(word['x0'])
            underscore_x1 = float(word['x1'])
            underscore_y = float(word['top'])
            underscore_bottom = float(word['bottom'])

            # Extract prefix label (e.g. "Volts____")
            prefix_label = None
            first_underscore_idx = text.find('_')
            if first_underscore_idx > 0:
                prefix_label = text[:first_underscore_idx].rstrip(':').strip()

            # Find character-level start to skip labels like "Volts"
            found_underscore_x = underscore_x0
            if first_underscore_idx > 0:
                ratio = first_underscore_idx / len(text)
                found_underscore_x = underscore_x0 + (underscore_x1 - underscore_x0) * ratio

            underscore_x0 = found_underscore_x
            underscore_width = underscore_x1 - underscore_x0

            # Minimum width check
            is_bottom_of_page = (underscore_y > page_height - 120)
            min_width = 20 if is_bottom_of_page else 30
            if underscore_width < min_width:
                continue

            # Skip section headings (ALL CAPS with >3 words)
            clean_for_header = text.replace('_', '').strip()
            if clean_for_header and clean_for_header.isupper() and len(clean_for_header.split()) > 3:
                continue

            # Skip large font titles (>16pt tall)
            word_height = underscore_bottom - underscore_y
            if word_height > 16:
                continue

            # Skip horizontal rules (>80% of page width)
            if underscore_width > page_width * 0.80:
                continue

            # Find label to the left on the same line
            label = None
            label_max_x1 = 0
            label_parts = []

            curr_idx = i - 1
            while curr_idx >= max(0, i - 10):
                prev_word = words[curr_idx]
                prev_y = float(prev_word['top'])
                prev_x1 = float(prev_word['x1'])
                prev_text = prev_word['text']

                # Stop at another underscore
                if '____' in prev_text:
                    break

                # Same line (within 5pt)
                if abs(prev_y - underscore_y) < 5:
                    if prev_x1 < underscore_x0 + 5:
                        clean_text = prev_text.rstrip(':').strip()
                        if clean_text and (len(clean_text) > 1 or clean_text in ['=', 'x', '+', '-', '/', '#', '$']):
                            label_parts.insert(0, clean_text)
                            label_max_x1 = max(label_max_x1, prev_x1)

                        # Large gap -> stop
                        if (underscore_x0 - prev_x1) > 40:
                            break
                curr_idx -= 1

            if prefix_label:
                label_parts.append(prefix_label)

            label = ' '.join(label_parts).strip()

            # Skip garbage labels (too long)
            if label and (len(label) > 90 or len(label.split()) > 12):
                continue

            # OCR fixes for technical terms
            if label:
                label_up = label.upper()
                if 'AMMPS' in label_up or 'AMPS' in label_up:
                    label = 'Amps'
                if label.upper() == 'VOLTS':
                    label = 'Volts'
                if label.upper() == 'CFM':
                    label = 'CFM'

            # Detect currency for $ _____ patterns
            is_dollar_field = (label == '$' or (label and label.startswith('$')))

            # Generate field name
            if is_dollar_field:
                base_name = "Total_Cost"
            elif label:
                base_name = clean_field_name(label)
            elif 'Other' in text:
                base_name = "Other"
            else:
                base_name = "Field"

            # Determine format
            if is_dollar_field:
                field_format = 'currency'
                format_opts = {'maxlen': 12, 'has_dollar_in_cell': True}
            else:
                field_format, format_opts = detect_field_format(None, label)

            # Ensure field starts after label
            final_x0 = max(underscore_x0, label_max_x1 + 3.5) if label_max_x1 > 0 else underscore_x0

            used_indices.add(i)

            results.append(FieldCandidate(
                page=page_num,
                x0=final_x0,
                y0=underscore_bottom - 13,
                x1=underscore_x1,
                y1=underscore_bottom + 1,
                field_type=FieldType.TEXT,
                source='general_underscore',
                name_hint=base_name,
                label=label if label else ('Other' if 'Other' in text else None),
                format_hint=field_format,
                format_options=format_opts if format_opts else None,
            ))

        # --- Second pass: detect consecutive single underscore characters ---
        # Some PDFs (e.g. 57561 DTE Rebate Worksheet) render fill-in
        # areas as individual underscore characters rather than a single
        # word containing "____".  Group them by y-position and create
        # fields for runs of ≥4 consecutive underscores.
        results.extend(self._detect_char_underscore_runs(page_model))

        return results

    def _detect_char_underscore_runs(
        self, page_model: PageModel
    ) -> List[FieldCandidate]:
        """Detect fill-in fields from consecutive single '_' characters."""
        results: List[FieldCandidate] = []
        words = page_model.words
        page_num = page_model.page_num

        # Collect single underscore words
        underscore_words = []
        for w in words:
            if w.get('text', '').strip() == '_':
                underscore_words.append(w)

        if len(underscore_words) < 4:
            return results

        # Group by y-position (within 3pt)
        y_groups: Dict[float, list] = {}
        for w in underscore_words:
            wy = round(float(w.get('top', w.get('y0', 0))))
            placed = False
            for key_y in list(y_groups.keys()):
                if abs(wy - key_y) < 3:
                    y_groups[key_y].append(w)
                    placed = True
                    break
            if not placed:
                y_groups[wy] = [w]

        for y_key, group in y_groups.items():
            if len(group) < 4:
                continue

            # Sort by x
            group.sort(key=lambda w: float(w.get('x0', 0)))

            # Find consecutive runs (gap < 8pt between underscores)
            runs = []
            current_run = [group[0]]
            for j in range(1, len(group)):
                prev_x1 = float(current_run[-1].get('x1', 0))
                curr_x0 = float(group[j].get('x0', 0))
                if curr_x0 - prev_x1 < 8:
                    current_run.append(group[j])
                else:
                    if len(current_run) >= 4:
                        runs.append(current_run)
                    current_run = [group[j]]
            if len(current_run) >= 4:
                runs.append(current_run)

            for run in runs:
                x0 = float(run[0].get('x0', 0))
                x1 = float(run[-1].get('x1', 0))
                y0 = min(float(w.get('top', w.get('y0', 0))) for w in run)
                y1 = max(float(w.get('bottom', w.get('y1', 0))) for w in run)
                run_width = x1 - x0

                if run_width < 20:
                    continue

                # Look for label to the left (e.g. "$")
                label = None
                is_dollar = False
                for w in words:
                    wt = w.get('text', '').strip()
                    wx1 = float(w.get('x1', 0))
                    wy = float(w.get('top', w.get('y0', 0)))
                    if (abs(wy - y0) < 5
                            and wx1 < x0 + 5
                            and x0 - wx1 < 30
                            and wt and '_' not in wt):
                        if wt == '$':
                            is_dollar = True
                            label = '$'
                        elif not label:
                            label = wt.rstrip(':').strip()

                if is_dollar:
                    base_name = "Total_Cost"
                    fmt = 'currency'
                    fmt_opts = {'maxlen': 12, 'has_dollar_in_cell': True}
                elif label:
                    base_name = clean_field_name(label)
                    fmt, fmt_opts = detect_field_format(None, label)
                else:
                    base_name = "Field"
                    fmt, fmt_opts = None, None

                # Adjust field position (use bottom of underscores)
                field_y0 = y1 - 13
                field_y1 = y1 + 1

                results.append(FieldCandidate(
                    page=page_num,
                    x0=x0,
                    y0=field_y0,
                    x1=x1,
                    y1=field_y1,
                    field_type=FieldType.TEXT,
                    source='general_underscore',
                    name_hint=base_name,
                    label=label,
                    format_hint=fmt,
                    format_options=fmt_opts,
                ))

        return results
