
import sys
import os
import json

# Add the directory containing smart_fillable_v23 to sys.path
sys.path.append('python_dist')
from smart_fillable_v23 import UniversalPDFFillable

def analyze_pdf(pdf_path):
    # Use normal settings
    settings = {
        'enable_global_single_line_height': True,
        'global_single_line_height': 0.12, # 8.64pt
    }
    
    # v23.59 Test: debug=True to see [OVERRIDE] and [TRACE-LINE]
    # Merge existing settings with debug setting
    merged_settings = {**settings, 'debug': True}
    tool = UniversalPDFFillable(pdf_path, settings=merged_settings)
    tool.run()
    
    print("\n--- Page 1 Text Fields ---")
    p1_fields = [f for f in tool.text_fields if f['page'] == 0]
    
    # Sort by Y then X
    p1_fields.sort(key=lambda x: (x['y0'], x['x0']))
    
    for f in p1_fields:
        h = f['y1'] - f['y0']
        w = f['x1'] - f['x0']
        name = f.get('name', 'N/A')
        source = f.get('source', 'N/A')
        print(f"Name: {name:40} Src: {source:20} H: {h:6.2f} W: {w:6.2f} Bbox: [{f['x0']:.1f}, {f['y0']:.1f}, {f['x1']:.1f}, {f['y1']:.1f}]")

if __name__ == "__main__":
    pdf_path = "46119_RECO_HVAC_Incentive_Claim_Form_v02.pdf"
    if os.path.exists(pdf_path):
        analyze_pdf(pdf_path)
    else:
        print(f"File not found: {pdf_path}")
