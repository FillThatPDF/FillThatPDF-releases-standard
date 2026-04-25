#!/bin/bash

# Fill That PDF! - Build Script
# This script builds the complete macOS app and DMG installer

echo "🚀 Building Fill That PDF!"
echo "=========================="

cd "$(dirname "$0")"

# Step 1: Install npm dependencies
echo ""
echo "📦 Step 1: Installing npm dependencies..."
npm install

if [ $? -ne 0 ]; then
    echo "❌ npm install failed!"
    exit 1
fi

# Step 2: Create Python executable using PyInstaller
echo ""
echo "🐍 Step 2: Creating Python executable..."

# Activate virtual environment
VENV_PATH=".venv"
if [ -d "$VENV_PATH" ]; then
    echo "   Activating virtual environment..."
    source "$VENV_PATH/bin/activate"
fi

# Check if PyInstaller is installed
if ! command -v pyinstaller &> /dev/null; then
    echo "   Installing PyInstaller..."
    pip install pyinstaller
fi

# Create python_dist directory
mkdir -p python_dist

# Build the Python script as a standalone executable
pyinstaller --noconfirm --onefile --windowed --name "smart_fillable_v21" --add-data "$(pwd)/python_dist:python_dist" \
    --specpath build_python \
    --clean \
    python_dist/smart_fillable_v21.py

if [ $? -ne 0 ]; then
    echo "❌ PyInstaller build failed!"
    exit 1
fi

echo "   ✅ Python executable created"

# Clean up PyInstaller build files
rm -rf build_python

# Step 3: Build the Electron app
echo ""
echo "⚡ Step 3: Building Electron app..."
npm run build:mac

if [ $? -ne 0 ]; then
    echo "❌ Electron build failed!"
    exit 1
fi

echo ""
echo "✅ Build complete!"
echo ""
echo "📁 Output locations:"
echo "   - App: dist/mac/Fill That PDF!.app"
echo "   - DMG: dist/Fill That PDF!-1.0.0.dmg"
echo ""
echo "To install, open the DMG and drag the app to Applications."
