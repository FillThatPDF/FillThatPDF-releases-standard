import pikepdf
import sys

def check_pdf(filename, page_idx):
    print(f"\n--- Checking {filename} (Page {page_idx+1}) ---")
    with pikepdf.open(filename) as pdf:
        page = pdf.pages[page_idx]
        if '/Annots' not in page:
            print("No annotations found on this page.")
            return
            
        fields = []
        for annot in page.Annots:
            if annot.Subtype == '/Widget' and '/T' in annot:
                name = str(annot.T)
                rect = [float(x) for x in annot.Rect]
                tu = str(annot.get('/TU', ''))
                fields.append({'name': name, 'rect': rect, 'tu': tu})
        
        # Sort by Y descending
        fields.sort(key=lambda x: x['rect'][1], reverse=True)
        
        for f in fields:
            print(f"Field: {f['name']:35} Rect: {f['rect']} Tooltip: {f['tu']}")

print("Full Field List for 46119 (Page 1):")
check_pdf("output_verify_46119.pdf", 0)
