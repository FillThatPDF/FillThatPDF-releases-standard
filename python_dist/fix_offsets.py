import sys

path = "/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/FillThatPDF_Optimized_v5/python_dist/smart_fillable_v22.py"
with open(path, 'r') as f:
    lines = f.readlines()

# Find the redundant block
# 12180:                     # Position with transformations
# ...
# 12187:                     tx0, ty0, tx1, ty1 = tx0 + gap, ty0 + gap, tx1 - gap, ty1 - gap

found = False
for i in range(len(lines)):
    if "# Position with transformations" in lines[i] and i > 12000:
        print(f"Found block at line {i+1}")
        # Delete lines i to i+7
        del lines[i:i+8]
        lines.insert(i, "                    # Sizing logic (tx0/tx1 now correctly initialized)\n")
        found = True
        break

if found:
    with open(path, 'w') as f:
        f.writelines(lines)
    print("Success")
else:
    print("Not found")
