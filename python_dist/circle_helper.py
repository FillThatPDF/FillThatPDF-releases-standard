import pikepdf
import math

def create_vector_circle_stream(width: float, height: float) -> bytes:
    # Draw a solid circle centered in the rect
    # Center
    cx, cy = width / 2.0, height / 2.0
    # Radius (leave some padding)
    r = min(width, height) * 0.25
    
    # Circle approximation using 4 Bezier curves
    # k = 0.5522847498 * r
    k = 0.55228 * r
    
    # PDF Stream:
    # q (save)
    # 0 0 0 rg (black fill)
    # x+r y m (move to right middle)
    # ... curves ...
    # f (fill)
    # Q (restore)
    
    cmds = [
        "q",
        "0 0 0 rg", # Black color
        f"{cx+r:.2f} {cy:.2f} m",
        f"{cx+r:.2f} {cy+k:.2f} {cx+k:.2f} {cy+r:.2f} {cx:.2f} {cy+r:.2f} c",
        f"{cx-k:.2f} {cy+r:.2f} {cx-r:.2f} {cy+k:.2f} {cx-r:.2f} {cy:.2f} c",
        f"{cx-r:.2f} {cy-k:.2f} {cx-k:.2f} {cy-r:.2f} {cx:.2f} {cy-r:.2f} c",
        f"{cx+k:.2f} {cy-r:.2f} {cx+r:.2f} {cy-k:.2f} {cx+r:.2f} {cy:.2f} c",
        "f",
        "Q"
    ]
    return " ".join(cmds).encode('latin-1')
