
import subprocess
import logging
import sys
import os

logging.basicConfig(level=logging.INFO)

def run_smart_fillable():
    cwd = os.getcwd()
    script_path = os.path.join(cwd, "python_dist", "smart_fillable_v23.py")
    input_pdf = os.path.join(cwd, "input_53252.pdf")
    output_pdf = os.path.join(cwd, "output_verify.pdf")
    
    if not os.path.exists(script_path):
        logging.error(f"Script not found: {script_path}")
        return False
    if not os.path.exists(input_pdf):
        logging.error(f"Input PDF not found: {input_pdf}")
        # Try finding it in expected location
        alt_path = "/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/_OLD/53252_PDFTEST_FILLABLE.pdf"
        if os.path.exists(alt_path):
             logging.info(f"Copying input from {alt_path}")
             import shutil
             shutil.copy(alt_path, input_pdf)
        else:
             return False

    cmd = [sys.executable, script_path, input_pdf, output_pdf]
    logging.info(f"Running command: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, check=True)
        return True
    except subprocess.CalledProcessError as e:
        logging.error(f"Error running smart_fillable: {e}")
        return False

def check_pdf(pdf_path):
    try:
        import pdfplumber
    except ImportError:
        logging.error("pdfplumber not installed")
        return

    with pdfplumber.open(pdf_path) as pdf:
        # Page 1 (Index 0)
        p1 = pdf.pages[0]
        # pdfplumber < 0.10 uses .annots, newer uses .annots too but structure varies
        # Let's try extracting annotations.
        # Note: pdfplumber sometimes doesn't parse annots well.
        # But let's see.
        
        # Alternatively, run smart_fillable in debug mode?
        # Or parse the debug logs?
        # smart_fillable usually logs to /tmp/smart_fillable_debug.log
        pass

if __name__ == "__main__":
    if run_smart_fillable():
        print("Smart Fillable run completed.")
        # We rely on reading the logs or output.
        # Actually proper verification via pdfplumber is better.
