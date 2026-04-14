
import pdfplumber
import logging

def check_pdf():
    pdf_path = "output_verify.pdf"
    print(f"Checking {pdf_path}...")
    try:
        with pdfplumber.open(pdf_path) as pdf:
            # Check Page 1 (Index 0) for SJG and Municipal
            p1 = pdf.pages[0]
            # Print page dimensions to understand coordinates
            print(f"Page 1 Size: {p1.width}x{p1.height}")
            
            # Extract annotations
            # pdfplumber approach varies. Let's try .annots first
            fields = p1.annots or []
            print(f"Page 1 has {len(fields)} fields")
            
            for f in fields:
                # Get name (T) and Rect
                # Name key might be 'T' or in data
                name = ""
                if 'T' in f:
                    name = f['T']
                elif 'data' in f and 'T' in f['data']:
                    name = f['data']['T']
                
                # Resolve indirect object for name if needed (pdfplumber handles this usually)
                if isinstance(name, bytes):
                    name = name.decode('utf-8', errors='ignore')
                
                rect = f.get('Rect', [])
                
                print(f"Field: {name} Rect={rect}")
                
            # Find text coordinates for SJG and Municipal
            print("\nText Locations on Page 1:")
            words = p1.extract_words()
            for w in words:
                if 'SJG' in w['text'] or 'Municipal' in w['text']:
                    print(f"Text '{w['text']}' at top={w['top']}, bottom={w['bottom']}, x0={w['x0']}")

            # Check Page 3 (Index 2) for Phantom Fields
            if len(pdf.pages) > 2:
                p3 = pdf.pages[2]
                print(f"Page 3 Size: {p3.width}x{p3.height}")
                fields_p3 = p3.annots or []
                print(f"Page 3 has {len(fields_p3)} fields")
                
                # We want to ensure no fields are around y=150-250 (instructional text)
                # "Elizabethtown Gas" text is usually in that range.
                # Let's dump all fields in that Y range.
                
                for f in fields_p3:
                    rect = f.get('Rect', [0,0,0,0])
                    # Rect is [x0, y0, x1, y1] (Bottom-Left origin in PDF spec, usually)
                    # pdfplumber converts to Top-Left? No, .annots usually returns raw PDF rects (Bottom-Left).
                    # But Page.extract_words uses Top-Left.
                    # Let's assume raw PDF rects: y0 is bottom, y1 is top.
                    # If page height is ~792.
                    # Top of page is y=792.
                    # Instructional text is near top. So y is high (e.g. 600-750).
                    
                    y0 = float(rect[1]) if len(rect)>1 else 0
                    y1 = float(rect[3]) if len(rect)>3 else 0
                    
                    # Print all fields to be sure
                    name = f.get('T', '')
                    if isinstance(name, bytes): name = name.decode('utf-8', errors='ignore')
                    
                    # Print fields that might be phantom (checkboxes or small text)
                    if 'Check' in str(name) or 'Text' in str(name) or 'Field' in str(name):
                        print(f"P3 Field: {name} Rect={rect}")
            else:
                print("Page 3 not found in output PDF")

    except Exception as e:
        print(f"Error checking PDF: {e}")

if __name__ == "__main__":
    check_pdf()
