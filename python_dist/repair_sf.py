
import sys

def repair():
    sf_path = "smart_fillable_v21.py"
    with open(sf_path, "r") as f:
        lines = f.readlines()
    
    # Range to replace: 12380 to 12607 (1-indexed)
    start_idx = 12380 - 1
    end_idx = 12607 # up to this line
    
    clean_func = """    def _create_radio_groups(self):
        \"\"\"
        Create radio button groups AFTER Preview fix.
        \"\"\"
        if not self.radio_groups:
            print("   No radio groups to create")
            return
        
        pdf = pikepdf.Pdf.open(str(self.output_pdf), allow_overwriting_input=True)
        
        if '/AcroForm' not in pdf.Root:
            pdf.Root['/AcroForm'] = pikepdf.Dictionary({
                '/Fields': pikepdf.Array([]),
                '/NeedAppearances': True
            })
        
        groups_created = 0
        widgets_to_remove = []
        
        for group in self.radio_groups:
            group_name = group['name']
            page_num = group['page']
            checkboxes = group['checkboxes']
            
            if len(checkboxes) < 2:
                continue
            
            page = pdf.pages[page_num]
            if '/Annots' not in page:
                continue
            
            page_height = float(page.MediaBox[3]) if '/MediaBox' in page else 792.0
            
            parent_dict = pikepdf.Dictionary({
                '/FT': pikepdf.Name('/Btn'),
                '/Ff': 49152,
                '/T': group_name,
                '/Kids': pikepdf.Array([]),
                '/V': pikepdf.Name('/Off')
            })
            parent = pdf.make_indirect(parent_dict)
            
            children_found = 0
            for idx, cb in enumerate(checkboxes, start=1):
                cb_x = cb['x']
                cb_y_pdf = page_height - cb['y'] - cb['height']
                cb_value = cb.get('radio_value') or 'Option'
                cb_label = cb.get('label', '')
                found_match = False
                
                for annot in page.Annots:
                    if annot.get('/Subtype') != pikepdf.Name('/Widget') or annot.get('/FT') != pikepdf.Name('/Btn'):
                        continue
                    
                    rect = annot.get('/Rect', [])
                    if len(rect) < 4: continue
                    
                    ax0, ay0 = float(rect[0]), float(rect[1])
                    
                    if abs(ax0 - cb_x) < 5 and abs(ay0 - cb_y_pdf) < 5:
                        found_match = True
                        print(f"      [create_radio_groups] Matched widget at {ax0:.1f},{ay0:.1f} for CB {cb_value}")
                        widgets_to_remove.append(annot.objgen)
                        
                        annot['/Ff'] = 49152
                        annot['/Parent'] = parent
                        if '/T' in annot: del annot['/T']
                        annot['/AS'] = pikepdf.Name('/Off')
                        
                        tooltip_text = cb_label if cb_label else cb_value
                        if tooltip_text: annot['/TU'] = tooltip_text
                        
                        checkbox_style_map = {'check': '4', 'circle': 'l', 'cross': '8', 'square': 'n', 'diamond': 'u', 'star': 'H'}
                        checkbox_style = self._get_setting('checkbox_style', 'check')
                        ca_char = checkbox_style_map.get(checkbox_style, '4')
                        
                        mk_dict = {'/CA': ca_char}
                        cb_border_thickness = int(self._get_setting('checkbox_border_thickness', 0))
                        if cb_border_thickness > 0:
                            annot['/BS'] = pikepdf.Dictionary({'/W': cb_border_thickness, '/S': pikepdf.Name('/S')})
                            cb_border_color = self._get_setting('checkbox_border_color', '#000000')
                            c_str = cb_border_color.lstrip('#')
                            if len(c_str) == 6:
                                bc = [int(c_str[i:i+2], 16)/255.0 for i in (0,2,4)]
                                mk_dict['/BC'] = pikepdf.Array(bc)
                        annot['/MK'] = pikepdf.Dictionary(mk_dict)
                        
                        # Border injection
                        if cb_border_thickness > 0:
                            try:
                                rw, rh = float(rect[2])-float(rect[0]), float(rect[3])-float(rect[1])
                                c_hex = self._get_setting('checkbox_border_color', '#000000').lstrip('#')
                                cr, cg, cb_v = [int(c_hex[i:i+2], 16)/255.0 for i in (0,2,4)] if len(c_hex)==6 else (0,0,0)
                                t = float(cb_border_thickness)
                                cmd = f" q {cr:.3f} {cg:.3f} {cb_v:.3f} RG {t} w 0 0 0 0 k {t/2.0:.2f} {t/2.0:.2f} {rw-t:.2f} {rh-t:.2f} re S Q".encode('ascii')
                                if '/AP' in annot and '/N' in annot['/AP']:
                                    for k in annot['/AP']['/N'].keys():
                                        s = annot['/AP']['/N'][k]
                                        s.write(s.read_bytes() + cmd)
                            except: pass
                            
                        # Value renaming
                        vname = str(cb.get('radio_value', '')) or ('Choice' + str(idx))
                        if '/AP' in annot and '/N' in annot['/AP']:
                            ap_n = annot['/AP']['/N']
                            if hasattr(ap_n, 'keys') and len(list(ap_n.keys())) > 0:
                                on_state = next((k for k in ap_n.keys() if str(k)!='/Off'), None)
                                if on_state and str(on_state) != f'/{vname}':
                                    ap_n[pikepdf.Name(f'/{vname}')] = ap_n[on_state]; del ap_n[on_state]
                                elif not on_state and '/Off' in ap_n: ap_n[pikepdf.Name(f'/{vname}')] = ap_n['/Off']
                            else: self._create_radio_appearance(pdf, annot, vname, float(rect[2])-float(rect[0]), float(rect[3])-float(rect[1]), ca_char)
                        else:
                            if '/AP' not in annot: annot['/AP'] = pikepdf.Dictionary()
                            self._create_radio_appearance(pdf, annot, vname, float(rect[2])-float(rect[0]), float(rect[3])-float(rect[1]), ca_char)
                        
                        annot['/P'] = page.obj
                        parent['/Kids'].append(annot)
                        children_found += 1
                        break
                
                if not found_match and page_num == 2:
                     print(f"      [create_radio_groups] NO MATCH for CB value {cb_value} at {cb_x:.1f},{cb_y_pdf:.1f}")
                     # Available widget dump
                     print(f"         Available Btn Widgets on P3:")
                     for w_annot in page.Annots:
                         if w_annot.get('/Subtype') == pikepdf.Name('/Widget') and w_annot.get('/FT') == pikepdf.Name('/Btn'):
                              wr = w_annot.get('/Rect')
                              print(f"            Widget at {float(wr[0]):.1f},{float(wr[1]):.1f} name={w_annot.get('/T')}")
            
            if children_found >= 2:
                pdf.Root['/AcroForm']['/Fields'].append(parent)
                groups_created += 1
            else:
                if page_num == 2:
                     print(f"      [create_radio_groups] FAILURE: Group {group_name} has only {children_found} matches")
        
        if widgets_to_remove:
            fields_array = pdf.Root['/AcroForm']['/Fields']
            new_fields = [f for f in fields_array if not (hasattr(f, 'objgen') and f.objgen in widgets_to_remove)]
            pdf.Root['/AcroForm']['/Fields'] = pikepdf.Array(new_fields)
            print(f"   Removed {len(widgets_to_remove)} widget refs from Fields array")
        
        pdf.save(str(self.output_pdf))
        print(f"   ✅ Created {groups_created} radio button groups")
"""
    
    new_lines = lines[:start_idx] + [clean_func + "\n"] + lines[end_idx:]
    
    with open(sf_path, "w") as f:
        f.writelines(new_lines)
    print("Repair successful!")

if __name__ == "__main__":
    repair()
