#!/bin/bash
set -e
cd "/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/FillThatPDF_Optimized_v19"
gh release upload v1.0.0 \
  --repo FillThatPDF/FillThatPDF-releases-pro \
  --clobber \
  "dist/Fill That PDF! PRO-1.0.0-universal.dmg#fill-that-pdf-pro-1.0.0.dmg"
echo "PRO_DMG_DONE"
