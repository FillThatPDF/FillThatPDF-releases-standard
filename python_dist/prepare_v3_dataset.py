import os
import shutil
import random
from pathlib import Path

def prepare():
    gt_src = Path("/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/training_data_gt")
    empty_src = Path("/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/training_data_empty_v2")
    output_root = Path("/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/dataset_gt_v1")
    
    for split in ["train", "val"]:
        (output_root / split / "images").mkdir(parents=True, exist_ok=True)
        (output_root / split / "labels").mkdir(parents=True, exist_ok=True)
    
    # Process GT data (The high-value data)
    gt_images = list((gt_src / "images").glob("*.jpg"))
    random.shuffle(gt_images)
    
    # 80/20 split
    split_idx = int(len(gt_images) * 0.8)
    train_gt = gt_images[:split_idx]
    val_gt = gt_images[split_idx:]
    
    def copy_files(file_list, split):
        for img_path in file_list:
            lbl_path = gt_src / "labels" / f"{img_path.stem}.txt"
            if lbl_path.exists():
                shutil.copy(img_path, output_root / split / "images")
                shutil.copy(lbl_path, output_root / split / "labels")
                
                # Oversample GT in training (5x)
                if split == "train":
                    for i in range(4):
                        shutil.copy(img_path, output_root / split / "images" / f"{img_path.stem}_copy{i}.jpg")
                        shutil.copy(lbl_path, output_root / split / "labels" / f"{img_path.stem}_copy{i}.txt")

    copy_files(train_gt, "train")
    copy_files(val_gt, "val")
    print(f"✅ Copied {len(gt_images)} GT pages (with oversampling).")
    
    # Add General Empty Data (The "Background" data)
    empty_images = list((empty_src / "images").glob("*.jpg"))
    # We only take 50% to prevent diluting the GT too much
    random.shuffle(empty_images)
    for img_path in empty_images[:50]:
        lbl_path = empty_src / "labels" / f"{img_path.stem}.txt"
        if lbl_path.exists():
            shutil.copy(img_path, output_root / "train" / "images")
            shutil.copy(lbl_path, output_root / "train" / "labels")

    # Generate data.yaml
    yaml_content = f"""
train: {output_root}/train/images
val: {output_root}/val/images

nc: 4
names: ['Text Field', 'Checkbox', 'Radio Button', 'Dropdown']
"""
    with open(output_root / "data.yaml", "w") as f:
        f.write(yaml_content)
    
    print(f"🚀 Dataset ready in {output_root}")

if __name__ == "__main__":
    prepare()
