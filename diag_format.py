import sys, json, subprocess

pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/Static PDFs/57603_DTE_MF_Measures_and_Specs_Market_Rate_Catalog_v4_Web_Release_Fillable_Calc.pdf"

proc = subprocess.run(
    [sys.executable, "python_dist/extract_fields.py", pdf_path],
    capture_output=True, text=True
)

data = json.loads(proc.stdout)
fields = data.get("fields", [])

print(f"Total fields: {len(fields)}")
print()

for f in fields:
    name = f.get("name", "")
    has_calc = bool(f.get("calculation"))
    has_format = bool(f.get("formatType"))
    is_unit = "unit" in name.lower()
    
    if is_unit or has_calc or has_format:
        ft = f.get("formatType", "")
        calc = f.get("calculation")
        acts = f.get("actions", {})
        ks = acts.get("Keystroke", "")[:120] if "Keystroke" in acts else ""
        fmt = acts.get("Format", "")[:120] if "Format" in acts else ""
        nd = f.get("numberDecimals")
        ts = f.get("thousandSep")
        
        print(f"FIELD: {name}")
        print(f"  page={f.get('page','?')}, type={f['type']}, formatType='{ft}', numberDecimals={nd}, thousandSep={ts}")
        if calc:
            print(f"  calculation={calc}")
        if ks:
            print(f"  Keystroke: {ks}")
        if fmt:
            print(f"  Format: {fmt}")
        if not ks and not fmt and not has_format:
            print(f"  (no format/keystroke actions)")
        print()
