import json
import pdfplumber

pdf_path = "46119_RECO_HVAC_Incentive_Claim_Form_v02.pdf"
with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[0]
    words = page.extract_words()
    print("Words around y=230-245:")
    for w in words:
        if 230 < w['top'] < 250:
            print(f"Text: {w['text']:20} x0: {w['x0']:8.2f} x1: {w['x1']:8.2f} top: {w['top']:8.2f}")
