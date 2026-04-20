#!/usr/bin/env python3
"""Restore small, non-table checkbox widgets conservatively across a PDF.

- Adds only visual checkboxes of reliable types (character/curve/vector)
- Skips any checkbox whose center lies inside a detected table cell
- Writes an output PDF when run with --confirm
- Writes a JSON summary and can generate quick overlays after writing PDF

Example:
  python3 tmp_restore_non_table_checkboxes.py --pdf input.pdf --confirm
"""
import argparse
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from smart_fillable_v21 import UniversalPDFFillable as SmartFillable


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pdf', required=True)
    parser.add_argument('--out', help='Output PDF path')
    parser.add_argument('--confirm', action='store_true')
    parser.add_argument('--pages', help='Optional pages to limit (1-based, e.g. 1-5 or 3,5)')
    args = parser.parse_args()

    inp = Path(args.pdf)
    if not inp.exists():
        print('ERROR: input not found')
        return

    out = Path(args.out) if args.out else inp.parent / f"{inp.stem}_restored_non_table_checkboxes.pdf"

    def parse_pages(s):
        if not s:
            return None
        pages = set()
        for part in s.split(','):
            if '-' in part:
                a,b = part.split('-',1); pages.update(range(int(a)-1,int(b)))
            else:
                pages.add(int(part)-1)
        return pages

    page_limit = parse_pages(args.pages)

    settings = {'disable_checkboxes_in_table_cells': True}
    sf = SmartFillable(str(inp), output_pdf=str(out), settings=settings)

    import pdfplumber
    sf.pdf = pdfplumber.open(str(inp))
    try:
        sf._preload_structural_data()
        sf._detect_all_checkboxes()
        sf._detect_table_cells()
    finally:
        pass

    candidates = []

    reliable_sources = set(['character','curve','vector'])

    # Build table index for quick membership test
    table_index = {}
    for cell in sf.table_cells:
        table_index.setdefault(cell['page'], []).append(cell)

    # Build existing widget bounding boxes for overlap checks
    existing = []
    for cb in getattr(sf, 'existing_checkboxes', []):
        existing.append((cb['page'], cb['x'], cb['y'], cb['width'], cb['height']))

    for cb in sf.checkboxes:
        p = cb['page']
        if page_limit and p not in page_limit:
            continue
        src = cb.get('source')
        if src not in reliable_sources:
            continue
        w = cb.get('width', 0); h = cb.get('height', 0)
        # Conservative size cutoff: only reasonably small checkboxes (<= 18pt)
        if max(w, h) > 18:
            continue
        cx = cb['x'] + w/2
        cy = cb['y'] + h/2
        # skip if inside a table cell
        inside_table = False
        for cell in table_index.get(p, []):
            if cell['x0'] <= cx <= cell['x1'] and cell['y0'] <= cy <= cell['y1']:
                inside_table = True
                break
        if inside_table:
            continue
        # skip if overlaps existing widget
        overlap_existing = False
        for (ep, ex, ey, ew, eh) in existing:
            if ep != p: continue
            # bbox overlap test
            if not (cb['x'] > ex+ew or cb['x']+w < ex or cb['y'] > ey+eh or cb['y']+h < ey):
                overlap_existing = True; break
        if overlap_existing:
            continue
        # skip if text present inside bbox
        try:
            txt = (sf.pdf.pages[p].within_bbox((cb['x']+1, cb['y']+1, cb['x']+w-1, cb['y']+h-1)).extract_text() or '').strip()
        except Exception:
            txt = ''
        if txt:
            continue
        candidates.append(cb)

    print(f"Detected {len(sf.checkboxes)} visual checkboxes total; {len(candidates)} conservative non-table candidates to add.")

    if not candidates:
        print('No candidates found. Aborting.')
        return

    added = 0
    added_pages = set()
    for cb in candidates:
        p = cb['page']
        ok = sf._add_checkbox(p, cb['x'], cb['y'], cb['width'], cb['height'], 'auto_restore_non_table')
        if ok:
            added += 1
            added_pages.add(p+1)

    print(f"Attempted to add {added} checkboxes on pages: {sorted(list(added_pages))}")

    # Save summary
    summary = {
        'input_pdf': str(inp),
        'output_pdf': str(out),
        'candidates': len(candidates),
        'added': added,
        'pages_changed': sorted(list(added_pages))
    }
    with open(inp.parent / (inp.stem + '.restore_non_table_summary.json'), 'w') as fh:
        json.dump(summary, fh, indent=2)

    if not args.confirm:
        print('Dry-run complete. Re-run with --confirm to write PDF and produce overlays.')
        print('Summary written to', inp.parent / (inp.stem + '.restore_non_table_summary.json'))
        return

    print('Writing output PDF...')
    # Try the engine writer first if it has any checkboxes queued
    if added > 0:
        sf._create_fillable_pdf()
        print('Wrote (engine):', out)
        pages_for_overlays = added_pages
    else:
        # Engine didn't add any checkboxes (likely due to duplicate detections); perform conservative direct widget injection
        import pikepdf
        pdf = pikepdf.Pdf.open(str(inp))
        acroform = pdf.Root.get('/AcroForm')
        if not acroform:
            pdf.Root.AcroForm = pikepdf.Dictionary({'/Fields': pikepdf.Array()})
            acroform = pdf.Root.AcroForm
        if '/Fields' not in acroform:
            acroform.Fields = pikepdf.Array()
        fields_arr = acroform.Fields

        def mkname(page, cx, cy):
            return f"AUTO_CB_P{page}_X{int(cx)}_Y{int(cy)}"

        created_pages = set()
        for cb in candidates:
            p = cb['page']
            w = cb['width']; h = cb['height']
            cx = cb['x'] + w/2; cy = cb['y'] + h/2
            half_size = max(w, h) / 2.0
            tx0, ty0, tx1, ty1 = sf._transform_coords_for_rotation(p, cx - half_size, cy - half_size, cx + half_size, cy + half_size)

            widget_dict = pikepdf.Dictionary({
                '/Type': pikepdf.Name('/Annot'),
                '/Subtype': pikepdf.Name('/Widget'),
                '/FT': pikepdf.Name('/Btn'),
                '/T': pikepdf.String(mkname(p+1, cx, cy)),
                '/Rect': pikepdf.Array([tx0, ty0, tx1, ty1]),
                '/F': 4,
                '/V': pikepdf.Name('/Off'),
                '/AS': pikepdf.Name('/Off'),
            })

            page_obj = pdf.pages[p]
            annot = pdf.make_indirect(widget_dict)
            if '/Annots' not in page_obj:
                page_obj.Annots = pdf.make_indirect(pikepdf.Array())
            page_obj.Annots.append(annot)
            fields_arr.append(annot)
            created_pages.add(p+1)

        out.parent.mkdir(parents=True, exist_ok=True)
        pdf.save(str(out))
        pdf.close()
        print('Wrote (injected widgets):', out, 'created widgets on pages', sorted(created_pages))
        pages_for_overlays = created_pages

    # Create quick overlays for changed pages (if any)
    if pages_for_overlays:
        pages_arg = ','.join(str(p) for p in sorted(pages_for_overlays))
        import subprocess
        subprocess.run(['python3','tmp_quick_overlays.py','--pdf',str(out),'--pages',pages_arg,'--out',f'overlays/{out.stem}_non_table','--dpi','72'])
        print('Wrote overlays to', f'overlays/{out.stem}_non_table')

if __name__ == '__main__':
    main()
