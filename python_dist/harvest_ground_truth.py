import pikepdf
from pdf2image import convert_from_path
import numpy as np
from pathlib import Path
import os
import cv2

def normalize(bbox, w, h):
    # bbox: [x0, y0, x1, y1] (Top-Left Origin)
    x0, y0, x1, y1 = bbox
    cw = (x1 - x0)
    ch = (y1 - y0)
    cx = x0 + cw/2
    cy = y0 + ch/2
    return cx/w, cy/h, cw/w, ch/h

def harvest():
    fillable_dir = Path("/Users/36981/Desktop/PDFTest/PDFs to test/Fillable PDFs")
    static_dir = Path("/Users/36981/Desktop/PDFTest/PDFs to test/Static PDFs")
    output_dir = Path("/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/training_data_gt")
    img_dir = output_dir / "images"
    lbl_dir = output_dir / "labels"
    
    img_dir.mkdir(parents=True, exist_ok=True)
    lbl_dir.mkdir(parents=True, exist_ok=True)
    
    dpi = 200
    
    fillable_files = list(fillable_dir.glob("*.pdf"))
    print(f"📂 Found {len(fillable_files)} fillable files.")
    
    total_pages = 0
    total_widgets = 0
    
    for f_path in fillable_files:
        # Find matching static
        # Logic: 9787_..._v01_fillable.pdf -> 9787_..._v01.pdf
        stem = f_path.stem.split("_Fillable")[0].split("_fillable")[0]
        s_path = static_dir / f"{stem}.pdf"
        
        if not s_path.exists():
            # Try fuzzy match
            prefix = stem[:5]
            matches = list(static_dir.glob(f"{prefix}*.pdf"))
            if matches:
                s_path = matches[0]
            else:
                print(f"⚠️ Skip: No static match for {f_path.name}")
                continue
        
        print(f"📝 Harvesting {f_path.name} -> {s_path.name}")
        
        try:
            images = convert_from_path(s_path, dpi=dpi)
            with pikepdf.open(f_path) as pdf:
                for i, page in enumerate(pdf.pages):
                    if i >= len(images): break
                    
                    pw, ph = float(page.MediaBox[2]), float(page.MediaBox[3])
                    img = np.array(images[i])
                    ih, iw = img.shape[:2]
                    
                    scale_x = iw / pw
                    scale_y = ih / ph
                    
                    labels = []
                    if "/Annots" in page:
                        for annot in page.Annots:
                            if annot.get("/Subtype") == "/Widget":
                                rect = [float(x) for x in annot.get("/Rect")]
                                x0, y0, x1, y1 = rect
                                # PDF Bottom-Up to Pixel Top-Down
                                ix0 = x0 * scale_x
                                ix1 = x1 * scale_x
                                iy0 = (ph - y1) * scale_y
                                iy1 = (ph - y0) * scale_y
                                
                                # class_id: 0:Text, 1:Checkbox, 2:Radio, 3:Dropdown
                                # Default to text (0) for now as we don't have types easily
                                # TODO: Map field types
                                cls_id = 0
                                if annot.get("/FT") == "/Btn":
                                    if b"/Radio" in bytes(annot.get("/Ff") or b""): cls_id = 2
                                    else: cls_id = 1
                                elif annot.get("/FT") == "/Ch": cls_id = 3
                                
                                labels.append(f"{cls_id} {' '.join(map(str, normalize([ix0, iy0, ix1, iy1], iw, ih)))}")
                                total_widgets += 1
                    
                    if labels:
                        # Save Image and Label
                        file_id = f"{stem}_p{i+1}"
                        cv2.imwrite(str(img_dir / f"{file_id}.jpg"), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
                        with open(lbl_dir / f"{file_id}.txt", "w") as lf:
                            lf.write("\n".join(labels))
                        total_pages += 1
        except Exception as e:
            print(f"❌ Error on {f_path.name}: {e}")

    print(f"\n✅ Harvested {total_widgets} fields across {total_pages} pages.")

if __name__ == "__main__":
    harvest()
