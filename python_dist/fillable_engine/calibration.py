"""
Auto-Calibration Module
=======================

Analyzes PageModel data from Phase 1 to automatically determine optimal
detection settings before Phase 2 runs.

Ported from v23's analyze_pdf_structure() / _calculate_optimal_settings().
Unlike v23 which re-opened the PDF with pdfplumber, v24 reuses the
PageModel objects that Phase 1 already built — no extra I/O.

Settings calibrated:
  - field_height            (default 14)
  - min_field_width         (from cell analysis)
  - min_field_height        (from cell analysis)
  - min_empty_box_width     (default 100)
  - min_empty_box_height    (default 15)
  - checkbox_size_min/max   (from rect analysis)
  - page_edge_margin        (from cell positions)
  - min_underscore_length   (from cell widths)
  - field_gap_tolerance     (from form density)
  - skip_cmyk_cyan_image_sampling  (nuance flag)
  - hash_descriptive_mode         (nuance flag)
  - fullwidth_linepair_detection  (nuance flag)
  - aggressive_static_filtering   (monetary columns)
  - create_signature_fields       (signature lines)
"""

import re
from typing import Dict, List, Any

from .models import PageModel


def auto_calibrate(pages: List[PageModel], settings: Dict[str, Any]) -> Dict[str, Any]:
    """
    Analyze page structure and return a merged settings dict with
    calibrated values.

    Checkbox size settings are ALWAYS applied per-PDF (overriding user
    defaults) because each PDF has different checkbox geometry and the
    whole point of auto-calibration is to adapt.  Other settings only
    fill in keys the user hasn't set.

    Args:
        pages:    PageModel list from Phase 1.
        settings: The current pipeline settings (may contain user overrides).

    Returns:
        The same *settings* dict, updated in-place with calibrated values.
    """
    print(f"\n{'='*60}")
    print("AUTO-CALIBRATION: Analyzing PDF Structure...")
    print(f"{'='*60}\n")

    analysis = _analyze_structure(pages)
    recommendations = _calculate_optimal_settings(analysis)

    # Keys that ALWAYS override user settings (per-PDF calibration)
    _ALWAYS_OVERRIDE = {'checkbox_size_min', 'checkbox_size_max'}

    applied = []
    for key, val in recommendations.items():
        if key in _ALWAYS_OVERRIDE:
            settings[key] = val
            applied.append(f"   {key} = {val}  (per-PDF override)")
        elif key not in settings:
            settings[key] = val
            applied.append(f"   {key} = {val}")

    if applied:
        print("Applied auto-calibrated settings:")
        for line in applied:
            print(line)
    else:
        print("   No new settings applied (all already set by user).")

    print(f"\n{'='*60}\n")
    return settings


# ---------------------------------------------------------------------------
#  Structure analysis — mirrors v23's analyze_pdf_structure()
# ---------------------------------------------------------------------------

def _analyze_structure(pages: List[PageModel]) -> Dict[str, Any]:
    """Walk every PageModel and collect structural statistics."""
    analysis: Dict[str, Any] = {
        'cell_widths': [],
        'cell_heights': [],
        'checkbox_sizes': [],
        'edge_distances': [],
        'rect_widths': [],
        'rect_heights': [],
        'has_tables': False,
        'has_checkboxes': False,
        'has_lines': False,
        'page_count': len(pages),
        'char_heights': [],
        # Nuance detection
        'has_cmyk_cyan_backgrounds': False,
        'has_hash_descriptive_labels': False,
        'has_fullwidth_linepairs': False,
        'cmyk_cyan_rects': [],
        'fullwidth_linepair_pages': [],
        # Content-aware
        'has_signature_lines': False,
        'has_label_value_pairs': False,
        'has_monetary_columns': False,
        'has_underscore_fields': False,
        'form_density': 'unknown',
        'detected_form_patterns': [],
    }

    total_labels = 0
    total_form_lines = 0

    for page in pages:
        page_num = page.page_num

        # --- Table cell analysis ---
        if page.tables:
            analysis['has_tables'] = True
            for table in page.tables:
                for cell in (table.cells or []):
                    if cell and len(cell) >= 4:
                        width = cell[2] - cell[0]
                        height = cell[3] - cell[1]
                        x_dist = min(cell[0], page.width - cell[2])
                        y_dist = min(cell[1], page.height - cell[3])
                        analysis['cell_widths'].append(width)
                        analysis['cell_heights'].append(height)
                        analysis['edge_distances'].append(min(x_dist, y_dist))

                # Detect "#" descriptive labels in table headers
                rows = table.rows if hasattr(table, 'rows') else []
                if rows and len(rows) >= 1:
                    for row in rows[:2]:
                        if hasattr(row, 'cells'):
                            for cell in row.cells:
                                if cell:
                                    txt = page.get_text_in_bbox(cell).lower().strip()
                                    if txt.startswith('#') and (' of ' in txt or txt.startswith('# of')):
                                        analysis['has_hash_descriptive_labels'] = True

        # --- Rectangle analysis ---
        for rect in (page.rects or []):
            w = rect.get('x1', 0) - rect.get('x0', 0)
            h = rect.get('y1', 0) - rect.get('y0', 0)
            if w <= 0 or h <= 0:
                continue
            analysis['rect_widths'].append(w)
            analysis['rect_heights'].append(h)

            # Potential checkbox: small, roughly square
            if 3 <= w <= 30 and 3 <= h <= 30 and abs(w - h) <= 4:
                analysis['checkbox_sizes'].append((w + h) / 2)
                analysis['has_checkboxes'] = True

            # CMYK cyan background detection
            if h > 60 and w > 200:
                color = rect.get('non_stroking_color', [])
                if isinstance(color, (int, float)):
                    color = [color]
                if len(color) == 4:
                    c, m, y_val, k = color
                    if c > 0.5 and k < 0.1:
                        analysis['has_cmyk_cyan_backgrounds'] = True
                        analysis['cmyk_cyan_rects'].append({
                            'page': page_num, 'y0': rect['y0'],
                            'y1': rect['y1'], 'height': h,
                        })

        # --- Full-width line pair detection ---
        fw_lines = []
        for line in (page.lines or []):
            x0 = float(line.get('x0', 0))
            y0 = float(line.get('top', line.get('y0', 0)))
            x1 = float(line.get('x1', 0))
            y1 = float(line.get('bottom', line.get('y1', 0)))
            if abs(y0 - y1) < 1 and (x1 - x0) > 500:
                fw_lines.append((y0 + y1) / 2)
        if len(fw_lines) >= 2:
            fw_lines.sort()
            for i in range(len(fw_lines) - 1):
                gap = fw_lines[i + 1] - fw_lines[i]
                if 20 < gap < 100:
                    analysis['has_fullwidth_linepairs'] = True
                    if page_num not in analysis['fullwidth_linepair_pages']:
                        analysis['fullwidth_linepair_pages'].append(page_num)

        # --- Lines existence ---
        if page.lines:
            analysis['has_lines'] = True

        # --- Word-level content analysis ---
        page_labels = 0
        page_underscores = 0
        page_monetary = 0

        for w in page.words:
            text = w.get('text', '').strip()
            if not text:
                continue

            # Labels (words ending with ':')
            if text.endswith(':') and len(text) > 2:
                page_labels += 1
                total_labels += 1
                analysis['has_label_value_pairs'] = True

            # Underscore fill areas
            if '____' in text:
                page_underscores += 1
                analysis['has_underscore_fields'] = True

            # Monetary values (static data, e.g. "$1,500.00")
            if re.match(r'^\$[\d,]+(\.\d{1,2})?$', text):
                page_monetary += 1
                analysis['has_monetary_columns'] = True

            # Signature patterns
            if text.lower().rstrip(':') in ('signature', 'sign'):
                analysis['has_signature_lines'] = True

        # Count horizontal form lines
        for line in (page.lines or []):
            lx0 = float(line.get('x0', 0))
            ly0 = float(line.get('top', line.get('y0', 0)))
            lx1 = float(line.get('x1', 0))
            ly1 = float(line.get('bottom', line.get('y1', 0)))
            if abs(ly0 - ly1) < 1 and (lx1 - lx0) > 40:
                total_form_lines += 1

        # Pattern summary per page
        if page_labels > 5 or page_underscores > 3:
            analysis['detected_form_patterns'].append(
                f"P{page_num+1}: Form page ({page_labels} labels, "
                f"{page_underscores} underscore fields)"
            )
        elif page_monetary > 3:
            analysis['detected_form_patterns'].append(
                f"P{page_num+1}: Data/monetary table ({page_monetary} monetary values)"
            )

    # Form density
    total_elements = total_labels + total_form_lines
    if total_elements > 30:
        analysis['form_density'] = 'dense'
    elif total_elements > 10:
        analysis['form_density'] = 'moderate'
    else:
        analysis['form_density'] = 'sparse'

    return analysis


# ---------------------------------------------------------------------------
#  Settings calculation — mirrors v23's _calculate_optimal_settings()
# ---------------------------------------------------------------------------

def _calculate_optimal_settings(analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Derive optimal detection settings from structural analysis."""
    settings: Dict[str, Any] = {}

    # --- Field size thresholds ---
    if analysis['cell_widths']:
        min_width = min(analysis['cell_widths'])
        avg_width = sum(analysis['cell_widths']) / len(analysis['cell_widths'])
        settings['min_field_width'] = max(5, int(min_width * 0.8))
        print(f"   Cell widths — min: {min_width:.1f}, avg: {avg_width:.1f} "
              f"-> min_field_width: {settings['min_field_width']}")

    if analysis['cell_heights']:
        min_height = min(analysis['cell_heights'])
        avg_height = sum(analysis['cell_heights']) / len(analysis['cell_heights'])
        settings['min_field_height'] = max(5, int(min_height * 0.8))
        print(f"   Cell heights — min: {min_height:.1f}, avg: {avg_height:.1f} "
              f"-> min_field_height: {settings['min_field_height']}")

    # --- Checkbox settings ---
    if analysis['checkbox_sizes']:
        sizes = sorted(analysis['checkbox_sizes'])
        n = len(sizes)
        median_cb = sizes[n // 2]
        # Use p5-p95 percentile range (captures the full spread of
        # checkbox sizes across all pages, not just the median cluster).
        p5 = sizes[max(0, int(n * 0.05))]
        p95 = sizes[min(n - 1, int(n * 0.95))]
        # Add 2pt padding on each side to tolerate minor variations
        settings['checkbox_size_min'] = max(3.5, round(p5 - 2, 1))
        settings['checkbox_size_max'] = round(p95 + 2, 1)
        # Safety: never narrower than default 5-28 range if PDF has
        # diverse checkbox sizes (e.g. 6pt on one page, 14pt on another).
        settings['checkbox_size_min'] = min(settings['checkbox_size_min'], 5)
        settings['checkbox_size_max'] = max(settings['checkbox_size_max'], 20)
        print(f"   Checkboxes — median: {median_cb:.1f}, p5: {p5:.1f}, "
              f"p95: {p95:.1f} -> range: "
              f"{settings['checkbox_size_min']}-{settings['checkbox_size_max']}")

    # --- Page edge margin ---
    if analysis['edge_distances']:
        min_edge = min(analysis['edge_distances'])
        settings['page_edge_margin'] = max(5, int(min_edge * 0.8))
        print(f"   Edge distances — min: {min_edge:.1f} "
              f"-> page_edge_margin: {settings['page_edge_margin']}")

    # --- Min underscore length ---
    if analysis['cell_widths']:
        avg_cell = sum(analysis['cell_widths']) / len(analysis['cell_widths'])
        settings['min_underscore_length'] = max(15, int(avg_cell * 0.5))

    # --- Nuance detection flags ---
    if analysis['has_cmyk_cyan_backgrounds']:
        settings['skip_cmyk_cyan_image_sampling'] = True
        print(f"   CMYK cyan backgrounds detected -> skip_cmyk_cyan_image_sampling")

    if analysis['has_hash_descriptive_labels']:
        settings['hash_descriptive_mode'] = True
        print(f"   Hash descriptive labels detected -> hash_descriptive_mode")

    if analysis['has_fullwidth_linepairs']:
        settings['fullwidth_linepair_detection'] = True
        print(f"   Full-width line pairs detected -> fullwidth_linepair_detection")

    # --- Content-aware flags ---
    if analysis.get('has_monetary_columns'):
        settings['aggressive_static_filtering'] = True
        print(f"   Monetary columns detected -> aggressive_static_filtering")

    if analysis.get('has_signature_lines'):
        settings['create_signature_fields'] = True
        print(f"   Signature lines detected -> create_signature_fields")

    # --- Form density ---
    form_density = analysis.get('form_density', 'unknown')
    if form_density == 'dense':
        settings['field_gap_tolerance'] = 3
        print(f"   Form density: DENSE -> field_gap_tolerance: 3")
    elif form_density == 'sparse':
        settings['field_gap_tolerance'] = 8
        print(f"   Form density: SPARSE -> field_gap_tolerance: 8")

    # --- Pattern summary ---
    if analysis.get('detected_form_patterns'):
        print(f"   Detected form patterns:")
        for pattern in analysis['detected_form_patterns']:
            print(f"      {pattern}")

    return settings
