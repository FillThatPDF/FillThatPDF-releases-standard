#!/bin/bash
set -e

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_DIST="$APP_DIR/python_dist"
DIST_ARM64="$PYTHON_DIST/dist_arm64"
DIST_X64="$PYTHON_DIST/dist_x64"

# --onefile binaries (single executable)
SCRIPTS=(smart_fillable fill_pdf_v3 extract_fields modify_fields garbage_field_cleanup auto_rename_all auto_name_field fix_checkbox_appearances apply_acroform_fix)

# --onedir binaries (directory containing executable + shared libs — faster startup)
ONEDIR_SCRIPTS=(smart_fillable_server)

build_arch="$1"

if [ "$build_arch" = "arm64" ]; then
    echo "=== Building ARM64 Binaries ==="
    rm -rf "$DIST_ARM64"
    mkdir -p "$DIST_ARM64"

    for script in "${SCRIPTS[@]}"; do
        echo "Building $script (ARM64)..."
        "$APP_DIR/pyenv/bin/pyinstaller" --clean --noconfirm \
            --distpath "$DIST_ARM64" \
            --workpath "$PYTHON_DIST/build/arm64" \
            "$PYTHON_DIST/$script.spec" 2>&1 | tail -3
        if [ -f "$DIST_ARM64/$script" ]; then
            echo "  ✅ $script done ($(du -h "$DIST_ARM64/$script" | cut -f1))"
        else
            echo "  ❌ $script FAILED"
            exit 1
        fi
    done

    # Build --onedir binaries (server — fast startup)
    for script in "${ONEDIR_SCRIPTS[@]}"; do
        echo "Building $script (ARM64, onedir)..."
        "$APP_DIR/pyenv/bin/pyinstaller" --clean --noconfirm \
            --distpath "$DIST_ARM64" \
            --workpath "$PYTHON_DIST/build/arm64" \
            "$PYTHON_DIST/$script.spec" 2>&1 | tail -3
        if [ -d "$DIST_ARM64/$script" ] && [ -f "$DIST_ARM64/$script/$script" ]; then
            echo "  ✅ $script done (onedir: $(du -sh "$DIST_ARM64/$script" | cut -f1))"
        else
            echo "  ❌ $script FAILED (expected directory $DIST_ARM64/$script/$script)"
            exit 1
        fi
    done

    echo "=== ARM64 Build Complete ==="
    ls -lh "$DIST_ARM64/"

elif [ "$build_arch" = "x64" ]; then
    echo "=== Building X64 Binaries ==="
    rm -rf "$DIST_X64"
    mkdir -p "$DIST_X64"

    for script in "${SCRIPTS[@]}"; do
        echo "Building $script (X64)..."
        if [ -f "$PYTHON_DIST/${script}_x64.spec" ]; then
            SPEC_FILE="$PYTHON_DIST/${script}_x64.spec"
        else
            SPEC_FILE="$PYTHON_DIST/${script}.spec"
        fi
        arch -x86_64 "$APP_DIR/venv_x64/bin/pyinstaller" --clean --noconfirm \
            --distpath "$DIST_X64" \
            --workpath "$PYTHON_DIST/build/x64" \
            "$SPEC_FILE" 2>&1 | tail -3

        # Rename if _x64 suffix exists
        if [ -f "$DIST_X64/${script}_x64" ]; then
            mv "$DIST_X64/${script}_x64" "$DIST_X64/${script}"
        fi

        if [ -f "$DIST_X64/$script" ]; then
            echo "  ✅ $script done ($(du -h "$DIST_X64/$script" | cut -f1))"
        else
            echo "  ❌ $script FAILED"
            exit 1
        fi
    done

    # Build --onedir binaries (server — fast startup)
    for script in "${ONEDIR_SCRIPTS[@]}"; do
        echo "Building $script (X64, onedir)..."
        if [ -f "$PYTHON_DIST/${script}_x64.spec" ]; then
            SPEC_FILE="$PYTHON_DIST/${script}_x64.spec"
        else
            SPEC_FILE="$PYTHON_DIST/${script}.spec"
        fi
        arch -x86_64 "$APP_DIR/venv_x64/bin/pyinstaller" --clean --noconfirm \
            --distpath "$DIST_X64" \
            --workpath "$PYTHON_DIST/build/x64" \
            "$SPEC_FILE" 2>&1 | tail -3

        # Rename if _x64 suffix exists
        if [ -d "$DIST_X64/${script}_x64" ]; then
            mv "$DIST_X64/${script}_x64" "$DIST_X64/${script}"
        fi

        if [ -d "$DIST_X64/$script" ] && [ -f "$DIST_X64/$script/$script" ]; then
            echo "  ✅ $script done (onedir: $(du -sh "$DIST_X64/$script" | cut -f1))"
        else
            echo "  ❌ $script FAILED (expected directory $DIST_X64/$script/$script)"
            exit 1
        fi
    done

    echo "=== X64 Build Complete ==="
    ls -lh "$DIST_X64/"
else
    echo "Usage: $0 [arm64|x64]"
    exit 1
fi
