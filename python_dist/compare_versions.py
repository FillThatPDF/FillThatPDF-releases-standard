#!/usr/bin/env python3
import fitz

STATIC = '/Users/36981/Desktop/PDFTest/PDFs to test/Static PDFs/53252_DTE_EEA_Field_Inspection_Report_v11.pdf'
V22 = '/tmp/v22_all_output/53252_DTE_EEA_Field_Inspection_Report_v11_v22_output.pdf'
GT = '/Users/36981/Desktop/PDFTest/PDFs to test/Fillable PDFs/53252_DTE_EEA_Field_Inspection_Report_v14_Web_Release_Fillable.pdf'

static_doc = fitz.open(STATIC)
print(f"Static (v11): {static_doc.page_count} pages")

gt_doc = fitz.open(GT)
print(f"GT (v14): {gt_doc.page_count} pages")
gt_fields = sum(len(list(p.widgets())) for p in gt_doc)
print(f"GT fields: {gt_fields}")

v22_doc = fitz.open(V22)
v22_fields = sum(len(list(p.widgets())) for p in v22_doc)
print(f"v22 output fields: {v22_fields}")
