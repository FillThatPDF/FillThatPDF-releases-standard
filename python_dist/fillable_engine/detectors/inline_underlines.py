"""
Inline fill-underline detector.

Ported from v23's _detect_inline_fill_underlines().

Finds fill-in underlines that appear immediately after text, such as:
    "Other ________"         (short underline after checkbox label)
    "If no, replaced: ____"  (underline after text phrase)

The key signal is: a horizontal line starts within ~8pt of where a text word
ends, and the line is not a structural border.

Field height is adaptive -- it uses the configured field_height but will
shrink if vertical space is constrained (e.g., squeezed between rows).

Source tag: 'inline_fill_underline'

Contract (v24 modular):
    * Receives read-only PageModel objects.
    * Returns List[FieldCandidate] -- never mutates shared state.
    * Does NOT check for overlaps with other detectors (the resolver handles that).
"""

from typing import List, Dict, Optional

from ..models import PageModel, FieldCandidate, FieldType
from ..helpers import clean_field_name
from .base import BaseDetector


class InlineUnderlineDetector(BaseDetector):
    """Detect inline fill-in underlines that immediately follow text words."""

    def detect(self, pages: List[PageModel]) -> List[FieldCandidate]:
        candidates: List[FieldCandidate] = []

        for page_model in pages:
            if page_model.is_text_only:
                continue
            candidates.extend(self._detect_inline_underlines(page_model))

        return candidates

    # ------------------------------------------------------------------
    # Core detection
    # ------------------------------------------------------------------

    def _detect_inline_underlines(self, page_model: PageModel) -> List[FieldCandidate]:
        results: List[FieldCandidate] = []
        page_num = page_model.page_num
        page_width = page_model.width
        words = page_model.words

        # Collect horizontal lines
        h_lines = self._collect_h_lines(page_model)

        default_height = int(self.settings.get('field_height', 14))

        for hline in h_lines:
            lx0 = hline['x0']
            lx1 = hline['x1']
            ly = hline['y']
            length = hline['length']

            # Skip if inside a header bar
            if page_model.is_in_header_bar(lx0, ly - 2, lx1, ly + 2):
                continue

            # Skip if this line is a table border (aligns with 2+ visual grid cell edges)
            if self._is_table_border(page_model, lx0, ly, lx1):
                continue

            # Skip lines that are completely inside a visual grid cell
            # (these are cell dividers, not fill-in underlines)
            if self._is_inside_grid_cell(page_model, lx0, ly, lx1):
                continue

            # Find a text word that ends right before this line (0-8pt gap)
            preceding_word = None
            best_gap = 999.0

            for w in words:
                wx1 = float(w['x1'])
                wtop = float(w['top'])
                wbot = float(w['bottom'])

                # Vertically aligned with line (within 8pt)
                if abs(wbot - ly) > 8 and abs(wtop - ly) > 8:
                    continue

                # Word ends just before line starts
                gap = lx0 - wx1
                if gap < -2 or gap > 8:
                    continue

                if gap < best_gap:
                    best_gap = gap
                    preceding_word = w

            if preceding_word is None:
                continue

            # Skip full-width lines (structural borders)
            if length > page_width * 0.8:
                continue

            # Determine adaptive field height
            field_height = default_height
            nearest_above = ly
            nearest_below = ly + 20

            for w in words:
                wbot = float(w['bottom'])
                wtop = float(w['top'])
                wx0 = float(w['x0'])
                wx1_val = float(w.get('x1', wx0))

                if w is preceding_word:
                    continue

                # Only check words that overlap horizontally with the underline
                if wx1_val < lx0 + 5 or wx0 > lx1 - 5:
                    continue

                # Words above the line
                if wbot < ly and wbot > ly - 30:
                    nearest_above = max(nearest_above, wbot)

                # Words below the line
                if wtop > ly and wtop < ly + 20:
                    nearest_below = min(nearest_below, wtop)

            # Also check header bars for vertical space constraints
            for hb in page_model.header_bars:
                hb_y0 = hb.get('y0', 0)
                hb_y1 = hb.get('y1', 0)
                if hb_y0 > ly and hb_y0 < ly + 20:
                    nearest_below = min(nearest_below, hb_y0)
                if hb_y1 < ly and hb_y1 > ly - 30:
                    nearest_above = max(nearest_above, hb_y1)

            space_above = ly - nearest_above if nearest_above < ly else default_height
            if space_above < field_height:
                field_height = max(6, space_above - 1)

            # Build multi-word label from preceding word(s) on same line
            pw_text = preceding_word['text'].strip()
            pw_x0 = float(preceding_word['x0'])
            pw_y = float(preceding_word['top'])

            label_words = [pw_text]
            label_start_x = pw_x0

            # Search backwards for adjacent words in the same text line
            for w in sorted(words, key=lambda w: -float(w['x1'])):
                wx1_val = float(w['x1'])
                wtop = float(w['top'])

                if abs(wtop - pw_y) > 3:
                    continue

                gap = label_start_x - wx1_val
                if gap < -1 or gap > 10:
                    continue

                wtext = w['text'].strip()
                # Stop at structural markers
                if wtext in ('\u2022', '\u2610', '\u2611', '\u25cb', '\u25cf', '\u25a0', '\u25a1', ''):
                    break

                label_words.insert(0, wtext)
                label_start_x = float(w['x0'])

                if len(label_words) >= 8:
                    break

            label = ' '.join(label_words).rstrip(':').strip()

            # Skip long sentence-like labels — these indicate body text
            # paragraphs, not form fill areas. Real fill labels are short
            # ("Other", "If no, replaced:", "Specify:", etc.)
            label_word_count = len(label.split())
            has_colon = label.endswith(':') or ':' in label
            if label_word_count > 5 and not has_colon:
                continue

            base_name = clean_field_name(label) if label else "Inline_Field"

            # Create the field: bottom sits ON the underline
            field_y1 = ly
            field_y0 = ly - field_height

            results.append(FieldCandidate(
                page=page_num,
                x0=lx0,
                y0=field_y0,
                x1=lx1,
                y1=field_y1,
                field_type=FieldType.TEXT,
                source='inline_fill_underline',
                name_hint=base_name,
                label=label,
                source_line_x0=lx0,
                source_line_x1=lx1,
            ))

        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_table_border(page_model: PageModel, x0: float, y: float, x1: float) -> bool:
        """Check if a horizontal line aligns with visual grid cell edges."""
        if not page_model.visual_grid_cells:
            return False
        matches = 0
        has_wide_cell_match = False
        for cx0, cy0, cx1, cy1 in page_model.visual_grid_cells:
            y_matches_top = abs(y - cy0) < 3
            y_matches_bot = abs(y - cy1) < 3
            if y_matches_top or y_matches_bot:
                ov = min(x1, cx1) - max(x0, cx0)
                if ov > 0:
                    matches += 1
                    if cx1 - cx0 > 200:
                        has_wide_cell_match = True
        return matches >= 2 or has_wide_cell_match

    @staticmethod
    def _is_inside_grid_cell(page_model: PageModel, x0: float, y: float, x1: float) -> bool:
        """Check if a line sits inside a visual grid cell (internal row divider)."""
        if not page_model.visual_grid_cells:
            return False
        for cx0, cy0, cx1, cy1 in page_model.visual_grid_cells:
            # Line must be inside cell (not on its edges)
            if x0 >= cx0 - 2 and x1 <= cx1 + 2 and cy0 + 3 < y < cy1 - 3:
                return True
        return False

    @staticmethod
    def _collect_h_lines(page_model: PageModel) -> List[Dict]:
        """Collect horizontal lines suitable for inline fill detection."""
        h_lines: List[Dict] = []

        for line in page_model.lines:
            lx0 = float(line.get('x0', 0))
            lx1 = float(line.get('x1', 0))
            ltop = float(line.get('top', line.get('y0', 0)))
            lbot = float(line.get('bottom', line.get('y1', 0)))
            lw = float(line.get('linewidth', line.get('width', 1)))

            # Must be horizontal
            if abs(ltop - lbot) > 2:
                continue

            length = abs(lx1 - lx0)
            # Minimum 40pt for fill-in underlines
            if length < 40:
                continue
            # Skip thick borders
            if lw > 3:
                continue

            h_lines.append({
                'x0': min(lx0, lx1),
                'x1': max(lx0, lx1),
                'y': (ltop + lbot) / 2,
                'length': length,
            })

        return h_lines
