from ultralytics import YOLO

def train():
    # Load Model (Start from base to avoid Filled Text bias)
    model = YOLO('yolo11n.pt') 
    
    print("Starting training on EMPTY v2 Data (High Res 1280)...")
    
    results = model.train(
        data='/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/dataset_empty_v2/data.yaml', 
        epochs=50, 
        imgsz=1280, 
        device='mps', 
        plots=True,
        patience=20, 
        project='/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/training_runs',
        name='fillthatpdf_yolo_empty_v2'
    )
    
    print("Training Complete!")

if __name__ == '__main__':
    train()
