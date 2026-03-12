"""
Post-processor: Recover missing address-row fields.

Many utility/incentive forms have a repeating contact-info block pattern:

    Name ___________  Contact Person ___________
    Company Name ___  City _____  State ___  ZIP _____
    Street Address ___________
    Phone ___________  Email Address ___________

Detectors often create City but miss State, ZIP, Name, and Contact Person
because:
  - State/ZIP are too narrow or not detected as separate fields
  - Name conflicts with other fields (lower rank loses in resolver)
  - Contact Person is handled but Name on same row may be missing

This post-processor detects City fields and recovers any missing
State, ZIP, Name, and Contact Person fields using the actual
label text positions and horizontal form lines.

Runs AFTER GridGapFill (so grid-created fields are available) and BEFORE
HeightStandardizer / LabelTrimmer.
"""

from typing import List, Dict, Set, Optional
from collections import defaultdict

from ..models import ResolvedField, FieldType, PageModel


# Labels that indicate a "street address" field
ADDRESS_LABELS: Set[str] = {'street', 'address', 'mailing', 'service'}

# Default right-edge for fields (standard letter page minus margin)
PAGE_RIGHT = 597.0


class AddressRowRecovery:
    """Recover missing State, ZIP, Name, and Contact Person fields."""

    def process(
        self,
        fields: List[ResolvedField],
        pages: List[PageModel],
    ) -> List[ResolvedField]:
        pages_by_num = {p.page_num: p for p in pages}
        recovered = 0

        # Build name set for uniqueness
        used_names: Set[str] = {f.name for f in fields}

        # ==================================================================
        # Part 1A: City + ZIP on same row → recover Street Address and State
        # ==================================================================

        for page in pages:
            pg = page.page_num
            page_fields = [f for f in fields if f.page == pg]

            city_fields = [f for f in page_fields
                           if 'city' in (f.name or '').lower()
                           and f.field_type == FieldType.TEXT]

            for city_f in city_fields:
                city_y0, city_y1 = city_f.y0, city_f.y1
                city_x0 = city_f.x0

                # Find ZIP on the same row (y-range overlap)
                zip_f = None
                for f in page_fields:
                    if ('zip' in (f.name or '').lower()
                            and f.field_type == FieldType.TEXT
                            and f.y0 < city_y1 + 5
                            and f.y1 > city_y0 - 5):
                        zip_f = f
                        break
                if not zip_f:
                    continue  # Part 1B handles City-without-ZIP

                # --- Street Address recovery ---
                has_street = any(
                    any(kw in (f.name or '').lower() for kw in ADDRESS_LABELS)
                    and f.y0 < city_y1 + 5 and f.y1 > city_y0 - 5
                    for f in page_fields
                )
                if not has_street and city_x0 > 150:
                    words = page.get_words_in_bbox(
                        (40, city_y0 - 20, city_x0 - 5, city_y1 + 5))
                    label_words = [w for w in words
                                   if w.get('text', '').lower()
                                   in ADDRESS_LABELS | {'street'}]
                    if label_words:
                        label_right = max(float(w['x1']) for w in label_words)
                        field_x0 = label_right + 3
                        field_x1 = city_x0 - 5
                        if field_x1 - field_x0 > 40:
                            if not self._has_overlap(fields, pg, city_y0,
                                                     field_x0, field_x1,
                                                     y1=city_y1):
                                name = self._unique_name(
                                    'Street_Address', used_names)
                                fields.append(ResolvedField(
                                    page=pg,
                                    x0=field_x0, y0=city_y0,
                                    x1=field_x1, y1=city_y1,
                                    field_type=FieldType.TEXT,
                                    source='address_recovery',
                                    name=name,
                                    label='Street Address',
                                ))
                                recovered += 1

                # --- State recovery (between City and ZIP) ---
                has_state = any(
                    'state' in (f.name or '').lower()
                    and f.y0 < city_y1 + 5 and f.y1 > city_y0 - 5
                    for f in fields if f.page == pg
                )
                city_right = city_f.x1
                zip_left = zip_f.x0
                if not has_state and zip_left - city_right > 30:
                    words = page.get_words_in_bbox(
                        (city_right - 5, city_y0 - 20,
                         zip_left + 5, city_y1 + 5))
                    state_words = [w for w in words
                                   if w.get('text', '').lower()
                                   in ('state', 'st')]
                    if state_words:
                        label_right = max(float(w['x1'])
                                          for w in state_words)
                        field_x0 = label_right + 3
                        field_x1 = zip_left - 3
                        if field_x1 - field_x0 > 15:
                            if not self._has_overlap(fields, pg, city_y0,
                                                     field_x0, field_x1,
                                                     y1=city_y1):
                                name = self._unique_name(
                                    'State', used_names)
                                fields.append(ResolvedField(
                                    page=pg,
                                    x0=field_x0, y0=city_y0,
                                    x1=field_x1, y1=city_y1,
                                    field_type=FieldType.TEXT,
                                    source='address_recovery',
                                    name=name,
                                    label='State',
                                ))
                                recovered += 1

        # ==================================================================
        # Part 1B: City WITHOUT ZIP → recover State and ZIP from labels
        # ==================================================================

        for page in pages:
            pg = page.page_num
            page_fields = [f for f in fields if f.page == pg]

            city_fields = [f for f in page_fields
                           if 'city' in (f.name or '').lower()
                           and f.field_type == FieldType.TEXT]

            for city_f in city_fields:
                city_y0, city_y1 = city_f.y0, city_f.y1

                # Skip if ZIP already exists on the same row (y-range overlap)
                has_zip = any(
                    'zip' in (f.name or '').lower()
                    and f.field_type == FieldType.TEXT
                    and f.y0 < city_y1 + 5 and f.y1 > city_y0 - 5
                    for f in fields if f.page == pg
                )
                if has_zip:
                    continue

                # Look for "State" and "ZIP" text labels to the right of City
                words_right = page.get_words_in_bbox(
                    (city_f.x1 - 5, city_y0 - 15, 640, city_y1 + 15))

                state_label = None
                zip_label = None
                for w in words_right:
                    wt = w.get('text', '').lower()
                    wx0 = float(w.get('x0', 0))
                    wx1 = float(w.get('x1', 0))
                    if wt in ('state', 'st') and wx0 > city_f.x1 - 10:
                        state_label = (wx0, wx1)
                    if wt == 'zip' and wx0 > city_f.x1 + 20:
                        zip_label = (wx0, wx1)

                if not state_label and not zip_label:
                    continue

                # Check if State already exists on this row (y-range overlap)
                has_state = any(
                    'state' in (f.name or '').lower()
                    and f.y0 < city_y1 + 5 and f.y1 > city_y0 - 5
                    for f in fields if f.page == pg
                )

                # --- Create State field ---
                if state_label and not has_state:
                    state_x0 = state_label[1] + 3  # After "State" label
                    if zip_label:
                        state_x1 = zip_label[0] - 3  # Before "ZIP" label
                    else:
                        state_x1 = min(city_f.x1 + 70, PAGE_RIGHT)

                    if state_x1 - state_x0 > 15:
                        if not self._has_overlap(fields, pg, city_y0,
                                                 state_x0, state_x1,
                                                 y1=city_y1):
                            name = self._unique_name('State', used_names)
                            fields.append(ResolvedField(
                                page=pg,
                                x0=state_x0, y0=city_y0,
                                x1=state_x1, y1=city_y1,
                                field_type=FieldType.TEXT,
                                source='address_recovery',
                                name=name,
                                label='State',
                            ))
                            recovered += 1

                # --- Create or rename ZIP field ---
                if zip_label:
                    zip_x0 = zip_label[1] + 3  # After "ZIP" label
                    zip_x1 = PAGE_RIGHT

                    if zip_x1 - zip_x0 > 20:
                        if not self._has_overlap(fields, pg, city_y0,
                                                 zip_x0, zip_x1,
                                                 y1=city_y1):
                            name = self._unique_name('ZIP', used_names)
                            fields.append(ResolvedField(
                                page=pg,
                                x0=zip_x0, y0=city_y0,
                                x1=zip_x1, y1=city_y1,
                                field_type=FieldType.TEXT,
                                source='address_recovery',
                                name=name,
                                label='ZIP',
                            ))
                            recovered += 1
                        else:
                            # A field already occupies the ZIP area — if it's
                            # a misnamed gap-fill (e.g. "Street_Address_5"),
                            # rename it to ZIP so the label matches the page
                            # text and the column purpose.
                            for f in fields:
                                if (f.page == pg
                                        and f.source == 'grid_gap_fill'
                                        and 'zip' not in (f.name or '').lower()
                                        and f.y0 < city_y1 + 5
                                        and f.y1 > city_y0 - 5
                                        and f.x0 >= zip_label[0] - 10
                                        and f.x1 <= zip_x1 + 5):
                                    old_name = f.name
                                    f.name = self._unique_name(
                                        'ZIP', used_names)
                                    f.label = 'ZIP'
                                    recovered += 1
                                    break

        # ==================================================================
        # Part 2: Recover Name / Contact Person above City rows
        # ==================================================================

        # Re-scan fields after Part 1 additions
        for page in pages:
            pg = page.page_num
            page_fields = [f for f in fields if f.page == pg]

            # Find City fields on this page
            city_fields = [f for f in page_fields
                           if 'city' in (f.name or '').lower()
                           and f.field_type == FieldType.TEXT]

            for city_f in city_fields:
                row_h = city_f.y1 - city_f.y0  # Standard row height

                # Search zone: one row above the City row
                search_y0 = city_f.y0 - 35
                search_y1 = city_f.y0
                if search_y0 < 50:
                    continue

                # Check if there's already a Person/Contact field on the
                # row above City (v24 often detects these already)
                person_above = None
                for f in fields:
                    if (f.page == pg
                            and f.field_type == FieldType.TEXT
                            and search_y0 < f.y0 < search_y1 + 5):
                        nl = (f.name or '').lower()
                        ll = (f.label or '').lower()
                        if 'person' in nl or 'person' in ll:
                            person_above = f
                            break

                # Determine y-coordinates for the name row
                if person_above:
                    # Use the Person field's y-coordinates
                    field_y0 = person_above.y0
                    field_y1 = person_above.y1
                else:
                    # Find actual horizontal lines in this zone
                    unique_ys = self._find_h_lines_in_zone(
                        page, search_y0, search_y1)
                    if len(unique_ys) >= 1:
                        field_y1 = unique_ys[-1]  # Underline
                        field_y0 = field_y1 - row_h
                    else:
                        field_y1 = city_f.y0
                        field_y0 = field_y1 - row_h

                # Remove thin junk fields in the name row
                junk_indices = []
                for i, f in enumerate(fields):
                    if (f.page == pg
                            and field_y0 - 15 < f.y0 < field_y1 + 5):
                        fh = f.y1 - f.y0
                        fw = f.x1 - f.x0
                        if fh < 8 and fw > 400:
                            junk_indices.append(i)
                for i in sorted(junk_indices, reverse=True):
                    removed_f = fields.pop(i)
                    print(f"   AddressRowRecovery: removed thin junk "
                          f"'{removed_f.name}' h={removed_f.height:.1f}pt")

                # Look for Name / Contact Person labels on the row above
                words_above = page.get_words_in_bbox(
                    (40, field_y0 - 5, PAGE_RIGHT, field_y1 + 5))

                name_words = []
                contact_words = []
                company_words = []
                for w in words_above:
                    wt = w.get('text', '').lower()
                    wx0 = float(w.get('x0', 0))
                    if wt == 'name' and wx0 < 200:
                        name_words.append(w)
                    elif wt == 'company' and wx0 < 200:
                        company_words.append(w)
                    if wt == 'contact' and wx0 > 300:
                        contact_words.append(w)

                # If we found "Company" but not "Name", look for "Name"
                # next to "Company" (e.g., "Company Name" label)
                if company_words and not name_words:
                    for cw in company_words:
                        cx1 = float(cw['x1'])
                        for w2 in words_above:
                            if (w2.get('text', '').lower() == 'name'
                                    and abs(float(w2['x0']) - cx1) < 10):
                                name_words.append(w2)
                                break

                if not name_words and not contact_words:
                    continue

                # Find "Contact Person" full label right edge
                contact_label_right = None
                for cw in contact_words:
                    cx1 = float(cw['x1'])
                    for pw in words_above:
                        if pw.get('text', '').lower() == 'person':
                            px0 = float(pw['x0'])
                            if 0 <= px0 - cx1 < 15:
                                contact_label_right = float(pw['x1'])
                                break
                    if contact_label_right is None:
                        contact_label_right = cx1

                # --- Create Name field (left side of the row) ---
                if name_words:
                    nw = name_words[0]
                    name_label_right = float(nw['x1'])
                    name_field_x0 = name_label_right + 3
                    name_field_x1 = (float(contact_words[0]['x0']) - 5
                                     if contact_words else 347.0)

                    if name_field_x1 - name_field_x0 > 40:
                        if not self._has_overlap(
                                fields, pg, field_y0,
                                name_field_x0, name_field_x1,
                                y_tol=2, y1=field_y1):
                            fname = self._unique_name('Name', used_names)
                            fields.append(ResolvedField(
                                page=pg,
                                x0=name_field_x0, y0=field_y0,
                                x1=name_field_x1, y1=field_y1,
                                field_type=FieldType.TEXT,
                                source='address_recovery',
                                name=fname,
                                label='Name',
                            ))
                            recovered += 1

                # --- Create Contact Person field (right side) ---
                if contact_words and contact_label_right:
                    cp_field_x0 = contact_label_right + 3
                    cp_field_x1 = PAGE_RIGHT

                    if cp_field_x1 - cp_field_x0 > 40:
                        if not self._has_overlap(
                                fields, pg, field_y0,
                                cp_field_x0, cp_field_x1,
                                y_tol=2, y1=field_y1):
                            fname = self._unique_name(
                                'Contact_Person', used_names)
                            fields.append(ResolvedField(
                                page=pg,
                                x0=cp_field_x0, y0=field_y0,
                                x1=cp_field_x1, y1=field_y1,
                                field_type=FieldType.TEXT,
                                source='address_recovery',
                                name=fname,
                                label='Contact Person',
                            ))
                            recovered += 1

        if recovered:
            print(f"   AddressRowRecovery: recovered {recovered} "
                  f"missing address fields")

        # ==================================================================
        # Part 3: Deduplicate overlapping address fields from different
        # detectors (e.g., inline_label City overlapping label_entry_below
        # City at slightly different y-positions).
        # ==================================================================
        _ADDR_KW = {'city', 'state', 'zip'}
        to_remove: Set[int] = set()
        for i, a in enumerate(fields):
            if i in to_remove:
                continue
            a_name = (a.name or '').lower()
            a_kw = next((kw for kw in _ADDR_KW if kw in a_name), None)
            if not a_kw:
                continue
            for j in range(i + 1, len(fields)):
                if j in to_remove:
                    continue
                b = fields[j]
                if b.page != a.page:
                    continue
                b_name = (b.name or '').lower()
                if a_kw not in b_name:
                    continue
                # Check y-range overlap
                if a.y0 >= b.y1 + 5 or a.y1 <= b.y0 - 5:
                    continue
                # Check x-range overlap (>50% of smaller field)
                x_ov = min(a.x1, b.x1) - max(a.x0, b.x0)
                min_w = min(a.x1 - a.x0, b.x1 - b.x0)
                if min_w <= 0 or x_ov < 0.5 * min_w:
                    continue
                # Overlapping — keep the taller field (better entry area)
                if (a.y1 - a.y0) >= (b.y1 - b.y0):
                    to_remove.add(j)
                else:
                    to_remove.add(i)
                    break  # a removed, stop comparing

        if to_remove:
            fields = [f for idx, f in enumerate(fields)
                      if idx not in to_remove]
            print(f"   AddressRowRecovery: removed {len(to_remove)} "
                  f"overlapping address duplicate(s)")

        return fields

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _has_overlap(
        fields: List[ResolvedField],
        page: int,
        y0: float,
        x0: float,
        x1: float,
        y_tol: float = 5,
        y1: Optional[float] = None,
    ) -> bool:
        """Check if any existing field overlaps the proposed area."""
        for f in fields:
            if f.page != page:
                continue
            if y1 is not None:
                # Use explicit y1 for row overlap check
                if f.y0 > y1 + y_tol or f.y1 < y0 - y_tol:
                    continue
            else:
                if abs(f.y0 - y0) >= y_tol:
                    continue
            if f.x0 < x1 and f.x1 > x0:
                return True
        return False

    @staticmethod
    def _find_h_lines_in_zone(
        page: PageModel, y0: float, y1: float
    ) -> List[float]:
        """Find unique y-positions of horizontal lines in a y-range."""
        ys = set()
        for line in page.lines:
            ly = float(line.get('top', line.get('y0', 0)))
            lx0 = float(line.get('x0', 0))
            lx1 = float(line.get('x1', 0))
            ly1 = float(line.get('bottom', line.get('y1', ly)))
            if (y0 < ly < y1
                    and (lx1 - lx0) > 50
                    and abs(ly1 - ly) < 2):
                ys.add(round(ly, 1))
        return sorted(ys)

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
