# Fill That PDF!

A macOS app that creates fillable PDFs and test-fills them with dummy data.

## Features

- **Make Fillable** - Converts any PDF into a fillable form with text fields, checkboxes, and radio buttons
- **Test Fill** - Fills a fillable PDF with realistic dummy data for testing
- **Make Fillable + Test Fill** - Does both in one click!

## Quick Start (Development)

1. Install dependencies:
   ```bash
   cd FillThatPDF
   npm install
   ```

2. Run the app in development mode:
   ```bash
   npm start
   ```

## Building the App

To create a distributable DMG installer:

```bash
chmod +x build.sh
./build.sh
```

This will:
1. Install npm dependencies
2. Bundle the Python script as a standalone executable
3. Build the Electron app
4. Create a DMG installer

## Requirements

- Node.js 18+
- Python 3.9+
- PyInstaller (`pip3 install pyinstaller`)

### Python Dependencies (for smart_fillable_v12.py)

```bash
pip3 install pdfplumber pikepdf pypdf Pillow reportlab
```

## File Structure

```
FillThatPDF/
├── main.js          # Electron main process
├── renderer.js      # UI logic
├── index.html       # App interface
├── package.json     # App configuration
├── build.sh         # Build script
├── assets/          # Icons and images
└── python_dist/     # Bundled Python executable (created during build)
```

## Usage

1. **Select Input PDF** - Click "Browse" to choose a PDF file (or drag & drop)
2. **Select Output Folder** (optional) - Choose where to save the result
3. **Click a button**:
   - 🚀 **Make Fillable + Test Fill** - Creates fillable PDF and fills it with test data
   - 📝 **Make Fillable Only** - Just creates the fillable PDF
   - ✏️ **Test Fill Only** - Fills an already-fillable PDF with test data

## Notes

- Output files are named automatically:
  - `filename_fillable.pdf` - The fillable version
  - `filename_fillable_FILLED.pdf` - The test-filled version
- If no output folder is selected, files are saved in the same folder as the input
