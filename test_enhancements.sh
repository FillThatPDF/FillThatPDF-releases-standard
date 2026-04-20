#!/bin/bash
# Quick test script for the enhanced detection system
# Run this to verify all features are working

echo "=========================================="
echo "FillThatPDF v1.1 Enhancement Tests"
echo "=========================================="
echo ""

PDF_DIST="/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/FillThatPDF_Optimized_v11/python_dist"
TEST_PDF="/Users/36981/Desktop/PDFTest/PDFs to test/Static PDFs/1/53252_DTE_EEA_Field_Inspection_Report_v11.pdf"
OUTPUT_DIR="/Users/36981/Desktop/PDFTest/PDFs to test/Static PDFs/1"

cd "$PDF_DIST"

echo "Test 1: First Run (Creates Profile)"
echo "-------------------------------------"
python3 smart_fillable_v23.py "$TEST_PDF" "$OUTPUT_DIR/test_first_run.pdf" --skip-inspection --auto-calibrate 2>&1 | grep -E "(Profile saved|fields detected|SUMMARY)"
echo ""

echo "Test 2: Second Run (Loads Profile)"
echo "-------------------------------------"
python3 smart_fillable_v23.py "$TEST_PDF" "$OUTPUT_DIR/test_second_run.pdf" --skip-inspection --auto-calibrate 2>&1 | grep -E "(LOADING SAVED PDF PROFILE|GUARDRAILS|within expected)"
echo ""

echo "Test 3: Force Fresh Detection (--no-profile)"
echo "-------------------------------------"
python3 smart_fillable_v23.py "$TEST_PDF" "$OUTPUT_DIR/test_no_profile.pdf" --skip-inspection --auto-calibrate --no-profile 2>&1 | grep -E "(AUTO-CALIBRATION|Applying auto-calibrated)"
echo ""

echo "Test 4: Check Profile File"
echo "-------------------------------------"
ls -lh ~/.FillThatPDF/profiles/ | grep 53252
echo ""

echo "Test 5: List All Profiles"
echo "-------------------------------------"
ls -1 ~/.FillThatPDF/profiles/ | wc -l | xargs echo "Total profiles:"
echo ""

echo "=========================================="
echo "All tests complete!"
echo "=========================================="