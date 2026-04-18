from ultralytics import YOLO

def train():
    # Load Model (Start from base to avoid Filled Text bias)
    # yolo11n.pt is the Nano model (fastest)
    model = YOLO('yolo11n.pt') 
    
    print("Starting training on WEIGHTED Data (High Res 1280)...")
    
    results = model.train(
        data='/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/dataset_weighted/data.yaml', 
        epochs=30, 
        imgsz=960, # High Res (optimized)
        device='mps', 
        plots=True,
        patience=10, 
        project='/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/training_runs',
        name='fillthatpdf_yolo_weighted'
    )
    
    print("Training Complete!")

if __name__ == '__main__':
    train()
