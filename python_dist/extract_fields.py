#!/usr/bin/env python3
"""
Extract fields from a fillable PDF and convert pages to images.
Refactored to use pypdfium2 for rendering and pikepdf for metadata.
No longer depends on PyMuPDF (fitz).
"""

import sys
import json
import argparse
import tempfile
import re
from pathlib import Path

try:
    import pypdfium2 as pdfium
except ImportError as e:
    print(json.dumps({"error": f"Missing dependency: pypdfium2 - {e}"}))
    sys.exit(1)

try:
    import pikepdf
except ImportError as e:
    print(json.dumps({"error": f"Missing dependency: pikepdf - {e}"}))
    sys.exit(1)

try:
    import pdfplumber
except ImportError:
    pdfplumber = None  # Graceful fallback — text extraction won't be available


def _extract_nearby_text(pdf_path: str, fields_list: list, page_cropdata: dict) -> dict:
    """Extract text near each field using pdfplumber.

    Returns dict mapping field index -> {"left": str, "above": str}
    pdfplumber uses top-down coordinates (top/bottom) which aligns with our field coords.
    """
    if pdfplumber is None:
        return {}

    nearby_map = {}
    try:
        with pdfplumber.open(pdf_path) as pdf:
            # Cache words per page
            page_words = {}
            for page_num in range(len(pdf.pages)):
                try:
                    page_words[page_num] = pdf.pages[page_num].extract_words()
                except Exception:
                    page_words[page_num] = []

            for field in fields_list:
                idx = field.get('index', 0)
                page_num = field.get('page', 0)
                if page_num not in page_words or not page_words[page_num]:
                    continue

                words = page_words[page_num]
                rect = field.get('rect', [0, 0, 0, 0])
                fx0, fy0, fx1, fy1 = rect

                # Get crop offsets for this page to align pdfplumber coords with field coords
                crop = page_cropdata.get(page_num, {})
                cx_off = crop.get('x_offset', 0)
                cy_top = crop.get('y_top', 0)

                nearby = {}

                # Search LEFT of field (within 200pt, same vertical band)
                left_words = []
                for w in words:
                    # pdfplumber word coords: x0, x1 (horizontal), top, bottom (vertical, top-down)
                    wx0, wx1 = float(w['x0']), float(w['x1'])
                    wtop, wbot = float(w['top']), float(w['bottom'])

                    # Adjust pdfplumber coords to match our field coord system
                    # pdfplumber coords are relative to CropBox, same as our field coords
                    # But our fields have crop_x_offset subtracted, so we need to do the same
                    wx0 -= cx_off
                    wx1 -= cx_off

                    # Check: word is to the left and vertically overlapping
                    if wx1 <= fx0 + 2 and wx1 >= fx0 - 200:
                        v_overlap = min(wbot, fy1) - max(wtop, fy0)
                        if v_overlap > -5:  # Allow 5pt tolerance
                            left_words.append((wx0, w['text']))

                if left_words:
                    left_words.sort(key=lambda t: t[0])
                    nearby['left'] = ' '.join(t[1] for t in left_words[-6:])  # Closest 6 words

                # Search ABOVE field (within 40pt, horizontally overlapping)
                above_words = []
                for w in words:
                    wx0, wx1 = float(w['x0']) - cx_off, float(w['x1']) - cx_off
                    wtop, wbot = float(w['top']), float(w['bottom'])

                    if wbot <= fy0 + 2 and wbot >= fy0 - 40:
                        h_overlap = min(wx1, fx1) - max(wx0, fx0)
                        if h_overlap > -30:  # Allow 30pt horizontal tolerance
                            above_words.append((wtop, wx0, w['text']))

                if above_words:
                    above_words.sort(key=lambda t: (-t[0], t[1]))  # Closest first, left to right
                    nearby['above'] = ' '.join(t[2] for t in above_words[:10])

                # Search SAME ROW for a rebate/rate constant — dollar amount on the same row
                # as the field but to the LEFT, between the row label and the input field.
                # e.g. "$35 .00" sitting between the measure description and the qty field.
                import re as _re
                row_mid = (fy0 + fy1) / 2
                row_tolerance = max((fy1 - fy0) * 0.8, 6)  # vertical band around row center
                row_constant_words = []
                for w in words:
                    wx0, wx1 = float(w['x0']) - cx_off, float(w['x1']) - cx_off
                    wtop, wbot = float(w['top']), float(w['bottom'])
                    wmid = (wtop + wbot) / 2

                    # Same row: word center within tolerance of field center
                    if abs(wmid - row_mid) > row_tolerance:
                        continue
                    # To the LEFT of the field (not overlapping)
                    if wx1 > fx0 + 5:
                        continue
                    row_constant_words.append((wx0, w['text']))

                if row_constant_words:
                    row_text = ' '.join(t[1] for t in sorted(row_constant_words))
                    # Look for dollar amount: "$35 .00", "$35.00", "$1,300", etc.
                    # The PDF sometimes splits "$35" and ".00" as separate words
                    dollar_match = _re.search(
                        r'\$\s*([\d,]+(?:\.\d+)?)\s*(?:\.\d+)?',
                        row_text
                    )
                    if dollar_match:
                        raw = dollar_match.group(1).replace(',', '')
                        # Handle case where cents are separate token ("$35 .00" → 35.00)
                        after = row_text[dollar_match.end():].strip()
                        cents_match = _re.match(r'^\.(\d+)', after)
                        try:
                            val_str = raw + (f'.{cents_match.group(1)}' if cents_match else '')
                            val = float(val_str)
                            if 0 < val < 1000000:
                                nearby['rowConstant'] = val
                                nearby['rowConstantText'] = row_text.strip()
                        except ValueError:
                            pass

                if nearby:
                    nearby_map[idx] = nearby
    except Exception as e:
        sys.stderr.write(f"Warning: Text extraction failed: {e}\n")

    return nearby_map


def _resolve_bg_color(annot) -> str:
    """Resolve background color, checking widget first then hierarchy."""
    if '/MK' in annot and '/BG' in annot['/MK']:
        bg = annot['/MK']['/BG']
        if len(bg) >= 3:
            r, g, b = [int(float(c) * 255) for c in bg]
            return f"#{r:02x}{g:02x}{b:02x}"
    
    # Check parents
    curr = annot
    while '/Parent' in curr:
        curr = curr['/Parent']
        if '/MK' in curr and '/BG' in curr['/MK']:
             bg = curr['/MK']['/BG']
             if len(bg) >= 3:
                r, g, b = [int(float(c) * 255) for c in bg]
                return f"#{r:02x}{g:02x}{b:02x}"
    return ""

def _resolve_bc_color(annot) -> str:
    """Resolve border color, checking widget first then hierarchy."""
    if '/MK' in annot and '/BC' in annot['/MK']:
        bc = annot['/MK']['/BC']
        if len(bc) >= 3:
            r, g, b = [int(float(c) * 255) for c in bc]
            return f"#{r:02x}{g:02x}{b:02x}"
    
    # Check parents
    curr = annot
    while '/Parent' in curr:
        curr = curr['/Parent']
        if '/MK' in curr and '/BC' in curr['/MK']:
             bc = curr['/MK']['/BC']
             if len(bc) >= 3:
                r, g, b = [int(float(c) * 255) for c in bc]
                return f"#{r:02x}{g:02x}{b:02x}"
    return ""

def _resolve_attribute(annot, key, default=None):
    """Resolve an attribute by walking up the Parent chain."""
    if key in annot: return annot[key]
    
    curr = annot
    while '/Parent' in curr:
        curr = curr['/Parent']
        if key in curr: return curr[key]
    
    return default

def _resolve_dest_to_page_index(dest, pdf_pike) -> int:
    """Attempt to resolve a pikepdf destination (Named or Array) to a 0-based page index.
    Returns None if it cannot be resolved."""
    if isinstance(dest, (pikepdf.String, pikepdf.Name)):
        try:
            resolved_dest = None
            if '/Dests' in pdf_pike.Root:
                resolved_dest = pdf_pike.Root.Dests.get(str(dest))
            if not resolved_dest and '/Names' in pdf_pike.Root and '/Dests' in pdf_pike.Root.Names:
                names = pdf_pike.Root.Names.Dests.get('/Names', [])
                for i in range(0, len(names), 2):
                    if str(names[i]) == str(dest):
                        resolved_dest = names[i+1]
                        break
            if resolved_dest:
                if isinstance(resolved_dest, pikepdf.Dictionary) and '/D' in resolved_dest:
                    dest = resolved_dest['/D']
                else:
                    dest = resolved_dest
        except Exception:
            pass

    if isinstance(dest, pikepdf.Array) and len(dest) > 0:
        page_ref = dest[0]
        try:
            if isinstance(page_ref, int):
                return page_ref
            # Iterate pages to find the matching indirect object
            for i, p in enumerate(pdf_pike.pages):
                if p.objgen == page_ref.objgen:
                    return i
        except Exception:
            pass
            
    return None

def _extract_choice_options(annot) -> list:
    """Extract options from a choice field (/Ch)."""
    opts = _resolve_attribute(annot, '/Opt')
    if opts is None: return []
    
    parsed = []
    for opt in opts:
        if isinstance(opt, pikepdf.Array):
            # [export_value, display_name]
            parsed.append({
                "value": str(opt[0]),
                "label": str(opt[1])
            })
        else:
            # simple string
            s = str(opt)
            parsed.append({
                "value": s,
                "label": s
            })
    return parsed

def _extract_choice_flags(ff: int) -> dict:
    """Extract choice-specific flags."""
    return {
        "editable": bool(ff & 0x40000),      # Edit (bit 19)
        "sort": bool(ff & 0x80000),          # Sort (bit 20)
        "multiSelect": bool(ff & 0x200000),  # MultiSelect (bit 22)
        "spellCheck": not bool(ff & 0x400000), # DoNotSpellCheck (bit 23) - Note: inverse
        "commitImmediately": bool(ff & 0x4000000) # CommitOnSelChange (bit 26)
    }

def _get_field_type_resolved(annot) -> str:
    ft = str(_resolve_attribute(annot, '/FT', ''))
    if '/Tx' in ft: return "text"
    if '/Btn' in ft:
        ff = int(_resolve_attribute(annot, '/Ff', 0))
        if ff & 0x8000: return "radio" # Radio flag
        if ff & 0x10000: return "button" # Pushbutton flag
        
        # Check kids for radio grouping
        if '/Kids' in annot: return "radio"
        
        # Also check parent's kids if we are a a child
        if '/Parent' in annot:
            # If parent has FT=Btn and Ff=Radio, we are a radio option
            p_ff = int(_resolve_attribute(annot, '/Ff', 0)) # Already resolved above, but confirm
            if p_ff & 0x8000: return "radio"
            
        return "checkbox"
    if '/Ch' in ft:
        ff = int(_resolve_attribute(annot, '/Ff', 0))
        # Combo flag is bit 18 (0x20000)
        if ff & 0x20000: return "combobox"
        return "listbox"
    if '/Sig' in ft: return "signature"
    return "unknown"

def extract_fields_and_images(pdf_path: str, output_dir: str = None) -> dict:
    """
    Extract all form fields and convert pages to images.
    """
    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="pdf_editor_")
    
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    result = {
        "pages": [],
        "fields": [],
        "radio_groups": {},
        "output_dir": str(output_path)
    }
    
    # Track name counts for group indexing
    name_counts = {}
    # Track crop data per page for text extraction
    page_cropdata = {}

    # Open PDF with pypdfium2 for rendering
    pdf_ium = pdfium.PdfDocument(pdf_path)
    
    # Open with pikepdf for metadata extraction
    pdf_pike = pikepdf.Pdf.open(pdf_path)
            
    # Scale for rendering (300 DPI / 72 DPI standard)
    # Rendered at 2x the coordinate-math DPI (150) so the preview stays sharp up
    # to ~200% editor zoom and degrades gracefully at higher zooms. The JS side
    # still uses DPI_SCALE = 150/72 for ALL coordinate math; it treats the extra
    # pixels as browser-downsampled oversampling.
    zoom = 300 / 72
    
    # Extract page info, fields, and render images
    for page_num in range(len(pdf_ium)):
        page_ium = pdf_ium[page_num]
        page_pike = pdf_pike.pages[page_num]
        
        # Get dimensions (in points) - this returns CropBox size (what gets rendered)
        width, height = page_ium.get_size()

        # Determine CropBox/MediaBox offset for coordinate transformation
        # Annotations use MediaBox coordinates, but rendering shows CropBox area
        mediabox = [float(x) for x in page_pike.get('/MediaBox', [0, 0, width, height])]
        cropbox_raw = page_pike.get('/CropBox', None)
        if cropbox_raw is not None:
            cropbox = [float(x) for x in cropbox_raw]
        else:
            cropbox = mediabox[:]
        # Offsets: how much the CropBox origin differs from MediaBox origin
        crop_x_offset = cropbox[0] - mediabox[0]  # Usually 0
        crop_y_offset = cropbox[1] - mediabox[1]  # e.g., 18pt for some PDFs
        # The top of the rendered area in MediaBox coords
        crop_y_top = cropbox[3]

        # Store crop data for text extraction
        page_cropdata[page_num] = {'x_offset': crop_x_offset, 'y_top': crop_y_top}

        # Render image
        bitmap = page_ium.render(scale=zoom)
        pil_image = bitmap.to_pil()
        img_path = output_path / f"page_{page_num}.png"
        pil_image.save(str(img_path))

        result["pages"].append({
            "page_num": page_num,
            "width": width,
            "height": height,
            "image_path": str(img_path)
        })

        # Extract widgets from pikepdf
        if '/Annots' in page_pike:
            for annot in page_pike.Annots:
                if annot.get('/Subtype') != pikepdf.Name('/Widget'):
                    continue

                # Resolve attributes
                resolved_t = _resolve_attribute(annot, '/T', f"Unnamed_{page_num}_{len(result['fields'])}")
                f_name = str(resolved_t)

                g_idx = name_counts.get(f_name, 0)
                name_counts[f_name] = g_idx + 1

                # Rect handling (PDF coords are bottom-up, editor wants top-down)
                # Annotations are in MediaBox coordinate space, but the rendered image
                # shows only the CropBox area. Use crop_y_top for Y-flip and offset X.
                rect = [float(c) for c in annot.get('/Rect', [0, 0, 0, 0])]
                # Normalize inverted rects (some PDF editors produce B>T or L>R)
                if rect[0] > rect[2]:
                    rect[0], rect[2] = rect[2], rect[0]
                if rect[1] > rect[3]:
                    rect[1], rect[3] = rect[3], rect[1]
                v_rect = [rect[0] - crop_x_offset, crop_y_top - rect[3],
                          rect[2] - crop_x_offset, crop_y_top - rect[1]]
                
                # Quadding (0=L, 1=C, 2=R) - Inheritable
                raw_q = int(_resolve_attribute(annot, "/Q", 0))
                
                # Visibility (F flag) - typically on widget
                visibility = "visible"
                f_flags = int(annot.get("/F", 0))
                if f_flags & 2:
                    visibility = "hidden"
                elif f_flags & 1:
                    visibility = "hidden"
                    
                # Orientation (R in MK dict)
                orientation = 0
                if "/MK" in annot and "/R" in annot["/MK"]:
                    orientation = int(annot["/MK"]["/R"])
                
                # Parse DA string for font and color
                da = str(_resolve_attribute(annot, '/DA', ''))
                font_tag, font_size, text_color = _parse_da(da)
                
                # Get field type with resolved attributes
                f_type = _get_field_type_resolved(annot)
                ff = int(_resolve_attribute(annot, '/Ff', 0))
                
                # Stable PDF object identifier (survives deletions of other fields)
                _objgen = list(annot.objgen) if annot.is_indirect else None

                field_info = {
                    "name": f_name,
                    "groupIndex": g_idx,
                    "index": len(result["fields"]),
                    "objgen": _objgen,
                    "type": f_type,
                    "page": page_num,
                    "rect": v_rect,
                    "value": str(_resolve_attribute(annot, '/V', "")),
                    "fontSize": font_size,
                    "fontFamily": _map_font_tag(font_tag),
                    "fontColor": text_color,
                    "fillColor": _resolve_bg_color(annot),
                    "borderColor": _resolve_bc_color(annot),
                    "textAlign": {0: "left", 1: "center", 2: "right"}.get(raw_q, "left"),
                    "tooltip": str(_resolve_attribute(annot, '/TU', "")),
                    "readOnly": bool(ff & 1),
                    "required": bool(ff & 2),
                    "multiline": bool(ff & 4096),
                    "maxLen": int(_resolve_attribute(annot, '/MaxLen', 0)),
                    "visibility": visibility,
                    "orientation": orientation,
                    "exportValue": _get_export_value_pike(annot),
                    "lineThickness": _get_line_thickness_pike(annot),
                    "lineStyle": _get_line_style_pike(annot),
                    "checkboxStyle": _get_checkbox_style_pike(annot),
                }

                # Extra info for Choice fields
                if f_type in ["combobox", "listbox"]:
                    field_info["options"] = _extract_choice_options(annot)
                    field_info.update(_extract_choice_flags(ff))

                # Extract JavaScript actions (AA can be on Widget or Parent)
                # Usually we want the Widget's AA for interaction, but format might be inherited
                actions = {}
                # Helper to read JS which may be a string or a compressed Stream
                def _read_js(js_obj):
                    if isinstance(js_obj, pikepdf.Stream):
                        return js_obj.read_bytes().decode('utf-8', errors='replace')
                    return str(js_obj)

                # Helper to extract from a dict
                def extract_actions_from_dict(d):
                    acts = {}
                    # Check AA (Additional Actions)
                    if '/AA' in d:
                        aa = d['/AA']
                        if '/U' in aa and '/JS' in aa['/U']: acts['Mouse Up'] = _read_js(aa['/U']['/JS'])
                        if '/K' in aa and '/JS' in aa['/K']: acts['Keystroke'] = _read_js(aa['/K']['/JS'])
                        if '/F' in aa and '/JS' in aa['/F']: acts['Format'] = _read_js(aa['/F']['/JS'])
                        if '/C' in aa and '/JS' in aa['/C']: acts['Calculate'] = _read_js(aa['/C']['/JS'])
                        if '/Bl' in aa and '/JS' in aa['/Bl']: acts['On Blur'] = _read_js(aa['/Bl']['/JS'])
                        if '/Fo' in aa and '/JS' in aa['/Fo']: acts['On Focus'] = _read_js(aa['/Fo']['/JS'])

                    # Check A (Primary Action) - commonly used for pushbuttons
                    if '/A' in d:
                        a = d['/A']
                        if '/S' in a and a['/S'] == '/JavaScript' and '/JS' in a:
                             acts['Action'] = _read_js(a['/JS'])

                    return acts

                # Merge parent actions then widget actions (widget overrides).
                # Some PDFs put /AA on the parent field dict (format, keystroke)
                # and /AA/C on either widget or parent.  Merge both so nothing
                # is missed; widget keys override parent keys.
                actions = {}
                if '/Parent' in annot:
                    actions = extract_actions_from_dict(annot['/Parent'])
                widget_actions = extract_actions_from_dict(annot)
                actions.update(widget_actions)  # widget overrides parent
                
                if actions:
                    field_info['actions'] = actions
                    format_type, details = _extract_format_details(actions)
                    if format_type:
                        field_info['formatType'] = format_type
                        field_info.update(details)
                    # Parse existing calculation JS into structured object
                    if 'Calculate' in actions:
                        calc_info = _parse_calculate_js(actions['Calculate'], field_name=f_name)
                        if calc_info:
                            field_info['calculation'] = calc_info

                # Check for radio button grouping
                if field_info["type"] == "radio":
                    parent_name = f_name
                    if parent_name:
                        if parent_name not in result["radio_groups"]:
                            result["radio_groups"][parent_name] = []
                        result["radio_groups"][parent_name].append(field_info["name"])
                        field_info["radio_group"] = parent_name
                
                result["fields"].append(field_info)

        # Extract hyperlink (/Link) annotations
        if '/Annots' in page_pike:
            link_idx = 0
            for annot in page_pike.Annots:
                if annot.get('/Subtype') != pikepdf.Name('/Link'):
                    continue

                rect = [float(c) for c in annot.get('/Rect', [0, 0, 0, 0])]
                # Normalize rect (some PDFs have inverted coordinates)
                if rect[0] > rect[2]:
                    rect[0], rect[2] = rect[2], rect[0]
                if rect[1] > rect[3]:
                    rect[1], rect[3] = rect[3], rect[1]
                # Skip degenerate rects
                if rect[2] - rect[0] < 1 or rect[3] - rect[1] < 1:
                    continue

                v_rect = [rect[0] - crop_x_offset, crop_y_top - rect[3],
                          rect[2] - crop_x_offset, crop_y_top - rect[1]]

                # Extract URL or destination
                url = ""
                dest_type = ""
                raw_dest = None

                action = annot.get('/A')
                if action:
                    action_type = str(action.get('/S', ''))
                    if '/URI' in action_type:
                        url = str(action.get('/URI', ''))
                        dest_type = "url"
                    elif '/GoTo' in action_type:
                        raw_dest = action.get('/D')
                    elif '/GoToR' in action_type:
                        url = str(action.get('/F', ''))
                        dest_type = "goto_remote"
                elif '/Dest' in annot:
                    raw_dest = annot.get('/Dest')

                # Handle internal destinations (both Named and Explicit Arrays)
                if raw_dest is not None:
                    resolved_idx = _resolve_dest_to_page_index(raw_dest, pdf_pike)
                    if resolved_idx is not None:
                        url = str(resolved_idx)
                        dest_type = "goto"
                    else:
                        # Fallback if unresolved string
                        url = str(raw_dest)
                        dest_type = "dest"

                # Generate a stable name for this link annotation
                link_name = f"Link_P{page_num}_{link_idx}"
                link_idx += 1

                # Border style
                border = annot.get('/Border', None)
                border_width = 0
                if border:
                    try:
                        border_vals = [float(x) for x in border]
                        if len(border_vals) >= 3:
                            border_width = border_vals[2]
                    except Exception:
                        pass

                _link_objgen = list(annot.objgen) if annot.is_indirect else None

                link_info = {
                    "name": link_name,
                    "groupIndex": 0,
                    "index": len(result["fields"]),
                    "objgen": _link_objgen,
                    "type": "link",
                    "page": page_num,
                    "rect": v_rect,
                    "url": url,
                    "destType": dest_type,
                    "value": url,
                    "fontSize": 0,
                    "fontFamily": "",
                    "fontColor": "",
                    "fillColor": "transparent",
                    "borderColor": "#000000",
                    "textAlign": "left",
                    "tooltip": url[:80] if url else "",
                    "readOnly": False,
                    "required": False,
                    "multiline": False,
                    "maxLen": 0,
                    "visibility": "visible",
                    "orientation": 0,
                    "exportValue": "",
                    "lineThickness": border_width,
                    "lineStyle": "solid",
                    "checkboxStyle": "",
                }

                result["fields"].append(link_info)

    pdf_ium.close()
    pdf_pike.close()

    # Extract nearby text for each field (non-fatal if pdfplumber unavailable)
    try:
        nearby_text = _extract_nearby_text(pdf_path, result['fields'], page_cropdata)
        for field in result['fields']:
            idx = field.get('index', 0)
            if idx in nearby_text:
                field['nearbyText'] = nearby_text[idx]
    except Exception as e:
        sys.stderr.write(f"Warning: Nearby text extraction skipped: {e}\n")

    return result


def _get_field_type_from_pike(annot) -> str:
    # Legacy wrapper if needed, but we used _get_field_type_resolved above
    return _get_field_type_resolved(annot)


def _parse_da(da: str):
    """Parse DA string like '0 0 0 rg /Helv 10 Tf'"""
    if not da:
        return 'Helv', 10, '#000000'
    
    tag_match = re.search(r'/(\w+)', da)
    tag = tag_match.group(1) if tag_match else 'Helv'
    
    # v22.7: Fixed regex to correctly handle font tags of any length
    size_match = re.search(r'/\w+\s+([\d.]+)\s+Tf', da)
    size = float(size_match.group(1)) if size_match else 10
    
    color = '#000000'
    # Check for RGB color
    rgb_match = re.search(r'([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+rg', da)
    if rgb_match:
        r, g, b = [int(float(c) * 255) for c in rgb_match.groups()]
        color = f"#{r:02x}{g:02x}{b:02x}"
    
    return tag, size, color


def _map_font_tag(tag: str) -> str:
    tag_lower = tag.lower()
    if "cour" in tag_lower: return "Courier"
    if "times" in tag_lower or "tiro" in tag_lower or tag_lower.startswith("tr"): return "Times-Roman"
    if "arial" in tag_lower: return "Arial"
    if "verdana" in tag_lower: return "Verdana"
    if "georgia" in tag_lower: return "Georgia"
    if "calibri" in tag_lower: return "Calibri"
    if "cambria" in tag_lower: return "Cambria"
    if "trebuchet" in tag_lower: return "Trebuchet MS"
    if "tahoma" in tag_lower: return "Tahoma"
    if "helv" in tag_lower: return "Helvetica"
    # Unrecognised — preserve the raw tag so round-tripping does not
    # silently change the font to Helvetica.
    return tag if tag else "Helvetica"


def _get_bg_color(annot) -> str:
    if '/MK' in annot and '/BG' in annot['/MK']:
        bg = annot['/MK']['/BG']
        if len(bg) >= 3:
            r, g, b = [int(float(c) * 255) for c in bg]
            return f"#{r:02x}{g:02x}{b:02x}"
    return ""


def _get_export_value_pike(annot) -> str:
    if '/AP' in annot and '/N' in annot['/AP']:
        ap_n = annot['/AP']['/N']
        for key in ap_n.keys():
            k_str = str(key)
            if k_str not in ['/Off', 'Off']:
                return k_str.lstrip('/')
    return "Yes"


def _get_line_thickness_pike(annot) -> int:
    if '/BS' in annot and '/W' in annot['/BS']:
        w = float(annot['/BS']['/W'])
        if w == 0: return 0
        if w < 1.5: return 1
        if w < 2.5: return 2
        return 3
    return 0


def _get_line_style_pike(annot) -> str:
    if '/BS' in annot and '/S' in annot['/BS']:
        s = str(annot['/BS']['/S'])
        if '/D' in s: return 'dashed'
        if '/U' in s: return 'underline'
    return 'solid'
    
def _get_checkbox_style_pike(annot) -> str:
    """Map ZapfDingbats character in /MK/CA to style name."""
    if '/MK' in annot and '/CA' in annot['/MK']:
        ca = str(annot['/MK']['/CA']).strip('()') # Handle (l) vs l
        char_map = {
            "4": "check",
            "8": "cross",
            "l": "circle",
            "u": "diamond",
            "n": "square",
            "H": "star"
        }
        return char_map.get(ca, "check")
    return "check"



def _parse_calculate_js(js_str: str, field_name: str = None) -> dict:
    """Parse a Calculate JavaScript string back into a structured calculation object.

    Returns dict like: {"type": "SUM", "sources": ["field1", "field2"]}
    or empty dict if not parseable.
    """
    if not js_str:
        return {}

    # Try AFSimple_Calculate("SUM", new Array("field1", "field2"))
    af_match = re.search(
        r'AFSimple_Calculate\s*\(\s*["\'](\w+)["\']\s*,\s*(?:new\s+Array\s*\()?([^)]+)\)',
        js_str, re.IGNORECASE
    )
    if af_match:
        af_type = af_match.group(1).upper()
        fields_str = af_match.group(2)
        field_names = re.findall(r'["\']([^"\']+)["\']', fields_str)
        type_map = {"SUM": "SUM", "PRD": "PRODUCT", "AVG": "AVERAGE", "MIN": "MIN", "MAX": "MAX"}
        return {
            "type": type_map.get(af_type, af_type),
            "sources": field_names
        }

    # Extract field refs — support both this.getField("x") and getField("x")
    field_refs = re.findall(r'(?:this\.)?getField\s*\(\s*["\']([^"\']+)["\']\s*\)', js_str)
    # Deduplicate while preserving order
    seen = set()
    unique_refs = []
    for ref in field_refs:
        if ref not in seen:
            seen.add(ref)
            unique_refs.append(ref)
    field_refs = unique_refs

    if not field_refs:
        return {}

    # Check for complex logic that should not be mangled into simple arithmetic
    # (variables, control flow, or multiple statements)
    is_complex = bool(re.search(r'\b(if|else|for|while|var|let|const|return|switch|case)\b', js_str))
    
    # We also check if there are multiple statements (more than one semicolon)
    # ignoring trailing semicolons
    stmt_count = len([s for s in js_str.split(';') if s.strip()])
    if stmt_count > 1 and not is_complex:
        # e.g. "import a; event.value = a + 1"
        is_complex = True

    if is_complex:
        return {
            "type": "CUSTOM_SCRIPT",
            "sources": field_refs,
            "script": js_str
        }

    # Constant multiply: event.value = NUMBER * AFMakeNumber(getField("x").value)
    # Matches patterns like: event.value=35*AFMakeNumber(getField("cal1").value)
    # Also handles: event.value = 4000.0 * ((Number(this.getField("x").value) || 0))
    const_match = re.search(
        r'event\.value\s*=\s*([\d.]+)\s*\*\s*\(*\s*(?:(?:AFMakeNumber|Number)\s*\(\s*)?(?:this\.)?getField',
        js_str
    )
    if const_match and len(field_refs) == 1:
        return {
            "type": "CONSTANT_MULTIPLY",
            "sources": field_refs,
            "constant": float(const_match.group(1))
        }
    # Also match: event.value = AFMakeNumber(getField("x").value) * 0.6
    const_match2 = re.search(
        r'event\.value\s*=\s*(?:AFMakeNumber\s*\(\s*)?(?:this\.)?getField\s*\([^)]+\)\.value\)?\s*\*\s*([\d.]+)\s*$',
        js_str.strip()
    )
    if const_match2 and len(field_refs) == 1:
        return {
            "type": "CONSTANT_MULTIPLY",
            "sources": field_refs,
            "constant": float(const_match2.group(1))
        }

    # Analyze the formula part after event.value=
    formula_match = re.search(r'event\.value\s*=\s*(.+)', js_str, re.DOTALL)
    formula_part = formula_match.group(1).strip() if formula_match else js_str

    # Determine operators used in the formula (ignore operators inside function calls/strings)
    has_add = ' + ' in formula_part
    has_sub = ' - ' in formula_part
    has_mul = ' * ' in formula_part
    has_div = ' / ' in formula_part

    # Pure addition of multiple fields → SUM
    if has_add and not has_sub and not has_mul and not has_div:
        return {"type": "SUM", "sources": field_refs}
    # Pure subtraction → SUBTRACT
    if has_sub and not has_add and not has_mul and not has_div:
        return {"type": "SUBTRACT", "sources": field_refs}
    # Pure multiplication of fields (no constants) → MULTIPLY
    if has_mul and not has_sub and not has_add and not has_div:
        return {"type": "MULTIPLY", "sources": field_refs}
    # Division → DIVIDE
    if has_div and not has_add and not has_sub and not has_mul:
        return {"type": "DIVIDE", "sources": field_refs}

    # Mixed operators or complex formula → CUSTOM with simplified formula
    # Try to extract a simplified formula for the editor UI
    simplified = None

    # Check for BVCALC markers: /** BVCALC simplified_formula EVCALC **/
    bv_match = re.search(r'/\*\*\s*BVCALC\s+(.*?)\s+EVCALC\s*\*\*/', js_str, re.DOTALL)
    if bv_match:
        simplified = bv_match.group(1).strip()
    else:
        # Extract expression after event.value= and strip AFMakeNumber/getField wrappers
        ev_match = re.search(r'event\.value\s*=\s*(.+?)(?:;|\s*$)', js_str, re.DOTALL)
        if ev_match:
            simplified = ev_match.group(1).strip()
            # Replace AFMakeNumber(getField("name").value) → field name
            simplified = re.sub(
                r'AFMakeNumber\s*\(\s*(?:this\.)?getField\s*\(\s*"([^"]+)"\s*\)\s*\.value\s*\)',
                r'\1', simplified)
            # Replace Number(this.getField("name").value) → field name
            simplified = re.sub(
                r'Number\s*\(\s*(?:this\.)?getField\s*\(\s*"([^"]+)"\s*\)\s*\.value\s*\)',
                r'\1', simplified)
            # Replace bare getField("name").value → field name
            simplified = re.sub(
                r'(?:this\.)?getField\s*\(\s*"([^"]+)"\s*\)\s*\.value',
                r'\1', simplified)

    if simplified:
        # Convert field names to letter references (A, B, C...)
        # Sort by name length descending to avoid partial replacements
        for i, ref in sorted(enumerate(field_refs), key=lambda x: len(x[1]), reverse=True):
            letter = chr(65 + i)
            simplified = re.sub(r'\b' + re.escape(ref) + r'\b', letter, simplified)

        # Auto-fix missing * operators (common in source PDFs):
        #   A (B  →  A * (B     and    ) (B  →  ) * (B
        #   )A    →  ) * A      and    2(A   →  2 * (A
        simplified = re.sub(r'([A-Z0-9)])\s*\(', r'\1 * (', simplified)
        simplified = re.sub(r'\)\s*([A-Z0-9])', r') * \1', simplified)

        # Auto-fix unbalanced parentheses (drop extra ')' / append missing ')')
        balanced = []
        depth = 0
        for ch in simplified:
            if ch == '(':
                depth += 1
                balanced.append(ch)
            elif ch == ')':
                if depth > 0:
                    depth -= 1
                    balanced.append(ch)
                # else: skip stray close-paren
            else:
                balanced.append(ch)
        balanced.extend(')' * depth)
        simplified = ''.join(balanced)

        # Auto-strip self-references: a field referencing itself always reads
        # its own (stale) value, producing wrong results (usually 0).
        if field_name and field_name in field_refs:
            self_idx = field_refs.index(field_name)
            self_letter = chr(65 + self_idx)
            n_original = len(field_refs)
            field_refs = [f for f in field_refs if f != field_name]

            # Replace self-ref letter with multiplicative identity (1)
            simplified = re.sub(
                r'(?<![A-Za-z])' + self_letter + r'(?![A-Za-z])',
                '1', simplified)
            # Clean up: (1) → 1, 1 * X → X, X * 1 → X
            for _ in range(3):
                simplified = re.sub(r'\(1\)', '1', simplified)
                simplified = re.sub(r'1\s*\*\s*', '', simplified)
                simplified = re.sub(r'\s*\*\s*1(?![0-9])', '', simplified)
            simplified = simplified.strip()

            # Re-letter sources above the removed index (shift down by 1)
            for i in range(self_idx + 1, n_original):
                old_l = chr(65 + i)
                new_l = chr(65 + i - 1)
                simplified = re.sub(
                    r'(?<![A-Za-z])' + old_l + r'(?![A-Za-z])',
                    new_l, simplified)

        return {"type": "CUSTOM", "sources": field_refs, "formula": simplified}

    return {"type": "CUSTOM", "sources": field_refs, "formula": js_str}


def _extract_format_details(actions: dict) -> tuple:
    all_js_orig = "".join(actions.values())
    all_js = all_js_orig.lower()
    details = {}
    
    if 'afdate_' in all_js:
        # AFDate_FormatEx("mm/dd/yyyy")
        match = re.search(r'afdate_formatex\s*\(\s*["\']([^"\']+)["\']', all_js)
        if match:
            fmt = match.group(1)
            fmt_map = {"mm/dd/yyyy": "MM/DD/YYYY", "dd/mm/yyyy": "DD/MM/YYYY", "yyyy-mm-dd": "YYYY-MM-DD", "mm-dd-yyyy": "MM-DD-YYYY"}
            details["dateFormat"] = fmt_map.get(fmt, "MM/DD/YYYY")
        return 'date', details
        
    if 'afspecial_format(2)' in all_js:
        return 'phone', {"phoneFormat": "(XXX) XXX-XXXX"}
        
    if 'afspecial_format(0)' in all_js:
        return 'zip', {"zipFormat": "XXXXX"}
    if 'afspecial_format(1)' in all_js:
        return 'zip', {"zipFormat": "XXXXX-XXXX"}
        
    if 'afnumber_' in all_js:
        # AFNumber_Format(decimals, sepStyle, negStyle, currStyle, currSymbol, bPrepend)
        match = re.search(r'afnumber_format\s*\(\s*(\d+)\s*,\s*(\d+)', all_js)
        if match:
            decimals = int(match.group(1))
            sep_style = int(match.group(2))
            
            # Full 6-param extraction — use original (case-preserved) JS for symbol
            full_match = re.search(
                r'AFNumber_Format\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*["\']([^"\']*?)["\']\s*(?:,\s*(true|false))?\s*\)',
                all_js_orig, re.IGNORECASE)
            if full_match:
                raw_symbol = full_match.group(5)  # may include space
                prepend_str = (full_match.group(6) or 'true').lower()
                prepend = prepend_str == 'true'
                
                if raw_symbol.strip():  # Has a non-empty currency symbol
                    clean_symbol = raw_symbol.strip()
                    has_space = raw_symbol != clean_symbol
                    
                    details["currencySymbol"] = clean_symbol
                    details["currencyDecimals"] = decimals
                    
                    # Determine symbolLocation
                    if prepend and has_space:
                        details["symbolLocation"] = "before-space"
                    elif prepend and not has_space:
                        details["symbolLocation"] = "before-nospace"
                    elif not prepend and has_space:
                        details["symbolLocation"] = "after-space"
                    else:
                        details["symbolLocation"] = "after-nospace"
                    
                    return 'currency', details
            
            # Fallback: check for currency with simpler regex (case-preserved)
            curr_match = re.search(r'AFNumber_Format\s*\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*["\']([^"\']+)["\']', all_js_orig, re.IGNORECASE)
            if curr_match and curr_match.group(1).strip():
                details["currencySymbol"] = curr_match.group(1).strip()
                details["currencyDecimals"] = decimals
                details["symbolLocation"] = "before-space"  # default
                return 'currency', details
            
            details["numberDecimals"] = decimals
            # sepStyle: 0=1,000.00, 1=1000.00, 2=1.000,00, 3=1000,00
            details["thousandSep"] = "," if sep_style == 0 else "." if sep_style == 2 else ""
            return 'number', details
        return 'number', {}

    # Detect custom digit-only keystroke validation scripts as integer number format.
    # Common patterns:
    #   event.rc = /^\d*$/.test(event.change);
    #   var re = /^[0-9]*$/; if (!re.test(event.change)) event.rc = false;
    #   event.rc = /^[0-9]*$/.test(event.change);
    # These are digit-only validation — treat as number with 0 decimals.
    digit_patterns = [
        r'/\^\\d\*\$/',           # /^\d*$/
        r'/\^\[0-9\]\*\$/',      # /^[0-9]*$/
        r'/\^\\d\+\$/',          # /^\d+$/
        r'/\^\[0-9\]\+\$/',      # /^[0-9]+$/
    ]
    for pat in digit_patterns:
        if re.search(pat, all_js):
            details["numberDecimals"] = 0
            details["thousandSep"] = ""
            return 'number', details

    return "", {}


def main():
    parser = argparse.ArgumentParser(description="Extract PDF fields and images")
    parser.add_argument("pdf_path", help="Path to the PDF file")
    parser.add_argument("--output-dir", help="Directory for output images")
    
    args = parser.parse_args()
    
    if not Path(args.pdf_path).exists():
        print(json.dumps({"error": f"PDF not found: {args.pdf_path}"}))
        sys.exit(1)
    
    try:
        result = extract_fields_and_images(args.pdf_path, args.output_dir)
        print(json.dumps(result))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
