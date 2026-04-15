
import pdfplumber
import cv2
import numpy as np
from pdf2image import convert_from_path

pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/Static PDFs/55570_DTE_SEEL_Contractor_Onboarding _Packet_v26.pdf"

with pdfplumber.open(pdf_path) as pdf:
    for page_idx in [0, 12, 44]: # P1, P13, P45
        page = pdf.pages[page_idx]
        images = convert_from_path(pdf_path, first_page=page_idx+1, last_page=page_idx+1, dpi=200)
        img = np.array(images[0])
        img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        
        # Scale: 72 / 200 = 0.36 pt/px
        # Inverse: 200 / 72 = 2.77 px/pt
        scale_inv = 200 / 72
        
        words = page.extract_words()
        for w in words:
            # Word Box in PDF Points (Top-Down)
            # pdfplumber words are top-down!
            wx0, wy0, wx1, wy1 = w['x0'], w['top'], w['x1'], w['bottom']
            
            # Map to Pixels
            px0 = int(wx0 * scale_inv)
            py0 = int(wy0 * scale_inv)
            px1 = int(wx1 * scale_inv)
            py1 = int(wy1 * scale_inv)
            
            # Draw on image
            cv2.rectangle(img, (px0, py0), (px1, py1), (0, 0, 255), 1)
            # Draw label
            # cv2.putText(img, w['text'], (px0, py0-2), cv2.FONT_HERSHEY_SIMPLEX, 0.3, (0, 0, 255), 1)
            
        out_path = f"alignment_p{page_idx+1}.jpg"
        cv2.imwrite(out_path, img)
        print(f"Saved Alignment Map: {out_path}")
