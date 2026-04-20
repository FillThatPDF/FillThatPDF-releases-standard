
import pdfplumber
import sys
sys.path.insert(0, '.')
from smart_fillable_v23 import UniversalPDFFillable as SmartFillable

def inspect_p30():
    print("Starting inspection of P30 fields...")
    sf = SmartFillable('/Users/36981/Desktop/PDFTest/PDFs to test/Static PDFs/53252_DTE_EEA_Field_Inspection_Report_v11.pdf', '/tmp/debug_p30.pdf')
    # Run the detection steps manually until we get the fields
    sf._preprocess_document()
    sf._detect_words()
    sf._detect_lines()
    sf._detect_tables()
    sf._detect_checkboxes()
    
    # Run empty box detection
    sf._detect_empty_box_lines()
    
    # Run large image rect detection
    sf._detect_large_image_rects()
    
    print("\nFields on Page 30:")
    p30_fields = [f for f in sf.text_fields if f['page'] == 29] # 0-indexed
    for f in p30_fields:
        print(f"Name: {f['name']}, Source: {f.get('source')}, Rect: ({f['x0']:.1f}, {f['y0']:.1f}, {f['x1']:.1f}, {f['y1']:.1f})")

inspect_p30()
