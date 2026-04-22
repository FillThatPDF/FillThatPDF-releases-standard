#!/usr/bin/env python3
"""
Diagnostic script: trace fields on page 19 (0-indexed page 18) of the DTE PDF
through the full pipeline, intercepting at key points.
"""
import sys
import os
import copy
import logging

logging.basicConfig(filename='/tmp/diag_page19.log', level=logging.DEBUG, filemode='w')
logging.getLogger('pdfminer').setLevel(logging.WARNING)
logging.getLogger('pdfplumber').setLevel(logging.WARNING)

# Add this directory to path so fillable_engine can be imported
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fillable_engine.models import PageModel, FieldCandidate, ResolvedField, FieldType
from fillable_engine.page_analyzer import PageAnalyzer
from fillable_engine.resolver import UnifiedResolver
from fillable_engine.detectors import ALL_DETECTORS
from fillable_engine.postprocess import ALL_POSTPROCESSORS

PDF_PATH = "/Users/36981/Desktop/PDFTest/PDFs to test/Static PDFs/1/56380_DTE_2026_PartnerGuidelines_v18.pdf"
TARGET_PAGE = 18  # 0-indexed = visual page 19

# Y range of interest (focus area, but we print ALL fields on the page)
Y_FOCUS_MIN = 400
Y_FOCUS_MAX = 600


def fmt_field(f, prefix=""):
    """Format a ResolvedField or FieldCandidate for printing."""
    if isinstance(f, ResolvedField):
        name_str = f"name={f.name!r}, "
    else:
        name_str = ""
    src_line = ""
    if hasattr(f, 'source_line_x0') and f.source_line_x0 is not None:
        src_line = f", srcLine=({f.source_line_x0:.1f}, {f.source_line_x1:.1f})"
    label_str = ""
    if hasattr(f, 'label') and f.label:
        label_str = f", label={f.label!r}"
    in_focus = "  <<<FOCUS" if Y_FOCUS_MIN <= f.y0 <= Y_FOCUS_MAX or Y_FOCUS_MIN <= f.y1 <= Y_FOCUS_MAX else ""
    return (f"{prefix}{name_str}source={f.source!r}, "
            f"x0={f.x0:.1f}, y0={f.y0:.1f}, x1={f.x1:.1f}, y1={f.y1:.1f}, "
            f"w={f.x1-f.x0:.1f}, h={f.y1-f.y0:.1f}"
            f"{src_line}{label_str}{in_focus}")


def snapshot_fields(fields):
    """Take a snapshot of field positions (for comparison)."""
    snap = {}
    for f in fields:
        snap[id(f)] = (f.x0, f.y0, f.x1, f.y1)
    return snap


def print_changes(fields, before_snap, after_snap, stage_name):
    """Print which fields changed between two snapshots."""
    page_fields = [f for f in fields if f.page == TARGET_PAGE]
    changed = []
    for f in page_fields:
        fid = id(f)
        if fid in before_snap and fid in after_snap:
            bx0, by0, bx1, by1 = before_snap[fid]
            ax0, ay0, ax1, ay1 = after_snap[fid]
            if abs(bx0 - ax0) > 0.01 or abs(by0 - ay0) > 0.01 or abs(bx1 - ax1) > 0.01 or abs(by1 - ay1) > 0.01:
                changed.append((f, bx0, by0, bx1, by1, ax0, ay0, ax1, ay1))
    if not changed:
        print(f"  (no position changes on page {TARGET_PAGE})")
    else:
        for f, bx0, by0, bx1, by1, ax0, ay0, ax1, ay1 in changed:
            in_focus = "  <<<FOCUS" if (Y_FOCUS_MIN <= by0 <= Y_FOCUS_MAX or
                                         Y_FOCUS_MIN <= ay0 <= Y_FOCUS_MAX or
                                         Y_FOCUS_MIN <= by1 <= Y_FOCUS_MAX or
                                         Y_FOCUS_MIN <= ay1 <= Y_FOCUS_MAX) else ""
            name_str = f"name={f.name!r}, " if hasattr(f, 'name') else ""
            print(f"  {name_str}source={f.source!r}:")
            print(f"    BEFORE: x0={bx0:.1f}, y0={by0:.1f}, x1={bx1:.1f}, y1={by1:.1f}, w={bx1-bx0:.1f}, h={by1-by0:.1f}")
            print(f"    AFTER:  x0={ax0:.1f}, y0={ay0:.1f}, x1={ax1:.1f}, y1={ay1:.1f}, w={ax1-ax0:.1f}, h={ay1-ay0:.1f}")
            dx0 = ax0 - bx0
            dy0 = ay0 - by0
            dx1 = ax1 - bx1
            dy1 = ay1 - by1
            print(f"    DELTA:  dx0={dx0:+.1f}, dy0={dy0:+.1f}, dx1={dx1:+.1f}, dy1={dy1:+.1f}{in_focus}")


def main():
    print(f"{'='*80}")
    print(f"DIAGNOSTIC: Page 19 (0-indexed {TARGET_PAGE}) field tracing")
    print(f"PDF: {PDF_PATH}")
    print(f"Focus Y range: {Y_FOCUS_MIN}-{Y_FOCUS_MAX}")
    print(f"{'='*80}\n")

    settings = {}

    # =========================================================================
    # PHASE 1: ANALYZE
    # =========================================================================
    print("="*60)
    print("PHASE 1: Page Analysis")
    print("="*60)
    analyzer = PageAnalyzer(settings)
    pages = analyzer.analyze(PDF_PATH, [TARGET_PAGE])

    page = None
    for p in pages:
        if p.page_num == TARGET_PAGE:
            page = p
            break

    if page is None:
        print(f"ERROR: Page {TARGET_PAGE} not found!")
        sys.exit(1)

    print(f"\nPage {TARGET_PAGE}: {page.width:.1f} x {page.height:.1f}, rotation={page.rotation}")
    print(f"  is_text_only={page.is_text_only}, is_tally_sheet={page.is_tally_sheet}, is_info_page={page.is_info_page}")
    print(f"  footer_y={page.footer_y}")
    print(f"  words count: {len(page.words)}")
    print(f"  lines count: {len(page.lines)}")
    print(f"  rects count: {len(page.rects)}")
    print(f"  h_lines count: {len(page.h_lines)}")
    print(f"  v_lines count: {len(page.v_lines)}")
    print(f"  visual_grid_cells count: {len(page.visual_grid_cells)}")
    print(f"  header_bars count: {len(page.header_bars)}")
    print(f"  table_bboxes count: {len(page.table_bboxes)}")

    # (a) Print ALL h_lines on this page
    print(f"\n--- (a) All h_lines on page {TARGET_PAGE} ({len(page.h_lines)} total) ---")
    for i, hl in enumerate(sorted(page.h_lines, key=lambda h: (h.get('y', 0), h.get('x0', 0)))):
        y_val = hl.get('y', 0)
        in_focus = "  <<<FOCUS" if Y_FOCUS_MIN <= y_val <= Y_FOCUS_MAX else ""
        print(f"  h_line[{i:3d}]: x0={hl['x0']:.1f}, x1={hl['x1']:.1f}, "
              f"top={hl['top']:.1f}, bottom={hl['bottom']:.1f}, y={y_val:.1f}, "
              f"width={hl['width']:.1f}{in_focus}")

    # Also print words in the focus Y range for context
    print(f"\n--- Words in Y range {Y_FOCUS_MIN}-{Y_FOCUS_MAX} ---")
    focus_words = [w for w in page.words
                   if Y_FOCUS_MIN <= float(w.get('top', 0)) <= Y_FOCUS_MAX
                   or Y_FOCUS_MIN <= float(w.get('bottom', 0)) <= Y_FOCUS_MAX]
    focus_words.sort(key=lambda w: (float(w.get('top', 0)), float(w.get('x0', 0))))
    for w in focus_words:
        print(f"  word: '{w['text']}' x0={float(w['x0']):.1f}, top={float(w['top']):.1f}, "
              f"x1={float(w['x1']):.1f}, bottom={float(w['bottom']):.1f}")

    # =========================================================================
    # PHASE 2: DETECT
    # =========================================================================
    print(f"\n{'='*60}")
    print("PHASE 2: Detection")
    print("="*60)
    all_candidates = []
    for det_cls in ALL_DETECTORS:
        det = det_cls(settings)
        det_name = det_cls.__name__
        try:
            candidates = det.detect(pages)
            all_candidates.extend(candidates)
            page_cands = [c for c in candidates if c.page == TARGET_PAGE]
            print(f"  {det_name}: {len(candidates)} total, {len(page_cands)} on page {TARGET_PAGE}")
        except Exception as e:
            print(f"  {det_name}: ERROR - {e}")
            import traceback
            traceback.print_exc()

    # (b) Print all FieldCandidates on page 18
    page_candidates = [c for c in all_candidates if c.page == TARGET_PAGE]
    page_candidates.sort(key=lambda c: (c.y0, c.x0))
    print(f"\n--- (b) All FieldCandidates on page {TARGET_PAGE} ({len(page_candidates)} total) ---")
    for i, c in enumerate(page_candidates):
        print(f"  [{i:3d}] {fmt_field(c)}")

    # =========================================================================
    # PHASE 3: RESOLVE
    # =========================================================================
    print(f"\n{'='*60}")
    print("PHASE 3: Resolution")
    print("="*60)
    resolver = UnifiedResolver(settings)
    resolved = resolver.resolve(all_candidates, pages)

    # (c) Print all ResolvedFields on page 18
    page_resolved = [f for f in resolved if f.page == TARGET_PAGE]
    page_resolved.sort(key=lambda f: (f.y0, f.x0))
    print(f"\n--- (c) All ResolvedFields on page {TARGET_PAGE} ({len(page_resolved)} total) ---")
    for i, f in enumerate(page_resolved):
        print(f"  [{i:3d}] {fmt_field(f)}")

    # =========================================================================
    # PHASE 3.5: POST-PROCESSORS (run one at a time, snapshot between)
    # =========================================================================
    print(f"\n{'='*60}")
    print("PHASE 3.5: Post-processors (step-by-step)")
    print("="*60)

    import inspect

    for pp_cls in ALL_POSTPROCESSORS:
        pp_name = pp_cls.__name__

        # Take before snapshot
        before_snap = snapshot_fields(resolved)
        count_before = len([f for f in resolved if f.page == TARGET_PAGE])

        # Instantiate
        try:
            sig = inspect.signature(pp_cls.__init__)
            params = list(sig.parameters.keys())
            if 'settings' in params:
                pp = pp_cls(settings)
            else:
                pp = pp_cls()
        except (ValueError, TypeError):
            pp = pp_cls()

        # Run
        try:
            resolved = pp.process(resolved, pages)
        except Exception as e:
            print(f"\n  {pp_name}: ERROR - {e}")
            import traceback
            traceback.print_exc()
            continue

        # Take after snapshot
        after_snap = snapshot_fields(resolved)
        count_after = len([f for f in resolved if f.page == TARGET_PAGE])

        print(f"\n--- After {pp_name} (page {TARGET_PAGE}: {count_before} -> {count_after} fields) ---")

        # Check for added/removed fields
        before_ids = set(before_snap.keys())
        after_ids = set(after_snap.keys())
        removed_ids = before_ids - after_ids
        added_ids = after_ids - before_ids

        if removed_ids:
            print(f"  REMOVED {len(removed_ids)} field(s) (across all pages)")
        if added_ids:
            print(f"  ADDED {len(added_ids)} field(s) (across all pages)")

        # Show what specifically is LineSnapper or HeightStandardizer
        if pp_name in ('LineSnapper', 'HeightStandardizer'):
            print(f"  Position changes on page {TARGET_PAGE}:")
            print_changes(resolved, before_snap, after_snap, pp_name)

            # Full field dump after these key stages
            page_fields = [f for f in resolved if f.page == TARGET_PAGE]
            page_fields.sort(key=lambda f: (f.y0, f.x0))
            marker = "d" if pp_name == "LineSnapper" else "e"
            print(f"\n  --- ({marker}) Full field dump after {pp_name} ---")
            for i, f in enumerate(page_fields):
                print(f"    [{i:3d}] {fmt_field(f)}")
        else:
            # Just show changes for other postprocessors
            print_changes(resolved, before_snap, after_snap, pp_name)

    # =========================================================================
    # FINAL SUMMARY
    # =========================================================================
    print(f"\n{'='*60}")
    print("FINAL RESULT")
    print("="*60)
    final_page = [f for f in resolved if f.page == TARGET_PAGE]
    final_page.sort(key=lambda f: (f.y0, f.x0))
    print(f"Total fields on page {TARGET_PAGE}: {len(final_page)}")
    print(f"\nFields in focus range Y={Y_FOCUS_MIN}-{Y_FOCUS_MAX}:")
    for f in final_page:
        if Y_FOCUS_MIN <= f.y0 <= Y_FOCUS_MAX or Y_FOCUS_MIN <= f.y1 <= Y_FOCUS_MAX:
            print(f"  {fmt_field(f)}")


if __name__ == '__main__':
    main()
