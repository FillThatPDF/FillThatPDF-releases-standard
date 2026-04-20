import sys, json, subprocess

pdf_path = "/Users/36981/Desktop/PDFTest/PDFs to test/Static PDFs/57603_DTE_MF_Measures_and_Specs_Market_Rate_Catalog_v4_Web_Release_Fillable_Calc.pdf"

proc = subprocess.run(
    [sys.executable, "python_dist/extract_fields.py", pdf_path],
    capture_output=True, text=True
)

data = json.loads(proc.stdout)
fields = data.get("fields", [])

# Show fields starting with iua, cal (non-t), and any 'unit' fields
targets = []
for f in fields:
    name = f.get("name", "")
    # Show iua* fields, and all fields with only Keystroke (no Format) actions
    acts = f.get("actions", {})
    has_keystroke_only = "Keystroke" in acts and "Format" not in acts
    if name.startswith("iua") or name.startswith("cal") and not name.endswith("t") or "unit" in name.lower() or has_keystroke_only:
        targets.append(f)

for f in targets[:50]:
    name = f.get("name", "")
    ft = f.get("formatType", "")
    calc = f.get("calculation")
    acts = f.get("actions", {})
    
    print(f"FIELD: {name}")
    print(f"  page={f.get('page','?')}, type={f['type']}, formatType='{ft}'")
    print(f"  numberDecimals={f.get('numberDecimals')}, thousandSep={f.get('thousandSep')}")
    if calc:
        print(f"  calculation={calc}")
    for k, v in acts.items():
        print(f"  {k}: {v[:150]}")
    print()
