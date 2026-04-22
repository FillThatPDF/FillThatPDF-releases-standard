
import pdfplumber

def debug_p1_cell():
    pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/New Test Forms/11691_ConEd_Distributor_Application_Form_v10_FILLABLE.pdf"
    
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        words = page.extract_words()
        
        # Search for "Distributor Name" or just "Name" after "Distributor"
        target_word = None
        for i, w in enumerate(words):
            if "Distributor" in w['text'] and i+1 < len(words) and "Name" in words[i+1]['text']:
                if float(w['top']) > 200: # Below header
                    target_word = w
                    break
        
        if not target_word:
            print("Could not find 'Distributor Name' label")
            # fallback to just finding "Name" approx
            for w in words:
                 if "Name" in w['text'] and float(w['top']) > 200:
                     target_word = w
                     break
        
        if not target_word:
            print("Could not find 'Distributor Name'")
            return
            
        print(f"Found 'Distributor': {target_word}")
        
        # Define a test cell around it (visual grid simulation)
        # x0=36, y0 ~ target top, x1=576, y1 ~ target bottom + height
        cy0 = float(target_word['top']) - 2
        cy1 = float(target_word['bottom']) + 15 # Assuming tall cell
        cx0 = 36
        cx1 = 576
        
        print(f"Analyzing Mock Cell: {cx0}, {cy0}, {cx1}, {cy1}")
        
        cell_crop = page.within_bbox((cx0, cy0, cx1, cy1))
        cell_text = cell_crop.extract_text()
        cell_words = cell_crop.extract_words()
        
        print(f"Cell Text: '{cell_text}'")
        print(f"Cell Words: {len(cell_words)}")
        for w in cell_words:
            print(f"  Word: '{w['text']}' x0={w['x0']:.2f} x1={w['x1']:.2f}")
            
        # Simulate logic
        text_r_edge = max(float(w['x1']) for w in cell_words)
        print(f"Calculated text_r_edge: {text_r_edge:.2f}")
        
        label_width = text_r_edge - cx0
        cell_width = cx1 - cx0
        print(f"Label Width: {label_width:.2f}")
        print(f"Cell Width: {cell_width:.2f}")
        print(f"Ratio: {label_width/cell_width:.2f}")

if __name__ == "__main__":
    debug_p1_cell()
