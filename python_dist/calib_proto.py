
import numpy as np

def calibrate_page(scanner, page_num, img):
    """
    Find the linear transform (scale, offset) for a page
    by matching PDF text words to visual text areas.
    """
    pdf_words = scanner.pdf_text_data[page_num]['words']
    if not pdf_words:
        return 0.36, 0, 0 # Fallback
        
    # 1. Get Top/Bottom words from PDF
    pdf_words_sorted = sorted(pdf_words, key=lambda w: w['top'])
    top_w = pdf_words_sorted[0]
    bot_w = pdf_words_sorted[-1]
    
    # 2. Find these words in the IMAGE (using a simple text-mask)
    # Actually, we can just use the middle 10 words to be robust
    # But wait, I don't have OCR.
    
    # NEW IDEA: Use the Bounding Box of ALL text.
    pdf_top = min(w['top'] for w in pdf_words)
    pdf_bot = max(w['bottom'] for w in pdf_words)
    pdf_h = scanner.pdf_text_data[page_num]['height']
    
    # Scan the image to find actual pixel boundaries of content
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 240, 255, cv2.THRESH_BINARY_INV)
    coords = np.column_stack(np.where(thresh > 0))
    if coords.size == 0:
        return 0.36, 0, 0
    
    pix_top = coords[:, 0].min()
    pix_bot = coords[:, 0].max()
    
    # Map pix_top -> pdf_top
    # Map pix_bot -> pdf_bot
    # Scale = (pdf_bot - pdf_top) / (pix_bot - pix_top)
    # Offset = pdf_top - (pix_top * Scale)
    
    scale = (pdf_bot - pdf_top) / (pix_bot - pix_top)
    offset_y = pdf_top - (pix_top * scale)
    
    print(f"   [CALIB] Page {page_num+1}: Scale={scale:.4f}, Offset={offset_y:.2f}")
    return scale, 0, offset_y
