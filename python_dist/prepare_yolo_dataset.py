import os
import shutil
import random
import yaml
from pathlib import Path
import argparse

def prepare_dataset(source_dir, output_dir, split_ratio=0.8):
    source = Path(source_dir)
    output = Path(output_dir)
    
    # 1. Structure
    img_train = output / "images" / "train"
    img_val = output / "images" / "val"
    lbl_train = output / "labels" / "train"
    lbl_val = output / "labels" / "val"
    
    for p in [img_train, img_val, lbl_train, lbl_val]:
        p.mkdir(parents=True, exist_ok=True)
        
    # 2. Get Files
    # We only care about images. If label is missing, it's an empty file.
    # Harvester output 'images' and 'labels' folders inside source.
    
    src_images_dir = source / "images"
    src_labels_dir = source / "labels"
    
    all_images = list(src_images_dir.glob("*.jpg"))
    random.shuffle(all_images)
    
    split_idx = int(len(all_images) * split_ratio)
    train_imgs = all_images[:split_idx]
    val_imgs = all_images[split_idx:]
    
    print(f"Total Images: {len(all_images)}")
    print(f"Training: {len(train_imgs)}")
    print(f"Validation: {len(val_imgs)}")
    
    # 3. Copy Files
    def copy_set(img_list, dest_img_dir, dest_lbl_dir):
        for img_path in img_list:
            # Copy Image
            shutil.copy(img_path, dest_img_dir / img_path.name)
            
            # Copy Label
            # Label name is same stem as image + .txt
            lbl_name = img_path.stem + ".txt"
            lbl_path = src_labels_dir / lbl_name
            
            dest_lbl_path = dest_lbl_dir / lbl_name
            
            if lbl_path.exists():
                shutil.copy(lbl_path, dest_lbl_path)
            else:
                # Create empty label file if no fields
                # print(f"Creating empty label for {lbl_name}")
                with open(dest_lbl_path, "w") as f:
                    pass

    print("Copying Training Set...")
    copy_set(train_imgs, img_train, lbl_train)
    
    print("Copying Validation Set...")
    copy_set(val_imgs, img_val, lbl_val)
    
    # 4. Create data.yaml
    classes = ["Text Field", "Checkbox", "Radio Button", "Dropdown"]
    
    yaml_content = {
        "path": str(output.absolute()),
        "train": "images/train",
        "val": "images/val",
        "names": {i: name for i, name in enumerate(classes)}
    }
    
    yaml_path = output / "data.yaml"
    with open(yaml_path, "w") as f:
        yaml.dump(yaml_content, f)
        
    print(f"\nDataset Ready at {output}")
    print(f"Config: {yaml_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", help="Path to harvest_training_data output")
    parser.add_argument("dest", help="Path to new YOLO dataset folder")
    args = parser.parse_args()
    
    prepare_dataset(args.source, args.dest)
