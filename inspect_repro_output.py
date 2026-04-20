
import pikepdf
import sys

def inspect_output():
    pdf_path = "46119_repro_output.pdf"
    try:
        pdf = pikepdf.Pdf.open(pdf_path)
        print(f"Opened {pdf_path}")
        
        for i, page in enumerate(pdf.pages):
            if i not in [0, 2]: # Page 1 and Page 3
                continue
                
            print(f"\n--- Page {i+1} Widgets ---")
            if "/Annots" in page:
                for annot in page.Annots:
                    if annot.Type == "/Annot" and annot.Subtype == "/Widget":
                        name = str(annot.get("/T", "N/A"))
                        rect = list(annot.get("/Rect"))
                        # Rect is [x0, y0, x1, y1]
                        x0, y0, x1, y1 = float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])
                        width = x1 - x0
                        height = y1 - y0
                        
                        # Filter for fields of interest
                        is_utility = any(x in name for x in ["NJNG", "PSE", "SJG", "Electric", "Gas", "Municipal", "Rockland"])
                        is_page3_table = i == 2
                        
                        if is_utility or is_page3_table:
                            print(f"Name='{name}' Rect=[{x0:.1f}, {y0:.1f}, {x1:.1f}, {y1:.1f}] W={width:.1f} H={height:.1f}")
                            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    inspect_output()
