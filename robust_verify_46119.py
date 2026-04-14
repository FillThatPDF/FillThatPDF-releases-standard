
import pdfplumber
import sys
import os

def robust_verify(pdf_path):
    print(f"Robust Verification of {pdf_path}...")
    if not os.path.exists(pdf_path):
        print(f"Error: {pdf_path} does not exist.")
        return

    try:
        with pdfplumber.open(pdf_path) as pdf:
            p1 = pdf.pages[0]
            # pdfplumber field extraction can sometimes be finicky with newly added/merged fields
            # Let's look at annots directly and also use the higher-level .form_fields if available
            
            annots = p1.annots or []
            print(f"Page 1 has {len(annots)} annotations (potential fields)")
            
            found_sjg = False
            found_muni = False
            
            for i, annot in enumerate(annots):
                # Print everything we can find about the name
                name = annot.get('T')
                if isinstance(name, bytes):
                    try: name = name.decode('utf-8')
                    except: pass
                
                rect = annot.get('Rect')
                
                # If name is None, it might be in the /T key or nested
                if name is None:
                    # Try raw dictionary access if possible
                    try:
                        name = annot['data'].get('T')
                        if isinstance(name, bytes): name = name.decode('utf-8')
                    except: pass

                if name:
                    print(f"  [{i}] Field Name: '{name}' Rect: {rect}")
                    if 'SJG' in str(name): found_sjg = True
                    if 'Municipal' in str(name): found_muni = True
                else:
                    # Sometimes researchers only look at the subtype or other markers
                    subtype = annot.get('Subtype')
                    # print(f"  [{i}] No name. Subtype: {subtype} Rect: {rect}")
                    pass
            
            if found_sjg: print("✅ SUCCESS: SJG field found.")
            else: print("❌ FAILURE: SJG field NOT found.")
            
            if found_muni: print("✅ SUCCESS: Municipal field found.")
            else: print("❌ FAILURE: Municipal field NOT found.")
            
            # If not found via annots, try a different extraction method
            if not (found_sjg and found_muni):
                print("\nAttempting alternative extraction via pypdf (if available via shell)...")

    except Exception as e:
        print(f"Error during verification: {e}")

if __name__ == "__main__":
    target = "output_46119_verify.pdf"
    robust_verify(target)
