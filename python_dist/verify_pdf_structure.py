
import pikepdf
pdf_path = '/Users/36981/Desktop/PDFTest/PDFs to test/Static PDFs/55570_Topological_Success_v7.pdf'
with pikepdf.open(pdf_path) as pdf:
    print(f"Root keys: {list(pdf.Root.keys())}")
    if '/AcroForm' in pdf.Root:
        acro = pdf.Root.AcroForm
        print(f"AcroForm present. Keys: {list(acro.keys())}")
        print(f"NeedAppearances: {acro.get('/NeedAppearances')}")
        fields = acro.get('/Fields', [])
        print(f"Fields in AcroForm: {len(fields)}")
        if len(fields) > 0:
            f0 = fields[0]
            print(f"Example Field Keys: {list(f0.keys())}")
            print(f"Example Field Subtype: {f0.get('/Subtype')}")
            print(f"Example Field Rect: {f0.get('/Rect')}")
            print(f"Example Field DA: {f0.get('/DA')}")
            print(f"Example Field MK: {list(f0.get('/MK', {}).keys()) if '/MK' in f0 else 'No MK'}")
    else:
        print("AcroForm MISSING from Root")
