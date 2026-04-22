import sys
import os
import logging

# Add python_dist to path
sys.path.append(os.path.join(os.getcwd(), 'python_dist'))

try:
    from smart_fillable_v23 import UniversalPDFFillable
except ImportError:
    # Try importing without extension if it was installed as a module
    from smart_fillable_v23 import UniversalPDFFillable

def run_reproduction():
    input_pdf = "46119_FINAL.pdf"
    output_pdf = "46119_repro_output.pdf"
    
    print(f"Running reproduction on {input_pdf}...")
    
    if not os.path.exists(input_pdf):
        print(f"Error: {input_pdf} not found.")
        return

    # Initialize the processor
    # We want to see debug logs, so we might need to configure logging if the class doesn't do it to stdout
    # The class configures logging to /tmp/smart_fillable_debug.log by default in line 47
    
    processor = UniversalPDFFillable(input_pdf, output_pdf)
    
    try:
        processor.run()
        print(f"Reproduction complete. Output saved to {output_pdf}")
    except Exception as e:
        print(f"An error occurred: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    run_reproduction()
