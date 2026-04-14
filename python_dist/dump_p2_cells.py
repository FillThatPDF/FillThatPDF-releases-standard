import pdfplumber
from collections import defaultdict

pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/New Test Forms/11691_ConEd_Distributor_Application_Form_v10_FILLABLE.pdf"

with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[1]
    print(f"--- Page 2 Cell Audit ---")
    
    # Use the same logic as the main script to find cells
    # (Simplified for debug)
    words = page.extract_words()
    
    # Just look for the words and their containing rects
    for r in page.rects:
        if r['width'] > 300:
            # Find words in this rect
            in_words = [w for w in words if r['x0']-2 <= w['x0'] <= r['x1']+2 and r['top']-2 <= w['top'] <= r['bottom']+2]
            text = " ".join(w['text'] for w in in_words)
            print(f"Rect: y={r['top']:.1f}-{r['bottom']:.1f} h={r['height']:.1f} | Text: '{text[:50]}...' len={len(text)}")
            
    # Also check lines
    for l in page.lines:
        if l['width'] > 300:
             print(f"Line: y={l['top']:.1f} x={l['x0']:.1f}-{l['x1']:.1f}")
