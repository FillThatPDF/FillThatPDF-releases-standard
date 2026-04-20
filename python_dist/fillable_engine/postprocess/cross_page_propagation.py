"""
Post-processor: Cross-page pattern propagation.

After all detectors and other post-processors run, some pages may have
significantly fewer fields than others in the same document.  This is common
when later pages reuse a similar layout but the rule-based detectors only
fire on one page (e.g. different line thicknesses, rasterized underlines).

This post-processor:
  1. Computes field density per page.
  2. Identifies "dense" pages (>= 5 fields) and "sparse" pages
     (< 30% of the median field count AND < 5 fields).
  3. On dense pages, extracts spatial patterns: for each label that ends
     with ':' or '?' and has a nearby field, record the relative offset
     (dx, dy) and size (w, h) from label to field.
  4. On sparse pages, finds orphan labels (words ending ':' or '?' with
     no field within 30pt).
  5. For each orphan, tries applying collected patterns.  Verifies target
     area is empty (no text, no existing field).
  6. Emits new ResolvedField with source='cross_page_propagated'.

Rank 200 (very low) — only fills truly empty areas where no other detector
found anything.

Runs AFTER TextColumnFilter, BEFORE HeightStandardizer and LabelTrimmer.
"""

from typing import List, Dict, Set, Tuple, Optional
from collections import defaultdict
import statistics

from ..models import ResolvedField, FieldType, PageModel
from ..helpers import clean_field_name


class CrossPagePropagation:
    """Propagate field patterns from well-detected pages to sparse pages."""

    def process(
        self,
        fields: List[ResolvedField],
        pages: List[PageModel],
    ) -> List[ResolvedField]:
        if len(pages) < 2:
            return fields  # Nothing to propagate in single-page docs

        pages_by_num = {p.page_num: p for p in pages}

        # ---- Step 1: Field density per page ----
        fields_per_page: Dict[int, List[ResolvedField]] = defaultdict(list)
        for f in fields:
            fields_per_page[f.page].append(f)

        counts = [len(fields_per_page.get(p.page_num, []))
                  for p in pages
                  if not p.is_text_only]

        if not counts or max(counts) < 5:
            return fields  # Not enough data to propagate

        median_count = statistics.median(counts)
        if median_count < 3:
            return fields  # Most pages are sparse — nothing to learn from

        # ---- Step 2: Classify pages ----
        dense_pages: List[int] = []
        sparse_pages: List[int] = []

        for p in pages:
            pg = p.page_num
            if p.is_text_only:
                continue
            n = len(fields_per_page.get(pg, []))
            if n >= 5:
                dense_pages.append(pg)
            elif n < median_count * 0.30 and n < 5:
                sparse_pages.append(pg)

        if not dense_pages or not sparse_pages:
            return fields

        # ---- Step 3: Extract patterns from dense pages ----
        # Pattern: normalized label text -> list of (dx, dy, field_w, field_h)
        patterns: Dict[str, List[Tuple[float, float, float, float]]] = \
            defaultdict(list)

        for pg in dense_pages:
            page_model = pages_by_num.get(pg)
            if not page_model:
                continue
            pg_fields = fields_per_page.get(pg, [])

            for word in page_model.words:
                w_txt = word.get('text', '')
                if not (w_txt.endswith(':') or w_txt.endswith('?')):
                    continue
                if len(w_txt) < 3:
                    continue

                wx1 = float(word['x1'])
                wy_mid = (float(word['top']) + float(word['bottom'])) / 2

                # Find nearest field to the right or below
                best_field = None
                best_dist = 999
                for f in pg_fields:
                    if f.field_type not in (FieldType.TEXT, FieldType.SIGNATURE):
                        continue
                    f_mid_y = (f.y0 + f.y1) / 2

                    # Field must be to the right or slightly below
                    dx = f.x0 - wx1
                    dy = f_mid_y - wy_mid

                    if dx < -5:
                        continue  # Field is to the left of label
                    if dy < -20:
                        continue  # Field is well above label
                    if dy > 40:
                        continue  # Field is too far below

                    dist = abs(dx) + abs(dy)
                    if dist < best_dist and dist < 200:
                        best_dist = dist
                        best_field = f

                if best_field:
                    label_key = w_txt.rstrip(':').rstrip('?').strip().lower()
                    if len(label_key) < 2:
                        continue
                    dx = best_field.x0 - wx1
                    dy = (best_field.y0 + best_field.y1) / 2 - wy_mid
                    fw = best_field.x1 - best_field.x0
                    fh = best_field.y1 - best_field.y0
                    patterns[label_key].append((dx, dy, fw, fh))

        if not patterns:
            return fields

        # ---- Step 4 & 5: Find orphan labels on sparse pages, apply patterns ----
        used_names: Set[str] = {f.name for f in fields}
        propagated = 0

        for pg in sparse_pages:
            page_model = pages_by_num.get(pg)
            if not page_model:
                continue

            pg_fields = fields_per_page.get(pg, [])

            for word in page_model.words:
                w_txt = word.get('text', '')
                if not (w_txt.endswith(':') or w_txt.endswith('?')):
                    continue
                if len(w_txt) < 3:
                    continue

                wx0 = float(word['x0'])
                wx1 = float(word['x1'])
                wy0 = float(word['top'])
                wy1 = float(word['bottom'])
                wy_mid = (wy0 + wy1) / 2

                # Check if this label already has a nearby field (not orphan)
                has_field = False
                for f in pg_fields:
                    f_mid_y = (f.y0 + f.y1) / 2
                    if (abs(f_mid_y - wy_mid) < 15
                            and f.x0 > wx1 - 5
                            and f.x0 < wx1 + 200):
                        has_field = True
                        break
                    # Also check field directly below
                    if (f.y0 > wy1 - 5
                            and f.y0 < wy1 + 30
                            and abs(f.x0 - wx0) < 30):
                        has_field = True
                        break
                if has_field:
                    continue

                # Try to apply a matching pattern
                label_key = w_txt.rstrip(':').rstrip('?').strip().lower()
                if label_key not in patterns:
                    continue

                # Use median of pattern values
                pat_list = patterns[label_key]
                if not pat_list:
                    continue

                # Take median of each dimension
                med_dx = statistics.median(p[0] for p in pat_list)
                med_dy = statistics.median(p[1] for p in pat_list)
                med_w = statistics.median(p[2] for p in pat_list)
                med_h = statistics.median(p[3] for p in pat_list)

                # Compute proposed field position
                f_x0 = wx1 + med_dx
                f_y0 = wy_mid + med_dy - med_h / 2
                f_x1 = f_x0 + med_w
                f_y1 = f_y0 + med_h

                # Sanity checks
                if f_x1 - f_x0 < 20:
                    continue
                if f_y1 - f_y0 < 5:
                    continue
                if f_x0 < 10 or f_x1 > page_model.width - 10:
                    continue
                if f_y0 < 10 or f_y1 > page_model.height - 10:
                    continue

                # Verify: no existing field overlaps
                overlap = False
                for f in fields:
                    if f.page != pg:
                        continue
                    if (f.x0 < f_x1 and f.x1 > f_x0
                            and f.y0 < f_y1 and f.y1 > f_y0):
                        overlap = True
                        break
                if overlap:
                    continue

                # Verify: proposed area is empty (no text)
                inner = page_model.get_words_in_bbox(
                    (f_x0, f_y0, f_x1, f_y1), overlap_threshold=0.1)
                if inner:
                    continue

                # Create field
                label_txt = w_txt.rstrip(':').rstrip('?').strip()
                name_hint = clean_field_name(label_txt)
                name = self._unique_name(name_hint or 'Field', used_names)

                new_field = ResolvedField(
                    page=pg,
                    x0=f_x0, y0=f_y0, x1=f_x1, y1=f_y1,
                    field_type=FieldType.TEXT,
                    source='cross_page_propagated',
                    name=name,
                    label=label_txt,
                )
                fields.append(new_field)
                fields_per_page[pg].append(new_field)
                propagated += 1

        if propagated:
            print(f"   CrossPagePropagation: propagated {propagated} "
                  f"field(s) from {len(dense_pages)} dense page(s) "
                  f"to {len(sparse_pages)} sparse page(s)")

        return fields

    @staticmethod
    def _unique_name(base: str, used: Set[str]) -> str:
        """Generate a unique field name."""
        if base not in used:
            used.add(base)
            return base
        n = 2
        while f'{base}_{n}' in used:
            n += 1
        name = f'{base}_{n}'
        used.add(name)
        return name
