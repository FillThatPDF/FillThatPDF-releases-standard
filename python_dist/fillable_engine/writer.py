"""
Phase 4: PDF Writer

Creates PDF form widgets (text fields, checkboxes, signatures) from ResolvedField objects.
Ported from _create_fillable_pdf in v23 — the mechanical widget creation code.
"""

import pikepdf
from pathlib import Path
from typing import List, Dict, Optional
from collections import defaultdict

from .models import ResolvedField, FieldType
from .helpers import transform_visual_to_storage

# Sources that represent grid/table cell fields — these get centered
# text alignment by default so data looks correct inside cell boxes.
_GRID_TABLE_SOURCES = frozenset({
    'grid_fallback',
    'grid_fallback_table_cell',
    'table_cell',
    'table_data_row',
    'table_col_fallback',
    'table_col',
    'grid_region_fallback',
    'visual_in_cell_fallback',
    'sub_table_data_row',
    'adjacent_empty_cell',
    'split_cell_multi_row',
    'split_line_gap',
    'grid_gap_fill',
    'box_entry',
})


class PDFWriter:
    """Create fillable PDF widgets from resolved fields."""

    def __init__(self, settings: Dict):
        self.settings = settings
        self._stats = defaultdict(int)

    def write(self, fields: List[ResolvedField], input_pdf: str, output_pdf: str,
              page_rotations: Dict[int, int] = None,
              page_mediaboxes: Dict[int, tuple] = None):
        """
        Create the output PDF with all form fields.

        Args:
            fields: List of ResolvedField objects from the resolver.
            input_pdf: Path to the source PDF.
            output_pdf: Path to write the fillable PDF.
            page_rotations: Dict mapping page_num to rotation angle.
            page_mediaboxes: Dict mapping page_num to (pw, ph) tuple.
        """
        page_rotations = page_rotations or {}
        page_mediaboxes = page_mediaboxes or {}

        with pikepdf.open(str(input_pdf)) as pdf:
            # Remove existing form widgets
            deleted = 0
            for page in pdf.pages:
                if '/Annots' in page:
                    to_remove = []
                    for i, annot in enumerate(page.Annots):
                        if annot.get('/Subtype') == '/Widget':
                            to_remove.append(i)
                    for i in reversed(to_remove):
                        del page.Annots[i]
                        deleted += 1
            if deleted:
                print(f"   Removed {deleted} existing widgets from source PDF")

            # Initialize AcroForm
            if '/AcroForm' not in pdf.Root:
                pdf.Root.AcroForm = pikepdf.Dictionary({
                    '/Fields': pikepdf.Array([]),
                    '/NeedAppearances': True,
                })
            acroform = pdf.Root.AcroForm
            acroform.NeedAppearances = True
            acroform.Fields = pikepdf.Array([])

            # Set /DR (Default Resources) — required for viewers to resolve
            # font references in /DA strings when regenerating appearances.
            # Without this, removing stale /AP (e.g. after calculation edits)
            # leaves viewers unable to render text → invisible fields.
            if '/DR' not in acroform:
                acroform['/DR'] = pikepdf.Dictionary()
            dr = acroform['/DR']
            if '/Font' not in dr:
                dr['/Font'] = pikepdf.Dictionary()
            font_dict = dr['/Font']

            # Helvetica (mapped as /Helv — used in /DA strings)
            helv_font = pdf.make_indirect(pikepdf.Dictionary({
                '/Type': pikepdf.Name('/Font'),
                '/Subtype': pikepdf.Name('/Type1'),
                '/BaseFont': pikepdf.Name('/Helvetica'),
                '/Encoding': pikepdf.Name('/WinAnsiEncoding'),
            }))
            font_dict['/Helv'] = helv_font
            font_dict['/Helvetica'] = helv_font  # compat alias

            # Courier (monospace)
            cour_font = pdf.make_indirect(pikepdf.Dictionary({
                '/Type': pikepdf.Name('/Font'),
                '/Subtype': pikepdf.Name('/Type1'),
                '/BaseFont': pikepdf.Name('/Courier'),
                '/Encoding': pikepdf.Name('/WinAnsiEncoding'),
            }))
            font_dict['/Cour'] = cour_font

            # Times-Roman (serif)
            tiro_font = pdf.make_indirect(pikepdf.Dictionary({
                '/Type': pikepdf.Name('/Font'),
                '/Subtype': pikepdf.Name('/Type1'),
                '/BaseFont': pikepdf.Name('/Times-Roman'),
                '/Encoding': pikepdf.Name('/WinAnsiEncoding'),
            }))
            font_dict['/TiRo'] = tiro_font

            # ZapfDingbats (checkboxes)
            zadb_font_global = pdf.make_indirect(pikepdf.Dictionary({
                '/Type': pikepdf.Name('/Font'),
                '/Subtype': pikepdf.Name('/Type1'),
                '/BaseFont': pikepdf.Name('/ZapfDingbats'),
            }))
            font_dict['/ZaDb'] = zadb_font_global

            # Set AcroForm-level /DA (Default Appearance) — fallback for
            # fields that inherit their DA.  Critical for appearance regen.
            # Use the user's configured font family.
            _font_family_map = {
                'Helvetica': '/Helv', 'Arial': '/Helv',
                'Courier': '/Cour',
                'Times-Roman': '/TiRo', 'Times': '/TiRo',
            }
            _acro_font = _font_family_map.get(
                self.settings.get('field_font_family', 'Helvetica'), '/Helv'
            )
            acroform['/DA'] = pikepdf.String(f'{_acro_font} 0 Tf 0 g')

            # Global name dedup
            used_names = set()
            counters = defaultdict(int)

            def make_unique_name(base):
                counters[base] += 1
                count = counters[base]
                name = base if count == 1 else f"{base}_{count}"
                while name in used_names:
                    counters[base] += 1
                    count = counters[base]
                    name = f"{base}_{count}"
                used_names.add(name)
                return name

            # Sort fields: checkboxes first, then text fields (matching v23 ordering)
            checkboxes = [f for f in fields if f.field_type == FieldType.CHECKBOX]
            text_fields = [f for f in fields if f.field_type != FieldType.CHECKBOX]

            # Separate standalone checkboxes from radio group children
            standalone_checkboxes = [cb for cb in checkboxes if not cb.is_radio_child]
            radio_children = [cb for cb in checkboxes if cb.is_radio_child]

            # Group radio children by radio_group_name
            radio_groups_map = defaultdict(list)
            for cb in radio_children:
                group_key = cb.radio_group_name or f'unnamed_{cb.page}_{int(cb.y0)}'
                radio_groups_map[group_key].append(cb)

            # --- Create standalone checkbox widgets ---
            for cb in standalone_checkboxes:
                page_num = cb.page
                if page_num >= len(pdf.pages):
                    continue
                page = pdf.pages[page_num]
                rot = page_rotations.get(page_num, 0)
                mb = page_mediaboxes.get(page_num, (612, 792))

                field_name = make_unique_name(cb.name)

                # Sizing — center the widget on the FULL checkbox bbox center.
                # Using visual_square * 0.5 from y0 shifts the widget upward
                # when height > width (common for character-based checkboxes),
                # causing the widget to appear "too high in the box."
                cw = cb.checkbox_width or (cb.x1 - cb.x0)
                ch = cb.checkbox_height or (cb.y1 - cb.y0)
                visual_square = min(cw, ch)
                field_size = max(6, min(14, visual_square * 0.85))
                cx = cb.x0 + cw / 2
                cy = (cb.y0 + cb.y1) / 2
                half = field_size / 2

                tx0, ty0, tx1, ty1 = transform_visual_to_storage(
                    page_num, cx - half, cy - half, cx + half, cy + half, rot, mb
                )

                widget = self._create_checkbox_widget(pdf, field_name, tx0, ty0, tx1, ty1)
                annot = pdf.make_indirect(widget)
                if '/Annots' not in page:
                    page.Annots = pdf.make_indirect(pikepdf.Array())
                page.Annots.append(annot)
                acroform.Fields.append(annot)
                self._stats['checkboxes'] += 1

            # --- Create radio button groups ---
            for group_name, group_cbs in radio_groups_map.items():
                if not group_cbs:
                    continue

                # Use first child's page for the parent field
                first_cb = group_cbs[0]
                page_num = first_cb.page

                # Sanitize group name for PDF
                field_name = make_unique_name(group_name)

                # Create parent radio field (lives in AcroForm.Fields, NOT in Annots)
                parent_field = pikepdf.Dictionary({
                    '/FT': pikepdf.Name('/Btn'),
                    '/Ff': 49152,  # Radio (32768) + NoToggleToOff (16384)
                    '/T': pikepdf.String(field_name),
                    '/V': pikepdf.Name('/Off'),
                    '/Kids': pikepdf.Array([]),
                })
                parent_ref = pdf.make_indirect(parent_field)
                acroform.Fields.append(parent_ref)

                for cb in group_cbs:
                    pg = cb.page
                    if pg >= len(pdf.pages):
                        continue
                    page = pdf.pages[pg]
                    rot = page_rotations.get(pg, 0)
                    mb = page_mediaboxes.get(pg, (612, 792))

                    # Sizing — same logic as standalone checkboxes
                    cw = cb.checkbox_width or (cb.x1 - cb.x0)
                    ch = cb.checkbox_height or (cb.y1 - cb.y0)
                    visual_square = min(cw, ch)
                    field_size = max(6, min(14, visual_square * 0.85))
                    cx = cb.x0 + cw / 2
                    cy = (cb.y0 + cb.y1) / 2
                    half = field_size / 2

                    tx0, ty0, tx1, ty1 = transform_visual_to_storage(
                        pg, cx - half, cy - half, cx + half, cy + half, rot, mb
                    )

                    # Sanitize radio value for PDF Name
                    value_name = self._sanitize_radio_value(cb.radio_value or cb.name or 'Option')

                    child_widget = self._create_radio_child_widget(
                        pdf, parent_ref, tx0, ty0, tx1, ty1, value_name
                    )
                    child_ref = pdf.make_indirect(child_widget)

                    # Add child to page Annots (for rendering)
                    if '/Annots' not in page:
                        page.Annots = pdf.make_indirect(pikepdf.Array())
                    page.Annots.append(child_ref)

                    # Add child to parent's /Kids
                    parent_field.Kids.append(child_ref)

                self._stats['radio_groups'] += 1
                self._stats['radio_options'] += len(group_cbs)

            # --- Create text field widgets ---
            for tf in text_fields:
                page_num = tf.page
                if page_num >= len(pdf.pages):
                    continue
                page = pdf.pages[page_num]
                rot = page_rotations.get(page_num, 0)
                mb = page_mediaboxes.get(page_num, (612, 792))

                field_name = make_unique_name(tf.name)

                tx0, ty0, tx1, ty1 = transform_visual_to_storage(
                    page_num, tf.x0, tf.y0, tf.x1, tf.y1, rot, mb
                )

                # Inset gap — always apply an inset so form lines remain
                # visible beneath the field's background highlight.
                # After line-snapping, field edges are typically 0.3-0.5 pt
                # from the nearest H/V-line.  A 1.5 pt inset guarantees
                # ~1 pt of clear space between the blue fill and every
                # adjacent form line, matching the "ruled paper" look.
                base_gap = float(self.settings.get('table_cell_padding', 0))
                gap = max(1.5, base_gap)

                # Text-underscore fields: nudge the field DOWN so its
                # bottom edge sits precisely ON the underscore line.
                # The gap inset (below) moves the bottom edge up by 1.5 pt;
                # a -0.5 pt shift compensates so the visual bottom lands
                # exactly at the underscore character baseline.
                # All underscore-based detectors use y1 = word_bottom + 1.
                _TEXT_UNDERSCORE_SOURCES = {
                    'date', 'signature', 'label_below_underscore',
                }
                if tf.source and (
                    'underscore' in tf.source
                    or tf.source in _TEXT_UNDERSCORE_SOURCES
                ):
                    shift = -0.5
                    ty0 += shift
                    ty1 += shift

                tx0 += gap
                ty0 += gap
                tx1 -= gap
                ty1 -= gap

                # Alignment — default to 'center' for grid/table cell fields
                # so data looks correct inside cell boxes.
                alignment = tf.alignment
                if not alignment and tf.source in _GRID_TABLE_SOURCES:
                    alignment = 'center'

                # Create appropriate widget type
                if tf.field_type == FieldType.SIGNATURE:
                    widget = self._create_signature_widget(pdf, field_name, tx0, ty0, tx1, ty1)
                elif tf.is_image_box:
                    widget = self._create_image_button_widget(pdf, field_name, tx0, ty0, tx1, ty1)
                elif tf.is_comb and tf.comb_count:
                    widget = self._create_comb_widget(pdf, field_name, tx0, ty0, tx1, ty1, tf.comb_count)
                else:
                    # Respect display_tooltips setting — omit /TU entirely
                    # when disabled so PDF viewers don't show hover text.
                    tooltip = tf.tooltip if self.settings.get('display_tooltips', True) else None
                    widget = self._create_text_widget(pdf, field_name, tx0, ty0, tx1, ty1,
                                                      tf.format_type, alignment,
                                                      max_length=tf.max_length,
                                                      tooltip=tooltip,
                                                      format_options=tf.format_options)

                annot = pdf.make_indirect(widget)
                if '/Annots' not in page:
                    page.Annots = pdf.make_indirect(pikepdf.Array())
                page.Annots.append(annot)
                acroform.Fields.append(annot)
                self._stats['text_fields'] += 1

            # Save
            pdf.save(str(output_pdf))

        radio_groups = self._stats.get('radio_groups', 0)
        radio_opts = self._stats.get('radio_options', 0)
        total = self._stats['checkboxes'] + self._stats['text_fields'] + radio_opts
        js_count = self._stats.get('js_formatted', 0)
        js_str = f", {js_count} JS-formatted" if js_count else ""
        radio_str = f", {radio_groups} radio groups ({radio_opts} options)" if radio_groups else ""
        print(f"   Created {total} widgets ({self._stats['checkboxes']} checkboxes{radio_str}, "
              f"{self._stats['text_fields']} text fields{js_str})")
        return output_pdf

    # -------------------------------------------------------------------
    # Widget creation helpers
    # -------------------------------------------------------------------

    def _create_text_widget(self, pdf, name, tx0, ty0, tx1, ty1,
                             format_type=None, alignment=None,
                             max_length=None, tooltip=None,
                             format_options=None):
        """Create a standard text input widget."""
        font_size = self.settings.get('field_font_size', 9)

        # Font family — map user-facing name to PDF resource name
        font_family_map = {
            'Helvetica': '/Helv', 'Arial': '/Helv',
            'Courier': '/Cour',
            'Times-Roman': '/TiRo', 'Times': '/TiRo',
        }
        font_family = self.settings.get('field_font_family', 'Helvetica')
        pdf_font = font_family_map.get(font_family, '/Helv')

        # Font color — convert hex (#RRGGBB) to PDF RGB operands
        font_color = self._hex_to_pdf_rgb(
            self.settings.get('field_font_color', '#000000')
        )
        # Match v23 DA order: font first, then color.  Some viewers
        # parse DA strictly and require this ordering when regenerating
        # text field appearances via NeedAppearances.
        da = f'{pdf_font} {font_size} Tf {font_color} rg'

        align = 0
        if alignment == 'center':
            align = 1
        elif alignment == 'right':
            align = 2

        widget = pikepdf.Dictionary({
            '/Type': pikepdf.Name('/Annot'),
            '/Subtype': pikepdf.Name('/Widget'),
            '/FT': pikepdf.Name('/Tx'),
            '/T': pikepdf.String(name),
            '/Rect': pikepdf.Array([tx0, ty0, tx1, ty1]),
            '/F': 4,
            '/DA': pikepdf.String(da),
            '/Q': align,
        })

        # Max length constraint
        if max_length and max_length > 0:
            widget['/MaxLen'] = max_length

        # Tooltip (shown on hover in Acrobat)
        if tooltip:
            widget['/TU'] = pikepdf.String(tooltip)

        # Background color
        bg_hex = self.settings.get('field_background_color', '#EDF4FF').lstrip('#')
        if len(bg_hex) == 6:
            opacity = int(self.settings.get('field_background_opacity', 100))
            if opacity > 0:
                bg_rgb = [int(bg_hex[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
                widget['/MK'] = pikepdf.Dictionary({
                    '/BG': pikepdf.Array(bg_rgb),
                })

        # Multiline flag for tall fields (height > 25pt)
        # PDF bit 13 of /Ff = 4096 enables text wrapping
        field_height = abs(ty1 - ty0)
        multiline_threshold = float(self.settings.get('multiline_height_threshold', 25))
        if field_height > multiline_threshold:
            widget['/Ff'] = 4096  # Multiline

        # Border — thickness mapping: 0=None, 1=Thin(0.5pt), 2=Medium(1pt), 3=Thick(2pt)
        border_thickness_setting = int(self.settings.get('field_border_thickness', 1))
        border_visible = self.settings.get('field_border_visible', False)
        if not border_visible or border_thickness_setting == 0:
            widget['/BS'] = pikepdf.Dictionary({'/W': 0})
        else:
            thickness_map = {1: 0.5, 2: 1, 3: 2}
            border_w = thickness_map.get(border_thickness_setting, 0.5)
            widget['/BS'] = pikepdf.Dictionary({
                '/W': border_w,
                '/S': pikepdf.Name('/S'),  # Solid style
            })
            # Border color in /MK
            border_color_hex = self.settings.get('field_border_color', '#000000')
            bc_rgb = self._hex_to_rgb_array(border_color_hex)
            mk = widget.get('/MK', pikepdf.Dictionary())
            mk['/BC'] = pikepdf.Array(bc_rgb)
            widget['/MK'] = mk

        # JavaScript formatting actions (date, phone, zip, etc.)
        js_actions = self._get_format_js(format_type, format_options)
        if js_actions:
            format_js, keystroke_js = js_actions
            aa = pikepdf.Dictionary()
            if format_js:
                aa['/F'] = pikepdf.Dictionary({
                    '/S': pikepdf.Name('/JavaScript'),
                    '/JS': pikepdf.String(format_js),
                })
            if keystroke_js:
                aa['/K'] = pikepdf.Dictionary({
                    '/S': pikepdf.Name('/JavaScript'),
                    '/JS': pikepdf.String(keystroke_js),
                })
            if len(list(aa.keys())) > 0:
                widget['/AA'] = aa
            self._stats['js_formatted'] = self._stats.get('js_formatted', 0) + 1

        # NO /AP on text fields — rely on NeedAppearances = True.
        # When a viewer opens the PDF it regenerates proper appearance
        # streams (including text content, caret, etc.) from /DA and /MK.
        # A static Stream /AP here would PREVENT regeneration in many
        # viewers (Acrobat, Bluebeam, Preview) — they render the stale
        # stream as-is and never draw typed text.  This matches v23
        # behaviour where text fields had no usable /AP.

        return widget

    def _get_format_js(self, format_type: str, format_options: dict = None):
        """Return (format_js, keystroke_js) for a given format type, or None.

        For currency fields, ``format_options`` controls the ``$`` prefix:
        * ``has_dollar_in_cell=True`` → the page already contains a hardcoded
          ``$`` symbol next to this field, so we format as a plain number
          (no ``$`` injected by the viewer).
        * Otherwise → viewer prepends the configured currency symbol.

        Settings honoured:
        * ``date_format``              — e.g. 'MM/DD/YYYY' → 'mm/dd/yyyy'
        * ``currency_symbol``          — e.g. '$', '€'
        * ``currency_decimal_places``  — e.g. 2
        """
        if not format_type:
            return None
        ft = format_type.lower()
        if ft == 'date':
            # Convert user-facing format to Acrobat JS format
            # User sends MM/DD/YYYY; Acrobat uses lowercase mm/dd/yyyy
            date_fmt = self.settings.get('date_format', 'MM/DD/YYYY')
            # Normalise to Acrobat JS format:
            #   MM → mm, DD → dd, YYYY → yyyy, YY → yy
            acrobat_fmt = (date_fmt
                           .replace('YYYY', 'yyyy')
                           .replace('YY', 'yy')
                           .replace('MM', 'mm')
                           .replace('DD', 'dd'))
            return (
                f'AFDate_FormatEx("{acrobat_fmt}");',
                f'AFDate_KeystrokeEx("{acrobat_fmt}");',
            )
        elif ft == 'phone':
            # AFSpecial codes: 0=Zip, 1=Zip+4, 2=Phone, 3=SSN
            return (
                'AFSpecial_Format(2);',
                'AFSpecial_Keystroke(2);',
            )
        elif ft == 'zip':
            return (
                'AFSpecial_Format(0);',
                'AFSpecial_Keystroke(0);',
            )
        elif ft == 'ssn':
            return (
                'AFSpecial_Format(3);',
                'AFSpecial_Keystroke(3);',
            )
        elif ft == 'currency':
            opts = format_options or {}
            decimals = int(self.settings.get('currency_decimal_places', 2))
            symbol = self.settings.get('currency_symbol', '$')
            if opts.get('has_dollar_in_cell'):
                # Page already has a hardcoded currency symbol next to the field —
                # format as number only (no symbol prefix from the viewer).
                return (
                    f'AFNumber_Format({decimals}, 0, 0, 0, "", true);',
                    f'AFNumber_Keystroke({decimals}, 0, 0, 0, "", true);',
                )
            else:
                # No hardcoded symbol — viewer prepends it to the value.
                return (
                    f'AFNumber_Format({decimals}, 0, 0, 0, "{symbol}", true);',
                    f'AFNumber_Keystroke({decimals}, 0, 0, 0, "{symbol}", true);',
                )
        elif ft == 'number':
            return (
                'AFNumber_Format(0, 1, 0, 0, "", true);',
                'AFNumber_Keystroke(0, 1, 0, 0, "", true);',
            )
        elif ft == 'state':
            # State: max 2 chars, force uppercase on keystroke
            return (
                '',
                'event.change = event.change.toUpperCase();',
            )
        return None

    def _create_checkbox_widget(self, pdf, name, tx0, ty0, tx1, ty1):
        """Create a checkbox widget with ZapfDingbats appearance."""
        cb_w = tx1 - tx0
        cb_h = ty1 - ty0

        zadb_font = pikepdf.Dictionary({
            '/Type': pikepdf.Name('/Font'),
            '/Subtype': pikepdf.Name('/Type1'),
            '/BaseFont': pikepdf.Name('/ZapfDingbats'),
        })
        resources = pikepdf.Dictionary({
            '/Font': pikepdf.Dictionary({'/ZaDb': zadb_font}),
        })

        font_size = min(cb_w, cb_h) * 0.8
        x_off = (cb_w - font_size * 0.6) / 2
        y_off = (cb_h - font_size * 0.6) / 2

        style = self.settings.get('checkbox_style', 'check')
        style_map = {
            'check': '4', 'circle': 'l', 'cross': '8',
            'square': 'n', 'diamond': 'u', 'star': 'H',
        }
        ca_char = style_map.get(style, '4')

        on_content = (f'q\nBT\n/ZaDb {font_size:.2f} Tf\n0 g\n'
                      f'{x_off:.2f} {y_off:.2f} Td\n({ca_char}) Tj\nET\nQ').encode('latin-1')
        on_stream = pikepdf.Stream(pdf, on_content)
        on_stream['/BBox'] = pikepdf.Array([0, 0, cb_w, cb_h])
        on_stream['/Subtype'] = pikepdf.Name('/Form')
        on_stream['/Type'] = pikepdf.Name('/XObject')
        on_stream['/Resources'] = resources

        off_stream = pikepdf.Stream(pdf, b'q Q')
        off_stream['/BBox'] = pikepdf.Array([0, 0, cb_w, cb_h])
        off_stream['/Subtype'] = pikepdf.Name('/Form')
        off_stream['/Type'] = pikepdf.Name('/XObject')

        ap_n = pikepdf.Dictionary()
        ap_n['/Off'] = off_stream
        ap_n[pikepdf.Name('/Yes')] = on_stream

        # Checkbox border settings
        cb_border_thickness = int(self.settings.get('checkbox_border_thickness', 0))
        thickness_map = {0: 0, 1: 0.5, 2: 1, 3: 2}
        cb_border_w = thickness_map.get(cb_border_thickness, 0)

        mk_dict = {'/CA': ca_char}
        if cb_border_w > 0:
            cb_border_hex = self.settings.get('checkbox_border_color', '#000000')
            mk_dict['/BC'] = pikepdf.Array(self._hex_to_rgb_array(cb_border_hex))

        widget = pikepdf.Dictionary({
            '/Type': pikepdf.Name('/Annot'),
            '/Subtype': pikepdf.Name('/Widget'),
            '/FT': pikepdf.Name('/Btn'),
            '/T': pikepdf.String(name),
            '/Rect': pikepdf.Array([tx0, ty0, tx1, ty1]),
            '/F': 4,
            '/V': pikepdf.Name('/Off'),
            '/AS': pikepdf.Name('/Off'),
            '/AP': pikepdf.Dictionary({'/N': ap_n}),
            '/MK': pikepdf.Dictionary(mk_dict),
            '/BS': pikepdf.Dictionary({'/W': cb_border_w}),
            '/DA': pikepdf.String('/ZaDb 0 Tf 0 g'),
        })

        return widget

    def _create_radio_child_widget(self, pdf, parent_ref, tx0, ty0, tx1, ty1, value_name):
        """Create a radio button child widget with ZapfDingbats appearance.

        Similar to a checkbox widget but:
        - Uses value_name (e.g. 'Pass', 'Fail') as the on-state key instead of 'Yes'
        - Has /Parent reference to the radio group parent field
        - No /FT, /T, /V — those are inherited from the parent
        """
        cb_w = tx1 - tx0
        cb_h = ty1 - ty0

        zadb_font = pikepdf.Dictionary({
            '/Type': pikepdf.Name('/Font'),
            '/Subtype': pikepdf.Name('/Type1'),
            '/BaseFont': pikepdf.Name('/ZapfDingbats'),
        })
        resources = pikepdf.Dictionary({
            '/Font': pikepdf.Dictionary({'/ZaDb': zadb_font}),
        })

        font_size = min(cb_w, cb_h) * 0.8
        x_off = (cb_w - font_size * 0.6) / 2
        y_off = (cb_h - font_size * 0.6) / 2

        style = self.settings.get('checkbox_style', 'check')
        style_map = {
            'check': '4', 'circle': 'l', 'cross': '8',
            'square': 'n', 'diamond': 'u', 'star': 'H',
        }
        ca_char = style_map.get(style, '4')

        on_content = (f'q\nBT\n/ZaDb {font_size:.2f} Tf\n0 g\n'
                      f'{x_off:.2f} {y_off:.2f} Td\n({ca_char}) Tj\nET\nQ').encode('latin-1')
        on_stream = pikepdf.Stream(pdf, on_content)
        on_stream['/BBox'] = pikepdf.Array([0, 0, cb_w, cb_h])
        on_stream['/Subtype'] = pikepdf.Name('/Form')
        on_stream['/Type'] = pikepdf.Name('/XObject')
        on_stream['/Resources'] = resources

        off_stream = pikepdf.Stream(pdf, b'q Q')
        off_stream['/BBox'] = pikepdf.Array([0, 0, cb_w, cb_h])
        off_stream['/Subtype'] = pikepdf.Name('/Form')
        off_stream['/Type'] = pikepdf.Name('/XObject')

        # Appearance dict: keyed by value_name (not 'Yes' like standalone checkboxes)
        ap_n = pikepdf.Dictionary()
        ap_n['/Off'] = off_stream
        ap_n[pikepdf.Name(f'/{value_name}')] = on_stream

        # Radio button border settings (same as checkboxes)
        cb_border_thickness = int(self.settings.get('checkbox_border_thickness', 0))
        thickness_map = {0: 0, 1: 0.5, 2: 1, 3: 2}
        cb_border_w = thickness_map.get(cb_border_thickness, 0)

        mk_dict = {'/CA': ca_char}
        if cb_border_w > 0:
            cb_border_hex = self.settings.get('checkbox_border_color', '#000000')
            mk_dict['/BC'] = pikepdf.Array(self._hex_to_rgb_array(cb_border_hex))

        widget = pikepdf.Dictionary({
            '/Type': pikepdf.Name('/Annot'),
            '/Subtype': pikepdf.Name('/Widget'),
            '/Parent': parent_ref,
            '/Rect': pikepdf.Array([tx0, ty0, tx1, ty1]),
            '/F': 4,
            '/AS': pikepdf.Name('/Off'),
            '/AP': pikepdf.Dictionary({'/N': ap_n}),
            '/MK': pikepdf.Dictionary(mk_dict),
            '/BS': pikepdf.Dictionary({'/W': cb_border_w}),
            '/DA': pikepdf.String('/ZaDb 0 Tf 0 g'),
        })

        return widget

    @staticmethod
    def _sanitize_radio_value(raw_value):
        """Sanitize a radio value string for use as a PDF Name object.

        PDF Names must be alphanumeric (plus a few safe chars).
        E.g. 'Pass' -> 'Pass', 'N/A' -> 'NA', 'Yes (default)' -> 'Yesdefault'
        """
        import re
        clean = re.sub(r'[^A-Za-z0-9_]', '', raw_value or 'Option')
        return clean or 'Option'

    # -------------------------------------------------------------------
    # Color conversion helpers
    # -------------------------------------------------------------------

    @staticmethod
    def _hex_to_rgb_array(hex_color: str) -> list:
        """Convert '#RRGGBB' hex string to [r, g, b] floats (0.0–1.0)."""
        h = hex_color.lstrip('#')
        if len(h) != 6:
            return [0.0, 0.0, 0.0]
        return [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]

    @staticmethod
    def _hex_to_pdf_rgb(hex_color: str) -> str:
        """Convert '#RRGGBB' hex string to PDF color operands like '0 0 0'."""
        h = hex_color.lstrip('#')
        if len(h) != 6:
            return '0 0 0'
        rgb = [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
        return f'{rgb[0]:.4g} {rgb[1]:.4g} {rgb[2]:.4g}'

    def _create_signature_widget(self, pdf, name, tx0, ty0, tx1, ty1):
        """Create a digital signature field widget."""
        fw = tx1 - tx0
        fh = ty1 - ty0
        ap_stream = pikepdf.Stream(pdf, b'q Q')
        ap_stream['/BBox'] = pikepdf.Array([0, 0, fw, fh])
        ap_stream['/Subtype'] = pikepdf.Name('/Form')
        ap_stream['/Type'] = pikepdf.Name('/XObject')

        return pikepdf.Dictionary({
            '/Type': pikepdf.Name('/Annot'),
            '/Subtype': pikepdf.Name('/Widget'),
            '/FT': pikepdf.Name('/Sig'),
            '/T': pikepdf.String(name),
            '/Rect': pikepdf.Array([tx0, ty0, tx1, ty1]),
            '/F': 4,
            '/AP': pikepdf.Dictionary({'/N': ap_stream}),
        })

    def _create_image_button_widget(self, pdf, name, tx0, ty0, tx1, ty1):
        """Create a pushbutton widget for image uploads.

        The button uses a JavaScript action (buttonImportIcon) to let the
        user select an image file.  The selected image becomes the button's
        icon, visually filling the placeholder rectangle.
        """
        field_name = name + '_af_image'
        fw = tx1 - tx0
        fh = ty1 - ty0

        # Placeholder appearance — light grey box with mountain/sun icon
        # and "Click to add image" text so the user knows it's interactive.
        # Icon scales proportionally to field size.
        icon_scale = min(fw, fh) / 80.0  # normalise to ~80pt reference
        if icon_scale < 0.4:
            icon_scale = 0.4
        if icon_scale > 2.5:
            icon_scale = 2.5

        # Centre the icon in the field
        icon_w = 40 * icon_scale
        icon_h = 30 * icon_scale
        ix = (fw - icon_w) / 2          # icon area left
        iy = (fh) / 2                   # icon vertical centre (slightly above middle)

        # Mountain peaks (two overlapping triangles)
        # Back mountain (larger, lighter grey)
        bm_left   = ix + 2 * icon_scale
        bm_right  = ix + 38 * icon_scale
        bm_peak   = iy + 10 * icon_scale
        bm_base   = iy - 8 * icon_scale
        # Front mountain (smaller, darker grey)
        fm_left   = ix + 14 * icon_scale
        fm_right  = ix + 40 * icon_scale
        fm_peak   = iy + 5 * icon_scale
        fm_base   = iy - 8 * icon_scale

        # Sun (circle via 4 Bezier arcs)
        sun_r  = 4 * icon_scale
        sun_cx = ix + 10 * icon_scale
        sun_cy = iy + 12 * icon_scale
        k = 0.5523 * sun_r  # Bezier magic number for circle approximation

        # Text position
        text_size = max(6, min(8, 8 * icon_scale))
        text_w = 60  # approx width of "Click to add image" at 8pt
        tx_pos = max(2, (fw - text_w) / 2)
        ty_pos = iy - 14 * icon_scale

        placeholder_text = (
            f'q\n'
            # Background
            f'0.95 0.95 0.95 rg\n'
            f'0 0 {fw:.2f} {fh:.2f} re f\n'
            # Back mountain (lighter grey)
            f'0.78 0.78 0.78 rg\n'
            f'{bm_left:.2f} {bm_base:.2f} m\n'
            f'{(bm_left + bm_right) / 2:.2f} {bm_peak:.2f} l\n'
            f'{bm_right:.2f} {bm_base:.2f} l\n'
            f'h f\n'
            # Front mountain (darker grey)
            f'0.68 0.68 0.68 rg\n'
            f'{fm_left:.2f} {fm_base:.2f} m\n'
            f'{(fm_left + fm_right) / 2:.2f} {fm_peak:.2f} l\n'
            f'{fm_right:.2f} {fm_base:.2f} l\n'
            f'h f\n'
            # Sun (warm gold circle via Bezier curves)
            f'0.85 0.75 0.45 rg\n'
            f'{sun_cx + sun_r:.2f} {sun_cy:.2f} m\n'
            f'{sun_cx + sun_r:.2f} {sun_cy + k:.2f} '
            f'{sun_cx + k:.2f} {sun_cy + sun_r:.2f} '
            f'{sun_cx:.2f} {sun_cy + sun_r:.2f} c\n'
            f'{sun_cx - k:.2f} {sun_cy + sun_r:.2f} '
            f'{sun_cx - sun_r:.2f} {sun_cy + k:.2f} '
            f'{sun_cx - sun_r:.2f} {sun_cy:.2f} c\n'
            f'{sun_cx - sun_r:.2f} {sun_cy - k:.2f} '
            f'{sun_cx - k:.2f} {sun_cy - sun_r:.2f} '
            f'{sun_cx:.2f} {sun_cy - sun_r:.2f} c\n'
            f'{sun_cx + k:.2f} {sun_cy - sun_r:.2f} '
            f'{sun_cx + sun_r:.2f} {sun_cy - k:.2f} '
            f'{sun_cx + sun_r:.2f} {sun_cy:.2f} c\n'
            f'f\n'
            # Text label
            f'0.55 0.55 0.55 rg\n'
            f'BT\n/Helv {text_size:.1f} Tf\n'
            f'{tx_pos:.2f} {ty_pos:.2f} Td\n'
            f'(Click to add image) Tj\n'
            f'ET\nQ'
        ).encode('latin-1')
        ap_stream = pikepdf.Stream(pdf, placeholder_text)
        ap_stream['/BBox'] = pikepdf.Array([0, 0, fw, fh])
        ap_stream['/Subtype'] = pikepdf.Name('/Form')
        ap_stream['/Type'] = pikepdf.Name('/XObject')
        ap_stream['/Resources'] = pikepdf.Dictionary({
            '/Font': pikepdf.Dictionary({
                '/Helv': pikepdf.Dictionary({
                    '/Type': pikepdf.Name('/Font'),
                    '/Subtype': pikepdf.Name('/Type1'),
                    '/BaseFont': pikepdf.Name('/Helvetica'),
                }),
            }),
        })

        # JavaScript action: open file picker → set button icon
        js_code = (
            f'var f = this.getField("{field_name}");\n'
            f'f.buttonImportIcon();'
        )

        return pikepdf.Dictionary({
            '/Type': pikepdf.Name('/Annot'),
            '/Subtype': pikepdf.Name('/Widget'),
            '/FT': pikepdf.Name('/Btn'),
            '/T': pikepdf.String(field_name),
            '/Rect': pikepdf.Array([tx0, ty0, tx1, ty1]),
            '/F': 4,
            '/Ff': 65536,  # Pushbutton
            '/MK': pikepdf.Dictionary({
                '/BC': pikepdf.Array([0.75, 0.75, 0.75]),  # Border colour
                '/TP': 1,  # Icon only (no caption)
                '/IF': pikepdf.Dictionary({
                    '/S': pikepdf.Name('/A'),       # Always scale icon
                    '/A': pikepdf.Array([0.5, 0.5]),  # Centre alignment
                }),
            }),
            '/BS': pikepdf.Dictionary({'/W': 1, '/S': pikepdf.Name('/S')}),
            '/AP': pikepdf.Dictionary({'/N': ap_stream}),
            '/A': pikepdf.Dictionary({
                '/S': pikepdf.Name('/JavaScript'),
                '/JS': pikepdf.String(js_code),
            }),
        })

    def _create_comb_widget(self, pdf, name, tx0, ty0, tx1, ty1, max_len):
        """Create a comb (fixed-width character) text field."""
        widget = self._create_text_widget(pdf, name, tx0, ty0, tx1, ty1)
        widget['/MaxLen'] = max_len
        # Comb flag (bit 25)
        widget['/Ff'] = (1 << 24)
        return widget
