#!/bin/bash
set -e
cd "/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/FillThatPDF_Optimized_v19"
gh release upload v1.0.0 \
  --repo FillThatPDF/FillThatPDF-releases-standard \
  --clobber \
  "dist/Fill That PDF!-1.0.0-universal.dmg#fill-that-pdf-1.0.0.dmg" \
  "dist/Fill That PDF!-1.0.0-universal.dmg.blockmap#fill-that-pdf-1.0.0.dmg.blockmap" \
  "dist/Fill That PDF!-1.0.0-universal-mac.zip#fill-that-pdf-1.0.0-universal-mac.zip" \
  "dist/Fill That PDF!-1.0.0-universal-mac.zip.blockmap#fill-that-pdf-1.0.0-universal-mac.zip.blockmap" \
  "dist/latest-mac.yml#latest-mac.yml"
echo "STD_ALL_DONE"
