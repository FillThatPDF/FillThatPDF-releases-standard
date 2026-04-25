"""
FillThatPDF v24 — Modular Field Detection Engine

Architecture: Analyze → Detect Candidates → Resolve → Write
No field is ever "created then deleted" — candidates are scored and committed once.
"""

from .pipeline import Pipeline
from .models import PageModel, FieldCandidate, ResolvedField, FieldType

__version__ = "24.0.0"
__all__ = ['Pipeline', 'PageModel', 'FieldCandidate', 'ResolvedField', 'FieldType']
