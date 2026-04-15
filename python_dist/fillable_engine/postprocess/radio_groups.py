"""
Post-processor: Radio group identification.

Runs AFTER the resolver commits fields and BEFORE the writer creates widgets.
Identifies clusters of checkboxes that should be grouped as radio buttons
(mutually-exclusive selections like Yes/No/N/A, Pass/Fail, etc.).

Ported from v23 _identify_radio_groups, _detect_section_based_radio_groups,
and _detect_horizontal_yes_no_groups.
"""

from collections import defaultdict
from typing import Dict, List, Optional, Set

from ..models import ResolvedField, FieldType, PageModel
from ..helpers import clean_field_name


# ---------------------------------------------------------------------------
# Canonical radio-column keywords  (lowercase -> display form)
# ---------------------------------------------------------------------------

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


class RadioGroupIdentifier:
    """
    Identify clusters of checkbox ResolvedFields that form radio groups.

    A radio group is a set of 2+ checkboxes that are mutually exclusive:
      - Vertically aligned in the same column (x-centres within 5pt)
        with radio-keyword labels, OR
      - Horizontally adjacent on the same row (y within 5pt)
        with radio-keyword labels (Yes/No/N/A, Pass/Fail, ...).

    After processing, the identified checkboxes have:
      - ``is_radio_child = True``
      - ``radio_group_name`` set to a unique group name
      - ``radio_value`` set to the display label for that option
    """

    # -- public entry point --------------------------------------------------

    def process(
        self,
        fields: List[ResolvedField],
        pages: List[PageModel],
    ) -> List[ResolvedField]:
        """
        Mutate *fields* in-place, setting radio-group attributes on checkbox
        clusters that qualify.  Returns the same list.
        """
        self._pages = {p.page_num: p for p in pages}
        self._group_counter = 0

        # Extract only checkboxes, indexed by page
        cb_fields = [f for f in fields if f.field_type == FieldType.CHECKBOX]
        if len(cb_fields) < 2:
            return fields

        already_grouped: Set[int] = set()  # id(field) already assigned

        # PASS 1 -- section-based grouping (roman numeral headers)
        self._detect_section_groups(cb_fields, already_grouped)

        # PASS 2 -- horizontal Yes/No neighbour detection
        self._detect_horizontal_yes_no(cb_fields, already_grouped)

        # PASS 3 -- row-based clustering (main pass)
        self._detect_row_clusters(cb_fields, already_grouped)

        # PASS 4 -- cleanup: remaining adjacent Yes/No pairs
        self._cleanup_ungrouped(cb_fields, already_grouped)

        return fields

    # -- pass helpers --------------------------------------------------------

    def _detect_section_groups(
        self,
        cb_fields: List[ResolvedField],
        already_grouped: Set[int],
    ) -> None:
        """Group checkboxes between consecutive Roman-numeral section headers."""
        for page_num, page in self._pages.items():
            words = page.words
            if not words:
                continue

            # Find Roman-numeral section headers
            roman = [
                'I.', 'II.', 'III.', 'IV.', 'V.',
                'VI.', 'VII.', 'VIII.', 'IX.', 'X.',
            ]
            headers = []
            for w in words:
                text = w.get('text', '').strip()
                if text in roman:
                    headers.append({
                        'text': text,
                        'y': float(w.get('top', 0)),
                        'x': float(w.get('x0', 0)),
                    })

            if len(headers) < 2:
                continue

            headers.sort(key=lambda h: h['y'])

            page_cbs = [
                f for f in cb_fields
                if f.page == page_num and id(f) not in already_grouped
            ]

            for i in range(len(headers) - 1):
                y_start = headers[i]['y']
                y_end = headers[i + 1]['y']
                section_cbs = [
                    f for f in page_cbs
                    if y_start < f.y0 < y_end
                ]
                if len(section_cbs) < 2:
                    continue

                radio_count = sum(
                    1 for f in section_cbs
                    if (f.label or '').lower().strip() in RADIO_COLUMN_KEYWORDS
                )
                if radio_count < 2:
                    continue

                name = f"Section_{headers[i]['text'].replace('.', '')}"
                group_name = self._unique_group_name(name, page_num)
                self._assign_group(section_cbs, group_name, already_grouped)

    def _detect_horizontal_yes_no(
        self,
        cb_fields: List[ResolvedField],
        already_grouped: Set[int],
    ) -> None:
        """Force-group adjacent Yes/No/NA checkboxes on the same row."""
        by_page: Dict[int, List[ResolvedField]] = defaultdict(list)
        for f in cb_fields:
            if id(f) not in already_grouped:
                by_page[f.page].append(f)

        for page_num, cbs in by_page.items():
            cbs.sort(key=lambda c: (round(c.y0 / 8), c.x0))
            i = 0
            while i < len(cbs):
                anchor = cbs[i]
                if id(anchor) in already_grouped:
                    i += 1
                    continue

                row_cbs = [anchor]
                j = i + 1
                while j < len(cbs):
                    candidate = cbs[j]
                    if id(candidate) in already_grouped:
                        j += 1
                        continue
                    if abs(candidate.y0 - anchor.y0) > 8:
                        break
                    prev = row_cbs[-1]
                    if (candidate.x0 - prev.x1) > 150:
                        break
                    # Check for colon-separator text between prev and candidate
                    if self._has_colon_separator(page_num, prev, candidate):
                        break
                    row_cbs.append(candidate)
                    j += 1

                if len(row_cbs) >= 2:
                    labels = [(f.label or '').strip().lower() for f in row_cbs]
                    radio_count = sum(
                        1 for lbl in labels
                        if self._is_radio_keyword(lbl)
                    )
                    if radio_count >= 2:
                        desc = self._find_row_description(row_cbs, page_num)
                        base = clean_field_name(desc) if desc else f"Radio_Page{page_num}_{int(anchor.y0)}"
                        suffix = '_YesNoNA' if len(row_cbs) >= 3 else '_YesNo'
                        group_name = self._unique_group_name(base + suffix, page_num)
                        self._assign_group(row_cbs, group_name, already_grouped)
                        i += len(row_cbs)
                        continue

                i += 1

    def _detect_row_clusters(
        self,
        cb_fields: List[ResolvedField],
        already_grouped: Set[int],
    ) -> None:
        """Main pass: group remaining checkboxes by row, then cluster by proximity."""
        by_row: Dict[tuple, List[ResolvedField]] = defaultdict(list)
        for f in cb_fields:
            if id(f) not in already_grouped:
                row_key = (f.page, round(f.y0 / 5) * 5)
                by_row[row_key].append(f)

        # Merge checkboxes slightly below a row into it (Spray Foam pattern)
        row_keys = list(by_row.keys())
        for (page, row_y) in row_keys:
            cbs_on_row = by_row[(page, row_y)]
            if len(cbs_on_row) < 2:
                continue
            x_sorted = sorted(f.x0 for f in cbs_on_row)
            max_gap = max(
                x_sorted[i + 1] - x_sorted[i]
                for i in range(len(x_sorted) - 1)
            )
            if max_gap > 100:
                continue  # wide-spaced row -- don't merge from below

            for other_key in row_keys:
                op, oy = other_key
                if op != page:
                    continue
                y_diff = oy - row_y
                if not (8 <= y_diff <= 18):
                    continue
                other_cbs = by_row[other_key]
                if len(other_cbs) >= 3:
                    continue
                if len(other_cbs) == len(cbs_on_row):
                    other_xs = sorted(f.x0 for f in other_cbs)
                    if all(
                        abs(other_xs[i] - x_sorted[i]) < 10
                        for i in range(len(x_sorted))
                    ):
                        continue  # same structure = repeating table row
                for ocb in list(other_cbs):
                    for rcb in cbs_on_row:
                        if abs(ocb.x0 - rcb.x0) < 10:
                            cbs_on_row.append(ocb)
                            other_cbs.remove(ocb)
                            break

        for key, cbs in by_row.items():
            if len(cbs) < 2:
                continue
            cbs_sorted = sorted(cbs, key=lambda c: c.x0)

            # Cluster by horizontal proximity (< 100pt gap)
            clusters: List[List[ResolvedField]] = []
            current: List[ResolvedField] = [cbs_sorted[0]]

            for idx in range(1, len(cbs_sorted)):
                gap = cbs_sorted[idx].x0 - cbs_sorted[idx - 1].x1
                sep = self._has_colon_separator(
                    key[0], cbs_sorted[idx - 1], cbs_sorted[idx]
                )
                if gap > 100 or sep:
                    if len(current) >= 2:
                        clusters.append(current)
                    current = [cbs_sorted[idx]]
                else:
                    current.append(cbs_sorted[idx])

            if len(current) >= 2:
                clusters.append(current)

            # Filter to clusters with >= 2 radio-keyword labels
            radio_clusters = []
            for cluster in clusters:
                labels = [(f.label or '').lower().strip() for f in cluster]
                rk_count = sum(1 for lbl in labels if lbl in RADIO_COLUMN_KEYWORDS)
                if rk_count >= 2:
                    radio_clusters.append(cluster)

            # Shared base name when multiple clusters share the same row
            row_base = None
            if len(radio_clusters) > 1:
                leftmost = min(radio_clusters, key=lambda c: min(f.x0 for f in c))
                desc = self._find_row_description(leftmost, key[0])
                if desc:
                    row_base = clean_field_name(desc)

            for cidx, cluster in enumerate(radio_clusters):
                page_num = key[0]
                first = cluster[0]
                all_labels = [(f.label or '').lower().strip() for f in cluster]
                all_radio = all(
                    lbl in RADIO_COLUMN_KEYWORDS or lbl == ''
                    for lbl in all_labels
                )

                if row_base:
                    gname = f"{row_base}_{cidx + 1}"
                elif first.label and not all_radio:
                    gname = clean_field_name(first.label)
                elif all_radio:
                    desc = self._find_row_description(cluster, page_num)
                    if desc:
                        gname = clean_field_name(desc)
                    else:
                        has_pf = any(l in ('pass', 'fail') for l in all_labels)
                        gname = f"PassFail_{self._group_counter}" if has_pf else f"YesNoNA_{self._group_counter}"
                else:
                    gname = f"RadioGroup_{self._group_counter}"

                group_name = self._unique_group_name(gname, page_num)
                self._assign_group(cluster, group_name, already_grouped)

    def _cleanup_ungrouped(
        self,
        cb_fields: List[ResolvedField],
        already_grouped: Set[int],
    ) -> None:
        """Final pass: catch remaining adjacent Yes/No pairs."""
        ungrouped = [f for f in cb_fields if id(f) not in already_grouped]
        by_page_row: Dict[tuple, List[ResolvedField]] = defaultdict(list)
        for f in ungrouped:
            row_key = (f.page, round(f.y0 / 10) * 10)
            by_page_row[row_key].append(f)

        for key, cbs in by_page_row.items():
            if len(cbs) < 2:
                continue
            cbs.sort(key=lambda c: c.x0)

            labels = [(f.label or '').strip().lower() for f in cbs]
            standard = all(
                lbl in ('yes', 'no', 'y', 'n', 'n/a', 'na', 'pass', 'fail')
                for lbl in labels
            )
            radio_count = sum(
                1 for lbl in labels
                if lbl in RADIO_COLUMN_KEYWORDS
                or lbl.startswith('yes') or lbl.startswith('no')
                or lbl.startswith('pass') or lbl.startswith('fail')
            )
            if radio_count >= 2 and standard:
                desc = self._find_row_description(cbs, key[0])
                gname = clean_field_name(desc) if desc else f"YesNo_Cleanup_{key[0]}_{key[1]}"
                group_name = self._unique_group_name(gname, key[0])
                self._assign_group(cbs, group_name, already_grouped)

    # -- internal utilities --------------------------------------------------

    def _assign_group(
        self,
        cluster: List[ResolvedField],
        group_name: str,
        already_grouped: Set[int],
    ) -> None:
        """Mark every field in *cluster* as belonging to *group_name*."""
        for idx, f in enumerate(cluster):
            f.is_radio_child = True
            f.radio_group_name = group_name

            label_lower = (f.label or '').lower().strip()
            if label_lower in RADIO_COLUMN_KEYWORDS:
                f.radio_value = RADIO_COLUMN_KEYWORDS[label_lower]
            else:
                col_hdr = self._find_column_header(f)
                if col_hdr:
                    f.radio_value = col_hdr
                elif f.label:
                    f.radio_value = f.label
                else:
                    f.radio_value = f"Option{idx + 1}"

            already_grouped.add(id(f))

    def _unique_group_name(self, base: str, page: int) -> str:
        self._group_counter += 1
        return f"{base}_p{page}_g{self._group_counter}"

    # -- spatial helpers -----------------------------------------------------

    def _find_column_header(self, field: ResolvedField) -> Optional[str]:
        """Search upward from *field* for a radio-keyword column header."""
        page = self._pages.get(field.page)
        if not page:
            return None

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

    def _find_row_description(
        self,
        cluster: List[ResolvedField],
        page_num: int,
    ) -> Optional[str]:
        """Find question / description text to the left of a checkbox cluster."""
        if not cluster:
            return None
        page = self._pages.get(page_num)
        if not page:
            return None

        leftmost = min(cluster, key=lambda f: f.x0)
        cb_x = leftmost.x0
        cb_y = leftmost.y0

        row_words = []
        for w in page.words:
            wx1 = float(w.get('x1', 0))
            wy = float(w.get('top', 0))
            if wx1 > cb_x - 15:
                continue
            y_diff = cb_y - wy
            if y_diff < -6 or y_diff > 8:
                continue
            text = w.get('text', '').strip()
            if text in ('', '-', '--', '*', '|', '.', ',', ':', ';'):
                continue
            row_words.append({
                'text': text,
                'x0': float(w.get('x0', 0)),
                'x1': wx1,
            })

        if not row_words:
            return None

        row_words.sort(key=lambda w: w['x0'])
        desc = ' '.join(w['text'] for w in row_words).strip()
        if desc and len(desc) >= 3 and not desc.isdigit():
            return desc
        return None

    def _has_colon_separator(
        self,
        page_num: int,
        left_field: ResolvedField,
        right_field: ResolvedField,
    ) -> bool:
        """Return True if there is a colon-ending word between two fields."""
        page = self._pages.get(page_num)
        if not page:
            return False
        for w in page.words:
            wx0 = float(w.get('x0', 0))
            wy = float(w.get('top', 0))
            if wx0 > left_field.x0 + 5 and wx0 < right_field.x0 - 5:
                if abs(wy - left_field.y0) < 30:
                    if w.get('text', '').strip().endswith(':'):
                        return True
        return False

    @staticmethod
    def _is_radio_keyword(label: str) -> bool:
        return (
            label in RADIO_COLUMN_KEYWORDS
            or label.startswith('yes') or label.startswith('no')
            or label.startswith('pass') or label.startswith('fail')
            or label.startswith('n/a') or label in ('y', 'n', 'na')
        )
