#!/usr/bin/env python3
"""
fill_pdf.py - Universal PDF Form Filler with Acrobat-compatible checkmarks
Refactored to remove PyMuPDF dependency.
"""

import pikepdf
import random
import sys
import os
import re
import subprocess
import time

# Seed random with current time for truly random selections
random.seed(time.time())

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
    'currencies': ['$1,250.00', '$3,500.00', '$750.00', '$2,000.00', '$500.00'],
    'percentages': ['95%', '98%', '85%', '92%', '88%'],
    'rvalues': ['R-13', 'R-19', 'R-30', 'R-38', 'R-49'],
    'btus': ['80,000', '60,000', '100,000', '40,000', '120,000'],
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
    'tons', 'temperature', 'temp', 'degf', 'deg f', 'degrees',
    'efficiency', 'airflow', 'oxygen', 'carbon', 'monoxide', 'dioxide',
    'coil', 'stack', 'elapsed', 'minutes', 'sir',
    'db', 'wb', 'score', 'program year', 'program_year', 'programyear',
]

def is_numeric_field_name(name: str) -> bool:
    base = re.sub(r'_\d+$', '', name)
    cleaned = base.replace(',', '').replace(' ', '').replace('.', '')
    return cleaned.isdigit() and len(cleaned) > 0

def create_vector_checkmark_stream(width: float, height: float) -> bytes:
    scale = min(width, height) / 6.0
    x1, y1 = 0.9 * scale, 3.0 * scale
    x2, y2 = 2.1 * scale, 0.9 * scale
    x3, y3 = 5.1 * scale, 5.1 * scale
    line_width = 1.5 * scale
    return f"q Q q 0 0 {width:.0f} {height:.0f} re W n {line_width:.1f} w 0 0 0 RG {x1:.1f} {y1:.1f} m {x2:.1f} {y2:.1f} l {x3:.1f} {y3:.1f} l S Q".encode('latin-1')

def make_appearance_dict(pdf: pikepdf.Pdf, on_state_name: str, width: float, height: float, is_radio: bool = False) -> pikepdf.Dictionary:
    check_content = create_vector_checkmark_stream(width, height)
    off_content = b'q Q'
    
    check_stream = pikepdf.Stream(pdf, check_content)
    check_stream['/BBox'] = pikepdf.Array([0, 0, width, height])
    check_stream['/Subtype'] = pikepdf.Name('/Form')
    check_stream['/Type'] = pikepdf.Name('/XObject')
    
    off_stream = pikepdf.Stream(pdf, off_content)
    off_stream['/BBox'] = pikepdf.Array([0, 0, width, height])
    off_stream['/Subtype'] = pikepdf.Name('/Form')
    off_stream['/Type'] = pikepdf.Name('/XObject')
    
    state_name = on_state_name if on_state_name.startswith('/') else '/' + on_state_name
    ap_n = pikepdf.Dictionary()
    ap_n[pikepdf.Name(state_name)] = check_stream
    ap_n['/Off'] = off_stream
    return ap_n

def get_field_rect(field) -> tuple:
    rect = field.get('/Rect', [0, 0, 10, 10])
    if hasattr(rect, '__iter__'): rect = [float(r) for r in rect]
    return abs(rect[2] - rect[0]), abs(rect[3] - rect[1])

def get_text_value(field_name: str) -> str:
    name_lower = field_name.lower()
    if is_numeric_field_name(field_name): return str(random.randint(10, 500))
    is_numeric = any(p in name_lower for p in NUMERIC_PATTERNS) or '$' in field_name
    if is_numeric:
        if 'year' in name_lower: return random.choice(['2024', '2025', '2026'])
        return str(random.randint(10, 1000))
    if 'state' in name_lower: return random.choice(SAMPLE_DATA['states'])
    elif 'zip' in name_lower: return random.choice(SAMPLE_DATA['zips'])
    elif 'phone' in name_lower or 'tel' in name_lower: return random.choice(SAMPLE_DATA['phones'])
    elif 'date' in name_lower: return random.choice(SAMPLE_DATA['dates'])
    elif 'email' in name_lower: return random.choice(SAMPLE_DATA['emails'])
    elif 'name' in name_lower: return random.choice(SAMPLE_DATA['names'])
    return 'Sample'

def fill_pdf(input_path: str, output_path: str = None):
    if output_path is None:
        output_path = f"{os.path.splitext(input_path)[0]}_FILLED.pdf"
    
    print(f"📄 Opening: {input_path}")
    pdf = pikepdf.open(input_path, allow_overwriting_input=True)
    
    if '/AcroForm' in pdf.Root and '/XFA' in pdf.Root.AcroForm:
        del pdf.Root.AcroForm['/XFA']
    if '/AcroForm' in pdf.Root:
        pdf.Root.AcroForm['/NeedAppearances'] = True
    
    if '/AcroForm' not in pdf.Root: return
    
    fields = list(pdf.Root.AcroForm.Fields)
    for field in fields:
        ft = str(field.get('/FT', ''))
        name = str(field.get('/T', ''))
        
        if '/Tx' in ft:
            value = get_text_value(name)
            field['/V'] = pikepdf.String(value)
            
            w, h = get_field_rect(field)
            es_v = value.replace('(', '\\(').replace(')', '\\)')
            stream = f"q .7 .85 1 rg 0 0 {w:.2f} {h:.2f} re f 0 g BT /Helv 9 Tf 2 4 Td ({es_v}) Tj ET Q"
            ap = pikepdf.Stream(pdf, stream.encode('latin-1'))
            ap['/BBox'] = pikepdf.Array([0, 0, w, h])
            ap['/Subtype'] = pikepdf.Name('/Form')
            ap['/Type'] = pikepdf.Name('/XObject')
            ap['/Resources'] = pikepdf.Dictionary({'/Font': pikepdf.Dictionary({'/Helv': pikepdf.Dictionary({'/Type': pikepdf.Name('/Font'), '/Subtype': pikepdf.Name('/Type1'), '/BaseFont': pikepdf.Name('/Helvetica')})})})
            if '/AP' not in field: field['/AP'] = pikepdf.Dictionary()
            field['/AP']['/N'] = ap
            field['/H'] = pikepdf.Name('/N')

        elif '/Btn' in ft:
            if '/Kids' in field:
                on_state = '/Yes'
                for kid in field['/Kids']:
                    if '/AP' in kid and '/N' in kid['/AP']:
                        for k in kid['/AP']['/N'].keys():
                            if str(k) != '/Off': 
                                on_state = str(k)
                                break
                    kw, kh = get_field_rect(kid)
                    kid['/AP'] = pikepdf.Dictionary({'/N': make_appearance_dict(pdf, on_state, kw, kh)})
                    kid['/AS'] = pikepdf.Name('/Off')
                field['/V'] = pikepdf.Name(on_state)
                field['/Kids'][0]['/AS'] = pikepdf.Name(on_state)
            else:
                w, h = get_field_rect(field)
                field['/AP'] = pikepdf.Dictionary({'/N': make_appearance_dict(pdf, '/Yes', w, h)})
                field['/V'] = pikepdf.Name('/Yes')
                field['/AS'] = pikepdf.Name('/Yes')
    
    pdf.save(output_path)
    pdf.close()
    print(f"💾 Saved: {output_path}")

if __name__ == '__main__':
    if len(sys.argv) < 2: sys.exit(1)
    fill_pdf(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
