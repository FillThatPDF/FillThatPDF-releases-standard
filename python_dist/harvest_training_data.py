import os
import argparse
import pikepdf
from pdf2image import convert_from_path
import cv2
import numpy as np
import json
from pathlib import Path

def normalize_coords(bbox, width, height):
    """
    Convert PDF coordinates [x0, y0, x1, y1] to YOLO format [x_center, y_center, width, height] (normalized 0-1).
    """
    # PDF Coords: x0, y0 (bottom-left), x1, y1 (top-right) usually
    # BUT pikepdf /Rect is [x_ll, y_ll, x_ur, y_ur]
    # We need to handle coordinate flips if necessary. 
    # Usually PDF y=0 is bottom. Image y=0 is top.
    
    x0, y0, x1, y1 = bbox
    
    # Calculate PDF height from the crop box or media box if available, otherwise passed in
    # Assuming input 'bbox' is already in a coordinate system consistent with 'width' and 'height'
    # For now, let's assume bbox is strictly [min_x, min_y, max_x, max_y] regardless of origin
    
    min_x = min(x0, x1)
    max_x = max(x0, x1)
    min_y = min(y0, y1)
    max_y = max(y0, y1)
    
    # Calculate Center and Size
    box_w = max_x - min_x
    box_h = max_y - min_y
    center_x = min_x + (box_w / 2)
    center_y = min_y + (box_h / 2)
    
    # Normalize
    n_center_x = center_x / width
    n_center_y = center_y / height
    n_width = box_w / width
    n_height = box_h / height
    
    return n_center_x, n_center_y, n_width, n_height

def process_pdf(pdf_path, output_dir):
    filename = Path(pdf_path).stem
    print(f"Processing: {pdf_path}")
    
    try:
        # 1. Load PDF Struct (for annotations)
        pdf = pikepdf.open(pdf_path)
        
        # 2. Render Images (High Res)
        # Using 300 DPI for sufficient detail? Or 72 DPI to match default PDF scale?
        # Better to match resolution. YOLO scales images anyway. Let's stick to 200 DPI for clarity.
        images = convert_from_path(pdf_path, dpi=200)
        
        for i, page in enumerate(pdf.pages):
            page_num = i + 1
            
            # Get Page Dimensions (MediaBox)
            mbox = page.MediaBox
            pdf_w = float(mbox[2])
            pdf_h = float(mbox[3])
            
            # Image Dimensions
            img = images[i]
            img_w, img_h = img.size
            
            # Save Image
            image_filename = f"{filename}_page_{page_num}.jpg"
            image_path = output_dir / "images" / image_filename
            img.save(image_path, "JPEG")
            
            # Extract Widgets
            labels = []
            page_metadata = [] # List of dicts
            
            if "/Annots" in page:
                for annot in page.Annots:
                    if annot.Type == "/Annot" and annot.Subtype == "/Widget":
                        # Get Rect
                        rect = [float(x) for x in annot.Rect]
                        
                        # Determine Class & Properties
                        # 0: Text, 1: Checkbox, 2: Radio, 3: Dropdown
                        cls_id = 0 
                        field_type = "Text"
                        
                        # Check FT (Field Type)
                        ft = str(annot.FT) if "/FT" in annot else ""
                        ff = int(annot.Ff) if "/Ff" in annot else 0
                        
                        is_btn = (ft == "/Btn")
                        is_ch = (ft == "/Ch")
                        
                        if is_btn:
                            # Check flags for Radio (15th bit?? No, use Ff)
                            # Ff 16 (0x8000) = Radio
                            # Ff 17 (0x10000) = Pushbutton
                            if (ff & 32768): # Radio
                                cls_id = 2
                                field_type = "Radio"
                            elif (ff & 65536): # Pushbutton
                                continue # Skip buttons?
                            else:
                                cls_id = 1
                                field_type = "Checkbox"
                        elif is_ch:
                            # Choice: Combo (Dropdown) or List
                            # Ff 18 (0x20000) = Combo
                            if (ff & 131072):
                                cls_id = 3
                                field_type = "Dropdown"
                            else:
                                cls_id = 3 # Treat list as dropdown for now
                                field_type = "List"
                        
                        # COORDINATE TRANSFORM
                        # PDF Coords: (0,0) is Bottom-Left. 
                        # Image Coords: (0,0) is Top-Left.
                        # Pikepdf Rect: [x_left, y_bottom, x_right, y_top]
                        
                        x_left, y_bottom, x_right, y_top = rect
                        
                        # Convert Y to Top-Down
                        # y_top_new = pdf_h - y_top
                        # y_bottom_new = pdf_h - y_bottom
                        
                        new_y_top = pdf_h - y_top
                        new_y_bottom = pdf_h - y_bottom
                        
                        # Final Box [min_x, min_y, max_x, max_y] in Top-Down
                        final_box = [x_left, new_y_top, x_right, new_y_bottom]
                        
                        # Normalize
                        nx, ny, nw, nh = normalize_coords(final_box, pdf_w, pdf_h)
                        
                        # Save Label (YOLO)
                        labels.append(f"{cls_id} {nx:.6f} {ny:.6f} {nw:.6f} {nh:.6f}")
                        
                        # Save Metadata (Rich Info)
                        meta = {
                            "id": f"field_{len(labels)}",
                            "class_id": cls_id,
                            "type": field_type,
                            "rect": final_box, # Normalized [x,y,w,h] might be better?
                            "rect_norm": [nx, ny, nw, nh],
                            "name": str(annot.T) if "/T" in annot else "",
                            "tooltip": str(annot.TU) if "/TU" in annot else "",
                            "default_value": str(annot.V) if "/V" in annot else "",
                            "appearance": str(annot.DA) if "/DA" in annot else "",
                            "flags": ff
                        }
                        page_metadata.append(meta)
            
            # Save Label File
            if labels:
                label_filename = f"{filename}_page_{page_num}.txt"
                label_path = output_dir / "labels" / label_filename
                with open(label_path, "w") as f:
                    f.write("\n".join(labels))
                    
                # Save Metadata JSON
                meta_filename = f"{filename}_page_{page_num}.json"
                meta_path = output_dir / "labels" / meta_filename
                with open(meta_path, "w") as f:
                    json.dump(page_metadata, f, indent=2)
                    
        print(f"   Saved {len(images)} pages.")
        
    except Exception as e:
        print(f"   Error processing {pdf_path}: {e}")

def main():
    parser = argparse.ArgumentParser(description="Harvest training data from fillable PDFs.")
    parser.add_argument("input_dir", help="Directory containing perfectly filled PDFs")
    parser.add_argument("output_dir", help="Directory to save dataset (images/labels)")
    args = parser.parse_args()
    
    input_path = Path(args.input_dir)
    output_path = Path(args.output_dir)
    
    # Setup Dirs
    (output_path / "images").mkdir(parents=True, exist_ok=True)
    (output_path / "labels").mkdir(parents=True, exist_ok=True)
    
    # Process
    if input_path.is_file():
        if input_path.suffix.lower() == ".pdf":
            process_pdf(input_path, output_path)
    elif input_path.is_dir():
        pdfs = list(input_path.glob("*.pdf"))
        print(f"Found {len(pdfs)} PDFs in {input_path}")
        for p in pdfs:
            process_pdf(p, output_path)
            
            
    # create classes.txt
    with open(output_path / "classes.txt", "w") as f:
        f.write("Text Field\nCheckbox\nRadio Button\nDropdown")
        
    print(f"\nDone! Dataset saved to {output_path}")

if __name__ == "__main__":
    main()
