#!/usr/bin/env python3
"""Analyze remaining FPs after safe filtering to find more patterns"""

import fitz
import os
import re
from collections import defaultdict

IOU_THRESHOLD = 0.3

def calc_iou(r1, r2):
    x1 = max(r1.x0, r2.x0)
    y1 = max(r1.y0, r2.y0)
    x2 = min(r1.x1, r2.x1)
    y2 = min(r1.y1, r2.y1)
    
    if x2 <= x1 or y2 <= y1:
        return 0.0
    
    intersection = (x2 - x1) * (y2 - y1)
    area1 = (r1.x1 - r1.x0) * (r1.y1 - r1.y0)
    area2 = (r2.x1 - r2.x0) * (r2.y1 - r2.y0)
    union = area1 + area2 - intersection
    
    return intersection / union if union > 0 else 0.0

def is_already_filtered(name, ftype, area):
    """Safe filtering rules already applied."""
    if re.match(r'^Check Box \d+$', name):
        return True
    if 'service territory' in name.lower():
        return True
    if '?' in name and ftype == 'Text':
        return True
    if 'Proposed project' in name and ftype == 'Text':
        return True
    if name.startswith('Efficiency Rating') and ftype == 'Text':
        return True
    if 'Disadvantaged' in name and ftype == 'Text':
        return True
    return False

def get_fields(pdf_path, skip_filtered=False):
    doc = fitz.open(pdf_path)
    fields = []
    for page_idx, page in enumerate(doc):
        for widget in page.widgets():
            name = widget.field_name or ''
            ftype = widget.field_type_string
            area = widget.rect.width * widget.rect.height
            
            if skip_filtered and is_already_filtered(name, ftype, area):
                continue
            
            fields.append({
                'name': name,
                'rect': widget.rect,
                'page': page_idx,
                'type': ftype,
                'area': area,
                'width': widget.rect.width,
                'height': widget.rect.height
            })
    doc.close()
    return fields

def match_fields(pred_fields, gt_fields):
    matched_gt = set()
    matched_pred = set()
    
    for i, pred in enumerate(pred_fields):
        best_iou = 0
        best_j = -1
        for j, gt in enumerate(gt_fields):
            if j in matched_gt:
                continue
            iou = calc_iou(pred['rect'], gt['rect'])
            if iou > best_iou:
                best_iou = iou
                best_j = j
        
        if best_iou >= IOU_THRESHOLD and best_j >= 0:
            matched_pred.add(i)
            matched_gt.add(best_j)
    
    tps = [pred_fields[i] for i in matched_pred]
    fps = [pred_fields[i] for i in range(len(pred_fields)) if i not in matched_pred]
    return tps, fps

# Test pairs
test_pairs = [
    ('11691', '11691_ConEd_Distributor_Application_Form_v10_fillable_RELEASE.pdf', '11691_v22_output.pdf'),
    ('14792', '14792_ConEd_Gas_HVAC_Tune-Up_Contractor_Application_v03_FINAL_RELEASE_Web_Fillable.pdf', '14792_v22_output.pdf'),
    ('32775', '32775_DTE_2022_HVAC_Customer_SelfSubmission_Application_v01_Web_Release_Fillable.pdf', '32775_v22_output.pdf'),
    ('53252', '53252_DTE_EEA_Field_Inspection_Report_v14_Web_Release_Fillable.pdf', '53252_v22_output.pdf'),
    ('55570', '55570_DTE_SEEL_Contractor_Onboarding _Packet_v26_fillable.pdf', '55570_v22_output.pdf'),
    ('57618', '57618_NGRID_New_York_Ack_Form_Fillable_v07_Release_Web_Fillable.pdf', '57618_v22_output.pdf'),
    ('57769', '57769_CH_Res_NYS_Clean_Heat_Completion_Acknowledgement_Form_v3_Web_Release_fillable.pdf', '57769_v22_output.pdf'),
    ('9787', '9787_ConEd_Res_HVAC_Gas_Rebate_Appl_v01_FINAL_RELEASE_Fillable_Locked.pdf', '9787_v22_output.pdf'),
]

gt_dir = '/Users/36981/Desktop/PDFTest/PDFs to test/Fillable PDFs'
v22_dir = '/tmp/v22_output'

all_fps = []
all_tps = []

for pdf_id, gt_name, v22_name in test_pairs:
    gt_path = os.path.join(gt_dir, gt_name)
    v22_path = os.path.join(v22_dir, v22_name)
    
    if not os.path.exists(gt_path) or not os.path.exists(v22_path):
        continue
    
    gt_fields = get_fields(gt_path)
    v22_fields = get_fields(v22_path, skip_filtered=True)
    
    tps, fps = match_fields(v22_fields, gt_fields)
    
    for f in fps:
        f['source'] = pdf_id
        all_fps.append(f)
    
    for f in tps:
        f['source'] = pdf_id
        all_tps.append(f)

print(f"After initial filtering:")
print(f"  TPs: {len(all_tps)}")
print(f"  FPs: {len(all_fps)}")

# Group FPs by PDF source
print("\n" + "="*70)
print("REMAINING FPs BY SOURCE")
print("="*70)

fp_by_source = defaultdict(list)
for fp in all_fps:
    fp_by_source[fp['source']].append(fp)

for src, fps in sorted(fp_by_source.items(), key=lambda x: -len(x[1])):
    print(f"\n{src}: {len(fps)} FPs")
    
    # Show samples
    for fp in fps[:5]:
        print(f"  - '{fp['name'][:60]}' type={fp['type']} area={fp['area']:.0f}")

# Look for more patterns
print("\n" + "="*70)
print("PATTERN ANALYSIS OF REMAINING FPs")
print("="*70)

# Pattern 1: Fields with colons
colon_fps = [f for f in all_fps if ':' in f['name']]
colon_tps = [f for f in all_tps if ':' in f['name']]
print(f"\nColon in name: FP={len(colon_fps)}, TP={len(colon_tps)}")

# Pattern 2: Fields with multiple colons (2+)
multi_colon_fps = [f for f in all_fps if f['name'].count(':') >= 2]
multi_colon_tps = [f for f in all_tps if f['name'].count(':') >= 2]
print(f"Multiple colons (2+): FP={len(multi_colon_fps)}, TP={len(multi_colon_tps)}")

# Pattern 3: HVAC-related headers
hvac_fps = [f for f in all_fps if 'HVAC' in f['name'] and f['type'] == 'Text']
hvac_tps = [f for f in all_tps if 'HVAC' in f['name'] and f['type'] == 'Text']
print(f"HVAC in text field: FP={len(hvac_fps)}, TP={len(hvac_tps)}")

# Pattern 4: Fields with "Measure" in name
measure_fps = [f for f in all_fps if 'Measure' in f['name']]
measure_tps = [f for f in all_tps if 'Measure' in f['name']]
print(f"'Measure' in name: FP={len(measure_fps)}, TP={len(measure_tps)}")

# Pattern 5: _Stack fields
stack_fps = [f for f in all_fps if '_Stack' in f['name']]
stack_tps = [f for f in all_tps if '_Stack' in f['name']]
print(f"'_Stack' in name: FP={len(stack_fps)}, TP={len(stack_tps)}")

# Pattern 6: Address_Stack specifically
addr_stack_fps = [f for f in all_fps if f['name'] == 'Address_Stack']
addr_stack_tps = [f for f in all_tps if f['name'] == 'Address_Stack']
print(f"'Address_Stack' exact: FP={len(addr_stack_fps)}, TP={len(addr_stack_tps)}")

# Pattern 7: Text fields with very long names (>80 chars)
long_name_fps = [f for f in all_fps if len(f['name']) > 80 and f['type'] == 'Text']
long_name_tps = [f for f in all_tps if len(f['name']) > 80 and f['type'] == 'Text']
print(f"Long text names (>80 chars): FP={len(long_name_fps)}, TP={len(long_name_tps)}")

# Pattern 8: Text fields with very large area (>5000)
large_fps = [f for f in all_fps if f['area'] > 5000 and f['type'] == 'Text']
large_tps = [f for f in all_tps if f['area'] > 5000 and f['type'] == 'Text']
print(f"Large text (>5000 area): FP={len(large_fps)}, TP={len(large_tps)}")

# Pattern 9: Fields with numbered suffix _2, _3, etc.
suffix_fps = [f for f in all_fps if re.search(r'_\d+$', f['name'])]
suffix_tps = [f for f in all_tps if re.search(r'_\d+$', f['name'])]
print(f"Numbered suffix (_N): FP={len(suffix_fps)}, TP={len(suffix_tps)}")

# Pattern 10: "per unit" pattern
per_unit_fps = [f for f in all_fps if 'per unit' in f['name'].lower()]
per_unit_tps = [f for f in all_tps if 'per unit' in f['name'].lower()]
print(f"'per unit' in name: FP={len(per_unit_fps)}, TP={len(per_unit_tps)}")

# Show the FPs with colon patterns
print("\n" + "="*70)
print("COLON PATTERN FPs (samples)")
print("="*70)
for fp in colon_fps[:20]:
    print(f"  {fp['source']}: '{fp['name'][:70]}' type={fp['type']}")

# Show FPs without patterns
print("\n" + "="*70)
print("FPs WITHOUT COMMON PATTERNS")
print("="*70)

other_fps = []
for fp in all_fps:
    has_pattern = False
    if ':' in fp['name']:
        has_pattern = True
    if 'HVAC' in fp['name']:
        has_pattern = True
    if '_Stack' in fp['name']:
        has_pattern = True
    if 'Measure' in fp['name']:
        has_pattern = True
    if 'per unit' in fp['name'].lower():
        has_pattern = True
    if fp['area'] > 5000 and fp['type'] == 'Text':
        has_pattern = True
    
    if not has_pattern:
        other_fps.append(fp)

print(f"\nFPs without common patterns: {len(other_fps)}")
for fp in other_fps[:30]:
    print(f"  {fp['source']}: '{fp['name'][:60]}' type={fp['type']} area={fp['area']:.0f}")
