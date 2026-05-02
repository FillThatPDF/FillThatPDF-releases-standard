
import sys
import os
import pdfplumber

# Add current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from smart_fillable_v23 import UniversalPDFFillable

if __name__ == "__main__":
    pdf_path = "../46119_RECO_HVAC_Incentive_Claim_Form_v02.pdf" # PDF is in parent dir
    
    print(f"Loading {pdf_path}...")
    proc = UniversalPDFFillable(pdf_path, pages="3") # Focus on Page 3 (Index 2)
    proc.run()
    
    # Dump fields on Page 3 (Index 2) - This is where "Gas and electric" are supposed to be.
    # The user said Page 3 of 46119 PDF.
    
    # Wait, my previous analysis said "Gas and electric" on Page 2 (index 1)?
    # Let's check both
    
    print("\n" + "="*60)
    print("🔎 UNIVERSAL SCAN FOR KEYWORDS: State, ZIP, Gas, Electric")
    print("="*60)
    
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            print(f"\n📄 PAGE {i+1} (Index {i})")
            words = page.extract_words()
            
            # Find keywords
            targets = ['State', 'ZIP', 'Gas', 'Electric', 'Gas/Electric']
            found_targets = []
            for w in words:
                # Loose matching
                if any(t in w['text'] for t in targets):
                    print(f"   🎯 Found '{w['text']}' at y={w['top']:.1f}-{w['bottom']:.1f} x={w['x0']:.1f}-{w['x1']:.1f}")
                    found_targets.append(w)
            
            # Find fields on this page
            page_fields = [f for f in proc.text_fields if f['page'] == i]
            page_cbs = [f for f in proc.checkboxes if f['page'] == i]
            
            if found_targets:
                print(f"   Other Fields found on this page: {len(page_fields)}")
                # Check for overlap/alignment with targets
                for t in found_targets:
                    # Look for fields near this target
                    # Same line (y)
                    nearby = []
                    for f in page_fields + page_cbs:
                        # Normalize coordinates
                        if 'y0' in f:
                            fy0, fy1 = f['y0'], f['y1']
                            fx0 = f['x0']
                        else:
                            fy0, fy1 = f['y'], f['y'] + f['height']
                            fx0 = f['x']
                            
                        if abs(fy0 - (context_y := float(t['bottom']))) < 20 or abs(fy1 - t['top']) < 20: # Rough vertical proximity
                             nearby.append(f)
                    
                    if nearby:
                        print(f"      Fields near '{t['text']}':")
                        # Pre-process for sorting
                        normalized = []
                        for f in nearby:
                            if 'y0' in f:
                                normalized.append({
                                    'x0': f['x0'], 'y0': f['y0'], 'x1': f['x1'], 'y1': f['y1'],
                                    'name': f.get('name', 'Unnamed'), 'type': f.get('type', 'text')
                                })
                            else:
                                normalized.append({
                                    'x0': f['x'], 'y0': f['y'], 'x1': f['x'] + f['width'], 'y1': f['y'] + f['height'],
                                    'name': f.get('label', 'Checkbox'), 'type': 'checkbox'
                                })
                                
                        for f in sorted(normalized, key=lambda x: x['x0']):
                             print(f"         - '{f['name']}' ({f['type']}) Rect: {f['x0']:.1f},{f['y0']:.1f}-{f['x1']:.1f},{f['y1']:.1f}")
            else:
                print("   No targets found.")

