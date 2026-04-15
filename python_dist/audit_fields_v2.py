import pikepdf
import sys

if len(sys.argv) < 2:
    print("Usage: python3 audit_fields_v2.py <pdf_path>")
    sys.exit(1)

pdf_path = sys.argv[1]

with pikepdf.open(pdf_path) as pdf:
    for i, page in enumerate(pdf.pages):
        print(f"--- Page {i+1} Fields ---")
        if "/Annots" in page:
            # Sort annots by Y (top to bottom)
            annots = sorted([a for a in page.Annots if a.Subtype == "/Widget"], 
                           key=lambda a: float(a.Rect[1]), reverse=True)
            
            for annot in annots:
                field = annot.get("/Parent") or annot
                name = str(field.T) if "/T" in field else "Unknown"
                rect = [float(x) for x in annot.Rect]
                # Page height is usually 792 for letter
                # But we should get it from page.MediaBox
                mb = page.MediaBox
                ph = float(mb[3])
                
                y0 = ph - rect[3]
                y1 = ph - rect[1]
                width = rect[2] - rect[0]
                source = str(field.get("/Source", "None"))
                print(f"[{i+1}] '{name}' | y={y0:.1f}-{y1:.1f} | x={rect[0]:.1f}-{rect[2]:.1f} | w={width:.1f} | src={source}")
