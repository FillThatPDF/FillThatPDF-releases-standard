
import fitz
import json
import sys
import os

# Add the directory containing smart_fillable_v23 to sys.path
sys.path.append('python_dist')
from smart_fillable_v23 import UniversalPDFFillable

def analyze_pdf(pdf_path):
    settings = {
        'enable_global_single_line_height': True,
        'global_single_line_height': 0.12, # 8.64pt
        'debug_pages': [1] # Page indices are 0-based, so 0 is P1, 1 is P2
    }
    
    # We need to run the detection to see the sources
    tool = UniversalPDFFillable(pdf_path, settings=settings)
    tool.run()
    
    print("\n--- Page 2 Text Fields (Index 1) ---")
    p2_fields = [f for f in tool.text_fields if f['page'] == 1]
    for f in p2_fields:
        h = f['y1'] - f['y0']
        name = f.get('name', 'N/A')
        source = f.get('source', 'N/A')
        print(f"Name: {name:40} Source: {source:25} Height: {h:6.2f}pt Bbox: {f['x0']:.1f}, {f['y0']:.1f}, {f['x1']:.1f}, {f['y1']:.1f}")

if __name__ == "__main__":
    pdf_path = "9787_ConEd_Res_HVAC_Electric_Rebate_Appl_v01_FINAL_RELEASE_NOEDIT_fillable.pdf"
    if os.path.exists(pdf_path):
        analyze_pdf(pdf_path)
    else:
        print(f"File not found: {pdf_path}")
