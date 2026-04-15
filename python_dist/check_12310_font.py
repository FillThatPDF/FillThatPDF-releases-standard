import os
import pdfplumber

def check_12310_font():
    pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/Batch 2 Forms/12310_ConEd_Res_Appliance_Application_v03_FINAL_RELEASE_print.pdf"
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        # Find 'c' 
        # "How did you find out about this program?" is usually around y=500+?
        # Let's just find any 'c' that looks isolated
        chars = page.chars
        
        candidates = [c for c in chars if c['text'] == 'c' and c['top'] > 400]
        print(f"Found {len(candidates)} 'c' candidates > y=400")
        for i, c in enumerate(candidates[:20]):
            print(f"[{i}] x={c['x0']:.1f} y={c['top']:.1f} font='{c['fontname']}'")
            
            # Check neighbors
            has_L = False
            has_R = False
            for other in chars:
                if other is c: continue
                if abs(other['top'] - c['top']) < 2:
                    if 0 < c['x0'] - other['x1'] < 4:
                        print(f"   Left neighbor: '{other['text']}' dist={c['x0'] - other['x1']:.2f}")
                        has_L = True
                    if 0 < other['x0'] - c['x1'] < 4:
                        print(f"   Right neighbor: '{other['text']}' dist={other['x0'] - c['x1']:.2f}")
                        has_R = True
            
            if not has_L and not has_R:
                print("   ISOLATED!")

if __name__ == "__main__":
    check_12310_font()
