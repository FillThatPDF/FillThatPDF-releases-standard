#!/usr/bin/env python3
"""
Modify fields in a PDF based on editor changes.
Refactored to use pikepdf instead of PyMuPDF.
Supports: move, resize, delete, rename, style, create, group/ungroup radio buttons, JS actions.
"""

import sys
import json
import argparse
import pikepdf
import tempfile
import shutil
import re
import math
from pathlib import Path
from typing import List, Dict, Any

def _ensure_font_resources(pdf):
    """Ensure standard fonts exist in AcroForm Default Resources."""
    try:
        if '/AcroForm' not in pdf.Root:
            pdf.Root['/AcroForm'] = pikepdf.Dictionary({
                '/Fields': pikepdf.Array([])
            })
        acroform = pdf.Root['/AcroForm']
        if '/DR' not in acroform:
            acroform['/DR'] = pikepdf.Dictionary()
        dr = acroform['/DR']
        if '/Font' not in dr:
            dr['/Font'] = pikepdf.Dictionary()
        
        font_dict = dr['/Font']
        
        def add_font(key, base_font):
            if key not in font_dict:
                font_dict[key] = pdf.make_indirect(pikepdf.Dictionary({
                    '/Type': pikepdf.Name('/Font'),
                    '/Subtype': pikepdf.Name('/Type1'),
                    '/BaseFont': pikepdf.Name(base_font),
                    '/Encoding': pikepdf.Name('/WinAnsiEncoding')
                }))

        add_font('/Helv', '/Helvetica')
        add_font('/Cour', '/Courier')
        add_font('/TiRo', '/Times-Roman')
        
        # Add ZapfDingbats for checkboxes - no encoding needed for symbolic fonts
        if '/ZaDb' not in font_dict:
            font_dict['/ZaDb'] = pdf.make_indirect(pikepdf.Dictionary({
                '/Type': pikepdf.Name('/Font'),
                '/Subtype': pikepdf.Name('/Type1'),
                '/BaseFont': pikepdf.Name('/ZapfDingbats')
            }))

        if '/Helvetica' not in font_dict:
             add_font('/Helvetica', '/Helvetica')

        # Set AcroForm-level /DA (Default Appearance) — fallback for fields
        # that inherit their DA.  Matches v23 engine's Pass 12 behavior.
        if '/DA' not in acroform:
            acroform['/DA'] = pikepdf.String('/Helv 0 Tf 0 g')

        # Ensure every font tag referenced in a field /DA exists in the
        # AcroForm DR.  Non-standard fonts (Verdana, Georgia, …) are
        # often defined only in page resources or /AP streams.  When we
        # later set NeedAppearances=False and fire calculateNow(),
        # Acrobat regenerates /AP and looks for the font in DR — if it
        # is missing the viewer silently falls back to Helvetica.
        _copy_missing_da_fonts_to_dr(pdf, font_dict)

    except Exception as e:
        sys.stderr.write(f"Warning: Failed to ensure font resources: {e}\n")


def _copy_missing_da_fonts_to_dr(pdf, dr_font_dict):
    """Copy fonts referenced in field /DA strings into AcroForm DR if absent."""
    import re as _re
    dr_keys = set(str(k).lstrip('/') for k in dr_font_dict.keys())
    needed = {}  # tag -> True
    # Collect all font tags used in field /DA strings
    for page in pdf.pages:
        if '/Annots' not in page:
            continue
        for annot_ref in page['/Annots']:
            annot = annot_ref.resolve() if hasattr(annot_ref, 'resolve') else annot_ref
            da = str(annot.get('/DA', ''))
            m = _re.search(r'/(\w+)\s+[\d.]+\s+Tf', da)
            if m:
                tag = m.group(1)
                if tag not in dr_keys:
                    needed[tag] = True
    if not needed:
        return
    # Search page resources and field /AP resources for the missing fonts
    for page in pdf.pages:
        page_fonts = {}
        res = page.get('/Resources')
        if res and '/Font' in res:
            for k in res['/Font'].keys():
                page_fonts[str(k).lstrip('/')] = res['/Font'][k]
        if '/Annots' not in page:
            continue
        for annot_ref in page['/Annots']:
            annot = annot_ref.resolve() if hasattr(annot_ref, 'resolve') else annot_ref
            # Check /AP/N resources
            if '/AP' in annot and '/N' in annot['/AP']:
                ap_n = annot['/AP']['/N']
                if hasattr(ap_n, 'resolve'):
                    ap_n = ap_n.resolve()
                if hasattr(ap_n, 'get') and '/Resources' in ap_n:
                    ap_res = ap_n['/Resources']
                    if '/Font' in ap_res:
                        for k in ap_res['/Font'].keys():
                            tag = str(k).lstrip('/')
                            if tag not in page_fonts:
                                page_fonts[tag] = ap_res['/Font'][k]
        # Copy any found fonts to DR
        for tag in list(needed.keys()):
            if tag in page_fonts:
                dr_font_dict[pikepdf.Name('/' + tag)] = page_fonts[tag]
                del needed[tag]
        if not needed:
            return

def _resolve_attribute(annot, key, default=None):
    """Resolve an attribute by walking up the Parent chain."""
    if key in annot: return annot[key]
    curr = annot
    while '/Parent' in curr:
        curr = curr['/Parent']
        if key in curr: return curr[key]
    return default

def build_calculate_js(calc: Dict[str, Any], field_name: str = None) -> str:
    """Build JavaScript for field calculation (/AA/C action).

    calc = {
        "type": "SUM"|"SUBTRACT"|"MULTIPLY"|"DIVIDE"|"AVERAGE"|"MIN"|"MAX"|"PRODUCT"|"CUSTOM"|"CONSTANT_MULTIPLY",
        "sources": ["field1", "field2", ...],
        "formula": "A * B / 1000",  # Only for CUSTOM type
        "constant": 35,             # Only for CONSTANT_MULTIPLY type
        "decimals": 2,              # Optional rounding
    }
    Returns a JavaScript string for event.value assignment.
    """
    calc_type = calc.get("type", "").upper()
    sources = calc.get("sources", [])
    decimals = calc.get("decimals")

    js = ""

    # Standard Acrobat functions (SUM, PRODUCT, AVERAGE, MIN, MAX)
    af_map = {"SUM": "SUM", "PRODUCT": "PRD", "AVERAGE": "AVG", "MIN": "MIN", "MAX": "MAX"}

    if calc_type in af_map and sources:
        field_list = ", ".join(f'"{s}"' for s in sources)
        js = f'AFSimple_Calculate("{af_map[calc_type]}", new Array({field_list}));'

    elif calc_type == "SUBTRACT" and len(sources) >= 2:
        # First field minus all others
        parts = []
        for i, s in enumerate(sources):
            parts.append(f'var v{i} = Number(this.getField("{s}").value) || 0;')
        expr = "v0" + "".join(f" - v{i}" for i in range(1, len(sources)))
        js = "\n".join(parts) + f"\nevent.value = {expr};"

    elif calc_type == "MULTIPLY" and sources:
        parts = []
        for i, s in enumerate(sources):
            parts.append(f'var v{i} = Number(this.getField("{s}").value) || 0;')
        expr = " * ".join(f"v{i}" for i in range(len(sources)))
        js = "\n".join(parts) + f"\nevent.value = {expr};"

    elif calc_type == "CONSTANT_MULTIPLY" and sources:
        constant = calc.get("constant", 1)
        try:
            constant = float(constant)
        except (ValueError, TypeError):
            constant = 1
        parts = []
        for i, s in enumerate(sources):
            parts.append(f'var v{i} = Number(this.getField("{s}").value) || 0;')
        expr = " * ".join(f"v{i}" for i in range(len(sources)))
        js = "\n".join(parts) + f"\nevent.value = {constant} * ({expr});"

    elif calc_type == "DIVIDE" and len(sources) >= 2:
        parts = []
        for i, s in enumerate(sources):
            parts.append(f'var v{i} = Number(this.getField("{s}").value) || 0;')
        # Zero-division protection
        divisor = " * ".join(f"v{i}" for i in range(1, len(sources)))
        js = "\n".join(parts) + f"\nvar divisor = {divisor};\nevent.value = divisor !== 0 ? v0 / divisor : 0;"

    elif calc_type == "CUSTOM" and sources:
        formula = calc.get("formula", "")
        if formula:
            # Simplify raw JS formulas (BVCALC markers, AFMakeNumber wrappers, etc.)
            # to clean letter-reference notation before generating output
            import re as _re

            # Strip BVCALC marker: /** BVCALC simplified_formula EVCALC **/...
            bv_match = _re.search(r'/\*\*\s*BVCALC\s+([\s\S]*?)\s+EVCALC\s*\*\*/', formula)
            if bv_match:
                formula = bv_match.group(1).strip()
                # BVCALC text uses field names — convert to letter references
                for i, s in sorted(enumerate(sources), key=lambda x: len(x[1]), reverse=True):
                    letter = chr(65 + i)
                    formula = _re.sub(r'\b' + _re.escape(s) + r'\b', letter, formula)
            elif 'event.value' in formula or 'AFMakeNumber' in formula or 'getField' in formula:
                # Raw JS — extract expression after event.value=
                ev_match = _re.search(r'event\.value\s*=\s*([\s\S]+?)(?:;|\s*$)', formula)
                if ev_match:
                    formula = ev_match.group(1).strip()
                # Strip AFMakeNumber(getField("name").value) → field name
                formula = _re.sub(
                    r'AFMakeNumber\s*\(\s*(?:this\.)?getField\s*\(\s*"([^"]+)"\s*\)\s*\.value\s*\)',
                    r'\1', formula)
                # Strip Number(this.getField("name").value) → field name
                formula = _re.sub(
                    r'Number\s*\(\s*(?:this\.)?getField\s*\(\s*"([^"]+)"\s*\)\s*\.value\s*\)',
                    r'\1', formula)
                # Strip bare getField("name").value → field name
                formula = _re.sub(
                    r'(?:this\.)?getField\s*\(\s*"([^"]+)"\s*\)\s*\.value',
                    r'\1', formula)
                # Convert field names to letter references (sort by length desc)
                for i, s in sorted(enumerate(sources), key=lambda x: len(x[1]), reverse=True):
                    letter = chr(65 + i)
                    formula = _re.sub(r'\b' + _re.escape(s) + r'\b', letter, formula)

            # Auto-fix missing * operators (common in source PDFs):
            #   A (B  →  A * (B     and    ) (B  →  ) * (B
            #   )A    →  ) * A      and    2(A   →  2 * (A
            formula = _re.sub(r'([A-Z0-9)])\s*\(', r'\1 * (', formula)
            formula = _re.sub(r'\)\s*([A-Z0-9])', r') * \1', formula)

            # Auto-fix unbalanced parentheses
            _balanced = []
            _depth = 0
            for _ch in formula:
                if _ch == '(':
                    _depth += 1
                    _balanced.append(_ch)
                elif _ch == ')':
                    if _depth > 0:
                        _depth -= 1
                        _balanced.append(_ch)
                else:
                    _balanced.append(_ch)
            _balanced.extend(')' * _depth)
            formula = ''.join(_balanced)

            # Auto-strip self-references: a field referencing itself always
            # reads its own stale value, producing wrong results.
            if field_name and field_name in sources:
                self_idx = sources.index(field_name)
                self_letter = chr(65 + self_idx)
                n_original = len(sources)
                sources = [s for s in sources if s != field_name]
                # Replace self-ref letter with multiplicative identity (1)
                formula = _re.sub(
                    r'(?<![A-Za-z])' + self_letter + r'(?![A-Za-z])',
                    '1', formula)
                # Clean up: (1) → 1, 1 * X → X, X * 1 → X
                for _ in range(3):
                    formula = _re.sub(r'\(1\)', '1', formula)
                    formula = _re.sub(r'1\s*\*\s*', '', formula)
                    formula = _re.sub(r'\s*\*\s*1(?![0-9])', '', formula)
                formula = formula.strip()
                # Re-letter remaining sources (shift down by 1)
                for i in range(self_idx + 1, n_original):
                    old_l = chr(65 + i)
                    new_l = chr(65 + i - 1)
                    formula = _re.sub(
                        r'(?<![A-Za-z])' + old_l + r'(?![A-Za-z])',
                        new_l, formula)

            # Transpile letter references (A, B, C...) to this.getField() calls
            parts = []
            for i, s in enumerate(sources):
                letter = chr(65 + i)  # A, B, C, ...
                parts.append(f'var {letter} = Number(this.getField("{s}").value) || 0;')
            js = "\n".join(parts) + f"\nevent.value = {formula};"

    # Apply rounding if decimals specified
    if js and decimals is not None:
        try:
            d = int(decimals)
            if d >= 0:
                # Wrap the final event.value assignment with toFixed
                js += f"\nevent.value = Number(event.value).toFixed({d});"
        except (ValueError, TypeError):
            pass

    return js


def _topological_sort_calculations(calculations: Dict[str, Dict], all_field_names: set) -> list:
    """Topological sort of calculated fields to determine /CO order.

    Returns ordered list of field names, or raises ValueError on circular dependency.
    """
    # Build dependency graph
    graph = {}  # field -> set of fields it depends on
    for target, calc in calculations.items():
        sources = set(calc.get("sources", []))
        # Only include dependencies that are themselves calculated
        graph[target] = sources & set(calculations.keys())

    # Kahn's algorithm for topological sort
    # Build adjacency list (reverse: dep -> [nodes that depend on dep])
    adj = {node: [] for node in graph}
    in_deg = {node: 0 for node in graph}
    for node, deps in graph.items():
        for dep in deps:
            if dep in adj:
                adj[dep].append(node)
                in_deg[node] += 1

    # Start with nodes that have no calculated dependencies
    queue = [n for n in in_deg if in_deg[n] == 0]
    result = []

    while queue:
        node = queue.pop(0)
        result.append(node)
        for neighbor in adj.get(node, []):
            in_deg[neighbor] -= 1
            if in_deg[neighbor] == 0:
                queue.append(neighbor)

    if len(result) != len(graph):
        # Circular dependency detected
        remaining = set(graph.keys()) - set(result)
        raise ValueError(f"Circular dependency detected among fields: {', '.join(remaining)}")

    return result


def build_format_js(styles: Dict[str, Any], settings: Dict[str, Any] = {}) -> Dict[str, str]:
    """Build JavaScript actions for field formatting."""
    js = {}
    format_type = styles.get("formatType", "")
    
    # Handle shorthand format populations
    if format_type == "date" and "dateFormat" not in styles:
        styles["dateFormat"] = settings.get("date_format", "MM/DD/YYYY")
    elif format_type == "currency":
        if "currencySymbol" not in styles: styles["currencySymbol"] = settings.get("currency_symbol", "$")
        if "currencyDecimals" not in styles: styles["currencyDecimals"] = settings.get("currency_decimal_places", 2)
    elif format_type == "phone" and "phoneFormat" not in styles:
        styles["phoneFormat"] = settings.get("phone_format", "(XXX) XXX-XXXX")
    elif format_type == "zip" and "zipFormat" not in styles:
        styles["zipFormat"] = settings.get("zip_format", "XXXXX")
    elif format_type == "number":
        if styles.get("numberDecimals") is None: styles["numberDecimals"] = settings.get("number_decimal_places", 0)
        if "thousandSep" not in styles: styles["thousandSep"] = settings.get("number_thousands_separator", ",")

    # Date
    date_format = styles.get("dateFormat", "")
    if date_format:
        fmt_map = {"MM/DD/YYYY": "mm/dd/yyyy", "DD/MM/YYYY": "dd/mm/yyyy", "YYYY-MM-DD": "yyyy-mm-dd", "MM-DD-YYYY": "mm-dd-yyyy"}
        acro_fmt = fmt_map.get(date_format, "mm/dd/yyyy")
        js["format"] = f'AFDate_FormatEx("{acro_fmt}");'
        js["keystroke"] = f'AFDate_KeystrokeEx("{acro_fmt}");'
    
    # Currency — write formatting even when symbol is "" (None / no prefix)
    sym = styles.get("currencySymbol", "")
    dec = styles.get("currencyDecimals", "2")
    sym_loc = styles.get("symbolLocation", "before-space")
    if format_type == "currency" or sym:
        try: d = int(dec)
        except: d = 2
        
        # Build the symbol string with space and prepend flag from symbolLocation
        if sym_loc == "before-space":
            js_sym = sym + " " if sym else ""
            prepend = "true"
        elif sym_loc == "before-nospace":
            js_sym = sym
            prepend = "true"
        elif sym_loc == "after-space":
            js_sym = " " + sym if sym else ""
            prepend = "false"
        elif sym_loc == "after-nospace":
            js_sym = sym
            prepend = "false"
        else:
            js_sym = sym + " " if sym else ""
            prepend = "true"
        
        js["format"] = f'AFNumber_Format({d}, 0, 0, 0, "{js_sym}", {prepend});'
        js["keystroke"] = f'AFNumber_Keystroke({d}, 0, 0, 0, "{js_sym}", {prepend});'
    
    # Phone
    phone = styles.get("phoneFormat", "")
    if phone in ["(XXX) XXX-XXXX", "XXX-XXX-XXXX"]:
        js["format"] = 'AFSpecial_Format(2);'
        js["keystroke"] = 'AFSpecial_Keystroke(2);'
    
    # Zip
    zip_fmt = styles.get("zipFormat", "")
    if zip_fmt:
        if zip_fmt == "XXXXX":
            js["format"] = 'AFSpecial_Format(0);'
            js["keystroke"] = 'AFSpecial_Keystroke(0);'
        elif zip_fmt == "XXXXX-XXXX":
            js["format"] = 'AFSpecial_Format(1);'
            js["keystroke"] = 'AFSpecial_Keystroke(1);'
    
    # Number
    n_dec = styles.get("numberDecimals")
    t_sep = styles.get("thousandSep", ",")
    if n_dec is not None and str(n_dec) != "":
        try: d = int(n_dec)
        except: d = 0
        # sepStyle: 0=1,000.00, 1=1000.00, 2=1.000,00, 3=1000,00
        sep_style = 0 if t_sep == "," else 2 if t_sep == "." else 1
        js["format"] = f'AFNumber_Format({d}, {sep_style}, 0, 0, "", true);'
        js["keystroke"] = f'AFNumber_Keystroke({d}, {sep_style}, 0, 0, "", true);'
    
    return js

def _remove_from_list(lst, obj):
    """Utility to remove an object from a pikepdf list by identity/objgen."""
    if not lst: return False
    idx = 0
    found = False
    while idx < len(lst):
        item = lst[idx]
        is_match = (item == obj)
        if not is_match and hasattr(item, 'objgen') and hasattr(obj, 'objgen'):
            if item.objgen == obj.objgen: is_match = True
        
        if is_match:
            del lst[idx]
            found = True
        else:
            idx += 1
    return found

def _unlink_and_remove(pdf, annot, all_fields):
    """
    Surgically removes a widget/field from its current AcroForm hierarchy
    and ensures it's removed from the top-level Fields array if it was a root.
    Also cleans up empty parent fields recursively.
    """
    # 1. Primary removal: our own object from the global list
    _remove_from_list(all_fields, annot)
    
    # 2. Hierarchy removal
    while '/Parent' in annot:
        parent = annot['/Parent']
        if '/Kids' in parent:
            kids = parent['/Kids']
            _remove_from_list(kids, annot)
            
            # If parent still has other children, we STOP.
            # We don't want to delete the whole section and its contents!
            if len(kids) > 0:
                break
            else:
                # Parent is empty. It's now the target for removal.
                # Remove it from the top-level list if it was there.
                _remove_from_list(all_fields, parent)
                # Ensure it no longer has a name to avoid conflicts
                if '/T' in parent: del parent['/T']
                # Continue up to check parent's parent
                annot = parent
        else: break

def _create_vector_checkmark(width, height):
    scale = min(width, height) / 6.0
    x1, y1, x2, y2, x3, y3 = 0.9 * scale, 3.0 * scale, 2.1 * scale, 0.9 * scale, 5.1 * scale, 5.1 * scale
    return f"{1.5 * scale:.1f} w 0 0 0 RG {x1:.1f} {y1:.1f} m {x2:.1f} {y2:.1f} l {x3:.1f} {y3:.1f} l S"

def _create_vector_cross(width, height):
    cx, cy, r = width / 2.0, height / 2.0, min(width, height) * 0.35
    return f"1.6 w 0 0 0 RG {cx-r:.2f} {cy-r:.2f} m {cx+r:.2f} {cy+r:.2f} l {cx-r:.2f} {cy+r:.2f} m {cx+r:.2f} {cy-r:.2f} l S"

def _create_vector_circle(width, height):
    cx, cy, r = width / 2.0, height / 2.0, min(width, height) * 0.25
    k = 0.55228 * r
    return f"0 0 0 rg {cx+r:.2f} {cy:.2f} m {cx+r:.2f} {cy+k:.2f} {cx+k:.2f} {cy+r:.2f} {cx:.2f} {cy+r:.2f} c {cx-k:.2f} {cy+r:.2f} {cx-r:.2f} {cy+k:.2f} {cx-r:.2f} {cy:.2f} c {cx-r:.2f} {cy-k:.2f} {cx-k:.2f} {cy-r:.2f} {cx:.2f} {cy-r:.2f} c {cx+k:.2f} {cy-r:.2f} {cx+r:.2f} {cy-k:.2f} {cx+r:.2f} {cy:.2f} c f"

def _create_vector_square(width, height):
    cx, cy, r = width / 2.0, height / 2.0, min(width, height) * 0.25
    return f"0 0 0 rg {cx-r:.2f} {cy-r:.2f} {r*2:.2f} {r*2:.2f} re f"

def _create_vector_diamond(width, height):
    cx, cy, r = width / 2.0, height / 2.0, min(width, height) * 0.3
    return f"0 0 0 rg {cx:.2f} {cy+r:.2f} m {cx+r:.2f} {cy:.2f} l {cx:.2f} {cy-r:.2f} l {cx-r:.2f} {cy:.2f} l f"

def _strip_ap_fill(annot):
    """Remove background-fill commands from an existing /AP/N stream.

    Some PDFs bake the fill colour directly into the appearance stream
    (e.g. ``q 0.929 0.957 1.000 rg 0 0 52.42 11.38 re f Q``) instead of
    using ``/MK/BG``.  When the user sets fillColor=transparent we need
    to strip that fill so the field renders as clear.
    """
    import re as _re
    try:
        if '/AP' not in annot:
            return
        ap_n = annot['/AP'].get('/N')
        if ap_n is None or not hasattr(ap_n, 'read_bytes'):
            return
        raw = bytes(ap_n.read_bytes()).decode('latin-1', errors='replace')
        # Match the fill pattern: optional q, R G B rg, rect re f, optional Q
        # Also handles the case where the entire stream is just the fill
        new_raw = _re.sub(
            r'q\s*[\d.]+\s+[\d.]+\s+[\d.]+\s+rg\s+'
            r'[\d.]+\s+[\d.]+\s+[\d.]+\s+[\d.]+\s+re\s+f\s*Q',
            '', raw).strip()
        if new_raw != raw.strip():
            # Stream was modified; write it back
            if not new_raw:
                # Entire stream was just the fill — delete AP so viewer
                # regenerates from DA/V (or it simply appears empty/transparent)
                del annot['/AP']
            else:
                ap_n.write(new_raw.encode('latin-1'))
    except Exception:
        pass  # best-effort — don't break save on edge cases

def _create_vector_star(width, height):
    cx, cy, r_outer = width / 2.0, height / 2.0, min(width, height) * 0.35
    r_inner = r_outer * 0.4
    import math
    points = []
    for i in range(10):
        angle = math.pi/2 + i * math.pi/5
        r = r_outer if i % 2 == 0 else r_inner
        points.append(f"{cx + r * math.cos(angle):.2f} {cy + r * math.sin(angle):.2f} {'m' if i == 0 else 'l'}")
    return "0 0 0 rg " + " ".join(points) + " f"

def _create_radio_appearance_stream(pdf, rect, ev, bg_rgb=(1,1,1), style='check', border_rgb=None, border_w=0):
    """Create vector appearance stream with background fill and optional border."""
    width, height = float(rect[2]) - float(rect[0]), float(rect[3]) - float(rect[1])

    # Generic background drawing command
    # bg_rgb can be None or [] for transparent — skip background fill in that case
    if bg_rgb and len(bg_rgb) >= 3:
        bg_cmd = f"q {bg_rgb[0]:.2f} {bg_rgb[1]:.2f} {bg_rgb[2]:.2f} rg 0 0 {width:.2f} {height:.2f} re f Q"
    else:
        bg_cmd = ""  # transparent — no background fill
    
    # Border drawing command
    border_cmd = ""
    if border_rgb and border_w > 0:
        # Draw border slightly inset to prevent clipping
        inset = border_w / 2.0
        border_cmd = f"q {border_rgb[0]:.2f} {border_rgb[1]:.2f} {border_rgb[2]:.2f} RG {border_w:.1f} w {inset:.2f} {inset:.2f} {width-border_w:.2f} {height-border_w:.2f} re S Q"
    
    # Style mapping
    style_func = {
        'cross': _create_vector_cross, 'circle': _create_vector_circle,
        'square': _create_vector_square, 'diamond': _create_vector_diamond,
        'star': _create_vector_star
    }.get(style.lower(), _create_vector_checkmark)
    
    on_stream = pdf.make_indirect(pikepdf.Stream(pdf, f"{bg_cmd} {border_cmd} q {style_func(width, height)} Q".encode('latin-1'), 
        pikepdf.Dictionary({
            '/Type': pikepdf.Name('/XObject'), '/Subtype': pikepdf.Name('/Form'),
            '/BBox': pikepdf.Array([0, 0, width, height]), '/Resources': pikepdf.Dictionary()
        })
    ))
    
    off_stream = pdf.make_indirect(pikepdf.Stream(pdf, f"{bg_cmd} {border_cmd}".encode('latin-1'),
        pikepdf.Dictionary({
            '/Type': pikepdf.Name('/XObject'), '/Subtype': pikepdf.Name('/Form'),
            '/BBox': pikepdf.Array([0, 0, width, height]), '/Resources': pikepdf.Dictionary()
        })
    ))
    
    return {'on': on_stream, 'off': off_stream}

def _apply_radio_groups(pdf, groups: Dict[str, List[str]], annot_map: Dict, settings: Dict = {}) -> int:
    """Convert checkboxes into radio button groups."""
    count = 0
    if '/AcroForm' not in pdf.Root: return 0
    acroform = pdf.Root['/AcroForm']
    all_fields = acroform['/Fields']
    
    # Build a map of annot -> page for later use
    annot_to_page = {}
    for page in pdf.pages:
        if '/Annots' in page:
            for annot in page.Annots:
                if hasattr(annot, 'objgen'):
                    annot_to_page[annot.objgen] = page

    for group_name, checkbox_names in groups.items():
        checkbox_items = []
        seen_widget_ids = set() # Deduplicate physical widgets
        
        for name in checkbox_names:
            if name in annot_map:
                for page, annot in annot_map[name]:
                    obj_id = f"{annot.objgen[0]}_{annot.objgen[1]}" if hasattr(annot, 'objgen') else str(id(annot))
                    if obj_id not in seen_widget_ids:
                        checkbox_items.append((annot, name))
                        seen_widget_ids.add(obj_id)
        
        if len(checkbox_items) < 1: continue
        
        # Create parent radio group field
        parent = pdf.make_indirect(pikepdf.Dictionary({
            '/FT': pikepdf.Name('/Btn'), 
            '/Ff': 1 << 15 | 1 << 14, # Radio | NoToggleToOff
            '/T': pikepdf.String(group_name), 
            '/Kids': pikepdf.Array([]), 
            '/V': pikepdf.Name('/Off'),
            '/DA': pikepdf.String('0 g /ZaDb 0 Tf') # Default font for buttons
        }))
        
        used_evs = {'Off', '/Off'}
        for i, (annot, orig_name) in enumerate(checkbox_items):
            # 1. Surgical Hierarchy Removal
            _unlink_and_remove(pdf, annot, all_fields)
            
            # 2. Decouple and Configure as Radio
            if '/Parent' in annot: del annot['/Parent']
            annot['/FT'] = pikepdf.Name('/Btn')
            ff = int(annot.get('/Ff', 0))
            annot['/Ff'] = ff | 1 << 15 # Radio bit
            # Clear old value
            if '/V' in annot: del annot['/V']
            
            # 3. Assign unique export value
            ev = orig_name.strip().replace(' ', '_')
            if not ev or ev in used_evs:
                ev = f"Choice{i+1}"
            used_evs.add(ev)
            
            # 4. CREATE Appearance Streams (Critical for visibility!)
            # Preserve original checkbox background — NEVER apply a default fill
            # to existing checkboxes being converted to radios.
            # No /MK or no /BG = transparent; empty /BG array = transparent;
            # /BG with [r,g,b] = specific color to preserve.
            bg_rgb = None  # Default: transparent (no background fill)
            if '/MK' in annot and '/BG' in annot['/MK']:
                try:
                    bg_vals = [float(v) for v in annot['/MK']['/BG']]
                    if len(bg_vals) >= 3:
                        bg_rgb = bg_vals  # Has a specific color — preserve it
                except: pass
            
            border_color = settings.get('field_border_color', 'none')
            border_w = float(settings.get('field_border_thickness', 0))
            border_rgb = None
            if border_color and border_color.startswith('#') and len(border_color.lstrip('#')) == 6:
                border_rgb = [int(border_color.lstrip('#')[i:i+2], 16) / 255.0 for i in (0, 2, 4)]
            
            # Delete any existing AP dictionary - we'll create fresh ones
            if '/AP' in annot:
                del annot['/AP']
            
            # Create new appearance streams
            rect = annot.get('/Rect', [0, 0, 10, 10])
            style = settings.get('checkbox_style', settings.get('checkboxStyle', 'check'))
            appearances = _create_radio_appearance_stream(pdf, rect, ev, bg_rgb, style, border_rgb, border_w)
            
            # Set up the AP dictionary with ON and OFF states
            ap_n = pikepdf.Dictionary()
            ap_n['/Off'] = appearances['off']
            ap_n[pikepdf.Name('/' + ev)] = appearances['on']
            annot['/AP'] = pikepdf.Dictionary({'/N': ap_n})
            
            # 5. Visual Characteristics
            if '/MK' not in annot: annot['/MK'] = pikepdf.Dictionary()
            # Background color for native renderers
            if bg_rgb and len(bg_rgb) >= 3:
                annot['/MK']['/BG'] = pikepdf.Array(bg_rgb)
            else:
                # Transparent — remove /BG so viewer doesn't draw a background
                if '/BG' in annot['/MK']:
                    del annot['/MK']['/BG']
            
            # Border settings for native renderers
            if border_rgb and border_w > 0:
                annot['/MK']['/BC'] = pikepdf.Array(border_rgb)
                if '/BS' not in annot: annot['/BS'] = pikepdf.Dictionary()
                annot['/BS']['/W'] = border_w
            else:
                if '/BC' in annot['/MK']: del annot['/MK']['/BC']
            
            # Clean Hierarchy: Child widgets in a Radio Group MUST NOT have their own /T
            if '/T' in annot: del annot['/T']

            # Ensure DA uses ZapfDingbats (legacy backup)
            annot['/DA'] = pikepdf.String('0 g /ZaDb 0 Tf')
            
            # Ensure page reference is set
            if hasattr(annot, 'objgen') and annot.objgen in annot_to_page:
                annot['/P'] = annot_to_page[annot.objgen].obj
            
            annot['/AS'] = pikepdf.Name('/Off')
            annot['/Parent'] = parent
            parent['/Kids'].append(annot)
        
        all_fields.append(parent)
        count += 1
    return count

def _apply_ungroup_radios(pdf, group_names: List[str]) -> int:
    """Convert radio groups back to individual checkboxes."""
    count = 0
    if '/AcroForm' not in pdf.Root or '/Fields' not in pdf.Root['/AcroForm']: return 0
    fields = pdf.Root['/AcroForm']['/Fields']
    
    # Identify parents to remove
    to_remove = []
    for i, field in enumerate(fields):
        name = str(field.get('/T', ''))
        if name in group_names and '/Kids' in field:
            to_remove.append((i, field))
    
    for idx, parent in sorted(to_remove, reverse=True, key=lambda x: x[0]):
        group_name = str(parent.get('/T', ''))
        kids = parent.get('/Kids', [])
        for i, kid in enumerate(kids):
            kid['/T'] = pikepdf.String(f"{group_name}_{i+1}")
            kid['/FT'] = pikepdf.Name('/Btn')
            kid['/Ff'] = 0 # Checkbox
            if '/Parent' in kid: del kid['/Parent']
            fields.append(kid)
        del fields[idx]
        count += 1
    return count

def apply_field_changes(pdf_path: str, output_path: str, changes: Dict[str, Any], settings: Dict[str, Any] = {}) -> dict:
    result = {"success": False, "deleted": 0, "renamed": 0, "moved": 0, "resized": 0, "styled": 0, "created": 0, "grouped": 0, "ungrouped": 0, "calculated": 0}

    # Diagnostic logger — writes to a temp file so we can inspect later
    import os as _diag_os
    _diag_log_path = _diag_os.path.join(tempfile.gettempdir(), 'DEBUG_modify_fields_trace.log')
    _diag_lines = []
    def _diag(msg):
        _diag_lines.append(msg)
        sys.stderr.write(f"[DIAG] {msg}\n")
    def _diag_snapshot(label, pdf_obj):
        """Count page annotations and /Fields entries."""
        counts = []
        total = 0
        for i, p in enumerate(pdf_obj.pages):
            annots = p.get('/Annots', [])
            c = len(list(annots)) if annots else 0
            if c > 0:
                counts.append(f"p{i+1}={c}")
            total += c
        fields_count = len(pdf_obj.Root.get('/AcroForm', {}).get('/Fields', []))
        _diag(f"  [{label}] total_annots={total} ({', '.join(counts)}), /Fields={fields_count}")
    def _diag_page_fields(label, pdf_obj, page_idx):
        """List all widget names on a specific page."""
        if page_idx >= len(pdf_obj.pages):
            return
        p = pdf_obj.pages[page_idx]
        annots = p.get('/Annots', [])
        names = []
        for a in annots:
            try:
                sub = a.get('/Subtype')
                if sub == pikepdf.Name('/Widget') or str(sub) == '/Widget':
                    t = str(a.get('/T', ''))
                    ft = str(a.get('/FT', ''))
                    og = f"{a.objgen[0]},{a.objgen[1]}" if a.is_indirect else "?"
                    names.append(f"{t}({ft},og={og})")
            except: pass
        _diag(f"  [{label}] page {page_idx+1} widgets ({len(names)}): {names}")

    def _diag_all_rects(label, pdf_obj):
        """Log every widget's Rect on every page for comprehensive before/after comparison."""
        for pi, page in enumerate(pdf_obj.pages):
            if '/Annots' not in page: continue
            for annot in page.Annots:
                try:
                    if annot.get('/Subtype') != pikepdf.Name('/Widget'): continue
                    name = str(annot.get('/T', ''))
                    if not name: continue
                    rect = [round(float(x), 2) for x in annot['/Rect']] if '/Rect' in annot else []
                    og = f"{annot.objgen[0]},{annot.objgen[1]}" if annot.is_indirect else "?"
                    _diag(f"  [{label}] p{pi+1} og={og} name={name} rect={rect}")
                except: pass

    try:
        pdf = pikepdf.Pdf.open(pdf_path, allow_overwriting_input=True)
        _diag(f"Opened: {pdf_path}")
        _diag_snapshot("INITIAL", pdf)
        _diag_all_rects("INITIAL", pdf)
        _ensure_font_resources(pdf)
        if '/AcroForm' not in pdf.Root:
            pdf.Root['/AcroForm'] = pikepdf.Dictionary({'/Fields': pikepdf.Array([])})
        acroform = pdf.Root['/AcroForm']
        all_fields = acroform['/Fields']

        # Stable widget snapshot for index-based targeting (legacy fallback)
        original_widgets = []
        for p in pdf.pages:
            if '/Annots' in p:
                for a in p.Annots:
                    if a.get('/Subtype') == '/Widget': original_widgets.append(a)

        # objgen map: PDF object numbers are absolutely stable across saves
        # (unlike sequential indices which shift when other fields are deleted)
        def build_objgen_map():
            m = {}
            for page in pdf.pages:
                if '/Annots' not in page: continue
                for annot in page.Annots:
                    if annot.get('/Subtype') != pikepdf.Name('/Widget'): continue
                    if annot.is_indirect:
                        key = f"{annot.objgen[0]},{annot.objgen[1]}"
                        m[key] = (page, annot)
            return m
        objgen_map = build_objgen_map()

        def build_annot_map():
            m = {}
            for page in pdf.pages:
                if '/Annots' not in page: continue
                for annot in page.Annots:
                    if annot.get('/Subtype') != pikepdf.Name('/Widget'): continue
                    # Use resolve_attribute to handle inherited names
                    name = str(_resolve_attribute(annot, '/T', ''))
                    if name:
                        if name not in m: m[name] = []
                        m[name].append((page, annot))
            return m

        annot_map = build_annot_map()

        # 1. Ungroup Radios
        ungroup = changes.get("ungroup_radios", [])
        if ungroup:
            # Filter out any names that are also involved in new_radio_groups
            # This prevents accidental renaming (XXX_1, XXX_2) when regrouping
            new_groups = changes.get("new_radio_groups", {})
            involved_names = set(new_groups.keys())
            for ns in new_groups.values():
                involved_names.update(ns)
            
            filtered_ungroup = [name for name in ungroup if name not in involved_names]
            if filtered_ungroup:
                result["ungrouped"] = _apply_ungroup_radios(pdf, filtered_ungroup)
                annot_map = build_annot_map()
                _diag_snapshot("AFTER-UNGROUP", pdf)
                _diag_page_fields("AFTER-UNGROUP", pdf, 2)

        # 2. Delete
        involved_in_groups = set()
        new_groups = changes.get("new_radio_groups", {})
        for gs in new_groups.values(): involved_in_groups.update(gs)
        
        # Filter out anything being grouped!
        delete_names = [n for n in changes.get("deleted", []) if n not in involved_in_groups]
        
        for name in delete_names:
            if name in annot_map:
                for page, annot in annot_map[name]:
                    # Remove from page
                    if '/Annots' in page:
                        _remove_from_list(page.Annots, annot)
                    # Surgical removal from hierarchy
                    _unlink_and_remove(pdf, annot, all_fields)
                    result["deleted"] += 1
                del annot_map[name]
        _diag(f"Deleted {result['deleted']} fields: {delete_names}")
        _diag_snapshot("AFTER-DELETE", pdf)
        _diag_page_fields("AFTER-DELETE", pdf, 2)

        # 3. Create
        # Use CropBox top for Y-flip (matches extract_fields.py coordinate system)
        # Annotations are in MediaBox coords; the editor uses CropBox-relative top-down coords
        def _get_crop_y_top(page):
            mb = [float(x) for x in page.get('/MediaBox', [0, 0, 612, 792])]
            cb_raw = page.get('/CropBox', None)
            if cb_raw is not None:
                return float(cb_raw[3])
            return mb[3]
        def _get_crop_x_offset(page):
            mb = [float(x) for x in page.get('/MediaBox', [0, 0, 612, 792])]
            cb_raw = page.get('/CropBox', None)
            if cb_raw is not None:
                return float(cb_raw[0]) - mb[0]
            return 0.0
        p_heights = [_get_crop_y_top(p) for p in pdf.pages]
        p_x_offsets = [_get_crop_x_offset(p) for p in pdf.pages]
        for fd in changes.get("created", []):
            try:
                p_num = fd.get("page", 0)
                if p_num >= len(pdf.pages): continue
                page = pdf.pages[p_num]
                v_rect = fd.get("rect", [0, 0, 100, 20])
                x_off = p_x_offsets[p_num] if p_num < len(p_x_offsets) else 0
                rect = [v_rect[0] + x_off, p_heights[p_num] - v_rect[3], v_rect[2] + x_off, p_heights[p_num] - v_rect[1]]
                f_name = fd.get("name", f"Field_{result['created'] + 1}")
                f_type = fd.get("type", "text")
                
                widget = pikepdf.Dictionary({
                    '/Type': pikepdf.Name('/Annot'), '/Subtype': pikepdf.Name('/Widget'),
                    '/T': pikepdf.String(f_name), '/Rect': pikepdf.Array([float(c) for c in rect]),
                    '/F': 4,
                })
                if f_type == "checkbox": 
                    widget['/FT'] = pikepdf.Name('/Btn')
                    # Style defaults from settings
                    bg_color = settings.get('field_background_color', '#EDF4FF')
                    bg_rgb = [int(bg_color.lstrip('#')[i:i+2], 16) / 255.0 for i in (0, 2, 4)] if (bg_color and len(bg_color.lstrip('#')) == 6) else [0.929, 0.957, 1.0]
                    
                    border_color = settings.get('field_border_color', 'none')
                    border_w = float(settings.get('field_border_thickness', 0))
                    border_rgb = None
                    if border_color and border_color.startswith('#') and len(border_color.lstrip('#')) == 6:
                        border_rgb = [int(border_color.lstrip('#')[i:i+2], 16) / 255.0 for i in (0, 2, 4)]
                    
                    style = settings.get('checkbox_style', settings.get('checkboxStyle', 'check'))
                    
                    appearances = _create_radio_appearance_stream(pdf, rect, '/Yes', bg_rgb, style, border_rgb, border_w)
                    ap_n = pikepdf.Dictionary()
                    ap_n['/Off'] = appearances['off']
                    ap_n['/Yes'] = appearances['on']
                    widget['/AP'] = pikepdf.Dictionary({'/N': ap_n})
                    widget['/AS'] = pikepdf.Name('/Off')
                    
                    # MK for native renderers
                    if '/MK' not in widget: widget['/MK'] = pikepdf.Dictionary()
                    widget['/MK']['/BG'] = pikepdf.Array(bg_rgb)
                    
                    if border_rgb and border_w > 0:
                        widget['/MK']['/BC'] = pikepdf.Array(border_rgb)
                        if '/BS' not in widget: widget['/BS'] = pikepdf.Dictionary()
                        widget['/BS']['/W'] = border_w
                    else:
                        if '/BC' in widget['/MK']: del widget['/MK']['/BC']
                    
                    # Use ZapfDingbats for DA (legacy)
                    widget['/DA'] = pikepdf.String('0 g /ZaDb 0 Tf')
                    
                elif f_type == "radio": 
                    widget['/FT'], widget['/Ff'] = pikepdf.Name('/Btn'), 0x8000
                elif f_type == "combobox" or f_type == "listbox":
                    # Choice field type for dropdowns
                    widget['/FT'] = pikepdf.Name('/Ch')
                    # Set combobox flag (bit 18 = 0x20000)
                    ff = 0x20000 if f_type == "combobox" else 0
                    widget['/Ff'] = ff
                    
                    # Add options if provided
                    opts = fd.get("options", [])
                    if opts:
                        pike_opts = []
                        for o in opts:
                            if isinstance(o, str):
                                pike_opts.append(pikepdf.String(o))
                            elif isinstance(o, dict):
                                label = str(o.get("label", ""))
                                value = str(o.get("value", label))
                                if label == value:
                                    pike_opts.append(pikepdf.String(label))
                                else:
                                    pike_opts.append(pikepdf.Array([pikepdf.String(value), pikepdf.String(label)]))
                        if pike_opts:
                            widget['/Opt'] = pikepdf.Array(pike_opts)
                    
                    # Style defaults from settings
                    f_size = float(settings.get('field_font_size', 9))
                    f_color = settings.get('field_font_color', '#000000').lstrip('#')
                    rgb = [int(f_color[i:i+2], 16)/255.0 for i in (0, 2, 4)] if len(f_color) == 6 else [0,0,0]
                    f_tag = {'Helvetica': 'Helv', 'Courier': 'Cour', 'Times-Roman': 'TiRo', 'Times New Roman': 'TiRo'}.get(settings.get('field_font_family', 'Helvetica'), 'Helv')
                    widget['/DA'] = pikepdf.String(f"/{f_tag} {f_size:.1f} Tf {rgb[0]:.3f} {rgb[1]:.3f} {rgb[2]:.3f} rg")

                    # Background color
                    bg_color = settings.get('field_background_color', '#EDF4FF')
                    if bg_color and bg_color.lower() not in ['none', 'transparent', 'null']:
                        bg_rgb = [int(bg_color.lstrip('#')[i:i+2], 16) / 255.0 for i in (0, 2, 4)] if len(bg_color.lstrip('#')) == 6 else [1,1,1]
                        widget['/MK'] = pikepdf.Dictionary({'/BG': pikepdf.Array(bg_rgb)})
                elif f_type == "signature" or fd.get("is_signature"):
                    widget['/FT'] = pikepdf.Name('/Sig')
                    # Create empty appearance stream for signature widget
                    fw = abs(rect[2] - rect[0])
                    fh = abs(rect[3] - rect[1])
                    ap_stream = pikepdf.Stream(pdf, b'q Q')
                    ap_stream['/BBox'] = pikepdf.Array([0, 0, fw, fh])
                    ap_stream['/Subtype'] = pikepdf.Name('/Form')
                    ap_stream['/Type'] = pikepdf.Name('/XObject')
                    widget['/AP'] = pikepdf.Dictionary({'/N': ap_stream})
                    # Background color
                    bg_color = settings.get('field_background_color', '#EDF4FF')
                    if bg_color and bg_color.lower() not in ['none', 'transparent', 'null']:
                        bg_rgb = [int(bg_color.lstrip('#')[i:i+2], 16) / 255.0 for i in (0, 2, 4)] if len(bg_color.lstrip('#')) == 6 else [1,1,1]
                        widget['/MK'] = pikepdf.Dictionary({'/BG': pikepdf.Array(bg_rgb)})
                else:
                    widget['/FT'] = pikepdf.Name('/Tx')

                    # Style defaults from settings
                    f_size = float(settings.get('field_font_size', 9))
                    f_color = settings.get('field_font_color', '#000000').lstrip('#')
                    rgb = [int(f_color[i:i+2], 16)/255.0 for i in (0, 2, 4)] if len(f_color) == 6 else [0,0,0]
                    f_tag = {'Helvetica': 'Helv', 'Courier': 'Cour', 'Times-Roman': 'TiRo', 'Times New Roman': 'TiRo'}.get(settings.get('field_font_family', 'Helvetica'), 'Helv')

                    widget['/DA'] = pikepdf.String(f"/{f_tag} {f_size:.1f} Tf {rgb[0]:.3f} {rgb[1]:.3f} {rgb[2]:.3f} rg")

                    # Background color from settings
                    bg_color = settings.get('field_background_color', '#EDF4FF')
                    if bg_color and bg_color.lower() not in ['none', 'transparent', 'null']:
                        bg_rgb = [int(bg_color.lstrip('#')[i:i+2], 16) / 255.0 for i in (0, 2, 4)] if len(bg_color.lstrip('#')) == 6 else [1,1,1]
                        widget.MK = pikepdf.Dictionary({'/BG': pikepdf.Array(bg_rgb)})

                if '/Annots' not in page: page['/Annots'] = pikepdf.Array([])
                
                # CRITICAL: Create ONE indirect object and add it to BOTH lists.
                # If they are different (one direct, one indirect), identity checks and 
                # grouping logic will fail, causing "disappearing" fields.
                indirect_widget = pdf.make_indirect(widget)
                page.Annots.append(indirect_widget)
                all_fields.append(indirect_widget)
                result["created"] += 1
            except: pass

        # Update map after creation
        annot_map = build_annot_map()

        # 4. Rename (Moved BEFORE Grouping so groups can find new names)
        name_updates = {}
        for old, new in changes.get("renamed", {}).items():
            if old in annot_map:
                for _, a in annot_map[old]: a['/T'] = pikepdf.String(new)
                result["renamed"] += 1
                annot_map[new] = annot_map.pop(old)
                name_updates[old] = new


        # 6. Move / Resize
        def _move_annot(annot, v_rect, page):
            """Apply a visual rect to an annotation, regenerating AP if needed."""
            p_idx = pdf.pages.index(page)
            x_off = p_x_offsets[p_idx] if p_idx < len(p_x_offsets) else 0
            new_rect = [v_rect[0] + x_off, p_heights[p_idx] - v_rect[3], v_rect[2] + x_off, p_heights[p_idx] - v_rect[1]]
            annot['/Rect'] = pikepdf.Array([float(c) for c in new_rect])

            # REGENERATE Appearance Stream after move
            # Check if it is a button (Checkbox/Radio)
            ft = str(_resolve_attribute(annot, '/FT', ''))
            if '/Btn' in ft:
                # Preserve original background color from the widget.
                # IMPORTANT: An empty /MK/BG array means transparent — preserve that!
                existing_bg = None  # None = not present, [] = transparent, [r,g,b] = color
                if '/MK' in annot and '/BG' in annot['/MK']:
                    existing_bg = [float(v) for v in annot['/MK']['/BG']]
                if existing_bg is not None:
                    bg_rgb = existing_bg if len(existing_bg) >= 3 else None
                else:
                    bg_color = settings.get('field_background_color', '#EDF4FF')
                    bg_rgb = [int(bg_color.lstrip('#')[i:i+2], 16) / 255.0 for i in (0, 2, 4)] if (bg_color and len(bg_color.lstrip('#')) == 6) else [0.929, 0.957, 1.0]
                style = settings.get('checkbox_style', settings.get('checkboxStyle', 'check'))

                # Determine export value (ev) for radio/checkbox
                ev = 'Yes'
                if '/Parent' in annot:
                    if '/AP' in annot and '/N' in annot['/AP']:
                        for k in annot['/AP']['/N'].keys():
                            if str(k) not in ['/Off', 'Off']:
                                ev = str(k).lstrip('/')
                                break

                appearances = _create_radio_appearance_stream(pdf, new_rect, ev, bg_rgb, style)
                ap_n = pikepdf.Dictionary()
                ap_n['/Off'] = appearances['off']
                ap_n[pikepdf.Name('/' + ev)] = appearances['on']
                annot['/AP'] = pikepdf.Dictionary({'/N': ap_n})
            else:
                acroform['/NeedAppearances'] = True

        # 6a. objgen-based moves/resizes (stable across saves — handles radio children)
        # After pikepdf saves, object numbers get renumbered. The editor's cached
        # objgens may now point to DIFFERENT fields — even on the SAME page.
        # The definitive check: the editor now sends the intended field name with
        # each objgen entry. If the actual widget name doesn't match, skip it.
        moved_by_og = changes.get("moved_by_objgen", {})
        resized_by_og = changes.get("resized_by_objgen", {})
        combined_og = {**moved_by_og, **resized_by_og}
        delete_set = set(changes.get("deleted", []))
        og_handled = set()  # Track which name-based entries were handled by objgen
        for og_key, data in combined_og.items():
            try:
                if og_key in objgen_map:
                    v_rect = data.get("rect")
                    if v_rect:
                        widget_page, annot = objgen_map[og_key]
                        wname = str(_resolve_attribute(annot, '/T', ''))
                        # Primary check: editor's intended name must match actual widget name
                        editor_intended_name = data.get("name")
                        if editor_intended_name and wname != editor_intended_name:
                            sys.stderr.write(f"Warning: objgen {og_key} -> '{wname}' but editor intended '{editor_intended_name}' — skipping stale objgen\n")
                            continue
                        # Fallback check for older editor sessions without name field:
                        # widget name must appear in name-based changes
                        if not editor_intended_name:
                            name_based_spatial = set(changes.get("moved", {}).keys()) | set(changes.get("resized", {}).keys())
                            if wname and wname not in name_based_spatial:
                                sys.stderr.write(f"Warning: objgen {og_key} -> '{wname}' not in name-based changes — skipping stale objgen\n")
                                continue
                        # Deleted check: widget must not have been deleted
                        if wname and wname in delete_set:
                            sys.stderr.write(f"Warning: objgen {og_key} -> '{wname}' was deleted — skipping\n")
                            continue
                        # Page check: widget's PDF page must match editor's expected page
                        editor_page = data.get("page")
                        if editor_page is not None:
                            actual_page_idx = list(pdf.pages).index(widget_page)
                            if actual_page_idx != editor_page:
                                sys.stderr.write(f"Warning: objgen {og_key} -> '{wname}' on PDF page {actual_page_idx} but editor expected page {editor_page} — skipping stale objgen\n")
                                continue
                        _move_annot(annot, v_rect, widget_page)
                        if wname:
                            og_handled.add(wname)
                        if og_key in moved_by_og: result["moved"] += 1
                        else: result["resized"] += 1
                else:
                    sys.stderr.write(f"Warning: objgen {og_key} not found in PDF\n")
            except Exception as e:
                sys.stderr.write(f"Warning: objgen-based move/resize failed for {og_key}: {e}\n")

        # 6a-legacy. Index-based moves/resizes (fallback for older editor sessions)
        moved_by_idx = changes.get("moved_by_index", {})
        resized_by_idx = changes.get("resized_by_index", {})
        combined_idx = {**moved_by_idx, **resized_by_idx}
        for idx_str, data in combined_idx.items():
            try:
                idx = int(idx_str)
                if idx < len(original_widgets):
                    v_rect = data.get("rect")
                    if v_rect:
                        annot = original_widgets[idx]
                        widget_page = None
                        for p in pdf.pages:
                            if '/Annots' in p:
                                for a in p.Annots:
                                    try:
                                        if a.is_indirect and annot.is_indirect and a.objgen == annot.objgen:
                                            widget_page = p
                                            break
                                    except Exception:
                                        continue
                            if widget_page:
                                break
                        if widget_page:
                            _move_annot(annot, v_rect, widget_page)
                            wname = str(_resolve_attribute(annot, '/T', ''))
                            if wname:
                                og_handled.add(wname)
                            if idx_str in moved_by_idx: result["moved"] += 1
                            else: result["resized"] += 1
            except Exception as e:
                sys.stderr.write(f"Warning: Index-based move/resize failed for idx {idx_str}: {e}\n")

        # 6b. Name-based moves/resizes (skip names handled by objgen or index)
        # Also cross-validate page if the editor sent page info
        combined_spatial = {**changes.get("moved", {}), **changes.get("resized", {})}
        for name, data in combined_spatial.items():
            if name in og_handled:
                continue  # Already handled by objgen/index-based processing
            t_name = name_updates.get(name, name)
            if t_name in annot_map:
                v_rect = data.get("rect")
                if v_rect:
                    editor_page = data.get("page")
                    for page, annot in annot_map[t_name]:
                        # Cross-validate page: if editor sent page info, verify it matches
                        if editor_page is not None:
                            actual_page_idx = list(pdf.pages).index(page)
                            if actual_page_idx != editor_page:
                                sys.stderr.write(f"Warning: name '{t_name}' on PDF page {actual_page_idx} but editor expected page {editor_page} — skipping wrong-page move\n")
                                continue
                        _move_annot(annot, v_rect, page)
                        if name in changes.get("moved", {}): result["moved"] += 1
                        else: result["resized"] += 1
        _diag(f"Moves: {result['moved']}, Resizes: {result['resized']}")
        _diag_snapshot("AFTER-MOVE/RESIZE", pdf)
        _diag_page_fields("AFTER-MOVE/RESIZE", pdf, 2)

        # 7. Styling & JS
        def apply_styles(annot, s):
            changed = False
            # Alignment
            if "textAlign" in s or "text_align" in s:
                val = s.get("textAlign", s.get("text_align"))
                annot['/Q'] = {"left": 0, "center": 1, "right": 2}.get(val, 0)
                changed = True
            
            # Colors (MK)
            if "fillColor" in s or "fill_color" in s:
                bg = s.get("fillColor", s.get("fill_color"))
                if bg == "transparent":
                    if '/MK' in annot and '/BG' in annot['/MK']: del annot['/MK']['/BG']
                    # Also strip background fill from existing AP/N stream.
                    # Some PDFs bake the fill into the appearance stream
                    # (e.g. «R G B rg 0 0 W H re f») instead of /MK/BG.
                    _strip_ap_fill(annot)
                elif bg.startswith('#'):
                    if '/MK' not in annot: annot['/MK'] = pikepdf.Dictionary()
                    rgb = [int(bg[i:i+2], 16)/255.0 for i in (1, 3, 5)]
                    annot['/MK']['/BG'] = pikepdf.Array(rgb)
                changed = True
            
            if "borderColor" in s:
                bc = s["borderColor"]
                if bc == "transparent":
                    if '/MK' in annot and '/BC' in annot['/MK']: del annot['/MK']['/BC']
                elif bc.startswith('#'):
                    if '/MK' not in annot: annot['/MK'] = pikepdf.Dictionary()
                    rgb = [int(bc[i:i+2], 16)/255.0 for i in (1, 3, 5)]
                    annot['/MK']['/BC'] = pikepdf.Array(rgb)
                changed = True

            # Checkbox Style (ZapfDingbats character in /MK/CA)
            # Only apply to button fields — /MK/CA is meaningless for text fields
            # and creating /MK without /BG on text fields breaks viewer rendering.
            if "checkboxStyle" in s or "checkbox_style" in s:
                ft_for_cb = str(_resolve_attribute(annot, '/FT', ''))
                if '/Btn' in ft_for_cb:
                    style = s.get("checkboxStyle", s.get("checkbox_style"))
                    char_map = {
                        "check": "4",
                        "cross": "8",
                        "circle": "l",
                        "diamond": "u",
                        "square": "n",
                        "star": "H"
                    }
                    char = char_map.get(style, "4")
                    if '/MK' not in annot: annot['/MK'] = pikepdf.Dictionary()
                    annot['/MK']['/CA'] = pikepdf.String(char)
                    annot['/DA'] = pikepdf.String('0 g /ZaDb 0 Tf')
                    changed = True

            # Border Style (BS)
            if "lineThickness" in s or "line_thickness" in s:
                if '/BS' not in annot: annot['/BS'] = pikepdf.Dictionary()
                val = s.get("lineThickness", s.get("line_thickness"))
                annot['/BS']['/W'] = float(val)
                changed = True
            
            if "lineStyle" in s or "line_style" in s:
                if '/BS' not in annot: annot['/BS'] = pikepdf.Dictionary()
                val = s.get("lineStyle", s.get("line_style"))
                pike_style = {"solid": "/S", "dashed": "/D", "underline": "/U"}.get(val, "/S")
                annot['/BS']['/S'] = pikepdf.Name(pike_style)
                changed = True
            
            # Font (DA)
            f_size = s.get("fontSize", s.get("font_size"))
            f_color = s.get("fontColor", s.get("text_color"))
            f_family = s.get("fontFamily", s.get("font_family"))
            if f_size is not None or f_color or f_family:
                # Use global settings as absolute fallback for extraction
                fallback_size = float(settings.get('field_font_size', 9))
                fallback_color = settings.get('field_font_color', '#000000').lstrip('#')
                fallback_rgb = [int(fallback_color[i:i+2], 16)/255.0 for i in (0, 2, 4)] if len(fallback_color) == 6 else [0,0,0]
                fallback_tag = {'Helvetica': 'Helv', 'Courier': 'Cour', 'Times-Roman': 'TiRo'}.get(settings.get('field_font_family', 'Helvetica'), 'Helv')
                
                current_da = str(annot.get('/DA', ''))
                if not current_da:
                    current_da = f"/{fallback_tag} {fallback_size:.1f} Tf {fallback_rgb[0]:.3f} {fallback_rgb[1]:.3f} {fallback_rgb[2]:.3f} rg"

                # Extract currentColor/Size from existing DA if possible
                match_color = re.search(r'([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+rg', current_da)
                match_font = re.search(r'/(\w+)\s+([\d.]+)\s+Tf', current_da)
                
                r, g, b = fallback_rgb
                if match_color: r, g, b = map(float, match_color.groups())
                elif ' g' in current_da: 
                    g_match = re.search(r'([\d.]+)\s+g', current_da)
                    if g_match: r = g = b = float(g_match.group(1))

                font_tag = fallback_tag
                size = fallback_size
                if match_font: font_tag, size = match_font.group(1), float(match_font.group(2))
                
                if f_size:
                    try:
                        sz = float(f_size)
                        if math.isfinite(sz): size = sz
                    except: pass
                if f_family: font_tag = {
                    'Helvetica': 'Helv', 
                    'Arial': 'Helv', 
                    'Courier': 'Cour', 
                    'Times New Roman': 'TiRo',
                    'Times-Roman': 'TiRo',
                    'Verdana': 'Verdana',
                    'Georgia': 'Georgia',
                    'Calibri': 'Calibri',
                    'Cambria': 'Cambria',
                    'Trebuchet MS': 'TrebuchetMS',
                    'Tahoma': 'Tahoma',
                }.get(f_family, f_family if f_family else 'Helv')
                if f_color and f_color.startswith('#'):
                    r, g, b = [int(f_color[i:i+2], 16)/255.0 for i in (1, 3, 5)]
                
                # Font first, then color — matches v23 DA order that viewers
                # expect when regenerating text field appearances.
                annot['/DA'] = pikepdf.String(f"/{font_tag} {size:.1f} Tf {r:.3f} {g:.3f} {b:.3f} rg")
                changed = True

            # MaxLen (character limit)
            if "maxLen" in s:
                ml = int(s["maxLen"]) if s["maxLen"] else 0
                if ml > 0:
                    annot['/MaxLen'] = ml
                else:
                    # Remove limit
                    if '/MaxLen' in annot: del annot['/MaxLen']
                changed = True

            # Default Value
            if "defaultValue" in s:
                dv = s["defaultValue"]
                if dv:
                    annot['/DV'] = pikepdf.String(dv)
                else:
                    if '/DV' in annot: del annot['/DV']
                changed = True

            # Visibility (/F annotation flags)
            if "visibility" in s:
                f_flags = int(annot.get('/F', 4))  # default Print=4
                vis = s["visibility"]
                # Clear relevant bits first: Hidden(1), Print(2), NoView(5)
                f_flags &= ~(1 << 1)   # clear Hidden (bit 2)
                f_flags &= ~(1 << 5)   # clear NoView (bit 6)
                f_flags &= ~(1 << 2)   # clear Print  (bit 3)
                if vis == "visible":
                    f_flags |= (1 << 2)           # Print on
                elif vis == "hidden":
                    f_flags |= (1 << 1)           # Hidden on
                elif vis == "visible_no_print":
                    pass                           # not hidden, print off
                elif vis == "hidden_printable":
                    f_flags |= (1 << 2)           # Print on
                    f_flags |= (1 << 5)           # NoView on
                annot['/F'] = f_flags
                changed = True

            # Orientation / Rotation (/MK/R)
            if "orientation" in s:
                rot = int(s["orientation"]) if s["orientation"] else 0
                if rot in (0, 90, 180, 270):
                    if '/MK' not in annot: annot['/MK'] = pikepdf.Dictionary()
                    if rot == 0:
                        if '/R' in annot['/MK']: del annot['/MK']['/R']
                    else:
                        annot['/MK']['/R'] = rot
                    changed = True

            # Flags (Ff)
            ff = int(annot.get('/Ff', 0))
            if "readOnly" in s: ff = (ff | 1) if s["readOnly"] else (ff & ~1)
            if "required" in s: ff = (ff | 2) if s["required"] else (ff & ~2)
            if "multiline" in s: ff = (ff | 0x1000) if s["multiline"] else (ff & ~0x1000)
            if "password" in s: ff = (ff | 0x2000) if s["password"] else (ff & ~0x2000)
            if "scrollText" in s: ff = (ff & ~0x800000) if s["scrollText"] else (ff | 0x800000)  # inverted: DoNotScroll
            if "comb" in s: ff = (ff | 0x1000000) if s["comb"] else (ff & ~0x1000000)
            if "locked" in s: ff = (ff | 0x80) if s["locked"] else (ff & ~0x80)  # bit 8

            # Choice flags
            if "editable" in s: ff = (ff | 0x40000) if s["editable"] else (ff & ~0x40000)
            if "sort" in s: ff = (ff | 0x80000) if s["sort"] else (ff & ~0x80000)
            if "spellCheck" in s: ff = (ff & ~0x400000) if s["spellCheck"] else (ff | 0x400000)
            if "commitImmediately" in s: ff = (ff | 0x4000000) if s["commitImmediately"] else (ff & ~0x4000000)

            if ff != int(annot.get('/Ff', 0)):
                annot['/Ff'] = ff
                changed = True

            # Dropdown Options
            if "options" in s:
                opts = s["options"]
                if isinstance(opts, list):
                    pike_opts = []
                    for o in opts:
                        # Handle both string format (old) and object format (new)
                        if isinstance(o, str):
                            # Old format: simple string
                            label = value = o
                        elif isinstance(o, dict):
                            # New format: {label: "...", value: "..."}
                            label = str(o.get("label", ""))
                            value = str(o.get("value", label))
                        else:
                            continue
                        
                        if label == value:
                            pike_opts.append(pikepdf.String(label))
                        else:
                            pike_opts.append(pikepdf.Array([pikepdf.String(value), pikepdf.String(label)]))
                    annot['/Opt'] = pikepdf.Array(pike_opts)
                    changed = True

            # Tooltip
            if "tooltip" in s:
                if s["tooltip"]:
                    annot['/TU'] = pikepdf.String(s["tooltip"])
                else:
                    # Clear tooltip when set to empty string
                    if '/TU' in annot: del annot['/TU']
                changed = True

            # Export Value (for radio-like behavior)
            if s.get("exportValue"):
                ev = s["exportValue"]
                if '/AP' in annot and '/N' in annot['/AP']:
                    ap_n = annot['/AP']['/N']
                    on_state = next((k for k in ap_n.keys() if str(k) not in ['/Off', 'Off']), None)
                    if on_state and str(on_state) != '/' + ev:
                        ap_n[pikepdf.Name('/' + ev)] = ap_n[on_state]
                        del ap_n[on_state]
                        if str(annot.get('/AS', '')) == str(on_state): annot['/AS'] = pikepdf.Name('/' + ev)
                        if str(annot.get('/V', '')) == str(on_state): annot['/V'] = pikepdf.Name('/' + ev)
                changed = True

            # JS Actions
            if "formatType" in s and s["formatType"] == "":
                if '/AA' in annot:
                    if '/F' in annot['/AA']: del annot['/AA']['/F']
                    if '/K' in annot['/AA']: del annot['/AA']['/K']
                    if len(annot['/AA']) == 0: del annot['/AA']
                # Also clear from parent if it has its own /AA/F or /AA/K
                _par = annot.get('/Parent')
                if _par is not None and not isinstance(_par, pikepdf.Dictionary):
                    try: _par = pdf.get_object(_par)
                    except: _par = None
                if _par is not None and isinstance(_par, pikepdf.Dictionary) and '/AA' in _par:
                    if '/F' in _par['/AA']: del _par['/AA']['/F']
                    if '/K' in _par['/AA']: del _par['/AA']['/K']
                    if len(_par['/AA']) == 0: del _par['/AA']
                changed = True
            else:
                js_actions = build_format_js(s, settings)
                if js_actions:
                    if '/AA' not in annot: annot['/AA'] = pikepdf.Dictionary()
                    if "format" in js_actions:
                        fmt_act = pdf.make_indirect(pikepdf.Dictionary({'/S': pikepdf.Name('/JavaScript'), '/JS': js_actions["format"]}))
                        annot['/AA']['/F'] = fmt_act
                    if "keystroke" in js_actions:
                        ks_act = pdf.make_indirect(pikepdf.Dictionary({'/S': pikepdf.Name('/JavaScript'), '/JS': js_actions["keystroke"]}))
                        annot['/AA']['/K'] = ks_act
                    # Propagate to parent's /AA if it has its own format actions
                    _par = annot.get('/Parent')
                    if _par is not None and not isinstance(_par, pikepdf.Dictionary):
                        try: _par = pdf.get_object(_par)
                        except: _par = None
                    if _par is not None and isinstance(_par, pikepdf.Dictionary) and '/AA' in _par:
                        if '/F' in _par['/AA'] and "format" in js_actions:
                            _par['/AA']['/F'] = fmt_act
                        if '/K' in _par['/AA'] and "keystroke" in js_actions:
                            _par['/AA']['/K'] = ks_act
                    changed = True

            # Validation rules (/AA/V)
            if "validation" in s and isinstance(s["validation"], dict):
                v = s["validation"]
                rule = v.get("rule", "")
                js_parts = []
                if rule == "range":
                    min_v = v.get("minVal", "")
                    max_v = v.get("maxVal", "")
                    err = v.get("errorMsg", "Value out of range")
                    conds = []
                    if min_v != "": conds.append(f"val < {min_v}")
                    if max_v != "": conds.append(f"val > {max_v}")
                    if conds:
                        js_parts.append(f"var val = parseFloat(event.value); if (!isNaN(val) && ({' || '.join(conds)})) {{ app.alert('{err}'); event.rc = false; }}")
                elif rule == "length":
                    min_c = v.get("minChars", "")
                    max_c = v.get("maxChars", "")
                    err = v.get("errorMsg", "Invalid length")
                    conds = []
                    if min_c != "": conds.append(f"event.value.length < {min_c}")
                    if max_c != "": conds.append(f"event.value.length > {max_c}")
                    if conds:
                        js_parts.append(f"if (event.value && ({' || '.join(conds)})) {{ app.alert('{err}'); event.rc = false; }}")
                elif rule == "email":
                    err = v.get("errorMsg", "Please enter a valid email address")
                    js_parts.append(f"if (event.value && !/^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$/.test(event.value)) {{ app.alert('{err}'); event.rc = false; }}")
                elif rule == "date":
                    err = v.get("errorMsg", "Please enter a valid date")
                    js_parts.append(f"if (event.value && isNaN(Date.parse(event.value))) {{ app.alert('{err}'); event.rc = false; }}")
                elif rule == "regex":
                    regex = v.get("regex", "")
                    err = v.get("errorMsg", "Invalid format")
                    if regex:
                        js_parts.append(f"if (event.value && !/{regex}/.test(event.value)) {{ app.alert('{err}'); event.rc = false; }}")
                elif rule == "":
                    # Clear validation
                    if '/AA' in annot and '/V' in annot['/AA']:
                        del annot['/AA']['/V']
                        if len(annot['/AA']) == 0: del annot['/AA']
                        changed = True

                if js_parts:
                    js_code = "; ".join(js_parts)
                    if '/AA' not in annot: annot['/AA'] = pikepdf.Dictionary()
                    annot['/AA']['/V'] = pdf.make_indirect(pikepdf.Dictionary({
                        '/S': pikepdf.Name('/JavaScript'),
                        '/JS': js_code
                    }))
                    changed = True

            # Custom Actions (/AA sub-keys)
            if "actions" in s and isinstance(s["actions"], dict):
                action_map = {
                    "mouseUp": "/U", "mouseDown": "/D",
                    "mouseEnter": "/E", "mouseExit": "/X",
                    "onFocus": "/Fo", "onBlur": "/Bl",
                    "keystroke": "/K", "format": "/F",
                    "validate": "/V", "calculate": "/C",
                }
                for action_name, js_code in s["actions"].items():
                    pdf_key = action_map.get(action_name)
                    if not pdf_key: continue
                    if js_code and js_code.strip():
                        if '/AA' not in annot: annot['/AA'] = pikepdf.Dictionary()
                        annot['/AA'][pdf_key] = pdf.make_indirect(pikepdf.Dictionary({
                            '/S': pikepdf.Name('/JavaScript'),
                            '/JS': js_code
                        }))
                        changed = True
                    else:
                        # Remove empty action
                        if '/AA' in annot and pdf_key in annot['/AA']:
                            del annot['/AA'][pdf_key]
                            changed = True

            if changed:
                # Keep existing /AP as fallback — many viewers ignore NeedAppearances
                acroform['/NeedAppearances'] = True

        # Apply by name
        styled_by_name = changes.get("styled", {})
        for name, styles in styled_by_name.items():
            t_name = name_updates.get(name, name)
            if t_name in annot_map:
                for _, annot in annot_map[t_name]: apply_styles(annot, styles)
                result["styled"] += 1

        # Apply by objgen (stable across saves) — with name cross-validation
        styled_by_og = changes.get("styled_by_objgen", {})
        name_based_styled = set(changes.get("styled", {}).keys())
        for og_key, styles in styled_by_og.items():
            try:
                if og_key in objgen_map:
                    _, annot = objgen_map[og_key]
                    wname = str(_resolve_attribute(annot, '/T', ''))
                    # Primary check: editor's intended name must match actual widget name
                    editor_intended_name = styles.get("_name")
                    if editor_intended_name and wname != editor_intended_name:
                        sys.stderr.write(f"Warning: styled objgen {og_key} -> '{wname}' but editor intended '{editor_intended_name}' — skipping stale objgen\n")
                        continue
                    # Fallback for older sessions without _name
                    if not editor_intended_name:
                        if wname and wname not in name_based_styled:
                            sys.stderr.write(f"Warning: objgen {og_key} -> '{wname}' not in name-based styles — skipping stale objgen\n")
                            continue
                    # Remove the internal _name key before applying styles
                    clean_styles = {k: v for k, v in styles.items() if k != '_name'}
                    apply_styles(annot, clean_styles)
                    result["styled"] += 1
            except Exception as e:
                sys.stderr.write(f"Warning: objgen-based style failed for {og_key}: {e}\n")

        # Apply by index (legacy fallback)
        styled_by_idx = changes.get("styled_by_index", {})
        for idx_str, styles in styled_by_idx.items():
            try:
                idx = int(idx_str)
                if idx < len(original_widgets):
                    apply_styles(original_widgets[idx], styles)
                    result["styled"] += 1
            except: pass

        # 8.5 Apply Calculations
        calculations = changes.get("calculations", {})
        if calculations:
            annot_map = build_annot_map()  # Refresh after renames/creates
            result["calculated"] = 0

            for target_name, calc in calculations.items():
                t_name = name_updates.get(target_name, target_name)
                if t_name not in annot_map:
                    continue

                # Build the calculation JavaScript
                calc_js = build_calculate_js(calc, field_name=t_name)
                if not calc_js:
                    continue

                for _, annot in annot_map[t_name]:
                    calc_action = pdf.make_indirect(pikepdf.Dictionary({
                        '/S': pikepdf.Name('/JavaScript'),
                        '/JS': calc_js
                    }))

                    # Write /AA/C (Calculate action) to the widget
                    if '/AA' not in annot:
                        annot['/AA'] = pikepdf.Dictionary()
                    annot['/AA']['/C'] = calc_action

                    # Also update parent's /AA/C if the parent has its own.
                    # Acrobat reads /AA from the parent field dict, so leaving
                    # a stale /AA/C there causes the old formula to persist.
                    parent = annot.get('/Parent')
                    if parent is not None and not isinstance(parent, pikepdf.Dictionary):
                        try:
                            parent = pdf.get_object(parent)
                        except Exception:
                            parent = None
                    if parent is not None and isinstance(parent, pikepdf.Dictionary):
                        if '/AA' in parent and '/C' in parent['/AA']:
                            parent['/AA']['/C'] = calc_action

                    # Strip stale /V from prior saves so the viewer
                    # doesn't try to render "0" before calcs run.
                    if '/V' in annot:
                        del annot['/V']

                    # Set ONLY ReadOnly — clear DoNotSpellCheck and
                    # any other Ff bits that the style pass added.
                    # DoNotSpellCheck is meaningless for computed fields.
                    if calc.get("readOnly", False):
                        ff = int(annot.get('/Ff', 0))
                        ff = ff & ~0x400000   # clear DoNotSpellCheck
                        ff = ff | 1           # set ReadOnly
                        annot['/Ff'] = ff

                result["calculated"] += 1

        # 8.6 Auto-fix self-referencing calculations in ALL existing fields.
        # A field whose JS references itself (e.g. cal28f reads cal28f.value)
        # always evaluates to 0.  Detect and rewrite such calculations even if
        # the user did not modify them in the Calculate editor.
        annot_map = build_annot_map()
        import re as _selfref_re
        for fname, entries in annot_map.items():
            if calculations and fname in calculations:
                continue  # Already regenerated above
            for _, annot in entries:
                aa = annot.get('/AA')
                if not aa or '/C' not in aa:
                    continue
                try:
                    c_action = aa['/C']
                    if hasattr(c_action, 'resolve'):
                        c_action = c_action.resolve()
                    js_code = str(c_action.get('/JS', ''))
                    if not js_code:
                        continue
                    # Quick check: does the JS reference this field name?
                    if fname not in js_code:
                        continue
                    # Check if this field is in its own getField references
                    field_refs = _selfref_re.findall(
                        r'(?:this\.)?getField\s*\(\s*["\']([^"\']+)["\']\s*\)', js_code)
                    if fname not in field_refs:
                        continue
                    # Self-reference detected — parse, strip, and rebuild
                    from extract_fields import _parse_calculate_js
                    parsed = _parse_calculate_js(js_code, field_name=fname)
                    if parsed and parsed.get('type') == 'CUSTOM' and parsed.get('sources'):
                        fixed_js = build_calculate_js(parsed, field_name=fname)
                        if fixed_js:
                            annot['/AA']['/C'] = pdf.make_indirect(pikepdf.Dictionary({
                                '/S': pikepdf.Name('/JavaScript'),
                                '/JS': fixed_js
                            }))
                            sys.stderr.write(f"Auto-fixed self-reference in {fname}\n")
                except Exception as e:
                    sys.stderr.write(f"Warning: self-ref fix failed for {fname}: {e}\n")
                break  # Only need first annotation per field

        # Build /CO (Calculation Order) via topological sort
        # Include BOTH new calculations AND existing /AA/C fields from the PDF
        # so that previously-saved calculations remain in the calculation order.
        if calculations:
            try:
                all_names = set(annot_map.keys())

                # Discover existing calculated fields (have /AA/C) not in current changes
                merged_calculations = dict(calculations)  # Start with new calcs
                for fname, entries in annot_map.items():
                    if fname in merged_calculations:
                        continue  # Already in the new calculations
                    for _, annot in entries:
                        aa = annot.get('/AA')
                        if aa and '/C' in aa:
                            # This field has an existing calculation — extract its sources
                            # so it can be included in the topological sort
                            existing_sources = []
                            try:
                                c_action = aa['/C']
                                js_obj = c_action.get('/JS', '')
                                # /JS can be a String or a Stream (compressed)
                                if isinstance(js_obj, pikepdf.Stream):
                                    js_code = bytes(js_obj.read_bytes()).decode('utf-8', errors='replace')
                                else:
                                    js_code = str(js_obj)
                                # Parse AFSimple_Calculate sources: new Array("f1","f2",...)
                                import re as _re
                                arr_match = _re.search(r'new\s+Array\s*\(([^)]+)\)', js_code)
                                if arr_match:
                                    existing_sources = _re.findall(r'"([^"]+)"', arr_match.group(1))
                                else:
                                    # Parse getField("name") references
                                    existing_sources = _re.findall(r'getField\s*\(\s*"([^"]+)"\s*\)', js_code)
                            except Exception:
                                pass
                            merged_calculations[fname] = {
                                "type": "EXISTING",
                                "sources": existing_sources
                            }
                            break  # Only need to check one annotation per field

                ordered = _topological_sort_calculations(merged_calculations, all_names)

                # Build /CO array with indirect references to the field annotations
                co_refs = []
                for field_name in ordered:
                    t_name = name_updates.get(field_name, field_name)
                    if t_name in annot_map:
                        # Use the first annotation for this field
                        _, annot = annot_map[t_name][0]
                        co_refs.append(annot)

                if co_refs:
                    acroform['/CO'] = pikepdf.Array(co_refs)
            except ValueError as e:
                result["calc_error"] = str(e)

        _diag_snapshot("AFTER-STYLING", pdf)
        _diag_page_fields("AFTER-STYLING", pdf, 2)

        # 8. Group Radios (Moved to END to ensure all creates/renames are complete)
        new_groups = changes.get("new_radio_groups", {})
        if new_groups:
            annot_map = build_annot_map() # Final rebuild
            result["grouped"] = _apply_radio_groups(pdf, new_groups, annot_map, settings)
            _diag_snapshot("AFTER-RADIO-GROUP", pdf)
            _diag_page_fields("AFTER-RADIO-GROUP", pdf, 2)

        # 8b. Link annotation changes (move/resize/delete)
        # Links don't have /T names; we identify them by the Link_P{page}_{idx} convention
        link_changes = {**changes.get("moved", {}), **changes.get("resized", {})}
        link_deletes = changes.get("deleted", [])
        has_link_ops = any(k.startswith("Link_P") for k in link_changes) or any(d.startswith("Link_P") for d in link_deletes if isinstance(d, str))
        if has_link_ops:
            # Build link annotation map: {name: (page, annot)}
            link_map = {}
            for page_idx, page in enumerate(pdf.pages):
                if '/Annots' not in page:
                    continue
                link_idx = 0
                for annot in page.Annots:
                    if annot.get('/Subtype') != pikepdf.Name('/Link'):
                        continue
                    rect = [float(c) for c in annot.get('/Rect', [0, 0, 0, 0])]
                    # Normalize rect (some PDFs have inverted coordinates)
                    if rect[0] > rect[2]:
                        rect[0], rect[2] = rect[2], rect[0]
                    if rect[1] > rect[3]:
                        rect[1], rect[3] = rect[3], rect[1]
                    if rect[2] - rect[0] < 1 or rect[3] - rect[1] < 1:
                        continue
                    link_name = f"Link_P{page_idx}_{link_idx}"
                    link_map[link_name] = (page, annot)
                    link_idx += 1

            # Delete links
            for name in link_deletes:
                if isinstance(name, str) and name.startswith("Link_P") and name in link_map:
                    page, annot = link_map[name]
                    if '/Annots' in page:
                        _remove_from_list(page.Annots, annot)
                    result["deleted"] = result.get("deleted", 0) + 1

            # Move/resize links
            for name, data in link_changes.items():
                if not name.startswith("Link_P") or name not in link_map:
                    continue
                v_rect = data.get("rect")
                if v_rect:
                    page, annot = link_map[name]
                    p_idx = pdf.pages.index(page)
                    x_off = p_x_offsets[p_idx] if p_idx < len(p_x_offsets) else 0
                    new_rect = [v_rect[0] + x_off, p_heights[p_idx] - v_rect[3],
                                v_rect[2] + x_off, p_heights[p_idx] - v_rect[1]]
                    annot['/Rect'] = pikepdf.Array([float(c) for c in new_rect])
                    result["moved"] = result.get("moved", 0) + 1

        # Build set of field names explicitly styled with transparent fill.
        # These must NOT have /MK/BG forced back by the enforcement pass.
        transparent_fill_names = set()
        for name, styles in styled_by_name.items():
            bg = styles.get("fillColor", styles.get("fill_color"))
            if bg == "transparent":
                transparent_fill_names.add(name_updates.get(name, name))
        for og_key, styles in styled_by_og.items():
            bg = styles.get("fillColor", styles.get("fill_color"))
            if bg == "transparent":
                try:
                    if og_key in objgen_map:
                        _, a = objgen_map[og_key]
                        n = str(a.get('/T', ''))
                        if n: transparent_fill_names.add(n)
                except: pass
        for idx_str, styles in styled_by_idx.items():
            bg = styles.get("fillColor", styles.get("fill_color"))
            if bg == "transparent":
                try:
                    idx = int(idx_str)
                    if idx < len(original_widgets):
                        n = str(original_widgets[idx].get('/T', ''))
                        if n: transparent_fill_names.add(n)
                except: pass

        # 9. Global Border & Visibility Enforcement (Final Pass)
        # 1. Strictly enforce "no borders" IF no border color is defined in settings.
        # 2. ENFORCE appearance streams for all checkboxes to ensure visibility without borders.
        global_border_color = settings.get('field_border_color', 'none')
        global_border_w = float(settings.get('field_border_thickness', 0))
        has_global_border = global_border_color and global_border_color.startswith('#') and global_border_w > 0

        # Consistent styling for enforcement
        bg_color = settings.get('field_background_color', '#EDF4FF')
        bg_rgb = [int(bg_color.lstrip('#')[i:i+2], 16) / 255.0 for i in (0, 2, 4)] if (bg_color and len(bg_color.lstrip('#')) == 6) else [0.929, 0.957, 1.0]
        style = settings.get('checkbox_style', settings.get('checkboxStyle', 'check'))

        for page in pdf.pages:
            if '/Annots' in page:
                for annot in page.Annots:
                    if annot.get('/Subtype') == pikepdf.Name('/Widget') or annot.get('/Subtype') == '/Widget':
                        # A. Border Removal
                        if not has_global_border:
                            if '/MK' in annot and '/BC' in annot['/MK']:
                                del annot['/MK']['/BC']
                            if '/BS' in annot: del annot['/BS']
                            if '/Border' in annot: del annot['/Border']

                        # B1. Fix non-button fields (Text, Choice, Signature)
                        ft = str(_resolve_attribute(annot, '/FT', ''))
                        if '/Btn' not in ft:
                            # B1a. Remove malformed AP/N dictionaries
                            # Some PDFs have AP/N as a Dictionary (with /Off key)
                            # on text fields instead of a Stream.  The PDF spec
                            # requires /AS when AP/N is a dict; without it viewers
                            # show blank fields.  Remove so NeedAppearances
                            # triggers proper regeneration.
                            if '/AP' in annot:
                                ap_n = annot['/AP'].get('/N')
                                if ap_n is not None and isinstance(ap_n, pikepdf.Dictionary):
                                    del annot['/AP']

                            # B1b. Clean up /MK for text fields
                            # /MK/CA (button caption) is meaningless on text
                            # fields and having /MK without /BG confuses viewers
                            # into showing black fill after appearance regeneration.
                            # However, if the user explicitly set fill to transparent,
                            # we must respect that and NOT force a background color.
                            # Fields with NO /MK at all are left untouched — they
                            # were transparent in the original PDF and should stay so.
                            f_name = str(annot.get('/T', ''))
                            if not f_name and '/Parent' in annot:
                                f_name = str(annot['/Parent'].get('/T', ''))
                            user_wants_transparent = f_name in transparent_fill_names

                            if '/MK' in annot:
                                mk = annot['/MK']
                                # Remove orphan /CA on non-button fields
                                if '/CA' in mk:
                                    del mk['/CA']
                                # After cleanup, if /MK is now empty remove it
                                # (field becomes transparent, no black-fill risk)
                                if len(mk.keys()) == 0:
                                    del annot['/MK']
                                elif not user_wants_transparent and '/BG' not in mk:
                                    # /MK still has keys but no /BG — add default
                                    # to prevent black fill on regen
                                    mk['/BG'] = pikepdf.Array(bg_rgb)
                            # No /MK at all: field is transparent — leave it alone

                        # B2. Visibility Enforcement for Buttons (Radio/Checkbox)
                        if '/Btn' in ft:
                            # If it has no AP, or we moved it, or it was incorrectly saved...
                            # We forcefully regenerate it to ensure it draws the background color.
                            # Acrobat's NeedAppearances is unreliable for vector-drawn buttons.
                            if '/AP' not in annot or not has_global_border:
                                rect = annot.get('/Rect', [0, 0, 10, 10])
                                # Determine state name
                                ev = 'Yes'
                                if '/AP' in annot and '/N' in annot['/AP']:
                                    for k in annot['/AP']['/N'].keys():
                                        if str(k) not in ['/Off', 'Off']: ev = str(k).lstrip('/'); break

                                appearances = _create_radio_appearance_stream(pdf, rect, ev, bg_rgb, style)
                                ap_n = pikepdf.Dictionary()
                                ap_n['/Off'] = appearances['off']
                                ap_n[pikepdf.Name('/' + ev)] = appearances['on']
                                annot['/AP'] = pikepdf.Dictionary({'/N': ap_n})

                                # Fix DA for Acrobat compatibility
                                annot['/DA'] = pikepdf.String('0 g /ZaDb 0 Tf')
        _diag_snapshot("AFTER-SECTION9-BORDERS", pdf)
        _diag_page_fields("AFTER-SECTION9-BORDERS", pdf, 2)

        # 10. NeedAppearances / /CO conflict resolution
        # When /CO is present Acrobat runs calculations on open.  If
        # NeedAppearances is ALSO True the two passes conflict and
        # calculated fields render as invisible.  Fix: when /CO exists,
        # ensure every text field has a background-fill /AP stream and
        # then set NeedAppearances = False so Acrobat never attempts a
        # second appearance-regeneration pass that clobbers the
        # calculation results.
        if '/CO' in acroform and len(acroform['/CO']) > 0:
            for page in pdf.pages:
                if '/Annots' not in page:
                    continue
                for annot in page.Annots:
                    ft = str(_resolve_attribute(annot, '/FT', ''))
                    if '/Tx' not in ft and '/Ch' not in ft:
                        continue  # only text & choice fields

                    # Resolve field name for transparent-fill check
                    f_name = str(annot.get('/T', ''))
                    if not f_name and '/Parent' in annot:
                        f_name = str(annot['/Parent'].get('/T', ''))
                    is_transparent = f_name in transparent_fill_names

                    # Skip fields that already have a valid Stream AP
                    # UNLESS the user explicitly set them to transparent
                    # (AP may contain a baked-in background fill that must
                    # be stripped).
                    if '/AP' in annot:
                        ap_n = annot['/AP'].get('/N')
                        if ap_n is not None and isinstance(ap_n, pikepdf.Stream):
                            if is_transparent:
                                _strip_ap_fill(annot)
                            continue

                    if is_transparent:
                        # Transparent fields don't need a background-fill AP
                        continue

                    # Build a background-fill AP from /MK/BG.
                    # If the field has no /MK/BG it was inherently transparent
                    # in the original PDF — do NOT force the global default
                    # blue onto it.  Only create a fill AP when the field
                    # actually has a background color defined.
                    rect = annot.get('/Rect', [0, 0, 10, 10])
                    w = abs(float(rect[2]) - float(rect[0]))
                    h = abs(float(rect[3]) - float(rect[1]))
                    mk = annot.get('/MK')
                    if mk and '/BG' in mk:
                        c = mk['/BG']
                        rb, gb, bb = float(c[0]), float(c[1]), float(c[2])
                    else:
                        # No /MK/BG — field is transparent; skip AP creation
                        continue
                    ap_bytes = (
                        f"q\n{rb:.3f} {gb:.3f} {bb:.3f} rg\n"
                        f"0 0 {w:.2f} {h:.2f} re f\nQ\n"
                    ).encode()
                    ap_stream = pikepdf.Stream(pdf, ap_bytes)
                    ap_stream['/Type']    = pikepdf.Name('/XObject')
                    ap_stream['/Subtype'] = pikepdf.Name('/Form')
                    ap_stream['/BBox']    = pikepdf.Array([0, 0, w, h])
                    annot['/AP'] = pikepdf.Dictionary({
                        '/N': pdf.make_indirect(ap_stream)
                    })
            # Now safe to disable NeedAppearances
            acroform['/NeedAppearances'] = False

            # Add a document-level open action that forces Acrobat to
            # run all calculations once the document is ready.  With
            # NeedAppearances=False, Acrobat won't auto-run /CO on
            # open, so we kick-start it via JavaScript.
            open_js = pikepdf.Dictionary({
                '/S': pikepdf.Name('/JavaScript'),
                '/JS': pikepdf.String('this.calculateNow();')
            })
            pdf.Root['/OpenAction'] = pdf.make_indirect(open_js)
        _diag_snapshot("AFTER-SECTION10-CO", pdf)
        _diag_page_fields("AFTER-SECTION10-CO", pdf, 2)

        # 10. Apply Tab Order
        tab_order = changes.get("tab_order", {})
        if tab_order:
            for page_key, ordered_names in tab_order.items():
                try:
                    p_idx = int(page_key)
                    if p_idx < 0 or p_idx >= len(pdf.pages):
                        continue
                    page = pdf.pages[p_idx]
                    if '/Annots' not in page:
                        continue

                    # Build lookup: field name → annotation reference
                    annots_by_name = {}
                    other_annots = []  # Non-widget annots (links, etc.)
                    for annot in page.Annots:
                        ft = annot.get('/FT') or (annot.get('/Parent', {}).get('/FT') if '/Parent' in annot else None)
                        t_val = str(annot.get('/T', ''))
                        if not t_val and '/Parent' in annot:
                            t_val = str(annot['/Parent'].get('/T', ''))
                        if t_val and ft:
                            annots_by_name[t_val] = annot
                        else:
                            other_annots.append(annot)

                    # Rebuild /Annots in specified order
                    new_annots = []
                    for name in ordered_names:
                        resolved = name_updates.get(name, name)
                        if resolved in annots_by_name:
                            new_annots.append(annots_by_name.pop(resolved))
                    # Append any remaining widget annots not in the order list
                    for annot in annots_by_name.values():
                        new_annots.append(annot)
                    # Append non-widget annots at the end
                    new_annots.extend(other_annots)

                    page['/Annots'] = pikepdf.Array(new_annots)
                    # Set page tab order to Structure (/S) so viewers respect /Annots order
                    page['/Tabs'] = pikepdf.Name('/S')
                except Exception:
                    pass  # Skip invalid page indices

        # Final Save using temp file
        _diag_snapshot("BEFORE-SAVE", pdf)
        _diag_all_rects("BEFORE-SAVE", pdf)
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
            tmp_path = tmp.name
        pdf.save(tmp_path)
        pdf.close()
        shutil.move(tmp_path, output_path)
        result["success"] = True

    except Exception as e:
        import traceback
        result["error"] = str(e)
        result["traceback"] = traceback.format_exc()

    # Write diagnostic log
    try:
        with open(_diag_log_path, 'w') as f:
            f.write('\n'.join(_diag_lines) + '\n')
        sys.stderr.write(f"[DIAG] Log written to {_diag_log_path}\n")
    except: pass

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf_path")
    parser.add_argument("output_path")
    parser.add_argument("changes_json", nargs='?', help="Positional changes JSON (legacy)")
    parser.add_argument("--changes-file", help="Path to JSON file containing changes")
    parser.add_argument("--settings", help="JSON string of settings")
    args = parser.parse_args()
    
    try:
        changes = {}
        if args.changes_file:
            with open(args.changes_file, 'r') as f: changes = json.load(f)
        elif args.changes_json: changes = json.loads(args.changes_json)
        settings = json.loads(args.settings) if args.settings else {}
        res = apply_field_changes(args.pdf_path, args.output_path, changes, settings)
        print(json.dumps(res))
    except Exception as e:
        print(json.dumps({"success": False, "error": str(e)}))

