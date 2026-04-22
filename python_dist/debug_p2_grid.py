import pdfplumber
from collections import defaultdict

pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/New Test Forms/11691_ConEd_Distributor_Application_Form_v10_FILLABLE.pdf"

with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[1]
    print(f"--- Page 2 Grid Audit ---")
    
    # Simple line-based cell detection for audit
    h_lines = sorted([l['top'] for l in page.lines if abs(l['y1']-l['y0']) < 1], key=lambda x: x)
    v_lines = sorted([l['x0'] for l in page.lines if abs(l['x1']-l['x0']) < 1], key=lambda x: x)
    
    # Just dump text blobs for now to see where "General Program Design" is
    words = page.extract_words()
    for w in words:
        if "General" in w['text'] or "Program" in w['text'] or "Design" in w['text']:
            print(f"Word: '{w['text']}' at y={w['top']:.1f}, x={w['x0']:.1f}")
            
    # Also dump large rects
    rects = page.rects
    for r in rects:
        if r['width'] > 200:
            print(f"Large Rect: y={r['top']:.1f} to {r['bottom']:.1f}, x={r['x0']:.1f} to {r['x1']:.1f}, h={r['height']:.1f}")
