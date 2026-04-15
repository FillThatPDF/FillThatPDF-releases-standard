#!/bin/bash
set -e

# Configuration
# Configuration
APP_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PYTHON_DIST="$APP_DIR/python_dist"
DIST_ARM64="$PYTHON_DIST/dist_arm64"
DIST_X64="$PYTHON_DIST/dist_x64"
DIST_UNIVERSAL="$PYTHON_DIST/dist"

echo "🚀 Starting Universal Binary Rebuild..."
cd "$APP_DIR"

# 1. ARM64 BUILD (Native)
echo "----------------------------------------------------------------"
echo "🛠️  Building ARM64 Binaries..."
# Ensure local pyenv exists
if [ ! -d "pyenv" ]; then
    echo "   Creating ARM64 venv (pyenv)..."
    python3 -m venv pyenv
fi
./pyenv/bin/python3 -m pip install --upgrade pip > /dev/null
./pyenv/bin/python3 -m pip install pyinstaller pdfplumber pikepdf pdf2image Pillow > /dev/null

# Clean previous
rm -rf "$DIST_ARM64"
mkdir -p "$DIST_ARM64"

# Build all scripts
SCRIPTS="smart_fillable fill_pdf_v3 extract_fields modify_fields garbage_field_cleanup auto_rename_all auto_name_field fix_checkbox_appearances apply_acroform_fix"

for script in $SCRIPTS; do
    echo "   Running PyInstaller for $script (ARM64)..."
    ./pyenv/bin/pyinstaller --clean --noconfirm --distpath "$DIST_ARM64" --workpath "$PYTHON_DIST/build/arm64" "$PYTHON_DIST/$script.spec" > /dev/null
done

# 2. X64 BUILD (Rosetta)
echo "----------------------------------------------------------------"
echo "🛠️  Building X64 Binaries..."
if [ ! -d "venv_x64" ]; then
    echo "   Creating x64 virtual environment..."
    arch -x86_64 /usr/bin/python3 -m venv venv_x64
    echo "   Installing dependencies in x64 venv..."
    # Determine pip path - /usr/bin/python3 venv usually has pip
    arch -x86_64 ./venv_x64/bin/python3 -m pip install --upgrade pip > /dev/null
    arch -x86_64 ./venv_x64/bin/python3 -m pip install pdfplumber pikepdf pdf2image Pillow pyinstaller > /dev/null
else 
    # Just ensure pyinstaller is there
    echo "   Using existing venv_x64..."
    arch -x86_64 ./venv_x64/bin/python3 -m pip install pyinstaller pdfplumber pikepdf pdf2image Pillow > /dev/null
fi

# Clean previous
rm -rf "$DIST_X64"
mkdir -p "$DIST_X64"

for script in $SCRIPTS; do
    echo "   Running PyInstaller for $script (X64)..."
    # Use _x64.spec if it exists, otherwise use base spec
    if [ -f "$PYTHON_DIST/${script}_x64.spec" ]; then
        SPEC_FILE="$PYTHON_DIST/${script}_x64.spec"
    else
        SPEC_FILE="$PYTHON_DIST/${script}.spec"
    fi
    arch -x86_64 ./venv_x64/bin/pyinstaller --clean --noconfirm --distpath "$DIST_X64" --workpath "$PYTHON_DIST/build/x64" "$SPEC_FILE" > /dev/null
    
    # Rename binary if it has _x64 suffix, so main.js can find it (it expects "smart_fillable", not "smart_fillable_x64")
    if [ -f "$DIST_X64/${script}_x64" ]; then
        mv "$DIST_X64/${script}_x64" "$DIST_X64/${script}"
    fi
done

# 3. LIPO (Merge)
echo "----------------------------------------------------------------"
echo "🔗 Creating Universal Binaries (Lipo)..."
mkdir -p "$DIST_UNIVERSAL"

# Map script names to binary names (basename without .py)
for script in $SCRIPTS; do
    name="$script"
    
    # Map back to target names if needed (though specs use target names)
    target_name="$name"
    
    echo "   Merging $name -> $target_name..."
    
    # PyInstaller with x64 spec might name it ${name}_x64
    ARM_BIN="$DIST_ARM64/$name"
    X64_BIN="$DIST_X64/${name}_x64"
    
    # If _x64 suffix not found, check without it
    if [ ! -f "$X64_BIN" ]; then
        X64_BIN="$DIST_X64/$name"
    fi
    
    # Check source files exist
    if [ -f "$ARM_BIN" ] && [ -f "$X64_BIN" ]; then
        lipo -create -output "$DIST_UNIVERSAL/$target_name" "$ARM_BIN" "$X64_BIN"
        chmod +x "$DIST_UNIVERSAL/$target_name"
        echo "     ✅ Created $target_name (Universal)"
    else
        echo "     ❌ Error: Missing source binaries for $name"
        echo "        ARM_BIN: $ARM_BIN (exists: $([ -f "$ARM_BIN" ] && echo "YES" || echo "NO"))"
        echo "        X64_BIN: $X64_BIN (exists: $([ -f "$X64_BIN" ] && echo "YES" || echo "NO"))"
    fi
done

echo "----------------------------------------------------------------"
echo "🎉 Universal Rebuild Complete!"
