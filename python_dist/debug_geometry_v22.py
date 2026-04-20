import pdfplumber
import json
import os

pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/New Test Forms/11691_ConEd_Distributor_Application_Form_v10_FILLABLE.pdf"
output_json = "page1_geometry.json"

with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[0]
    
    data = {
        "width": float(page.width),
        "height": float(page.height),
        "lines": [],
        "rects": [],
        "text": []
    }
    
    for l in page.lines:
        data["lines"].append({
            "x0": float(l['x0']), "y0": float(l['top']),
            "x1": float(l['x1']), "y1": float(l['bottom']),
            "width": float(l['width']), "height": float(l['height'])
        })
        
    for r in page.rects:
        data["rects"].append({
            "x0": float(r['x0']), "y0": float(r['top']),
            "x1": float(r['x1']), "y1": float(r['bottom']),
            "width": float(r['width']), "height": float(r['height']),
            "non_stroking_color": r.get('non_stroking_color')
        })
        
    for w in page.extract_words():
        data["text"].append({
            "text": w['text'],
            "x0": float(w['x0']), "top": float(w['top']),
            "x1": float(w['x1']), "bottom": float(w['bottom'])
        })

with open(output_json, "w") as f:
    json.dump(data, f, indent=2)

print(f"Dumped geometry to {output_json}")
