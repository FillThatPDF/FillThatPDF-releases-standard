
import sys
import os
import pdfplumber

# Add current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from smart_fillable_v23 import UniversalPDFFillable

if __name__ == "__main__":
    pdf_path = "../46119_RECO_HVAC_Incentive_Claim_Form_v02.pdf"
    
    # Run targeted processing for Page 3
    proc = UniversalPDFFillable(pdf_path, pages="3")
    proc.run()
    
    print("\n" + "="*60)
    print("🔎 ALIGNMENT & PHANTOM FIELD INVESTIGATION (PAGE 3)")
    print("="*60)
    
    # Focus fields: 'SJG', 'Municipal Electric Co.', 'PSE&G'
    targets = ['SJG', 'Municipal', 'PSE&G']
    
    # Normalize fields
    all_fields = []
    for f in proc.text_fields:
        if f['page'] == 2:
            f['type'] = 'text'
            all_fields.append(f)
    for f in proc.checkboxes:
        if f['page'] == 2:
            # Convert to x0, y0, x1, y1
            new_f = f.copy()
            new_f['type'] = 'checkbox'
            new_f['x0'] = f['x']
            new_f['y0'] = f['y']
            new_f['x1'] = f['x'] + f['width']
            new_f['y1'] = f['y'] + f['height']
            # Map 'label' to 'name' for consistency
            if 'label' in f:
                new_f['name'] = f['label']
            all_fields.append(new_f)

    print(f"Total fields on Page 3: {len(all_fields)}")
    
    # 1. Check Alignment for target fields
    print("\n--- Field Alignment Check ---")
    for f in all_fields:
        name = f.get('name', '')
        # Check if this field is near our targets
        is_target_related = any(t in name for t in targets)
        
        if is_target_related or f.get('type') == 'text':
             print(f"Field '{name}' ({f['type']}) Source={f.get('source')} Rect: {f['x0']:.1f},{f['y0']:.1f}-{f['x1']:.1f},{f['y1']:.1f} H={f['y1']-f['y0']:.1f}")

    # 2. Check for Phantom Fields (Fields covering text)
    print("\n--- Phantom Field Check (Text Overlap) ---")
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[2]
        words = page.extract_words()
        
        for f in all_fields:
            # Check for overlap with any word
            overlaps = []
            fx0, fy0, fx1, fy1 = f['x0'], f['y0'], f['x1'], f['y1']
            
            for w in words:
                # Check intersection
                if (fx0 < w['x1'] and fx1 > w['x0'] and
                    fy0 < w['bottom'] and fy1 > w['top']):
                    overlaps.append(w['text'])
            
            if overlaps:
                print(f"⚠️ Field '{f.get('name')}' overlaps text: {overlaps}")

