import shutil
from pathlib import Path
import random
import yaml
import os
import argparse

def prepare_weighted(filled_dir, empty_dir, output_dir, oversample_factor=2):
    filled_p = Path(filled_dir)
    empty_p = Path(empty_dir)
    out_p = Path(output_dir)
    
    # Clean Output
    if out_p.exists(): shutil.rmtree(out_p)
    out_p.mkdir(parents=True)
    
    (out_p / "images" / "train").mkdir(parents=True)
    (out_p / "images" / "val").mkdir(parents=True)
    (out_p / "labels" / "train").mkdir(parents=True)
    (out_p / "labels" / "val").mkdir(parents=True)
    
    # Gather Files
    filled_imgs = list((filled_p / "images").glob("*.jpg"))
    empty_imgs = list((empty_p / "images").glob("*.jpg"))
    
    print(f"Source: {len(filled_imgs)} Filled, {len(empty_imgs)} Empty")
    
    # 1. Process Filled (No Oversampling)
    # Split 80/20
    random.shuffle(filled_imgs)
    split_idx = int(len(filled_imgs) * 0.8)
    train_filled = filled_imgs[:split_idx]
    val_filled = filled_imgs[split_idx:]
    
    copy_files(train_filled, filled_p, out_p, "train")
    copy_files(val_filled, filled_p, out_p, "val")
    
    # 2. Process Empty (With Oversampling)
    # Split FIRST to avoid leakage (don't have copy1 in train and copy2 in val)
    random.shuffle(empty_imgs)
    split_idx = int(len(empty_imgs) * 0.8)
    train_empty = empty_imgs[:split_idx]
    val_empty = empty_imgs[split_idx:]
    
    # Copy Train (Oversampled)
    for i in range(oversample_factor):
        suffix = "" if i == 0 else f"_copy{i}"
        copy_files(train_empty, empty_p, out_p, "train", suffix)
        
    # Copy Val (No Oversampling needed for metric accuracy, but acceptable)
    copy_files(val_empty, empty_p, out_p, "val")
    
    print(f"Dataset Created at {output_dir}")
    create_yaml(out_p)

def copy_files(img_list, source_root, out_root, split, suffix=""):
    for img_path in img_list:
        label_name = img_path.stem + ".txt"
        label_path = source_root / "labels" / label_name
        
        if not label_path.exists():
            continue
            
        new_stem = img_path.stem + suffix
        new_img_name = new_stem + img_path.suffix
        new_label_name = new_stem + ".txt"
        
        shutil.copy(img_path, out_root / "images" / split / new_img_name)
        shutil.copy(label_path, out_root / "labels" / split / new_label_name)

def create_yaml(dataset_path):
    data = {
        'path': str(dataset_path),
        'train': 'images/train',
        'val': 'images/val',
        'names': {
            0: 'Text Field',
            1: 'Checkbox',
            2: 'Radio Button',
            3: 'Dropdown'
        }
    }
    
    with open(dataset_path / 'data.yaml', 'w') as f:
        yaml.dump(data, f)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("filled_dir")
    parser.add_argument("empty_dir")
    parser.add_argument("output_dir")
    parser.add_argument("--factor", type=int, default=2)
    args = parser.parse_args()
    
    prepare_weighted(args.filled_dir, args.empty_dir, args.output_dir, args.factor)
