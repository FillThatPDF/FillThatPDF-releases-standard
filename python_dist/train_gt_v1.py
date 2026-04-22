from ultralytics import YOLO
import torch

def train():
    # Load the base model (v2 weights or fresh n)
    # Using v2 as a starting point to preserve general field knowledge
    model_path = "/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/training_runs/fillthatpdf_yolo_empty_v2/weights/best.pt"
    model = YOLO(model_path)
    
    # Train
    model.train(
        data="/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/dataset_gt_v1/data.yaml",
        epochs=50,
        imgsz=1280,
        device="mps",
        name="fillthatpdf_yolo_gt_v1",
        project="/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/training_runs",
        batch=8,
        patience=15, # Early stopping
        save=True
    )

if __name__ == "__main__":
    train()
