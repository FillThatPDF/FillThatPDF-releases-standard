"""
Shaded-field detector — detects light-gray shaded rectangles that indicate
fillable areas.

Many modern forms use subtle gray fills instead of lines or underscores to
mark input fields.  This detector catches those by inspecting the fill color
of rectangles extracted by pdfplumber.

Contract (v24 modular):
    * Receives read-only PageModel objects.
    * Returns List[FieldCandidate] -- never mutates shared state.
"""

from typing import List, Dict, Optional

from ..models import PageModel, FieldCandidate, FieldType
from ..helpers import clean_field_name, detect_field_format
from .base import BaseDetector


class ShadedFieldDetector(BaseDetector):
    """Detect light-gray shaded rectangles that indicate fillable areas."""

    def detect(self, pages: List[PageModel]) -> List[FieldCandidate]:
        candidates: List[FieldCandidate] = []
        for page_model in pages:
            candidates.extend(self._detect_shaded_rects(page_model))
        return candidates

    # ------------------------------------------------------------------
    # Color classification
    # ------------------------------------------------------------------

    @staticmethod
    def _is_light_gray(color) -> bool:
        """Check if a fill color indicates light gray shading.

        Accepts grayscale (single float), RGB (3-tuple), or CMYK (4-tuple)
        from pdfplumber's non_stroking_color.
        """
        if color is None:
            return False
        if isinstance(color, (int, float)):
            color = [color]
        if not color:
            return False

        if len(color) == 1:  # Grayscale
            return 0.70 < color[0] < 0.95
        elif len(color) == 3:  # RGB
            r, g, b = color
            avg = (r + g + b) / 3
            spread = max(r, g, b) - min(r, g, b)
            # Near-gray (channels close together) and in the light range
            return 0.70 < avg < 0.98 and spread < 0.08
        elif len(color) == 4:  # CMYK
            c_, m, y, k = color
            # Light gray in CMYK: minimal chromatic ink, moderate K
            return c_ + m + y < 0.15 and 0.05 < k < 0.30
        return False

    # ------------------------------------------------------------------
    # Per-page detection
    # ------------------------------------------------------------------

    def _detect_shaded_rects(self, page: PageModel) -> List[FieldCandidate]:
        results: List[FieldCandidate] = []
        page_num = page.page_num

        for rect in page.rects:
            # Must be a FILLED rect
            if not rect.get('fill'):
                continue

            color = rect.get('non_stroking_color')
            if not self._is_light_gray(color):
                continue

            x0 = float(rect.get('x0', 0))
            y0 = float(rect.get('top', rect.get('y0', 0)))
            x1 = float(rect.get('x1', 0))
            y1 = float(rect.get('bottom', rect.get('y1', 0)))
            w = x1 - x0
            h = y1 - y0

            # --- Dimension filters ---
            if w < 30 or h < 8:
                continue  # Too small to be a field
            if h < 3:
                continue  # Thin line, not a field
            if h > 60 and w < 60:
                continue  # Tall narrow box — decorative
            if h > 80:
                continue  # Very tall — likely a background fill

            # --- Structural exclusions ---
            # Skip rects inside visual grid cells (table detectors handle those)
            if self._is_inside_grid_cell(x0, y0, x1, y1, page):
                continue

            # Skip header bars
            if page.is_in_header_bar(x0, y0, x1, y1):
                continue

            # Skip rects that contain substantial text (pre-filled, not empty)
            if page.rect_has_text(x0, y0, x1, y1, min_chars=3):
                continue

            # Skip rects overlapping raster images
            if page.overlaps_raster_image(x0, y0, x1, y1, 0.3):
                continue

            # --- Find label ---
            label = self._find_label(x0, y0, x1, y1, page)
            name_hint = clean_field_name(label) if label else None
            fmt_hint, fmt_opts = detect_field_format(None, label)

            results.append(FieldCandidate(
                page=page_num,
                x0=x0, y0=y0, x1=x1, y1=y1,
                field_type=FieldType.TEXT,
                source='shaded_field',
                name_hint=name_hint,
                label=label,
                confidence=0.85,
                format_hint=fmt_hint,
                format_options=fmt_opts if fmt_opts else None,
            ))

        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_inside_grid_cell(
        x0: float, y0: float, x1: float, y1: float,
        page: PageModel, tol: float = 3.0,
    ) -> bool:
        """Check if a rect sits fully inside a visual grid cell."""
        for cx0, cy0, cx1, cy1 in page.visual_grid_cells:
            if (x0 >= cx0 - tol and y0 >= cy0 - tol
                    and x1 <= cx1 + tol and y1 <= cy1 + tol):
                return True
        return False

    def _find_label(
        self,
        x0: float, y0: float, x1: float, y1: float,
        page: PageModel,
    ) -> Optional[str]:
        """Find a label to the left or above a shaded rect."""
        words = page.words
        mid_y = (y0 + y1) / 2

        # --- Left: words ending before rect x0 on same Y band ---
        left_words = []
        for w in words:
            wx1 = float(w['x1'])
            wy0 = float(w['top'])
            wy1 = float(w['bottom'])
            w_mid_y = (wy0 + wy1) / 2

            # Same vertical band (within 5pt of field center)
            if abs(w_mid_y - mid_y) > 5:
                continue
            # Ends before the rect (within 200pt)
            if wx1 > x0 + 2:
                continue
            if wx1 < x0 - 200:
                continue
            left_words.append(w)

        if left_words:
            # Sort right-to-left, collect up to 5 consecutive words
            left_words.sort(key=lambda w: float(w['x1']), reverse=True)
            label_parts = []
            prev_x0 = x0
            for w in left_words:
                wx1 = float(w['x1'])
                gap = prev_x0 - wx1
                if gap > 30 and label_parts:
                    break  # Too far from the label cluster
                label_parts.insert(0, w['text'].rstrip(':'))
                prev_x0 = float(w['x0'])
                if len(label_parts) >= 5:
                    break
            if label_parts:
                return ' '.join(label_parts).strip()

        # --- Above: words within 20pt above the rect ---
        above_words = []
        for w in words:
            wb = float(w['bottom'])
            wx0 = float(w['x0'])
            wx1 = float(w['x1'])
            if wb > y0 + 2:
                continue
            if wb < y0 - 20:
                continue
            # Must overlap horizontally with the rect
            if wx1 < x0 - 10 or wx0 > x1 + 10:
                continue
            above_words.append(w)

        if above_words:
            above_words.sort(key=lambda w: float(w['x0']))
            return ' '.join(
                w['text'].rstrip(':') for w in above_words[:6]
            ).strip()

        return None
