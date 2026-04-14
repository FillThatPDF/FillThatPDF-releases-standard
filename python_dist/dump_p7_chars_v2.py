import pdfplumber

pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/Batch 1 Forms/57643_DTE_MF_Measures_and_Specs_Income_Qualified_Catalog_v2.pdf"

print("Scanning Page 7 for characters...")
with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[6] # Page 7
    chars = page.chars
    
    for char in chars:
        y = char.get('top')
        text = char.get('text', '')
        font = char.get('fontname', '').lower()
        x0 = char.get('x0')
        if not text.isalnum() and text.strip():
            hex_val = hex(ord(text[0])) if text else 'N/A'
            print(f"Char: '{text}' (Hex: {hex_val}) Font: {font} at ({x0:.1f}, {y:.1f})")
