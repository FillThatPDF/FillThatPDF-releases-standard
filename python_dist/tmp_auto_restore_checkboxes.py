#!/usr/bin/env python3
"""Auto-restore checkboxes on specified pages for a purged PDF.

Uses the existing SmartFillable engine to detect checkbox candidates but
forces checkbox creation on a given page range (overrides tally-sheet
heuristic for those pages). This is destructive: it writes a new PDF.
Run with --confirm to actually write the output.

Example:
  python3 tmp_auto_restore_checkboxes.py --pdf test_57643_v21_ROUND6_FINAL_purged.pdf --pages 17-19 --confirm
"""
import argparse
from pathlib import Path
import sys

# Add local python_dist to path if running from repo root
# so the bundled smart_fillable_v21 can be imported
sys.path.insert(0, str(Path(__file__).resolve().parent))

# SmartFillable class is exposed as UniversalPDFFillable in v21
from smart_fillable_v21 import UniversalPDFFillable as SmartFillable


def parse_pages(s: str):
    pages = set()
    parts = s.split(',')
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if '-' in p:
            a, b = p.split('-', 1)
            a, b = int(a), int(b)
            pages.update(range(a, b + 1))
        else:
            pages.add(int(p))
    # Convert to 0-based
    return sorted([p - 1 for p in pages if p >= 1])


def main():
    parser = argparse.ArgumentParser(description='Auto-restore checkboxes on selected pages')
    parser.add_argument('--pdf', required=True, help='Input purged PDF path')
    parser.add_argument('--pages', required=True, help='Page list/range (1-indexed), e.g. 17-19 or 17,18')
    parser.add_argument('--out', help='Output PDF path (default: <input>_restored_checkboxes.pdf)')
    parser.add_argument('--confirm', action='store_true', help='Apply changes and write output')
    args = parser.parse_args()

    input_pdf = Path(args.pdf)
    if not input_pdf.exists():
        print(f"ERROR: Input PDF not found: {input_pdf}")
        return

    target_pages = parse_pages(args.pages)
    if not target_pages:
        print("ERROR: No valid pages parsed from --pages")
        return

    out_pdf = Path(args.out) if args.out else input_pdf.parent / f"{input_pdf.stem}_restored_checkboxes.pdf"

    # Settings: enable checkboxes in table cells (we're intentionally restoring)
    settings = {'disable_checkboxes_in_table_cells': False, 'debug': False}

    sf = SmartFillable(str(input_pdf), output_pdf=str(out_pdf), settings=settings)

    print(f"Input: {input_pdf}")
    print(f"Target pages (0-based): {target_pages}")

    # Preload structural data and predictions
    # Open pdfplumber handle needed for visual detections
    import pdfplumber
    sf.pdf = pdfplumber.open(str(input_pdf))
    try:
        sf._preload_structural_data()
        sf._predetect_tally_sheets()
    finally:
        # Keep the handle open while we do detection; it will be closed later by _create_fillable_pdf
        pass

    print(f"Detected tally-sheet pages (before override): {sorted(sf.tally_sheet_pages)}")

    # Force these target pages to NOT be considered tally sheets so checkboxes are allowed
    for p in target_pages:
        if p in sf.tally_sheet_pages:
            sf.tally_sheet_pages.remove(p)
            print(f"  - Removed page {p+1} from tally_sheet_pages (forcing checkbox detection)")

    # Also ensure they're not marked as text-only pages
    for p in target_pages:
        if p in sf.text_only_pages:
            sf.text_only_pages.remove(p)
            print(f"  - Removed page {p+1} from text_only_pages")

    # Targeted approach: build visual grids on the specified pages and add checkboxes
    print("Scanning target pages for grid cells and adding checkboxes where visual boxes exist...")

    added_total = 0

    for p in target_pages:
        if p < 0 or p >= sf.page_count:
            print(f"  Skipping invalid page {p+1}")
            continue

        print(f"  Page {p+1}: Building visual grid...")
        cells = sf._build_visual_grid(p)
        print(f"    Found {len(cells)} grid cells")

        for c in cells:
            cx0, cy0, cx1, cy1 = c['x0'], c['y0'], c['x1'], c['y1']
            cell_w = cx1 - cx0
            cell_h = cy1 - cy0
            if not (6 <= cell_w <= 60 and 6 <= cell_h <= 60):
                continue

            # Skip if text already exists inside the cell (user-filled or example text)
            try:
                txt = (sf.pdf.pages[p].within_bbox((cx0+1, cy0+1, cx1-1, cy1-1)).extract_text() or '').strip()
            except Exception:
                txt = ''
            if txt:
                continue

            # Only add if there's a clear visual checkbox inside the cell
            if not sf._cell_has_visual_checkbox(p, cx0, cy0, cx1, cy1):
                continue

            size = min(cell_w, cell_h) * 0.85
            cb_x = cx0 + (cell_w - size) / 2
            cb_y = cy0 + (cell_h - size) / 2

            added = sf._add_checkbox(p, cb_x, cb_y, size, size, 'table_cell_forced')
            if added:
                added_total += 1

    print(f"Total checkboxes added: {added_total}")

    if added_total == 0:
        print("No checkboxes were added on the requested pages. Aborting.")
        return

    # Show a brief sample
    print("Sample newly-added checkboxes:")
    for cb in [cb for cb in sf.checkboxes if cb['page'] in target_pages][:10]:
        print(f"  P{cb['page']+1} at ({cb['x']:.1f},{cb['y']:.1f}) size={cb['width']:.1f}x{cb['height']:.1f} label='{cb.get('label','')}' source={cb.get('source')}")
    if not args.confirm:
        print('\nDry-run complete. No file written. Re-run with --confirm to write output PDF.')
        print(f"Planned output: {out_pdf}")
        return

    # Create fillable PDF (this will write output file)
    print('\nWriting output PDF with restored checkboxes...')
    sf._create_fillable_pdf()

    print(f"Wrote: {out_pdf}")
    print(f"Checkboxes created: {sf.stats.get('checkboxes_created', 0)}")

if __name__ == '__main__':
    main()
