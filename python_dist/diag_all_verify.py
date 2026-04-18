#!/usr/bin/env python3
"""Quick verification of all 4 fixes: P31, P41, P44, P49."""
import sys
sys.path.insert(0, '.')

from fillable_engine.page_analyzer import PageAnalyzer
from fillable_engine.resolver import UnifiedResolver
from fillable_engine.models import FieldType

PDF = "/Users/36981/Desktop/PDFTest/enhanced_quarantine/56380_DTE_2026_PartnerGuidelines_v18_REPAIRED_ENHANCED.pdf"

# Phase 1+2+3
analyzer = PageAnalyzer({})
pages = analyzer.analyze(PDF, None)
from smart_fillable_v24 import ALL_DETECTORS
all_candidates = []
for det_cls in ALL_DETECTORS:
    det = det_cls({})
    all_candidates.extend(det.detect(pages))
resolver = UnifiedResolver({})
resolved = resolver.resolve(all_candidates, pages)

# Run post-processors
from fillable_engine.postprocess import ALL_POSTPROCESSORS
fields = resolved
for pp_cls in ALL_POSTPROCESSORS:
    fields = pp_cls().process(fields, pages)

print(f"Total fields: {len(fields)}")

# P41 (page_num 40): Check signature field
print("\n=== P41: Signature field ===")
p41_sig = [f for f in fields if f.page == 40 and f.field_type == FieldType.SIGNATURE]
p41_all = [f for f in fields if f.page == 40]
print(f"  P41 total: {len(p41_all)} fields")
for f in p41_sig:
    print(f"  SIGNATURE: ({f.x0:.1f},{f.y0:.1f})-({f.x1:.1f},{f.y1:.1f}) src={f.source}")
if not p41_sig:
    print("  WARNING: No signature field found!")

# P44 (page_num 43): Check $: fields (label_entry_cell)
print("\n=== P44: $: fields ===")
p44 = [f for f in fields if f.page == 43]
p44_lec = [f for f in p44 if f.source == 'label_entry_cell']
print(f"  P44 total: {len(p44)} fields")
print(f"  label_entry_cell: {len(p44_lec)} fields")
for f in p44_lec:
    print(f"  ({f.x0:.1f},{f.y0:.1f})-({f.x1:.1f},{f.y1:.1f}) h={f.y1-f.y0:.1f}")

# P49 (page_num 48): Check field heights and H-line crossings
print("\n=== P49: Field containment check ===")
p49_page = [p for p in pages if p.page_num == 48][0]
p49_fields = [f for f in fields if f.page == 48]
print(f"  P49 total: {len(p49_fields)} fields")

# Check for fields that cross H-lines
hline_ys = set()
for hl in p49_page.h_lines:
    ly = hl.get('y', (hl.get('top', 0) + hl.get('bottom', 0)) / 2)
    hline_ys.add(round(ly, 1))

crossing = []
for f in p49_fields:
    if f.field_type == FieldType.CHECKBOX:
        continue
    for hy in hline_ys:
        # Does this field cross the H-line? (H-line between y0 and y1)
        if f.y0 + 2 < hy < f.y1 - 2:
            crossing.append((f, hy))
            break

if crossing:
    print(f"  WARNING: {len(crossing)} fields cross H-lines:")
    for f, hy in crossing[:10]:
        print(f"    ({f.x0:.1f},{f.y0:.1f})-({f.x1:.1f},{f.y1:.1f}) "
              f"h={f.y1-f.y0:.1f} crosses H-line at y={hy} src={f.source}")
else:
    print("  ✓ No fields cross H-lines!")

# P31 (page_num 30): Show field info
print("\n=== P31: Summary ===")
p31 = [f for f in fields if f.page == 30]
print(f"  P31 total: {len(p31)} fields")
