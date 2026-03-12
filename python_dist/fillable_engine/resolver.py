"""
Phase 3: Unified Resolver

This is the architectural centerpiece — ONE pass replaces ALL 12+ cleanup methods from v23:
  _resolve_all_conflicts, _aggressive_overlap_cleanup, _remove_prefilled_column_fields,
  _remove_colored_header_fields, _purge_thin_fields_under_header_bars, _remove_junk_fields,
  _remove_duplicate_fields, _cleanup_field_boundaries, _remove_checkboxes_over_images,
  _remove_checkboxes_in_text_columns, _remove_checkboxes_overlapping_box_entry, etc.

Algorithm: Priority-Sorted Greedy Placement
1. Pre-filter obvious junk (negative coords, tiny, inside headers, prefilled)
2. Sort all candidates by rank (highest first), then area (largest)
3. Walk sorted list: for each candidate, check overlap vs committed fields
4. If no conflict → commit. If conflict → higher rank wins (already committed = same/higher rank).
5. Result: every field is committed exactly once or discarded.
"""

from typing import List, Dict, Optional, Tuple
from collections import defaultdict

from .models import (
    PageModel, FieldCandidate, ResolvedField, FieldType,
    SOURCE_RANKS, get_source_rank,
)
from .helpers import (
    bbox_overlap_area, bbox_area, bbox_overlap_ratio, SpatialIndex,
    clean_field_name, FieldNameGenerator, detect_field_format,
    FORMAT_PATTERNS,
)


class UnifiedResolver:
    """Single-pass conflict resolution. No field is ever created then deleted."""

    def __init__(self, settings: Dict):
        self.settings = settings
        self.table_cell_padding = float(settings.get('table_cell_padding', 0))
        self.namer = FieldNameGenerator()

    def resolve(self, candidates: List[FieldCandidate],
                pages: List[PageModel]) -> List[ResolvedField]:
        """
        Resolve all candidates into committed fields.

        Args:
            candidates: All FieldCandidates from all detectors (unsorted).
            pages: PageModel objects for structural context.

        Returns:
            List of ResolvedField objects — the final committed fields.
        """
        page_map = {p.page_num: p for p in pages}

        # Step 1: Pre-filter obviously invalid candidates
        filtered = self._prefilter(candidates, page_map)
        print(f"   [Resolver] {len(candidates)} candidates → {len(filtered)} after pre-filter")

        # Step 2: Sort by rank (highest first), then area (largest first for ties)
        filtered.sort(key=lambda c: (-get_source_rank(c.source), -c.area, c.y0, c.x0))

        # Step 3: Greedy placement with spatial indexing
        committed_by_page: Dict[int, List[ResolvedField]] = defaultdict(list)
        spatial_indexes: Dict[int, SpatialIndex] = {}

        stats = {'committed': 0, 'discarded_overlap': 0, 'discarded_clip': 0}

        for candidate in filtered:
            pg = candidate.page
            if pg not in spatial_indexes and pg in page_map:
                pm = page_map[pg]
                spatial_indexes[pg] = SpatialIndex(pm.width, pm.height)

            if pg not in spatial_indexes:
                continue

            si = spatial_indexes[pg]

            # Find potential conflicts
            nearby = si.query(candidate.x0, candidate.y0, candidate.x1, candidate.y1)

            action = self._check_conflicts(candidate, nearby, page_map.get(pg))

            if action == 'commit':
                resolved = self._commit(candidate)
                committed_by_page[pg].append(resolved)
                si.insert(resolved, resolved.x0, resolved.y0, resolved.x1, resolved.y1)
                stats['committed'] += 1
            else:
                stats['discarded_overlap'] += 1

        # Step 3.5: Remove text fields that overlap committed checkboxes.
        # The coexistence logic (above) allows both to commit during greedy
        # placement, but the text field visually covers the checkbox area
        # with a blue highlight — making checkboxes unusable.  Any text
        # field that geometrically overlaps a checkbox is a detection
        # artifact (e.g. a table-row detector that doesn't know about
        # checkboxes).  Remove the text field; keep the checkboxes.
        removed_cb_overlap = 0
        for pg in committed_by_page:
            page_fields = committed_by_page[pg]
            checkboxes = [f for f in page_fields
                          if f.field_type == FieldType.CHECKBOX]
            if not checkboxes:
                continue

            to_remove = set()
            for tf in page_fields:
                if tf.field_type == FieldType.CHECKBOX:
                    continue
                if tf.is_image_box:
                    continue  # Image upload boxes must not be removed by checkbox overlap
                for cb in checkboxes:
                    # Geometric overlap test
                    ox0 = max(tf.x0, cb.x0)
                    oy0 = max(tf.y0, cb.y0)
                    ox1 = min(tf.x1, cb.x1)
                    oy1 = min(tf.y1, cb.y1)
                    if ox1 > ox0 and oy1 > oy0:
                        ov_area = (ox1 - ox0) * (oy1 - oy0)
                        cb_area = cb.width * cb.height
                        if cb_area > 0 and ov_area / cb_area > 0.25:
                            to_remove.add(id(tf))
                            break  # No need to check more CBs

            if to_remove:
                removed_cb_overlap += len(to_remove)
                committed_by_page[pg] = [
                    f for f in page_fields if id(f) not in to_remove
                ]

        # Step 4: Column-level currency detection — for text fields
        # that still lack a format, check if they sit in a table column
        # whose header contains currency keywords (e.g. "Total Rebate",
        # "Amount", "$").  Group fields by approximate X position, look
        # at the page text directly above the topmost field in each
        # column, and propagate currency format to all fields in matching
        # columns.  Guards: ≥3 fields in column, header ≤6 words, and
        # narrow search strip (25pt above) to avoid body-text matches.
        for pg, page_fields in committed_by_page.items():
            pm = page_map.get(pg)
            if not pm:
                continue
            # Collect unformatted text fields
            unformatted = [
                f for f in page_fields
                if f.field_type == FieldType.TEXT and not f.format_type
            ]
            if not unformatted:
                continue
            # Group by approximate X position (within 8 pt = same column)
            columns: Dict[int, list] = defaultdict(list)
            for f in unformatted:
                col_key = round(f.x0 / 8) * 8
                columns[col_key].append(f)
            for col_key, col_fields in columns.items():
                if len(col_fields) < 3:
                    continue  # Need ≥3 fields to be a real table column
                topmost_y = min(f.y0 for f in col_fields)
                avg_x0 = sum(f.x0 for f in col_fields) / len(col_fields)
                avg_x1 = sum(f.x1 for f in col_fields) / len(col_fields)
                # Look at text in a NARROW strip directly above the
                # topmost field (25pt) — avoids matching body text.
                hdr_bbox = (avg_x0 - 5, max(0, topmost_y - 25),
                            avg_x1 + 5, topmost_y - 1)
                hdr_words = pm.get_words_in_bbox(hdr_bbox, 0.3)
                hdr_text = ' '.join(
                    w.get('text', '') for w in hdr_words).lower()
                if not hdr_text or len(hdr_text.split()) > 6:
                    continue  # Skip long text — it's body text, not a header
                # Check if header matches currency keywords
                currency_kw = FORMAT_PATTERNS['currency']['keywords']
                excludes = FORMAT_PATTERNS['currency'].get('exclude', [])
                if (any(kw in hdr_text for kw in currency_kw) and
                        not any(ex in hdr_text for ex in excludes)):
                    for f in col_fields:
                        f.format_type = 'currency'
                        f.format_options = f.format_options or {}

        # Step 4b: Currency "$" enrichment — for currency fields, check
        # if a hardcoded "$" symbol exists in the page text adjacent to
        # the field.  If so, the viewer should NOT add another "$".
        for pg, page_fields in committed_by_page.items():
            pm = page_map.get(pg)
            if not pm:
                continue
            for tf in page_fields:
                if tf.format_type != 'currency':
                    continue
                opts = tf.format_options or {}
                if opts.get('has_dollar_in_cell'):
                    continue  # Already flagged by detector/format detection
                # Check page words for "$" near the field's left edge.
                # Look in a narrow strip to the left of the field (up to
                # 20 pt) and vertically overlapping.
                search_bbox = (
                    max(0, tf.x0 - 20), tf.y0 - 2,
                    tf.x0 + 8, tf.y1 + 2,
                )
                nearby_words = pm.get_words_in_bbox(search_bbox, 0.3)
                if any(w.get('text', '').strip() in ('$', '$:')
                       for w in nearby_words):
                    if tf.format_options is None:
                        tf.format_options = {}
                    tf.format_options['has_dollar_in_cell'] = True

        # Step 5: Flatten results
        result = []
        for pg in sorted(committed_by_page.keys()):
            result.extend(committed_by_page[pg])

        total_committed = stats['committed'] - removed_cb_overlap
        print(f"   [Resolver] Committed {stats['committed']}, "
              f"discarded {stats['discarded_overlap']} overlaps"
              + (f", removed {removed_cb_overlap} text-over-checkbox"
                 if removed_cb_overlap else ""))
        return result

    # -------------------------------------------------------------------
    # Pre-filtering (replaces scattered validation in v23)
    # -------------------------------------------------------------------

    def _prefilter(self, candidates: List[FieldCandidate],
                   page_map: Dict[int, PageModel]) -> List[FieldCandidate]:
        """Remove obviously invalid candidates before resolution."""
        valid = []
        for c in candidates:
            # Dimension validation
            if c.width < 5 or c.height < 3:
                continue
            if c.x0 < 0 or c.y0 < 0:
                continue  # Above/left of page
            if c.area < 15:
                continue  # Too tiny

            pg = page_map.get(c.page)
            if pg is None:
                continue

            # Skip text-only pages (but allow checkboxes and signatures)
            if pg.is_text_only and c.field_type == FieldType.TEXT:
                if c.source not in ('signature', 'date'):
                    continue

            # Skip informational pages (all table cells pre-filled)
            if pg.is_info_page:
                continue

            # Footer zone exclusion — skip fields in detected footer areas
            # (company logos, page numbers, URLs, copyright)
            if pg.footer_y > 0 and c.y0 >= pg.footer_y:
                continue

            # Grid-cell clipping for wide label_entry_below.
            # When a label_entry_below spans multiple visual grid cell
            # columns (e.g. covering both "Model:" and "Tons:" cells),
            # clip it to the narrowest enclosing grid cell.  This
            # prevents LineSnapper from merging unrelated column fields
            # into one oversized field that LabelTrimmer then trims away.
            if (c.source == 'label_entry_below'
                    and pg.visual_grid_cells and c.width > 150):
                _overlapping = []
                for gx0, gy0, gx1, gy1 in pg.visual_grid_cells:
                    ov_y = min(c.y1, gy1) - max(c.y0, gy0)
                    if ov_y <= 0:
                        continue
                    ov_x = min(c.x1, gx1) - max(c.x0, gx0)
                    if ov_x <= 0:
                        continue
                    _overlapping.append((gx0, gy0, gx1, gy1))
                if len(_overlapping) >= 2:
                    # Spans multiple cells — clip to cell with
                    # largest horizontal overlap (best-matching cell)
                    _overlapping.sort(
                        key=lambda g: min(c.x1, g[2]) - max(c.x0, g[0]),
                        reverse=True)
                    bx0, by0, bx1, by1 = _overlapping[0]
                    new_x0 = max(c.x0, bx0)
                    new_x1 = min(c.x1, bx1)
                    if new_x1 - new_x0 > 30:
                        c.x0 = new_x0
                        c.x1 = new_x1

            # Header bar exclusion (non-protected sources)
            if c.field_type == FieldType.TEXT and c.source not in (
                'signature', 'date', 'grid_fallback',
                'split_cell_multi_row', 'form_line_segment', 'box_entry',
                'comb_box_grid', 'label_entry_below',
                'audit_gap_fill', 'user_injected', 'large_image_rect',
                'embedded_underscore', 'general_underscore',
                'char_underscore_run',
            ):
                if pg.is_in_header_bar(c.x0, c.y0, c.x1, c.y1):
                    continue

            # Text-header-cell exclusion — catches fields inside wide grid cells
            # that already contain text (section headers, sub-header rows,
            # instruction rows).  This supplements the header bar check by
            # catching junk from "protected" sources that bypass it.
            # Form-fill sources are protected: they explicitly detect
            # fill-in areas next to label text and must be allowed.
            if c.field_type == FieldType.TEXT and c.source not in (
                'box_entry', 'comb_box_grid', 'user_injected',
                'signature', 'date', 'form_line_segment',
                'form_line_presplit',
                'label_entry_below', 'label_entry_cell',
                'grid_fallback', 'grid_fallback_table_cell',
                'grid_region_fallback', 'table_col_fallback',
                'split_cell_multi_row', 'split_line_gap',
                'horizontal_line_table',
            ):
                if pg.is_inside_text_header_cell(c.x0, c.y0, c.x1, c.y1, 0.7):
                    continue

            # Inter-table gap exclusion — text fields in narrow gaps between
            # table segments are almost always section-header-row artefacts
            # (e.g. "LED Lamp Replacing CFL" spanning the gap).  Only suppress
            # when the gap area actually contains text (is a section header).
            if (c.field_type == FieldType.TEXT
                    and c.source in (
                        'horizontal_line_table', 'horizontal_line_table_subrow',
                        'table_data_row', 'sub_table_data_row',
                        'special_structural_header',
                    )
                    and pg.is_in_table_gap(c.x0, c.y0, c.x1, c.y1, 30)):
                # The gap has descriptive text — skip this field
                gap_text = pg.get_text_in_bbox(
                    (c.x0 - 10, c.y0 - 3, c.x1 + 10, c.y1 + 3), 0.15)
                # Only skip if the gap area itself has text (section title)
                # OR if the field is narrow (sub-column fragment)
                if gap_text.strip() or c.width < 100:
                    continue

            # Column classification — suppress text fields in read-only columns.
            # TableCellDetector already respects this, but OTHER detectors
            # (HLT, FormLine, etc.) may place fields in columns that the
            # table analysis determined are pre-printed/informational.
            # Exception: cells with ':' or '___' may be label+entry pairs.
            # Width guard: only apply to single-column fields (< 200pt).
            # Source guard: "smart" label-aware sources (label_entry_below,
            # label_entry_cell, split_cell_multi_row) are protected ONLY
            # when they are empty (genuine entry areas).  When they contain
            # text, they may be misidentified labels in read-only columns
            # (e.g. "Wi-FI thermostat" in a pre-printed description column).
            _label_entry_sources = ('label_entry_below', 'label_entry_cell',
                                    'split_cell_multi_row')
            if (c.field_type == FieldType.TEXT
                    and c.width < 200
                    and pg.column_classifications
                    and c.source not in ('signature', 'date', 'user_injected',
                                         'box_entry', 'comb_box_grid')):
                # Smart label sources: protect only when the field is a
                # genuine entry area (empty AND wide).  Narrow empty
                # label_entry fields in read-only columns are table cell
                # artifacts (e.g. whitespace below "Wi-FI thermostat").
                # Wide ones (≥ 80pt) span the form layout and are real.
                if c.source in _label_entry_sources:
                    _le_text = pg.get_text_in_bbox(
                        (c.x0, c.y0, c.x1, c.y1), 0.15) or ''
                    if not _le_text.strip() and c.width >= 80:
                        pass  # empty + wide → genuine entry, skip column check
                    elif not _le_text.strip():
                        # Empty but narrow (< 80pt) — likely a table cell
                        # artefact, not a genuine entry area.  Apply the
                        # column classification check.
                        col_cls = pg.column_classifications.get(round(c.x0))
                        if col_cls is None:
                            for hx, cls in pg.column_classifications.items():
                                if abs(round(c.x0) - hx) < 15:
                                    col_cls = cls
                                    break
                        if col_cls == 'read-only':
                            continue
                    else:
                        # Has text — apply column classification
                        col_cls = pg.column_classifications.get(round(c.x0))
                        if col_cls is None:
                            for hx, cls in pg.column_classifications.items():
                                if abs(round(c.x0) - hx) < 15:
                                    col_cls = cls
                                    break
                        if col_cls == 'read-only':
                            if ':' not in _le_text and '___' not in _le_text:
                                continue
                else:
                    col_cls = pg.column_classifications.get(round(c.x0))
                    if col_cls is None:
                        # Fuzzy match — column x-positions may differ by a few pts
                        for hx, cls in pg.column_classifications.items():
                            if abs(round(c.x0) - hx) < 15:
                                col_cls = cls
                                break
                    if col_cls == 'read-only':
                        cell_text = pg.get_text_in_bbox(
                            (c.x0, c.y0, c.x1, c.y1), 0.15) or ''
                        if ':' not in cell_text and '___' not in cell_text:
                            continue

            # Strict-grid header cells — very short (< 10pt) strict_grid
            # candidates are column header remnants, not data entry cells.
            # They sit at the exact header/data boundary and are too thin
            # for any meaningful text input.
            if c.source == 'strict_grid' and c.height < 10:
                continue

            # Thin full-width empty_box strips — structural artefacts.
            # Very short (< 5pt) boxes spanning an entire table row
            # are tiny gaps between text and grid lines, not entry
            # fields.  Without this, LineSnapper expands them to fill
            # the row, absorbing real fields (e.g. embedded_underscore).
            if (c.source == 'empty_box' and c.height < 5
                    and c.width > 300):
                continue

            # special_structural_header with underscore text — entry
            # fields misidentified as structural headers.  Remove them
            # so the underlying embedded_underscore candidates survive.
            if c.source == 'special_structural_header':
                _ss_text = pg.get_text_in_bbox(
                    (c.x0, c.y0, c.x1, c.y1), 0.15) or ''
                if '___' in _ss_text:
                    continue

            # Grid_fallback in very small tables — structural artefacts.
            # Tiny pdfplumber tables (< 50pt tall, typically 1–2 rows) around
            # comb-box headers produce grid_fallback candidates in cells that
            # are purely structural (not meant for text entry).
            if c.source == 'grid_fallback' and c.field_type == FieldType.TEXT:
                _skip_small_tbl = False
                for tbl in pg.table_bboxes:
                    tx0, ty0, tx1, ty1 = tbl
                    if (ty1 - ty0) < 50:
                        if (c.x0 >= tx0 - 3 and c.x1 <= tx1 + 3
                                and c.y0 >= ty0 - 3 and c.y1 <= ty1 + 3):
                            _skip_small_tbl = True
                            break
                if _skip_small_tbl:
                    continue

            # Raster image overlap — skip ALL fields (text AND checkboxes)
            # that overlap embedded images (company logos, photos, etc.).
            # Logos often produce small rects/curves that detectors mistake
            # for checkboxes or empty boxes.
            # Exception: form_line_segment / form_line_presplit fields are
            # detected from actual vector lines with labels — very high
            # confidence.  They often sit at the edge of page-spanning
            # diagrams (e.g. floor plans) and must not be suppressed.
            if c.source not in ('form_line_segment', 'form_line_presplit'):
                if pg.overlaps_raster_image(c.x0, c.y0, c.x1, c.y1, 0.3):
                    continue

            # Image upload region exclusion — skip TEXT fields (not checkboxes)
            # In v23, image regions exclude text fields but checkboxes are allowed
            # (they mark photo-attach areas where text shouldn't be placed).
            if c.field_type == FieldType.TEXT:
                if pg.is_in_image_upload_region(c.x0, c.y0, c.x1, c.y1, 0.5):
                    continue

            # Label-row exclusion: short split_cell_multi_row candidates
            # (h < 12pt) that contain printed text are label-only fragments
            # (e.g. "Company Name:", "Installed Address").  They are too
            # thin for text entry.  Filtering them prevents doubled-up
            # highlights where both the label fragment and the entry-area
            # field commit in the same cell.
            # NOTE: only discard when text IS present.  Thin candidates
            # WITHOUT text are legitimate entry areas (e.g. fill lines
            # below labels on forms like RECO) and must be kept.
            if c.source == 'split_cell_multi_row' and c.height < 12:
                if pg.rect_has_text(c.x0, c.y0, c.x1, c.y1, min_chars=2):
                    continue

            # Tiny structural_box exclusion — narrow grid cell fragments
            # (e.g. corner of a table) that aren't useful fill areas.
            if c.source == 'structural_box' and c.width < 55:
                continue

            # Narrow inline_label orphans — single words picked up from
            # multi-word form labels (e.g. "Company" from "Contractor
            # Company Name:").  Real value fields are much wider.
            # Exception: narrow fields (35-50 pt) sandwiched between wider
            # companions at the same Y level are legitimate table-row
            # fields (e.g. "State" between "City" and "ZIP").
            if c.source == 'inline_label' and c.width < 50:
                keep_narrow = False
                if c.width >= 35:
                    companions_at_y = [
                        oc for oc in candidates
                        if oc.source == 'inline_label'
                        and oc.page == c.page
                        and abs(oc.y0 - c.y0) < 2
                        and (oc.x1 - oc.x0) >= 60
                        and oc is not c
                    ]
                    has_left = any(
                        comp.x1 <= c.x0 + 5 for comp in companions_at_y)
                    has_right = any(
                        comp.x0 >= c.x1 - 5 for comp in companions_at_y)
                    keep_narrow = has_left and has_right
                if not keep_narrow:
                    continue

            # Tiny horizontal_line_table fragments — margin artefacts
            # or narrow label-area cells that aren't real fill areas.
            if c.source == 'horizontal_line_table' and c.width < 25:
                continue

            # HLT label-cell exclusion — narrow HLT candidates (< 90 pt)
            # whose bbox is full of text are label cells, not value cells.
            if (c.source == 'horizontal_line_table'
                    and c.field_type == FieldType.TEXT and c.width < 90):
                if pg.rect_has_text(c.x0, c.y0, c.x1, c.y1, min_chars=2):
                    continue

            # Dense-text exclusion for table-row / structural_header fields.
            # Specification text in table rows (e.g. "(Single: SEER ≥ 20,
            # EER ≥ 12.5 or HSPF ≥ 10; Multi: …)") contains colons that
            # trick the detectors.  If the candidate's bounding box holds
            # 4+ words of text, it's almost certainly covering descriptive
            # content — not a fill-in area.  The search bbox is padded by
            # 2pt to catch words at the edges.
            # Also reject labels starting with a lowercase word (e.g.
            # "or HSPF ≥ 10; Multi:") — form labels start capitalised.
            if c.source in ('horizontal_line_table_subrow',
                            'special_structural_header') and c.field_type == FieldType.TEXT:
                words = pg.get_words_in_bbox(
                    (c.x0 - 2, c.y0 - 2, c.x1 + 2, c.y1 + 2), 0.25)
                if len(words) >= 4:
                    continue
                label = (c.label or c.name_hint or '').strip()
                if label and label[0].islower():
                    continue

            # Prefilled column exclusion
            # (Grid_fallback, HLT, signature, and other table sources are protected)
            # Checkboxes are ALWAYS exempt — they are explicit user input
            # controls and should never be removed by prefilled-column logic
            # (the page analyzer may misclassify checkbox columns as prefilled).
            protected_from_prefilled = {
                'signature', 'date', 'grid_fallback', 'grid_fallback_table_cell',
                'visual_in_cell_fallback', 'grid_region_fallback', 'table_col_fallback',
                'split_cell_multi_row', 'horizontal_line_table',
                'horizontal_line_table_subrow', 'audit_gap_fill', 'form_line_segment',
                'box_entry', 'comb_box_grid', 'label_entry_below', 'split_line_gap',
                'table_data_row', 'sub_table_data_row', 'table',
                'form_line_presplit', 'label_entry_cell', 'large_image_rect',
            }
            if (c.field_type != FieldType.CHECKBOX
                    and c.source not in protected_from_prefilled
                    and pg.prefilled_col_ranges):
                if self._is_in_prefilled_column(c, pg):
                    continue

            valid.append(c)
        return valid

    def _is_in_prefilled_column(self, c: FieldCandidate, pg: PageModel) -> bool:
        """Check if a candidate is inside a known prefilled data column."""
        for px0, px1, py0, py1 in pg.prefilled_col_ranges:
            # 80% horizontal overlap
            ov_x0 = max(c.x0, px0)
            ov_x1 = min(c.x1, px1)
            if ov_x1 > ov_x0:
                ov_w = ov_x1 - ov_x0
                if ov_w / max(1, c.width) > 0.8:
                    # 40% vertical overlap
                    ov_y0 = max(c.y0, py0)
                    ov_y1 = min(c.y1, py1)
                    if ov_y1 > ov_y0 and (ov_y1 - ov_y0) > c.height * 0.4:
                        return True
        return False

    @staticmethod
    def _has_h_line_separator(candidate, existing, page: PageModel) -> bool:
        """Check if a horizontal line separates two vertically adjacent fields.

        Returns True when H-line segments at a Y coordinate between the two
        fields collectively span the horizontal overlap region.  Segments
        are merged (like GridGapFill._has_h_edge) because form grids
        often fragment H-lines at column boundaries.
        """
        # Determine the gap region between the two fields
        sep_y_min = min(candidate.y1, existing.y1)
        sep_y_max = max(candidate.y0, existing.y0)
        if sep_y_max < sep_y_min:
            # One is inside the other vertically — no gap
            return False

        # X overlap region
        x_ov_start = max(candidate.x0, existing.x0)
        x_ov_end = min(candidate.x1, existing.x1)
        if x_ov_end <= x_ov_start:
            return False

        fuzz = 2.0
        # Collect all H-line segments in the gap's Y range
        segs = []
        for hl in page.h_lines:
            hy = hl.get('y0', hl.get('top', 0))
            if sep_y_min - fuzz <= hy <= sep_y_max + fuzz:
                segs.append((hl.get('x0', 0), hl.get('x1', 0)))

        if not segs:
            return False

        # Merge adjacent/overlapping segments
        segs.sort()
        merged_x0, merged_x1 = segs[0]
        for sx0, sx1 in segs[1:]:
            if sx0 <= merged_x1 + fuzz:
                merged_x1 = max(merged_x1, sx1)
            else:
                # Check if current merged segment covers the overlap
                if (merged_x0 <= x_ov_start + 10
                        and merged_x1 >= x_ov_end - 10):
                    return True
                merged_x0, merged_x1 = sx0, sx1

        return (merged_x0 <= x_ov_start + 10
                and merged_x1 >= x_ov_end - 10)

    # -------------------------------------------------------------------
    # Conflict checking (the core of the resolver)
    # -------------------------------------------------------------------

    def _check_conflicts(self, candidate: FieldCandidate,
                         committed: List[ResolvedField],
                         page: Optional[PageModel]) -> str:
        """
        Check if a candidate conflicts with already-committed fields.

        Returns: 'commit' or 'discard'

        Key insight: since candidates are sorted by rank (highest first),
        any committed field has the same or higher rank than the candidate.
        So if there's a conflict, the committed field wins — we just discard
        the candidate (never remove the committed field).
        """
        if not committed:
            return 'commit'

        c_bbox = (candidate.x0, candidate.y0, candidate.x1, candidate.y1)
        c_area = candidate.area
        c_rank = get_source_rank(candidate.source)
        is_checkbox = candidate.field_type == FieldType.CHECKBOX

        for existing in committed:
            e_bbox = (existing.x0, existing.y0, existing.x1, existing.y1)
            e_area = existing.width * existing.height

            # --- Vertical adjacency check ---
            # Two text fields from DIFFERENT sources touching at a Y boundary
            # with substantial X overlap are redundant (e.g., split_cell label
            # row + label_entry value row covering the same logical area).
            # Same-source adjacency is intentional (e.g., grid_fallback rows
            # in adjacent table rows) and must be allowed.
            #
            # Grid-cell exemption: sources that produce individual grid cells
            # (strict_grid, grid_fallback, empty_box, etc.) often abut each
            # other across H-lines.  These are independent data cells in
            # adjacent rows, NOT redundant coverage of the same area.
            #
            # Width-similarity guard: only apply when both fields have similar
            # widths.  A narrow field adjacent to a much wider field represents
            # a DIFFERENT logical area (e.g. a "$:" entry cell next to a wide
            # gap/header row), not redundant coverage.  Without this guard,
            # wide gap cells committed first block narrow column-local fields.
            GRID_CELL_SOURCES = {
                'strict_grid', 'grid_fallback', 'empty_box', 'gap_fill',
                'grid_gap_fill', 'table_data_row',
            }
            if (not is_checkbox and existing.field_type != FieldType.CHECKBOX
                    and candidate.source != existing.source):
                y_gap = min(abs(candidate.y0 - existing.y1),
                            abs(existing.y0 - candidate.y1))
                if y_gap <= 3:
                    # Grid-cell sources in adjacent rows are independent
                    if (candidate.source in GRID_CELL_SOURCES
                            and existing.source in GRID_CELL_SOURCES):
                        pass  # Adjacent grid cells — not redundant
                    # Width similarity — skip adjacency for very different widths
                    elif max(candidate.width, existing.width) > 0 and (
                            min(candidate.width, existing.width) /
                            max(candidate.width, existing.width) < 0.5):
                        pass  # Very different widths → separate logical areas
                    # H-line separator — if a horizontal line sits between
                    # the two fields, they are in genuinely different rows
                    # of the form (e.g. "Authorization Date:" row above vs
                    # "Offer Valid Until:" row below).  Skip adjacency.
                    elif page and self._has_h_line_separator(
                            candidate, existing, page):
                        pass  # Separated by h_line → different rows
                    else:
                        # Check horizontal overlap
                        x_ov_start = max(candidate.x0, existing.x0)
                        x_ov_end = min(candidate.x1, existing.x1)
                        if x_ov_end > x_ov_start:
                            x_ov = x_ov_end - x_ov_start
                            min_w = min(candidate.width, existing.width)
                            if min_w > 0 and x_ov / min_w > 0.5:
                                # Adjacent with >50% X overlap — discard candidate
                                # (committed field has same or higher rank)
                                return 'discard'

            # Compute overlap
            inter = bbox_overlap_area(c_bbox, e_bbox)
            if inter <= 0:
                continue

            min_area = min(c_area, e_area)
            if min_area <= 0:
                continue

            overlap_ratio = inter / min_area

            # --- Determine overlap threshold ---
            is_existing_checkbox = existing.field_type == FieldType.CHECKBOX

            # CHECKBOX + TEXT coexistence:
            # A small checkbox inside a much larger text cell should NOT block
            # the text cell (or vice versa). This mirrors v23 behavior where
            # checkboxes and table cells coexist in the same row.
            if is_checkbox and not is_existing_checkbox and e_area > c_area * 3:
                # Committed text cell is much larger — checkbox already committed,
                # text candidate would be blocked. But we shouldn't reach here
                # because checkbox rank is now higher than text.
                # Exception: if the committed field is an image_box, the checkbox
                # is a false positive (e.g. camera icon graphic inside a photo
                # upload area). Discard the checkbox — it would otherwise cause
                # Step 3.5 to remove the image_box.
                if existing.is_image_box:
                    return 'discard'
                continue  # Allow coexistence
            if not is_checkbox and is_existing_checkbox and c_area > e_area * 3:
                # Text candidate is much larger than committed checkbox — let
                # the text field coexist alongside the checkbox.
                continue  # Allow coexistence

            if is_checkbox != is_existing_checkbox:
                threshold = 0.4   # Checkbox vs text: moderate threshold
            elif is_checkbox and is_existing_checkbox:
                threshold = 0.3   # Checkbox vs checkbox
            else:
                threshold = 0.4   # Text vs text

            if overlap_ratio < threshold:
                continue  # Overlap too small — no conflict

            # --- There IS a significant conflict ---

            # Special case: image boxes always win over non-image
            if existing.is_image_box and not candidate.is_image_box:
                return 'discard'

            # Special case: comb/box_entry fields always win
            if existing.is_comb and not candidate.is_comb:
                return 'discard'

            # Special case: radio children are immune
            if existing.is_radio_child:
                return 'discard'

            # Default: committed field was placed first (same or higher rank), so it wins.
            # This is the key simplification — no "remove and replace" logic.
            return 'discard'

        return 'commit'

    # -------------------------------------------------------------------
    # Commit a candidate as a resolved field
    # -------------------------------------------------------------------

    def _commit(self, candidate: FieldCandidate) -> ResolvedField:
        """Convert a FieldCandidate into a ResolvedField with a unique name."""
        # Generate name from hints
        if candidate.name_hint:
            base_name = clean_field_name(candidate.name_hint)
        elif candidate.label:
            base_name = clean_field_name(candidate.label)
        elif candidate.column_header and candidate.row_label:
            base_name = clean_field_name(f"{candidate.column_header} {candidate.row_label}")
        elif candidate.column_header:
            base_name = clean_field_name(candidate.column_header)
        elif candidate.row_label:
            base_name = clean_field_name(candidate.row_label)
        else:
            # Generate from source + position
            base_name = f"Cell_{int(candidate.x0)}_{int(candidate.y0)}"

        name = self.namer.generate(base_name)

        # Format fallback: if detector didn't set format_hint, try to detect
        # from column_header or label (catches "Date" columns, "Date:" labels).
        format_hint = candidate.format_hint
        format_options = candidate.format_options
        if not format_hint and candidate.field_type == FieldType.TEXT:
            fmt, fmt_opts = detect_field_format(candidate.column_header, candidate.label)
            if fmt:
                format_hint = fmt
                format_options = fmt_opts

        return ResolvedField(
            page=candidate.page,
            x0=candidate.x0,
            y0=candidate.y0,
            x1=candidate.x1,
            y1=candidate.y1,
            field_type=candidate.field_type,
            source=candidate.source,
            name=name,
            label=candidate.label,
            tooltip=candidate.label,
            format_type=format_hint,
            format_options=format_options,
            max_length=candidate.max_length,
            default_value=candidate.default_value,
            is_comb=candidate.is_comb,
            comb_count=candidate.comb_count,
            is_image_box=candidate.is_image_box,
            checkbox_width=candidate.checkbox_width,
            checkbox_height=candidate.checkbox_height,
            is_radio_child=candidate.is_radio_child,
            radio_group_name=candidate.radio_group_hint,
            winning_source=candidate.source,
            source_line_x0=candidate.source_line_x0,
            source_line_x1=candidate.source_line_x1,
        )
