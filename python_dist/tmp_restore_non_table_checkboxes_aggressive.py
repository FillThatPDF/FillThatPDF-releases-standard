#!/usr/bin/env python3
"""Aggressive restore dry-run: detect many more visual checkbox candidates
and optionally write a temporary injected PDF + overlays for QA.

Usage:
  python3 tmp_restore_non_table_checkboxes_aggressive.py --pdf input.pdf --write-dry

- Does NOT change original when run without a final --confirm flag.
- Writes <input>_aggressive_dry_injected.pdf when --write-dry is provided.
- Writes JSON report <input>.aggressive_restore_dry.json and overlays into overlays/<outstem>_aggressive
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
    parser.add_argument('--pages', help='Optional pages to limit (1-based range or list)')
    parser.add_argument('--write-dry', action='store_true', help='Write a dry-run injected PDF for overlays')
    args = parser.parse_args()

    inp = Path(args.pdf)
    if not inp.exists():
        print('ERROR: input not found'); return

    def parse_pages(s):
        if not s: return None
        pages = set()
        for part in s.split(','):
            if '-' in part:
                a,b = part.split('-',1); pages.update(range(int(a)-1,int(b)))
            else: pages.add(int(part)-1)
        return pages

    page_limit = parse_pages(args.pages)

    settings={'disable_checkboxes_in_table_cells': True, 'debug': False}
    sf = SmartFillable(str(inp), output_pdf=str(inp.parent / (inp.stem + '_aggressive_dry_injected.pdf')), settings=settings)

    import pdfplumber
    sf.pdf = pdfplumber.open(str(inp))
    try:
        sf._preload_structural_data()
        sf._detect_all_checkboxes()
        sf._detect_table_cells()
    finally:
        pass

    # Aggressive sources (include line_box and others)
    aggressive_sources = set(['character','curve','vector','line_box','line','box_char','visual_in_cell','table_col'])

    candidates = []
    # build table index
    table_index = {}
    for cell in sf.table_cells:
        table_index.setdefault(cell['page'], []).append(cell)

    for cb in sf.checkboxes:
        p = cb['page']
        if page_limit and p not in page_limit: continue
        src = cb.get('source')
        if src not in aggressive_sources:
            continue
        w = cb.get('width',0); h = cb.get('height',0)
        # allow larger checkboxes up to 28pt
        if max(w,h) > 28: continue
        cx = cb['x'] + w/2; cy = cb['y'] + h/2
        # skip if inside a table cell
        inside_table=False
        for cell in table_index.get(p, []):
            if cell['x0'] <= cx <= cell['x1'] and cell['y0'] <= cy <= cell['y1']:
                inside_table=True; break
        if inside_table: continue
        # skip headers
        if cy < 40: continue
        # skip obvious duplicates/overlaps with existing widgets
        overlap=False
        for ex in getattr(sf,'existing_checkboxes',[]):
            if ex['page']!=p: continue
            if not (cb['x'] > ex['x']+ex['width'] or cb['x']+w < ex['x'] or cb['y'] > ex['y']+ex['height'] or cb['y']+h < ex['y']):
                overlap=True; break
        if overlap: continue
        candidates.append(cb)

    per_page = {}
    for c in candidates: per_page[c['page']+1] = per_page.get(c['page']+1,0)+1

    report = {
        'input_pdf': str(inp),
        'candidates_total': len(candidates),
        'candidates_per_page': per_page,
        'sample': [{'page':c['page']+1,'center':[c['x']+c['width']/2, c['y']+c['height']/2],'w':c['width'],'h':c['height'],'source':c.get('source')} for c in candidates[:200]]
    }

    report_path = inp.parent / (inp.stem + '.aggressive_restore_dry.json')
    with open(report_path, 'w') as fh: json.dump(report, fh, indent=2)

    print(f"Found {len(candidates)} aggressive non-table candidates. Report: {report_path}")

    if not candidates:
        return

    if not args.write_dry:
        print('Dry-run complete; re-run with --write-dry to write a temp injected PDF and overlays for review.')
        return

    # Write injected PDF (temporary) with these candidates inserted as widgets
    import pikepdf
    out_temp = inp.parent / (inp.stem + '_aggressive_dry_injected.pdf')
    pdf = pikepdf.Pdf.open(str(inp))
    acro = pdf.Root.get('/AcroForm')
    if not acro:
        pdf.Root.AcroForm = pikepdf.Dictionary({'/Fields': pikepdf.Array()})
        acro = pdf.Root.AcroForm
    if '/Fields' not in acro:
        acro.Fields = pikepdf.Array()
    fields = acro.Fields

    def mkfn(p,cx,cy): return f"AG_CB_P{p}_X{int(cx)}_Y{int(cy)}"

    created_pages=set()
    for c in candidates:
        p=c['page']
        w=c['width']; h=c['height']
        cx=c['x']+w/2; cy=c['y']+h/2
        half = max(w,h)/2.0
        tx0, ty0, tx1, ty1 = sf._transform_coords_for_rotation(p, cx-half, cy-half, cx+half, cy+half)
        widget = pikepdf.Dictionary({
            '/Type': pikepdf.Name('/Annot'), '/Subtype': pikepdf.Name('/Widget'), '/FT': pikepdf.Name('/Btn'),
            '/T': pikepdf.String(mkfn(p+1,cx,cy)), '/Rect': pikepdf.Array([tx0,ty0,tx1,ty1]), '/F':4, '/V':pikepdf.Name('/Off'), '/AS':pikepdf.Name('/Off')
        })
        pg = pdf.pages[p]
        ann = pdf.make_indirect(widget)
        if '/Annots' not in pg: pg.Annots = pdf.make_indirect(pikepdf.Array())
        pg.Annots.append(ann)
        fields.append(ann)
        created_pages.add(p+1)

    pdf.save(str(out_temp)); pdf.close()
    print('Wrote dry injected PDF:', out_temp)

    # Produce overlays for created pages
    pages_arg = ','.join(str(p) for p in sorted(created_pages))
    import subprocess
    out_overlay_dir = Path('overlays') / (out_temp.stem + '_aggressive')
    subprocess.run(['python3','tmp_quick_overlays.py','--pdf',str(out_temp),'--pages',pages_arg,'--out',str(out_overlay_dir),'--dpi','150'])
    print('Wrote overlays to', out_overlay_dir)

if __name__=='__main__': main()
