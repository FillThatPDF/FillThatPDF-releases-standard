
import json
import pikepdf
import numpy as np

# Load my latest un-calibrated detections (make sure to use a run where dx/dy was 0 for visual)
with open("debug_detections.json", "r") as f:
    detections = json.load(f)

# Load Ground Truth widgets
gt_path = "/Users/36981/Desktop/PDFTest/PDFs to test/Fillable PDFs/55570_DTE_SEEL_Contractor_Onboarding_Packet_v21_Web_Release_Fillable.pdf"
gt_widgets = []
with pikepdf.open(gt_path) as pdf:
    for i, page in enumerate(pdf.pages):
        if '/Annots' in page:
            for annot in page.Annots:
                if annot.Subtype == '/Widget' and '/Rect' in annot:
                    r = [float(x) for x in annot.Rect]
                    gt_widgets.append({'page': i, 'rect': r})

def get_iou(boxA, boxB):
    xA = max(boxA[0], boxB[0])
    yA = max(boxA[1], boxB[1])
    xB = min(boxA[2], boxB[2])
    yB = min(boxA[3], boxB[3])
    interArea = max(0, xB - xA) * max(0, yB - yA)
    boxAArea = (boxA[2] - boxA[0]) * (boxA[3] - boxA[1])
    boxBArea = (boxB[2] - boxB[0]) * (boxB[3] - boxB[1])
    iou = interArea / float(boxAArea + boxBArea - interArea) if (boxAArea + boxBArea - interArea) > 0 else 0
    return iou

best_dx, best_dy = 0, 0
max_recall = 0

print(f"Optimizing for {len(gt_widgets)} GT widgets and {len(detections)} Detections...")

# Range: -15 to 15 in 0.5 steps
for dx in np.arange(-10, 10, 0.5):
    for dy in np.arange(-10, 10, 0.5):
        matches = 0
        matched_gt = [False] * len(gt_widgets)
        
        for d in detections:
            # Apply trial calibration
            dr = d['rect']
            # Source 'visual' had dx=0 in the run that generated debug_detections.json
            # Source 'ai' had dx=1.85, dy=0.44.
            # Let's normalize to RAW by subtracting the calibration that was applied.
            if d.get('source') == 'ai':
                 raw_x0 = dr[0] - 1.85
                 raw_y0 = dr[1] - 0.44
                 raw_x1 = dr[2] - 1.85
                 raw_y1 = dr[3] - 0.44
            else:
                 raw_x0, raw_y0, raw_x1, raw_y1 = dr
            
            # Apply trial dx, dy
            tx0, ty0, tx1, ty1 = raw_x0 + dx, raw_y0 + dy, raw_x1 + dx, raw_y1 + dy
            
            for i, gt in enumerate(gt_widgets):
                if matched_gt[i]: continue
                if gt['page'] != d['page']: continue
                
                iou = get_iou([tx0, ty0, tx1, ty1], gt['rect'])
                if iou >= 0.5:
                    matches += 1
                    matched_gt[i] = True
                    break
        
        recall = matches / len(gt_widgets)
        if recall > max_recall:
            max_recall = recall
            best_dx, best_dy = dx, dy
            print(f"  New Best: dx={dx:.2f}, dy={dy:.2f} -> Recall: {recall:.2%} ({matches} matches)")

print(f"\nFINAL BEST CALIBRATION: dx={best_dx:.2f}, dy={best_dy:.2f} (Recall: {max_recall:.2%})")
