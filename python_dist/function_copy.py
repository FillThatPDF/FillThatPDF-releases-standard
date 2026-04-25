    def _create_radio_groups(self):
        """
        Create radio button groups AFTER Preview fix.
        
        LEARNED: Use pikepdf to set /Parent references and /MK with checkmark.
        IMPORTANT: Must remove children from AcroForm/Fields when adding to parent.
        """
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
        # Track all widget references that become radio children (to remove from Fields)
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
            
            # Create parent field
            parent_dict = pikepdf.Dictionary({
                '/FT': pikepdf.Name('/Btn'),
                '/Ff': 49152,  # Radio | NoToggleToOff
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
                cb_label = cb.get('label', '')  # Get the nearby text label for tooltip
                found_match = False
                
                for annot in page.Annots:
                    if annot.get('/Subtype') != pikepdf.Name('/Widget'):
                        continue
                    if annot.get('/FT') != pikepdf.Name('/Btn'):
                        continue
                    
                    rect = annot.get('/Rect', [])
                    if len(rect) < 4:
                        continue
                    
                    ax0, ay0 = float(rect[0]), float(rect[1])
                    
                    if abs(ax0 - cb_x) < 5 and abs(ay0 - cb_y_pdf) < 5:
                        found_match = True
                        print(f"      [create_radio_groups] Matched widget at {ax0:.1f},{ay0:.1f} for CB {cb_value}")
                        # Track this widget for removal from Fields array
                        widgets_to_remove.append(annot.objgen)
                        
                        # Convert to radio button
                        annot['/Ff'] = 49152
                        annot['/Parent'] = parent
                        
                        # CRITICAL: Radio button children should NOT have /T names
                        if '/T' in annot:
                            del annot['/T']
                        
                        annot['/AS'] = pikepdf.Name('/Off')
                        
                        # Set tooltip
                        tooltip_text = cb_label if cb_label else cb_value
                        if tooltip_text:
                            annot['/TU'] = tooltip_text
                        
                        # learned: Checkmark appearance style
                        checkbox_style_map = {
                            'check': '4', 'circle': 'l', 'cross': '8', 'square': 'n', 'diamond': 'u', 'star': 'H'
                        }
                        checkbox_style = self._get_setting('checkbox_style', 'check')
                        ca_char = checkbox_style_map.get(checkbox_style, '4')
                        
                        mk_dict = {'/CA': ca_char}
                        cb_border_thickness = int(self._get_setting('checkbox_border_thickness', 0))
                        if cb_border_thickness > 0:
                            annot['/BS'] = pikepdf.Dictionary({'/W': cb_border_thickness, '/S': pikepdf.Name('/S')})
                            cb_border_color = self._get_setting('checkbox_border_color', '#000000')
                            c = cb_border_color.lstrip('#')
                            if len(c) == 6:
                                bc = [int(c[i:i+2], 16)/255.0 for i in (0,2,4)]
                                mk_dict['/BC'] = pikepdf.Array(bc)
                        annot['/MK'] = pikepdf.Dictionary(mk_dict)
                        
                        # Border injection
                        if cb_border_thickness > 0:
                            try:
                                r_val = annot.get('/Rect')
                                aw = float(r_val[2]) - float(r_val[0])
                                ah = float(r_val[3]) - float(r_val[1])
                                c_str = cb_border_color.lstrip('#')
                                if len(c_str) == 6:
                                    cr = int(c_str[0:2], 16) / 255.0
                                    cg = int(c_str[2:4], 16) / 255.0
                                    cb_v = int(c_str[4:6], 16) / 255.0
                                else: cr, cg, cb_v = 0, 0, 0
                                t = float(cb_border_thickness)
                                ix, iy = t/2.0, t/2.0
                                iw, ih = aw-t, ah-t
                                if iw < 0: iw = 0
                                if ih < 0: ih = 0
                                cmd = f" q {cr:.3f} {cg:.3f} {cb_v:.3f} RG {t} w 0 0 0 0 k {ix:.2f} {iy:.2f} {iw:.2f} {ih:.2f} re S Q"
                                if '/AP' in annot and '/N' in annot['/AP']:
                                    ap_n = annot['/AP']['/N']
                                    for key in ap_n.keys():
                                        stream = ap_n[key]
                                        current_data = stream.read_bytes()
                                        stream.write(current_data + cmd.encode('ascii'))
                            except: pass
                            
                        # Matching value/appearance renaming logic
                        annot_w = float(rect[2]) - float(rect[0])
                        annot_h = float(rect[3]) - float(rect[1])
                        value_name = str(cb.get('radio_value', ''))
                        if not value_name or value_name == 'None':
                            value_name = 'Choice' + str(idx)
                        
                        if '/AP' in annot and '/N' in annot['/AP']:
                            ap_n = annot['/AP']['/N']
                            has_keys = hasattr(ap_n, 'keys') and len(list(ap_n.keys())) > 0
                            if has_keys:
                                on_state = None
                                for key in list(ap_n.keys()):
                                    if str(key) != '/Off':
                                        on_state = key
                                        break
                                if on_state and str(on_state) != f'/{value_name}':
                                    ap_n[pikepdf.Name(f'/{value_name}')] = ap_n[on_state]
                                    del ap_n[on_state]
                                elif not on_state and '/Off' in ap_n:
                                    ap_n[pikepdf.Name(f'/{value_name}')] = ap_n['/Off']
                            else:
                                self._create_radio_appearance(pdf, annot, value_name, annot_w, annot_h, ca_char)
                        else:
                            if '/AP' not in annot: annot['/AP'] = pikepdf.Dictionary()
                            self._create_radio_appearance(pdf, annot, value_name, annot_w, annot_h, ca_char)
                        
                        annot['/P'] = page.obj
                        parent['/Kids'].append(annot)
                        children_found += 1
                        print(f"      [create_radio_groups] Added child {idx} to {group_name}")
                        break
                
                # If we didn't find a match for this specific CB
                if not found_match and page_num == 2:
                     print(f"      [create_radio_groups] NO MATCH for CB value={cb_value} at {cb_x:.1f},{cb_y_pdf:.1f}")
                     print(f"         Available Widgets on P3:")
                     for w_annot in page.Annots:
                         if w_annot.get('/Subtype') == pikepdf.Name('/Widget') and w_annot.get('/FT') == pikepdf.Name('/Btn'):
                              wr = w_annot.get('/Rect')
                              print(f"            Widget at {float(wr[0]):.1f},{float(wr[1]):.1f} name={w_annot.get('/T')}")

                        # Update appearance dictionary to have the correct export value name
                        value_name = re.sub(r'[^\\w]', '', cb_value) or 'Option'
                        
                        # Get annot dimensions for creating appearance streams
                        annot_rect = annot.get('/Rect', [0, 0, 10, 10])
                        if hasattr(annot_rect, '__iter__'):
                            annot_rect = [float(r) for r in annot_rect]
                        annot_w = abs(annot_rect[2] - annot_rect[0])
                        annot_h = abs(annot_rect[3] - annot_rect[1])
                        
                        if '/AP' in annot and '/N' in annot['/AP']:
                            ap_n = annot['/AP']['/N']
                            # Check if ap_n has keys (non-empty)
                            has_keys = hasattr(ap_n, 'keys') and len(list(ap_n.keys())) > 0
                            
                            if has_keys:
                                # Look for existing "on" state (/Yes or any non-Off state)
                                on_state = None
                                for key in list(ap_n.keys()):
                                    if str(key) != '/Off':
                                        on_state = key
                                        break
                                
                                if on_state and str(on_state) != f'/{value_name}':
                                    # Rename existing on state to our value name
                                    ap_n[pikepdf.Name(f'/{value_name}')] = ap_n[on_state]
                                    del ap_n[on_state]
                                elif not on_state and '/Off' in ap_n:
                                    # No on state exists - copy Off appearance as the on state
                                    ap_n[pikepdf.Name(f'/{value_name}')] = ap_n['/Off']
                            else:
                                # Empty /AP/N - create proper appearance streams
                                self._create_radio_appearance(pdf, annot, value_name, annot_w, annot_h, ca_char)
                        else:
                            # No /AP/N at all - create it with proper appearance streams
                            if '/AP' not in annot:
                                annot['/AP'] = pikepdf.Dictionary()
                            self._create_radio_appearance(pdf, annot, value_name, annot_w, annot_h, ca_char)
                        
                        annot['/P'] = page.obj
                        parent['/Kids'].append(annot)
                        children_found += 1
                        print(f"      [create_radio_groups] Added child {idx} to {group_name}")
                        break
            
            if children_found >= 2:
                pdf.Root['/AcroForm']['/Fields'].append(parent)
                groups_created += 1
                print(f"      [create_radio_groups] SUCCESS: Group {group_name} added to Fields with {children_found} kids")
            else:
                print(f"      [create_radio_groups] FAILURE: Group {group_name} has only {children_found} matches - NOT added")

        
        # Remove the widgets that became radio children from the Fields array
