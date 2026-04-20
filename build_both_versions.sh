#!/bin/bash

# Build both PRO and Standard versions with code signing and notarization
# FillThatPDF! - Complete Build Script

echo "🚀 Building Fill That PDF! - PRO and Standard Versions"
echo "========================================================"
echo ""

# Set Apple credentials for code signing and notarization
export APPLE_ID="alexthebritgordon@gmail.com"
export APPLE_APP_SPECIFIC_PASSWORD="bhrp-sljp-hlud-outa"
export APPLE_APP_PASSWORD="bhrp-sljp-hlud-outa"
export APPLE_TEAM_ID="9VRW78GQHM"

echo "✅ Apple Developer credentials configured"
echo "   Email: $APPLE_ID"
echo "   Team ID: $APPLE_TEAM_ID"
echo ""

cd "$(dirname "$0")"

# Step 1: Install npm dependencies
echo "📦 Step 1: Installing npm dependencies..."
npm install

if [ $? -ne 0 ]; then
    echo "❌ npm install failed!"
    exit 1
fi

echo "✅ npm dependencies installed"
echo ""

# Step 2: Clean previous builds
echo "🧹 Step 2: Cleaning previous builds..."
rm -rf dist/
mkdir -p dist/

echo "✅ Build directory cleaned"
echo ""

# Step 3: Build PRO version
echo "🔨 Step 3: Building PRO version..."
echo "=================================="
node build.js pro > build_log_pro.txt 2>&1

if [ $? -ne 0 ]; then
    echo "❌ PRO build failed! Check build_log_pro.txt for details"
    cat build_log_pro.txt
    exit 1
fi

# Rename PRO DMG
if [ -f "dist/Fill That PDF! PRO-1.0.0.dmg" ]; then
    mv "dist/Fill That PDF! PRO-1.0.0.dmg" "dist/FillThatPDF_PRO_v1.0.0.dmg"
    echo "✅ PRO version built successfully"
    echo "   Location: dist/FillThatPDF_PRO_v1.0.0.dmg"
else
    echo "⚠️  PRO DMG not found at expected location"
    ls -la dist/
fi
echo ""

# Step 4: Clean for Standard build
echo "🧹 Step 4: Preparing for Standard build..."
rm -rf dist/*.app dist/*.blockmap dist/mac dist/mac-universal-unpacked
echo ""

# Step 5: Build Standard version
echo "🔨 Step 5: Building Standard version..."
echo "======================================="
node build.js standard > build_log_std.txt 2>&1

if [ $? -ne 0 ]; then
    echo "❌ Standard build failed! Check build_log_std.txt for details"
    cat build_log_std.txt
    exit 1
fi

# Rename Standard DMG
if [ -f "dist/Fill That PDF!-1.0.0.dmg" ]; then
    mv "dist/Fill That PDF!-1.0.0.dmg" "dist/FillThatPDF_Standard_v1.0.0.dmg"
    echo "✅ Standard version built successfully"
    echo "   Location: dist/FillThatPDF_Standard_v1.0.0.dmg"
else
    echo "⚠️  Standard DMG not found at expected location"
    ls -la dist/
fi
echo ""

# Step 6: Display results
echo "🎉 Build Complete!"
echo "=================="
echo ""
echo "📁 Output files:"
ls -lh dist/*.dmg 2>/dev/null || echo "   No DMG files found"
echo ""
echo "Build logs saved to:"
echo "   - build_log_pro.txt (PRO version)"
echo "   - build_log_std.txt (Standard version)"
echo ""
echo "Both versions have been:"
echo "   ✅ Code signed with your Apple Developer certificate"
echo "   ✅ Notarized with Apple (if credentials are valid)"
echo ""
echo "To install:"
echo "   1. Open the DMG file"
echo "   2. Drag 'Fill That PDF!' to Applications folder"
echo "   3. First launch: Right-click → Open (to bypass Gatekeeper)"
echo ""
