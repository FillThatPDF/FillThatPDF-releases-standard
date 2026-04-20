"""
Diagnostic: Trace P44 even-group fields through the entire pipeline.
Shows what exists after resolver, then after each post-processor.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from fillable_engine.page_analyzer import PageAnalyzer
from fillable_engine.resolver import UnifiedResolver
from fillable_engine.detectors import ALL_DETECTORS
from fillable_engine.postprocess import ALL_POSTPROCESSORS

PDF = "/Users/36981/Desktop/PDFTest/enhanced_quarantine/56380_DTE_2026_PartnerGuidelines_v18_REPAIRED_ENHANCED.pdf"

# P44 = page_num 43
TARGET_PAGE = 43
# Even groups: Y ranges (approximate y0 values)
EVEN_Y_RANGES = [
    (140, 170),  # Group 2
    (284, 314),  # Group 4
    (428, 458),  # Group 6
    (572, 602),  # Group 8
]
# Also check odd groups for comparison
ODD_Y_RANGES = [
    (68, 98),    # Group 1
    (212, 242),  # Group 3
    (356, 386),  # Group 5
    (500, 530),  # Group 7
    (671, 701),  # Group 10
]
# Column 1 X range: 171-293
COL1_X = (165, 298)

settings = {}

# Phase 1: Analyze
print("Phase 1: Analyzing...")
analyzer = PageAnalyzer(settings)
pages = analyzer.analyze(PDF, [TARGET_PAGE])
print(f"   Got {len(pages)} pages")

# Phase 2: Detect
print("\nPhase 2: Detecting...")
all_candidates = []
for det_cls in ALL_DETECTORS:
    det = det_cls(settings)
    candidates = det.detect(pages)
    all_candidates.extend(candidates)
    # Show candidates in our target area
    p44_cands = [c for c in candidates if c.page == TARGET_PAGE]
    col1_cands = [c for c in p44_cands
                  if c.x0 < COL1_X[1] and (c.x0 + c.x1)/2 > COL1_X[0]
                  and any(yr[0] <= c.y0 <= yr[1] for yr in EVEN_Y_RANGES + ODD_Y_RANGES)]
    if col1_cands:
        print(f"   {det_cls.__name__}: {len(col1_cands)} col1 candidates in target rows:")
        for c in col1_cands:
            print(f"      ({c.x0:.0f},{c.y0:.0f})-({c.x1:.0f},{c.y1:.0f}) "
                  f"src={c.source} conf={c.confidence:.1f} type={c.field_type}")

# Also show ALL candidates that touch col1 on P44
print("\n--- ALL P44 candidates touching column 1 (x < 293) ---")
p44_col1_all = [c for c in all_candidates
                if c.page == TARGET_PAGE and c.x0 < 293 and c.x1 > 165]
for c in sorted(p44_col1_all, key=lambda c: (c.y0, c.x0)):
    print(f"   ({c.x0:.0f},{c.y0:.0f})-({c.x1:.0f},{c.y1:.0f}) "
          f"src={c.source} conf={c.confidence:.1f} type={c.field_type}")

# Phase 3: Resolve
print("\nPhase 3: Resolving...")
resolver = UnifiedResolver(settings)
resolved = resolver.resolve(all_candidates, pages)
print(f"   {len(resolved)} fields committed total")

def show_col1_fields(fields, label):
    """Show fields in column 1 area on P44."""
    col1 = [f for f in fields if f.page == TARGET_PAGE
            and f.x0 < COL1_X[1] and (f.x0 + f.x1)/2 > COL1_X[0]]
    print(f"\n--- {label}: {len(col1)} col1 fields on P44 ---")
    for f in sorted(col1, key=lambda f: f.y0):
        in_even = any(yr[0] <= f.y0 <= yr[1] for yr in EVEN_Y_RANGES)
        in_odd = any(yr[0] <= f.y0 <= yr[1] for yr in ODD_Y_RANGES)
        tag = " [EVEN]" if in_even else (" [ODD]" if in_odd else "")
        print(f"   ({f.x0:.0f},{f.y0:.0f})-({f.x1:.0f},{f.y1:.0f}) "
              f"src={f.source} type={f.field_type} name={f.name}{tag}")

# Also show ALL wide fields spanning both columns
def show_wide_fields(fields, label):
    """Show fields wider than 200pt on P44."""
    wide = [f for f in fields if f.page == TARGET_PAGE
            and (f.x1 - f.x0) > 200 and f.x0 < 200]
    if wide:
        print(f"\n--- {label}: {len(wide)} WIDE fields (>200pt, starting <200) on P44 ---")
        for f in sorted(wide, key=lambda f: f.y0):
            print(f"   ({f.x0:.0f},{f.y0:.0f})-({f.x1:.0f},{f.y1:.0f}) "
                  f"src={f.source} w={f.x1-f.x0:.0f}")

show_col1_fields(resolved, "After Resolver")
show_wide_fields(resolved, "After Resolver")

# Phase 3.5: Post-process step by step
import copy
fields = list(resolved)
for pp_cls in ALL_POSTPROCESSORS:
    pp_name = pp_cls.__name__
    pp = pp_cls()
    before_count = len(fields)
    fields = pp.process(fields, pages)
    after_count = len(fields)
    show_col1_fields(fields, f"After {pp_name}")
    show_wide_fields(fields, f"After {pp_name}")
    if before_count != after_count:
        print(f"   ({pp_name}: {before_count} → {after_count}, delta={after_count-before_count})")

print(f"\n\nFINAL: {len(fields)} total fields")
