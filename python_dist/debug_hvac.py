import pdfplumber

with pdfplumber.open("9787_ConEd_Res HVAC Electric Rebate Appl_v01_FINAL_RELEASE_NOEDIT.pdf") as pdf:
    page = pdf.pages[1] # Page 2
    # Area for HVAC Yes/No: x approx 100-200, y approx 300-400
    print("--- RECTS ---")
    for r in page.rects:
        if 200 < r['bottom'] < 500:
            print(f"Rect: {r['x0']:.1f}, {r['top']:.1f}, {r['x1']:.1f}, {r['bottom']:.1f} (w={r['width']:.1f}, h={r['height']:.1f})")
    
    print("\n--- CURVES ---")
    for c in page.curves:
         if 200 < c['bottom'] < 500:
            print(f"Curve: {c['x0']:.1f}, {c['top']:.1f}, {c['x1']:.1f}, {c['bottom']:.1f} (w={c['width']:.1f}, h={c['height']:.1f})")

    print("\n--- LINES ---")
    for l in page.lines:
         if 200 < l['bottom'] < 500 and l['width'] < 20:
            print(f"Line: {l['x0']:.1f}, {l['top']:.1f}, {l['x1']:.1f}, {l['bottom']:.1f} (w={l['width']:.1f}, h={l['height']:.1f})")
