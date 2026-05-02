
import pdfplumber



def check_file(filename):
    print(f"\nChecking {filename}...")
    try:
        with pdfplumber.open(filename) as pdf:
            p1 = pdf.pages[0]
            print("--- TEXT ---")
            print(p1.extract_text())
            print("--- END TEXT ---")
            
            words = p1.extract_words()
            for w in words:
               if 'SJG' in w['text'] or 'Municipal' in w['text']:
                   print(f"    Word: '{w['text']}' at {w['top']:.2f}, {w['x0']:.2f}")
    except Exception as e:
        print(f"  Error: {e}")


def inspect():
    files = [
        "46119_RECO_HVAC_Incentive_Claim_Form_v02.pdf",
        "9787_ConEd_Res_HVAC_Electric_Rebate_Appl_v01_FINAL_RELEASE_NOEDIT_fillable.pdf",
        "input_53252.pdf"
    ]
    for f in files:
        check_file(f)


if __name__ == "__main__":
    inspect()
