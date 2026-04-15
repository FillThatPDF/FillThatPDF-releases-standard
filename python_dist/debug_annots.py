#!/usr/bin/env python3
"""Debug script to find Widget annotations that are actually hyperlinks."""
import pikepdf
import sys
import os

test_dir = '/Users/36981/Desktop/PDFTest'
for f in sorted(os.listdir(test_dir)):
    if not f.endswith('.pdf'):
        continue
    fpath = os.path.join(test_dir, f)
    try:
        pdf = pikepdf.open(fpath)
        for pi, page in enumerate(pdf.pages):
            if '/Annots' not in page:
                continue
            for annot in page.Annots:
                sub = str(annot.get('/Subtype', 'none'))
                if sub != '/Widget':
                    continue
                ft = str(annot.get('/FT', 'none'))
                has_a = '/A' in annot
                a_type = ''
                uri = ''
                if has_a:
                    a_dict = annot['/A']
                    a_type = str(a_dict.get('/S', ''))
                    if '/URI' in a_dict:
                        uri = str(a_dict['/URI'])[:60]
                t = str(annot.get('/T', 'none'))
                parent_ft = ''
                if '/Parent' in annot:
                    parent_ft = str(annot['/Parent'].get('/FT', 'none'))
                
                # Flag: no FT = suspicious; URI action = hyperlink-like
                is_link_like = (ft == 'none' and parent_ft == 'none') or a_type == '/URI'
                flag = ' *** LINK-LIKE ***' if is_link_like else ''
                
                print(f'{f[:50]:50s} p{pi} FT={ft:8s} PFT={parent_ft:8s} T={t[:25]:25s} A={a_type:12s} URI={uri}{flag}')
        pdf.close()
    except Exception as e:
        pass
