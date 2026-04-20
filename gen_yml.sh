#!/bin/bash
set -e
cd "/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/FillThatPDF_v1.1.3"
VER="1.1.3"

for VARIANT in "FillThatPDF-PRO" "FillThatPDF"; do
  echo "Generating YAML for $VARIANT..."
  
  ARM_ZIP="dist/${VARIANT}-${VER}-arm64.zip"
  ARM_DMG="dist/${VARIANT}-${VER}-arm64.dmg"
  X64_ZIP="dist/${VARIANT}-${VER}-x64.zip"
  X64_DMG="dist/${VARIANT}-${VER}-x64.dmg"

  ARM_ZIP_SHA=$(openssl dgst -sha512 -binary "$ARM_ZIP" | base64)
  ARM_DMG_SHA=$(openssl dgst -sha512 -binary "$ARM_DMG" | base64)
  X64_ZIP_SHA=$(openssl dgst -sha512 -binary "$X64_ZIP" | base64)
  X64_DMG_SHA=$(openssl dgst -sha512 -binary "$X64_DMG" | base64)

  ARM_ZIP_SZ=$(stat -f%z "$ARM_ZIP")
  ARM_DMG_SZ=$(stat -f%z "$ARM_DMG")
  X64_ZIP_SZ=$(stat -f%z "$X64_ZIP")
  X64_DMG_SZ=$(stat -f%z "$X64_DMG")
  
  NOW=$(date -u +%Y-%m-%dT%H:%M:%S.000Z)

  cat > "dist/latest-mac-${VARIANT}.yml" <<-ENDYML
version: ${VER}
files:
  - url: ${VARIANT}-${VER}-arm64.zip
    sha512: ${ARM_ZIP_SHA}
    size: ${ARM_ZIP_SZ}
    arch: arm64
  - url: ${VARIANT}-${VER}-arm64.dmg
    sha512: ${ARM_DMG_SHA}
    size: ${ARM_DMG_SZ}
    arch: arm64
  - url: ${VARIANT}-${VER}-x64.zip
    sha512: ${X64_ZIP_SHA}
    size: ${X64_ZIP_SZ}
    arch: x64
  - url: ${VARIANT}-${VER}-x64.dmg
    sha512: ${X64_DMG_SHA}
    size: ${X64_DMG_SZ}
    arch: x64
path: ${VARIANT}-${VER}-arm64.zip
sha512: ${ARM_ZIP_SHA}
releaseDate: '${NOW}'
ENDYML
  echo "  ✅ dist/latest-mac-${VARIANT}.yml"
done
echo "Done!"
