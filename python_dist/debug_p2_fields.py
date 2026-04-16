#!/usr/bin/env python3
"""Debug Page 2 field properties in the fillable PDF."""

import pikepdf

pdf_path = '/Users/36981/Desktop/PDFTest/PDFs to test/Batch 1 Forms/53252_DTE_EEA_Field_Inspection_Report_v11_FILLABLE.pdf'
pdf = pikepdf.open(pdf_path)

print('=== Page 2 Fields - Full Details ===')
page = pdf.pages[1]

if '/Annots' in page:
    for i, annot in enumerate(page['/Annots']):
        obj = annot if isinstance(annot, pikepdf.Dictionary) else pdf.get_object(annot)
        if '/FT' in obj and str(obj['/FT']) == '/Tx':
            name = str(obj.get('/T', 'unnamed'))
            rect = [float(x) for x in obj['/Rect']]
            
            # Check for AP (appearance stream)
            ap = obj.get('/AP')
            has_ap = ap is not None
            
            # Check for /DA (default appearance)
            da = obj.get('/DA')
            
            # Check for flags
            ff = int(obj.get('/Ff', 0))
            
            # Print all relevant info
            print(f'\nField: {name}')
            print(f'  Rect: {rect}')
            print(f'  Height: {rect[3]-rect[1]:.1f}pt')
            print(f'  Has AP stream: {has_ap}')
            print(f'  DA: {da}')
            print(f'  Ff: {ff} (multiline={bool(ff & 0x1000)})')
            
            if has_ap:
                print(f'  AP dict keys: {list(ap.keys())}')
                if '/N' in ap:
                    n_stream = ap['/N']
                    print(f'  AP/N type: {type(n_stream)}')
                    if hasattr(n_stream, 'keys'):
                        print(f'  AP/N keys: {list(n_stream.keys())}')
                    if '/BBox' in n_stream:
                        bbox = [float(x) for x in n_stream['/BBox']]
                        print(f'  AP BBox: {bbox}')
                        print(f'  AP BBox height: {bbox[3]-bbox[1]:.1f}pt')
else:
    print('No annotations on page 2')
