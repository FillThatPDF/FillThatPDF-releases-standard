"""
Post-processing modules for resolved fields.

These run AFTER the resolver commits fields and BEFORE the writer creates
widgets.  Each post-processor implements a ``process(fields, pages)`` method
that mutates *fields* in place and returns the list.

Execution order matters:
  1. LabelEnricher         -- enrich checkbox labels so radio detection has
                              accurate data to work with.
  1b. LabelBelowSplitter   -- split wide form-line fields using distinct
                              label groups positioned below the field line.
  2. RadioGroupIdentifier  -- identify mutually-exclusive checkbox clusters
                              and mark them as radio groups.
  3. LineSnapper           -- snap field edges to nearby form lines FIRST so
                              that fields land on the correct row before any
                              height normalisation shifts their centres.
  4. AdjacentFieldMerger   -- merge same-source fields that are horizontally
                              adjacent with no V-line between them (fixes
                              over-segmented H-line strokes).
  5. GridGapFill           -- fill empty grid cells that detectors missed
                              (additive only — never removes existing fields).
  5b. LabelBelowSplitter   -- second pass: split wide grid_gap_fill fields
                              using labels below (and remove duplicate
                              inline_label fields).
  6. TextColumnFilter      -- remove/clip fields in informational table columns
                              (text-only columns with no fill indicators).
  7. HeightStandardizer    -- normalise text-field heights within each row
                              (mostly a no-op after snapping sets line-spacing
                              heights, but cleans up unsnapped fields).
  8. LabelTrimmer          -- trim or remove fields that overlap printed
                              label text so highlights never cover labels.
"""

from .radio_groups import RadioGroupIdentifier
from .label_enrichment import LabelEnricher
from .height_standardization import HeightStandardizer
from .line_snapping import LineSnapper
from .adjacent_merge import AdjacentFieldMerger
from .grid_gap_fill import GridGapFill
from .address_recovery import AddressRowRecovery
from .text_column_filter import TextColumnFilter
from .label_trimmer import LabelTrimmer
from .label_below_split import LabelBelowSplitter
from .cross_page_propagation import CrossPagePropagation

ALL_POSTPROCESSORS = [
    LabelEnricher,
    LabelBelowSplitter,
    RadioGroupIdentifier,
    LineSnapper,
    AdjacentFieldMerger,
    GridGapFill,
    LabelBelowSplitter,      # 2nd pass: split wide grid_gap_fill fields
    AddressRowRecovery,
    TextColumnFilter,
    CrossPagePropagation,     # Propagate patterns from dense→sparse pages
    HeightStandardizer,
    LabelTrimmer,
]

__all__ = [
    'RadioGroupIdentifier',
    'LabelEnricher',
    'LabelBelowSplitter',
    'HeightStandardizer',
    'LineSnapper',
    'AdjacentFieldMerger',
    'GridGapFill',
    'AddressRowRecovery',
    'TextColumnFilter',
    'CrossPagePropagation',
    'LabelTrimmer',
    'ALL_POSTPROCESSORS',
]
