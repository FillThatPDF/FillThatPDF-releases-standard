
import pdfplumber
import sys

def check_pdf(pdf_path):
    print(f"Checking {pdf_path}...")
    try:
        with pdfplumber.open(pdf_path) as pdf:
            p1 = pdf.pages[0]
            fields = p1.annots or []
            print(f"Page 1 has {len(fields)} fields")
            
            sjg_found = False
            muni_found = False
            
            for f in fields:
                t = f.get('T')
                if isinstance(t, bytes):
                    try: t = t.decode('utf-8')
                    except: pass
                
                print(f"Field: {t} Rect: {f.get('Rect')}")
                
                if t and 'SJG' in str(t): sjg_found = True
                if t and 'Municipal' in str(t): muni_found = True
                
            if sjg_found: print("✅ Found SJG")
            else: print("❌ SJG Not Found")
            
            if muni_found: print("✅ Found Municipal")
            else: print("❌ Municipal Not Found")
            
    except Exception as e:
        print(f"Error opening PDF: {e}")

if __name__ == "__main__":
    check_pdf("output_46119_verify.pdf")
