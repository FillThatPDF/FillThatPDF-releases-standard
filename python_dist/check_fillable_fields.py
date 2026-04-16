#!/usr/bin/env python3
"""Check the generated fillable PDF field positions"""
import pikepdf

pdf_path = '/Users/36981/Desktop/PDFTest/PDFs to test/Batch 1 Forms/53252_DTE_EEA_Field_Inspection_Report_v11_fillable.pdf'
pdf = pikepdf.open(pdf_path)

# Get AcroForm fields
acroform = pdf.Root.get('/AcroForm')
if not acroform:
    print("No AcroForm found")
    exit()

fields = acroform.get('/Fields', [])
print(f"Total fields: {len(fields)}")

# Check page 3 (0-indexed = 2)
print("\n=== Page 3 Checkboxes ===")
page3 = pdf.pages[2]
page3_height = float(page3.MediaBox[3]) if '/MediaBox' in page3 else 792
print(f"Page 3 height: {page3_height}")

p3_checkboxes = []
for field in fields:
    try:
        ft = str(field.get('/FT', ''))
        if ft != '/Btn':
            continue
        # Check if it's a checkbox (not radio)
        ff = int(field.get('/Ff', 0))
        if ff & (1 << 15):  # Radio button flag
            continue
        # Get rect
        if '/Kids' in field:
            for kid in field['/Kids']:
                rect = kid.get('/Rect', [])
                if rect:
                    x0, y0, x1, y1 = [float(v) for v in rect]
                    page_ref = kid.get('/P', None)
                    if page_ref:
                        for i, pg in enumerate(pdf.pages):
                            if pg.objgen == page_ref.objgen:
                                if i == 2:  # Page 3
                                    name = str(field.get('/T', 'Unknown'))
                                    p3_checkboxes.append((name, x0, y0, x1, y1))
                                break
        else:
            rect = field.get('/Rect', [])
            if rect:
                x0, y0, x1, y1 = [float(v) for v in rect]
                page_ref = field.get('/P', None)
                if page_ref:
                    for i, pg in enumerate(pdf.pages):
                        if pg.objgen == page_ref.objgen:
                            if i == 2:  # Page 3
                                name = str(field.get('/T', 'Unknown'))
                                p3_checkboxes.append((name, x0, y0, x1, y1))
                            break
    except Exception as e:
        pass

print(f"Found {len(p3_checkboxes)} checkboxes on page 3")
# Sort by x position
p3_checkboxes.sort(key=lambda x: x[1])
for name, x0, y0, x1, y1 in p3_checkboxes[:15]:
    print(f"  {name}: x={x0:.1f}, y={y0:.1f}")

# Check page 6 image upload fields
print("\n=== Page 6 Image Upload Fields ===")
page6 = pdf.pages[5]
page6_height = float(page6.MediaBox[3]) if '/MediaBox' in page6 else 792
print(f"Page 6 height: {page6_height}")

p6_images = []
for field in fields:
    try:
        ft = str(field.get('/FT', ''))
        name = str(field.get('/T', 'Unknown'))
        if 'Image' not in name and 'image' not in name:
            continue
        
        rect = field.get('/Rect', [])
        if rect:
            x0, y0, x1, y1 = [float(v) for v in rect]
            page_ref = field.get('/P', None)
            if page_ref:
                for i, pg in enumerate(pdf.pages):
                    if pg.objgen == page_ref.objgen:
                        if i == 5:  # Page 6
                            p6_images.append((name, x0, y0, x1, y1))
                        break
    except Exception as e:
        pass

print(f"Found {len(p6_images)} image fields on page 6")
for name, x0, y0, x1, y1 in p6_images[:10]:
    # Convert y to top-down for comparison with pdfplumber
    y_top = page6_height - y1
    y_bottom = page6_height - y0
    w = x1 - x0
    h = y1 - y0
    print(f"  {name}: {w:.1f}x{h:.1f} at x=({x0:.1f}, {x1:.1f}), PDF-y=({y0:.1f}, {y1:.1f}) -> top-down y=({y_top:.1f}, {y_bottom:.1f})")

# Check page 2 text fields
print("\n=== Page 2 Summary Fields ===")
page2 = pdf.pages[1]
p2_text = []
for field in fields:
    try:
        ft = str(field.get('/FT', ''))
        if ft != '/Tx':
            continue
        name = str(field.get('/T', 'Unknown'))
        
        rect = field.get('/Rect', [])
        if rect:
            x0, y0, x1, y1 = [float(v) for v in rect]
            page_ref = field.get('/P', None)
            if page_ref:
                for i, pg in enumerate(pdf.pages):
                    if pg.objgen == page_ref.objgen:
                        if i == 1:  # Page 2
                            p2_text.append((name, x0, y0, x1, y1))
                        break
    except Exception as e:
        pass

print(f"Found {len(p2_text)} text fields on page 2")
# Expected cell: x=(171-575.5) for content
for name, x0, y0, x1, y1 in p2_text[:10]:
    w = x1 - x0
    h = y1 - y0
    print(f"  {name[:40]:40}: {w:.1f}x{h:.1f} at x=({x0:.1f}-{x1:.1f})")

pdf.close()
