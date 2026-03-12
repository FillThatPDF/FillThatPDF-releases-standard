#!/usr/bin/env python3
"""
Apply AcroForm NeedAppearances fix to make PDFs display correctly in Acrobat.
Uses Preview.app save trick to regenerate appearance streams properly.
"""

import sys
import os
import subprocess
import time

def apply_preview_save(pdf_path: str) -> bool:
    """
    Open PDF in Preview, save, and close to fix Acrobat compatibility.
    This regenerates appearance streams in a way Acrobat accepts.
    """
    abs_path = os.path.abspath(pdf_path)
    
    applescript = f'''
    tell application "Preview"
        activate
        open POSIX file "{abs_path}"
        delay 1.5
    end tell
    
    tell application "System Events"
        tell process "Preview"
            keystroke "s" using command down
            delay 1
        end tell
    end tell
    
    tell application "Preview"
        delay 0.5
        close front window
    end tell
    '''
    
    try:
        result = subprocess.run(
            ['osascript', '-e', applescript],
            capture_output=True,
            text=True,
            timeout=20
        )
        time.sleep(0.5)
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print("   ⚠️ Preview save timed out")
        return False
    except Exception as e:
        print(f"   ⚠️ Preview save error: {e}")
        return False


def apply_acroform_fix(pdf_path):
    """Apply the Preview save trick to fix Acrobat compatibility."""
    print(f"🔧 Applying Acrobat fix to: {pdf_path}")
    
    # Use Preview save trick - most reliable method
    print("   🔄 Opening in Preview to regenerate appearances...")
    if apply_preview_save(pdf_path):
        print("   ✅ Preview save complete - PDF ready for Acrobat!")
        return True
    else:
        # Fallback to NeedAppearances flag
        print("   ⚠️ Preview save failed, trying NeedAppearances fallback...")
        try:
            import pikepdf
            
            pdf = pikepdf.Pdf.open(pdf_path, allow_overwriting_input=True)
            
            if '/AcroForm' in pdf.Root:
                acroform = pdf.Root['/AcroForm']
                acroform['/NeedAppearances'] = True
                pdf.save(pdf_path)
                print("   ✅ NeedAppearances flag set (fallback)")
            
        except Exception as e:
            print(f"   ❌ Error: {e}")
            return False
    
    return True

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: apply_acroform_fix.py <pdf_path>")
        sys.exit(1)
    
    pdf_path = sys.argv[1]
    
    if not os.path.exists(pdf_path):
        print(f"Error: File not found: {pdf_path}")
        sys.exit(1)
    
    apply_acroform_fix(pdf_path)
