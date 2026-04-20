
import pdfplumber
import json

def dump_lines_and_cells(pdf_path, page_idx, y_min, y_max):
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_idx]
        
        print(f"--- Page {page_idx+1} Lines (Y: {y_min}-{y_max}) ---")
        lines = page.lines
        for line in lines:
            if y_min <= line['top'] <= y_max:
                print(f"LINE: x0={line['x0']:.1f} top={line['top']:.1f} x1={line['x1']:.1f} bottom={line['bottom']:.1f} width={line['width']:.2f}")
        
        print(f"\n--- Page {page_idx+1} Tables (Y: {y_min}-{y_max}) ---")
        tables = page.find_tables()
        for i, table in enumerate(tables):
            bbox = table.bbox
            if not (bbox[1] > y_max or bbox[3] < y_min):
                print(f"TABLE {i}: Bbox={bbox}")
                for r_idx, row in enumerate(table.rows):
                    print(f"  ROW {r_idx} Bbox={row.bbox}")
                    for c_idx, cell in enumerate(row.cells):
                        if cell:
                             # cell is (x0, y0, x1, y1)
                             print(f"    CELL {c_idx} Bbox={cell}")

if __name__ == "__main__":
    pdf_path = "46119_RECO_HVAC_Incentive_Claim_Form_v02.pdf"
    # Gas/Electric area is around Y=500 to 560
    dump_lines_and_cells(pdf_path, 0, 480, 580)
