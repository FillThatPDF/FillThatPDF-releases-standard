import pikepdf

v21_path = "/Users/36981/Desktop/PDFTest/PDFs to test/Fillable PDFs/55570_DTE_SEEL_Contractor_Onboarding_Packet_v21_Web_Release_Fillable.pdf"

with pikepdf.open(v21_path) as pdf:
    for page_num in [32]: # Page 33 is index 32
        page = pdf.pages[page_num]
        if '/Annots' in page:
            for annot in page.Annots:
                if annot.get('/Subtype') == '/Widget':
                    ft = annot.get('/FT')
                    ff = annot.get('/Ff', 0)
                    t = annot.get('/T', 'Unnamed')
                    rect = [float(x) for x in annot.Rect]
                    
                    ff_val = int(ff)
                    is_pushbutton = (ff_val & 0x10000) != 0
                    is_radio = (ff_val & 0x8000) != 0
                    
                    if is_pushbutton or is_radio or "Yes" in str(t) or "No" in str(t):
                        print(f"Page {page_num+1} | Field: {t} | FT: {ft} | Ff: {ff} | Pushbutton: {is_pushbutton} | Radio: {is_radio} | Rect: {rect}")
                        if '/A' in annot:
                            print(f"  - Action: {annot.A}")
                        if '/AS' in annot:
                            print(f"  - Appearance State: {annot.AS}")
                        # Check parent if name is missing
                        if '/T' not in annot and '/Parent' in annot:
                            parent = annot.Parent
                            print(f"  - Parent Name: {parent.get('/T')}")
