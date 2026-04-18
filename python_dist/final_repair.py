
import sys

def repair():
    sf_path = "smart_fillable_v21.py"
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

    # Find the next method
    end_line = -1
    for i in range(start_line + 1, len(lines)):
        if lines[i].startswith("    def ") or lines[i].startswith("class "):
            end_line = i
            break
    
    if end_line == -1: end_line = len(lines)
    
    clean_body = """        \"\"\"Add a checkbox to the detection list. Returns True if added.\"\"\"
        if page_num in self.tally_sheet_pages:
            return False
        if self._is_duplicate_checkbox(page_num, x, y):
            return False
        if y < 40:
            return False
        if self.box_entry_areas:
            cb_cx, cb_cy = x + w/2, y + h/2
            for area in self.box_entry_areas:
                if (area['page'] == page_num and area['x0'] <= cb_cx <= area['x1'] and area['y0'] <= cb_cy <= area['y1']):
                    return False
        if self._is_in_quantity_column(page_num, x, y):
            return False
        label = self._find_nearby_text(page_num, x + w, y, direction='right', max_dist=45)
        if not label:
            label = self._find_label_above(page_num, x, y, w)
        if not label or len(label) < 2:
            try:
                rt = (self.pdf.pages[page_num].within_bbox((x-60, y-5, x+5, y+h+5)).extract_text() or "").strip()
                if re.search(r'[A-Z]{1,3}-\\\\d{1,4}', rt):
                    return False
            except: pass
        if label:
            ll = label.lower()
            ak = ['apt', 'unit', 'suite', 'city', 'state', 'zip', 'no.', 'number', 'amount', 'manufacturer', 'model #', 'serial #', 'license']
            ok = ['yes', 'no', 'complete', 'agree', 'other:', 'pass', 'fail']
            for kw in ak:
                if kw in ll:
                    if not any(o in ll for o in ok):
                        return False 
        if source not in ['vector', 'character']:
            col_key = (page_num, round(x / 15) * 15)
            if self._checkbox_column_counts.get(col_key, 0) >= 60:
                return False 
        if page_num == 0 and (not label or label.lower() == 'none' or len(label) < 3):
             return False
        self.checkboxes.append({'page': page_num, 'x': x, 'y': y, 'width': w, 'height': h, 'label': label, 'row_y': round(y), 'source': source})
        self.checkbox_positions.append({'page': page_num, 'x0': x-2, 'y0': y-2, 'x1': x+w+2, 'y1': y+h+2, 'source': source})
        col_key = (page_num, round(x / 15) * 15)
        self._checkbox_column_counts[col_key] = self._checkbox_column_counts.get(col_key, 0) + 1
        return True
"""
    
    new_lines = lines[:start_line+1] + [clean_body] + lines[end_line:]
    with open(sf_path, "w") as f:
        f.writelines(new_lines)
    print("Repair successful!")

if __name__ == "__main__":
    repair()
