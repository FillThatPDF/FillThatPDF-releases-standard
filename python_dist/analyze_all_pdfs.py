#!/usr/bin/env python3
"""
Comprehensive PDF Pattern Analysis Script
Scans all PDFs in a folder and extracts different field detection patterns:
- Horizontal lines (form underlines)
- Colon-separated labels
- Tables with empty cells
- Checkboxes
- Special label patterns
"""

import pdfplumber
import os
import json
from pathlib import Path
from collections import defaultdict

def analyze_pdf(pdf_path):
    """Analyze a single PDF for all field patterns."""
    results = {
        'filename': os.path.basename(pdf_path),
        'pages': [],
        'summary': {
            'total_pages': 0,
            'total_lines': 0,
            'total_tables': 0,
            'total_checkboxes': 0,
            'colon_labels': 0,
            'inline_label_rows': 0,
            'patterns_found': []
        }
    }
    
    try:
        pdf = pdfplumber.open(pdf_path)
        results['summary']['total_pages'] = len(pdf.pages)
        
        for page_num, page in enumerate(pdf.pages):
            page_data = {
                'page_num': page_num + 1,
                'horizontal_lines': [],
                'tables': [],
                'colon_labels': [],
                'inline_keywords': [],
                'potential_checkboxes': [],
                'special_patterns': []
            }
            
            # 1. Extract horizontal lines (form underlines)
            if hasattr(page, 'lines') and page.lines:
                for line in page.lines:
                    x0, x1 = float(line.get('x0', 0)), float(line.get('x1', 0))
                    y0, y1 = float(line.get('y0', 0)), float(line.get('y1', 0))
                    width = abs(x1 - x0)
                    height = abs(y1 - y0)
                    
                    # Horizontal line (height near 0, sufficient width)
                    if height < 2 and width > 15:
                        page_data['horizontal_lines'].append({
                            'x0': round(x0, 1),
                            'y': round(float(line.get('top', y0)), 1),
                            'x1': round(x1, 1),
                            'width': round(width, 1)
                        })
                        results['summary']['total_lines'] += 1
            
            # Also check thin rectangles (some PDFs use these as underlines)
            if hasattr(page, 'rects') and page.rects:
                for rect in page.rects:
                    width = float(rect.get('width', 0))
                    height = float(rect.get('height', 0))
                    if height < 3 and width > 15:
                        page_data['horizontal_lines'].append({
                            'x0': round(float(rect.get('x0', 0)), 1),
                            'y': round(float(rect.get('top', 0)), 1),
                            'x1': round(float(rect.get('x1', 0)), 1),
                            'width': round(width, 1),
                            'source': 'rect'
                        })
            
            # 2. Extract tables
            tables = page.find_tables()
            for table in tables:
                table_info = {
                    'bbox': table.bbox,
                    'rows': len(table.cells) if hasattr(table, 'cells') else 0,
                    'cells_count': len(table.cells) if hasattr(table, 'cells') else 0
                }
                page_data['tables'].append(table_info)
                results['summary']['total_tables'] += 1
            
            # 3. Extract words with colon endings (labels)
            words = page.extract_words()
            inline_keywords = ['city', 'state', 'zip', 'phone', 'fax', 'email', 'county', 'date']
            
            for word in words:
                text = word['text'].strip()
                
                # Colon-ending labels
                if text.endswith(':') and len(text) > 1:
                    page_data['colon_labels'].append({
                        'text': text,
                        'x0': round(float(word['x0']), 1),
                        'y': round(float(word['top']), 1)
                    })
                    results['summary']['colon_labels'] += 1
                
                # Inline keywords (City, State, ZIP, etc.)
                text_lower = text.lower().replace(':', '')
                if text_lower in inline_keywords:
                    page_data['inline_keywords'].append({
                        'text': text,
                        'x0': round(float(word['x0']), 1),
                        'y': round(float(word['top']), 1)
                    })
            
            # 4. Look for checkbox patterns (small squares)
            if hasattr(page, 'rects') and page.rects:
                for rect in page.rects:
                    width = float(rect.get('width', 0))
                    height = float(rect.get('height', 0))
                    # Small square-ish shapes could be checkboxes
                    if 6 < width < 20 and 6 < height < 20 and abs(width - height) < 5:
                        page_data['potential_checkboxes'].append({
                            'x0': round(float(rect.get('x0', 0)), 1),
                            'y': round(float(rect.get('top', 0)), 1),
                            'size': round(width, 1)
                        })
                        results['summary']['total_checkboxes'] += 1
            
            # 5. Special patterns - look for specific text patterns
            page_text = page.extract_text() or ""
            special_patterns = [
                ('inline_multi_field', 'City' in page_text and 'State' in page_text and 'ZIP' in page_text),
                ('date_fields', 'Date:' in page_text or 'date:' in page_text),
                ('phone_email', 'Phone' in page_text and 'Email' in page_text),
                ('signature_line', 'Signature' in page_text),
                ('dwelling_units', 'dwelling units' in page_text.lower()),
                ('equipment_installed', 'Equipment Installed' in page_text),
                ('payee_name_address', 'Payee Name' in page_text or 'Name and Address' in page_text),
            ]
            
            for pattern_name, found in special_patterns:
                if found:
                    page_data['special_patterns'].append(pattern_name)
                    if pattern_name not in results['summary']['patterns_found']:
                        results['summary']['patterns_found'].append(pattern_name)
            
            # Count inline label rows
            if page_data['inline_keywords']:
                # Group by Y position
                y_positions = set(round(kw['y'] / 5) * 5 for kw in page_data['inline_keywords'])
                results['summary']['inline_label_rows'] += len(y_positions)
            
            results['pages'].append(page_data)
        
        pdf.close()
        
    except Exception as e:
        results['error'] = str(e)
    
    return results


def main():
    pdf_folder = "/Users/36981/Desktop/PDFs to test"
    output_file = "/Users/36981/Desktop/PDFTest/FILLABLE TESTING 2/FillThatPDF_Universal/python_dist/pdf_pattern_analysis.json"
    
    pdf_files = [f for f in os.listdir(pdf_folder) if f.endswith('.pdf')]
    
    all_results = {
        'analyzed_pdfs': [],
        'global_summary': {
            'total_pdfs': len(pdf_files),
            'all_patterns': set(),
            'pattern_counts': defaultdict(int)
        }
    }
    
    print(f"Analyzing {len(pdf_files)} PDFs...")
    print("=" * 60)
    
    for pdf_file in sorted(pdf_files):
        pdf_path = os.path.join(pdf_folder, pdf_file)
        print(f"\n📄 Analyzing: {pdf_file}")
        
        result = analyze_pdf(pdf_path)
        all_results['analyzed_pdfs'].append(result)
        
        # Print summary for this PDF
        summary = result['summary']
        print(f"   Pages: {summary['total_pages']}")
        print(f"   Lines: {summary['total_lines']}")
        print(f"   Tables: {summary['total_tables']}")
        print(f"   Colon labels: {summary['colon_labels']}")
        print(f"   Inline label rows: {summary['inline_label_rows']}")
        print(f"   Checkboxes: {summary['total_checkboxes']}")
        print(f"   Patterns: {', '.join(summary['patterns_found']) if summary['patterns_found'] else 'none'}")
        
        # Update global summary
        for pattern in summary['patterns_found']:
            all_results['global_summary']['all_patterns'].add(pattern)
            all_results['global_summary']['pattern_counts'][pattern] += 1
    
    # Convert sets to lists for JSON serialization
    all_results['global_summary']['all_patterns'] = list(all_results['global_summary']['all_patterns'])
    all_results['global_summary']['pattern_counts'] = dict(all_results['global_summary']['pattern_counts'])
    
    # Print global summary
    print("\n" + "=" * 60)
    print("GLOBAL SUMMARY")
    print("=" * 60)
    print(f"Total PDFs analyzed: {all_results['global_summary']['total_pdfs']}")
    print(f"All patterns found: {all_results['global_summary']['all_patterns']}")
    print("Pattern counts:")
    for pattern, count in sorted(all_results['global_summary']['pattern_counts'].items()):
        print(f"   {pattern}: {count} PDFs")
    
    # Save detailed results
    with open(output_file, 'w') as f:
        json.dump(all_results, f, indent=2)
    print(f"\nDetailed results saved to: {output_file}")


if __name__ == "__main__":
    main()
