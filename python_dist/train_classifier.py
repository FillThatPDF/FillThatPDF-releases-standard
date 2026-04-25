#!/usr/bin/env python3
"""
Field Type Classifier Training
==============================

Train a CNN to classify cropped field images into types:
- text, checkbox, radio, dropdown, not_a_field

This classifier works WITH the rule-based system for high precision.

WORKFLOW:
1. Run harvest_training_v2.py to create classifier/ folder with crops
2. Run this script to train the model
3. Use the model with smart_fillable_hybrid.py

Usage:
    python train_classifier.py --data ./dataset_v3/classifier --epochs 30

Author: FillThatPDF Team
Date: February 2026
"""

import os
import sys
import argparse
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Tuple, Dict, List
import random

import numpy as np
import cv2

# Check for PyTorch
try:
    import torch
    import torch.nn as nn
    import torch.optim as optim
    from torch.utils.data import Dataset, DataLoader
    from torchvision import transforms, models
    from torch.optim.lr_scheduler import ReduceLROnPlateau
except ImportError:
    print("❌ PyTorch required! Install with:")
    print("   pip install torch torchvision")
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


# =============================================================================
# DATASET
# =============================================================================

class FieldClassifierDataset(Dataset):
    """Dataset for field classification."""
    
    CLASS_NAMES = ["text", "checkbox", "radio", "dropdown", "not_a_field"]
    
    def __init__(self, data_dir: str, split: str = "train", 
                 transform=None, val_split: float = 0.15):
        """
        Args:
            data_dir: Path to classifier/ folder with subdirectories per class
                      Supports both flat structure (class folders directly)
                      and train/val structure (train/class, val/class)
            split: "train" or "val"
            transform: Transforms to apply
            val_split: Fraction for validation (only used if flat structure)
        """
        self.data_dir = Path(data_dir)
        self.split = split
        self.transform = transform
        
        # Gather all samples
        self.samples = []  # (path, class_idx)
        self.class_counts = {c: 0 for c in self.CLASS_NAMES}
        
        # Check for train/val structure vs flat structure
        train_dir = self.data_dir / "train"
        val_dir = self.data_dir / "val"
        
        if train_dir.exists() and val_dir.exists():
            # Train/val structure - use the appropriate subfolder
            search_dir = train_dir if split == "train" else val_dir
            use_auto_split = False
        else:
            # Flat structure - class folders directly under data_dir
            search_dir = self.data_dir
            use_auto_split = True
        
        for i, class_name in enumerate(self.CLASS_NAMES):
            class_dir = search_dir / class_name
            if not class_dir.exists():
                # Skip missing classes silently for train/val structure
                if use_auto_split:
                    logger.warning(f"⚠️ Missing class folder: {class_dir}")
                continue
            
            images = list(class_dir.glob("*.png")) + list(class_dir.glob("*.jpg"))
            for img_path in images:
                self.samples.append((str(img_path), i))
            
            self.class_counts[class_name] += len(images)
        
        # Auto split only for flat structure
        if use_auto_split:
            random.seed(42)
            random.shuffle(self.samples)
            
            split_idx = int(len(self.samples) * (1 - val_split))
            if split == "train":
                self.samples = self.samples[:split_idx]
            else:
                self.samples = self.samples[split_idx:]
        
        logger.info(f"[{split}] {len(self.samples)} samples")
    
    def __len__(self):
        return len(self.samples)
    
    def __getitem__(self, idx):
        path, label = self.samples[idx]
        
        # Load image
        img = cv2.imread(path)
        if img is None:
            # Return black image on error
            img = np.zeros((64, 128, 3), dtype=np.uint8)
        else:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        
        # Apply transforms
        if self.transform:
            img = self.transform(img)
        else:
            # Default: resize and normalize
            img = cv2.resize(img, (128, 64))
            img = torch.from_numpy(img).permute(2, 0, 1).float() / 255.0
        
        return img, label


# =============================================================================
# MODEL
# =============================================================================

def create_model(num_classes: int = 5, pretrained: bool = True) -> nn.Module:
    """
    Create MobileNetV3-Small for fast inference.
    
    Why MobileNetV3:
    - Fast enough for real-time classification during PDF processing
    - Small enough to include in app package
    - Still accurate enough for this task
    """
    model = models.mobilenet_v3_small(
        weights=models.MobileNet_V3_Small_Weights.IMAGENET1K_V1 if pretrained else None
    )
    
    # Replace classifier
    model.classifier[-1] = nn.Linear(
        model.classifier[-1].in_features,
        num_classes
    )
    
    return model


def create_simple_cnn(num_classes: int = 5) -> nn.Module:
    """
    Simple CNN for when pre-trained isn't available or dataset is small.
    """
    return nn.Sequential(
        # Conv block 1
        nn.Conv2d(3, 32, 3, padding=1),
        nn.BatchNorm2d(32),
        nn.ReLU(inplace=True),
        nn.MaxPool2d(2),
        
        # Conv block 2
        nn.Conv2d(32, 64, 3, padding=1),
        nn.BatchNorm2d(64),
        nn.ReLU(inplace=True),
        nn.MaxPool2d(2),
        
        # Conv block 3
        nn.Conv2d(64, 128, 3, padding=1),
        nn.BatchNorm2d(128),
        nn.ReLU(inplace=True),
        nn.AdaptiveAvgPool2d(1),
        
        # Classifier
        nn.Flatten(),
        nn.Dropout(0.3),
        nn.Linear(128, num_classes)
    )


# =============================================================================
# TRAINING
# =============================================================================

class Trainer:
    """Training loop with validation."""
    
    def __init__(self, model: nn.Module, train_loader: DataLoader, 
                 val_loader: DataLoader, device: str, 
                 class_weights: torch.Tensor = None):
        self.model = model.to(device)
        self.train_loader = train_loader
        self.val_loader = val_loader
        self.device = device
        
        # Loss with class weights (for imbalanced data)
        if class_weights is not None:
            self.criterion = nn.CrossEntropyLoss(weight=class_weights.to(device))
        else:
            self.criterion = nn.CrossEntropyLoss()
        
        # Optimizer
        self.optimizer = optim.AdamW(model.parameters(), lr=1e-3, weight_decay=0.01)
        
        # LR scheduler
        self.scheduler = ReduceLROnPlateau(
            self.optimizer, mode='min', factor=0.5, patience=3
        )
        
        # Tracking
        self.best_val_acc = 0
        self.history = {"train_loss": [], "train_acc": [], "val_loss": [], "val_acc": []}
    
    def train_epoch(self) -> Tuple[float, float]:
        """Train one epoch."""
        self.model.train()
        total_loss = 0
        correct = 0
        total = 0
        
        for images, labels in self.train_loader:
            images = images.to(self.device)
            labels = labels.to(self.device)
            
            self.optimizer.zero_grad()
            outputs = self.model(images)
            loss = self.criterion(outputs, labels)
            loss.backward()
            self.optimizer.step()
            
            total_loss += loss.item()
            _, predicted = outputs.max(1)
            total += labels.size(0)
            correct += predicted.eq(labels).sum().item()
        
        return total_loss / len(self.train_loader), 100 * correct / total
    
    def validate(self) -> Tuple[float, float]:
        """Validate model."""
        self.model.eval()
        total_loss = 0
        correct = 0
        total = 0
        
        with torch.no_grad():
            for images, labels in self.val_loader:
                images = images.to(self.device)
                labels = labels.to(self.device)
                
                outputs = self.model(images)
                loss = self.criterion(outputs, labels)
                
                total_loss += loss.item()
                _, predicted = outputs.max(1)
                total += labels.size(0)
                correct += predicted.eq(labels).sum().item()
        
        return total_loss / len(self.val_loader), 100 * correct / total
    
    def train(self, epochs: int, save_path: str) -> Dict:
        """Full training loop."""
        logger.info("\n" + "="*60)
        logger.info("🏋️ TRAINING STARTED")
        logger.info("="*60)
        
        for epoch in range(epochs):
            train_loss, train_acc = self.train_epoch()
            val_loss, val_acc = self.validate()
            
            # Update scheduler
            self.scheduler.step(val_loss)
            
            # Track history
            self.history["train_loss"].append(train_loss)
            self.history["train_acc"].append(train_acc)
            self.history["val_loss"].append(val_loss)
            self.history["val_acc"].append(val_acc)
            
            # Log
            logger.info(
                f"Epoch {epoch+1:3d}/{epochs} | "
                f"Train: {train_loss:.4f}, {train_acc:.1f}% | "
                f"Val: {val_loss:.4f}, {val_acc:.1f}%"
            )
            
            # Save best
            if val_acc > self.best_val_acc:
                self.best_val_acc = val_acc
                torch.save(self.model.state_dict(), save_path)
                logger.info(f"   ✓ Saved best model ({val_acc:.1f}%)")
        
        return self.history


def compute_class_weights(dataset: FieldClassifierDataset) -> torch.Tensor:
    """Compute inverse frequency weights for class imbalance."""
    counts = np.array([dataset.class_counts[c] for c in dataset.CLASS_NAMES])
    counts = np.maximum(counts, 1)  # Avoid div by zero
    weights = 1.0 / counts
    weights = weights / weights.sum() * len(weights)
    return torch.tensor(weights, dtype=torch.float32)


# =============================================================================
# EVALUATION
# =============================================================================

def evaluate_model(model: nn.Module, data_loader: DataLoader, 
                   device: str, class_names: List[str]) -> Dict:
    """Detailed evaluation with per-class metrics."""
    model.eval()
    
    all_preds = []
    all_labels = []
    
    with torch.no_grad():
        for images, labels in data_loader:
            images = images.to(device)
            outputs = model(images)
            _, predicted = outputs.max(1)
            
            all_preds.extend(predicted.cpu().numpy())
            all_labels.extend(labels.numpy())
    
    all_preds = np.array(all_preds)
    all_labels = np.array(all_labels)
    
    # Overall accuracy
    accuracy = (all_preds == all_labels).mean() * 100
    
    # Per-class metrics
    results = {"overall_accuracy": accuracy, "per_class": {}}
    
    for i, class_name in enumerate(class_names):
        mask = all_labels == i
        if mask.sum() == 0:
            results["per_class"][class_name] = {"precision": 0, "recall": 0, "f1": 0, "count": 0}
            continue
        
        # True positives, false positives, false negatives
        tp = ((all_preds == i) & (all_labels == i)).sum()
        fp = ((all_preds == i) & (all_labels != i)).sum()
        fn = ((all_preds != i) & (all_labels == i)).sum()
        
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
        
        results["per_class"][class_name] = {
            "precision": precision * 100,
            "recall": recall * 100,
            "f1": f1 * 100,
            "count": int(mask.sum())
        }
    
    return results


def print_evaluation(results: Dict, class_names: List[str]):
    """Pretty print evaluation results."""
    logger.info("\n" + "="*60)
    logger.info("📊 EVALUATION RESULTS")
    logger.info("="*60)
    logger.info(f"\nOverall Accuracy: {results['overall_accuracy']:.1f}%\n")
    
    logger.info(f"{'Class':<15} {'Precision':>10} {'Recall':>10} {'F1':>10} {'Count':>8}")
    logger.info("-" * 60)
    
    for class_name in class_names:
        m = results["per_class"].get(class_name, {})
        logger.info(
            f"{class_name:<15} "
            f"{m.get('precision', 0):>9.1f}% "
            f"{m.get('recall', 0):>9.1f}% "
            f"{m.get('f1', 0):>9.1f}% "
            f"{m.get('count', 0):>8}"
        )


# =============================================================================
# DATA AUGMENTATION
# =============================================================================

def get_transforms(is_train: bool):
    """Get data transforms with augmentation for training."""
    if is_train:
        return transforms.Compose([
            transforms.ToPILImage(),
            transforms.Resize((64, 128)),
            transforms.RandomHorizontalFlip(p=0.3),
            transforms.RandomAffine(degrees=5, translate=(0.1, 0.1), scale=(0.9, 1.1)),
            transforms.ColorJitter(brightness=0.2, contrast=0.2),
            transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
            transforms.RandomErasing(p=0.2)
        ])
    else:
        return transforms.Compose([
            transforms.ToPILImage(),
            transforms.Resize((64, 128)),
            transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
        ])


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Train Field Type Classifier",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--data", required=True, help="Path to classifier/ folder")
    parser.add_argument("--epochs", type=int, default=30, help="Training epochs")
    parser.add_argument("--batch-size", type=int, default=32, help="Batch size")
    parser.add_argument("--output", "-o", default="./classifier_model.pt", 
                        help="Output model path")
    parser.add_argument("--simple", action="store_true", 
                        help="Use simple CNN instead of MobileNet")
    parser.add_argument("--no-weights", action="store_true",
                        help="Don't use class weights for imbalanced data")
    
    args = parser.parse_args()
    
    # Check data folder
    data_path = Path(args.data)
    if not data_path.exists():
        logger.error(f"❌ Data folder not found: {data_path}")
        logger.info("\nRun harvest_training_v2.py first to create training data:")
        logger.info("  python harvest_training_v2.py --output ./dataset_v3")
        sys.exit(1)
    
    # Device
    if torch.backends.mps.is_available():
        device = "mps"
    elif torch.cuda.is_available():
        device = "cuda"
    else:
        device = "cpu"
    logger.info(f"🖥️ Device: {device}")
    
    # Create datasets
    logger.info(f"\n📂 Loading data from: {data_path}")
    
    train_dataset = FieldClassifierDataset(
        str(data_path), split="train", 
        transform=get_transforms(is_train=True)
    )
    val_dataset = FieldClassifierDataset(
        str(data_path), split="val",
        transform=get_transforms(is_train=False)
    )
    
    if len(train_dataset) == 0:
        logger.error("❌ No training data found!")
        sys.exit(1)
    
    logger.info(f"\nClass distribution:")
    for cls, count in train_dataset.class_counts.items():
        logger.info(f"  {cls}: {count}")
    
    # Data loaders
    train_loader = DataLoader(
        train_dataset, batch_size=args.batch_size, 
        shuffle=True, num_workers=0
    )
    val_loader = DataLoader(
        val_dataset, batch_size=args.batch_size,
        shuffle=False, num_workers=0
    )
    
    # Create model
    if args.simple:
        logger.info("\n🏗️ Creating simple CNN...")
        model = create_simple_cnn(num_classes=5)
    else:
        logger.info("\n🏗️ Creating MobileNetV3-Small...")
        model = create_model(num_classes=5)
    
    # Class weights
    class_weights = None
    if not args.no_weights:
        class_weights = compute_class_weights(train_dataset)
        logger.info(f"Class weights: {class_weights.numpy().round(2)}")
    
    # Train
    trainer = Trainer(model, train_loader, val_loader, device, class_weights)
    history = trainer.train(args.epochs, args.output)
    
    # Final evaluation
    model.load_state_dict(torch.load(args.output, map_location=device))
    results = evaluate_model(model, val_loader, device, train_dataset.CLASS_NAMES)
    print_evaluation(results, train_dataset.CLASS_NAMES)
    
    # Save training info
    info_path = Path(args.output).with_suffix('.json')
    info = {
        "date": datetime.now().isoformat(),
        "epochs": args.epochs,
        "best_val_acc": trainer.best_val_acc,
        "class_names": train_dataset.CLASS_NAMES,
        "class_counts": train_dataset.class_counts,
        "history": history,
        "evaluation": results
    }
    with open(info_path, 'w') as f:
        json.dump(info, f, indent=2)
    
    logger.info(f"\n✅ Training complete!")
    logger.info(f"   Model: {args.output}")
    logger.info(f"   Info: {info_path}")
    logger.info(f"   Best val accuracy: {trainer.best_val_acc:.1f}%")


if __name__ == "__main__":
    main()
