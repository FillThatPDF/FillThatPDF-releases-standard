#!/usr/bin/env python3
"""
Run v22 on test PDFs and analyze errors.
"""
import subprocess
import os
import sys

# Add the python_dist to path
sys.path.insert(0, '/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/FillThatPDF_Optimized_v7/python_dist')

STATIC_DIR = "/Users/36981/Desktop/PDFTest/PDFs to test/Static PDFs"
FILLABLE_DIR = "/Users/36981/Desktop/PDFTest/PDFs to test/Fillable PDFs"

PDF_PAIRS = [
    ("55570", "55570_DTE_SEEL_Contractor_Onboarding _Packet_v26.pdf", 
              "55570_DTE_SEEL_Contractor_Onboarding _Packet_v26_fillable.pdf"),
    ("57618", "57618_NGRID_New_York_Ack_Form_Fillable_v07.pdf", 
              "57618_NGRID_New_York_Ack_Form_Fillable_v07_fillable.pdf"),
    ("11691", "11691_ConEd_Distributor_Application_Form_v10.pdf", 
              "11691_ConEd_Distributor_Application_Form_v10_fillable_RELEASE.pdf"),
    ("57769", "57769_CH_Res_NYS_Clean_Heat_Completion_Acknowledgement_Form_v2.pdf", 
              "57769_CH_Res_NYS_Clean_Heat_Completion_Acknowledgement_Form_v3_Web_Release_fillable.pdf"),
    ("56380", "56380_DTE_2026_PartnerGuidelines_v18.pdf", 
              "56380_DTE_2026_PartnerGuidelines_v18_Fillable.pdf"),
    ("53252", "53252_DTE_EEA_Field_Inspection_Report_v11.pdf", 
              "53252_DTE_EEA_Field_Inspection_Report_v14_Web_Release_Fillable.pdf"),
    ("32775", "32775_DTE_2022_HVAC_Customer_SelfSubmission_Application_v01.pdf", 
              "32775_DTE_2022_HVAC_Customer_SelfSubmission_Application_v01_Web_Release_Fillable.pdf"),
    ("14792", "14792_ConEd_Gas_HVAC_Tune-Up_Contractor_Application_v03_FINAL_RELEASE_Web.pdf", 
              "14792_ConEd_Gas_HVAC_Tune-Up_Contractor_Application_v03_FINAL_RELEASE_Web_Fillable.pdf"),
    ("9787", "9787_DTE_EEA_Field_Inspection_Report_v10.pdf", 
             "9787_DTE_EEA_Field_Inspection_Report_v10_fillable.pdf"),
]
V22_OUTPUT_DIR = "/tmp/v22_output"
V22_SCRIPT = "/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/FillThatPDF_Optimized_v7/python_dist/smart_fillable_v22.py"

os.makedirs(V22_OUTPUT_DIR, exist_ok=True)

print("Running v22 on test PDFs...")
for pdf_id, unfilled, fillable in PDF_PAIRS:
    input_path = os.path.join(STATIC_DIR, unfilled)
    output_path = os.path.join(V22_OUTPUT_DIR, f"{pdf_id}_v22_output.pdf")
    
    if not os.path.exists(input_path):
        print(f"  ⚠️ Missing input: {input_path}")
        continue
    
    print(f"  Processing {pdf_id}...")
    cmd = ['python3', V22_SCRIPT, input_path, '--output', output_path]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"    ❌ Error: {result.stderr[:200]}")
    else:
        print(f"    ✅ Done")

print("\nNow run direct_fp_analysis.py to analyze errors.")
