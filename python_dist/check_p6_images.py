#!/usr/bin/env python3
"""Check image boxes more thoroughly"""
import pikepdf

pdf_path = '/Users/36981/Desktop/PDFTest/PDFs to test/Batch 1 Forms/53252_DTE_EEA_Field_Inspection_Report_v11_fillable.pdf'
pdf = pikepdf.open(pdf_path)

page = pdf.pages[5]  # Page 6
page_height = float(page.MediaBox[3]) if '/MediaBox' in page else 792
annots = page.get('/Annots', [])

print(f"Page 6: {len(annots)} annotations")
print(f"Page height: {page_height}")

print("\nAll pushbuttons (/Btn with Pushbutton flag):")
for i, annot in enumerate(annots):
    try:
        ft = str(annot.get('/FT', ''))
        if ft != '/Btn':
            continue
        ff = int(annot.get('/Ff', 0))
        is_pushbutton = ff & (1 << 16)  # Pushbutton flag
        
        rect = annot.get('/Rect', [])
        if not rect:
            continue
        x0, y0, x1, y1 = [float(v) for v in rect]
        w = x1 - x0
        h = y1 - y0
        y_top = page_height - y1
        
        name = str(annot.get('/T', 'Unknown'))
        
        if is_pushbutton:
            print(f"  {name}: {w:.1f}x{h:.1f} at y_top={y_top:.1f}")
        elif w > 50 and h > 50:  # Large buttons that might be image areas
            print(f"  LARGE Btn (not pushbutton): {name}: {w:.1f}x{h:.1f} at y_top={y_top:.1f}")
    except Exception as e:
        print(f"  Error: {e}")

print("\nAll annotations with 'Image' in name:")
for annot in annots:
    try:
        name = str(annot.get('/T', ''))
        if 'Image' in name or 'image' in name:
            rect = annot.get('/Rect', [])
            if rect:
                x0, y0, x1, y1 = [float(v) for v in rect]
                w = x1 - x0
                h = y1 - y0
                y_top = page_height - y1
                ft = str(annot.get('/FT', ''))
                print(f"  {name}: {w:.1f}x{h:.1f} at y_top={y_top:.1f}, FT={ft}")
    except Exception as e:
        print(f"  Error: {e}")

pdf.close()
