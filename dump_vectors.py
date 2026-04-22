
import pdfplumber
import sys

def dump_all_vectors(pdf_path, page_num):
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_num]
        print(f"--- Page {page_num+1} Vectors ---")
        
        vectors = []
        for l in page.lines:
            vectors.append({
                'type': 'line',
                'y0': float(l['top']),
                'y1': float(l['bottom']),
                'x0': float(l['x0']),
                'x1': float(l['x1']),
                'width': float(l.get('linewidth', 0))
            })
        
        for r in page.rects:
            vectors.append({
                'type': 'rect',
                'y0': float(r['top']),
                'y1': float(r['bottom']),
                'x0': float(r['x0']),
                'x1': float(r['x1']),
                'height': float(r['height']),
                'width': float(r['width'])
            })
            
        # Sort by Y
        vectors.sort(key=lambda x: (x['y0'], x['x0']))
        
        for v in vectors:
            if 450 < v['y0'] < 600:
                print(f"{v['type'].upper():4} y={v['y0']:.2f}-{v['y1']:.2f} x={v['x0']:.2f}-{v['x1']:.2f} h={v.get('height', v['y1']-v['y0']):.2f} w={v.get('width', v['x1']-v['x0']):.2f}")

if __name__ == "__main__":
    pdf_path = "46119_RECO_HVAC_Incentive_Claim_Form_v02.pdf"
    dump_all_vectors(pdf_path, 0)
