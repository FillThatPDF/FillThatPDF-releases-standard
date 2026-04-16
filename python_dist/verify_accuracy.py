import pikepdf
import argparse
from pathlib import Path

def get_widgets(pdf_path):
    widgets = []
    with pikepdf.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            if '/Annots' in page:
                for annot in page.Annots:
                    if annot.get('/Subtype') == '/Widget':
                        rect = [float(x) for x in annot.get('/Rect')]
                        # pikepdf rect: [x_min, y_min, x_max, y_max]
                        widgets.append({
                            "rect": rect,
                            "page": i
                        })
    return widgets

def calculate_iou(rect1, rect2):
    # rect format: [x0, y0, x1, y1]
    x_left = max(rect1[0], rect2[0])
    y_bottom = max(rect1[1], rect2[1])
    x_right = min(rect1[2], rect2[2])
    y_top = min(rect1[3], rect2[3])
    
    if x_right < x_left or y_top < y_bottom:
        return 0.0
        
    intersection_area = (x_right - x_left) * (y_top - y_bottom)
    area1 = (rect1[2] - rect1[0]) * (rect1[3] - rect1[1])
    area2 = (rect2[2] - rect2[0]) * (rect2[3] - rect2[1])
    
    union_area = float(area1 + area2 - intersection_area)
    if union_area == 0: return 0.0
    
    return intersection_area / union_area

def verify(ai_pdf, gt_pdf):
    ai_widgets = get_widgets(ai_pdf)
    gt_widgets = get_widgets(gt_pdf)
    
    print(f"\n📊 Verification Report")
    print(f"   AI Predictions: {len(ai_widgets)}")
    print(f"   Ground Truth:   {len(gt_widgets)}")
    
    if not gt_widgets:
        print("❌ Error: Ground Truth PDF has no form widgets!")
        return

    matches = 0
    gt_matched = [False] * len(gt_widgets)
    ai_matched = [False] * len(ai_widgets)
    
    # Matching Logic
    print("\n🔍 Alignment Analysis:")
    match_threshold = 0.3
    total_iou = 0
    total_dx = 0
    total_dy = 0
    
    for i, gt in enumerate(gt_widgets):
        best_iou = 0
        best_idx = -1
        
        for j, ai in enumerate(ai_widgets):
            if ai_matched[j]: continue
            if gt['page'] == ai['page']:
                iou = calculate_iou(gt['rect'], ai['rect'])
                if iou > best_iou:
                    best_iou = iou
                    best_idx = j
        
        if best_iou > match_threshold:
            matches += 1
            gt_matched[i] = True
            ai_matched[best_idx] = True
            total_iou += best_iou
            ai = ai_widgets[best_idx]
            total_dx += (ai['rect'][0] - gt['rect'][0])
            total_dy += (ai['rect'][1] - gt['rect'][1])
        else:
            if best_iou > 0.05:
                # Still record shift for fuzzy matches to see global trend
                ai = ai_widgets[best_idx]
                # don't add to average yet, just print
                pass
                    
    recall = matches / len(gt_widgets)
    precision = matches / len(ai_widgets) if ai_widgets else 0
    
    print(f"\n📈 Summary Metrics:")
    print(f"   Matches Found:  {matches}")
    if matches:
        print(f"   Avg Match IoU:  {total_iou/matches:.2%}")
        print(f"   Avg Shift:      dx={total_dx/matches:.2f}, dy={total_dy/matches:.2f}")
    
    missing_count = 0
    print("\n❌ Missing Fields (Ground Truth not found by AI):")
    for i, gt in enumerate(gt_widgets):
        if not gt_matched[i]:
            missing_count += 1
            if missing_count <= 20:
                print(f"      - Page {gt['page']+1}: {gt['rect']}")
    
    print(f"\n   Total Missing: {len(gt_widgets) - matches} (False Negatives)")
    print(f"   Extra Fields:   {len(ai_widgets) - matches} (False Positives)")
    print(f"   RECALL:         {recall:.2%}")
    print(f"   PRECISION:      {precision:.2%}")
    
    if recall < 1.0:
        missing_pages = sorted(list(set(gt['page'] + 1 for i, gt in enumerate(gt_widgets) if not gt_matched[i])))
        print(f"   Missing on pages: {missing_pages}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI PDF Accuracy Verifier")
    parser.add_argument("ai_pdf", help="Path to AI predicted fillable PDF")
    parser.add_argument("gt_pdf", help="Path to Ground Truth filled PDF")
    args = parser.parse_args()
    
    verify(args.ai_pdf, args.gt_pdf)
