#!/bin/bash
set -e
BASE="/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2"
VER="1.1.3"
cd "$BASE/FillThatPDF_v${VER}"

echo "=== Setting up temp git context ==="
export GIT_DIR="$BASE/FillThatPDF_v${VER}/.git_push"
export GIT_WORK_TREE="$BASE/FillThatPDF_v${VER}"

if [ -d "$GIT_DIR" ]; then
  rm -rf "$GIT_DIR"
fi

git init
git config user.email "alexthebritgordon@gmail.com"
git config user.name "Alex Gordon"
git branch -m main

# Check if remotes exist before adding
git remote add pro https://github.com/FillThatPDF/FillThatPDF-releases-pro.git 2>/dev/null || true
git remote add standard https://github.com/FillThatPDF/FillThatPDF-releases-standard.git 2>/dev/null || true

echo "=== Staging files ==="
git add -A
git commit -m "v${VER}: Hyperlinks Manager - resolved named destinations, fixed off-by-one page display, fixed Go to Selected scrolling"

echo "=== Pushing to PRO repo ==="
git push pro main --force

echo "=== Pushing to Standard repo ==="
git push standard main --force

echo "=== Cleanup ==="
unset GIT_DIR GIT_WORK_TREE
rm -rf "$BASE/FillThatPDF_v${VER}/.git_push"

echo ""
echo "=== Triggering Windows builds ==="
gh workflow run "Build Windows Installer" \
  --repo FillThatPDF/FillThatPDF-releases-pro \
  -f version_type=pro

gh workflow run "Build Windows Installer" \
  --repo FillThatPDF/FillThatPDF-releases-standard \
  -f version_type=standard

echo "✅ Windows builds triggered!"
echo ""
echo "Monitor with:"
echo "  gh run list --workflow='Build Windows Installer' --repo FillThatPDF/FillThatPDF-releases-pro --limit 3"
echo "  gh run list --workflow='Build Windows Installer' --repo FillThatPDF/FillThatPDF-releases-standard --limit 3"
