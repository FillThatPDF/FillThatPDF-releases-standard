#!/usr/bin/env python3
"""Check comb field coordinates."""
import pikepdf

pdf = pikepdf.open('/Users/36981/Desktop/PDFTest/PDFs to test/Batch 1 Forms/57643_DTE_MF_Measures_and_Specs_Income_Qualified_Catalog_v2_FILLABLE.pdf')
page = pdf.pages[6]  # Page 7

print("=== Page 7 Comb Fields ===")
for annot in page.get('/Annots', []):
    obj = pdf.get_object(annot) if not isinstance(annot, pikepdf.Dictionary) else annot
    if '/FT' in obj and str(obj['/FT']) == '/Tx':
        name = str(obj.get('/T', ''))
        if 'account' in name.lower() or 'gas' in name.lower() or 'electric' in name.lower():
            rect = [float(x) for x in obj['/Rect']]
            ff = int(obj.get('/Ff', 0))
            maxlen = int(obj.get('/MaxLen', 0)) if '/MaxLen' in obj else 0
            is_comb = bool(ff & 0x1000000)
            print(f'\n{name}:')
            print(f'  Rect: x={rect[0]:.1f} to {rect[2]:.1f}, y={rect[1]:.1f} to {rect[3]:.1f}')
            print(f'  Width: {rect[2]-rect[0]:.1f}pt, MaxLen={maxlen}, Comb={is_comb}')
            if maxlen > 0:
                print(f'  Char width: {(rect[2]-rect[0])/maxlen:.1f}pt per char')

# Compare with actual boxes
print("\n=== Expected Box Positions (from PDF lines) ===")
print("Gas Account: x=36.2 to 252.2 (13 lines, 12 boxes, 18pt each)")
print("  Full width should be: 36.2 to 270.2 (234pt for 12 boxes)")
print("Electric Account: x=301.8 to 517.8 (13 lines, 12 boxes, 18pt each)")
print("  Full width should be: 301.8 to 535.8 (234pt for 12 boxes)")
