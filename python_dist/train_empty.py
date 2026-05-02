from ultralytics import YOLO

def train():
    # Load Model (Start from base to avoid Filled Text bias)
    model = YOLO('yolo11n.pt') 
    
    print("Starting training on Empty Data (High Res)...")
    
    results = model.train(
        data='/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/dataset_empty/data.yaml', 
        epochs=100, 
        imgsz=1280, # High Res for thin lines
        device='mps', 
        plots=True,
        patience=20, 
        project='/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/training_runs',
        name='fillthatpdf_yolo_empty'
    )
    
    print("Training Complete!")

if __name__ == '__main__':
    train()
