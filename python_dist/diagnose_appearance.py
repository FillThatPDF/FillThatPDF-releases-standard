#!/usr/bin/env python3
"""Diagnose appearance stream issues by comparing input/output PDFs."""
import pikepdf
import sys
import json

PDF_PATH = "/Users/36981/Desktop/PDFTest/PDFs to test/Static PDFs/1/57643_DTE_MF_Measures_and_Specs_Income_Qualified_Catalog_v8_fillable.pdf"
OUTPUT_PATH = "/tmp/diag_test_output.pdf"

def inspect_field(pdf, name_filter="rebate"):
    """Find and inspect fields matching the filter."""
    fields_found = []
    for page_idx, page in enumerate(pdf.pages):
        if '/Annots' not in page:
            continue
        for annot in page.Annots:
            t = annot.get('/T')
            if not t:
                # Check parent
                parent = annot.get('/Parent')
                if parent:
                    t = parent.get('/T')
            if not t:
                continue
            field_name = str(t)
            if name_filter.lower() not in field_name.lower():
                continue

            info = {
                'name': field_name,
                'page': page_idx,
                'has_AP': '/AP' in annot,
                'has_AP_N': '/AP' in annot and '/N' in annot.get('/AP', {}),
                'has_V': '/V' in annot,
                'V_value': str(annot.get('/V', 'NONE')),
                'has_DA': '/DA' in annot,
                'DA_value': str(annot.get('/DA', 'NONE')),
                'has_AA': '/AA' in annot,
                'has_AA_C': '/AA' in annot and '/C' in annot.get('/AA', {}),
                'has_MK': '/MK' in annot,
                'has_MK_BG': '/MK' in annot and '/BG' in annot.get('/MK', {}),
                'has_MK_BC': '/MK' in annot and '/BC' in annot.get('/MK', {}),
                'has_BS': '/BS' in annot,
                'has_Border': '/Border' in annot,
                'FT': str(annot.get('/FT', 'NONE')),
                'Ff': int(annot.get('/Ff', 0)),
            }

            # Check AP/N stream size if present
            if info['has_AP_N']:
                try:
                    ap_n = annot['/AP']['/N']
                    if hasattr(ap_n, 'read_bytes'):
                        info['AP_N_size'] = len(ap_n.read_bytes())
                    else:
                        info['AP_N_type'] = type(ap_n).__name__
                except Exception as e:
                    info['AP_N_error'] = str(e)

            fields_found.append(info)
    return fields_found

def simulate_calc_save(input_path, output_path):
    """Simulate what modify_fields.py does when applying just calculations."""
    pdf = pikepdf.open(input_path)

    # Find a "total rebate" field and apply a simple calc
    for page in pdf.pages:
        if '/Annots' not in page:
            continue
        for annot in page.Annots:
            t = annot.get('/T')
            if not t:
                continue
            name = str(t)
            if 'total rebate' in name.lower() or 'rebate' in name.lower():
                print(f"\n=== Modifying field: {name} ===")
                print(f"  Before: /AP present={'/AP' in annot}, /V={annot.get('/V', 'NONE')}")
                print(f"  Before: /MK present={'/MK' in annot}, /DA={annot.get('/DA', 'NONE')}")

                # Simulate what modify_fields does:
                # 1. Add /AA/C
                if '/AA' not in annot:
                    annot['/AA'] = pikepdf.Dictionary()
                annot['/AA']['/C'] = pdf.make_indirect(pikepdf.Dictionary({
                    '/S': pikepdf.Name('/JavaScript'),
                    '/JS': 'AFSimple_Calculate("SUM", new Array("test1","test2"));'
                }))

                # 2. Set /V to '0' if empty
                if '/V' not in annot or str(annot['/V']) == '':
                    annot['/V'] = pikepdf.String('0')

                print(f"  After:  /AP present={'/AP' in annot}, /V={annot.get('/V', 'NONE')}")
                break
        break  # Only modify first matching page

    pdf.save(output_path)
    pdf.close()
    return output_path

print("=" * 60)
print("STEP 1: Inspect ORIGINAL PDF")
print("=" * 60)

pdf_in = pikepdf.open(PDF_PATH)

# Check AcroForm
acroform = pdf_in.Root.get('/AcroForm')
if acroform:
    print(f"AcroForm NeedAppearances: {acroform.get('/NeedAppearances', 'NOT SET')}")
    print(f"AcroForm has /CO: {'/CO' in acroform}")
else:
    print("NO AcroForm found!")

fields_in = inspect_field(pdf_in, "rebate")
print(f"\nFound {len(fields_in)} 'rebate' fields in original:")
for f in fields_in[:5]:
    print(f"  {f['name']}:")
    print(f"    AP={f['has_AP']}, AP/N={f['has_AP_N']}, V={f['V_value']}")
    print(f"    DA={f['DA_value']}")
    print(f"    MK={f['has_MK']}, BG={f['has_MK_BG']}, BC={f['has_MK_BC']}")
    print(f"    BS={f['has_BS']}, Border={f['has_Border']}")
    if 'AP_N_size' in f:
        print(f"    AP/N stream size: {f['AP_N_size']} bytes")

pdf_in.close()

print("\n" + "=" * 60)
print("STEP 2: Simulate calc-only save")
print("=" * 60)

out_path = simulate_calc_save(PDF_PATH, OUTPUT_PATH)

print("\n" + "=" * 60)
print("STEP 3: Inspect OUTPUT PDF")
print("=" * 60)

pdf_out = pikepdf.open(out_path)

acroform_out = pdf_out.Root.get('/AcroForm')
if acroform_out:
    print(f"AcroForm NeedAppearances: {acroform_out.get('/NeedAppearances', 'NOT SET')}")

fields_out = inspect_field(pdf_out, "rebate")
print(f"\nFound {len(fields_out)} 'rebate' fields in output:")
for f in fields_out[:5]:
    print(f"  {f['name']}:")
    print(f"    AP={f['has_AP']}, AP/N={f['has_AP_N']}, V={f['V_value']}")
    print(f"    DA={f['DA_value']}")
    print(f"    MK={f['has_MK']}, BG={f['has_MK_BG']}, BC={f['has_MK_BC']}")
    print(f"    BS={f['has_BS']}, Border={f['has_Border']}")
    if 'AP_N_size' in f:
        print(f"    AP/N stream size: {f['AP_N_size']} bytes")

pdf_out.close()

print("\n" + "=" * 60)
print("STEP 4: Compare differences")
print("=" * 60)

for fi, fo in zip(fields_in[:5], fields_out[:5]):
    diffs = []
    for key in fi:
        if fi[key] != fo.get(key):
            diffs.append(f"  {key}: {fi[key]} → {fo[key]}")
    if diffs:
        print(f"\n{fi['name']} CHANGED:")
        for d in diffs:
            print(d)
    else:
        print(f"\n{fi['name']}: no changes")
