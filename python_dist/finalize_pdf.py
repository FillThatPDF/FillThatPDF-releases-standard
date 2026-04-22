
import pikepdf
import json
from pathlib import Path

v26_path = "/Users/36981/Desktop/PDFTest/PDFs to test/Static PDFs/55570_DTE_SEEL_Contractor_Onboarding _Packet_v26.pdf"
results_path = "topological_results.json"
out_path = "/Users/36981/Desktop/PDFTest/PDFs to test/Static PDFs/55570_Topological_Success_v14.pdf"

with open(results_path, 'r') as f:
    fields = json.load(f)

with pikepdf.open(v26_path) as pdf:
    if '/AcroForm' not in pdf.Root:
        pdf.Root.AcroForm = pikepdf.Dictionary({
            '/Fields': pikepdf.Array([]),
            '/NeedAppearances': True
        })
    acroform = pdf.Root.AcroForm
    acroform.NeedAppearances = True
    acroform.Fields = pikepdf.Array([]) # Clear existing

    print(f"Clearing existing widgets from {len(pdf.pages)} pages...")
    for i, page in enumerate(pdf.pages):
        if '/Annots' in page:
            new_annots = pikepdf.Array([a for a in page.Annots if a.get('/Subtype') != '/Widget'])
            page.Annots = pdf.make_indirect(new_annots)
    print("Existing widgets cleared.")

    # First pass: Create parent dictionaries for radio groups
    parents = {}
    for f in fields:
        parent_name = f.get('parent', '')
        if parent_name and parent_name not in parents:
            parent_dict = pikepdf.Dictionary({
                '/T': pikepdf.String(parent_name),
                '/FT': pikepdf.Name('/Btn'),
                '/Ff': 32768, # Radio
                '/Kids': pikepdf.Array([])
            })
            # Add to AcroForm
            acroform.Fields.append(pdf.make_indirect(parent_dict))
            parents[parent_name] = acroform.Fields[-1]

    print(f"Injecting {len(fields)} fields...")
    for i, f in enumerate(fields):
        if i % 100 == 0: print(f"Processing field {i}...")
        page_num = f['page']
        page = pdf.pages[page_num]
        name = f['label']
        rect = f['rect']
        
        widget_dict = {
            '/Type': pikepdf.Name('/Annot'),
            '/Subtype': pikepdf.Name('/Widget'),
            '/Rect': pikepdf.Array(rect),
        }
        
        # If not part of a radio group, it needs a name
        if not f.get('parent'):
            widget_dict['/T'] = pikepdf.String(name)
        
        if f['type'] == 'checkbox':
            widget_dict['/FT'] = pikepdf.Name('/Btn')
            # Transparent appearance to overlay existing vector box
            widget_dict['/MK'] = pikepdf.Dictionary({
                '/BC': pikepdf.Array([]), # No Border
                '/BG': pikepdf.Array([]), # No Background (Transparent)
                '/CA': pikepdf.String("4") # Check mark
            })
            widget_dict['/V'] = pikepdf.Name('/Off')
            widget_dict['/DA'] = pikepdf.String("/ZapfDingbats 12 Tf 0 g")
        elif f['type'] == 'radio':
            # FT is inherited from parent usually, but we'll set it
            widget_dict['/Parent'] = parents[f['parent']]
            # The "On" value is stored in /AS from fingerprints
            as_state = f.get('as_state', '/Off')
            widget_dict['/AS'] = pikepdf.Name(as_state)
            # Transparent appearance for Radio
            widget_dict['/MK'] = pikepdf.Dictionary({
                 '/BC': pikepdf.Array([]), # No Border
                 '/BG': pikepdf.Array([]), # Transparent
                 '/CA': pikepdf.String("l") # Dot
            })
            widget_dict['/BS'] = pikepdf.Dictionary({
                '/W': 1,
                '/S': pikepdf.Name('/S')
            })
            # Add to parent kids
            parents[f['parent']].Kids.append(pdf.make_indirect(pikepdf.Dictionary(widget_dict)))
        elif f['type'] == 'pushbutton':
            widget_dict['/FT'] = pikepdf.Name('/Btn')
            widget_dict['/Ff'] = 65536 # Pushbutton
            # Attach Action JS
            if f.get('action_js'):
                widget_dict['/A'] = pikepdf.Dictionary({
                    '/S': pikepdf.Name('/JavaScript'),
                    '/JS': pikepdf.String(f['action_js'])
                })
        elif f['type'] == 'combo':
             widget_dict['/FT'] = pikepdf.Name('/Ch')
             widget_dict['/Ff'] = 131072 # Combo flag
             widget_dict['/DA'] = pikepdf.String("/Helv 10 Tf 0 g")
        else:
            widget_dict['/FT'] = pikepdf.Name('/Tx')
            # Default Font and Size
            widget_dict['/DA'] = pikepdf.String("/Helv 10 Tf 0 g")
        
        # For radio, the widget IS the kid, and it's already added to parent.Kids.
        # But the WIDGET itself needs to be on the page annotations.
        if f['type'] == 'radio':
            annot = parents[f['parent']].Kids[-1]
        else:
            annot = pdf.make_indirect(pikepdf.Dictionary(widget_dict))
            acroform.Fields.append(annot)

        if '/Annots' not in page:
            page.Annots = pdf.make_indirect(pikepdf.Array())
        page.Annots.append(annot)

    pdf.save(out_path)
print(f"Saved: {out_path}")
