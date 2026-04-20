import subprocess
import json
import re

def get_fields(padding):
    cmd = [
        "./pyenv/bin/python3", "python_dist/smart_fillable_v23.py",
        "/Users/36981/Desktop/Studio_Time_Sheet.pdf", "/dev/null",
        "--settings", json.dumps({"table_cell_padding": padding, "verbose_logging": True})
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    # Extract field info from [DEBUG] or summary logs if possible, 
    # but actual text_fields isn't dumped to stdout easily.
    # I'll rely on conflict logs and creation logs.
    fields = []
    # Grep for creation logs: "Page 1: Empty box '...' (width x height) label='...'"
    # Wait, the creation log for individual fields isn't always there.
    # I'll look for "[Conflict Resolved]" logs to see what was removed.
    return result.stdout

s2 = get_fields(2)
s0 = get_fields(0)

with open("out2.txt", "w") as f: f.write(s2)
with open("out0.txt", "w") as f: f.write(s0)

print("Logs dumped to out2.txt and out0.txt")
