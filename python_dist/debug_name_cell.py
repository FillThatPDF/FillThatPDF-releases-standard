import pdfplumber
import sys

pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/New Test Forms/9787_ConEd_Res HVAC Electric Rebate Appl_v01_FINAL_RELEASE_NOEDIT_FILLABLE.pdf"

with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[0]
    
    # Define the bbox for the Name cell (approx)
    # Based on PDF visual: Left side, Y~260
    # Let's find table cells in that area
    tables = page.find_tables()
    for table in tables:
        for cell in table.cells:
            x0, y0, x1, y1 = cell
            # Check if this is the Name cell (Y around 260-270)
            if 250 < y0 < 280 and x0 < 100:
                print(f"Checking Cell at ({x0:.1f}, {y0:.1f}) - ({x1:.1f}, {y1:.1f})")
                text = (page.within_bbox(cell).extract_text() or "").strip()
                print(f"  Full Text: {repr(text)}")
                
                # Replicate the logic
                first_line = text.split('\n')[0].strip()
                print(f"  First Line: {repr(first_line)}")
                print(f"  Lower First Line: {repr(first_line.lower())}")
                print(f"  StartsWith 'name': {first_line.lower().startswith('name')}")
                
                # Check other params
                cx0, cy0, cx1, cy1 = cell
                cell_w = cx1 - cx0
                cell_h = cy1 - cy0
                first_line_len = len(first_line.replace(' ', ''))
                estimated_text_width = first_line_len * 6
                text_len = len(text)
                text_area_ratio = (estimated_text_width * 12) / (cell_w * cell_h) if cell_w * cell_h > 0 else 1
                
                print(f"  Cell W: {cell_w:.1f}, Cell H: {cell_h:.1f}")
                print(f"  Text Len: {text_len}")
                print(f"  Est Text Width: {estimated_text_width:.1f}")
                print(f"  Limit (1.5x): {estimated_text_width * 1.5:.1f}")
                print(f"  Cell W > Limit?: {cell_w > estimated_text_width * 1.5}")
                print(f"  Text Area Ratio: {text_area_ratio:.2f}")
                print(f"  Ratio < 0.4?: {text_area_ratio < 0.4}")
                
                is_form_input = (
                    (text_len < 25 or first_line.lower().startswith('name')) and cell_h > 18 and (
                        cell_w > estimated_text_width * 1.5 or
                        text_area_ratio < 0.4
                    )
                )
                print(f"  IS FORM INPUT? {is_form_input}")
