from ultralytics import YOLO
import sys

def train():
    # Load model
    # 'yolo11n.pt' is the latest Nano model. If not found, it will auto-download.
    # If 11 is not available in the installed pip version yet (it's very new), fallback to yolov8n.pt
    model_name = 'yolo11n.pt'
    
    print(f"Loading {model_name}...")
    try:
        model = YOLO(model_name)
    except Exception as e:
        print(f"Could not load {model_name}, falling back to yolov8n.pt. Error: {e}")
        model = YOLO('yolov8n.pt')

    # Train the model
    # data: Path to data.yaml
    # device: 'mps' for Mac, 'cpu' if fails
    print("Starting training on MPS (Mac GPU)...")
    
    results = model.train(
        data='/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/dataset_combined/data.yaml', 
        epochs=50, 
        imgsz=640,
        device='mps', 
        plots=True,
        patience=10, # Stop early if no improvement
        project='/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/training_runs',
        name='fillthatpdf_yolo_combined'
    )
    
    print("Training Complete!")
    print(f"Best model path: {results.best}")
    
    # Validate
    metrics = model.val()
    print(f"Validation mAP50-95: {metrics.box.map}")

if __name__ == '__main__':
    train()
