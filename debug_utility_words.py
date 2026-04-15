
import pdfplumber
import sys

def debug_utility_words(pdf_path):
    print(f"Debugging utility words in {pdf_path}")
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0] # Page 1
        words = page.extract_words()
        # Sort by y, then x
        words.sort(key=lambda w: (float(w['top']), float(w['x0'])))
        
        # Look for utility keywords
        utility_kws = ['sjg', 'municipal', 'electric', 'co.', 'atlantic', 'city', 'njng', 'elizabethtown', 'pseg']
        
        print(f"{'Text':<30} | {'x0':<10} | {'x1':<10} | {'top':<10} | {'bottom':<10}")
        print("-" * 80)
        for w in words:
            txt = w['text'].lower()
            if any(k in txt for k in utility_kws):
                print(f"{w['text']:<30} | {w['x0']:<10.1f} | {w['x1']:<10.1f} | {w['top']:<10.1f} | {w['bottom']:<10.1f}")

if __name__ == "__main__":
    debug_utility_words("46119_RECO_HVAC_Incentive_Claim_Form_v02.pdf")
