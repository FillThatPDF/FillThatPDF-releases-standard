import pdfplumber

pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/Batch 1 Forms/57643_DTE_MF_Measures_and_Specs_Income_Qualified_Catalog_v2.pdf"

print("Scanning Page 13 for characters...")
with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[12] # Page 13
    chars = page.chars
    
    # Filter for characters that look like boxes or are in special fonts
    potential_boxes = []
    
    for char in chars:
        font = char.get('fontname', '').lower()
        text = char.get('text', '')
        size = char.get('size', 0)
        x0, y0 = char.get('x0'), char.get('top')
        
        if not text.isalnum() or 'ding' in font or 'sym' in font or size > 10:
             hex_val = hex(ord(text[0])) if text else 'N/A'
             print(f"Char: '{text}' (Len: {len(text)}, Hex: {hex_val}) Font: {font} Size: {size:.1f} at ({x0:.1f}, {y0:.1f})")
