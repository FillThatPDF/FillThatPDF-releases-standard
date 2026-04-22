import sys
import os
import json

# Add python_dist to path
sys.path.append(os.path.join(os.getcwd(), 'python_dist'))

try:
    from smart_fillable_v23 import UniversalPDFFillable
except ImportError:
    from smart_fillable_v23 import UniversalPDFFillable

def dump_fields():
    input_pdf = "46119_FINAL.pdf"
    
    # Run the detector but intercept the fields before PDF generation
    detector = UniversalPDFFillable(input_pdf, "dummy_output.pdf")
    
    # We need to run the full process to get the final fields
    # But since run() might take time and generate PDF, we can try to call internal methods
    # However, run() orchestrates everything.
    # Let's just run it and catch the fields from the instance if possible, 
    # OR we can just modify the script to print them.
    # Actually, let's just run it and print the fields at the end.
    
    try:
        detector.run()
        
        print("\n--- FINAL FIELDS DUMP ---")
        for f in detector.text_fields:
            if f['page'] in [0, 2]: # Page 1 and Page 3 (0-indexed)
                print(f"Page {f['page']+1}: Name='{f.get('name')}' Label='{f.get('label')}' Src='{f.get('source')}' Rect=[{f['x0']:.1f}, {f['y0']:.1f}, {f['x1']:.1f}, {f['y1']:.1f}] W={f['x1']-f['x0']:.1f} H={f['y1']-f['y0']:.1f}")
                
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    dump_fields()
