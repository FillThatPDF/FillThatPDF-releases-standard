#!/usr/bin/env python3
import sys
sys.path.insert(0, '/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/FillThatPDF_Optimized_v4/python_dist')
import pdfplumber

pdf = pdfplumber.open('/Users/36981/Desktop/PDFTest/PDFs to test/Batch 1 Forms/57643_DTE_MF_Measures_and_Specs_Income_Qualified_Catalog_v2.pdf')
page = pdf.pages[10]  # P11

print("=== P11 Header Row Detection ===")
# Find the table cells
from pdfplumber.utils import extract_text, cluster_objects

# Get all rectangles (table grid lines)
rects = page.rects
print(f"Total rectangles: {len(rects)}")

# Check for cyan/teal header bars
print("\n=== Checking Colored Rectangles (y < 100 from top) ===")
page_height = page.height
for r in page.rects:
    y_from_top = page_height - r['y1']  # Convert to top-down
    if y_from_top < 100:  # Top 100pts
        color = r.get('non_stroking_color', [])
        print(f"Rect at y={r['y0']:.1f} (from_top={y_from_top:.1f}), color={color}")

# Check the specific table with "# of Units" header
print("\n=== Looking for '# of' headers ===")
words = page.extract_words()
for w in words:
    if '# of' in w['text'].lower() or 'fixtures' in w['text'].lower():
        y_from_top = page_height - w['bottom']
        print(f"Found: '{w['text']}' at x={w['x0']:.1f}, y_from_top={y_from_top:.1f}, y_pdf={w['top']:.1f}")

# Check cells in the area where the table is
print("\n=== Analyzing Table Structure (y_pdf > 700) ===")
table_settings = {
    "vertical_strategy": "lines",
    "horizontal_strategy": "lines",
    "intersection_tolerance": 3,
}
tables = page.find_tables(table_settings)
print(f"Found {len(tables)} tables")

for i, table in enumerate(tables):
    if table.bbox[1] > 700:  # Focus on tables in top area
        print(f"\nTable {i} bbox: {table.bbox}")
        # Check first row (header row)
        if table.rows:
            header_row = table.rows[0]
            print(f"Header row cells: {len(header_row)}")
            for j, cell in enumerate(header_row):
                if cell:
                    print(f"  Cell {j}: '{cell[:30] if cell else ''}'")
