import pikepdf
import random
import sys
import os
import re
import subprocess
import time
import json
import argparse
from datetime import datetime, timedelta

# Seed random with current time for truly random selections
random.seed(time.time())

# ============ SETTINGS MANAGEMENT ============
class FillSettings:
    def __init__(self, settings_json=None):
        self.settings = {}
        if settings_json:
            try:
                self.settings = json.loads(settings_json)
            except Exception as e:
                print(f"[WARNING] Error parsing settings JSON: {e}")
        
        # Default styling
        self.bg_color = self._parse_color(self.settings.get('field_background_color', '#EDF4FF'))
        self.font_size = float(self.settings.get('field_font_size', 9))
        self.font_family = self.settings.get('field_font_family', 'Helvetica')
        self.text_color = self._parse_color(self.settings.get('field_font_color', '#000000'))
        
        # Formatting
        self.currency_symbol = self.settings.get('currency_symbol', '$')
        self.date_format = self.settings.get('date_format', 'mm/dd/yyyy').lower()
        self.phone_format = self.settings.get('phone_format', '(XXX) XXX-XXXX')
        self.thousands_sep = self.settings.get('number_thousands_separator', ',')
        # Check both snake_case and camelCase keys
        self.checkbox_style = self.settings.get('checkbox_style', self.settings.get('checkboxStyle', 'Check'))
        
    def _parse_color(self, hex_color):
        """Convert #RRGGBB to (r, g, b) float tuple."""
        if not hex_color or not hex_color.startswith('#'):
            return (0, 0, 0)
        try:
            hex_color = hex_color.lstrip('#')
            if len(hex_color) == 6:
                r, g, b = [int(hex_color[i:i+2], 16) / 255.0 for i in (0, 2, 4)]
                return (r, g, b)
        except:
            pass
        return (0, 0, 0)

# ============ AFNumber_Format pre-formatter ============
# v1.0.2: Pre-format /V for fields whose /AA/F contains AFNumber_Format.
# Rationale: with NeedAppearances=True, viewers regenerate field appearances
# from /V using /DA — but format-action JavaScript (/AA/F) runs on live
# keystroke/focus events, not during passive appearance regeneration.
# Result: a read-only calc field holding /V="2500.0" renders as "2500.0"
# (or blank in some viewers) instead of "$2,500.00". Fix: detect
# AFNumber_Format, format the number in Python, write the formatted string
# to /V, and strip /AA/F so nothing tries to re-format it later. Other
# field properties (/Q, /DA, /MK, /Ff, comb layout) are left untouched so
# the viewer's own NeedAppearances regen continues to center quantity
# fields and distribute comb cells correctly.
def parse_af_number_format(js):
    """
    Parse AFNumber_Format(nDec, sepStyle, negStyle, currStyle, strCurrency,
    bCurrencyPrepend) from a /AA/F JavaScript snippet. Returns a dict or
    None if no match.
    """
    if not js:
        return None
    m = re.search(
        r'AFNumber_Format\s*\(\s*'
        r'(\d+)\s*,\s*'                              # nDec
        r'(\d+)\s*,\s*'                              # sepStyle
        r'(\d+)\s*,\s*'                              # negStyle
        r'(\d+)\s*,\s*'                              # currStyle
        r'"((?:[^"\\]|\\.)*)"\s*,\s*'                # strCurrency
        r'(true|false)'                              # bCurrencyPrepend
        r'\s*\)',
        js, re.IGNORECASE
    )
    if not m:
        return None
    currency = m.group(5).encode().decode('unicode_escape')
    return {
        'n_dec': int(m.group(1)),
        'sep_style': int(m.group(2)),
        'neg_style': int(m.group(3)),
        'curr_style': int(m.group(4)),
        'currency': currency,
        'prepend': m.group(6).lower() == 'true',
    }


def format_af_number(value, fmt):
    """
    Format a numeric value using AFNumber_Format rules. `value` can be a
    float, int, or a string like "2500.0"/"7.6". Returns the formatted
    display string (e.g., "$2,500.00"). Non-numeric input is returned as-is.
    """
    if fmt is None:
        return str(value)
    try:
        if isinstance(value, str):
            cleaned = value.replace('$', '').replace(',', '').strip()
            if cleaned == '':
                return value
            num = float(cleaned)
        else:
            num = float(value)
    except (ValueError, TypeError):
        return str(value)

    # sepStyle: 0 = 1,234.56 ; 1 = 1234.56 ; 2 = 1.234,56 ; 3 = 1234,56
    sep_style = fmt['sep_style']
    use_thousands = sep_style in (0, 2)
    decimal_char = '.' if sep_style in (0, 1) else ','
    thousand_char = ',' if sep_style == 0 else ('.' if sep_style == 2 else '')

    n_dec = fmt['n_dec']
    negative = num < 0
    abs_num = abs(num)

    # Format with US conventions first, then swap separators.
    if use_thousands:
        base = f"{abs_num:,.{n_dec}f}"
    else:
        base = f"{abs_num:.{n_dec}f}"

    if sep_style == 2:
        base = base.replace(',', '\x00').replace('.', ',').replace('\x00', '.')
    elif sep_style == 3:
        base = base.replace('.', ',')
    # sep_style 0 and 1 already correct.

    currency = fmt['currency'] or ''
    if fmt['prepend']:
        body = f"{currency}{base}"
    else:
        body = f"{base}{currency}"

    # negStyle: 0 = -1234 ; 1 = red (color only, no syntax) ; 2 = (1234) ;
    # 3 = red parens. We only adjust the literal text.
    if negative:
        neg_style = fmt['neg_style']
        if neg_style in (2, 3):
            return f"({body})"
        return f"-{body}"
    return body


def preformat_field_value(field, value):
    """
    If `field` has an /AA/F AFNumber_Format action, return the formatted
    display string for `value` and strip /AA/F so viewers don't try to
    re-format. Otherwise return str(value) unchanged.
    """
    aa = field.get('/AA', None)
    if aa is None or '/F' not in aa:
        return str(value)
    f_action = aa['/F']
    js = str(f_action.get('/JS', ''))
    fmt = parse_af_number_format(js)
    if fmt is None:
        return str(value)
    formatted = format_af_number(value, fmt)

    # Strip /AA/F — /V now holds the finished display string.
    try:
        del aa['/F']
        if len(list(aa.keys())) == 0:
            del field['/AA']
    except Exception:
        pass
    # Also strip /AA/K (keystroke validator) — with /V pre-formatted and
    # no /F to reformat on focus, the keystroke script would reject its
    # own output on next focus. Safer to remove.
    try:
        if '/AA' in field and '/K' in field['/AA']:
            del field['/AA']['/K']
            if len(list(field['/AA'].keys())) == 0:
                del field['/AA']
    except Exception:
        pass
    return formatted


# ============ SAMPLE DATA ============
SAMPLE_DATA = {
    'names': ['John Smith', 'Sarah Johnson', 'Michael Brown', 'Emily Davis', 'David Wilson'],
    'companies': ['ABC Corp', 'XYZ Industries', 'Global Solutions', 'Tech Innovations'],
    'phones': ['(734) 555-0123', '(248) 555-0456', '(313) 555-0789', '(586) 555-1234'],
    'dates': ['01/15/2026', '02/20/2026', '06/15/2025', '09/30/2026', '11/12/2025'],
    'states': ['MI', 'OH', 'IL', 'IN', 'WI', 'CA', 'NY', 'TX', 'FL', 'PA'],
    'zips': ['48201', '48202', '48226', '48235', '48301'],
    'addresses': ['123 Main St', '456 Oak Ave', '789 Pine Rd', '321 Elm St'],
    'emails': ['john@email.com', 'sarah@company.com', 'info@business.org'],
    'currencies': ['1250.00', '3500.00', '750.00', '2000.00', '500.00'],
    'percentages': ['95', '98', '85', '92', '88'],
    'rvalues': ['R-13', 'R-19', 'R-30', 'R-38', 'R-49'],
    'btus': ['80000', '60000', '100000', '40000', '120000'],
    'cfm': ['1200', '1400', '1000', '800', '1600'],
}

NUMERIC_PATTERNS = [
    'rvalue', 'r-value', 'r value', 'btuh', 'btu', 'afue', 'seer', 'hspf', 'eer', 'cop',
    'preservice', 'postservice', 'pre-service', 'post-service',
    'input', 'output', 'rated', 'peak', 'enthalpy', 'cfm', 'kwh', 'therm',
    'rebate', 'incentive', 'cost', 'price', 'amount', 'total', 'fee',
    'kilowatt', 'watt', 'savings', 'sq ft', 'sqft', 'square',
    'test in', 'test out', 'pre-test', 'post-test', 'capacity',
    'before', 'after', 'quantity', 'qty',
    'tons', 'temperature', 'temp', 'degf', 'deg f', 'degrees', 'units', 'number of',
    'efficiency', 'airflow', 'oxygen', 'carbon', 'monoxide', 'dioxide',
    'coil', 'stack', 'elapsed', 'minutes', 'sir',
    'db', 'wb', 'score', 'program year', 'program_year', 'programyear',
]

def is_numeric_field_name(name: str) -> bool:
    base = re.sub(r'_\d+$', '', name)
    cleaned = base.replace(',', '').replace(' ', '').replace('.', '')
    return cleaned.isdigit() and len(cleaned) > 0

def detect_field_format_from_js(field) -> dict:
    result = {'type': None, 'decimals': 2, 'symbol': '', 'pattern': ''}
    if '/AA' not in field:
        return result
    
    aa = field['/AA']
    format_js = ''
    if '/F' in aa:
        f_action = aa['/F']
        if '/JS' in f_action:
            format_js = str(f_action['/JS'])
    
    if not format_js and '/K' in aa:
        k_action = aa['/K']
        if '/JS' in k_action:
            format_js = str(k_action['/JS'])
    
    if not format_js:
        return result
    
    format_js_lower = format_js.lower()
    if 'afnumber_format' in format_js_lower:
        if '$' in format_js or '€' in format_js or '£' in format_js:
            result['type'] = 'currency'
            match = re.search(r'AFNumber_Format\s*\(\s*(\d+)', format_js, re.IGNORECASE)
            if match: result['decimals'] = int(match.group(1))
            for sym in ['$', '€', '£', '¥']:
                if sym in format_js:
                    result['symbol'] = sym
                    break
        else:
            result['type'] = 'number'
            match = re.search(r'AFNumber_Format\s*\(\s*(\d+)', format_js, re.IGNORECASE)
            if match: result['decimals'] = int(match.group(1))
    elif 'afdate_format' in format_js_lower:
        result['type'] = 'date'
        match = re.search(r'AFDate_Format(?:Ex)?\s*\(\s*["\']([^"\']+)', format_js, re.IGNORECASE)
        if match: result['pattern'] = match.group(1)
    elif 'afspecial_format' in format_js_lower:
        match = re.search(r'AFSpecial_Format\s*\(\s*(\d+)', format_js, re.IGNORECASE)
        if match:
            special_type = int(match.group(1))
            if special_type == 0: result['type'] = 'zip'
            elif special_type == 1: result['type'] = 'zip5'
            elif special_type == 2: result['type'] = 'phone'
            elif special_type == 3: result['type'] = 'ssn'
    elif 'percent' in format_js_lower or '%' in format_js:
        result['type'] = 'percentage'
    elif re.search(r'\\d[*+]', format_js):
        # Regex keystroke validator like /^\d*$/.test(event.change) — digits only
        result['type'] = 'number'
        result['decimals'] = 0

    return result

def create_vector_checkmark_stream(width: float, height: float) -> bytes:
    scale = min(width, height) / 6.0
    x1, y1 = 0.9 * scale, 3.0 * scale
    x2, y2 = 2.1 * scale, 0.9 * scale
    x3, y3 = 5.1 * scale, 5.1 * scale
    line_width = 1.5 * scale
    stream = f"q Q q 0 0 {width:.0f} {height:.0f} re W n {line_width:.1f} w 0 0 0 RG {x1:.1f} {y1:.1f} m {x2:.1f} {y2:.1f} l {x3:.1f} {y3:.1f} l S Q"
    return stream.encode('latin-1')

def create_vector_off_stream() -> bytes:
    return b'q Q'

def create_comb_appearance_stream(width: float, height: float, text: str, max_len: int) -> bytes:
    if max_len <= 0: max_len = 1
    cell_w = width / max_len
    font_size = min(height * 0.7, cell_w * 0.8) # Heuristic for font size
    
    # Background
    stream_parts = [f"q 0.7 0.85 1 rg 0 0 {width:.2f} {height:.2f} re f 0 g /Helv {font_size:.1f} Tf BT"]
    
    # Vertical Alignment (Approximate baseline)
    y_pos = (height - font_size) / 2 + (font_size * 0.2)
    
    # Draw each char
    for i, char in enumerate(text):
        if i >= max_len: break
        
        # Center in cell
        char_w_approx = font_size * 0.5 # rough estimate for digit width
        x_start = i * cell_w
        x_pos = x_start + (cell_w - char_w_approx) / 2
        
        # Escape parens/backslashes
        escaped_char = char.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
        
        # Use Td for positioning (absolute from BT start implies calculating relative offsets, 
        # but using Tm is absolute text matrix which is easier here)
        stream_parts.append(f"1 0 0 1 {x_pos:.2f} {y_pos:.2f} Tm ({escaped_char}) Tj")
    
    stream_parts.append("ET Q")
    return " ".join(stream_parts).encode('latin-1')

def create_vector_cross_stream(width: float, height: float) -> bytes:
    # Draw a Cross (X) centered in the rect
    cx, cy = width / 2.0, height / 2.0
    r = min(width, height) * 0.35
    cmds = [
        "q", "1.6 w", "0 0 0 RG",
        f"{cx-r:.2f} {cy-r:.2f} m", f"{cx+r:.2f} {cy+r:.2f} l",
        f"{cx-r:.2f} {cy+r:.2f} m", f"{cx+r:.2f} {cy-r:.2f} l",
        "S", "Q"
    ]
    return " ".join(cmds).encode('latin-1')

def create_vector_circle_fill_stream(width: float, height: float) -> bytes:
    # Draw a solid Circle (Dot)
    cx, cy = width / 2.0, height / 2.0
    r = min(width, height) * 0.25 # Solid dot radius
    k = 0.55228 * r
    cmds = [
        "q", "0 0 0 rg", # Black fill
        f"{cx+r:.2f} {cy:.2f} m",
        f"{cx+r:.2f} {cy+k:.2f} {cx+k:.2f} {cy+r:.2f} {cx:.2f} {cy+r:.2f} c",
        f"{cx-k:.2f} {cy+r:.2f} {cx-r:.2f} {cy+k:.2f} {cx-r:.2f} {cy:.2f} c",
        f"{cx-r:.2f} {cy-k:.2f} {cx-k:.2f} {cy-r:.2f} {cx:.2f} {cy-r:.2f} c",
        f"{cx+k:.2f} {cy-r:.2f} {cx+r:.2f} {cy-k:.2f} {cx+r:.2f} {cy:.2f} c",
        "f", "Q"
    ]
    return " ".join(cmds).encode('latin-1')

def create_vector_square_stream(width: float, height: float) -> bytes:
    # Draw a solid Square
    cx, cy = width / 2.0, height / 2.0
    r = min(width, height) * 0.25
    cmds = [
        "q", "0 0 0 rg",
        f"{cx-r:.2f} {cy-r:.2f} {r*2:.2f} {r*2:.2f} re",
        "f", "Q"
    ]
    return " ".join(cmds).encode('latin-1')

def create_vector_diamond_stream(width: float, height: float) -> bytes:
    # Draw a solid Diamond
    cx, cy = width / 2.0, height / 2.0
    r = min(width, height) * 0.3
    cmds = [
        "q", "0 0 0 rg",
        f"{cx:.2f} {cy+r:.2f} m", # Top
        f"{cx+r:.2f} {cy:.2f} l", # Right
        f"{cx:.2f} {cy-r:.2f} l", # Bottom
        f"{cx-r:.2f} {cy:.2f} l", # Left
        "f", "Q"
    ]
    return " ".join(cmds).encode('latin-1')

def create_vector_star_stream(width: float, height: float) -> bytes:
    # Draw a solid Star
    cx, cy = width / 2.0, height / 2.0
    r_outer = min(width, height) * 0.35
    r_inner = r_outer * 0.4
    import math
    points = []
    for i in range(10):
        angle = math.pi/2 + i * math.pi/5 # Start at top
        r = r_outer if i % 2 == 0 else r_inner
        px = cx + r * math.cos(angle)
        py = cy + r * math.sin(angle)
        op = "m" if i == 0 else "l"
        points.append(f"{px:.2f} {py:.2f} {op}")
    
    cmds = ["q", "0 0 0 rg"] + points + ["f", "Q"]
    return " ".join(cmds).encode('latin-1')

def make_appearance_dict(pdf: pikepdf.Pdf, on_state_name: str, width: float, height: float, style: str = 'Check') -> pikepdf.Dictionary:
    s = style.lower()
    if s == 'cross':
        check_content = create_vector_cross_stream(width, height)
    elif s == 'circle':
        check_content = create_vector_circle_fill_stream(width, height)
    elif s == 'square':
        check_content = create_vector_square_stream(width, height)
    elif s == 'diamond':
        check_content = create_vector_diamond_stream(width, height)
    elif s == 'star':
        check_content = create_vector_star_stream(width, height)
    else:
        check_content = create_vector_checkmark_stream(width, height)
    
    off_content = create_vector_off_stream()
        
    check_stream = pikepdf.Stream(pdf, check_content)
    check_stream['/BBox'] = pikepdf.Array([0, 0, width, height])
    check_stream['/Subtype'] = pikepdf.Name('/Form')
    check_stream['/Type'] = pikepdf.Name('/XObject')
    check_stream['/Resources'] = pikepdf.Dictionary()
    
    off_stream = pikepdf.Stream(pdf, off_content)
    off_stream['/BBox'] = pikepdf.Array([0, 0, width, height])
    off_stream['/Subtype'] = pikepdf.Name('/Form')
    off_stream['/Type'] = pikepdf.Name('/XObject')
    off_stream['/Resources'] = pikepdf.Dictionary()
    
    ap_n = pikepdf.Dictionary()
    # Name must start with /
    state_name = on_state_name if on_state_name.startswith('/') else '/' + on_state_name
    ap_n[pikepdf.Name(state_name)] = check_stream
    ap_n['/Off'] = off_stream
    return ap_n

def get_field_rect(field) -> tuple:
    rect = field.get('/Rect', [0, 0, 10, 10])
    return abs(float(rect[2]) - float(rect[0])), abs(float(rect[3]) - float(rect[1]))


def _parse_da(da_str):
    """Parse a /DA string like '/Helv 9 Tf 0 g' into (font_name, font_size, color_ops)."""
    m = re.search(r'/(\S+)\s+([\d.]+)\s+Tf', da_str)
    fname = m.group(1) if m else 'Helv'
    fsize = float(m.group(2)) if m else 0.0
    # Color ops: everything in DA that isn't the font operator
    color = re.sub(r'/\S+\s+[\d.]+\s+Tf', '', da_str).strip()
    if not color:
        color = '0 g'
    return fname, fsize, color


# Approximate character widths for Helvetica (as fraction of font size).
# Covers ASCII 32-126; everything else defaults to 0.5.
_HELV_WIDTHS = {
    ' ': 0.278, '!': 0.278, '"': 0.355, '#': 0.556, '$': 0.556, '%': 0.889,
    '&': 0.667, "'": 0.191, '(': 0.333, ')': 0.333, '*': 0.389, '+': 0.584,
    ',': 0.278, '-': 0.333, '.': 0.278, '/': 0.278, '0': 0.556, '1': 0.556,
    '2': 0.556, '3': 0.556, '4': 0.556, '5': 0.556, '6': 0.556, '7': 0.556,
    '8': 0.556, '9': 0.556, ':': 0.278, ';': 0.278, '<': 0.584, '=': 0.584,
    '>': 0.584, '?': 0.556, '@': 1.015, 'A': 0.667, 'B': 0.667, 'C': 0.722,
    'D': 0.722, 'E': 0.667, 'F': 0.611, 'G': 0.778, 'H': 0.722, 'I': 0.278,
    'J': 0.500, 'K': 0.667, 'L': 0.556, 'M': 0.833, 'N': 0.722, 'O': 0.778,
    'P': 0.667, 'Q': 0.778, 'R': 0.722, 'S': 0.667, 'T': 0.611, 'U': 0.722,
    'V': 0.667, 'W': 0.944, 'X': 0.667, 'Y': 0.667, 'Z': 0.611,
    'a': 0.556, 'b': 0.556, 'c': 0.500, 'd': 0.556, 'e': 0.556, 'f': 0.278,
    'g': 0.556, 'h': 0.556, 'i': 0.222, 'j': 0.222, 'k': 0.500, 'l': 0.222,
    'm': 0.833, 'n': 0.556, 'o': 0.556, 'p': 0.556, 'q': 0.556, 'r': 0.333,
    's': 0.500, 't': 0.278, 'u': 0.556, 'v': 0.500, 'w': 0.722, 'x': 0.500,
    'y': 0.500, 'z': 0.500,
}


def _text_width(text, font_size):
    """Approximate rendered width of text in Helvetica at the given font size."""
    return sum(_HELV_WIDTHS.get(ch, 0.5) for ch in text) * font_size


def _make_text_ap(pdf, field, text):
    """Build an explicit /AP /N appearance stream for a filled text field.

    This avoids the unreliable NeedAppearances regeneration. The stream uses
    the field's /DA (font/color), /Rect (dimensions), and /Q (alignment).
    """
    rect = field.get('/Rect', [0, 0, 100, 20])
    x1, y1, x2, y2 = [float(v) for v in rect]
    w = abs(x2 - x1)
    h = abs(y2 - y1)

    # Resolve /DA — may be on /Parent for widget annotations
    da = str(field.get('/DA', ''))
    if not da and '/Parent' in field:
        da = str(field['/Parent'].get('/DA', ''))
    if not da:
        da = '/Helv 0 Tf 0 g'

    font_name, font_size, color_ops = _parse_da(da)

    # Auto-size (0 Tf): pick a size that fits the field height
    if font_size < 0.1:
        font_size = max(6.0, min(h * 0.65, 14.0))

    # Alignment: /Q  0=left, 1=center, 2=right
    q = int(field.get('/Q', 0))
    if not q and '/Parent' in field:
        q = int(field['/Parent'].get('/Q', 0))

    text_w = _text_width(text, font_size)
    pad = 2.0

    if q == 2:      # right
        tx = max(pad, w - text_w - pad)
    elif q == 1:    # center
        tx = max(pad, (w - text_w) / 2.0)
    else:           # left
        tx = pad

    # Vertical centre — baseline offset accounts for descender (~20% of em)
    ty = (h - font_size) / 2.0 + font_size * 0.20
    if ty < 1.0:
        ty = 1.0

    esc = text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')

    content = (
        f"/Tx BMC\n"
        f"q\n"
        f"BT\n"
        f"/{font_name} {font_size:.2f} Tf\n"
        f"{color_ops}\n"
        f"{tx:.2f} {ty:.2f} Td\n"
        f"({esc}) Tj\n"
        f"ET\n"
        f"Q\n"
        f"EMC"
    ).encode('latin-1')

    stm = pikepdf.Stream(pdf, content)
    stm['/BBox'] = pikepdf.Array([0, 0, w, h])
    stm['/Subtype'] = pikepdf.Name('/Form')
    stm['/Type'] = pikepdf.Name('/XObject')

    # Font resource — pull the reference from AcroForm/DR
    font_ref = pikepdf.Dictionary()
    dr = pdf.Root.get('/AcroForm', pikepdf.Dictionary()).get('/DR', None)
    if dr and '/Font' in dr:
        fkey = pikepdf.Name(f'/{font_name}')
        if fkey in dr['/Font']:
            font_ref[fkey] = dr['/Font'][fkey]
    stm['/Resources'] = pikepdf.Dictionary({'/Font': font_ref})

    return stm


# ============ IMAGE UPLOAD SUPPORT (v1.0.3) ============
#
# PDF image-upload buttons (Adobe convention: field name ending '_af_image',
# or a /AA or /A action containing buttonImportIcon JS) are filled by:
#   1) embedding the source PNG as an Image XObject,
#   2) building a Form XObject appearance stream that draws the image
#      uniformly-scaled and centered inside the widget /Rect,
#   3) writing /AP/N = <formXObject> on the widget and /MK/I = <imageXObject>
#      for viewers that read the icon from /MK.
#
# Signature fields (/FT /Sig) are intentionally left untouched — Acrobat's
# special handling of Sig fields (Sign Here overlay, AP regeneration from
# empty /V) makes visual-only fill unreliable. Filling them properly
# requires real cryptographic signing which is out of scope.
try:
    from PIL import Image as _PIL_Image
    import io as _io
except Exception:
    _PIL_Image = None

# Per-pdf cache of embedded Image XObjects keyed by absolute path. Avoids
# re-embedding the same PNG for every field (DTE-style PDFs may have 50+
# image-upload fields referencing the same logo).
_image_xobject_cache = {}


def _reset_image_cache():
    """Call at the start of each fill_pdf run so cached XObjects don't leak
    between invocations (same process filling multiple PDFs)."""
    global _image_xobject_cache
    _image_xobject_cache = {}


def _embed_image_as_xobject(pdf, image_path):
    """Load an image from disk, embed into pdf as a JPEG Image XObject.
    Returns (xobject_stream, width_px, height_px). Caches per path.
    """
    if image_path in _image_xobject_cache:
        return _image_xobject_cache[image_path]

    if _PIL_Image is None:
        raise RuntimeError(
            "PIL/Pillow is required for image-upload and signature fill"
        )

    img = _PIL_Image.open(image_path)
    # Flatten transparency onto white so JPEG/DCTDecode works (no alpha
    # channel in JPEG). Keep RGB only.
    if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
        bg = _PIL_Image.new('RGB', img.size, (255, 255, 255))
        bg.paste(img, mask=img.convert('RGBA').split()[-1])
        img = bg
    elif img.mode != 'RGB':
        img = img.convert('RGB')

    buf = _io.BytesIO()
    img.save(buf, format='JPEG', quality=88, optimize=True)
    jpeg_bytes = buf.getvalue()
    w, h = img.size

    stm = pikepdf.Stream(pdf, jpeg_bytes)
    stm['/Type'] = pikepdf.Name('/XObject')
    stm['/Subtype'] = pikepdf.Name('/Image')
    stm['/Width'] = w
    stm['/Height'] = h
    stm['/ColorSpace'] = pikepdf.Name('/DeviceRGB')
    stm['/BitsPerComponent'] = 8
    stm['/Filter'] = pikepdf.Name('/DCTDecode')

    _image_xobject_cache[image_path] = (stm, w, h)
    return stm, w, h


def _make_image_button_ap(pdf, image_xobj, img_w_px, img_h_px, rect_w, rect_h):
    """Build a Form XObject appearance that draws `image_xobj` uniformly
    scaled (preserving aspect) and centered inside (0,0)-(rect_w,rect_h).
    """
    if img_w_px <= 0 or img_h_px <= 0 or rect_w <= 0 or rect_h <= 0:
        draw_w, draw_h = rect_w, rect_h
    else:
        s = min(rect_w / img_w_px, rect_h / img_h_px)
        draw_w = img_w_px * s
        draw_h = img_h_px * s

    tx = (rect_w - draw_w) / 2.0
    ty = (rect_h - draw_h) / 2.0

    # Image XObject draws in a 1x1 unit square, so the cm matrix scales it
    # to the desired pixel footprint and offsets to the centering origin.
    content = (
        f"q\n"
        f"{draw_w:.4f} 0 0 {draw_h:.4f} {tx:.4f} {ty:.4f} cm\n"
        f"/ImgTF Do\n"
        f"Q"
    ).encode('latin-1')

    form = pikepdf.Stream(pdf, content)
    form['/Type'] = pikepdf.Name('/XObject')
    form['/Subtype'] = pikepdf.Name('/Form')
    form['/BBox'] = pikepdf.Array([0, 0, rect_w, rect_h])
    form['/Resources'] = pikepdf.Dictionary({
        '/XObject': pikepdf.Dictionary({'/ImgTF': image_xobj}),
        '/ProcSet': pikepdf.Array([pikepdf.Name('/PDF'), pikepdf.Name('/ImageC')]),
    })
    return form


def _extract_js_from_actions(action_obj):
    """Walk an /A or /AA subtree and concatenate every /JS string found.
    Used by _is_image_upload_field to detect buttonImportIcon triggers.
    """
    out = []
    try:
        if isinstance(action_obj, pikepdf.Dictionary):
            js = action_obj.get('/JS')
            if js is not None:
                out.append(str(js))
            for k in action_obj.keys():
                v = action_obj[k]
                if isinstance(v, (pikepdf.Dictionary, pikepdf.Array)):
                    out.append(_extract_js_from_actions(v))
        elif isinstance(action_obj, pikepdf.Array):
            for v in action_obj:
                if isinstance(v, (pikepdf.Dictionary, pikepdf.Array)):
                    out.append(_extract_js_from_actions(v))
    except Exception:
        pass
    return ' '.join(out)


def _is_image_upload_field(field, name):
    """Detect image-upload pushbutton fields across naming conventions:
      - field name contains '_af_image' (Adobe Acrobat convention), OR
      - /AA or /A contains 'buttonImportIcon' JS, OR
      - Ff bit 17 (pushbutton, 0x10000) + buttonImportIcon JS.
    Pushbutton alone (no JS) is ambiguous (could be a nav button) — skip.
    """
    if name and '_af_image' in name.lower():
        return True
    for aa_key in ('/AA', '/A'):
        a = field.get(aa_key)
        if a is None:
            continue
        try:
            if 'buttonImportIcon' in _extract_js_from_actions(a):
                return True
        except Exception:
            pass
    return False


def _fill_image_upload_field(pdf, field, logo_paths):
    """Fill an image-upload pushbutton by embedding a random logo and
    writing /AP/N on the field (and each /Kids widget). Sets /MK/I for
    viewers that read icons from /MK. Returns True on success.
    """
    if not logo_paths:
        return False
    logo_path = random.choice(logo_paths)
    img_xobj, img_w_px, img_h_px = _embed_image_as_xobject(pdf, logo_path)

    def _apply(widget):
        w, h = get_field_rect(widget)
        form_xobj = _make_image_button_ap(pdf, img_xobj, img_w_px, img_h_px, w, h)
        widget['/AP'] = pikepdf.Dictionary({'/N': form_xobj})
        mk = widget.get('/MK', pikepdf.Dictionary())
        if not isinstance(mk, pikepdf.Dictionary):
            mk = pikepdf.Dictionary()
        mk['/I'] = img_xobj
        mk['/TP'] = 1  # TextPosition 1 = icon only, no caption
        widget['/MK'] = mk

    _apply(field)
    if '/Kids' in field:
        for kid in field['/Kids']:
            _apply(kid)
    return True


def _make_comb_ap(pdf, field, text, max_len, font_name, font_size, color_ops):
    """Build an /AP/N stream for a comb text field (evenly-spaced character cells)."""
    rect = field.get('/Rect', [0, 0, 100, 20])
    x1, y1, x2, y2 = [float(v) for v in rect]
    w = abs(x2 - x1)
    h = abs(y2 - y1)
    cell_w = w / max(max_len, 1)

    lines = ["/Tx BMC", "q", "BT", f"/{font_name} {font_size:.2f} Tf", color_ops]
    ty = (h - font_size) / 2.0 + font_size * 0.20
    if ty < 1.0:
        ty = 1.0

    for i, ch in enumerate(text[:max_len]):
        ch_w = _HELV_WIDTHS.get(ch, 0.5) * font_size
        cx = cell_w * i + (cell_w - ch_w) / 2.0
        esc = ch.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
        lines.append(f"{cx:.2f} {ty:.2f} Td")
        lines.append(f"({esc}) Tj")
        lines.append(f"{-cx:.2f} {-ty:.2f} Td")

    lines += ["ET", "Q", "EMC"]
    content = "\n".join(lines).encode('latin-1')

    stm = pikepdf.Stream(pdf, content)
    stm['/BBox'] = pikepdf.Array([0, 0, w, h])
    stm['/Subtype'] = pikepdf.Name('/Form')
    stm['/Type'] = pikepdf.Name('/XObject')

    font_ref = pikepdf.Dictionary()
    dr = pdf.Root.get('/AcroForm', pikepdf.Dictionary()).get('/DR', None)
    if dr and '/Font' in dr:
        fkey = pikepdf.Name(f'/{font_name}')
        if fkey in dr['/Font']:
            font_ref[fkey] = dr['/Font'][fkey]
    stm['/Resources'] = pikepdf.Dictionary({'/Font': font_ref})
    return stm


def get_text_value(field_name: str, field=None, settings: FillSettings = None, is_calc_source: bool = False, calc_source_scale: str = 'small', max_len: int = 0) -> str:
    name_lower = field_name.lower()

    # Calculation source fields get small numbers for easy verification
    # But fields feeding into formulas with large divisors (like /1000) need
    # larger values so the result is visible after division
    if is_calc_source:
        if calc_source_scale == 'high':
            return str(random.randint(150, 200))
        elif calc_source_scale == 'low':
            return str(random.randint(50, 100))
        elif calc_source_scale == 'large':
            return str(random.randint(100, 200))
        return str(random.randint(1, 2))

    # If max_len is very short (1-3), generate smarter short values
    if max_len > 0 and max_len <= 3:
        # For state-like fields with 2-char limit, use state abbreviations
        if 'state' in name_lower and max_len == 2:
            return random.choice(SAMPLE_DATA['states'])
        # For very short fields, generate a short numeric or alpha value
        if max_len == 1:
            return random.choice(['Y', 'N', 'X', '1', '0'])
        if max_len == 2:
            return random.choice(['NY', 'CA', 'TX', 'MI', 'OH', 'FL', '42', 'OK'])
        if max_len == 3:
            return random.choice(['Yes', '100', 'USA', 'ABC'])

    # Use global settings if provided
    date_fmt_str = settings.date_format if settings else '%m/%d/%Y'
    # Python strftime tokens: mm -> %m, dd -> %d, yyyy -> %Y
    py_date_fmt = date_fmt_str.replace('mm', '%m').replace('dd', '%d').replace('yyyy', '%Y')

    if field is not None:
        format_info = detect_field_format_from_js(field)
        if format_info['type']:
            fmt_type = format_info['type']
            if fmt_type == 'currency':
                # Store RAW numeric value — the PDF's /AA/F format action
                # (e.g., AFNumber_Format) will add the "$" symbol when displayed.
                decimals = format_info.get('decimals', 2)
                return f"{random.randint(100, 9999)}.{random.randint(0, 10**decimals - 1):0{decimals}d}"
            elif fmt_type == 'number':
                decimals = format_info.get('decimals', 2)
                if decimals == 0: return str(random.randint(10, 999))
                return f"{random.randint(10, 999)}.{random.randint(0, 10**decimals - 1):0{decimals}d}"
            elif fmt_type == 'date':
                date_val = datetime.now() + timedelta(days=random.randint(0, 365))
                return date_val.strftime(py_date_fmt)
            elif fmt_type == 'phone': return random.choice(SAMPLE_DATA['phones'])
            elif fmt_type == 'zip' or fmt_type == 'zip5': return random.choice(SAMPLE_DATA['zips'])
            elif fmt_type == 'percentage': return random.choice(SAMPLE_DATA['percentages'])
            elif fmt_type == 'ssn': return f"{random.randint(100,999):03d}-{random.randint(10,99):02d}-{random.randint(1000,9999):04d}"

    if is_numeric_field_name(field_name): return str(random.randint(10, 500))
    is_numeric = any(p in name_lower for p in NUMERIC_PATTERNS) or '$' in field_name
    if is_numeric:
        if 'year' in name_lower: return random.choice(['2024', '2025', '2026'])
        return str(random.randint(10, 1000))

    if 'state' in name_lower: return random.choice(SAMPLE_DATA['states'])
    elif 'zip' in name_lower: return random.choice(SAMPLE_DATA['zips'])
    elif 'phone' in name_lower or 'tel' in name_lower: return random.choice(SAMPLE_DATA['phones'])
    elif 'date' in name_lower:
        date_val = datetime.now() + timedelta(days=random.randint(0, 365))
        return date_val.strftime(py_date_fmt)
    elif 'email' in name_lower: return random.choice(SAMPLE_DATA['emails'])
    elif 'name' in name_lower: return random.choice(SAMPLE_DATA['names'])
    return random.choice(['Sample', 'Test', 'Example'])

def is_comb_field(field) -> bool:
    """Check if field has Comb flag (bit 25) set."""
    try:
        ff = int(field.get('/Ff', 0))
        return bool(ff & 0x1000000)
    except:
        return False

# ============ CALCULATION ENGINE ============

def get_calc_js(field):
    """Extract JavaScript code from a calculated field's /AA/C action."""
    if '/AA' not in field or '/C' not in field['/AA']:
        return None
    c_action = field['/AA']['/C']
    if '/JS' not in c_action:
        return None
    js_obj = c_action['/JS']
    if isinstance(js_obj, pikepdf.Stream):
        return js_obj.read_bytes().decode('latin-1')
    return str(js_obj)

def evaluate_calc_expression(js_code, field_values):
    """Evaluate a calculation JS expression using known field values.
    Returns the computed numeric value, or 0 if evaluation fails.

    Handles multiple JS generation patterns:
    - AFSimple_Calculate("SUM", new Array("f1", "f2"))   (Acrobat built-ins)
    - AFMakeNumber(getField("f").value)                   (original PDF pattern)
    - Number(this.getField("f").value) || 0               (editor inline pattern)
    - var v0 = Number(this.getField("f").value) || 0;
      event.value = v0 * v1;                              (editor variable pattern)
    - var A = Number(this.getField("f").value) || 0;
      event.value = (((A - B)) * C) / 1000;              (editor CUSTOM pattern)
    """
    try:
        # Handle conditional min pattern:
        # if (v2 <= (v1 / 2)) event.value = v2; else event.value = (v1 / 2);
        if 'if' in js_code and '<=' in js_code:
            refs = re.findall(r'(?:this\.)?getField\s*\(\s*"([^"]+)"\s*\)', js_code)
            if len(refs) >= 2:
                div_match = re.search(r'/\s*(\d+\.?\d*)\s*\)', js_code)
                divisor = float(div_match.group(1)) if div_match else 2
                v1 = float(field_values.get(refs[0], 0))
                v2 = float(field_values.get(refs[1], 0))
                return min(v2, v1 / divisor) if divisor != 0 else 0

        # --- AFSimple_Calculate built-in functions ---
        af_match = re.search(
            r'AFSimple_Calculate\s*\(\s*["\'](\w+)["\']\s*,\s*(?:new\s+Array\s*\()?([^)]+)\)',
            js_code, re.IGNORECASE
        )
        if af_match:
            af_type = af_match.group(1).upper()
            field_names = re.findall(r'["\']([^"\']+)["\']', af_match.group(2))
            vals = [float(field_values.get(fn, 0)) for fn in field_names]
            if not vals:
                return 0
            if af_type == 'SUM':
                return round(sum(vals), 2)
            elif af_type == 'PRD':
                r = 1
                for v in vals: r *= v
                return round(r, 2)
            elif af_type == 'AVG':
                return round(sum(vals) / len(vals), 2) if vals else 0
            elif af_type == 'MIN':
                return round(min(vals), 2)
            elif af_type == 'MAX':
                return round(max(vals), 2)
            return 0

        # --- Build variable→value map from var declarations ---
        # Matches: var v0 = Number(this.getField("name").value) || 0;
        #      or: var A  = Number(this.getField("name").value) || 0;
        var_map = {}
        for vm in re.finditer(
            r'var\s+(\w+)\s*=\s*(?:Number\s*\(\s*)?(?:this\.)?getField\s*\(\s*"([^"]+)"\s*\)\s*\.value\s*\)?\s*(?:\|\|\s*0)?\s*;',
            js_code
        ):
            var_name = vm.group(1)
            field_name = vm.group(2)
            var_map[var_name] = float(field_values.get(field_name, 0))

        # --- Extract the event.value = EXPRESSION part ---
        ev_match = re.search(r'event\.value\s*=\s*(.+?)(?:;|\s*$)', js_code)
        if not ev_match:
            return 0

        expr = ev_match.group(1).strip()

        # --- Replace all inline field-reference patterns with numeric values ---
        def replace_field_ref(match):
            field_name = match.group(1)
            return str(float(field_values.get(field_name, 0)))

        # Pattern 1: AFMakeNumber(getField("FIELD").value)
        expr = re.sub(
            r'AFMakeNumber\s*\(\s*(?:this\.)?getField\s*\(\s*"([^"]+)"\s*\)\s*\.value\s*\)',
            replace_field_ref, expr
        )
        # Pattern 2: Number(this.getField("FIELD").value) || 0  (with surrounding parens)
        expr = re.sub(
            r'\(?\s*Number\s*\(\s*(?:this\.)?getField\s*\(\s*"([^"]+)"\s*\)\s*\.value\s*\)\s*(?:\|\|\s*0)\s*\)?',
            replace_field_ref, expr
        )
        # Pattern 3: bare getField("FIELD").value
        expr = re.sub(
            r'(?:this\.)?getField\s*\(\s*"([^"]+)"\s*\)\s*\.value',
            replace_field_ref, expr
        )

        # --- Substitute variable references (v0, v1, A, B, C...) ---
        if var_map:
            # Sort by name length descending to avoid partial replacements
            for vname in sorted(var_map.keys(), key=len, reverse=True):
                expr = re.sub(r'\b' + re.escape(vname) + r'\b', str(var_map[vname]), expr)

        # --- Clean up any remaining || 0 fallbacks ---
        expr = re.sub(r'\|\|\s*0', '', expr)

        # Validate expression — only allow safe characters (digits, operators, parens, spaces, dots)
        safe_expr = expr.strip()
        if re.match(r'^[\d\.\+\-\*\/\(\)\s]+$', safe_expr):
            result = eval(safe_expr)
            return round(float(result), 2)

        return 0
    except Exception:
        return 0

def topological_sort_calcs(calc_info):
    """Sort calculated fields so dependencies are computed first.
    calc_info: {field_name: {'js': str, 'references': [str], ...}}
    Returns list of field names in dependency-resolved computation order."""
    calc_names = set(calc_info.keys())

    # Kahn's algorithm for topological sort
    in_degree = {name: 0 for name in calc_names}
    graph = {name: [] for name in calc_names}

    for name, info in calc_info.items():
        for ref in info['references']:
            if ref in calc_names:
                graph[ref].append(name)
                in_degree[name] += 1

    queue = [name for name, deg in in_degree.items() if deg == 0]
    sorted_list = []

    while queue:
        node = queue.pop(0)
        sorted_list.append(node)
        for neighbor in graph[node]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    # Append any remaining (circular deps — shouldn't happen but be safe)
    for name in calc_names:
        if name not in sorted_list:
            sorted_list.append(name)

    return sorted_list

def fill_pdf(input_path: str, output_path: str = None, settings_json: str = None, assets_dir: str = None):
    try:
        fill_settings = FillSettings(settings_json)
        _reset_image_cache()

        if output_path is None:
            output_path = f"{os.path.splitext(input_path)[0]}_FILLED.pdf"

        # Resolve test-fill assets (logos for image-upload fields).
        # Preference order:
        #   1) --assets-dir CLI arg (main.js passes this from the Electron
        #      app's resourcesPath in packaged builds, or __dirname/assets
        #      /test_fill in dev).
        #   2) $FILLTHATPDF_ASSETS_DIR env var.
        #   3) <script_dir>/../assets/test_fill (dev fallback).
        # If no assets are found, image-upload fields are skipped
        # gracefully — regular fields still fill.
        logo_paths = []
        candidates = []
        if assets_dir:
            candidates.append(assets_dir)
        env_dir = os.environ.get('FILLTHATPDF_ASSETS_DIR')
        if env_dir:
            candidates.append(env_dir)
        script_dir = os.path.dirname(os.path.abspath(__file__))
        candidates.append(os.path.join(script_dir, '..', 'assets', 'test_fill'))

        resolved_assets_dir = None
        for cand in candidates:
            if cand and os.path.isdir(cand):
                resolved_assets_dir = cand
                break

        if resolved_assets_dir:
            for fname in ('logo1.png', 'logo2.png'):
                p = os.path.join(resolved_assets_dir, fname)
                if os.path.exists(p):
                    logo_paths.append(p)
            print(f"Test-fill assets: {resolved_assets_dir} (logos={len(logo_paths)})")
        else:
            print("[WARNING] No test-fill assets dir found — image-upload fields will be skipped")

        print(f"Opening: {input_path}")
        pdf = pikepdf.open(input_path, allow_overwriting_input=True)
        
        if '/AcroForm' in pdf.Root and '/XFA' in pdf.Root.AcroForm:
            del pdf.Root.AcroForm['/XFA']

        if '/AcroForm' not in pdf.Root:
            print("[ERROR] No form fields found")
            return
        
        acroform = pdf.Root.AcroForm
        # We generate explicit /AP streams for every filled field, so
        # NeedAppearances is no longer needed. In fact, setting it to True
        # causes some viewers (Acrobat) to IGNORE the explicit AP and
        # attempt its own regeneration, which fails for certain field
        # structures (e.g., merged field+widget calc fields).
        if '/NeedAppearances' in acroform:
            del acroform['/NeedAppearances']

        def get_all_terminal_fields(fields_list):
            terminal_fields = []
            for f in fields_list:
                # A terminal field either has /FT or has no /Kids
                # If it has /Kids but no /FT, it's a non-terminal field (a group)
                if '/FT' in f:
                    terminal_fields.append(f)
                elif '/Kids' in f:
                    terminal_fields.extend(get_all_terminal_fields(f['/Kids']))
                else:
                    # No /FT and no /Kids? Might be an orphan widget or a terminal field 
                    # without type (unlikely but we'll include it to be safe)
                    terminal_fields.append(f)
            return terminal_fields

        all_terminal_fields = get_all_terminal_fields(acroform.get('/Fields', []))
        counts = {'text': 0, 'radio': 0, 'checkbox': 0, 'choice': 0, 'numeric': 0, 'image': 0, 'image_skipped': 0}

        # ---- PASS 1: Analyze fields for calculations ----
        calc_fields = {}       # name -> {'field': obj, 'js': str, 'references': [...]}
        source_field_names = set()  # Fields referenced by calculations

        for field in all_terminal_fields:
            ft = str(field.get('/FT', ''))
            name = str(field.get('/T', ''))
            # If name is missing, try to get from parent
            if not name and '/Parent' in field:
                name = str(field['/Parent'].get('/T', ''))
            
            if '/Tx' not in ft:
                continue
            js = get_calc_js(field)
            if js:
                # Extract field references from getField("name") calls
                refs = re.findall(r'(?:this\.)?getField\s*\(\s*"([^"]+)"\s*\)', js)
                # Also extract refs from AFSimple_Calculate("OP", new Array("f1","f2"))
                if not refs:
                    af_match = re.search(
                        r'AFSimple_Calculate\s*\(\s*["\'](\w+)["\']\s*,\s*(?:new\s+Array\s*\()?([^)]+)\)',
                        js, re.IGNORECASE
                    )
                    if af_match:
                        refs = re.findall(r'["\']([^"\']+)["\']', af_match.group(2))
                # Deduplicate while preserving order
                seen = set()
                unique = []
                for r in refs:
                    if r not in seen:
                        seen.add(r)
                        unique.append(r)
                refs = unique
                calc_fields[name] = {'field': field, 'js': js, 'references': refs}
                source_field_names.update(refs)

        # Pure source fields: referenced by calcs but not themselves calculated
        calc_source_names = source_field_names - set(calc_fields.keys())

        # Detect formulas with large divisors (e.g., /1000) and subtraction patterns.
        # Assign scale roles so results are visible and mathematically correct:
        #   'high'  = minuend in subtraction (e.g., existing watts) → 150-200
        #   'low'   = subtrahend (e.g., replacement watts) → 50-100, ensures positive difference
        #   'large' = multiplier needed to overcome large divisor (e.g., hours) → 100-200
        #   'small' = default for all other source fields (e.g., # of fixtures) → 1-2
        source_scale_overrides = {}  # field_name -> 'high'|'low'|'large'

        for calc_name, info in calc_fields.items():
            js = info['js']
            refs = info['references']

            # Check for division by large constant (>= 100)
            has_large_div = False
            div_matches = re.findall(r'/\s*(\d+\.?\d*)', js)
            for div_str in div_matches:
                try:
                    if float(div_str) >= 100:
                        has_large_div = True
                        break
                except ValueError:
                    pass

            if not has_large_div:
                continue

            # Check for subtraction pattern: A - B in the formula
            # Handles both old AFMakeNumber pattern and new variable-based pattern
            sub_match = re.search(
                r'getField\s*\(\s*"([^"]+)"\s*\)\.value\s*\)?\s*(?:\|\|\s*0)?\s*\)?\s*-\s*'
                r'(?:AFMakeNumber\s*\(\s*|(?:\(?\s*Number\s*\(\s*)?)?(?:this\.)?getField\s*\(\s*"([^"]+)"',
                js
            )
            # Also detect subtraction in variable-based formulas: A - B
            if not sub_match and len(refs) >= 2 and ' - ' in js:
                # Look for event.value = ... pattern with subtraction
                ev_part = re.search(r'event\.value\s*=\s*(.+?)(?:;|\s*$)', js)
                if ev_part and ' - ' in ev_part.group(1):
                    # Map variables back to field refs
                    var_field = {}
                    for vm in re.finditer(r'var\s+(\w+)\s*=\s*(?:Number\s*\(\s*)?(?:this\.)?getField\s*\(\s*"([^"]+)"', js):
                        var_field[vm.group(1)] = vm.group(2)
                    minus_match = re.search(r'(\w+)\s*-\s*(\w+)', ev_part.group(1))
                    if minus_match and minus_match.group(1) in var_field and minus_match.group(2) in var_field:
                        sub_match = type('Match', (), {
                            'group': lambda self, n: [None, var_field[minus_match.group(1)], var_field[minus_match.group(2)]][n]
                        })()

            if sub_match:
                minuend = sub_match.group(1)
                subtrahend = sub_match.group(2)

                # Subtraction pair: ensure minuend > subtrahend for positive results
                if minuend not in calc_fields:
                    source_scale_overrides[minuend] = 'high'
                if subtrahend not in calc_fields:
                    source_scale_overrides[subtrahend] = 'low'

                # Remaining refs are multipliers
                other_refs = [r for r in refs if r != minuend and r != subtrahend and r not in calc_fields]
                if len(other_refs) >= 2:
                    # Last multiplier (e.g., hours) → large; first (e.g., fixtures) → stays small
                    source_scale_overrides[other_refs[-1]] = 'large'
                elif len(other_refs) == 1:
                    source_scale_overrides[other_refs[0]] = 'large'
            else:
                # Large divisor but no subtraction — give all source fields large values
                for ref in refs:
                    if ref not in calc_fields and ref not in source_scale_overrides:
                        source_scale_overrides[ref] = 'large'

        if calc_fields:
            print(f"Found {len(calc_fields)} calculated fields, {len(calc_source_names)} source fields")
            if source_scale_overrides:
                print(f"   {len(source_scale_overrides)} source fields with scaled values (formulas with large divisors)")

        # ---- PASS 2: Fill regular fields + source fields ----
        filled_values = {}  # name -> numeric value (for calculation engine)

        for field in all_terminal_fields:
            try:
                ft = str(field.get('/FT', ''))
                name = str(field.get('/T', ''))
                if not name and '/Parent' in field:
                    name = str(field['/Parent'].get('/T', ''))

                if '/Tx' in ft:
                    # Skip calculated fields — handled in pass 3
                    if name in calc_fields:
                        counts['text'] += 1
                        continue

                    # Skip read-only fields UNLESS they are calculation source fields
                    ff_val = int(field.get('/Ff', 0))
                    if (ff_val & 1) and name not in calc_source_names:
                        counts['text'] += 1
                        continue

                    # Read /MaxLen before generating value so we can pick smarter data
                    max_len_all = int(field.get('/MaxLen', 0))
                    if not max_len_all and '/Parent' in field:
                        max_len_all = int(field['/Parent'].get('/MaxLen', 0))

                    is_source = name in calc_source_names
                    scale = source_scale_overrides.get(name, 'small')
                    value = get_text_value(name, field, fill_settings, is_calc_source=is_source, calc_source_scale=scale, max_len=max_len_all)
                    if max_len_all > 0 and len(value) > max_len_all:
                        value = value[:max_len_all]

                    # Track numeric value for calculation engine BEFORE
                    # pre-formatting rewrites `value` into a display string.
                    try:
                        filled_values[name] = float(value.replace('$', '').replace(',', ''))
                    except ValueError:
                        filled_values[name] = 0

                    # v1.0.2: Pre-format numeric fields whose /AA/F contains
                    # AFNumber_Format (e.g., currency sources). Strips /AA/F
                    # so viewers draw /V as-is via NeedAppearances regen.
                    # Skip comb fields — they handle layout themselves below.
                    if not is_comb_field(field):
                        value = preformat_field_value(field, value)

                    field['/V'] = pikepdf.String(value)
                    counts['text'] += 1

                    # For source fields with /Kids, also set /V and AP on kid widgets
                    if is_source and '/Kids' in field:
                        for kid in field['/Kids']:
                            kid['/V'] = pikepdf.String(value)
                            kid_ap = _make_text_ap(pdf, kid, value)
                            kid['/AP'] = pikepdf.Dictionary({'/N': kid_ap})

                    # PRESERVE /AA actions (format, keystroke, etc.) so the PDF
                    # viewer can apply them (e.g., AFNumber_Format adds "$" symbol).
                    # Only strip /AA/C (calculate) if we're regenerating calculations,
                    # otherwise keep everything intact.

                    is_comb = is_comb_field(field)
                    max_len = int(field.get('/MaxLen', 0)) if is_comb else 0
                    
                    if is_comb and max_len > 0:
                        # Generate numeric value for comb fields (often account numbers)
                        val_len = min(max_len, random.randint(min(5, max_len), max_len))
                        value = "".join([str(random.randint(0, 9)) for _ in range(val_len)])
                        field['/V'] = pikepdf.String(value)
                        
                        # Ensure Comb flag is set (Bit 25)
                        current_ff = int(field.get('/Ff', 0))
                        field['/Ff'] = current_ff | 0x1000000
                        
                        # Set Default Appearance (DA) with Font and Color
                        # Acrobat needs this to generate the appearance
                        width, height = get_field_rect(field)
                        
                        # Use a large enough font size, or auto (0)
                        # For comb fields, auto-size often fails, so we calc a heuristic
                        cell_w = width / max_len
                        font_size_calc = min(height * 0.7, cell_w * 0.8)
                        
                        # Preserve original font color for comb fields too
                        comb_fg = "0 g"
                        comb_font = "Helv"
                        comb_da = str(field.get('/DA', ''))
                        if not comb_da and '/Parent' in field:
                            comb_da = str(field['/Parent'].get('/DA', ''))
                        if comb_da:
                            cm = re.search(r'([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+rg', comb_da)
                            if cm:
                                comb_fg = f"{cm.group(1)} {cm.group(2)} {cm.group(3)} rg"
                            fm = re.search(r'/(\w+)\s+[\d.]+\s+Tf', comb_da)
                            if fm:
                                comb_font = fm.group(1)
                        field['/DA'] = pikepdf.String(f"{comb_fg} /{comb_font} {font_size_calc:.1f} Tf")

                        # Preserve transparent comb fields — only add BG if originally present
                        # Empty array [] means transparent in PDF spec.
                        comb_has_bg = False
                        comb_mk = field.get('/MK', None)
                        if not comb_mk and '/Parent' in field:
                            comb_mk = field['/Parent'].get('/MK', None)
                        if comb_mk and '/BG' in comb_mk:
                            if len(list(comb_mk['/BG'])) > 0:
                                comb_has_bg = True
                        if comb_has_bg:
                            if '/MK' not in field: field['/MK'] = pikepdf.Dictionary()
                            field['/MK']['/BG'] = pikepdf.Array([0.7, 0.85, 1])

                        # Generate comb-specific appearance stream
                        comb_ap = _make_comb_ap(pdf, field, value, max_len,
                                                comb_font, font_size_calc, comb_fg)
                        field['/AP'] = pikepdf.Dictionary({'/N': comb_ap})
                        continue

                    # ---- Generate explicit appearance stream ----
                    # NeedAppearances=True is unreliable across viewers
                    # (macOS Preview, some Acrobat versions skip regeneration
                    # for certain fields). Instead, build /AP/N ourselves so
                    # the text is always rendered on open.
                    ap_stream = _make_text_ap(pdf, field, value)
                    field['/AP'] = pikepdf.Dictionary({
                        '/N': ap_stream,
                    })

                elif '/Btn' in ft:
                    if '/Kids' in field:
                        # Ensure this is treated as a Radio Group
                        current_ff = int(field.get('/Ff', 0))
                        # Set Bit 16 (Radio) and Bit 15 (NoToggleToOff)
                        # 0x8000 = 32768, 0x4000 = 16384
                        field['/Ff'] = current_ff | 32768 | 16384
                        
                        kids = list(field['/Kids'])
                        selected_idx = random.randint(0, len(kids)-1)
                        
                        # Determine correct "On" state name for the selected option
                        # If AP missing, we MUST assign unique names to ensure mutual exclusivity
                        selected_on_state = None
                        
                        for idx, kid in enumerate(kids):
                            # 1. Determine the "On" state name for this kid
                            k_on = None
                            if '/AP' in kid and '/N' in kid['/AP']:
                                for k in kid['/AP']['/N'].keys():
                                    if str(k) not in ['/Off', 'Off']: 
                                        k_on = str(k)
                                        break
                            
                            # Fallback if no AP or no valid state found
                            if not k_on:
                                k_on = f"/Choice{idx+1}"
                            
                            # 2. Set Parent Value if this is the selected index
                            if idx == selected_idx:
                                field['/V'] = pikepdf.Name(k_on)
                                kid['/AS'] = pikepdf.Name(k_on)
                                selected_on_state = k_on
                            else:
                                kid['/AS'] = pikepdf.Name('/Off')
                                
                                kid['/AS'] = pikepdf.Name('/Off')
                                
                            # 3. Add Native Backup (MK) + Manual AP
                            if '/MK' not in kid: kid['/MK'] = pikepdf.Dictionary()
                            
                            # Determine CA char based on style
                            s = fill_settings.checkbox_style.lower()
                            if s == 'cross': ca_char = '8'
                            elif s == 'circle': ca_char = 'l'
                            elif s == 'square': ca_char = 'n'
                            elif s == 'diamond': ca_char = 'u'
                            elif s == 'star': ca_char = 'H'
                            else: ca_char = '4'
                            
                            kid['/MK']['/CA'] = pikepdf.String(ca_char)
                            kid['/DA'] = pikepdf.String("/ZaDb 0 Tf 0 g")
                            kid['/DA'] = pikepdf.String("/ZaDb 0 Tf 0 g")
                            
                            # CLEANUP: Remove /V from Kid if present (Parent holds V)
                            if '/V' in kid: del kid['/V']
                                
                            # 4. Generate Appearance Stream with the correct On state name
                            # Manual AP ensures visibility and correct state mapping
                            kw, kh = get_field_rect(kid)
                            kid['/AP'] = pikepdf.Dictionary({'/N': make_appearance_dict(pdf, k_on, kw, kh, fill_settings.checkbox_style)})
                        
                        counts['radio'] += 1
                    else:
                        # Image-upload pushbutton? Detect by field name
                        # (`_af_image`) or by buttonImportIcon JS in /A /AA.
                        if _is_image_upload_field(field, name):
                            try:
                                if _fill_image_upload_field(pdf, field, logo_paths):
                                    counts['image'] += 1
                                else:
                                    counts['image_skipped'] += 1
                            except Exception as e:
                                print(f"[WARNING] Image-upload fill failed for '{name}': {e}")
                                counts['image_skipped'] += 1
                            continue

                        # Checkbox
                        # Add Native Backup (MK)
                        if '/MK' not in field: field['/MK'] = pikepdf.Dictionary()
                        field['/MK']['/CA'] = pikepdf.String('4')
                        field['/DA'] = pikepdf.String("/ZaDb 0 Tf 0 g")

                        field['/V'] = pikepdf.Name('/Yes')
                        field['/AS'] = pikepdf.Name('/Yes')
                        
                        # Checkbox Manual AP
                        # Determine CA char based on style
                        if '/MK' not in field: field['/MK'] = pikepdf.Dictionary()
                        s = fill_settings.checkbox_style.lower()
                        if s == 'cross': ca_char = '8'
                        elif s == 'circle': ca_char = 'l'
                        elif s == 'square': ca_char = 'n'
                        elif s == 'diamond': ca_char = 'u'
                        elif s == 'star': ca_char = 'H'
                        else: ca_char = '4'

                        field['/MK']['/CA'] = pikepdf.String(ca_char)
                        field['/DA'] = pikepdf.String("/ZaDb 0 Tf 0 g")
                        field['/DA'] = pikepdf.String("/ZaDb 0 Tf 0 g")

                        w, h = get_field_rect(field)
                        field['/AP'] = pikepdf.Dictionary({'/N': make_appearance_dict(pdf, 'Yes', w, h, fill_settings.checkbox_style)})
                        counts['checkbox'] += 1
                
                elif '/Ch' in ft:
                    # Choice field (Dropdown or ListBox)
                    options = []
                    raw_opts = []
                    
                    # Check for Opt on field or parent
                    opt_source = field.get('/Opt')
                    if opt_source is None and '/Parent' in field:
                        opt_source = field['/Parent'].get('/Opt')
                        
                    if opt_source is not None:
                        try:
                            opt_items = list(opt_source)
                            for opt in opt_items:
                                raw_opts.append(opt)
                                if isinstance(opt, pikepdf.Array):
                                    # [export_value, display_value]
                                    options.append(str(opt[0]))
                                else:
                                    options.append(str(opt))
                        except Exception:
                            pass
                    
                    # Store indices of valid options: skip empty, whitespace-only,
                    # and placeholder options (index 0 starting with "Select",
                    # "Choose", "Pick", "-- ", etc.)
                    _PLACEHOLDER_PREFIXES = ('select', 'choose', 'pick', '--', '—', '- -', 'none')
                    valid_indices = []
                    for i, o in enumerate(options):
                        text = o.strip()
                        if not text:
                            continue
                        if i == 0 and any(text.lower().startswith(p) for p in _PLACEHOLDER_PREFIXES):
                            continue
                        valid_indices.append(i)
                    
                    if valid_indices:
                        selected_idx = random.choice(valid_indices)
                        selected_raw = raw_opts[selected_idx]
                        
                        # Set /V to the export value (first element if it's an array)
                        val = selected_raw[0] if isinstance(selected_raw, pikepdf.Array) else selected_raw
                        field['/V'] = val
                        field['/I'] = pikepdf.Array([selected_idx])
                        field['/DV'] = val

                        # Font Preservation: Extract font from original DA if possible
                        original_da = str(field.get('/DA', ''))
                        if not original_da and '/Parent' in field:
                            original_da = str(field['/Parent'].get('/DA', ''))
                        
                        if original_da:
                            field['/DA'] = pikepdf.String(original_da)
                        else:
                            field['/DA'] = pikepdf.String("/Helv 9 Tf 0 g")
                        
                        # Set values on parent if this is a widget
                        if '/Parent' in field:
                            field['/Parent']['/V'] = val
                            field['/Parent']['/I'] = field['/I']
                        
                        # Generate explicit appearance stream for the selected value
                        display_text = options[selected_idx] if selected_idx < len(options) else str(val)
                        ap_stream = _make_text_ap(pdf, field, display_text)
                        field['/AP'] = pikepdf.Dictionary({'/N': ap_stream})
                        counts['choice'] += 1
            except Exception as e:
                print(f"[WARNING] Error processing field '{name}': {e}")
                continue
        
        # ---- PASS 3: Compute calculated field values ----
        if calc_fields:
            sorted_calcs = topological_sort_calcs(calc_fields)
            computed_count = 0

            for calc_name in sorted_calcs:
                info = calc_fields[calc_name]
                computed = evaluate_calc_expression(info['js'], filled_values)
                filled_values[calc_name] = computed

                field = info['field']

                # v1.0.2: Remove /AA/C (calculation trigger) first so we
                # don't trigger unwanted recomputation in viewers.
                if '/AA' in field and '/C' in field['/AA']:
                    del field['/AA']['/C']
                    if len(list(field['/AA'].keys())) == 0:
                        del field['/AA']

                # v1.0.2: Pre-format the value using the field's own
                # AFNumber_Format (if any). This also strips /AA/F so the
                # viewer won't try to re-format. Passive NeedAppearances
                # regeneration then draws /V as plain text, using the
                # field's existing /Q (alignment) and /DA (font/color).
                value_str = preformat_field_value(field, computed)
                field['/V'] = pikepdf.String(value_str)

                # v1.0.2: Clear the ReadOnly flag (bit 1) on calculated
                # fields. Adobe Acrobat's NeedAppearances regeneration
                # SKIPS read-only text fields — leaving them blank even
                # when /V is set. Since we've already computed the final
                # display string and stripped the calc/format actions,
                # there's no reason to keep the field read-only in the
                # test-fill output. This is the fix for the "Rebate Total
                # column invisible on pages 7–10" bug.
                ff_val = int(field.get('/Ff', 0))
                if ff_val & 0x1:
                    field['/Ff'] = ff_val & ~0x1

                # For fields with /Kids, propagate value and AP to kid widgets
                if '/Kids' in field:
                    for kid in field['/Kids']:
                        kid['/V'] = pikepdf.String(value_str)
                        kid_ap = _make_text_ap(pdf, kid, value_str)
                        kid['/AP'] = pikepdf.Dictionary({'/N': kid_ap})

                # Generate explicit appearance stream (same approach as Pass 2)
                ap_stream = _make_text_ap(pdf, field, value_str)
                field['/AP'] = pikepdf.Dictionary({'/N': ap_stream})

                computed_count += 1

            print(f"Computed {computed_count} calculated field values")

        pdf.save(output_path)
        pdf.close()
        print(
            f"\nSummary:\n"
            f"   Text: {counts['text']}\n"
            f"   Radio: {counts['radio']}\n"
            f"   Check: {counts['checkbox']}\n"
            f"   Choice: {counts['choice']}\n"
            f"   Image: {counts['image']}"
            + (f" (skipped {counts['image_skipped']})" if counts['image_skipped'] else "") + "\n"
            f"   Saved: {output_path}"
        )

    except Exception as e:
        print(f"[ERROR] Error filling PDF: {e}")
        # traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Fill PDF form with sample data")
    parser.add_argument("input", help="Input PDF file")
    parser.add_argument("output", nargs="?", help="Output PDF file")
    parser.add_argument("--settings", help="JSON string of global settings")
    parser.add_argument(
        "--assets-dir",
        help="Directory containing test-fill assets (logo1.png, logo2.png, "
             "signature.png). Main.js passes this from the Electron app's "
             "resourcesPath. Can also be set via FILLTHATPDF_ASSETS_DIR env var."
    )

    args = parser.parse_args()
    if not args.input:
        sys.exit(1)

    fill_pdf(args.input, args.output, args.settings, args.assets_dir)
