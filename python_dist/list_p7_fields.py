#!/usr/bin/env python3
"""List fields on Page 7"""
import pikepdf

pdf = pikepdf.open('/Users/36981/Desktop/PDFTest/test_57643_debug.pdf')
root = pdf.Root
if '/AcroForm' in root:
    acroform = root['/AcroForm']
    fields = acroform.get('/Fields', [])
    print(f'Total fields: {len(fields)}')
    
    # Get page 7 fields
    p7_fields = []
    for f in fields:
        try:
            field = pdf.get_object(f) if isinstance(f, pikepdf.ObjectStreamID) else f
            name = str(field.get('/T', ''))
            # Get page from /P or parent
            page = field.get('/P')
            if page:
                page_idx = list(pdf.pages).index(page)
                if page_idx == 6:  # Page 7
                    rect = field.get('/Rect', [])
                    p7_fields.append((name, list(rect) if rect else []))
        except Exception as e:
            pass
    
    print(f'Page 7 fields: {len(p7_fields)}')
    for name, rect in sorted(p7_fields, key=lambda x: x[0]):
        rect_str = f'[{rect[0]:.0f},{rect[1]:.0f},{rect[2]:.0f},{rect[3]:.0f}]' if rect else 'no rect'
        print(f'  {name}: {rect_str}')
