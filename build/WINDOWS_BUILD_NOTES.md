# Windows Build Notes

## Cross-Platform Rules

### Never hardcode `/tmp/` in production Python files
- macOS has `/tmp` by default — Windows does NOT (`C:\tmp` doesn't exist).
- Always use `tempfile.gettempdir()` instead:
  ```python
  import tempfile, os
  log_path = os.path.join(tempfile.gettempdir(), 'my_debug.log')
  ```
- This resolves correctly on all platforms:
  - macOS/Linux → `/tmp/`
  - Windows → `C:\Users\<user>\AppData\Local\Temp\`

### Never use Unicode symbols in `print()` output
- Windows default console encoding is `cp1252`, which CANNOT encode characters like `→` (U+2192), `←`, `▶`, etc.
- Always use ASCII alternatives in print statements: `->` instead of `→`, `--` instead of `—`
- Unicode in **comments** is fine (not printed to console)

## Bugs Fixed

### 1. FileNotFoundError on Windows — `/tmp/` path (March 2026)
- **File:** `python_dist/smart_fillable_v24.py` (line 23)
- **Error:** `FileNotFoundError: [Errno 2] No such file or directory: 'C:\tmp\smart_fillable_debug.log'`
- **Fix:** Replaced `'/tmp/smart_fillable_debug.log'` with `os.path.join(tempfile.gettempdir(), 'smart_fillable_debug.log')`

### 2. UnicodeEncodeError on Windows — `→` in print() (March 2026)
- **Files:** `fillable_engine/resolver.py`, `fillable_engine/pipeline.py`, `fillable_engine/detectors/table_cells.py`
- **Error:** `UnicodeEncodeError: 'charmap' codec can't encode character '\u2192'`
- **Fix:** Replaced `→` with `->` in all `print()` statements

### 3. UnicodeEncodeError on Windows — emoji in print() (March 2026)
- **Files:** `fill_pdf_v3.py`, `apply_acroform_fix.py`, `fix_checkbox_appearances.py`, `garbage_field_cleanup.py`
- **Error:** Same `UnicodeEncodeError` — emoji (⚠️, ✅, ❌, 📄, 🧹, etc.) can't encode in Windows cp1252
- **Fix:** Replaced all emoji with ASCII labels: `[WARNING]`, `[OK]`, `[ERROR]`, etc.

## Production Python Files (built via PyInstaller for Windows)
These files are compiled into `.exe` binaries by the GitHub Actions workflow.
Check all of them for hardcoded paths before building:
- `smart_fillable_v24.py` ← had the bug
- `smart_fillable_server.py`
- `extract_fields.py`
- `modify_fields.py`
- `fill_pdf_v3.py`
- `apply_acroform_fix.py`
- `fix_checkbox_appearances.py`
- `auto_name_field.py`
- `auto_rename_all.py`
- `garbage_field_cleanup.py`
