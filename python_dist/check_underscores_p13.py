
import pdfplumber

pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/Static PDFs/55570_DTE_SEEL_Contractor_Onboarding _Packet_v26.pdf"

with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[12] # Page 13
    text = page.extract_text()
    print(f"Page 13 Underscore Check:")
    if '_' in text:
        print(f"  Found underscores in text!")
        # Find some underscores and print their coords
        words = page.extract_words()
        for w in words:
            if '_' in w['text']:
                print(f"  Underscore Word: '{w['text']}' at {w['x0'], w['top'], w['x1'], w['bottom']}")
    else:
        print("  NO underscores found in text.")
    
    # Check for Pipe symbols (tables)
    if '|' in text:
        print("  Found pipes in text!")
