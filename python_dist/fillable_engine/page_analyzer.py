"""
Phase 1: Page Analysis

Builds immutable PageModel objects from a PDF file.
Handles all structural parsing: lines, rects, text, tables, header bars, exclusion zones.
This replaces _preload_structural_data, _detect_text_only_pages, _predetect_tally_sheets,
_detect_header_bars, _predetect_box_entry_areas, _predetect_image_upload_regions,
_detect_square_grid_regions from v23.
"""

import pdfplumber
from pathlib import Path
from collections import defaultdict
from typing import List, Dict, Optional, Tuple

from .models import PageModel
from .helpers import classify_lines, merge_collinear_h_segments

# ── Comprehensive Unicode checkbox/square/tick character set ──
# Compiled from Unicode specs, Wingdings/Webdings/ZapfDingbats mappings,
# and real-world PDF analysis. Kept as a module-level frozenset for O(1) lookup.
_UNICODE_CHECKBOX_CHARS = frozenset([
    # Ballot boxes
    '\u2610', '\u2611', '\u2612',                       # ☐ ☑ ☒
    '\u2BBD',                                            # BALLOT BOX WITH LIGHT X
    '\U0001F5F3', '\U0001F5F5', '\U0001F5F7', '\U0001F5F9',  # Emoji ballot variants
    # Check marks / tick marks
    '\u2713', '\u2714',                                  # ✓ ✔
    '\u2705',                                            # ✅ (emoji)
    '\U0001F5F8',                                        # Light check mark
    # X marks / cross marks
    '\u2715', '\u2716', '\u2717', '\u2718',              # ✕ ✖ ✗ ✘
    '\u274C', '\u274E',                                  # ❌ ❎
    '\u2613',                                            # ☓ saltire
    # Geometric squares
    '\u25A0', '\u25A1', '\u25A2', '\u25A3',             # ■ □ ▢ ▣
    '\u25AA', '\u25AB',                                  # ▪ ▫
    '\u25FB', '\u25FC', '\u25FD', '\u25FE',             # ◻ ◼ ◽ ◾
    '\u2B1A', '\u2B1B', '\u2B1C',                       # ⬚ ⬛ ⬜
    '\u2B1D', '\u2B1E',                                  # Very small squares
    # Squared operators (X/dot/plus inside box)
    '\u229E', '\u229F', '\u22A0', '\u22A1',             # ⊞ ⊟ ⊠ ⊡
    '\u2317',                                            # ⌗ viewdata square
    '\u27E4', '\u27E5',                                  # ⟤ ⟥ tick in square
    # Dingbat shadowed squares
    '\u274F', '\u2750', '\u2751', '\u2752',              # ❏ ❐ ❑ ❒
    # Emoji square buttons
    '\U0001F532', '\U0001F533',                          # 🔲 🔳
    # Combining enclosing characters (used as standalone boxes in some PDFs)
    '\u20DE', '\u20DD',                                  # ⃞ ⃝ (enclosing square/circle)
    # Circles (radio buttons)
    '\u25CB', '\u25CF', '\u25CE',                       # ○ ● ◎
    '\u25EF',                                            # ◯
    '\u26AA', '\u26AB',                                  # ⚪ ⚫
    '\u2B55',                                            # ⭕
    # Halfwidth
    '\uFFED',                                            # ﾭ halfwidth black square
    # Square position / misc
    '\u2BC0', '\u2BD0',                                  # ⯀ ⯐
    '\u26F6',                                            # ⛶ square four corners
    # PUA — Wingdings empty/checked boxes (common in embedded PDFs)
    '\uf02a',                                            # Wingdings 2 empty box
    '\uf051', '\uf052',                                  # Q/R boxes
    '\uf063',                                            # checkbox variant
    '\uf06e', '\uf06f', '\uf070', '\uf071', '\uf072',   # circle/box variants
    '\uf073', '\uf074', '\uf075',                        # more box variants
    '\uf085',                                            # box variant
    '\uf0a0', '\uf0a7', '\uf0a8',                       # outlined squares
    '\uf0fb', '\uf0fc', '\uf0fd', '\uf0fe',             # X mark, check, box+X, box+check
])


class PageAnalyzer:
    """Builds PageModel objects from a PDF file. Pure analysis — no field detection."""

    def __init__(self, settings: Dict):
        self.settings = settings
        self.table_cell_padding = float(settings.get('table_cell_padding', 0))

    def analyze(self, pdf_path: str, target_pages: Optional[List[int]] = None) -> List[PageModel]:
        """
        Analyze a PDF and return a list of PageModel objects.

        Args:
            pdf_path: Path to the input PDF
            target_pages: Optional list of 0-based page indices to process.
                         None means all pages.

        Returns:
            List[PageModel], one per analyzed page, sorted by page_num.
        """
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        pages = []
        print(f"   Analyzing PDF structure...")

        with pdfplumber.open(str(pdf_path)) as pdf:
            page_count = len(pdf.pages)
            print(f"   {page_count} page(s) found")

            for page_num, page in enumerate(pdf.pages):
                if target_pages is not None and page_num not in target_pages:
                    continue

                pm = self._analyze_page(page, page_num)
                pages.append(pm)

        # Post-analysis passes that need cross-page context
        self._detect_text_only_pages(pages)
        self._detect_tally_sheets(pages)
        self._detect_footer_zones(pages)
        self._detect_info_pages(pages)

        print(f"   Analysis complete: {len(pages)} page(s) processed")
        return sorted(pages, key=lambda p: p.page_num)

    def _analyze_page(self, page, page_num: int) -> PageModel:
        """Build a PageModel from a single pdfplumber page."""

        # Get rotation and mediabox
        rotation = int(page.rotation) if hasattr(page, 'rotation') else 0
        # pdfplumber stores page_obj.mediabox as (x0, y0, x1, y1) from pdfminer
        if hasattr(page, 'page_obj') and hasattr(page.page_obj, 'mediabox'):
            mb = page.page_obj.mediabox
            pw = abs(float(mb[2]) - float(mb[0]))
            ph = abs(float(mb[3]) - float(mb[1]))
        elif hasattr(page, 'page_obj') and hasattr(page.page_obj, 'attrs'):
            mediabox = page.page_obj.attrs.get('MediaBox')
            if mediabox:
                pw = abs(float(mediabox[2]) - float(mediabox[0]))
                ph = abs(float(mediabox[3]) - float(mediabox[1]))
            else:
                pw, ph = float(page.width), float(page.height)
        else:
            pw, ph = float(page.width), float(page.height)

        # Extract all structural data
        words = page.extract_words(
            x_tolerance=3, y_tolerance=3,
            keep_blank_chars=False, use_text_flow=False
        ) or []
        lines = page.lines or []
        rects = page.rects or []
        curves = page.curves or []
        edges = page.edges or []
        images = page.images if hasattr(page, 'images') else []

        # Table detection via pdfplumber
        try:
            tables = page.find_tables() or []
        except Exception:
            tables = []

        # Classify lines
        h_lines, v_lines = classify_lines(lines)

        # Merge collinear horizontal segments that touch without a
        # vertical line at the junction — prevents phantom cell splits
        h_lines_merged = merge_collinear_h_segments(h_lines, v_lines, words=words)

        if len(h_lines_merged) < len(h_lines):
            # Some segments were merged — rebuild raw lines list so all
            # detectors (which read pm.lines) see the merged segments.
            non_h = [l for l in lines
                     if abs(l.get('top', l.get('y0', 0)) -
                            l.get('bottom', l.get('y1', 0))) >= 2
                     or abs(l.get('x1', 0) - l.get('x0', 0)) <= 5]
            for hl in h_lines_merged:
                non_h.append({
                    'x0': hl['x0'], 'x1': hl['x1'],
                    'top': hl.get('top', hl['y']),
                    'bottom': hl.get('bottom', hl['y']),
                    'y0': hl.get('top', hl['y']),
                    'y1': hl.get('bottom', hl['y']),
                })
            lines = non_h

        h_lines = h_lines_merged

        # Detect header bars
        header_bars = self._detect_header_bars_for_page(page, page_num, words)

        # Detect image upload regions
        image_regions = self._detect_image_upload_regions(rects, curves, page, page_num)

        # Detect box entry areas
        box_entry_areas = self._detect_box_entry_areas(page, page_num, words, v_lines)

        # Build visual grid cells
        visual_grid = self._build_visual_grid(page_num, tables, edges,
                                               float(page.width), float(page.height))

        # Detect existing form fields
        existing_checkboxes, existing_fields = self._detect_existing_form_fields(page)

        # Correct non-square checkbox bboxes (e.g. U+2B1C ⬜ in regular fonts
        # where pdfplumber clips `top` to the font cap-height, making height < width)
        existing_checkboxes = self._correct_checkbox_bboxes(existing_checkboxes)

        # Detect square grid regions
        square_grids = self._detect_square_grid_regions(rects, page_num)

        # Add wide text-containing grid cells as header bars.
        # Some section headers (colored full-width bars) aren't detected
        # by color analysis (e.g. fill=1.0 in grayscale) but DO appear
        # as wide visual grid cells containing text.
        header_bars = self._add_wide_grid_cell_headers(
            header_bars, visual_grid, words, float(page.width))

        # Filter out image_upload_regions that are visual grid cells.
        # Grid cells in tables (e.g. large empty cells in a form grid) are
        # regular data-entry areas, not photo-placeholder boxes.  Keeping
        # them as image_upload_regions causes the resolver pre-filter to
        # suppress legitimate text-field candidates that overlap them.
        if visual_grid and image_regions:
            tol = 3.0
            filtered_regions = []
            for r in image_regions:
                is_grid = any(
                    abs(r['x0'] - gc[0]) < tol and abs(r['y0'] - gc[1]) < tol
                    and abs(r['x1'] - gc[2]) < tol and abs(r['y1'] - gc[3]) < tol
                    for gc in visual_grid
                )
                if not is_grid:
                    filtered_regions.append(r)
            image_regions = filtered_regions

        # Extract table bounding boxes for inter-table gap detection
        table_bboxes = []
        for t in tables:
            if hasattr(t, 'bbox') and t.bbox:
                b = t.bbox
                table_bboxes.append((float(b[0]), float(b[1]), float(b[2]), float(b[3])))

        return PageModel(
            page_num=page_num,
            width=float(page.width),
            height=float(page.height),
            rotation=rotation,
            mediabox=(pw, ph),
            words=words,
            lines=lines,
            rects=rects,
            curves=curves,
            edges=edges,
            tables=tables,
            images=images,
            header_bars=header_bars,
            h_lines=h_lines,
            v_lines=v_lines,
            visual_grid_cells=visual_grid,
            box_entry_areas=box_entry_areas,
            image_upload_regions=image_regions,
            square_grid_regions=square_grids,
            existing_checkboxes=existing_checkboxes,
            existing_form_fields=existing_fields,
            table_bboxes=table_bboxes,
        )

    # -------------------------------------------------------------------
    # Header bar detection
    # -------------------------------------------------------------------

    def _detect_header_bars_for_page(self, page, page_num: int, words: List[Dict]) -> List[Dict]:
        """Detect colored header bars on a page (filled rects/paths containing text)."""
        objs = page.objects
        candidates = []

        # Collect filled rects
        for r in objs.get('rect', []):
            if r.get('fill'):
                w = r['x1'] - r['x0']
                h = r['y1'] - r['y0']
                if w > 60 and 8 < h < 40:
                    candidates.append(r)

        # Collect filled paths
        for p in objs.get('path', []):
            if p.get('fill'):
                w = p['x1'] - p['x0']
                h = p['y1'] - p['y0']
                if w > 60 and 8 < h < 40:
                    candidates.append(p)

        header_bars = []
        for c in candidates:
            color = c.get('non_stroking_color', [])
            if isinstance(color, (int, float)):
                color = [color]

            is_colored = False
            if color:
                if len(color) == 1 and color[0] < 0.95:
                    is_colored = True
                elif len(color) == 3 and (color[0] < 0.95 or color[1] < 0.95 or color[2] < 0.95):
                    is_colored = True
                elif len(color) == 4 and sum(color) > 0.05:
                    is_colored = True

            if is_colored:
                rx0, ry0, rx1, ry1 = c['x0'], c['top'], c['x1'], c['bottom']
                # Header bar must contain text (empty colored box = input field)
                has_text = False
                for w in words:
                    if (w['x0'] >= rx0 - 2 and w['x1'] <= rx1 + 2 and
                            w['top'] >= ry0 - 2 and w['bottom'] <= ry1 + 2):
                        has_text = True
                        break
                if has_text:
                    header_bars.append({
                        'x0': rx0, 'x1': rx1,
                        'y0': ry0, 'y1': ry1,
                        'right_edge': rx1, 'is_dark': True,
                    })

        # --- Merge narrow header bars at the same Y range ---
        # Many PDFs have column-header rows where each cell is individually
        # colored and < 200 pt wide.  is_in_header_bar() skips narrow bars,
        # so we merge rows of adjacent bars into one wide bar that the
        # overlap check can use.
        header_bars = self._merge_header_bar_rows(header_bars)

        return header_bars

    @staticmethod
    def _merge_header_bar_rows(header_bars: List[Dict]) -> List[Dict]:
        """Merge adjacent narrow header bars that share the same Y range into wider bars."""
        if not header_bars:
            return header_bars

        # Group bars by Y range (within 3pt of y0 and y1)
        rows = []  # list of lists
        used = [False] * len(header_bars)
        for i, bar in enumerate(header_bars):
            if used[i]:
                continue
            row = [bar]
            used[i] = True
            for j in range(i + 1, len(header_bars)):
                if used[j]:
                    continue
                other = header_bars[j]
                if abs(bar['y0'] - other['y0']) < 3 and abs(bar['y1'] - other['y1']) < 3:
                    row.append(other)
                    used[j] = True
            rows.append(row)

        result = list(header_bars)  # keep originals
        for row in rows:
            if len(row) < 2:
                continue
            total_width = sum(b['x1'] - b['x0'] for b in row)
            if total_width > 200:
                # Create a merged bar spanning the full row
                merged = {
                    'x0': min(b['x0'] for b in row),
                    'x1': max(b['x1'] for b in row),
                    'y0': min(b['y0'] for b in row),
                    'y1': max(b['y1'] for b in row),
                    'right_edge': max(b['x1'] for b in row),
                    'is_dark': True,
                }
                result.append(merged)
        return result

    @staticmethod
    def _add_wide_grid_cell_headers(
        header_bars: List[Dict],
        grid_cells: List[Tuple[float, float, float, float]],
        words: List[Dict],
        page_width: float,
    ) -> List[Dict]:
        """
        Add individual wide grid cells containing ALL-CAPS text as header bars.

        Section headers (e.g. "RESIDENTIAL GAS HVAC SYSTEMS") appear as
        colored bars spanning the full page width.  When their fill color
        is not detected by pdfplumber (e.g. grayscale 1.0), they're missed
        by the color-based header-bar detector.  However, they always show
        up as wide visual grid cells with all-caps title text.

        Key distinction: section headers have ALL-CAPS text, while form
        areas like "Account Holder's Signature X Date" have mixed-case.
        Only add cells where the text is predominantly uppercase.
        """
        for cx0, cy0, cx1, cy1 in grid_cells:
            cell_w = cx1 - cx0
            cell_h = cy1 - cy0
            # Only consider wide, short cells (section header shape)
            if cell_w < page_width * 0.5 or cell_h > 35 or cell_h < 5:
                continue
            # Already covered by an existing header bar?
            already = False
            for hb in header_bars:
                if (abs(hb['y0'] - cy0) < 3 and abs(hb['y1'] - cy1) < 3
                        and hb['x1'] - hb['x0'] > page_width * 0.4):
                    already = True
                    break
            if already:
                continue
            # Gather text inside this cell
            cell_words = []
            for w in words:
                wx_mid = (float(w['x0']) + float(w['x1'])) / 2
                wy_mid = (float(w['top']) + float(w['bottom'])) / 2
                if cx0 <= wx_mid <= cx1 and cy0 <= wy_mid <= cy1:
                    cell_words.append(w['text'])
            if not cell_words:
                continue  # No text — not a header
            # Check if text is predominantly uppercase (section header style)
            text = ' '.join(cell_words)
            alpha_chars = [c for c in text if c.isalpha()]
            if not alpha_chars:
                continue
            upper_ratio = sum(1 for c in alpha_chars if c.isupper()) / len(alpha_chars)
            if upper_ratio < 0.60:
                continue  # Mixed-case → likely a form area, not a section header

            header_bars.append({
                'x0': cx0, 'x1': cx1,
                'y0': cy0, 'y1': cy1,
                'right_edge': cx1, 'is_dark': True,
            })
        return header_bars

    # -------------------------------------------------------------------
    # Image upload region detection
    # -------------------------------------------------------------------

    def _detect_image_upload_regions(self, rects, curves, page, page_num: int) -> List[Dict]:
        """Detect large empty rectangles likely used for image/photo uploads."""
        regions = []
        all_shapes = list(rects) + list(curves)

        for shape in all_shapes:
            x0 = shape.get('x0', 0)
            y0 = shape.get('top', shape.get('y0', 0))
            x1 = shape.get('x1', 0)
            y1 = shape.get('bottom', shape.get('y1', 0))
            w = x1 - x0
            h = y1 - y0

            # Image regions are large boxes (>80pt both dimensions)
            if w > 80 and h > 80:
                # Check if it's mostly empty (no significant text)
                try:
                    crop = page.within_bbox((x0 + 2, y0 + 2, x1 - 2, y1 - 2))
                    text = (crop.extract_text() or "").strip()
                    if len(text) < 10:  # Nearly empty
                        regions.append({
                            'x0': x0, 'y0': y0, 'x1': x1, 'y1': y1,
                            'page': page_num,
                        })
                except Exception:
                    pass

        return regions

    # -------------------------------------------------------------------
    # Box entry (comb field) area detection
    # -------------------------------------------------------------------

    def _detect_box_entry_areas(self, page, page_num: int,
                                 words: List[Dict], v_lines: List[Dict]) -> List[Dict]:
        """Detect rows of evenly-spaced small boxes (account number fields)."""
        areas = []
        if not v_lines:
            return areas

        # Group vertical lines by Y range (within 5pt)
        from collections import defaultdict
        y_groups = defaultdict(list)
        for vl in v_lines:
            y_mid = (vl['top'] + vl['bottom']) / 2
            # Round to nearest 10pt for grouping
            key = round(y_mid / 10) * 10
            y_groups[key].append(vl)

        for key, group in y_groups.items():
            if len(group) < 6:
                continue  # Need at least 6 vertical lines for a box row

            # Sort by x position
            group.sort(key=lambda v: v['x0'])

            # Check for even spacing
            x_positions = [v['x0'] for v in group]
            spacings = [x_positions[i + 1] - x_positions[i] for i in range(len(x_positions) - 1)]
            if not spacings:
                continue

            avg_spacing = sum(spacings) / len(spacings)
            if avg_spacing < 5 or avg_spacing > 25:
                continue  # Box spacing should be 5-25pt

            # Check consistency (all spacings within 30% of average)
            consistent = all(abs(s - avg_spacing) < avg_spacing * 0.3 for s in spacings)
            if consistent:
                areas.append({
                    'x0': min(x_positions),
                    'x1': max(x_positions),
                    'y0': min(v['top'] for v in group),
                    'y1': max(v['bottom'] for v in group),
                    'page': page_num,
                    'box_count': len(group) - 1,
                    'box_width': avg_spacing,
                })

        return areas

    # -------------------------------------------------------------------
    # Visual grid construction
    # -------------------------------------------------------------------

    def _build_visual_grid(self, page_num: int, tables, edges,
                            page_width: float, page_height: float) -> List[Tuple[float, float, float, float]]:
        """Build visual grid cells from table edges. Returns list of (x0, y0, x1, y1) tuples."""
        cells = []

        for table in tables:
            try:
                # pdfplumber table has .cells attribute — list of (x0, y0, x1, y1)
                if hasattr(table, 'cells') and table.cells:
                    for cell in table.cells:
                        if cell and len(cell) == 4:
                            cx0, cy0, cx1, cy1 = [float(v) for v in cell]
                            if cx1 > cx0 and cy1 > cy0:
                                cells.append((cx0, cy0, cx1, cy1))
            except Exception:
                continue

        return cells

    # -------------------------------------------------------------------
    # Square grid region detection
    # -------------------------------------------------------------------

    def _detect_square_grid_regions(self, rects: List[Dict], page_num: int) -> List[Dict]:
        """Detect dense square grids (like checkbox matrices)."""
        regions = []
        small_squares = []

        for r in rects:
            w = abs(r.get('x1', 0) - r.get('x0', 0))
            h = abs(r.get('bottom', r.get('y1', 0)) - r.get('top', r.get('y0', 0)))
            if 4 <= w <= 18 and 4 <= h <= 18 and abs(w - h) < 3:
                small_squares.append(r)

        if len(small_squares) >= 10:
            # Group squares that are in grid formation
            xs = sorted(set(round(r['x0'], 0) for r in small_squares))
            ys = sorted(set(round(r.get('top', r.get('y0', 0)), 0) for r in small_squares))

            if len(xs) >= 3 and len(ys) >= 3:
                regions.append({
                    'x0': min(xs) - 5,
                    'y0': min(ys) - 5,
                    'x1': max(xs) + 20,
                    'y1': max(ys) + 20,
                    'page': page_num,
                    'count': len(small_squares),
                })

        return regions

    # -------------------------------------------------------------------
    # Existing form field detection
    # -------------------------------------------------------------------

    def _detect_existing_form_fields(self, page) -> Tuple[List[Dict], List[Dict]]:
        """
        Detect pre-existing form fields and checkbox characters in the PDF.

        Two sources:
        1. Character glyphs — Wingdings, ZapfDingbats, Webdings, Symbol fonts
           that render as checkbox squares / checkmark symbols.
        2. (Future) AcroForm Btn widget annotations.

        Returns:
            (checkboxes, fields) — lists of dicts with x0/top/x1/bottom keys.
        """
        checkboxes = []
        fields = []
        seen = set()  # (rounded x0, rounded top) for dedup

        chars = page.chars or []
        for char in chars:
            char_text = char.get('text', '')
            if not char_text:
                continue

            font_name = char.get('fontname', '').lower()
            is_checkbox = False

            # ---- Unicode checkbox characters (any font) ----
            if char_text in _UNICODE_CHECKBOX_CHARS:
                is_checkbox = True

            # ---- CID-encoded characters ----
            elif char_text.startswith('(cid:') and char_text.endswith(')'):
                try:
                    cid_num = int(char_text[5:-1])
                    if cid_num in [
                        110, 111, 112, 113, 114, 115, 116, 117,  # Wingdings 1 boxes
                        133, 160, 167, 168,
                        251, 252, 253, 254,
                        99,
                        79, 80, 81, 82, 83, 84, 85, 86,          # Wingdings 2
                        159, 161, 162, 163, 164, 165, 166, 169, 170,
                        97, 103,                                   # Webdings
                        61603,                                     # PUA 0xF0A3
                    ]:
                        is_checkbox = True
                except ValueError:
                    pass

            # ---- Wingdings 1 ----
            elif 'wingding' in font_name and '2' not in font_name and '3' not in font_name:
                char_ord = ord(char_text[0]) if len(char_text) == 1 else 0
                if char_ord in [
                    0x6E, 0x6F, 0x70, 0x71, 0x72, 0x73, 0x74, 0x75,
                    0x85, 0xA0, 0xA7, 0xA8,
                    0xFB, 0xFC, 0xFD, 0xFE, 0x63, 99, 111,
                    0xF06E, 0xF06F, 0xF070, 0xF071, 0xF072, 0xF073, 0xF074, 0xF075,
                    0xF085, 0xF0A0, 0xF0A7, 0xF0A8,
                    0xF0FB, 0xF0FC, 0xF0FD, 0xF0FE, 0xF063,
                    61539, 61608, 61603, 61559, 61567, 61568,
                ]:
                    is_checkbox = True

            # ---- Wingdings 2 ----
            elif 'wingding' in font_name and '2' in font_name:
                char_ord = ord(char_text[0]) if len(char_text) == 1 else 0
                if char_ord in [
                    0x2A,  # empty checkbox square (e.g. NMGC forms)
                    0x4F, 0x50, 0x51, 0x52, 0x53, 0x54, 0x55, 0x56,
                    0x85, 0x9F, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5,
                    0xA6, 0xA7, 0xA8, 0xA9, 0xAA, 163,
                    0xF02A,  # PUA variant of 0x2A
                    0xF04F, 0xF050, 0xF051, 0xF052, 0xF053, 0xF054, 0xF055, 0xF056,
                    0xF085, 0xF09F, 0xF0A0, 0xF0A1, 0xF0A2, 0xF0A3, 0xF0A4, 0xF0A5,
                    0xF0A6, 0xF0A7, 0xF0A8, 0xF0A9, 0xF0AA, 61603,
                ]:
                    is_checkbox = True

            # ---- Wingdings 3 ----
            elif 'wingding' in font_name and '3' in font_name:
                char_ord = ord(char_text[0]) if len(char_text) == 1 else 0
                if char_ord in [0x7D, 0x7E, 0xF07D, 0xF07E]:
                    is_checkbox = True

            # ---- Webdings ----
            elif 'webdings' in font_name or 'advp' in font_name:
                char_ord = ord(char_text[0]) if len(char_text) == 1 else 0
                if char_ord in [0x61, 0x63, 0x67, 0xF061, 0xF063, 0xF067, 97, 99, 103]:
                    is_checkbox = True

            # ---- ZapfDingbats ----
            elif 'zapf' in font_name or 'dingbat' in font_name:
                char_ord = ord(char_text[0]) if len(char_text) == 1 else 0
                if char_ord in [
                    0x6E, 0x6F, 0x70, 0x71, 0x72,
                    0x33, 0x34, 0x35, 0x36, 0x37, 0x38,
                    110, 111, 112, 113, 114, 51, 52, 53, 54, 55, 56,
                ]:
                    is_checkbox = True

            # ---- Symbol font ----
            elif 'symbol' in font_name:
                char_ord = ord(char_text[0]) if len(char_text) == 1 else 0
                if char_ord in [0xD6, 0xF0D6]:
                    is_checkbox = True

            if is_checkbox:
                x0 = float(char.get('x0', 0))
                top = float(char.get('top', 0))
                x1 = float(char.get('x1', 0))
                bottom = float(char.get('bottom', 0))

                # Handle combining / zero-width characters (e.g. U+20DE)
                # They report x0 == x1; the visual glyph is a square enclosing
                # the preceding character, so shift left and use font size.
                char_w = x1 - x0
                char_h = bottom - top
                if char_w < 1.0 and char_h > 0:
                    size = max(char_h, float(char.get('size', char_h)))
                    # Scale up slightly — combining enclosing glyphs render
                    # ~1.25× the font size as the enclosing square
                    box_size = size * 1.3
                    # The combining enclosing square glyph (e.g. U+20DE) in
                    # Inter-Regular has its visual center at ~25% of box_size
                    # to the RIGHT of the character origin (left bearing ≈ -25%,
                    # right extent ≈ +75%).  pdfplumber reports x0==x1==origin.
                    # Place x0 at origin - 25% so the box is correctly centred.
                    x0 = x0 - box_size / 4
                    x1 = x0 + box_size
                    # Vertically center too
                    mid_y = (top + bottom) / 2
                    top = mid_y - box_size / 2
                    bottom = mid_y + box_size / 2

                # Dedup within 5pt
                key = (round(x0 / 5) * 5, round(top / 5) * 5)
                if key in seen:
                    continue
                seen.add(key)

                checkboxes.append({
                    'x0': x0, 'top': top, 'x1': x1, 'bottom': bottom,
                    'source': 'character',
                })

        return checkboxes, fields

    def _correct_checkbox_bboxes(self, checkboxes: List[Dict]) -> List[Dict]:
        """
        Correct non-square checkbox bounding boxes produced by the character detector.

        pdfplumber derives `top` from the font's cap-height / line-top, which for large
        Unicode square glyphs (e.g. U+2B1C ⬜ WHITE LARGE SQUARE in regular text fonts)
        can be 3-5 pt lower than the actual rendered glyph top.  This causes the
        interactive widget to be placed slightly below the visual glyph center.

        Heuristic: when width / height > 1.3 (i.e. the character is significantly wider
        than tall), assume the glyph fills a square of side = width, extending the top
        upward:  corrected_top = bottom - width.

        Only applied to character-sourced entries so vector/curve detections
        (which already use full visual extents) are unaffected.
        """
        corrected = []
        for cb in checkboxes:
            if cb.get('source') != 'character':
                corrected.append(cb)
                continue
            x0 = float(cb['x0'])
            top = float(cb['top'])
            x1 = float(cb['x1'])
            bottom = float(cb['bottom'])
            width = x1 - x0
            height = bottom - top
            if height > 0 and width / height > 1.3:
                corrected_top = bottom - width
                corrected.append({
                    'x0': x0,
                    'top': corrected_top,
                    'x1': x1,
                    'bottom': bottom,
                    'source': cb.get('source', 'character'),
                })
            else:
                corrected.append(cb)
        return corrected

    # -------------------------------------------------------------------
    # Page classification
    # -------------------------------------------------------------------

    def _detect_text_only_pages(self, pages: List[PageModel]):
        """Mark pages that are text-only (no form structure)."""
        for pm in pages:
            # Check for horizontal form lines
            form_lines = [l for l in pm.h_lines if l['width'] > 50]
            underscore_count = sum(1 for w in pm.words if '___' in w.get('text', ''))

            page_area = pm.width * pm.height
            word_density = len(pm.words) / (page_area / 10000) if page_area > 0 else 0

            # Colon-based numbered list detection
            colon_words = [w for w in pm.words if w.get('text', '').endswith(':')]
            numbered_colons = 0
            for cw in colon_words:
                for w in pm.words:
                    if (abs(w['top'] - cw['top']) < 3 and
                            w['x1'] < cw['x0'] and w['x1'] > cw['x0'] - 30):
                        if w.get('text', '').rstrip('.').isdigit() or w.get('text', '') in ['.', ')']:
                            numbered_colons += 1
                            break

            is_text_only = False
            if len(form_lines) == 0 and underscore_count == 0:
                if word_density > 15:
                    is_text_only = True
                elif colon_words and numbered_colons >= len(colon_words) * 0.5:
                    is_text_only = True

            pm.is_text_only = is_text_only
            if is_text_only:
                print(f"   Page {pm.page_num + 1}: text-only (no form fields expected)")

    def _detect_tally_sheets(self, pages: List[PageModel]):
        """Mark pages that are tally sheets (dense grids, mostly empty)."""
        for pm in pages:
            page_text = " ".join(w.get('text', '') for w in pm.words[:100]).lower()
            has_tally_header = 'tally' in page_text

            if not has_tally_header:
                worksheet_indicators = [
                    'rebate', 'equipment type', 'ref #', 'unit cost',
                    'total rebate', 'model number', 'serial number',
                    'installed', 'quantity'
                ]
                if any(ind in page_text for ind in worksheet_indicators):
                    continue

            if len(pm.visual_grid_cells) < 50:
                continue

            # Count empty cells (approximate — check text presence)
            empty = 0
            for cell in pm.visual_grid_cells:
                text = pm.get_text_in_bbox(cell, 0.15)
                if not text.strip():
                    empty += 1

            empty_frac = empty / len(pm.visual_grid_cells)
            is_dense = len(pm.visual_grid_cells) > 100

            if (empty_frac > 0.70 or (is_dense and empty_frac > 0.50)) and has_tally_header:
                pm.is_tally_sheet = True
                print(f"   Page {pm.page_num + 1}: tally sheet ({len(pm.visual_grid_cells)} cells, {empty_frac:.0%} empty)")

    def _detect_footer_zones(self, pages: List[PageModel]):
        """Detect page footer zones (logos, page numbers, URLs).

        Uses two strategies:
        1. Per-page keyword detection (page, .com, ©, etc.)
        2. Cross-page consistency: if ≥60% of pages have text in the
           bottom 45pt band, ALL pages with bottom text get footer zones.
        """
        if not pages:
            return

        footer_band = 45  # pts from bottom
        footer_keywords = {'page', '.com', 'www.', '©', 'copyright'}

        # Phase 1: Detect per-page footer candidates
        page_has_bottom_text = {}  # page_num -> (threshold, word_count, has_keyword)
        for pm in pages:
            page_h = pm.height
            threshold = page_h - footer_band

            bottom_words = [w for w in pm.words
                           if float(w.get('top', 0)) > threshold]
            if not bottom_words:
                continue

            bottom_text = " ".join(w.get('text', '') for w in bottom_words).lower()
            has_kw = any(kw in bottom_text for kw in footer_keywords)
            # Also detect if it's just a number (page number)
            if not has_kw and bottom_text.strip().replace(' ', '').isdigit():
                has_kw = True

            page_has_bottom_text[pm.page_num] = (threshold, len(bottom_words), has_kw)

        # Phase 2: Cross-page consistency check
        # If a majority of pages have bottom-band text, it's a footer
        if len(pages) >= 3:
            bottom_ratio = len(page_has_bottom_text) / len(pages)
            cross_page_footer = bottom_ratio >= 0.60
        else:
            cross_page_footer = False

        # Phase 3: Apply footer zones
        footer_count = 0
        for pm in pages:
            if pm.page_num not in page_has_bottom_text:
                continue

            threshold, wcount, has_kw = page_has_bottom_text[pm.page_num]

            # Mark footer if: keyword match OR cross-page consistency
            if has_kw or cross_page_footer:
                pm.footer_y = threshold
                footer_count += 1

        if footer_count > 0:
            print(f"   Footer zones: {footer_count}/{len(pages)} pages "
                  f"(y>{pages[0].height - footer_band:.0f})")

    def _detect_info_pages(self, pages: List[PageModel]):
        """Detect informational pages where ALL table cells have pre-printed data.

        If a page has tables but EVERY data cell already contains text,
        the page is a reference/specification page with no fillable areas.
        Mark it so detectors can skip field creation entirely.
        """
        for pm in pages:
            if pm.is_text_only or pm.is_tally_sheet:
                continue

            # Need tables with visual grid cells
            if len(pm.tables) == 0 and len(pm.visual_grid_cells) == 0:
                continue

            # Check all table cells — if nearly all have text, it's informational
            all_cells = []

            # From pdfplumber tables
            for table in pm.tables:
                if hasattr(table, 'cells'):
                    for cell in table.cells:
                        if cell and len(cell) >= 4:
                            x0, y0, x1, y1 = float(cell[0]), float(cell[1]), float(cell[2]), float(cell[3])
                            cw = x1 - x0
                            ch = y1 - y0
                            if cw > 20 and ch > 8:  # Skip tiny cells
                                all_cells.append((x0, y0, x1, y1))

            # From visual grid cells
            for cell in pm.visual_grid_cells:
                cw = cell[2] - cell[0]
                ch = cell[3] - cell[1]
                if cw > 20 and ch > 8:
                    all_cells.append(cell)

            if len(all_cells) < 10:
                continue  # Not enough cells to judge

            # Dedup cells that overlap significantly
            unique_cells = []
            for c in all_cells:
                is_dup = False
                for u in unique_cells:
                    # Check >70% overlap
                    ix0, iy0, ix1, iy1 = max(c[0], u[0]), max(c[1], u[1]), min(c[2], u[2]), min(c[3], u[3])
                    if ix1 > ix0 and iy1 > iy0:
                        inter = (ix1 - ix0) * (iy1 - iy0)
                        c_area = max(1, (c[2] - c[0]) * (c[3] - c[1]))
                        if inter / c_area > 0.7:
                            is_dup = True
                            break
                if not is_dup:
                    unique_cells.append(c)

            if len(unique_cells) < 10:
                continue

            # Count cells with text (excluding checkbox characters)
            # Cells containing □/☐/☑/☒ or "Yes"/"No"/"N/A" patterns
            # are fillable checkboxes, not pre-filled data.
            _CB_CHARS = _UNICODE_CHECKBOX_CHARS
            _CB_WORDS = {'yes', 'no', 'n/a', 'na'}
            filled = 0
            has_checkbox_cells = False
            for cell in unique_cells:
                txt = pm.get_text_in_bbox(cell, 0.15)
                if txt and txt.strip():
                    cell_text = txt.strip()
                    # Check if cell contains checkbox characters
                    if any(c in _CB_CHARS for c in cell_text):
                        has_checkbox_cells = True
                        continue  # Don't count checkbox cells as "filled"
                    # Check if cell contains only Yes/No/N/A checkbox labels
                    # (e.g. "Yes No N/A" combined in one cell)
                    cell_words = cell_text.lower().split()
                    if cell_words and all(w in _CB_WORDS for w in cell_words):
                        has_checkbox_cells = True
                        continue
                    filled += 1

            fill_ratio = filled / len(unique_cells)

            # If >=85% of cells have text AND there are no underscores or
            # explicit fill indicators, this is an informational page
            has_fill_indicators = any(
                '___' in w.get('text', '') for w in pm.words
            )
            # Checkbox characters anywhere on the page are fill indicators
            if has_checkbox_cells:
                has_fill_indicators = True
            # Colon labels on info pages are part of descriptions ("Type:", "Size:")
            # not fill-in prompts. Only count as fill indicators if MULTIPLE
            # colon words have empty right-sides (form labels like "Name:", "Date:").
            entry_colon_count = 0
            for w in pm.words:
                if w.get('text', '').endswith(':'):
                    wx1 = float(w.get('x1', 0))
                    wy = float(w.get('top', 0))
                    right_words = [rw for rw in pm.words
                                   if float(rw.get('x0', 0)) > wx1 + 5
                                   and abs(float(rw.get('top', 0)) - wy) < 3
                                   and float(rw.get('x0', 0)) < wx1 + 200]
                    if len(right_words) == 0:
                        entry_colon_count += 1
            # Need 3+ fill-style colons to consider it a form page
            has_entry_colons = entry_colon_count >= 3

            # Strict criteria for informational page classification:
            # 1. ≥95% fill ratio (nearly all cells have text)
            # 2. At least 40 unique cells (rules out small form tables)
            # 3. No fill indicators (underscores, entry colons)
            # 4. High word density (lots of text on page)
            page_area = pm.width * pm.height
            word_density = len(pm.words) / (page_area / 10000) if page_area > 0 else 0

            if (fill_ratio >= 0.95
                    and len(unique_cells) >= 40
                    and not has_fill_indicators
                    and not has_entry_colons
                    and word_density > 5):
                pm.is_info_page = True
                print(f"   Page {pm.page_num + 1}: informational page "
                      f"({len(unique_cells)} cells, {fill_ratio:.0%} filled)")
