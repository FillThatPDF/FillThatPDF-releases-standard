"""
Large image rectangle detector.

Ported from v23's _detect_large_image_rectangles().

Finds large empty rectangles that should become image upload buttons.
These are distinct from text area boxes in that they are:
1. Larger square/rectangular shapes (>80pt x >80pt)
2. Typically within table cells but not caught by table processing
3. Often have "Attach Image" or similar text nearby

Also creates fields for pre-detected image_upload_regions on the PageModel.

Source tag: 'large_image_rect'

Contract (v24 modular):
    * Receives read-only PageModel objects.
    * Returns List[FieldCandidate] -- never mutates shared state.
    * Does NOT check for overlaps with other detectors (the resolver handles that).
"""

from typing import List, Dict

from ..models import PageModel, FieldCandidate, FieldType
from ..helpers import bbox_overlap_area
from .base import BaseDetector


# Size constraints for image boxes
_MIN_WIDTH = 80
_MIN_HEIGHT = 80
_MAX_WIDTH = 600
_MAX_HEIGHT = 500
_MIN_ASPECT = 0.3
_MAX_ASPECT = 3.5


class ImageRectangleDetector(BaseDetector):
    """Detect large empty rectangles that should become image upload fields."""

    def detect(self, pages: List[PageModel]) -> List[FieldCandidate]:
        candidates: List[FieldCandidate] = []

        for page_model in pages:
            candidates.extend(self._detect_from_rects(page_model))
            candidates.extend(self._detect_from_image_regions(page_model, candidates))

        return candidates

    # ------------------------------------------------------------------
    # Grid-cell guard
    # ------------------------------------------------------------------

    @staticmethod
    def _is_visual_grid_cell(x0, y0, x1, y1, grid_cells, tol=3.0) -> bool:
        """Return True if (x0,y0,x1,y1) closely matches a visual grid cell.

        Image upload buttons should only appear on standalone empty boxes
        (like photo placeholder pages), NOT on regular table cells that
        happen to be large and empty.  Standalone photo boxes are drawn
        as raw rectangles/curves and never form part of a visual grid.
        """
        for gc in grid_cells:
            if (abs(x0 - gc[0]) < tol and abs(y0 - gc[1]) < tol
                    and abs(x1 - gc[2]) < tol and abs(y1 - gc[3]) < tol):
                return True
        return False

    # ------------------------------------------------------------------
    # Detect from page rects
    # ------------------------------------------------------------------

    def _detect_from_rects(self, page_model: PageModel) -> List[FieldCandidate]:
        results: List[FieldCandidate] = []
        page_num = page_model.page_num
        rects = page_model.rects
        words = page_model.words
        image_box_count = 0

        if not rects:
            return results

        for rect in rects:
            x0 = float(rect.get('x0', 0))
            y_top = float(rect.get('top', rect.get('y0', 0)))
            x1 = float(rect.get('x1', 0))
            y_bottom = float(rect.get('bottom', rect.get('y1', 0)))

            # Skip header region
            if y_top < 90:
                continue

            width = x1 - x0
            height = y_bottom - y_top

            # Size checks
            if width < _MIN_WIDTH or height < _MIN_HEIGHT:
                continue
            if width > _MAX_WIDTH or height > _MAX_HEIGHT:
                continue

            aspect_ratio = width / height if height > 0 else 0
            if aspect_ratio < _MIN_ASPECT or aspect_ratio > _MAX_ASPECT:
                continue

            # Skip rectangles that are visual grid cells (table cells,
            # not standalone photo-placeholder boxes)
            if self._is_visual_grid_cell(x0, y_top, x1, y_bottom,
                                         page_model.visual_grid_cells):
                continue

            # Check if mostly empty (minimal text inside)
            words_inside = [
                w for w in words
                if float(w['x0']) >= x0 and float(w.get('x1', 0)) <= x1
                and float(w['top']) >= y_top and float(w.get('bottom', 0)) <= y_bottom
            ]
            if len(words_inside) > 5:
                continue

            # Apply small inset for stroke width
            inset = 2
            field_x0 = x0 + inset
            field_x1 = x1 - inset
            field_y0 = y_top + inset
            field_y1 = y_bottom - inset

            image_box_count += 1
            results.append(FieldCandidate(
                page=page_num,
                x0=field_x0,
                y0=field_y0,
                x1=field_x1,
                y1=field_y1,
                field_type=FieldType.IMAGE_UPLOAD,
                source='large_image_rect',
                name_hint=f"ImageUpload_P{page_num}_{image_box_count}",
                label='Attach Image',
                is_image_box=True,
            ))

        return results

    # ------------------------------------------------------------------
    # Create fields from pre-detected image upload regions
    # ------------------------------------------------------------------

    def _detect_from_image_regions(
        self,
        page_model: PageModel,
        existing_candidates: List[FieldCandidate],
    ) -> List[FieldCandidate]:
        """Create fields for pre-detected image_upload_regions not yet covered."""
        results: List[FieldCandidate] = []
        page_num = page_model.page_num

        if not page_model.image_upload_regions:
            return results

        image_box_count = len(existing_candidates) + 1

        for region in page_model.image_upload_regions:
            rx0 = float(region.get('x0', 0))
            ry0 = float(region.get('y0', 0))
            rx1 = float(region.get('x1', 0))
            ry1 = float(region.get('y1', 0))

            rw = rx1 - rx0
            rh = ry1 - ry0

            # Same size constraints as _detect_from_rects — reject page-size
            # background images and tiny fragments
            if rw < _MIN_WIDTH or rh < _MIN_HEIGHT:
                continue
            if rw > _MAX_WIDTH or rh > _MAX_HEIGHT:
                continue
            aspect = rw / rh if rh > 0 else 0
            if aspect < _MIN_ASPECT or aspect > _MAX_ASPECT:
                continue

            # Skip regions that are visual grid cells (table cells)
            if self._is_visual_grid_cell(rx0, ry0, rx1, ry1,
                                         page_model.visual_grid_cells):
                continue

            # Check if already covered by an existing candidate
            already_covered = False
            for c in existing_candidates:
                if c.page != page_num or not c.is_image_box:
                    continue
                overlap = bbox_overlap_area(
                    (rx0, ry0, rx1, ry1),
                    (c.x0, c.y0, c.x1, c.y1),
                )
                region_area = max(1e-6, (rx1 - rx0) * (ry1 - ry0))
                if overlap / region_area > 0.5:
                    already_covered = True
                    break

            if already_covered:
                continue

            inset = 2
            results.append(FieldCandidate(
                page=page_num,
                x0=rx0 + inset,
                y0=ry0 + inset,
                x1=rx1 - inset,
                y1=ry1 - inset,
                field_type=FieldType.IMAGE_UPLOAD,
                source='large_image_rect',
                name_hint=f"ImageUpload_P{page_num}_{image_box_count}",
                label='Attach Image',
                is_image_box=True,
            ))
            image_box_count += 1

        return results
