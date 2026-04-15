
import sys

def patch():
    sf_path = "smart_fillable_v21.py"
    with open(sf_path, "r") as f:
        content = f.read()
    
    old_start = "def _add_checkbox(self, page_num: int, x: float, y: float, w: float, h: float, source: str) -> bool:"
    
    # INDENTED BODY (4 spaces based on the function def having 4 spaces)
    new_func_internal = \"\"\"    \"\"\"Add a checkbox to the detection list. Returns True if added.\"\"\"
    if page_num in self.tally_sheet_pages:
        if page_num == 2: print(f"      [CB-REJECT] P3 tally sheet")
        return False
    
    if self._is_duplicate_checkbox(page_num, x, y):
        if page_num == 2: print(f"      [CB-REJECT] P3 duplicate at {x:.1f},{y:.1f}")
        return False

    if y < 40:
        if page_num == 2: print(f"      [CB-REJECT] P3 too high at {x:.1f},{y:.1f}")
        return False
    
    if self.box_entry_areas:
        cb_center_x = x + w / 2
        cb_center_y = y + h / 2
        for area in self.box_entry_areas:
            if area['page'] == page_num and area['x0'] <= cb_center_x <= area['x1'] and area['y0'] <= cb_center_y <= area['y1']:
                if page_num == 2: print(f"      [CB-REJECT] P3 box entry at {x:.1f},{y:.1f}")
                return False
                
    if self._is_in_quantity_column(page_num, x, y):
        if page_num == 2: print(f"      [CB-REJECT] P3 quantity col at {x:.1f},{y:.1f}")
        return False
        
    label = self._find_nearby_text(page_num, x + w, y, direction='right', max_dist=45)
    if not label:
        label = self._find_label_above(page_num, x, y, w)
        
    if not label or len(label) < 2:
        try:
            scan_rect = (x - 60, y - 5, x + 5, y + h + 5)
            rt = (self.pdf.pages[page_num].within_bbox(scan_rect).extract_text() or \"\").strip()
            if re.search(r'[A-Z]{1,3}-\\d{1,4}', rt):
                if page_num == 2: print(f"      [CB-REJECT] P3 icon shield at {x:.1f},{y:.1f}")
                return False
        except: pass
        
    if label:
        ll = label.lower()
        ak = ['apt', 'unit', 'suite', 'city', 'state', 'zip', 'no.', 'number', 'amount', 'manufacturer', 'model #', 'serial #', 'license']
        ok = ['yes', 'no', 'complete', 'agree', 'other:', 'pass', 'fail']
        for kw in ak:
            if kw in ll:
                valid = False
                if kw == 'city' and any(v in ll for v in ['electricity', 'capacity', 'velocity']): valid = True
                if kw == 'unit' and any(v in ll for v in ['family', 'home', 'townhome', 'rowhome', 'duplex', 'apartment', 'heater']): valid = True
                if any(o in ll for o in ok): valid = True
                if not valid:
                    if page_num == 2: print(f"      [CB-REJECT] P3 anti-kw '{kw}' in '{label}'")
                    return False
                    
    if source not in ['vector', 'character']:
        ck = (page_num, round(x / 15) * 15)
        if self._checkbox_column_counts.get(ck, 0) >= 60:
            if page_num == 2: print(f"      [CB-REJECT] P3 col limit")
            return False
            
    if page_num == 0 and (not label or label.lower() == 'none' or len(label) < 3):
        return False

    self.checkboxes.append({'page': page_num, 'x': x, 'y': y, 'width': w, 'height': h, 'label': label, 'row_y': round(y), 'source': source})
    self.checkbox_positions.append({'page': page_num, 'x0': x-2, 'y0': y-2, 'x1': x+w+2, 'y1': y+h+2, 'source': source})
    ck = (page_num, round(x / 15) * 15)
    self._checkbox_column_counts[ck] = self._checkbox_column_counts.get(ck, 0) + 1
    if page_num == 2: print(f"      [CB-OK] P3 '{label}' at ({x:.1f},{y:.1f}) source={source}")
    return True
\"\"\"
    
    with open(sf_path, "r") as f:
        lines = f.readlines()
    
    start_line = -1
    for i, line in enumerate(lines):
        if "def _add_checkbox(self, page_num: int, x: float, y: float, w: float, h: float, source: str) -> bool:" in line:
            start_line = i
            break
            
    if start_line == -1:
        print("Could not find _add_checkbox")
        return

    # Find where the NEXT function starts
    end_line = -1
    for i in range(start_line + 1, len(lines)):
        if lines[i].startswith("    def ") or lines[i].startswith("class "):
            end_line = i
            break
    
    if end_line == -1:
        end_line = len(lines)
        
    print(f"Replacing lines {start_line+1} to {end_line}")
    
    # Indent the internal block by another 4 spaces
    # Actually, new_func_internal already starts with 4 spaces.
    # The previous attempt failed because the VERY FIRST LINE was not indented correctly maybe?
    
    # Let's ensure EVERY line has 8 spaces (4 for function body + 4 for current indent level)
    # Wait, the function definition starts with 4 spaces. So its body should have 8 spaces.
    
    indented_body = []
    for line in new_func_internal.split('\\n'):
        if line.strip():
            indented_body.append("    " + line + "\\n")
        else:
            indented_body.append("\\n")
            
    new_content = lines[:start_line+1] + indented_body + lines[end_line:]
    
    with open(sf_path, "w") as f:
        f.writelines(new_content)
    print("Patch successful")

if __name__ == "__main__":
    patch()
