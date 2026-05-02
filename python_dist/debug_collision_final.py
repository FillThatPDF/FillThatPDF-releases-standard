import sys
import pdfplumber
from smart_fillable_v23 import UniversalPDFFillable

detector = UniversalPDFFillable('/Users/36981/Desktop/PDFTest/PDFs to test/Static PDFs/53252_DTE_EEA_Field_Inspection_Report_v11.pdf')
detector.pdf = pdfplumber.open(detector.input_pdf)
detector._preload_structural_data()
detector._detect_all_checkboxes()

field = {'x0': 38.1, 'y0': 441.8, 'x1': 214.1, 'y1': 458.9, 'page': 0}

collisions = []
for cb in detector.checkboxes:
    if cb['page'] != field['page']: continue
    x_overlap = max(0, min(field['x1'], cb['x'] + cb['width']) - max(field['x0'], cb['x']))
    y_overlap = max(0, min(field['y1'], cb['y'] + cb['height']) - max(field['y0'], cb['y']))
    if x_overlap > 1 and y_overlap > 1:
        collisions.append(cb)

print(f"COLLISION: {len(collisions)} found")
for c in collisions:
    print(c)
