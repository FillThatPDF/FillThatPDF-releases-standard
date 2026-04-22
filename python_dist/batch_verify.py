import os
import subprocess
from pathlib import Path
import json

def run_batch():
    static_dir = Path("/Users/36981/Desktop/PDFTest/PDFs to test/Static PDFs")
    fillable_dir = Path("/Users/36981/Desktop/PDFTest/PDFs to test/Fillable PDFs")
    model_path = "/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/training_runs/fillthatpdf_yolo_gt_v1/weights/best.pt"
    
    fillable_files = list(fillable_dir.glob("*.pdf"))
    results = []
    
    print(f"🚀 Starting Batch Audit on {len(fillable_files)} files...")
    
    for f_path in fillable_files:
        stem = f_path.stem.split("_Fillable")[0].split("_fillable")[0]
        s_path = static_dir / f"{stem}.pdf"
        
        if not s_path.exists():
            prefix = stem[:5]
            matches = list(static_dir.glob(f"{prefix}*.pdf"))
            if matches: s_path = matches[0]
            else: continue
            
        print(f"🔍 Testing {s_path.name}...")
        
        # 1. Run AI
        ai_pdf = s_path.parent / f"{s_path.stem}_AI_fillable.pdf"
        try:
            subprocess.run([
                "python3", "smart_fillable_ai.py",
                str(s_path), str(model_path),
                "--conf", "0.05", "--imgsz", "1280"
            ], check=True, capture_output=True)
            
            # 2. Verify
            verify_out = subprocess.run([
                "python3", "verify_accuracy.py",
                str(ai_pdf), str(f_path)
            ], check=True, capture_output=True, text=True).stdout
            
            # Parse metrics from stdout
            recall = 0
            precision = 0
            for line in verify_out.split("\n"):
                if "RECALL:" in line: recall = float(line.split(":")[1].replace("%","").strip())
                if "PRECISION:" in line: precision = float(line.split(":")[1].replace("%","").strip())
            
            results.append({"file": s_path.name, "recall": recall, "precision": precision})
            print(f"   ✅ Recall: {recall}% | Precision: {precision}%")
            
        except Exception as e:
            print(f"   ❌ Error on {s_path.name}: {e}")

    # Final Average
    if results:
        avg_recall = sum(r['recall'] for r in results) / len(results)
        avg_precision = sum(r['precision'] for r in results) / len(results)
        print(f"\n🏆 FINAL BATCH METRICS:")
        print(f"   Avg Recall:    {avg_recall:.2f}%")
        print(f"   Avg Precision: {avg_precision:.2f}%")
        
        with open("batch_results.json", "w") as f:
            json.dump(results, f, indent=2)

if __name__ == "__main__":
    run_batch()
