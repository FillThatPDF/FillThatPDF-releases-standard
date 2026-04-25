
import pikepdf
import sys

def inspect_pdf(pdf_path):
    print(f"Inspecting {pdf_path}...")
    with pikepdf.open(pdf_path) as pdf:
        annots = pdf.pages[0].get('/Annots')
        if not annots:
            print("No annotations found.")
            return
        
        for i, annot in enumerate(annots):
            if annot.get('/Subtype') == '/Widget':
                t = annot.get('/T')
                tu = annot.get('/TU')
                rect = annot.get('/Rect')
                print(f"Field {i}: Name='{t}', Tooltip='{tu}', Rect={rect}")

if __name__ == "__main__":
    inspect_pdf(sys.argv[1])
