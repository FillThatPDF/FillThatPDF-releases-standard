from smart_fillable_v21 import UniversalPDFFillable
import json
import sys
import pdfplumber

def dump_detected_cells(input_pdf):
    detector = UniversalPDFFillable(input_pdf, "output.pdf")
    detector.pdf = pdfplumber.open(input_pdf)
    detector._preload_structural_data()
    detector._detect_table_cells()
    
    cells = []
    for c in detector.table_cells:
        cells.append({
            "page": c["page"],
            "x0": c["x0"],
            "y0": c["y0"],
            "x1": c["x1"],
            "y1": c["y1"],
            "text": c.get("text", "")
        })
    
    with open("detected_cells.json", "w") as f:
        json.dump(cells, f, indent=2)
    print(f"Dumped {len(cells)} cells to detected_cells.json")

if __name__ == "__main__":
    dump_detected_cells(sys.argv[1])
