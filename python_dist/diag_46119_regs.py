import sys
import os

# Add the directory to sys.path
sys.path.append('/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/FillThatPDF_Optimized_v11/python_dist')

from smart_fillable_v23 import UniversalPDFFillable as SmartFillable

def run_diagnostic():
    pdf_path = '/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/FillThatPDF_Optimized_v11/46119_RECO_HVAC_Incentive_Claim_Form_v02.pdf'
    output_path = '/Users/36981/Desktop/PDFTest/46119_output.pdf'
    
    print(f"Running diagnostic on {pdf_path}...")
    
    # Use the smart fillable engine
    engine = SmartFillable(pdf_path)
    
    # Redirect stdout to capture logs
    import io
    log_stream = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = log_stream
    
    try:
        engine.run()
        # We don't necessarily need to save the PDF, just see the logs
    finally:
        sys.stdout = old_stdout
    
    logs = log_stream.getvalue()
    with open('debug_46119_new_diag.log', 'w') as f:
        f.write(logs)
    
    print("Diagnostic complete. Logs saved to debug_46119_new_diag.log")
    
    # Print summary of fields on P1 and P3
    print("\nPage 1 Fields:")
    for f in engine.text_fields:
        if f['page'] == 0:
            print(f"  {f['name']} (label: {f.get('label')}, src: {f.get('source')}, x0: {f['x0']:.1f}, x1: {f['x1']:.1f})")
            
    print("\nPage 3 Fields:")
    for f in engine.text_fields:
        if f['page'] == 2:
             print(f"  {f['name']} (label: {f.get('label')}, src: {f.get('source')}, x0: {f['x0']:.1f}, x1: {f['x1']:.1f})")

if __name__ == "__main__":
    run_diagnostic()
