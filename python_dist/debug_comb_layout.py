#!/usr/bin/env python3
"""Debug comb field layout"""

# Gas box vertical lines: 36.2, 54.2, 72.2, ..., 252.2 (13 lines = 12 boxes)
# The lines are at box boundaries (both left and right edges of boxes)
# First line = left edge of box 1
# Last line = right edge of box 12
# So field should span from first_line to last_line (NOT last_line + spacing)

gas_lines = [36.2, 54.2, 72.2, 90.2, 108.2, 126.2, 144.2, 162.2, 180.2, 198.2, 216.2, 234.2, 252.2]
print('Gas Account box positions:')
print(f'  First line (x_start): {gas_lines[0]}')
print(f'  Last line (x_end): {gas_lines[-1]}')
print(f'  Num lines: {len(gas_lines)}')
print(f'  Num boxes: {len(gas_lines) - 1}')
print(f'  Spacing: {gas_lines[1] - gas_lines[0]:.1f}')
print(f'  Field should span: x={gas_lines[0]} to x={gas_lines[-1]}')
print()
print('With 13 lines bounding 12 boxes:')
print('  - x_start = first line = left edge of first box')
print('  - x_end = last line = RIGHT edge of last box')
print('  - So x_end_full should just be x_end, NOT x_end + spacing')
print()
print('The v35.14 fix incorrectly assumed x_end was LEFT edge of last box')
print('But now that we have 13 lines including the rightmost boundary,')
print('x_end IS the right edge, and we should NOT add spacing')
