#!/usr/bin/env python3
"""Deep inspection of AP streams and what happens during modify_fields save."""
import pikepdf
import subprocess
import json
import os
import sys

PDF_PATH = "/Users/36981/Desktop/PDFTest/PDFs to test/Static PDFs/1/57643_DTE_MF_Measures_and_Specs_Income_Qualified_Catalog_v8_fillable.pdf"
OUTPUT_PATH = "/tmp/diag_real_save_output.pdf"

def get_ap_stream_content(annot):
    """Extract the actual content of the AP/N stream."""
    if '/AP' not in annot or '/N' not in annot['/AP']:
        return None
    ap_n = annot['/AP']['/N']
    # AP/N can be a stream (direct appearance) or a dict of states
    if isinstance(ap_n, pikepdf.Stream):
        try:
            return ap_n.read_bytes().decode('latin-1')
        except:
            return f"<binary {len(ap_n.read_bytes())} bytes>"
    elif isinstance(ap_n, pikepdf.Dictionary):
        # It's a dict of state names -> streams
        result = {}
        for key in ap_n.keys():
            try:
                stream = ap_n[key]
                if isinstance(stream, pikepdf.Stream):
                    result[str(key)] = stream.read_bytes().decode('latin-1')
                else:
                    result[str(key)] = str(type(stream))
            except:
                result[str(key)] = "<error>"
        return result
    return f"<type: {type(ap_n).__name__}>"

def find_fields_on_page(pdf, page_idx, name_filter=None):
    """Find all fields on a specific page."""
    if page_idx >= len(pdf.pages):
        return []
    page = pdf.pages[page_idx]
    if '/Annots' not in page:
        return []

    results = []
    for annot in page.Annots:
        t = annot.get('/T')
        if not t:
            parent = annot.get('/Parent')
            if parent:
                t = parent.get('/T')
        if not t:
            continue
        name = str(t)
        if name_filter and name_filter.lower() not in name.lower():
            continue
        results.append((name, annot))
    return results

# Page 10 (0-indexed = 9) is where the rebate fields are
TARGET_PAGE = 9

print("=" * 70)
print(f"Inspecting page {TARGET_PAGE + 1} of original PDF")
print("=" * 70)

pdf = pikepdf.open(PDF_PATH)

# Check NeedAppearances
acroform = pdf.Root.get('/AcroForm')
print(f"NeedAppearances: {acroform.get('/NeedAppearances', 'NOT SET')}")

# Find fields on page 10
fields = find_fields_on_page(pdf, TARGET_PAGE)
print(f"\nAll fields on page {TARGET_PAGE + 1}: {len(fields)}")

# Show rebate-related fields
rebate_fields = [(n, a) for n, a in fields if 'rebate' in n.lower() or 'total' in n.lower() or 'unit' in n.lower()]
print(f"Rebate/total/unit fields: {len(rebate_fields)}")

for name, annot in rebate_fields[:10]:
    print(f"\n--- {name} ---")
    print(f"  FT: {annot.get('/FT', 'NONE')}")
    print(f"  V: {repr(str(annot.get('/V', 'NO_VALUE')))}")
    print(f"  DA: {str(annot.get('/DA', 'NONE'))}")
    print(f"  Rect: {annot.get('/Rect')}")
    print(f"  Has /AP: {'/AP' in annot}")
    print(f"  Has /MK: {'/MK' in annot}")
    if '/MK' in annot:
        mk = annot['/MK']
        print(f"    /MK keys: {list(mk.keys())}")

    # Show AP stream content
    ap_content = get_ap_stream_content(annot)
    if isinstance(ap_content, str):
        # Truncate long streams
        if len(ap_content) > 200:
            print(f"  AP/N stream ({len(ap_content)} chars): {ap_content[:200]}...")
        else:
            print(f"  AP/N stream: {ap_content}")
    elif isinstance(ap_content, dict):
        for state, content in ap_content.items():
            if isinstance(content, str) and len(content) > 200:
                print(f"  AP/N[{state}] ({len(content)} chars): {content[:200]}...")
            else:
                print(f"  AP/N[{state}]: {content}")
    else:
        print(f"  AP/N: {ap_content}")

pdf.close()

# Now run the ACTUAL modify_fields.py with a minimal calculation change
print("\n" + "=" * 70)
print("Running ACTUAL modify_fields.py with calc-only changes")
print("=" * 70)

# Create minimal changes - just one calculation
changes = {
    "styled": {},
    "styled_by_index": {},
    "calculations": {
        "Total Rebate G x H_8": {
            "type": "SUM",
            "sources": ["# of Units_8"],
            "decimals": 2,
            "round": "none",
            "readOnly": False
        }
    },
    "renamed": {},
    "moved": {},
    "resized": {},
    "deleted": [],
    "created": [],
    "new_radio_groups": {}
}

# Write changes to temp file
import tempfile
changes_file = os.path.join(tempfile.gettempdir(), 'diag_changes.json')
with open(changes_file, 'w') as f:
    json.dump(changes, f)

settings = {"field_border_color": "none", "field_border_thickness": "0"}

# Run modify_fields.py directly
script_path = os.path.join(os.path.dirname(__file__), 'modify_fields.py')
result = subprocess.run(
    [sys.executable, script_path, PDF_PATH, OUTPUT_PATH, '--changes-file', changes_file, '--settings', json.dumps(settings)],
    capture_output=True, text=True, timeout=60
)

print(f"Exit code: {result.returncode}")
if result.stdout.strip():
    try:
        output = json.loads(result.stdout.strip().split('\n')[-1])
        print(f"Result: {json.dumps(output, indent=2)}")
    except:
        print(f"Stdout: {result.stdout[:500]}")
if result.stderr.strip():
    print(f"Stderr: {result.stderr[:500]}")

# Now inspect the output
print("\n" + "=" * 70)
print(f"Inspecting page {TARGET_PAGE + 1} of OUTPUT PDF")
print("=" * 70)

if os.path.exists(OUTPUT_PATH):
    pdf_out = pikepdf.open(OUTPUT_PATH)

    acroform_out = pdf_out.Root.get('/AcroForm')
    print(f"NeedAppearances: {acroform_out.get('/NeedAppearances', 'NOT SET')}")

    fields_out = find_fields_on_page(pdf_out, TARGET_PAGE)
    rebate_out = [(n, a) for n, a in fields_out if 'rebate' in n.lower() or 'total' in n.lower() or 'unit' in n.lower()]

    for name, annot in rebate_out[:10]:
        print(f"\n--- {name} ---")
        print(f"  V: {repr(str(annot.get('/V', 'NO_VALUE')))}")
        print(f"  Has /AP: {'/AP' in annot}")
        print(f"  Has /AA: {'/AA' in annot}, Has /AA/C: {'/AA' in annot and '/C' in annot.get('/AA', {})}")

        ap_content = get_ap_stream_content(annot)
        if isinstance(ap_content, str):
            if len(ap_content) > 200:
                print(f"  AP/N stream ({len(ap_content)} chars): {ap_content[:200]}...")
            else:
                print(f"  AP/N stream: {ap_content}")
        elif isinstance(ap_content, dict):
            for state, content in ap_content.items():
                if isinstance(content, str) and len(content) > 200:
                    print(f"  AP/N[{state}] ({len(content)} chars): {content[:200]}...")
                else:
                    print(f"  AP/N[{state}]: {content}")
        else:
            print(f"  AP/N: {ap_content}")

    pdf_out.close()
else:
    print("OUTPUT FILE NOT FOUND!")
