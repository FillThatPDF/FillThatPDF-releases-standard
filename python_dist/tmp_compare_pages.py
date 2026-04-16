#!/usr/bin/env python3
"""Quick page comparison script."""
import pikepdf

gt = "/Users/36981/Desktop/PDFTest/PDFs to test/Fillable PDFs/55570_DTE_SEEL_Contractor_Onboarding_Packet_v21_Web_Release_Fillable.pdf"
det = "/Users/36981/Desktop/PDFTest/PDFs to test/Static PDFs/55570_Topological_Success_v14.pdf"

print("Comparing page counts and structures...\n")

with pikepdf.open(gt) as pdf1:
    with pikepdf.open(det) as pdf2:
        print(f"Ground Truth: {len(pdf1.pages)} pages")
        print(f"Detection:    {len(pdf2.pages)} pages")
        
        # Show field distribution
        print("\n--- Field Distribution ---")
        print(f"{'Page':<6} {'GT':>8} {'Det':>8}")
        
        for i in range(max(len(pdf1.pages), len(pdf2.pages))):
            gt_cnt = 0
            det_cnt = 0
            
            if i < len(pdf1.pages) and "/Annots" in pdf1.pages[i]:
                gt_cnt = sum(1 for a in pdf1.pages[i].Annots if a.get("/Subtype") == "/Widget")
            
            if i < len(pdf2.pages) and "/Annots" in pdf2.pages[i]:
                det_cnt = sum(1 for a in pdf2.pages[i].Annots if a.get("/Subtype") == "/Widget")
            
            if gt_cnt > 0 or det_cnt > 0:
                match = "✓" if gt_cnt == det_cnt else "❌"
                print(f"Page {i+1:<3} {gt_cnt:>8} {det_cnt:>8}  {match}")
