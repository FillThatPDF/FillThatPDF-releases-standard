#!/bin/bash
set -e

# Support multiple env var names for different electron-builder/notarize versions
export APPLE_ID="alexthebritgordon@gmail.com"
export APPLE_PASSWORD="japi-nxlq-bbug-pbxj"
export APPLE_ID_PASSWORD="japi-nxlq-bbug-pbxj"
export APPLE_APP_SPECIFIC_PASSWORD="japi-nxlq-bbug-pbxj"
export APPLE_TEAM_ID="9VRW78GQHM"

echo "🚀 Starting Full Repackage (Standard and PRO) with full credentials..."

echo "------------------------------------------------"
echo "📦 Building STANDARD version..."
rm -f build_standard.log
node build.js standard > build_standard.log 2>&1

echo "------------------------------------------------"
echo "📦 Building PRO version..."
rm -f build_pro.log
node build.js pro > build_pro.log 2>&1

echo "------------------------------------------------"
echo "✅ Repackage Complete!"
ls -lh dist/*.dmg
