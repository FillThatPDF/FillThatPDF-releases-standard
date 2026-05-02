#!/usr/bin/env python3
"""Verify all fixes: P17 Tax ID/Website, P31 underscore fields, P49 Results."""
import sys; sys.path.insert(0, '.')
from fillable_engine.page_analyzer import PageAnalyzer
from fillable_engine.resolver import UnifiedResolver
from fillable_engine.models import FieldType
from smart_fillable_v24 import ALL_DETECTORS
from fillable_engine.postprocess import ALL_POSTPROCESSORS

PDF = '/Users/36981/Desktop/PDFTest/enhanced_quarantine/56380_DTE_2026_PartnerGuidelines_v18_REPAIRED_ENHANCED.pdf'
analyzer = PageAnalyzer({})
pages = analyzer.analyze(PDF, None)
cands = []
for dc in ALL_DETECTORS:
    cands.extend(dc({}).detect(pages))
resolver = UnifiedResolver({})
resolved = resolver.resolve(cands, pages)
for pp_cls in ALL_POSTPROCESSORS:
    resolved = pp_cls().process(resolved, pages)

print(f"Total: {len(resolved)} fields\n")

# === P17 (page_num 16): Tax ID # and Website ===
print("=" * 60)
print("P17: Tax ID # and Website")
print("=" * 60)
p17 = [f for f in resolved if f.page == 16]
# Show all fields sorted by position
for f in sorted(p17, key=lambda f: (f.y0, f.x0)):
    print(f"  ({f.x0:.1f},{f.y0:.1f})-({f.x1:.1f},{f.y1:.1f}) h={f.y1-f.y0:.1f} "
          f"w={f.x1-f.x0:.1f} src={f.source} lbl=[{f.label[:40] if f.label else ''}]")

# Check for "Tax ID" fields
tax_id_fields = [f for f in p17 if f.label and 'Tax ID' in f.label]
website_fields = [f for f in p17 if f.label and 'Website' in f.label]
print(f"\n  Tax ID # fields: {len(tax_id_fields)}")
for f in tax_id_fields:
    print(f"    ({f.x0:.1f},{f.y0:.1f})-({f.x1:.1f},{f.y1:.1f}) h={f.y1-f.y0:.1f} src={f.source}")
print(f"  Website fields: {len(website_fields)}")
for f in website_fields:
    print(f"    ({f.x0:.1f},{f.y0:.1f})-({f.x1:.1f},{f.y1:.1f}) h={f.y1-f.y0:.1f} src={f.source}")

# Check for entry-area fields below Tax ID label (~y=386-400
tax_id_entry = [f for f in p17 if 385 <= f.y0 <= 400 and f.x0 > 350]
print(f"  Entry fields in Tax ID area (y=385-400): {len(tax_id_entry)}")
for f in tax_id_entry:
    print(f"    ({f.x0:.1f},{f.y0:.1f})-({f.x1:.1f},{f.y1:.1f}) h={f.y1-f.y0:.1f} src={f.source}")

# === P31 (page_num 30): Underscore fields ===
print("\n" + "=" * 60)
print("P31: Underscore fields (writer inset check)")
print("=" * 60)
p31 = [f for f in resolved if f.page == 30]
underscore_fields = [f for f in p31 if f.source and 'underscore' in f.source]
print(f"  Total: {len(p31)} fields, {len(underscore_fields)} underscore-based")
print("  Sample underscore fields (first 5):")
for f in sorted(underscore_fields, key=lambda f: (f.y0, f.x0))[:5]:
    print(f"    ({f.x0:.1f},{f.y0:.1f})-({f.x1:.1f},{f.y1:.1f}) h={f.y1-f.y0:.1f} src={f.source}")
print("  NOTE: Writer will apply 3.5pt bottom inset (vs 1.5pt for non-underscore)")

# === P49 (page_num 48): Results table + Equipment Info ===
print("\n" + "=" * 60)
print("P49: Results table + Equipment Info")
print("=" * 60)
p49 = [f for f in resolved if f.page == 48]
page49 = [p for p in pages if p.page_num == 48][0]
print(f"  Total: {len(p49)} fields")

# Grid-cell fields that were protected from LabelTrimmer
grid_fields = [f for f in p49 if f.source in {'empty_box', 'strict_grid', 'grid_fallback', 'gap_fill'}]
print(f"  Grid-cell fields preserved: {len(grid_fields)}")
for f in sorted(grid_fields, key=lambda f: (f.y0, f.x0)):
    print(f"    ({f.x0:.1f},{f.y0:.1f})-({f.x1:.1f},{f.y1:.1f}) h={f.y1-f.y0:.1f} "
          f"w={f.x1-f.x0:.1f} src={f.source}")

# Equipment Info underscore fields
equip_fields = [f for f in p49 if f.source and 'underscore' in f.source]
print(f"\n  Equipment Info underscore fields: {len(equip_fields)}")
for f in sorted(equip_fields, key=lambda f: (f.y0, f.x0))[:6]:
    print(f"    ({f.x0:.1f},{f.y0:.1f})-({f.x1:.1f},{f.y1:.1f}) h={f.y1-f.y0:.1f} src={f.source}")

# H-line crossing check
hline_ys = set()
for hl in page49.h_lines:
    ly = hl.get('y', (hl.get('top', 0) + hl.get('bottom', 0)) / 2)
    hline_ys.add(round(ly, 1))

crossing = []
for f in p49:
    if f.field_type == FieldType.CHECKBOX:
        continue
    for hy in hline_ys:
        if f.y0 + 2 < hy < f.y1 - 2:
            crossing.append((f, hy))
            break

if crossing:
    print(f"\n  WARNING: {len(crossing)} fields cross H-lines:")
    for f, hy in crossing[:5]:
        print(f"    ({f.x0:.1f},{f.y0:.1f})-({f.x1:.1f},{f.y1:.1f}) h={f.y1-f.y0:.1f} crosses y={hy} src={f.source}")
else:
    print(f"\n  ✓ No fields cross H-lines!")
