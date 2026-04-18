#!/bin/bash
set -e
cd "/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/FillThatPDF_v1.1.3"
VER="1.1.3"
DIST="dist"

echo "=== Creating PRO release ==="
gh release create "v${VER}" \
  --repo FillThatPDF/FillThatPDF-releases-pro \
  --title "v${VER}" \
  --notes "v1.1.3: Hyperlinks Manager improvements - resolved named destinations to page numbers, fixed Go to Selected scrolling, fixed off-by-one page display bug." \
  "$DIST/FillThatPDF-PRO-${VER}-arm64.dmg" \
  "$DIST/FillThatPDF-PRO-${VER}-arm64.zip" \
  "$DIST/FillThatPDF-PRO-${VER}-x64.dmg" \
  "$DIST/FillThatPDF-PRO-${VER}-x64.zip" \
  "$DIST/FillThatPDF-PRO-${VER}-arm64.dmg.blockmap" \
  "$DIST/FillThatPDF-PRO-${VER}-arm64.zip.blockmap" \
  "$DIST/FillThatPDF-PRO-${VER}-x64.dmg.blockmap" \
  "$DIST/FillThatPDF-PRO-${VER}-x64.zip.blockmap"

echo "Uploading PRO latest-mac.yml..."
cp "$DIST/latest-mac-FillThatPDF-PRO.yml" /tmp/latest-mac.yml
gh release upload "v${VER}" /tmp/latest-mac.yml --repo FillThatPDF/FillThatPDF-releases-pro --clobber
echo "✅ PRO release done"

echo ""
echo "=== Creating Standard release ==="
gh release create "v${VER}" \
  --repo FillThatPDF/FillThatPDF-releases-standard \
  --title "v${VER}" \
  --notes "v1.1.3: Hyperlinks Manager improvements - resolved named destinations to page numbers, fixed Go to Selected scrolling, fixed off-by-one page display bug." \
  "$DIST/FillThatPDF-${VER}-arm64.dmg" \
  "$DIST/FillThatPDF-${VER}-arm64.zip" \
  "$DIST/FillThatPDF-${VER}-x64.dmg" \
  "$DIST/FillThatPDF-${VER}-x64.zip" \
  "$DIST/FillThatPDF-${VER}-arm64.dmg.blockmap" \
  "$DIST/FillThatPDF-${VER}-arm64.zip.blockmap" \
  "$DIST/FillThatPDF-${VER}-x64.dmg.blockmap" \
  "$DIST/FillThatPDF-${VER}-x64.zip.blockmap"

echo "Uploading Standard latest-mac.yml..."
cp "$DIST/latest-mac-FillThatPDF.yml" /tmp/latest-mac.yml
gh release upload "v${VER}" /tmp/latest-mac.yml --repo FillThatPDF/FillThatPDF-releases-standard --clobber
echo "✅ Standard release done"
echo ""
echo "🎉 All GitHub releases created!"
