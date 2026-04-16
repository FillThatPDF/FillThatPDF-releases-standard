    def _is_in_quantity_column(self, page_num, x, y):
        # v35.34: Check if the given coordinates fall within a column identified independently as a "Quantity" column
        # This is used to aggressively ban checkboxes in "Unit Number" / "Number of Floors" columns.
        
        # 1. Check strict header columns (using the page tables)
        tables = self.page_tables.get(page_num, [])
        page = self.pdf.pages[page_num]
        
        for table in tables:
            rows = table.rows
            if not rows: continue
            
            # Simple header check (first 3 rows)
            for r_idx, row in enumerate(rows[:3]):
                if not hasattr(row, 'cells'): continue
                for cell in row.cells:
                    if not cell: continue
                    cx0, cy0, cx1, cy1 = cell
                    
                    # Does x fall within this column?
                    if cx0 <= x <= cx1:
                        # Extract text
                        try:
                            # Use cached words if possible or raw extraction
                            crop = page.within_bbox((cx0, cy0, cx1, cy1))
                            text = (crop.extract_text() or "").strip().lower()
                            
                            # Strict quantity keywords
                            if 'unit number' in text or 'number of floors' in text or 'qty' in text or 'quantity' in text:
                                return True
                            
                            # Special case: "Unit" header in a tally sheet usually means "Unit Number"
                            if text == 'unit' and page_num in self.tally_sheet_pages:
                                return True
                        except:
                            pass
        return False
