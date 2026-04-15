import os
import pdfplumber

def check_motors_font():
    pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/Batch 1 Forms/Motors 02-04-10kt.pdf"
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        # Find 'c' in 'Account' around y=161
        chars = page.chars
        target_chars = [c for c in chars if 160 < c['top'] < 165 and c['text'] == 'c']
        
        for tc in target_chars:
            print(f"Target 'c' at x={tc['x0']:.1f} width={tc['width']:.1f}")
            # Check neighbors
            for c in chars:
                if c is tc: continue
                if abs(c['top'] - tc['top']) < 2:
                    dist_left = tc['x0'] - c['x1']
                    dist_right = c['x0'] - tc['x1']
                    
                    if 0 < dist_left < 3:
                        print(f"  Left neighbor '{c['text']}' dist={dist_left:.2f}")
                    if 0 < dist_right < 3:
                        print(f"  Right neighbor '{c['text']}' dist={dist_right:.2f}")

if __name__ == "__main__":
    check_motors_font()
