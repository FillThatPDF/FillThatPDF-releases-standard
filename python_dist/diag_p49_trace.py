#!/usr/bin/env python3
"""Trace P49 empty_box fields through post-processor stages."""
import sys, copy
sys.path.insert(0, '.')

from fillable_engine.page_analyzer import PageAnalyzer
from fillable_engine.resolver import UnifiedResolver
from fillable_engine.models import FieldType

PDF = "/Users/36981/Desktop/PDFTest/enhanced_quarantine/56380_DTE_2026_PartnerGuidelines_v18_REPAIRED_ENHANCED.pdf"

# Phase 1: Analyze
analyzer = PageAnalyzer({})
pages = analyzer.analyze(PDF, None)

# Phase 2: Detect
from smart_fillable_v24 import ALL_DETECTORS
all_candidates = []
for det_cls in ALL_DETECTORS:
    det = det_cls({})
    candidates = det.detect(pages)
    all_candidates.extend(candidates)
print(f"Phase 2: {len(all_candidates)} candidates")

# Phase 3: Resolve
resolver = UnifiedResolver({})
resolved = resolver.resolve(all_candidates, pages)
print(f"Phase 3: {len(resolved)} fields")

# Determine P49 — check which page_num has "Results" table with lots of fields
# Based on pipeline output: Page 49 had 55 fields
# User's P49 could be page_num 48 (0-indexed) or 49 (if 1-indexed)
for test_pn in [48, 49, 50]:
    count = sum(1 for f in resolved if f.page == test_pn)
    eb = sum(1 for f in resolved if f.page == test_pn and f.source == 'empty_box')
    print(f"  page_num {test_pn}: {count} total, {eb} empty_box")

# Use page_num 49 if it has the most fields, else 48
counts = {pn: sum(1 for f in resolved if f.page == pn) for pn in [48, 49, 50]}
TARGET = max(counts, key=counts.get)
print(f"\nUsing page_num = {TARGET} for P49")

page = [p for p in pages if p.page_num == TARGET][0]

# Show H-lines in the problematic Y range
print(f"\n=== H-lines on P49 (Y 350-500) ===")
for hl in sorted(page.h_lines, key=lambda h: h.get('y', h.get('top', 0))):
    ly = hl.get('y', (hl.get('top', 0) + hl.get('bottom', 0)) / 2)
    if 350 <= ly <= 500:
        print(f"  y={ly:.1f} x=({hl.get('x0',0):.1f}-{hl.get('x1',0):.1f})")

# Show empty_box fields BEFORE post-processing
eb_fields = [f for f in resolved if f.page == TARGET and f.source == 'empty_box']
print(f"\n=== empty_box fields BEFORE post-processing ({len(eb_fields)}) ===")
for f in sorted(eb_fields, key=lambda f: (f.y0, f.x0)):
    print(f"  ({f.x0:.1f},{f.y0:.1f})-({f.x1:.1f},{f.y1:.1f}) "
          f"h={f.y1-f.y0:.1f} w={f.x1-f.x0:.1f}")

# Now run post-processors step by step
fields = copy.deepcopy(resolved)

from fillable_engine.postprocess import (
    LabelEnricher, RadioGroupIdentifier, LineSnapper,
    GridGapFill, TextColumnFilter, HeightStandardizer, LabelTrimmer,
)

fields = LabelEnricher().process(fields, pages)
fields = RadioGroupIdentifier().process(fields, pages)

# BEFORE LineSnapper — snapshot empty_box
pre_snap = {id(f): (f.y0, f.y1) for f in fields if f.page == TARGET and f.source == 'empty_box'}

fields = LineSnapper().process(fields, pages)

# AFTER LineSnapper
print(f"\n=== empty_box fields AFTER LineSnapper ===")
eb_after_snap = [f for f in fields if f.page == TARGET and f.source == 'empty_box']
for f in sorted(eb_after_snap, key=lambda f: (f.y0, f.x0)):
    print(f"  ({f.x0:.1f},{f.y0:.1f})-({f.x1:.1f},{f.y1:.1f}) "
          f"h={f.y1-f.y0:.1f} w={f.x1-f.x0:.1f}")

fields = GridGapFill().process(fields, pages)
fields = TextColumnFilter().process(fields, pages)

# BEFORE HeightStandardizer — check Y range 390-460
print(f"\n=== All fields in Y 390-460 BEFORE HeightStandardizer ===")
in_range = [f for f in fields if f.page == TARGET and 390 <= f.y0 <= 460]
for f in sorted(in_range, key=lambda f: (f.y0, f.x0)):
    print(f"  ({f.x0:.1f},{f.y0:.1f})-({f.x1:.1f},{f.y1:.1f}) "
          f"h={f.y1-f.y0:.1f} src={f.source}")

fields = HeightStandardizer().process(fields, pages)

print(f"\n=== All fields in Y 390-460 AFTER HeightStandardizer ===")
in_range = [f for f in fields if f.page == TARGET and 390 <= f.y0 <= 460]
for f in sorted(in_range, key=lambda f: (f.y0, f.x0)):
    print(f"  ({f.x0:.1f},{f.y0:.1f})-({f.x1:.1f},{f.y1:.1f}) "
          f"h={f.y1-f.y0:.1f} src={f.source}")

fields = LabelTrimmer().process(fields, pages)

print(f"\n=== All fields in Y 390-460 FINAL ===")
in_range = [f for f in fields if f.page == TARGET and 390 <= f.y0 <= 460]
for f in sorted(in_range, key=lambda f: (f.y0, f.x0)):
    print(f"  ({f.x0:.1f},{f.y0:.1f})-({f.x1:.1f},{f.y1:.1f}) "
          f"h={f.y1-f.y0:.1f} src={f.source}")

print(f"\nTotal fields on P49: {sum(1 for f in fields if f.page == TARGET)}")
