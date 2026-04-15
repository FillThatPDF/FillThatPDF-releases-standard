import pikepdf
import sys

def list_fields(pdf_path):
    print(f"--- Fields in {pdf_path} ---")
    with pikepdf.open(pdf_path) as pdf:
        if not hasattr(pdf.Root, 'AcroForm') or not hasattr(pdf.Root.AcroForm, 'Fields'):
            print("No AcroForm fields found.")
            return
            
        fields = pdf.Root.AcroForm.Fields
        for i, field in enumerate(fields):
            try:
                name = str(field.get('/T', 'Unnamed'))
                ft = str(field.get('/FT', 'Unknown'))
                rect = [float(x) for x in field.get('/Rect', [])]
                
                # Try to find page
                page_num = -1
                for p_idx, page in enumerate(pdf.pages):
                    if '/Annots' in page:
                        for annot in page.Annots:
                            if annot == field:
                                page_num = p_idx
                                break
                    if page_num != -1: break
                
                print(f"[{i}] Page {page_num}: {name} ({ft}) Rect={rect}")
            except Exception as e:
                print(f"[{i}] Error: {e}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        list_fields(sys.argv[1])
