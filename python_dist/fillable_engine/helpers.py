"""
Shared utility functions for the fillable engine.
Ported from UniversalPDFFillable helper methods — no shared state.
"""

import re
from typing import Dict, List, Optional, Tuple, Set
from collections import defaultdict


# ---------------------------------------------------------------------------
# Bounding box geometry
# ---------------------------------------------------------------------------

def bbox_overlap_area(a, b):
    """Compute intersection area of two bboxes (x0, y0, x1, y1)."""
    ix0 = max(a[0], b[0])
    iy0 = max(a[1], b[1])
    ix1 = min(a[2], b[2])
    iy1 = min(a[3], b[3])
    if ix1 > ix0 and iy1 > iy0:
        return (ix1 - ix0) * (iy1 - iy0)
    return 0.0


def bbox_area(bbox):
    """Area of a bbox (x0, y0, x1, y1)."""
    return max(0, bbox[2] - bbox[0]) * max(0, bbox[3] - bbox[1])


def bbox_overlap_ratio(a, b):
    """Overlap area as fraction of the smaller bbox."""
    inter = bbox_overlap_area(a, b)
    if inter <= 0:
        return 0.0
    min_area = min(bbox_area(a), bbox_area(b))
    return inter / max(1e-6, min_area)


def bboxes_overlap(a, b, threshold=0.4):
    """Check if two bboxes overlap beyond a threshold (fraction of smaller)."""
    return bbox_overlap_ratio(a, b) > threshold


def bbox_contains(outer, inner, tolerance=2.0):
    """Check if outer bbox contains inner bbox (with tolerance)."""
    return (inner[0] >= outer[0] - tolerance and
            inner[1] >= outer[1] - tolerance and
            inner[2] <= outer[2] + tolerance and
            inner[3] <= outer[3] + tolerance)


def bbox_center(bbox):
    """Center point of a bbox."""
    return ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)


def bbox_iou(a, b):
    """Intersection over Union of two bboxes."""
    inter = bbox_overlap_area(a, b)
    if inter <= 0:
        return 0.0
    union = bbox_area(a) + bbox_area(b) - inter
    return inter / max(1e-6, union)


# ---------------------------------------------------------------------------
# Coordinate transforms (ported from _transform_coords_for_rotation)
# ---------------------------------------------------------------------------

def transform_visual_to_storage(page_num: int, x0: float, y0: float,
                                 x1: float, y1: float,
                                 rotation: int, mediabox: Tuple[float, float]) -> Tuple[float, float, float, float]:
    """
    Transform visual coordinates (pdfplumber top-down) to storage coordinates (pikepdf bottom-up).

    Visual coordinate system (pdfplumber): origin at top-left, x->right, y->down.
    Storage coordinate system (pikepdf):   origin at bottom-left, x->right, y->up.
    """
    pw, ph = mediabox
    vx0, vy0, vx1, vy1 = x0, y0, x1, y1

    if rotation == 0:
        sx0 = vx0
        sy0 = ph - vy1
        sx1 = vx1
        sy1 = ph - vy0
    elif rotation == 90:
        sx0 = vy0
        sy0 = vx0
        sx1 = vy1
        sy1 = vx1
    elif rotation == 180:
        sx0 = pw - vx1
        sy0 = vy0
        sx1 = pw - vx0
        sy1 = vy1
    elif rotation == 270:
        sx0 = pw - vy1
        sy0 = ph - vx1
        sx1 = pw - vy0
        sy1 = ph - vx0
    else:
        sx0 = vx0
        sy0 = ph - vy1
        sx1 = vx1
        sy1 = ph - vy0

    # Normalize: ensure sx0 < sx1 and sy0 < sy1
    if sx0 > sx1:
        sx0, sx1 = sx1, sx0
    if sy0 > sy1:
        sy0, sy1 = sy1, sy0

    # Clamp to MediaBox bounds
    sx0 = max(0, min(pw, sx0))
    sx1 = max(0, min(pw, sx1))
    sy0 = max(0, min(ph, sy0))
    sy1 = max(0, min(ph, sy1))

    return (sx0, sy0, sx1, sy1)


# ---------------------------------------------------------------------------
# Field naming (ported from _clean_field_name, _generate_unique_name)
# ---------------------------------------------------------------------------

# Filler words that don't make good field names
_FILLER_WORDS = frozenset({
    'as', 'that', 'apply', 'check', 'all', 'select', 'one', 'shown',
    'on', 'your', 'be', 'must', 'if', 'to', 'the', 'a', 'an', 'of',
    'for', 'in', 'is', 'it', 'or', 'and',
})


def clean_field_name(raw_label: str) -> str:
    """Clean raw text into a field name."""
    if not raw_label:
        return "Field"

    clean = raw_label.strip()
    # Remove trailing colons and periods
    clean = clean.rstrip(':').rstrip('.').strip()
    # Remove non-printable characters
    clean = re.sub(r'[^\x20-\x7E]', '', clean)
    # Replace multiple spaces
    clean = re.sub(r'\s+', ' ', clean).strip()
    # Replace spaces with underscores for field name
    clean = clean.replace(' ', '_')
    # Remove leading/trailing underscores
    clean = clean.strip('_')

    # If just a filler word, return "Field"
    if clean.lower().replace('_', '') in _FILLER_WORDS:
        return "Field"

    # Truncate to reasonable length
    if len(clean) > 120:
        clean = clean[:120].rsplit('_', 1)[0]

    return clean or "Field"


class FieldNameGenerator:
    """Generate unique field names. Thread-safe for a single pipeline run."""

    def __init__(self):
        self._counters: Dict[str, int] = defaultdict(int)

    def generate(self, base_name: str) -> str:
        """Generate a unique name. First occurrence has no suffix; duplicates get _2, _3, etc."""
        key = base_name
        self._counters[key] += 1
        count = self._counters[key]
        if count == 1:
            return base_name
        return f"{base_name}_{count}"

    def reset(self):
        """Reset all counters (for a new PDF run)."""
        self._counters.clear()


# ---------------------------------------------------------------------------
# Format detection (ported from _detect_field_format)
# ---------------------------------------------------------------------------

FORMAT_PATTERNS = {
    'date': {
        'keywords': ['date', 'dob', 'birth', 'expir', 'effective'],
        'exclude': ['update', 'to date', 'mandate'],
        'maxlen': 10,
    },
    'phone': {
        'keywords': ['phone', 'fax', 'tel', 'mobile', 'cell'],
        'exclude': ['smartphone'],
        'maxlen': 14,
    },
    'zip': {
        'keywords': ['zip', 'postal'],
        'exclude': [],
        'maxlen': 10,
    },
    'email': {
        'keywords': ['email', 'e-mail'],
        'exclude': [],
        'maxlen': 50,
    },
    'ssn': {
        'keywords': ['ssn', 'social security'],
        'exclude': [],
        'maxlen': 11,
    },
    'state': {
        'keywords': ['state'],
        'exclude': ['statement', 'united states', 'state of',
                     'restate', 'estate'],
        'maxlen': 2,
    },
    'currency': {
        'keywords': ['amount', 'cost', 'price', 'total', 'rebate', 'incentive',
                      'fee', 'payment', 'balance', 'value'],
        'exclude': ['account', 'number', 'address', 'date'],
        'maxlen': 12,
    },
}


def detect_field_format(header_name: Optional[str], label: Optional[str],
                        cell_text: Optional[str] = None) -> Tuple[Optional[str], Dict]:
    """Determine field format from header/label text.

    Only matches when the label is short enough to be a field label
    (not a multi-word section header).  Long labels (>8 words) skip
    currency detection UNLESS the last few words contain a currency
    keyword (e.g. "Rockland Electric ... Rebate Amount Owed").
    """
    check_text = ((header_name or '') + ' ' + (label or '')).lower().replace('_', ' ')
    all_text = (check_text + ' ' + (cell_text or '').lower()).replace('_', ' ')
    word_count = len(check_text.split())

    # Special case: standalone "$" in cell_text or label → currency
    # (a "$" prefix next to the field means it already has the symbol)
    for src_text in (cell_text, label):
        if src_text:
            stripped = src_text.strip()
            if stripped in ('$', '$:', '$ ') or stripped.startswith('$'):
                excludes = FORMAT_PATTERNS['currency'].get('exclude', [])
                if not any(ex in check_text for ex in excludes):
                    return 'currency', {'maxlen': 12, 'has_dollar_in_cell': True}

    for fmt, config in FORMAT_PATTERNS.items():
        # Long-label guards: very long labels (>6 words) are usually
        # structural / multi-field labels, not single-field labels.
        # Skip non-currency formats entirely for long labels.
        # Currency: allow up to 8 words, or any length if the keyword
        # appears in the last 4 words (e.g. "... Rebate Amount Owed").
        if word_count > 6 and fmt != 'currency':
            continue
        if fmt == 'currency' and word_count > 8:
            words = check_text.split()
            tail = ' '.join(words[-4:]) if len(words) >= 4 else check_text
            if not any(kw in tail for kw in config['keywords']):
                continue
        if any(kw in all_text for kw in config['keywords']):
            if not any(ex in all_text for ex in config.get('exclude', [])):
                return fmt, {'maxlen': config.get('maxlen', 0)}

    return None, {}


# ---------------------------------------------------------------------------
# Line classification helpers
# ---------------------------------------------------------------------------

def classify_lines(page_lines: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    """
    Classify page lines into horizontal and vertical.
    Returns (h_lines, v_lines) with normalized top/bottom/x0/x1 keys.
    """
    h_lines = []
    v_lines = []

    for line in page_lines:
        x0 = min(line.get('x0', 0), line.get('x1', 0))
        x1 = max(line.get('x0', 0), line.get('x1', 0))
        y0 = min(line.get('top', line.get('y0', 0)), line.get('bottom', line.get('y1', 0)))
        y1 = max(line.get('top', line.get('y0', 0)), line.get('bottom', line.get('y1', 0)))

        dx = x1 - x0
        dy = y1 - y0

        if dy < 2 and dx > 5:
            # Horizontal line
            h_lines.append({
                'x0': x0, 'x1': x1,
                'top': y0, 'bottom': y1,
                'y': (y0 + y1) / 2,
                'width': dx,
            })
        elif dx < 2 and dy > 5:
            # Vertical line
            v_lines.append({
                'x0': x0, 'x1': x1,
                'top': y0, 'bottom': y1,
                'y': (y0 + y1) / 2,
                'height': dy,
            })

    return h_lines, v_lines


def merge_collinear_h_segments(
    h_lines: List[Dict],
    v_lines: List[Dict],
    words: Optional[List[Dict]] = None,
    y_tol: float = 1.5,
    x_gap: float = 1.0,
    v_tol: float = 3.0,
    label_x_tol: float = 10.0,
    label_y_tol: float = 5.0,
) -> List[Dict]:
    """
    Merge collinear horizontal line segments that touch at the same Y level,
    but ONLY when:
      1. No vertical line exists at the junction point, AND
      2. No text label starts near the junction (indicating a real field boundary).

    The text-label check prevents merging segments that represent distinct
    fields (e.g. "Account Holder" | "Title") while still merging phantom
    cell splits in tables where column headers are far from data-row lines.

    Parameters
    ----------
    words : list of dicts, optional
        Page words (with x0, top, bottom, text keys) used for label checks.
    label_x_tol : float
        Max horizontal distance from junction to a word's x0 to consider
        it a label at that junction.
    label_y_tol : float
        Max vertical distance from the line's y to the word's nearest
        edge for the label check.  Tight (5 pt) so that table column
        headers (~6+ pt away) don't block merging of data rows,
        while labels right next to the line (<5 pt) do block.
    """
    if len(h_lines) < 2:
        return h_lines

    # Sort by Y then X for sequential merging
    h_sorted = sorted(h_lines, key=lambda l: (l['y'], l['x0']))

    # ------------------------------------------------------------------
    # Structural column detection: find junction x-coordinates that
    # appear in 3+ distinct Y levels.  These are table column boundaries
    # (e.g. Company Name | Contact Person | City | State | ZIP with no
    # vertical lines) and must never be merged, even when text labels
    # are too far from the h-line to trigger the label check.
    # ------------------------------------------------------------------
    from collections import defaultdict as _ddict
    _junction_y_sets: dict = _ddict(set)
    _prev = h_sorted[0]
    for _seg in h_sorted[1:]:
        if (abs(_seg['y'] - _prev['y']) < y_tol and
                _seg['x0'] <= _prev['x1'] + x_gap):
            # Round junction x to 0.5pt to cluster near-identical values
            jx = round(_prev['x1'] * 2) / 2
            _junction_y_sets[jx].add(round(_prev['y'] * 2) / 2)
        _prev = _seg

    _col_x_tol = 2.0  # tolerance for matching structural column x
    structural_col_xs = set()
    for jx, ys in _junction_y_sets.items():
        if len(ys) >= 3:
            structural_col_xs.add(jx)

    def is_structural_column(x: float) -> bool:
        """Check if x falls on a structural column boundary."""
        rx = round(x * 2) / 2
        for scx in structural_col_xs:
            if abs(rx - scx) < _col_x_tol:
                return True
        return False

    # Collect vertical line X positions with their Y ranges for junction checks
    v_segments = [(float(v['x0']), float(v['top']), float(v['bottom']))
                  for v in v_lines]

    def has_vline_at(x: float, y: float) -> bool:
        """Check if a vertical line crosses position (x, y)."""
        for vx, vy0, vy1 in v_segments:
            if abs(vx - x) < v_tol and vy0 - v_tol <= y <= vy1 + v_tol:
                return True
        return False

    def has_label_at(x: float, y: float) -> bool:
        """Check if a text label starts near position (x, y).

        A label is a word with ≥ 3 characters whose x0 is close to the
        junction x and whose nearest vertical edge (top or bottom) is
        close to the line y.  This catches labels placed just above or
        below the line that indicate a real field boundary, while
        distant column headers (~15 pt away) don't block merging.
        """
        if not words:
            return False
        for w in words:
            wtext = w.get('text', '')
            if len(wtext) < 3:
                continue
            wx0 = float(w.get('x0', 0))
            if abs(wx0 - x) >= label_x_tol:
                continue
            w_top = float(w.get('top', 0))
            w_bot = float(w.get('bottom', w_top + 10))
            # Use nearest edge distance — catches labels just above
            # or below the line without being fooled by far-away headers
            nearest_y_dist = min(abs(w_top - y), abs(w_bot - y))
            if nearest_y_dist < label_y_tol:
                return True
        return False

    merged: List[Dict] = []
    curr = h_sorted[0].copy()

    for nxt in h_sorted[1:]:
        # Same Y level and touching/overlapping X ranges?
        if (abs(nxt['y'] - curr['y']) < y_tol and
                nxt['x0'] <= curr['x1'] + x_gap):
            junction_x = curr['x1']
            junction_y = curr['y']
            # Only merge if NO structural column, NO vertical line,
            # AND no text label at the junction
            if (not is_structural_column(junction_x) and
                    not has_vline_at(junction_x, junction_y) and
                    not has_label_at(junction_x, junction_y)):
                curr['x1'] = max(curr['x1'], nxt['x1'])
                curr['width'] = curr['x1'] - curr['x0']
                continue
        merged.append(curr)
        curr = nxt.copy()

    merged.append(curr)
    return merged


# ---------------------------------------------------------------------------
# Spatial indexing helper for the resolver
# ---------------------------------------------------------------------------

class SpatialIndex:
    """
    Simple grid-based spatial index for fast overlap queries.
    Divides the page into grid cells and tracks which fields occupy each cell.
    """

    def __init__(self, page_width: float, page_height: float, cell_size: float = 50.0):
        self.cell_size = cell_size
        self.cols = max(1, int(page_width / cell_size) + 1)
        self.rows = max(1, int(page_height / cell_size) + 1)
        self.grid: Dict[Tuple[int, int], List] = defaultdict(list)

    def _get_cells(self, x0, y0, x1, y1):
        """Get all grid cells that a bbox touches."""
        c0 = max(0, int(x0 / self.cell_size))
        c1 = min(self.cols - 1, int(x1 / self.cell_size))
        r0 = max(0, int(y0 / self.cell_size))
        r1 = min(self.rows - 1, int(y1 / self.cell_size))
        cells = []
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                cells.append((r, c))
        return cells

    def insert(self, item, x0, y0, x1, y1):
        """Insert an item into the spatial index."""
        for cell in self._get_cells(x0, y0, x1, y1):
            self.grid[cell].append(item)

    def query(self, x0, y0, x1, y1):
        """Find all items that could potentially overlap with the given bbox."""
        seen = set()
        results = []
        for cell in self._get_cells(x0, y0, x1, y1):
            for item in self.grid[cell]:
                item_id = id(item)
                if item_id not in seen:
                    seen.add(item_id)
                    results.append(item)
        return results
