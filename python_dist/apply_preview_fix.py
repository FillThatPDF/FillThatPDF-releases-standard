#!/usr/bin/env python3
"""
Apply Preview.app fix to make filled PDFs display correctly in Acrobat.
Opens the PDF in Preview.app, saves it, then closes it.
This regenerates field appearances so they display in Acrobat.
"""

import sys
import os
import subprocess
import time

def apply_preview_fix(pdf_path):
    """Apply the Preview.app fix by opening and saving in Preview."""
    print(f"🔧 Applying Preview.app fix to: {pdf_path}")
    
    try:
        # Get file modification time before
        mtime_before = os.path.getmtime(pdf_path)
        
        # AppleScript to open in Preview, save, and close
        applescript = f'''
        tell application "Preview"
            open POSIX file "{pdf_path}"
            delay 1
            save front document
            delay 0.5
            close front document
        end tell
        '''
        
        subprocess.run(['osascript', '-e', applescript], 
                      capture_output=True, timeout=30)
        
        # Wait for file to be updated
        max_wait = 10
        waited = 0
        while waited < max_wait:
            time.sleep(0.5)
            waited += 0.5
            mtime_after = os.path.getmtime(pdf_path)
            if mtime_after > mtime_before:
                break
        
        if os.path.getmtime(pdf_path) > mtime_before:
            print("   ✅ Preview.app fix applied successfully")
        else:
            print("   ⚠️ File may not have been updated")
            
    except subprocess.TimeoutExpired:
        print("   ⚠️ Preview.app timed out")
        # Try to close Preview
        subprocess.run(['osascript', '-e', 'tell application "Preview" to quit'], 
                      capture_output=True)
    except Exception as e:
        print(f"   ❌ Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: apply_preview_fix.py <pdf_path>")
        sys.exit(1)
    
    pdf_path = sys.argv[1]
    
    if not os.path.exists(pdf_path):
        print(f"Error: File not found: {pdf_path}")
        sys.exit(1)
    
    apply_preview_fix(pdf_path)
