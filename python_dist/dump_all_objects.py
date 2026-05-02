import pdfplumber
import json
import sys

pdf_path = sys.argv[1]
page_num = int(sys.argv[2])
bbox = list(map(float, sys.argv[3:7])) # x0, y0, x1, y1

with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[page_num]
    crop = page.within_bbox(bbox)
    
    data = {
        'text': crop.extract_text(),
        'objects': {k: [ {subk: subv for subk, subv in r.items() if subk in ['x0', 'y0', 'x1', 'y1', 'non_stroking_color', 'stroking_color', 'width', 'height', 'top', 'bottom']} for r in v ] for k, v in crop.objects.items() if v}
    }
    print(json.dumps(data, indent=2))
