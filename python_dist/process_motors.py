from smart_fillable_v21 import UniversalPDFFillable
import os

input_pdf = "/Users/36981/Desktop/PDFTest/PDFs to test/Motors 02-04-10kt.pdf"
output_pdf = "Motors_FIXED.pdf"

detector = UniversalPDFFillable(input_pdf, output_pdf)
detector.run()
print(f"Saved to {output_pdf}")
