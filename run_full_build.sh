#!/bin/bash

# Complete build script for FillThatPDF! PRO and Standard
# Runs the full build with code signing and notarization

LOG="/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/FillThatPDF_Optimized_v7/full_build.log"
cd "/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/FillThatPDF_Optimized_v7"

echo "=== Build started at $(date) ===" > "$LOG"

# Set Apple credentials
export APPLE_ID="alexthebritgordon@gmail.com"
export APPLE_APP_SPECIFIC_PASSWORD="bhrp-sljp-hlud-outa"
export APPLE_TEAM_ID="9VRW78GQHM"

echo "✅ Apple credentials set" >> "$LOG"
echo "   APPLE_ID: $APPLE_ID" >> "$LOG"
echo "   APPLE_TEAM_ID: $APPLE_TEAM_ID" >> "$LOG"

# Clean
rm -rf dist/
echo "✅ Cleaned dist folder" >> "$LOG"

# Build PRO
echo "" >> "$LOG"
echo "=== Building PRO version ===" >> "$LOG"
node build.js pro >> "$LOG" 2>&1
PRO_EXIT=$?
echo "PRO build exit code: $PRO_EXIT" >> "$LOG"

if [ $PRO_EXIT -eq 0 ]; then
    # Rename PRO DMG
    if [ -f "dist/Fill That PDF! PRO-1.0.0.dmg" ]; then
        mv "dist/Fill That PDF! PRO-1.0.0.dmg" "dist/FillThatPDF_PRO_v1.0.0.dmg"
        echo "✅ PRO DMG renamed to FillThatPDF_PRO_v1.0.0.dmg" >> "$LOG"
    fi
fi

# Clean intermediate files but keep DMG
rm -rf dist/*.app dist/*.blockmap dist/mac* dist/.icon-icns 2>/dev/null

# Build Standard
echo "" >> "$LOG"
echo "=== Building Standard version ===" >> "$LOG"
node build.js standard >> "$LOG" 2>&1
STD_EXIT=$?
echo "Standard build exit code: $STD_EXIT" >> "$LOG"

if [ $STD_EXIT -eq 0 ]; then
    # Rename Standard DMG
    if [ -f "dist/Fill That PDF!-1.0.0.dmg" ]; then
        mv "dist/Fill That PDF!-1.0.0.dmg" "dist/FillThatPDF_Standard_v1.0.0.dmg"
        echo "✅ Standard DMG renamed to FillThatPDF_Standard_v1.0.0.dmg" >> "$LOG"
    fi
fi

# Summary
echo "" >> "$LOG"
echo "=== Build Complete at $(date) ===" >> "$LOG"
echo "Output files:" >> "$LOG"
ls -lh dist/*.dmg >> "$LOG" 2>&1

echo "BUILD_COMPLETE" >> "$LOG"
