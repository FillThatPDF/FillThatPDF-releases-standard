import pikepdf
import sys

pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/Batch 1 Forms/32987_DEBUG_V5.pdf"
with pikepdf.open(pdf_path) as pdf:
    # Check AcroForm
    if '/AcroForm' in pdf.Root:
        af = pdf.Root.AcroForm
        if '/Fields' in af:
            fields = af.Fields
            print(f"Total fields in AcroForm: {len(fields)}")
            for i, f in enumerate(fields):
                name = str(f.get('/T', 'Unnamed'))
                if "Number" in name or "Comments" in name:
                    print(f"\n--- Field index {i}: {name} ---")
                    print(f"Rect: {f.get('/Rect')}")
                    print(f"FT: {f.get('/FT')}")
                    if '/MK' in f:
                        print(f"MK: {f.MK}")
                    else:
                        print("MK: MISSING")
                    print(f"DA: {f.get('/DA')}")
                    print(f"F: {f.get('/F')}")
                    print(f"Ff: {f.get('/Ff')}")
                    # Check Page reference
                    # (Usually fields in Fields array are indirect objects found on pages)
    
    # Check Page 0 Annots
    p0 = pdf.pages[0]
    if '/Annots' in p0:
        print(f"\nPage 0 Annots: {len(p0.Annots)}")
        for i, ann in enumerate(p0.Annots):
             if ann.get('/Subtype') == '/Widget':
                 name = str(ann.get('/T', 'Unnamed'))
                 if "Number" in name:
                     print(f"Annot {i}: {name} Rect={ann.Rect}")

