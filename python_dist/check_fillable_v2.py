#!/usr/bin/env python3
"""Check the generated fillable PDF - iterate through page annotations"""
import pikepdf

pdf_path = '/Users/36981/Desktop/PDFTest/PDFs to test/Batch 1 Forms/53252_DTE_EEA_Field_Inspection_Report_v11_fillable.pdf'
pdf = pikepdf.open(pdf_path)

def check_page(page_num, search_terms=None):
    """Check annotations on a specific page"""
    page = pdf.pages[page_num]
    annots = page.get('/Annots', [])
    print(f"\n=== Page {page_num + 1} - {len(annots)} annotations ===")
    page_height = float(page.MediaBox[3]) if '/MediaBox' in page else 792
    
    results = []
    for annot in annots:
        try:
            subtype = str(annot.get('/Subtype', ''))
            rect = annot.get('/Rect', [])
            if not rect:
                continue
            x0, y0, x1, y1 = [float(v) for v in rect]
            w = x1 - x0
            h = y1 - y0
            
            # Get field info
            ft = str(annot.get('/FT', ''))
            t = str(annot.get('/T', ''))
            
            # Convert to top-down for comparison
            y_top = page_height - y1
            
            results.append({
                'name': t,
                'ft': ft,
                'subtype': subtype,
                'x0': x0, 'y0': y0, 'x1': x1, 'y1': y1,
                'w': w, 'h': h,
                'y_top': y_top
            })
        except:
            pass
    
    return results

# Page 3 - Quality Scores
print("\n" + "="*60)
print("PAGE 3 - QUALITY SCORES TABLE")
print("Expected checkbox columns: Pass (220.6-252.1), Fail (252.1-283.6)")
print("="*60)
p3 = check_page(2)
# Show checkboxes (small widgets)
checkboxes = [r for r in p3 if r['w'] < 20 and r['h'] < 20 and r['ft'] == '/Btn']
print(f"\nCheckboxes (small /Btn): {len(checkboxes)}")
# Group by x position
x_positions = {}
for cb in checkboxes:
    x_key = round(cb['x0'] / 5) * 5  # Round to nearest 5
    x_positions.setdefault(x_key, []).append(cb)
for x_key in sorted(x_positions.keys()):
    cbs = x_positions[x_key]
    print(f"  x~{x_key}: {len(cbs)} checkboxes")
    if len(cbs) <= 3:
        for cb in cbs:
            print(f"    {cb['name']}: x={cb['x0']:.1f}, y_top={cb['y_top']:.1f}")

# Page 6 - Water Heater
print("\n" + "="*60)
print("PAGE 6 - WATER HEATER (IMAGE BOXES)")
print("Expected image boxes at pdfplumber coords:")
print("  (36.5, 387.5) to (197.5, 499.5) - 161x112")
print("  (225.5, 387.5) to (386.5, 499.5)")
print("  (414.5, 387.5) to (575.5, 499.5)")
print("="*60)
p6 = check_page(5)
# Show push buttons (image upload areas)
pushbuttons = [r for r in p6 if r['ft'] == '/Btn' and r['w'] > 50]
print(f"\nPush buttons (large /Btn): {len(pushbuttons)}")
for pb in pushbuttons[:10]:
    print(f"  {pb['name'][:30]}: {pb['w']:.1f}x{pb['h']:.1f} at ({pb['x0']:.1f}, {pb['x1']:.1f}), y_top={pb['y_top']:.1f}")

# Also check text fields that might be image areas
large_text = [r for r in p6 if r['ft'] == '/Tx' and r['w'] > 80 and r['h'] > 80]
print(f"\nLarge text fields (>80x80): {len(large_text)}")
for tf in large_text[:10]:
    print(f"  {tf['name'][:30]}: {tf['w']:.1f}x{tf['h']:.1f} at ({tf['x0']:.1f}, {tf['x1']:.1f}), y_top={tf['y_top']:.1f}")

# Page 2 - Summary
print("\n" + "="*60)
print("PAGE 2 - SUMMARY")
print("Expected content cell width: ~404.5 at x=(171-575.5)")
print("="*60)
p2 = check_page(1)
text_fields = [r for r in p2 if r['ft'] == '/Tx']
print(f"\nText fields: {len(text_fields)}")
for tf in text_fields[:10]:
    print(f"  {tf['name'][:40]}: {tf['w']:.1f}x{tf['h']:.1f} at x=({tf['x0']:.1f}-{tf['x1']:.1f})")

pdf.close()
