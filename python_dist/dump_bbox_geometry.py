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
        'rects': [{k: v for k, v in r.items() if k in ['x0', 'y0', 'x1', 'y1', 'non_stroking_color', 'stroking_color', 'width', 'height']} for r in crop.rects],
        'lines': [{k: v for k, v in l.items() if k in ['x0', 'y0', 'x1', 'y1', 'width', 'height', 'stroking_color']} for l in crop.lines],
        'words': [{k: v for k, v in w.items() if k in ['x0', 'top', 'x1', 'bottom', 'text']} for w in crop.extract_words()],
        'images': len(crop.images)
    }
    print(json.dumps(data, indent=2))
