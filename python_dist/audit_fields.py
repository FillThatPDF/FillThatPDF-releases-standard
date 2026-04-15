import pikepdf

pdf_path = "/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/FillThatPDF_Optimized_v3/python_dist/coned_round19_v1.pdf"

with pikepdf.open(pdf_path) as pdf:
    for i, page in enumerate(pdf.pages):
        print(f"--- Page {i+1} Fields ---")
        if "/Annots" in page:
            for annot in page.Annots:
                if annot.Subtype == "/Widget" and "/T" in annot:
                    name = str(annot.T)
                    rect = [float(x) for x in annot.Rect]
                    # Convert to pdfplumber/standard format (y0 is top)
                    # Pikepdf Rect is [xL, yB, xR, yT]
                    y0 = 792 - rect[3] # yTop to top-offset
                    y1 = 792 - rect[1] # yBottom to top-offset
                    print(f"Field: '{name}' | y={y0:.1f} to {y1:.1f} | h={y1-y0:.1f} | x={rect[0]:.1f}")
