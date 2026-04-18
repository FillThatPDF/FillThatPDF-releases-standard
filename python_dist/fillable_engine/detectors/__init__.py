"""Field candidate detectors — each returns List[FieldCandidate] without shared state."""

from .base import BaseDetector
from .horizontal_line_tables import HorizontalLineTableDetector
from .table_cells import TableCellDetector
from .form_lines import FormLineDetector
from .signatures import SignatureDetector
from .checkboxes import CheckboxDetector
from .empty_boxes import EmptyBoxDetector
from .underscore_fields import UnderscoreFieldDetector
from .special_labels import SpecialLabelDetector
from .inline_labels import InlineLabelDetector
from .box_entry import BoxEntryDetector
from .image_rectangles import ImageRectangleDetector
from .inline_underlines import InlineUnderlineDetector
from .shaded_fields import ShadedFieldDetector
from .discovery import DiscoveryDetector

# All detectors in recommended execution order
ALL_DETECTORS = [
    CheckboxDetector,            # PASS 1: All checkbox types (vector, character, curve, line_box)
    HorizontalLineTableDetector, # PASS 1.5: HLT (critical for DTE)
    TableCellDetector,           # PASS 2: Grid/table cells (critical for ConEd)
    SignatureDetector,           # PASS 3: Signature/date fields
    ImageRectangleDetector,      # PASS 3.2: Large image upload rectangles (rank 600)
    BoxEntryDetector,            # PASS 3.5: Comb/box entry fields (SSN, phone)
    FormLineDetector,            # PASS 4B: Form line underlines
    EmptyBoxDetector,            # PASS 5: Large empty box areas
    ShadedFieldDetector,         # PASS 5.5: Light-gray shaded fill areas
    SpecialLabelDetector,        # PASS 6: "Label:" with nearby underline
    InlineLabelDetector,         # PASS 7: City/State/ZIP inline labels
    UnderscoreFieldDetector,     # PASS 8: Text underscore patterns
    InlineUnderlineDetector,     # PASS 9: Vector underlines after text
    DiscoveryDetector,           # PASS 10: Final discovery for missed labels
]

# Backwards compatibility
CORE_DETECTORS = ALL_DETECTORS[:5]

__all__ = [
    'BaseDetector',
    'HorizontalLineTableDetector',
    'TableCellDetector',
    'FormLineDetector',
    'SignatureDetector',
    'CheckboxDetector',
    'EmptyBoxDetector',
    'UnderscoreFieldDetector',
    'SpecialLabelDetector',
    'InlineLabelDetector',
    'BoxEntryDetector',
    'ImageRectangleDetector',
    'InlineUnderlineDetector',
    'ShadedFieldDetector',
    'DiscoveryDetector',
    'ALL_DETECTORS',
    'CORE_DETECTORS',
]
