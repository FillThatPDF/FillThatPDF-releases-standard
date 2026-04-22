
import subprocess
import pdfplumber
import sys
import os

def check_46119():
    input_pdf = "46119_RECO_HVAC_Incentive_Claim_Form_v02.pdf"
    output_pdf = "output_46119_verify.pdf"
    
    print(f"Running smart_fillable_v23.py on {input_pdf}...")
    try:
        subprocess.run([
            "python3", "python_dist/smart_fillable_v23.py", 
            input_pdf, 
            output_pdf
        ], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error running smart_fillable: {e}")
        return

    print(f"\nVerifying {output_pdf}...")

    # Helper to get value with multiple key checks
    def get_val(obj, key_str):
        if not isinstance(obj, dict): return None
        
        # Try direct key
        v = obj.get(key_str)
        if v is not None: return v
        
        # Try byte key
        try: v = obj.get(key_str.encode('utf-8'))
        except: pass
        if v is not None: return v
        
        # Try specific PDF keys (e.g. /T, /Rect)
        # pdfplumber often returns keys as /Key or Key
        for k in [key_str, '/' + key_str]:
            v = obj.get(k)
            if v is not None: return v
            try: v = obj.get(k.encode('utf-8'))
            except: pass
            if v is not None: return v
        
        # Check 'data' dict if present
        if 'data' in obj and isinstance(obj['data'], dict):
            return get_val(obj['data'], key_str)
            
        return None

    with pdfplumber.open(output_pdf) as pdf:
        p1 = pdf.pages[0]
        fields = p1.annots or []
        print(f"Page 1 has {len(fields)} fields")
        
        found_sjg = False
        found_muni = False
        
        for i, f in enumerate(fields):
            # Debug: print first field details
            if i == 0:
                print(f"Sample field type: {type(f)}")
                if isinstance(f, dict):
                    print(f"Sample field keys: {list(f.keys())}")
                    # Print values for T and Rect specifically
                    print(f"Sample T: {get_val(f, 'T')}")
                    print(f"Sample Rect: {get_val(f, 'Rect')}")

            name = get_val(f, 'T')
            rect = get_val(f, 'Rect')
            
            # safe decode name
            if isinstance(name, bytes):
                try: name = name.decode('utf-8')
                except: name = str(name)
            if name is None: name = ""

            # Check for SJG
            if 'SJG' in name: found_sjg = True
            if 'Municipal' in name: found_muni = True

        if found_sjg: print("✅ Found SJG field")
        else: print("❌ SJG field NOT found")
        
        if found_muni: print("✅ Found Municipal field")
        else: print("❌ Municipal field NOT found")

        print("\n--- All Fields on Page 1 ---")
        for f in fields:
            name = get_val(f, 'T')
            rect = get_val(f, 'Rect')
            source = get_val(f, 'source')
            # safe decode name
            if isinstance(name, bytes):
                try: name = name.decode('utf-8')
                except: name = str(name)
            print(f"  Field: {name} Rect: {rect} Source: {source}")

if __name__ == "__main__":
    check_46119()
