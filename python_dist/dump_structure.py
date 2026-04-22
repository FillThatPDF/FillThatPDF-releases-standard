import pdfplumber
import json
import sys

def dump_structure(input_pdf, output_json):
    data = {"pages": []}
    with pdfplumber.open(input_pdf) as pdf:
        for i, page in enumerate(pdf.pages):
            page_data = {
                "page": i + 1,
                "width": float(page.width),
                "height": float(page.height),
                "rects": [],
                "lines": [],
                "words": []
            }
            
            # Extract Rects
            for r in page.rects:
                page_data["rects"].append({
                    "x0": float(r["x0"]),
                    "top": float(r["top"]),
                    "x1": float(r["x1"]),
                    "bottom": float(r["bottom"]),
                    "width": float(r["width"]),
                    "height": float(r["height"]),
                    "non_stroking_color": r.get("non_stroking_color")
                })
                
            # Extract Lines
            for l in page.lines:
                page_data["lines"].append({
                    "x0": float(l["x0"]),
                    "top": float(l["top"]),
                    "x1": float(l["x1"]),
                    "bottom": float(l["bottom"]),
                    "width": float(l["width"]),
                    "height": float(l["height"])
                })
            
            # Extract Words (First few for context)
            for w in page.extract_words():
                page_data["words"].append({
                    "text": w["text"],
                    "x0": float(w["x0"]),
                    "top": float(w["top"]),
                    "x1": float(w["x1"]),
                    "bottom": float(w["bottom"])
                })
                
            data["pages"].append(page_data)
            
    with open(output_json, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Structure dumped to {output_json}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python dump_structure.py <input_pdf>")
        sys.exit(1)
    dump_structure(sys.argv[1], "pdf_structure.json")
