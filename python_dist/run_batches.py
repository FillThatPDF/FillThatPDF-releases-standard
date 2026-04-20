import os
import subprocess
import sys

def run_batch(directory_name, batch_path):
    print(f"\n{'='*60}")
    print(f"PROCESSING {directory_name}")
    print(f"{'='*60}")
    
    files = [f for f in os.listdir(batch_path) if f.endswith('.pdf') and not f.endswith('_REPAIRED.pdf')]
    files.sort()
    
    success_count = 0
    fail_count = 0
    
    for i, filename in enumerate(files):
        input_path = os.path.join(batch_path, filename)
        output_filename = filename.replace('.pdf', '_REPAIRED.pdf')
        output_path = os.path.join(batch_path, output_filename)
        
        print(f"\n[{i+1}/{len(files)}] Processing: {filename}")
        
        try:
            # Run the repair script
            cmd = ["python3", "smart_fillable_v21.py", input_path, output_path]
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode == 0:
                print(f"   ✅ SUCCESS: {output_filename}")
                success_count += 1
            else:
                print(f"   ❌ FAILED: {filename}")
                print(f"      Error: {result.stderr.strip()[:200]}...")
                fail_count += 1
        except Exception as e:
            print(f"   ❌ EXCEPTION: {filename}")
            print(f"      {str(e)}")
            fail_count += 1
            
    return success_count, fail_count

def main():
    script_dir = "/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/FillThatPDF_Optimized_v5/python_dist"
    os.chdir(script_dir)
    
    base_path = "/Users/36981/Desktop/PDFTest/PDFs to test"
    batches = [
        ("Batch 1 Forms", os.path.join(base_path, "Batch 1 Forms")),
        ("Batch 2 Forms", os.path.join(base_path, "Batch 2 Forms"))
    ]
    
    total_success = 0
    total_fail = 0
    
    for name, path in batches:
        s, f = run_batch(name, path)
        total_success += s
        total_fail += f
        
    print(f"\n{'='*60}")
    print(f"FINAL SUMMARY")
    print(f"   Total Success: {total_success}")
    print(f"   Total Failures: {total_fail}")
    print(f"{'='*60}")

if __name__ == "__main__":
    main()
