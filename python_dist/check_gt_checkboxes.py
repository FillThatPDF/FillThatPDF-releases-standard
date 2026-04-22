#!/usr/bin/env python3
"""Check what checkbox names are in GT to understand valid patterns"""

import fitz
import os
from collections import Counter

gt_dir = '/Users/36981/Desktop/PDFTest/PDFs to test/Fillable PDFs'

gt_files = [
    '11691_ConEd_Distributor_Application_Form_v10_fillable_RELEASE.pdf',
    '14792_ConEd_Gas_HVAC_Tune-Up_Contractor_Application_v03_FINAL_RELEASE_Web_Fillable.pdf',
    '32775_DTE_2022_HVAC_Customer_SelfSubmission_Application_v01_Web_Release_Fillable.pdf',
    '53252_DTE_EEA_Field_Inspection_Report_v14_Web_Release_Fillable.pdf',
    '55570_DTE_SEEL_Contractor_Onboarding _Packet_v26_fillable.pdf',
    '57618_NGRID_New_York_Ack_Form_Fillable_v07_Release_Web_Fillable.pdf',
    '57769_CH_Res_NYS_Clean_Heat_Completion_Acknowledgement_Form_v3_Web_Release_fillable.pdf',
    '9787_ConEd_Res_HVAC_Gas_Rebate_Appl_v01_FINAL_RELEASE_Fillable_Locked.pdf',
]

all_cb_names = []
word_counts = Counter()

for filename in gt_files:
    path = os.path.join(gt_dir, filename)
    if not os.path.exists(path):
        continue
    
    doc = fitz.open(path)
    for page in doc:
        for w in page.widgets():
            if w.field_type == 2:  # CheckBox
                name = w.field_name or ''
                all_cb_names.append(name)
                words = name.split()
                word_counts[len(words)] += 1
    doc.close()

print(f"Total GT checkboxes: {len(all_cb_names)}")
print(f"\nBy word count:")
for wc, count in sorted(word_counts.items()):
    print(f"  {wc} words: {count}")

# Show examples with 4+ words
long_names = [n for n in all_cb_names if len(n.split()) >= 4]
print(f"\nGT checkboxes with 4+ words ({len(long_names)}):")
for n in long_names[:30]:
    print(f"  '{n}'")

# Check for technical specs
spec_names = [n for n in all_cb_names if any(x in n for x in ['≥', '>=', '≤', '<=', 'specification'])]
print(f"\nGT checkboxes with specs ({len(spec_names)}):")
for n in spec_names[:20]:
    print(f"  '{n}'")
