#!/usr/bin/env python3
"""Check where P3 checkboxes come from in fillable PDF"""
import pikepdf

pdf_path = '/Users/36981/Desktop/PDFTest/PDFs to test/Batch 1 Forms/53252_DTE_EEA_Field_Inspection_Report_v11_fillable.pdf'
pdf = pikepdf.open(pdf_path)

page = pdf.pages[2]  # Page 3
page_height = float(page.MediaBox[3]) if '/MediaBox' in page else 792
annots = page.get('/Annots', [])

print(f"Page 3: {len(annots)} annotations")

checkboxes = []
for annot in annots:
    try:
        ft = str(annot.get('/FT', ''))
        if ft != '/Btn':
            continue
        # Check if it's a checkbox (not radio)
        ff = int(annot.get('/Ff', 0))
        if ff & (1 << 15):  # Radio button flag
            continue
        if ff & (1 << 16):  # Pushbutton flag
            continue
        
        rect = annot.get('/Rect', [])
        if not rect:
            continue
        x0, y0, x1, y1 = [float(v) for v in rect]
        w = x1 - x0
        h = y1 - y0
        
        # Only small widgets (checkboxes)
        if w > 20 or h > 20:
            continue
        
        name = str(annot.get('/T', 'Unknown'))
        
        checkboxes.append({
            'name': name,
            'x': x0,
            'y': y0,
            'w': w,
            'h': h,
            'y_top': page_height - y1
        })
    except:
        pass

print(f"Found {len(checkboxes)} checkboxes on page 3")

# Group by x position
by_x = {}
for cb in checkboxes:
    x_key = round(cb['x'] / 5) * 5
    by_x.setdefault(x_key, []).append(cb)

print("\nGrouped by X position:")
for x_key in sorted(by_x.keys()):
    cbs = by_x[x_key]
    print(f"\n  x~{x_key}: {len(cbs)} checkboxes")
    for cb in cbs[:5]:
        print(f"    {cb['name']}: x={cb['x']:.1f}, y_top={cb['y_top']:.1f}")

pdf.close()
