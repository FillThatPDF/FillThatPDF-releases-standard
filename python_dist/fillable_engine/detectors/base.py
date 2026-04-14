"""
Base detector class — all field detectors inherit from this.

Key contract:
- detect() receives read-only PageModel objects
- detect() returns List[FieldCandidate] without modifying ANY shared state
- Detectors are independent — they NEVER see other detectors' candidates
"""

from abc import ABC, abstractmethod
from typing import List, Dict

from ..models import PageModel, FieldCandidate


class BaseDetector(ABC):
    """Abstract base for all field detectors."""

    def __init__(self, settings: Dict):
        self.settings = settings
        self.table_cell_padding = float(settings.get('table_cell_padding', 0))

    @abstractmethod
    def detect(self, pages: List[PageModel]) -> List[FieldCandidate]:
        """
        Detect field candidates across all pages.

        Args:
            pages: List of PageModel objects (read-only).

        Returns:
            List of FieldCandidate objects.

        Contract:
            - MUST NOT modify pages or any shared state.
            - MUST NOT check for overlap with other detectors' candidates.
            - MAY emit overlapping candidates from the same source if ambiguous.
            - SHOULD set name_hint, label, source, confidence on each candidate.
        """
        raise NotImplementedError
