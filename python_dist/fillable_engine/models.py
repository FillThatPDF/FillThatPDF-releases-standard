"""
Core data models for the fillable engine.

These dataclasses define the contract between all modules:
- PageModel: immutable page structure (Phase 1 output)
- FieldCandidate: proposed field from a detector (Phase 2 output)
- ResolvedField: committed field after resolution (Phase 3 output)
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Set, Any
from enum import Enum


class FieldType(Enum):
    TEXT = "text"
    CHECKBOX = "checkbox"
    SIGNATURE = "signature"
    IMAGE_UPLOAD = "image_upload"
    COMB = "comb"


# ---------------------------------------------------------------------------
# Phase 1 output: Page structure (immutable after construction)
# ---------------------------------------------------------------------------

@dataclass
class PageModel:
    """
    Immutable structural data for one PDF page, built once during analysis.
    Passed read-only to all detectors.
    """
    page_num: int
    width: float
    height: float
    rotation: int
    mediabox: Tuple[float, float]  # (pw, ph) from PDF MediaBox

    # Raw pdfplumber data (cached once, never mutated)
    words: List[Dict] = field(default_factory=list)
    lines: List[Dict] = field(default_factory=list)
    rects: List[Dict] = field(default_factory=list)
    curves: List[Dict] = field(default_factory=list)
    edges: List[Dict] = field(default_factory=list)
    tables: List[Any] = field(default_factory=list)  # pdfplumber Table objects
    images: List[Dict] = field(default_factory=list)

    # Derived structural data (computed once during analysis)
    header_bars: List[Dict] = field(default_factory=list)
    h_lines: List[Dict] = field(default_factory=list)
    v_lines: List[Dict] = field(default_factory=list)
    visual_grid_cells: List[Tuple[float, float, float, float]] = field(default_factory=list)

    # Classification flags
    is_text_only: bool = False
    is_tally_sheet: bool = False
    is_info_page: bool = False  # All tables fully pre-filled → no fillable fields expected

    # Footer exclusion zone — y threshold below which fields are suppressed
    footer_y: float = 0.0  # 0 = no footer detected

    # Pre-detected exclusion zones
    box_entry_areas: List[Dict] = field(default_factory=list)
    image_upload_regions: List[Dict] = field(default_factory=list)
    square_grid_regions: List[Dict] = field(default_factory=list)
    prefilled_col_ranges: Set[Tuple[float, float, float, float]] = field(default_factory=set)
    column_classifications: Dict[int, str] = field(default_factory=dict)  # round(x0) -> 'fillable'|'read-only'|'unknown'
    table_bboxes: List[Tuple[float, float, float, float]] = field(default_factory=list)  # (x0,y0,x1,y1) for each table

    # Existing form fields from source PDF (pre-existing checkboxes, etc.)
    existing_checkboxes: List[Dict] = field(default_factory=list)
    existing_form_fields: List[Dict] = field(default_factory=list)

    # -----------------------------------------------------------------------
    # Read-only helpers (no mutation)
    # -----------------------------------------------------------------------

    def get_words_in_bbox(self, bbox, overlap_threshold=0.20):
        """Find words that significantly overlap a bounding box."""
        x0, y0, x1, y1 = bbox
        cell_area = max(1e-6, (x1 - x0) * (y1 - y0))
        valid = []
        for w in self.words:
            ix0 = max(x0, w['x0'])
            iy0 = max(y0, w['top'])
            ix1 = min(x1, w['x1'])
            iy1 = min(y1, w['bottom'])
            if ix1 > ix0 and iy1 > iy0:
                inter = (ix1 - ix0) * (iy1 - iy0)
                word_area = max(1e-6, (w['x1'] - w['x0']) * (w['bottom'] - w['top']))
                if (inter / word_area > overlap_threshold or
                        inter / cell_area > overlap_threshold):
                    valid.append(w)
        return valid

    def get_text_in_bbox(self, bbox, overlap_threshold=0.20):
        """Get concatenated text from a bounding box."""
        words = self.get_words_in_bbox(bbox, overlap_threshold)
        if not words:
            return ""
        words.sort(key=lambda w: (w['top'], w['x0']))
        return " ".join(w['text'] for w in words).strip()

    def is_in_header_bar(self, x0, y0, x1, y1):
        """Check if a bounding box significantly overlaps a header bar."""
        cell_area = (x1 - x0) * (y1 - y0)
        if cell_area <= 0:
            return False
        for hb in self.header_bars:
            hb_w = hb['x1'] - hb['x0']
            if hb_w < 200:
                continue  # Narrow colored box — probably a field, not a section header
            ix0 = max(x0, hb['x0'])
            iy0 = max(y0, hb['y0'])
            ix1 = min(x1, hb['x1'])
            iy1 = min(y1, hb['y1'])
            inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
            if inter > 0.2 * cell_area:
                return True
        return False

    def is_inside_text_header_cell(self, x0, y0, x1, y1, min_overlap=0.7):
        """
        Check if a bounding box is deeply inside a wide visual grid cell
        that already contains text.

        This catches fields placed inside section header cells, sub-header
        label cells, and other structural non-fillable areas — even for
        protected sources that bypass the normal header bar check.

        A grid cell is considered a "text header cell" if it:
        - Is at least 150pt wide
        - Is 5-35pt tall (header-shaped, not a tall data cell)
        - Already contains 3+ characters of text
        """
        field_area = max(1e-6, (x1 - x0) * (y1 - y0))
        for cx0, cy0, cx1, cy1 in self.visual_grid_cells:
            cell_w = cx1 - cx0
            cell_h = cy1 - cy0
            if cell_w < 150 or cell_h > 35 or cell_h < 5:
                continue
            # Check overlap
            ix0 = max(x0, cx0)
            iy0 = max(y0, cy0)
            ix1 = min(x1, cx1)
            iy1 = min(y1, cy1)
            if ix1 <= ix0 or iy1 <= iy0:
                continue
            inter = (ix1 - ix0) * (iy1 - iy0)
            if inter / field_area < min_overlap:
                continue
            # Cell must contain text
            text = self.get_text_in_bbox((cx0, cy0, cx1, cy1), 0.15)
            if len(text.strip()) >= 3:
                # Cells with underscore runs ("___") contain entry areas
                # and are NOT pure text headers — skip them.
                if '___' in text:
                    continue
                return True
        return False

    def is_in_image_upload_region(self, x0, y0, x1, y1, min_overlap=0.3):
        """Check if bbox overlaps an image upload region."""
        area = max(1e-6, (x1 - x0) * (y1 - y0))
        for r in self.image_upload_regions:
            ix0 = max(x0, r['x0'])
            iy0 = max(y0, r['y0'])
            ix1 = min(x1, r['x1'])
            iy1 = min(y1, r['y1'])
            if ix1 > ix0 and iy1 > iy0:
                inter = (ix1 - ix0) * (iy1 - iy0)
                if inter / area >= min_overlap:
                    return True
        return False

    def overlaps_raster_image(self, x0, y0, x1, y1, min_overlap=0.3):
        """Check if bbox overlaps any raster image (logos, photos, etc.).

        Returns True if the intersection area / candidate area >= min_overlap.
        """
        area = max(1e-6, (x1 - x0) * (y1 - y0))
        for img in self.images:
            img_x0 = float(img.get('x0', 0))
            img_y0 = float(img.get('y0', img.get('top', 0)))
            img_x1 = float(img.get('x1', 0))
            img_y1 = float(img.get('y1', img.get('bottom', 0)))
            ix0 = max(x0, img_x0)
            iy0 = max(y0, img_y0)
            ix1 = min(x1, img_x1)
            iy1 = min(y1, img_y1)
            if ix1 > ix0 and iy1 > iy0:
                inter = (ix1 - ix0) * (iy1 - iy0)
                if inter / area >= min_overlap:
                    return True
        return False

    def rect_has_text(self, x0, y0, x1, y1, min_chars=2):
        """Check if a rectangle contains meaningful text."""
        text = self.get_text_in_bbox((x0, y0, x1, y1), 0.15)
        return len(text.strip()) >= min_chars

    def is_in_table_gap(self, x0, y0, x1, y1, max_gap=30):
        """Check if a bbox is in a narrow vertical gap between two tables.

        Returns True if the bbox sits vertically between two table bounding
        boxes (within max_gap pt) and overlaps horizontally with both.
        This detects section header rows between table segments.
        """
        if len(self.table_bboxes) < 2:
            return False

        for i, t1 in enumerate(self.table_bboxes):
            t1_x0, t1_y0, t1_x1, t1_y1 = t1
            for t2 in self.table_bboxes[i + 1:]:
                t2_x0, t2_y0, t2_x1, t2_y1 = t2
                # Determine which table is above and which is below
                if t1_y1 < t2_y0:
                    above_y1, below_y0 = t1_y1, t2_y0
                    above_x0, above_x1 = t1_x0, t1_x1
                    below_x0, below_x1 = t2_x0, t2_x1
                elif t2_y1 < t1_y0:
                    above_y1, below_y0 = t2_y1, t1_y0
                    above_x0, above_x1 = t2_x0, t2_x1
                    below_x0, below_x1 = t1_x0, t1_x1
                else:
                    continue  # Tables overlap vertically

                gap = below_y0 - above_y1
                if gap < 0 or gap > max_gap:
                    continue

                # Check bbox is in the gap zone
                if y0 >= above_y1 - 3 and y1 <= below_y0 + 3:
                    # Check horizontal overlap with both tables
                    h_overlap_above = min(x1, above_x1) - max(x0, above_x0)
                    h_overlap_below = min(x1, below_x1) - max(x0, below_x0)
                    if h_overlap_above > 0 and h_overlap_below > 0:
                        return True

        return False


# ---------------------------------------------------------------------------
# Phase 2 output: Field candidates from detectors
# ---------------------------------------------------------------------------

@dataclass
class FieldCandidate:
    """
    A proposed field from a single detector.
    Created during Phase 2, never mutated after creation.
    Carries its provenance (source, confidence) for the resolver.
    """
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    field_type: FieldType = FieldType.TEXT
    source: str = ""  # e.g. "horizontal_line_table", "grid_fallback", "signature"
    confidence: float = 1.0  # 0.0-1.0, used for tie-breaking

    # Naming hints
    name_hint: Optional[str] = None
    label: Optional[str] = None
    label_position: Optional[str] = None  # "above", "left", "below"

    # Format hints
    format_hint: Optional[str] = None   # "date", "currency", "phone", etc.
    format_options: Optional[Dict] = None
    max_length: Optional[int] = None
    default_value: Optional[str] = None

    # Checkbox-specific
    checkbox_width: Optional[float] = None
    checkbox_height: Optional[float] = None

    # Comb / box entry
    is_comb: bool = False
    comb_count: Optional[int] = None

    # Image upload
    is_image_box: bool = False

    # Source line bounds (for form_line / inline_underline fields)
    source_line_x0: Optional[float] = None
    source_line_x1: Optional[float] = None

    # Radio group hint
    is_radio_child: bool = False
    radio_group_hint: Optional[str] = None

    # Table context
    row_label: Optional[str] = None
    column_header: Optional[str] = None
    table_index: Optional[int] = None

    @property
    def width(self):
        return self.x1 - self.x0

    @property
    def height(self):
        return self.y1 - self.y0

    @property
    def area(self):
        return max(0, self.width) * max(0, self.height)

    @property
    def center(self):
        return ((self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2)

    def overlaps(self, other, threshold=0.4):
        """Check if this candidate has significant overlap with another."""
        ix0 = max(self.x0, other.x0)
        iy0 = max(self.y0, other.y0)
        ix1 = min(self.x1, other.x1)
        iy1 = min(self.y1, other.y1)
        if ix1 <= ix0 or iy1 <= iy0:
            return False
        inter = (ix1 - ix0) * (iy1 - iy0)
        min_area = min(self.area, other.area)
        if min_area <= 0:
            return False
        return inter / min_area > threshold


# ---------------------------------------------------------------------------
# Phase 3 output: Resolved (committed) fields
# ---------------------------------------------------------------------------

@dataclass
class ResolvedField:
    """
    A field that has been committed by the resolver. Ready for widget creation.
    Only ResolvedFields become PDF form widgets.
    """
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    field_type: FieldType
    source: str
    name: str  # Final unique name

    # Display
    label: Optional[str] = None
    tooltip: Optional[str] = None

    # Formatting
    format_type: Optional[str] = None
    format_options: Optional[Dict] = None
    max_length: Optional[int] = None
    default_value: Optional[str] = None
    alignment: Optional[str] = None  # "left", "center", "right"

    # Comb
    is_comb: bool = False
    comb_count: Optional[int] = None

    # Image upload
    is_image_box: bool = False

    # Source line bounds (for clamping during snapping)
    source_line_x0: Optional[float] = None
    source_line_x1: Optional[float] = None

    # Checkbox
    checkbox_width: Optional[float] = None
    checkbox_height: Optional[float] = None

    # Radio group
    is_radio_child: bool = False
    radio_group_name: Optional[str] = None
    radio_value: Optional[str] = None

    # Debugging: what candidates competed for this slot
    winning_source: Optional[str] = None
    defeated_sources: List[str] = field(default_factory=list)

    @property
    def width(self):
        return self.x1 - self.x0

    @property
    def height(self):
        return self.y1 - self.y0


# ---------------------------------------------------------------------------
# Source ranking table — single source of truth for conflict resolution
# ---------------------------------------------------------------------------

SOURCE_RANKS = {
    'user_injected': 1000,
    'large_image_rect': 600,
    'box_entry': 550,
    'comb_box_grid': 550,
    'label_entry_below': 540,
    'horizontal_line_table_utility': 535,
    'split_cell_multi_row': 530,
    'horizontal_line_table_subrow': 530,
    'split_line_gap': 525,
    'special_structural_header': 475,
    'form_line_segment': 370,
    'label_below_split': 370,
    'inline_fill_underline': 300,
    'label_entry_cell': 480,
    'strict_grid': 480,
    'grid_fallback': 480,
    'grid_fallback_table_cell': 480,
    'grid_region_fallback': 480,
    'table_col_fallback': 480,
    'table_col': 550,
    'table_col_speculative': 150,
    'horizontal_line_table': 480,
    'page_consist_below': 475,
    'embedded_underscore': 460,
    'embedded_underscore_split': 460,
    'signature': 520,
    'date': 520,
    'character': 410,
    'discovery_discovery_gap': 280,
    'discovery_discovery_line': 280,
    'form_line_presplit': 365,
    'colored_split_entry': 380,
    'special_label': 350,
    'inline_label': 350,
    'audit_gap_fill': 340,
    'grid_reconstruction': 330,
    'visual_in_cell_fallback': 320,
    'visual_in_cell': 310,
    'structural_box': 250,
    'table': 180,
    'table_data_row': 180,
    'sub_table_data_row': 180,
    'vector': 550,
    'curve': 550,
    'line_box': 540,
    'underscore': 100,
    'general_underscore': 100,
    'label_below_underscore': 100,
    'split_field': 100,
    'colon_separated': 90,
    'form_line': 80,
    'empty_box_lines': 300,
    'empty_box': 300,
    'shaded_field': 290,
    'discovery_question_gap': 275,
    'grid_gap_fill': 270,
    'discovery_aligned_gap': 260,
    'cross_page_propagated': 200,
    'tiling_rect_table': 490,
}


def get_source_rank(source: str) -> int:
    """Get the priority rank for a field source. Higher = more priority."""
    return SOURCE_RANKS.get(source, 200)
