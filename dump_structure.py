import pdfplumber

pdf_path = "/Users/36981/Desktop/Studio_Time_Sheet.pdf"
with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[0]
    print(f"Total lines: {len(page.lines)}")
    print(f"Total rects: {len(page.rects)}")
    
    print("\n--- RECTS ---")
    for i, r in enumerate(sorted(page.rects, key=lambda l: l['top'])):
        print(f"Rect {i}: x0={r['x0']:.2f}, x1={r['x1']:.2f}, top={r['top']:.2f}, bottom={r['bottom']:.2f}, w={r['width']:.2f}, h={r['height']:.2f}")

    print("\n--- LINES ---")
    for i, l in enumerate(sorted(page.lines, key=lambda l: l['top'])):
        print(f"Line {i}: x0={l['x0']:.2f}, x1={l['x1']:.2f}, top={l['top']:.2f}, bottom={l['bottom']:.2f}, w={l['width']:.2f}")
