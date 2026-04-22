#!/usr/bin/env python3
"""Analyze FP patterns from error analysis"""
import json
from collections import Counter

# Load the error analysis report
with open('/tmp/error_analysis/error_analysis_report_20260201_180006.json') as f:
    data = json.load(f)

# Look at FP patterns across all PDFs
all_fp_names = []
for info in data['results']:
    pdf_name = info.get('pdf', 'unknown')
    for fp in info.get('false_positives', []):
        all_fp_names.append({
            'pdf': pdf_name,
            'name': fp.get('name', ''),
            'type': fp.get('type', ''),
            'area': round(fp.get('width', 0) * fp.get('height', 0), 1)
        })

# Count FPs by name patterns
name_patterns = Counter()
for fp in all_fp_names:
    name = fp['name']
    # Categorize by pattern
    if 'ENERGY' in name.upper():
        name_patterns['ENERGY STAR patterns'] += 1
    elif 'Cell_' in name:
        name_patterns['Cell_xxx_xxx auto-named'] += 1
    elif any(x in name for x in ['Furnace', 'Boiler', 'Refrigerator', 'Insulation', 'Duct']):
        name_patterns['Appliance/building labels'] += 1
    elif name.startswith('R') and '+' in name:
        name_patterns['R-value labels (R10+)'] += 1
    elif any(x in name for x in ['Rated', 'Certified', 'following']):
        name_patterns['Status labels'] += 1
    elif 'checkbox' in fp['type']:
        name_patterns['Checkbox type FP'] += 1
    elif 'radio' in fp['type']:
        name_patterns['Radio type FP'] += 1
    else:
        name_patterns['Other text'] += 1

print('FP Name Patterns:')
for pattern, count in name_patterns.most_common(20):
    print(f'  {pattern}: {count}')

print(f'\nTotal FPs: {len(all_fp_names)}')

# Show some specific examples
print('\nSample FP names (first 50):')
for fp in all_fp_names[:50]:
    print(f"  [{fp['pdf'][:5]}] {fp['type']:8} area={fp['area']:>8} {fp['name'][:50]}")

# Also look at "Other text" FPs specifically  
print('\n\n=== "Other text" FPs ===')
other_fps = [fp for fp in all_fp_names if 
    'ENERGY' not in fp['name'].upper() and
    'Cell_' not in fp['name'] and
    not any(x in fp['name'] for x in ['Furnace', 'Boiler', 'Refrigerator', 'Insulation', 'Duct']) and
    not (fp['name'].startswith('R') and '+' in fp['name']) and
    not any(x in fp['name'] for x in ['Rated', 'Certified', 'following']) and
    'checkbox' not in fp['type'] and 
    'radio' not in fp['type']
]

print(f"Count: {len(other_fps)}")
for fp in other_fps[:100]:
    print(f"  [{fp['pdf'][:5]}] {fp['type']:8} area={fp['area']:>8} {fp['name'][:60]}")
