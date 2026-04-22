const { app, BrowserWindow, ipcMain, dialog, Menu } = require('electron');
const path = require('path');
const { spawn, execSync } = require('child_process');
const fs = require('fs');
const Store = require('electron-store');
// Defer electron-updater require until app is ready (avoids getVersion crash in dev)
const appConfig = require('./config');

// Suppress EPIPE/EIO errors (harmless pipe errors from child process communication)
process.on('uncaughtException', (err) => {
    if (err.code === 'EPIPE' || err.code === 'EIO') return; // Silently ignore pipe I/O errors
    console.error('Uncaught exception:', err);
});
process.on('unhandledRejection', (reason) => {
    console.error('Unhandled rejection:', reason);
});

function setupAutoUpdater() {
    const { autoUpdater } = require('electron-updater');
    autoUpdater.on('update-available', (info) => {
        // Show in-app banner instead of dialog
        if (mainWindow) {
            mainWindow.webContents.send('update-available', info.version);
        }
    });
    autoUpdater.on('download-progress', (progressObj) => {
        if (mainWindow) {
            mainWindow.webContents.send('update-download-progress', Math.round(progressObj.percent));
        }
    });
    autoUpdater.on('update-downloaded', (info) => {
        if (mainWindow) {
            mainWindow.webContents.send('update-downloaded', info.version);
        }
    });
    autoUpdater.on('error', (err) => {
        console.error('Auto-updater error:', err);
    });

    // IPC handler: user clicked "Restart to Update"
    ipcMain.handle('install-update', () => {
        autoUpdater.quitAndInstall();
    });

    return autoUpdater;
}

// Get the correct Python path - checks common locations for packaged app compatibility (for development mode)
function getPythonPath() {
    // List of Python paths to check (in order of preference)
    const isWin = process.platform === 'win32';
    const pythonPaths = isWin ? [
        path.join(__dirname, 'venv\\Scripts\\python.exe'),  // Local venv (Windows)
        path.join(__dirname, '.venv\\Scripts\\python.exe'), // Local .venv (Windows)
        'python',                                            // Fallback to PATH (Windows)
        'python3'                                            // Alternative PATH
    ] : [
        path.join(__dirname, 'pyenv/bin/python3'), // Local pyenv
        path.join(__dirname, 'venv/bin/python3'),  // Local venv
        path.join(__dirname, '.venv/bin/python3'), // Local .venv
        '/opt/homebrew/bin/python3',  // Homebrew on Apple Silicon
        '/usr/local/bin/python3',      // Homebrew on Intel Mac
        '/usr/bin/python3',            // System Python
        'python3'                       // Fallback to PATH
    ];

    for (const pythonPath of pythonPaths) {
        try {
            // Check if this Python has required packages
            const result = execSync(`"${pythonPath}" -c "import pikepdf; import pdfplumber; print('ok')"`, {
                encoding: 'utf8',
                timeout: 5000,
                stdio: ['pipe', 'pipe', 'pipe']
            });
            if (result && result.trim() === 'ok') {
                try { console.log(`Using Python: ${pythonPath}`); } catch (_) { /* ignore EPIPE */ }
                return pythonPath;
            }
        } catch (e) {
            // EPIPE can occur if Python exits before Node finishes writing to pipe
            // Check if the command actually succeeded despite the error
            if (e.code === 'EPIPE' && e.stdout && e.stdout.toString().trim() === 'ok') {
                try { console.log(`Using Python: ${pythonPath}`); } catch (_) { /* ignore EPIPE */ }
                return pythonPath;
            }
            // This Python doesn't have required packages, try next
            continue;
        }
    }

    // Fallback - just return python/python3 and hope for the best
    const fallback = isWin ? 'python' : 'python3';
    try { console.warn(`Could not find Python with required packages, using default ${fallback}`); } catch (_) {}
    return fallback;
}

// Cache the Python path
let cachedPythonPath = null;
function python() {
    if (!cachedPythonPath) {
        cachedPythonPath = getPythonPath();
    }
    return cachedPythonPath;
}

// ===== BUNDLED EXECUTABLE SUPPORT =====
// Platform/architecture-specific folder name
function getArchFolder() {
    if (process.platform === 'win32') return 'dist_win';
    return process.arch === 'x64' ? 'dist_x64' : 'dist_arm64';
}

// Append .exe on Windows for binary names
function exeName(name) {
    return process.platform === 'win32' ? name + '.exe' : name;
}

// Get path to bundled executable (used in packaged app)
function getBundledExecutable(name) {
    const archFolder = getArchFolder();
    const exe = exeName(name);

    if (app.isPackaged) {
        // Try folder-based first (onedir), then fall back to single file (onefile)
        const onedirPath = path.join(process.resourcesPath, 'python_dist', archFolder, name, exe);
        if (fs.existsSync(onedirPath)) return onedirPath;
        return path.join(process.resourcesPath, 'python_dist', archFolder, exe);
    } else {
        // Dev mode: check for onedir binary first
        const devOnedirPath = path.join(__dirname, 'python_dist', archFolder, name, exe);
        if (fs.existsSync(devOnedirPath)) return devOnedirPath;
        // Then check for onefile binary
        const bundledPath = path.join(__dirname, 'python_dist', archFolder, exe);
        if (fs.existsSync(bundledPath) && !fs.statSync(bundledPath).isDirectory()) {
            return bundledPath;
        }
        // Fall back to legacy dist folder if architecture-specific not found
        const legacyPath = path.join(__dirname, 'python_dist', 'dist', exe);
        if (fs.existsSync(legacyPath)) {
            return legacyPath;
        }
        return null; // Will fall back to Python
    }
}

// Run a script - uses bundled executable if available, otherwise Python
function runScript(scriptName, args = []) {
    const bundledExe = getBundledExecutable(scriptName);
    
    if (bundledExe && fs.existsSync(bundledExe)) {
        return spawn(bundledExe, args);
    } else {
        // Fall back to Python script
        const pythonExec = getPythonPath();
        const scriptPath = app.isPackaged
            ? path.join(process.resourcesPath, `${scriptName}.py`)
            : path.join(__dirname, 'python_dist', `${scriptName}.py`);
        return spawn(pythonExec, [scriptPath, ...args]);
    }
}


// ===== PERSISTENT PYTHON SERVER (Option B: eliminates per-PDF startup overhead) =====
// Instead of spawning a new Python process for every PDF, we keep one alive.
// The server imports all modules once and processes PDFs via stdin/stdout JSON.

let _serverProcess = null;
let _serverReady = false;
let _serverPendingRequests = {};  // id → { resolve, reject, progressCallback }
let _serverRequestCounter = 0;
let _serverBuffer = '';  // Accumulates partial stdout chunks

function _getServerScriptPath() {
    if (app.isPackaged) {
        const archFolder = getArchFolder();
        const serverExe = exeName('smart_fillable_server');
        // --onedir bundled server: executable is inside a subdirectory
        const onedirExe = path.join(process.resourcesPath, 'python_dist', archFolder, 'smart_fillable_server', serverExe);
        if (fs.existsSync(onedirExe)) {
            return { type: 'exe', path: onedirExe };
        }
        // Legacy --onefile bundled server (single binary)
        const onefileExe = path.join(process.resourcesPath, 'python_dist', archFolder, serverExe);
        if (fs.existsSync(onefileExe)) {
            return { type: 'exe', path: onefileExe };
        }
        // Fallback to Python script
        return { type: 'py', path: path.join(process.resourcesPath, 'smart_fillable_server.py') };
    }
    // Development mode — always use Python script for latest code
    return { type: 'py', path: path.join(__dirname, 'python_dist', 'smart_fillable_server.py') };
}

function startPythonServer() {
    if (_serverProcess) return;  // Already running

    const scriptInfo = _getServerScriptPath();
    try {
        if (scriptInfo.type === 'exe') {
            _serverProcess = spawn(scriptInfo.path, [], { stdio: ['pipe', 'pipe', 'pipe'] });
        } else {
            const pythonExec = python();
            _serverProcess = spawn(pythonExec, [scriptInfo.path], { stdio: ['pipe', 'pipe', 'pipe'] });
        }
    } catch (err) {
        console.error('Failed to start Python server:', err.message);
        _serverProcess = null;
        return;
    }

    _serverReady = false;
    _serverBuffer = '';

    _serverProcess.stdout.on('data', (data) => {
        _serverBuffer += data.toString();

        // Process complete lines
        let newlineIdx;
        while ((newlineIdx = _serverBuffer.indexOf('\n')) !== -1) {
            const line = _serverBuffer.slice(0, newlineIdx);
            _serverBuffer = _serverBuffer.slice(newlineIdx + 1);

            if (line.startsWith('RESULT:')) {
                // Parse the JSON result
                try {
                    const result = JSON.parse(line.slice(7));
                    if (result.ready) {
                        _serverReady = true;
                        console.log('Python server ready');
                        continue;
                    }
                    if (result.pong || result.quit) continue;

                    // Route result to the pending request
                    const reqId = result.id || '';
                    const pending = _serverPendingRequests[reqId];
                    if (pending) {
                        delete _serverPendingRequests[reqId];
                        if (result.success) {
                            pending.resolve(result);
                        } else {
                            pending.reject(new Error(result.error || 'Unknown error'));
                        }
                    }
                } catch (e) {
                    console.error('Failed to parse server result:', line, e);
                }
            } else {
                // Progress line — forward to ALL pending request callbacks
                for (const reqId of Object.keys(_serverPendingRequests)) {
                    const pending = _serverPendingRequests[reqId];
                    if (pending && pending.progressCallback) {
                        pending.progressCallback(line + '\n');
                    }
                }
            }
        }
    });

    _serverProcess.stderr.on('data', (data) => {
        // Forward stderr as progress (some libs print warnings there)
        const text = data.toString();
        for (const reqId of Object.keys(_serverPendingRequests)) {
            const pending = _serverPendingRequests[reqId];
            if (pending && pending.progressCallback) {
                pending.progressCallback(text);
            }
        }
    });

    _serverProcess.on('close', (code) => {
        try { console.log(`Python server exited with code ${code}`); } catch (e) { /* ignore */ }
        _serverProcess = null;
        _serverReady = false;
        // Reject all pending requests
        for (const reqId of Object.keys(_serverPendingRequests)) {
            const pending = _serverPendingRequests[reqId];
            delete _serverPendingRequests[reqId];
            if (pending) {
                pending.reject(new Error('Python server process exited unexpectedly'));
            }
        }
    });

    _serverProcess.on('error', (err) => {
        console.error('Python server error:', err);
        _serverProcess = null;
        _serverReady = false;
    });
}

function stopPythonServer() {
    if (_serverProcess) {
        try {
            _serverProcess.stdin.write(JSON.stringify({ cmd: 'quit' }) + '\n');
        } catch (e) { /* ignore */ }
        setTimeout(() => {
            if (_serverProcess) {
                try { _serverProcess.kill('SIGTERM'); } catch (e) { /* ignore */ }
            }
        }, 2000);
    }
}

/**
 * Send a make_fillable request to the persistent server.
 * Returns a Promise that resolves with the result.
 *
 * @param {object} options - { input, output, settings, auto_calibrate, pages }
 * @param {function} progressCallback - Called with each progress line (string)
 * @returns {Promise<object>} - { success, output, elapsed }
 */
function serverMakeFillable(options, progressCallback) {
    return new Promise((resolve, reject) => {
        // Ensure server is running
        if (!_serverProcess) {
            startPythonServer();
        }
        if (!_serverProcess) {
            reject(new Error('Could not start Python server'));
            return;
        }

        const reqId = `req-${++_serverRequestCounter}`;
        _serverPendingRequests[reqId] = { resolve, reject, progressCallback };

        const cmd = {
            cmd: 'make_fillable',
            id: reqId,
            input: options.input,
            output: options.output,
            settings: options.settings || {},
            auto_calibrate: options.auto_calibrate || false,
            pages: options.pages || null,
        };

        try {
            _serverProcess.stdin.write(JSON.stringify(cmd) + '\n');
        } catch (err) {
            delete _serverPendingRequests[reqId];
            reject(new Error(`Failed to write to server: ${err.message}`));
        }
    });
}

/**
 * Check if the persistent server is available and ready.
 */
function isServerAvailable() {
    return _serverProcess && _serverReady;
}

/**
 * Send an extract_fields request to the persistent server.
 * Returns a Promise that resolves with the extraction result data.
 *
 * @param {string} pdfPath - Path to the PDF file
 * @returns {Promise<object>} - The extraction result (pages, fields, radio_groups, output_dir)
 */
function serverExtractFields(pdfPath) {
    return new Promise((resolve, reject) => {
        if (!_serverProcess) {
            startPythonServer();
        }
        if (!_serverProcess) {
            reject(new Error('Could not start Python server'));
            return;
        }

        const reqId = `req-${++_serverRequestCounter}`;
        _serverPendingRequests[reqId] = { resolve, reject, progressCallback: null };

        const cmd = {
            cmd: 'extract_fields',
            id: reqId,
            input: pdfPath,
        };

        try {
            _serverProcess.stdin.write(JSON.stringify(cmd) + '\n');
        } catch (err) {
            delete _serverPendingRequests[reqId];
            reject(new Error(`Failed to write to server: ${err.message}`));
        }
    });
}

/**
 * Wait for the persistent server to become ready (up to timeoutMs).
 * Returns true if the server became ready, false if it timed out or no process exists.
 */
function waitForServer(timeoutMs = 15000) {
    return new Promise((resolve) => {
        if (isServerAvailable()) {
            resolve(true);
            return;
        }
        if (!_serverProcess) {
            resolve(false);
            return;
        }
        const start = Date.now();
        const interval = setInterval(() => {
            if (isServerAvailable()) {
                clearInterval(interval);
                resolve(true);
            } else if (!_serverProcess || Date.now() - start > timeoutMs) {
                clearInterval(interval);
                resolve(false);
            }
        }, 100);
    });
}


// Default settings for the application
const DEFAULT_SETTINGS = {
    // === DETECTION CONTROLS ===
    detection_sensitivity: 'aggressive',    // 'conservative', 'standard', or 'aggressive'
    enable_empty_box_detection: true,      // Detect image placeholder boxes
    enable_underscore_detection: true,     // Detect signature/text lines
    enable_radio_grouping: true,           // Auto-group Yes/No/NA into radio buttons
    enable_duplicate_fields: false,        // Allow duplicate overlapping fields (default: false)
    enable_table_detection: true,          // Detect table cells as fields
    skip_page_edges: false,                // Skip fields near page margins
    enable_garbage_field_cleanup: true,    // Remove garbage fields from text-only pages
    garbage_cleanup_sensitivity: 'conservative', // 'conservative', 'standard', or 'aggressive'
    auto_calibrate: true,                  // Automatically analyze PDF and optimize settings
    display_tooltips: true,                // Enable/disable hover tooltips on fields

    // === INTELLIGENCE LAYER (v51.0) ===
    enable_intelligence_layer: true,       // Master switch for template matching + ML post-processing
    enable_template_matching: true,        // Recognize previously-processed PDFs and reuse cached fields
    enable_ml_postprocessor: true,         // CNN-based false positive filtering
    enable_auto_save_templates: true,      // Auto-save templates after processing for future matching
    ml_confidence_threshold: 0.95,         // Min confidence to remove a field as false positive (0.0-1.0)
    
    // === CHECKBOX SETTINGS ===
    checkbox_size_min: 8,                  // Minimum checkbox size (pt)
    checkbox_size_max: 18,                 // Maximum checkbox size (pt)
    checkbox_field_size: 10,               // Size of created checkbox widgets
    checkbox_style: 'check',               // 'check', 'circle', 'cross', 'square', 'diamond', 'star'
    checkbox_bg_color: '#FFFFFF',          // Checkbox background fill color
    checkbox_bg_opacity: 100,              // Background opacity (0-100)
    checkbox_border_color: '#000000',      // Checkbox border/stroke color
    checkbox_border_thickness: 0,          // 0=None, 1=Thin, 2=Medium, 3=Thick (Default 0)
    checkbox_border_opacity: 100,          // Border opacity (0-100)
    
    // === VISUAL/STYLING ===
    theme: 'dark',                         // 'dark' or 'light'
    field_background_color: '#EDF4FF',     // Field fill color (hex)
    field_background_opacity: 100,         // Field background opacity (0-100)
    field_border_visible: false,           // Show field borders
    field_border_color: '#000000',         // Field border color (hex)
    field_border_thickness: 1,             // 0=None, 1=Thin, 2=Medium, 3=Thick
    field_border_style: 'solid',           // 'solid' or 'dashed'
    field_font_size: 9,                    // Default font size (pt)
    field_font_family: 'Helvetica',        // 'Helvetica', 'Courier', 'Times-Roman', 'Arial'
    field_font_color: '#000000',           // Font color (hex)
    small_field_threshold: 100,            // Width threshold for center alignment
    
    // === JAVASCRIPT AUTO-FORMATTING ===
    enable_currency_formatting: true,      // Auto-format currency fields
    enable_date_picker: true,              // Add date dropdown
    enable_phone_formatting: true,         // Auto-format phone numbers
    enable_number_formatting: true,        // Auto-format numeric fields
    
    // Currency Options
    currency_symbol: '$',                  // Currency symbol
    currency_symbol_position: 'before',    // 'before' or 'after'
    currency_decimal_places: 2,            // Number of decimal places
    currency_thousands_separator: ',',     // Thousands separator
    currency_decimal_separator: '.',       // Decimal separator
    
    // Number Options
    number_decimal_places: 2,              // Default decimal places for numbers
    number_thousands_separator: ',',       // Thousands separator for numbers
    number_negative_style: 'minus',        // 'minus', 'parens', or 'red'
    
    // Date Options
    date_format: 'MM/DD/YYYY',             // Date format pattern
    date_separator: '/',                   // Date separator character
    
    // Phone Options
    phone_format: '(XXX) XXX-XXXX',        // Phone format pattern
    
    // === ADVANCED LAYOUT ===
    field_gap: 0.5,                        // Gap between adjacent fields (pt)
    min_field_width: 20,                   // Minimum field width (pt)
    min_field_height: 10,                  // Minimum field height (pt)
    cell_coverage_threshold: 0.7,          // Auto-expand threshold (0-1)
    table_cell_padding: 0,                 // Padding inside table cells when expanding fields (pt)
    auto_expand_fields: true,              // Expand narrow fields to fill cells
    
    // === DETECTION THRESHOLDS (ADVANCED) ===
    min_underscore_length: 3,              // Minimum underscore chars to detect (3-10)
    min_empty_box_width: 100,              // Min empty box width to detect (pt)
    min_empty_box_height: 30,              // Min empty box height to detect (pt)
    page_edge_margin: 30,                  // Margin to skip near page edges (pt)
    enable_global_single_line_height: false, // Override all single-line field heights
    global_single_line_height: 0.25,       // Height in inches (e.g. 0.25")
    
    // === FORMATTING DEFAULT ===
    // (Removed duplicates)
    
    // === DEBUG/DEVELOPER ===
    check_for_updates: true,               // Check for software updates on startup
    verbose_logging: false,                // Show detailed detection info
    preserve_existing_widgets: false,      // Keep fields from source PDF
    generate_qa_overlays: true,            // Generate visual QA images
    ui_settings_collapsed: {},             // Per-section collapsed state (persisted UI)
    
    // === OUTPUT ===
    output_suffix: '_fillable',            // Output filename suffix
    testfill_suffix: '_FILLED',            // Test fill output filename suffix
    auto_improve_names: false,             // Run auto-naming on all fields after generation

    // === TEST FILL CONFIGURATION ===
    testFillConfig: {
        categories: [
            { name: 'names', keywords: ['name'], data: ['John Smith', 'Sarah Johnson', 'Michael Brown', 'Emily Davis', 'David Wilson'] },
            { name: 'companies', keywords: ['company', 'business'], data: ['ABC Corp', 'XYZ Industries', 'Global Solutions', 'Tech Innovations'] },
            { name: 'phones', keywords: ['phone', 'tel'], data: ['(734) 555-0123', '(248) 555-0456', '(313) 555-0789', '(586) 555-1234'] },
            { name: 'dates', keywords: ['date', 'signed', 'completed'], data: ['01/15/2026', '02/20/2026', '06/15/2025', '09/30/2026', '11/12/2025'] },
            { name: 'states', keywords: ['state'], data: ['MI', 'OH', 'IL', 'IN', 'WI', 'CA', 'NY', 'TX', 'FL', 'PA'] },
            { name: 'zips', keywords: ['zip'], data: ['48201', '48202', '48226', '48235', '48301'] },
            { name: 'addresses', keywords: ['address', 'street'], data: ['123 Main St', '456 Oak Ave', '789 Pine Rd', '321 Elm St'] },
            { name: 'emails', keywords: ['email'], data: ['john@email.com', 'sarah@company.com', 'info@business.org'] },
            { name: 'currencies', keywords: ['cost', 'price', 'rebate', 'total', 'incentive', 'fee', 'amount'], data: ['$1,250.00', '$3,500.00', '$750.00', '$2,000.00', '$500.00'] },
            { name: 'percentages', keywords: ['afue', 'seer', 'hspf', 'eer', 'efficiency', '%'], data: ['95%', '98%', '85%', '92%', '88%'] },
            { name: 'rvalues', keywords: ['rvalue', 'r-value', 'r value'], data: ['R-13', 'R-19', 'R-30', 'R-38', 'R-49'] },
            { name: 'btus', keywords: ['btuh', 'btu'], data: ['80,000', '60,000', '100,000', '40,000', '120,000'] },
            { name: 'cfm', keywords: ['cfm', 'airflow'], data: ['1200', '1400', '1000', '800', '1600'] },
            { name: 'cities', keywords: ['city'], data: ['Detroit', 'Ann Arbor', 'Grand Rapids', 'Lansing', 'Flint'] }
        ],
        numericPatterns: 'rvalue, r-value, btuh, btu, afue, seer, hspf, eer, cop, preservice, postservice, input, output, rated, peak, cfm, kwh, therm, rebate, incentive, cost, price, amount, total, fee, sq ft, sqft, square, quantity, qty, tons, temperature, temp, degf, efficiency, airflow, oxygen, carbon, coil, stack, elapsed, minutes, sir, score, program year'
    },
    
    // === V2 STATISTICS ===
    stats: {
        pdfsProcessed: 0,
        fieldsCreated: 0,
        checkboxesCreated: 0,
        radioGroupsCreated: 0,
        textFieldsCreated: 0
    },
    
    // === TEMPLATES ===
    templates: {} // Stores named setting configurations
};

// Detection Sensitivity Presets - override individual detection parameters
const SENSITIVITY_PRESETS = {
    conservative: {
        min_underscore_length: 6,      // Require more underscores
        min_empty_box_width: 150,      // Only larger boxes
        min_empty_box_height: 50,
        min_field_width: 30,           // Skip very small fields
        min_field_height: 12,
        page_edge_margin: 40           // Larger margin from edges
    },
    standard: {
        min_underscore_length: 3,
        min_empty_box_width: 40,       // Reduced from 100 to catch smaller cells (e.g. 54pt)
        min_empty_box_height: 15,      // Reduced from 30 to catch single-line cells (e.g. 18pt)
        min_field_width: 20,
        min_field_height: 10,
        page_edge_margin: 30
    },
    aggressive: {
        min_underscore_length: 2,      // Detect shorter underscores
        min_empty_box_width: 25,       // Reduced from 60
        min_empty_box_height: 10,      // Reduced from 20
        min_field_width: 15,           // Allow smaller fields
        min_field_height: 8,
        page_edge_margin: 20           // Smaller margin from edges
    }
};

/**
 * Apply detection sensitivity preset to settings before passing to Python.
 * This translates the preset name ('aggressive', 'conservative', etc.) into
 * the actual numeric parameter values.
 */
function applyDetectionPreset(settings) {
    const presetName = settings.detection_sensitivity || 'standard';
    const preset = SENSITIVITY_PRESETS[presetName];
    
    if (preset) {
        // Merge preset values into settings (preset overrides individual values)
        return { ...settings, ...preset };
    }
    return settings;
}

// Initialize settings store with defaults
const settingsStore = new Store({
    name: 'settings',
    defaults: DEFAULT_SETTINGS,
    migrations: {
        '2.0.0': store => {
            store.set('stats', DEFAULT_SETTINGS.stats);
        },
        '2.1.0': store => {
            store.set('templates', {});
        }
    }
});

let mainWindow;

// Get the correct path for bundled resources
function getResourcePath(relativePath) {
    if (app.isPackaged) {
        return path.join(process.resourcesPath, relativePath);
    }
    return path.join(__dirname, relativePath);
}

// Get Python executable path
// Get the smart_fillable script/executable path
function getPythonScriptPath() {
    const archFolder = getArchFolder();

    if (app.isPackaged) {
        // In packaged app, use architecture-specific bundled executable
        const bundledExe = path.join(process.resourcesPath, 'python_dist', archFolder, exeName('smart_fillable'));
        if (fs.existsSync(bundledExe)) {
            return { type: 'exe', path: bundledExe };
        }
        // Fallback to Python script
        return { type: 'py', path: path.join(process.resourcesPath, 'smart_fillable_v24.py') };
    }
    // In development, preferentially use the Python script to ensure latest code changes are picked up
    // const bundledExe = path.join(__dirname, 'python_dist', archFolder, 'smart_fillable');
    // if (fs.existsSync(bundledExe)) {
    //    return { type: 'exe', path: bundledExe };
    // }
    return { type: 'py', path: path.join(__dirname, 'python_dist', 'smart_fillable_v24.py') };
}

// Helper to spawn the script (works with both bundled exe and Python)
function spawnScript(scriptInfo, args) {
    if (scriptInfo.type === 'exe') {
        return spawn(scriptInfo.path, args);
    } else {
        const pythonExec = getPythonPath();
        return spawn(pythonExec, [scriptInfo.path, ...args]);
    }
}

// ===== ACTIVE PROCESS TRACKING (for cancellation) =====
let activeChildProcess = null;

function setActiveProcess(proc) {
    activeChildProcess = proc;
    if (proc) {
        proc.on('close', () => {
            if (activeChildProcess === proc) {
                activeChildProcess = null;
            }
        });
        proc.on('error', () => {
            if (activeChildProcess === proc) {
                activeChildProcess = null;
            }
        });
    }
}

ipcMain.handle('cancel-processing', async () => {
    if (activeChildProcess) {
        try {
            activeChildProcess.kill('SIGTERM');
            // Give it a moment, then force kill if still alive
            setTimeout(() => {
                try {
                    if (activeChildProcess && !activeChildProcess.killed) {
                        activeChildProcess.kill('SIGKILL');
                    }
                } catch (e) { /* already dead */ }
            }, 2000);
            return { success: true };
        } catch (e) {
            return { success: false, error: e.message };
        }
    }
    return { success: false, error: 'No active process' };
});

// Get AcroForm fix script path (binary with Python fallback)
function getAcroFormFixScriptPath() {
    const archFolder = getArchFolder();

    // Check for bundled binary first
    const binDir = app.isPackaged
        ? path.join(process.resourcesPath, 'python_dist', archFolder)
        : path.join(__dirname, 'python_dist', archFolder);
    const binPath = path.join(binDir, exeName('apply_acroform_fix'));

    if (fs.existsSync(binPath)) {
        return { type: 'binary', path: binPath };
    }

    // Fallback to Python script
    const scriptPath = app.isPackaged
        ? path.join(process.resourcesPath, 'apply_acroform_fix.py')
        : path.join(__dirname, 'apply_acroform_fix.py');
    return { type: 'py', path: scriptPath };
}

function createWindow() {
    mainWindow = new BrowserWindow({
        width: 560,   // Reduced from 700 (20% smaller)
        height: 800,  // Increased to fit all content without scrolling
        minWidth: 500,
        minHeight: 600,
        maxWidth: 700,  // Keep width constrained for consistent look
        resizable: true,
        titleBarStyle: 'hiddenInset',
        backgroundColor: '#1a1a2e',
        webPreferences: {
            nodeIntegration: true,
            contextIsolation: false
        }
    });

    mainWindow.loadFile('index.html');
    
    // Uncomment for debugging
    // mainWindow.webContents.openDevTools();

    // Handle renderer crash (GPU process failure) — reload instead of going blank
    mainWindow.webContents.on('render-process-gone', (event, details) => {
        console.log('Renderer crashed:', details.reason);
        if (details.reason !== 'clean-exit') {
            mainWindow.loadFile('index.html');
        }
    });
}

app.whenReady().then(async () => {
    const { session } = require('electron');

    try {
        await session.defaultSession.clearCache();
        console.log('[DEBUG] Session cache cleared');
    } catch (e) {
        console.warn('Failed to clear cache:', e);
    }

    setupAutoUpdater();

    createWindow();

    // Start persistent Python server for fast PDF processing
    startPythonServer();

    // Auto-update check (if enabled in settings) - only in packaged app
    const checkForUpdates = settingsStore.get('settings.check_for_updates', true);
    if (checkForUpdates && app.isPackaged) {
        const autoUpdater = setupAutoUpdater();
        autoUpdater.checkForUpdatesAndNotify();
    }
    
    // Create application menu
    const template = [
        {
            label: 'Fill That PDF!',
            submenu: [
                {
                    label: 'About Fill That PDF!',
                    click: () => {
                        const isPro = appConfig.isPro;
                        const isDemo = appConfig.isDemo;
                        const name = appConfig.productName;
                        let featuresList = '• AI-powered field detection\n• Instant test fill\n• Smart formatting';
                        
                        if (isPro) {
                            featuresList += '\n• Batch PDF processing\n• Visual field editor';
                        }
                        
                        let message = name;
                        if (isDemo) {
                            // Calculate trial days if possible
                            const storeStart = new Store().get('fillthatpdf_trial_start');
                            if (storeStart) {
                                const daysRemaining = Math.max(0, 7 - Math.floor((new Date() - new Date(storeStart)) / (1000 * 60 * 60 * 24)));
                                message += `\n(Trial: ${daysRemaining} days remaining)`;
                            }
                        }

                        dialog.showMessageBox(mainWindow, {
                            type: 'info',
                            title: 'About Fill That PDF!',
                            message: message,
                            detail: `Version: ${appConfig.version}
Build: April 2026

Features:
${featuresList}

© 2026 Fill That PDF! All rights reserved.`,
                            buttons: ['OK'],
                            icon: null
                        });
                    }
                },
                { type: 'separator' },
                { role: 'services' },
                { type: 'separator' },
                { role: 'hide' },
                { role: 'hideOthers' },
                { role: 'unhide' },
                { type: 'separator' },
                { role: 'quit' }
            ]
        },
        {
            label: 'Edit',
            submenu: [
                { role: 'undo' },
                { role: 'redo' },
                { type: 'separator' },
                { role: 'cut' },
                { role: 'copy' },
                { role: 'paste' },
                { role: 'selectAll' }
            ]
        },
        {
            label: 'View',
            submenu: [
                { role: 'reload' },
                { role: 'toggleDevTools' },
                { type: 'separator' },
                { role: 'resetZoom' },
                { role: 'zoomIn' },
                { role: 'zoomOut' }
            ]
        },
        {
            label: 'Window',
            submenu: [
                { role: 'minimize' },
                { role: 'close' }
            ]
        }
    ];
    
    const menu = Menu.buildFromTemplate(template);
    Menu.setApplicationMenu(menu);
});

app.on('window-all-closed', () => {
    stopPythonServer();
    if (process.platform !== 'darwin') {
        app.quit();
    }
});

app.on('will-quit', () => {
    stopPythonServer();
});

app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
        createWindow();
    }
});

// Handle file selection dialogs
ipcMain.handle('select-input-file', async () => {
    const result = await dialog.showOpenDialog(mainWindow, {
        title: 'Select PDF File',
        filters: [{ name: 'PDF Files', extensions: ['pdf'] }],
        properties: ['openFile']
    });
    
    if (!result.canceled && result.filePaths.length > 0) {
        return result.filePaths[0];
    }
    return null;
});

ipcMain.handle('select-input-file-or-folder', async () => {
    // macOS supports both openFile + openDirectory in one dialog
    // On Windows, we fall back to two-step approach
    if (process.platform === 'darwin') {
        const result = await dialog.showOpenDialog(mainWindow, {
            title: 'Select PDF File or Folder',
            filters: [{ name: 'PDF Files', extensions: ['pdf'] }],
            properties: ['openFile', 'openDirectory']
        });
        if (!result.canceled && result.filePaths.length > 0) {
            return result.filePaths[0];
        }
        return null;
    } else {
        // Windows: show a message box asking what to select
        const { response } = await dialog.showMessageBox(mainWindow, {
            type: 'question',
            buttons: ['Select PDF File', 'Select Folder', 'Cancel'],
            defaultId: 0,
            title: 'Select Input',
            message: 'What would you like to select?'
        });
        if (response === 0) {
            const result = await dialog.showOpenDialog(mainWindow, {
                title: 'Select PDF File',
                filters: [{ name: 'PDF Files', extensions: ['pdf'] }],
                properties: ['openFile']
            });
            if (!result.canceled && result.filePaths.length > 0) return result.filePaths[0];
        } else if (response === 1) {
            const result = await dialog.showOpenDialog(mainWindow, {
                title: 'Select Folder with PDFs',
                properties: ['openDirectory']
            });
            if (!result.canceled && result.filePaths.length > 0) return result.filePaths[0];
        }
        return null;
    }
});

ipcMain.handle('select-input-folder', async () => {
    const result = await dialog.showOpenDialog(mainWindow, {
        title: 'Select Input Folder',
        properties: ['openDirectory']
    });
    
    if (!result.canceled && result.filePaths.length > 0) {
        return result.filePaths[0];
    }
    return null;
});

ipcMain.handle('scan-folder-for-pdfs', async (event, folderPath) => {
    try {
        const files = fs.readdirSync(folderPath);
        // Include any file ending with .pdf
        return files.filter(file => {
            return file.toLowerCase().endsWith('.pdf');
        }).map(file => path.join(folderPath, file));
    } catch (error) {
        throw new Error(`Failed to scan folder: ${error.message}`);
    }
});

ipcMain.handle('analyze-folder-content', async (event, filePaths) => {
    const { PDFDocument } = require('pdf-lib');
    let hasBlank = false;
    let hasFillable = false;

    for (const filePath of filePaths) {
        try {
            const bytes = fs.readFileSync(filePath);
            const pdfDoc = await PDFDocument.load(bytes);
            const form = pdfDoc.getForm();
            const fieldCount = form.getFields().length;

            if (fieldCount > 0) {
                hasFillable = true;
            } else {
                hasBlank = true;
            }

            if (hasBlank && hasFillable) return 'mixed';
        } catch (e) {
            console.error(`Error analyzing ${filePath}:`, e);
        }
    }

    if (hasBlank && hasFillable) return 'mixed';
    if (hasFillable) return 'all-fillable';
    if (hasBlank) return 'all-blank';
    return 'none';
});

ipcMain.handle('select-output-folder', async () => {
    const result = await dialog.showOpenDialog(mainWindow, {
        title: 'Select Output Folder',
        properties: ['openDirectory', 'createDirectory']
    });
    
    if (!result.canceled && result.filePaths.length > 0) {
        return result.filePaths[0];
    }
    return null;
});

// Template Management IPC Handlers
ipcMain.handle('get-templates', async () => {
    console.log('[IPC] get-templates requested');
    const templates = settingsStore.get('templates') || {};
    console.log('[IPC] get-templates returning', Object.keys(templates).length, 'templates');
    return templates;
});

ipcMain.handle('save-template', async (event, name, settings) => {
    const templates = settingsStore.get('templates') || {};
    // Only save the actual configuration fields, not system stats or other logic
    // We filter which keys are saved to keep templates clean
    // But for simplicity, we can just save the whole settings object passed, assuming renderer filtered it
    templates[name] = settings;
    settingsStore.set('templates', templates);
    return true;
});

ipcMain.handle('delete-template', async (event, name) => {
    const templates = settingsStore.get('templates') || {};
    if (templates[name]) {
        delete templates[name];
        settingsStore.set('templates', templates);
        return true;
    }
    return false;
});

// Update Checker IPC Handler
// check-for-updates is now handled by electron-updater via setupAutoUpdater()

// Show error dialog
ipcMain.handle('show-error', async (event, title, message) => {
    await dialog.showMessageBox(mainWindow, {
        type: 'error',
        title: title,
        message: message,
        buttons: ['OK']
    });
});

// Show success dialog
ipcMain.handle('show-success', async (event, title, message) => {
    await dialog.showMessageBox(mainWindow, {
        type: 'info',
        title: title,
        message: message,
        buttons: ['OK']
    });
});

// Helper function to run garbage field cleanup on a PDF
async function runGarbageFieldCleanup(pdfPath) {
    return new Promise((resolve, reject) => {
        // Get sensitivity setting
        const sensitivity = settingsStore.get('garbage_cleanup_sensitivity', 'standard');

        // Check for bundled executable first (prioritize compiled binary)
        const fs = require('fs');
        const arch = getArchFolder();
        
        let binDir = app.isPackaged 
            ? path.join(process.resourcesPath, 'python_dist', arch)
            : path.join(__dirname, 'python_dist', arch);
            
        let binPath = path.join(binDir, exeName('garbage_field_cleanup'));

        let cleanupProcess;

        if (fs.existsSync(binPath)) {
            console.log('[Garbage Cleanup] Using binary:', binPath);
            if (process.platform === 'darwin') {
                try {
                    fs.chmodSync(binPath, '755');
                } catch (e) {
                    console.error('[Garbage Cleanup] Failed to chmod binary:', e);
                }
            }
            cleanupProcess = spawn(binPath, [pdfPath, pdfPath, '--sensitivity', sensitivity]);
            setActiveProcess(cleanupProcess);
        } else {
            // Fallback to Python script
            console.log('[Garbage Cleanup] Binary not found, falling back to script');
            const pythonExec = getPythonPath();
            let scriptDir;
            if (app.isPackaged) {
                scriptDir = process.resourcesPath;
            } else {
                scriptDir = path.join(__dirname, 'python_dist');
            }
            const scriptPath = path.join(scriptDir, 'garbage_field_cleanup.py');
            const args = [scriptPath, pdfPath, pdfPath, '--sensitivity', sensitivity];
            console.log('[Garbage Cleanup] Running script with args:', args);
            cleanupProcess = spawn(pythonExec, args);
            setActiveProcess(cleanupProcess);
        }

        let output = '';
        let errorOutput = '';

        cleanupProcess.stdout.on('data', (data) => {
            output += data.toString();
        });

        cleanupProcess.stderr.on('data', (data) => {
            errorOutput += data.toString();
        });

        cleanupProcess.on('close', (code) => {
            if (code === null || code === 143 || code === 137) {
                reject(new Error('CANCELLED'));
                return;
            }
            if (code === 0) {
                try {
                    const jsonStart = output.indexOf('{');
                    if (jsonStart >= 0) {
                        const result = JSON.parse(output.slice(jsonStart));
                        resolve(result);
                    } else {
                        resolve({ fields_removed: 0, pages_cleaned: [], success: true });
                    }
                } catch (e) {
                    resolve({ fields_removed: 0, pages_cleaned: [], success: true });
                }
            } else {
                reject(new Error(`Garbage cleanup failed: ${errorOutput}`));
            }
        });
        
        cleanupProcess.on('error', (err) => {
            reject(err);
        });
    });
}

// Helper function to run auto-rename on all fields in a PDF
async function runAutoRenameAll(pdfPath) {
    return new Promise((resolve, reject) => {
        const fs = require('fs');
        const arch = getArchFolder();
        
        // Check for bundled executable first (prioritize compiled binary)
        let binDir = app.isPackaged 
            ? path.join(process.resourcesPath, 'python_dist', arch)
            : path.join(__dirname, 'python_dist', arch);
            
        let binPath = path.join(binDir, exeName('auto_rename_all'));

        let autoNameProcess;

        if (fs.existsSync(binPath)) {
            console.log('[Auto Rename All] Using binary:', binPath);
            if (process.platform === 'darwin') {
                try {
                    fs.chmodSync(binPath, 0o755);
                    try {
                        execSync(`xattr -d com.apple.quarantine "${binPath}"`);
                    } catch (e) {
                        console.log('xattr command failed (ignoring):', e.message);
                    }
                } catch (e) {
                    console.error('Failed to set executable permissions:', e);
                }
            }
            autoNameProcess = spawn(binPath, [pdfPath]);
            setActiveProcess(autoNameProcess);
        } else {
            // Fallback to Python script
            console.log('[Auto Rename All] Binary not found, falling back to script');
            const pythonExec = getPythonPath();
            let scriptDir;
            if (app.isPackaged) {
                scriptDir = process.resourcesPath;
            } else {
                scriptDir = path.join(__dirname, 'python_dist');
            }
            const scriptPath = path.join(scriptDir, 'auto_rename_all.py');
            autoNameProcess = spawn(pythonExec, [scriptPath, pdfPath]);
            setActiveProcess(autoNameProcess);
        }

        let output = '';
        let errorOutput = '';

        autoNameProcess.stdout.on('data', (data) => {
            output += data.toString();
        });

        autoNameProcess.stderr.on('data', (data) => {
            errorOutput += data.toString();
        });

        autoNameProcess.on('close', (code) => {
            if (code === null || code === 143 || code === 137) {
                reject(new Error('CANCELLED'));
                return;
            }
            if (code === 0) {
                try {
                    const jsonStart = output.indexOf('{');
                    if (jsonStart >= 0) {
                        const result = JSON.parse(output.slice(jsonStart));
                        if (result.renamed !== undefined) {
                            mainWindow.webContents.send('progress-update', `   Renamed ${result.renamed} of ${result.total} fields\n`);
                        }
                    }
                    resolve(true);
                } catch (e) {
                    resolve(true); // Still successful even if we can't parse output
                }
            } else {
                reject(new Error(`Auto-rename failed: ${errorOutput}`));
            }
        });
        
        autoNameProcess.on('error', (err) => {
            reject(err);
        });
    });
}

// Run the Python script to make PDF fillable
ipcMain.handle('run-make-fillable', async (event, inputPath, outputPath) => {
    // Demo mode: Check PDF usage limit
    if (appConfig.isDemo) {
        const pdfsProcessed = settingsStore.get('demo_pdfs_processed', 0);
        const maxFreePDFs = appConfig.demo.maxFreePDFs;
        
        if (pdfsProcessed >= maxFreePDFs) {
            // Show upgrade dialog
            const result = await dialog.showMessageBox(mainWindow, {
                type: 'info',
                title: 'Demo Limit Reached',
                message: 'Free Trial Complete!',
                detail: `You've used your ${maxFreePDFs} free PDFs. Upgrade to continue creating fillable PDFs.\n\nStandard: Basic features\nPRO: Visual Editor, AI Assistant, Bulk Processing`,
                buttons: ['Buy Now', 'Later'],
                defaultId: 0
            });
            
            if (result.response === 0) {
                // Open purchase page
                require('electron').shell.openExternal('https://fillthatpdf.com/#pricing');
            }
            
            return { success: false, error: 'Demo limit reached', demoLimitReached: true };
        }
    }
    
    const inputDir = path.dirname(inputPath);
    const baseName = path.basename(inputPath, '.pdf');

    // Read output suffix from settings (use stored value, not default)
    const outputSuffix = settingsStore.get('output_suffix', '_fillable');

    let outputFilePath;
    if (outputPath) {
        outputFilePath = path.join(outputPath, `${baseName}${outputSuffix}.pdf`);
    } else {
        outputFilePath = path.join(inputDir, `${baseName}${outputSuffix}.pdf`);
    }

    // Get current settings, apply detection preset, and pass to Python script
    let currentSettings;
    try {
        currentSettings = applyDetectionPreset(settingsStore.store);
    } catch (e) {
        console.error('Settings processing error:', e.message);
        currentSettings = {};
    }

    const autoCalibrate = settingsStore.get('auto_calibrate', true);

    // ===== Post-processing helper (shared by both paths) =====
    async function runPostProcessing(outputFilePath, stdout) {
        // Increment demo PDF counter on success
        if (appConfig.isDemo) {
            const current = settingsStore.get('demo_pdfs_processed', 0);
            settingsStore.set('demo_pdfs_processed', current + 1);
        }

        // Check if auto-improve names is enabled
        const autoImproveNames = settingsStore.get('auto_improve_names', false);
        console.log('Auto-improve names setting:', autoImproveNames);
        mainWindow.webContents.send('progress-update', `\n📊 Auto-improve names setting: ${autoImproveNames}\n`);

        if (autoImproveNames) {
            try {
                mainWindow.webContents.send('progress-update', '🔄 Auto-improving field names...\n');
                await runAutoRenameAll(outputFilePath);
                mainWindow.webContents.send('progress-update', '✅ Field names improved!\n');
            } catch (err) {
                mainWindow.webContents.send('progress-update', `⚠️ Auto-naming skipped: ${err.message}\n`);
            }
        }

        // Run garbage field cleanup if enabled
        const enableGarbageCleanup = settingsStore.get('enable_garbage_field_cleanup', true);
        if (enableGarbageCleanup) {
            try {
                mainWindow.webContents.send('progress-update', '🧹 Cleaning up garbage fields...\n');
                const cleanupResult = await runGarbageFieldCleanup(outputFilePath);
                if (cleanupResult.fields_removed > 0) {
                    mainWindow.webContents.send('progress-update', `   ✅ Removed ${cleanupResult.fields_removed} garbage fields from ${cleanupResult.pages_cleaned.length} pages\n`);
                } else {
                    mainWindow.webContents.send('progress-update', '   ✅ No garbage fields detected\n');
                }
            } catch (err) {
                mainWindow.webContents.send('progress-update', `⚠️ Garbage cleanup skipped: ${err.message}\n`);
            }
        }

        return { success: true, outputPath: outputFilePath, log: stdout };
    }

    // ===== PATH A: Persistent server (fast — no startup overhead) =====
    // If the server process exists but isn't ready yet, wait for it instead of
    // falling through to the slow PATH B spawn.
    if (!isServerAvailable() && _serverProcess) {
        mainWindow.webContents.send('progress-update', 'Warming up engine…\n');
        await waitForServer(15000);
    }
    if (isServerAvailable()) {
        let stdout = '';
        try {
            const result = await serverMakeFillable({
                input: inputPath,
                output: outputFilePath,
                settings: currentSettings,
                auto_calibrate: autoCalibrate,
            }, (progressText) => {
                stdout += progressText;
                mainWindow.webContents.send('progress-update', progressText);
            });

            return await runPostProcessing(outputFilePath, stdout);
        } catch (err) {
            // If server fails, fall through to legacy spawn
            console.warn('Server request failed, falling back to spawn:', err.message);
        }
    }

    // ===== PATH B: Legacy spawn (fallback — works without server) =====
    return new Promise((resolve, reject) => {
        const settingsJson = JSON.stringify(currentSettings);
        const scriptInfo = getPythonScriptPath();
        const args = [inputPath, outputFilePath, '--settings', settingsJson];
        if (autoCalibrate) {
            args.push('--auto-calibrate');
        }

        const pythonProcess = spawnScript(scriptInfo, args);
        setActiveProcess(pythonProcess);

        let stdout = '';
        let stderr = '';

        pythonProcess.stdout.on('data', (data) => {
            const text = data.toString();
            stdout += text;
            // Send progress updates to renderer
            mainWindow.webContents.send('progress-update', text);
        });

        pythonProcess.stderr.on('data', (data) => {
            stderr += data.toString();
        });

        pythonProcess.on('close', async (code) => {
            if (code === null || code === 143 || code === 137) {
                // Process was killed (SIGTERM=143, SIGKILL=137)
                reject(new Error('CANCELLED'));
                return;
            }
            if (code === 0) {
                try {
                    const result = await runPostProcessing(outputFilePath, stdout);
                    resolve(result);
                } catch (postErr) {
                    resolve({ success: true, outputPath: outputFilePath, log: stdout });
                }
            } else {
                reject(new Error(`Process exited with code ${code}\n${stderr}`));
            }
        });

        pythonProcess.on('error', (err) => {
            reject(new Error(`Failed to start process: ${err.message}`));
        });
    });
});

// v20: Run staged inspection (no PDF generated, outputs JSON)
ipcMain.handle('run-inspection', async (event, inputPath, outputPath) => {
    // ===== Fast path: when the persistent server is running, skip the
    // separate inspection binary spawn entirely.  The v24 pipeline does not
    // produce inspection JSON (--inspect is a no-op), so the inspection step
    // only wastes ~5-7 s re-extracting the PyInstaller binary.  Returning
    // "no issues" immediately lets the renderer proceed straight to
    // run-make-fillable which IS routed through the fast server. =====
    if (!isServerAvailable() && _serverProcess) {
        await waitForServer(15000);
    }
    if (isServerAvailable()) {
        const inputDir = path.dirname(inputPath);
        const baseName = path.basename(inputPath, '.pdf');
        const outputSuffix = settingsStore.get('output_suffix', '_fillable');
        const outputFilePath = outputPath
            ? path.join(outputPath, `${baseName}${outputSuffix}.pdf`)
            : path.join(inputDir, `${baseName}${outputSuffix}.pdf`);

        return {
            success: true,
            outputPath: outputFilePath,
            issues: [],
            inspectionComplete: true,
        };
    }

    // ===== Legacy path: no server available — spawn the binary =====
    return new Promise((resolve, reject) => {
        const inputDir = path.dirname(inputPath);
        const baseName = path.basename(inputPath, '.pdf');

        // Read output suffix from settings (use stored value, not default)
        const outputSuffix = settingsStore.get('output_suffix', '_fillable');

        let outputFilePath;
        if (outputPath) {
            outputFilePath = path.join(outputPath, `${baseName}${outputSuffix}.pdf`);
        } else {
            outputFilePath = path.join(inputDir, `${baseName}${outputSuffix}.pdf`);
        }
        
        // Use bundled executable or Python script
        const scriptInfo = getPythonScriptPath();
        
        // Get current settings, apply detection preset, and pass to Python script
        const currentSettings = applyDetectionPreset(settingsStore.store);
        const settingsJson = JSON.stringify(currentSettings);
        
        // Use --inspect flag for staged inspection, include settings
        // Use --inspect flag for staged inspection, include settings
        const autoCalibrate = settingsStore.get('auto_calibrate', true);
        const args = [inputPath, outputFilePath, '--settings', settingsJson, '--inspect'];
        if (autoCalibrate) {
            args.push('--auto-calibrate');
        }
        
        const pythonProcess = spawnScript(scriptInfo, args);
        setActiveProcess(pythonProcess);

        let allIssues = [];
        let inspectionComplete = false;
        let lineBuffer = '';  // Buffer for incomplete lines across chunks
        
        pythonProcess.stdout.on('data', (data) => {
            // Append to buffer and process complete lines
            lineBuffer += data.toString();
            
            // Split on newlines, keeping last incomplete line in buffer
            const lines = lineBuffer.split('\n');
            lineBuffer = lines.pop();  // Keep last (possibly incomplete) line
            
            for (const line of lines) {
                const trimmed = line.trim();
                if (trimmed.startsWith('{')) {
                    try {
                        const jsonData = JSON.parse(trimmed);
                        
                        // Emit progress updates to renderer
                        if (jsonData.type === 'stage_start') {
                            mainWindow.webContents.send('inspection-stage-start', jsonData);
                        } else if (jsonData.type === 'stage_complete') {
                            mainWindow.webContents.send('inspection-stage-complete', jsonData);
                            // Collect issues
                            if (jsonData.issues) {
                                allIssues = allIssues.concat(jsonData.issues);
                            }
                        } else if (jsonData.type === 'inspection_complete') {
                            inspectionComplete = true;
                            mainWindow.webContents.send('inspection-complete', {
                                ...jsonData,
                                issues: allIssues,
                                outputPath: outputFilePath
                            });
                        }
                    } catch (e) {
                        // Log parse errors for debugging (large JSON lines may still fail)
                        console.error('JSON parse error:', e.message, 'Line length:', trimmed.length);
                    }
                } else if (trimmed) {
                    // Regular progress text
                    mainWindow.webContents.send('progress-update', trimmed + '\n');
                }
            }
        });
        
        let stderrOutput = '';
        pythonProcess.stderr.on('data', (data) => {
            const str = data.toString();
            stderrOutput += str;
            console.error('Python stderr:', str);
            mainWindow.webContents.send('progress-update', str);
        });
        
        pythonProcess.on('close', (code) => {
            if (code === null || code === 143 || code === 137) {
                reject(new Error('CANCELLED'));
                return;
            }
            if (code === 0) {
                resolve({
                    success: true,
                    outputPath: outputFilePath,
                    issues: allIssues,
                    inspectionComplete
                });
            } else {
                reject(new Error(`Inspection process exited with code ${code}\n\nError Details:\n${stderrOutput}`));
            }
        });

        pythonProcess.on('error', (err) => {
            reject(new Error(`Failed to start inspection: ${err.message}`));
        });
    });
});

// v20: Apply fixes and generate PDF
ipcMain.handle('apply-inspection-fixes', async (event, inputPath, outputPath, issueIds) => {
    return new Promise((resolve, reject) => {
        const inputDir = path.dirname(inputPath);
        const baseName = path.basename(inputPath, '.pdf');
        
        // Read output suffix from settings (use stored value, not default)
        const outputSuffix = settingsStore.get('output_suffix', '_fillable');
        
        let outputFilePath;
        if (outputPath) {
            outputFilePath = path.join(outputPath, `${baseName}${outputSuffix}.pdf`);
        } else {
            outputFilePath = path.join(inputDir, `${baseName}${outputSuffix}.pdf`);
        }
        
        // Use bundled executable or Python script
        const scriptInfo = getPythonScriptPath();
        
        // Get current settings, apply detection preset, and pass to Python script
        const currentSettings = applyDetectionPreset({ ...DEFAULT_SETTINGS, ...settingsStore.store });
        const settingsJson = JSON.stringify(currentSettings);
        
        // Use --fix flag with comma-separated IDs AND settings
        const fixArg = issueIds.join(',');
        const pythonProcess = spawnScript(scriptInfo, [inputPath, outputFilePath, '--fix', fixArg, '--settings', settingsJson]);
        setActiveProcess(pythonProcess);

        let stdout = '';
        let stderr = '';

        pythonProcess.stdout.on('data', (data) => {
            const text = data.toString();
            stdout += text;
            mainWindow.webContents.send('progress-update', text);
        });

        pythonProcess.stderr.on('data', (data) => {
            stderr += data.toString();
        });

        pythonProcess.on('close', async (code) => {
            if (code === null || code === 143 || code === 137) {
                reject(new Error('CANCELLED'));
                return;
            }
            if (code === 0) {
                // Check if auto-improve names is enabled
                const autoImproveNames = settingsStore.get('auto_improve_names', false);
                console.log('Auto-improve names setting:', autoImproveNames);
                mainWindow.webContents.send('progress-update', `\n📊 Auto-improve names: ${autoImproveNames}\n`);
                
                if (autoImproveNames) {
                    try {
                        mainWindow.webContents.send('progress-update', '🔄 Auto-improving field names...\n');
                        await runAutoRenameAll(outputFilePath);
                        mainWindow.webContents.send('progress-update', '✅ Field names improved!\n');
                    } catch (err) {
                        mainWindow.webContents.send('progress-update', `⚠️ Auto-naming skipped: ${err.message}\n`);
                    }
                }
                
                // Run garbage field cleanup if enabled
                const enableGarbageCleanup = settingsStore.get('enable_garbage_field_cleanup', true);
                if (enableGarbageCleanup) {
                    try {
                        mainWindow.webContents.send('progress-update', '🧹 Cleaning up garbage fields...\n');
                        const cleanupResult = await runGarbageFieldCleanup(outputFilePath);
                        if (cleanupResult.fields_removed > 0) {
                            mainWindow.webContents.send('progress-update', `   ✅ Removed ${cleanupResult.fields_removed} garbage fields from ${cleanupResult.pages_cleaned.length} pages\n`);
                        } else {
                            mainWindow.webContents.send('progress-update', '   ✅ No garbage fields detected\n');
                        }
                    } catch (err) {
                        mainWindow.webContents.send('progress-update', `⚠️ Garbage cleanup skipped: ${err.message}\n`);
                    }
                }
                
                resolve({ success: true, outputPath: outputFilePath, log: stdout });
            } else {
                reject(new Error(`Fix process exited with code ${code}\n${stderr}`));
            }
        });
        
        pythonProcess.on('error', (err) => {
            reject(new Error(`Failed to apply fixes: ${err.message}`));
        });
    });
});

// Run the JavaScript fill script
ipcMain.handle('run-test-fill', async (event, inputPath, outputPath) => {
    return new Promise(async (resolve, reject) => {
        try {
            const inputDir = path.dirname(inputPath);
            const baseName = path.basename(inputPath, '.pdf');
            
            // Read test fill suffix from settings
            const testfillSuffix = settingsStore.get('testfill_suffix', '_FILLED');
            
            let outputFilePath;
            if (outputPath) {
                outputFilePath = path.join(outputPath, `${baseName}${testfillSuffix}.pdf`);
            } else {
                outputFilePath = path.join(inputDir, `${baseName}${testfillSuffix}.pdf`);
            }

            // ========== SMART MODE (Python - Format-Aware) ==========
            // Always try Python fill first (same as EZ-Filler), fall back to JS if unavailable
            mainWindow.webContents.send('progress-update', '🧠 Smart Fill Mode (Python with format detection)\n');
            mainWindow.webContents.send('progress-update', '📄 Loading PDF...\n');

            // Use helper to get architecture-specific path
            const bundledExe = getBundledExecutable('fill_pdf_v3');

            let pythonProcess;
            // DEV: Only force Python in development
            const forceUsePython = !app.isPackaged;

            if (!forceUsePython && bundledExe && fs.existsSync(bundledExe)) {
                console.log('Running bundled fill_pdf:', bundledExe);
                pythonProcess = spawn(bundledExe, [inputPath, outputFilePath]);
            } else {
                // Build Python script path
                const pythonScriptName = 'fill_pdf_v3.py';
                let pythonScript;

                if (app.isPackaged) {
                    pythonScript = path.join(process.resourcesPath, pythonScriptName);
                } else {
                    pythonScript = path.join(__dirname, 'python_dist', pythonScriptName);
                }

                // Check if script exists
                if (!fs.existsSync(pythonScript)) {
                    mainWindow.webContents.send('progress-update', `⚠️  Python script not found: ${pythonScript}\n`);
                    mainWindow.webContents.send('progress-update', '⚡ Falling back to Quick Mode...\n');
                    // Fall through to JavaScript mode
                } else {
                    console.log('Running Python fill_pdf:', pythonScript);
                    pythonProcess = spawn(python(), [pythonScript, inputPath, outputFilePath]);
                }
            }

            if (pythonProcess) {
                setActiveProcess(pythonProcess);

                pythonProcess.stdout.on('data', (data) => {
                    const lines = data.toString().split('\n');
                    lines.forEach(line => {
                        if (line.trim()) {
                            mainWindow.webContents.send('progress-update', line + '\n');
                        }
                    });
                });

                pythonProcess.stderr.on('data', (data) => {
                    mainWindow.webContents.send('progress-update', `⚠️  ${data.toString()}\n`);
                });

                pythonProcess.on('close', (code) => {
                    if (code === null || code === 143 || code === 137) {
                        reject(new Error('CANCELLED'));
                        return;
                    }
                    if (code === 0) {
                        mainWindow.webContents.send('progress-update', '✅ Fill complete!\n');
                        resolve({ success: true, outputPath: outputFilePath });
                    } else {
                        mainWindow.webContents.send('progress-update', `❌ Python exited with code ${code}\n`);
                        reject(new Error(`Python fill failed with code ${code}`));
                    }
                });

                return; // Exit early, Python handles everything
            }

            // ========== FALLBACK: QUICK MODE (JavaScript - Name-Based) ==========
            mainWindow.webContents.send('progress-update', '⚡ Quick Fill Mode (JavaScript)\n');
            
            const { PDFDocument, PDFTextField, PDFCheckBox, PDFDropdown, PDFRadioGroup } = require('pdf-lib');
            
            mainWindow.webContents.send('progress-update', '📄 Loading PDF...\n');
            
            const existingPdfBytes = fs.readFileSync(inputPath);
            const pdfDoc = await PDFDocument.load(existingPdfBytes);
            
            const form = pdfDoc.getForm();
            const fields = form.getFields();
            
            mainWindow.webContents.send('progress-update', `📊 Found ${fields.length} fields to fill\n`);
            
            // Sample data
            const sampleData = {
                names: ['John Smith', 'Sarah Johnson', 'Michael Brown', 'Emily Davis', 'David Wilson'],
                companies: ['ABC Corp', 'XYZ Industries', 'Global Solutions', 'Tech Innovations'],
                addresses: ['123 Main St', '456 Oak Ave', '789 Pine Rd', '321 Elm St'],
                emails: ['john@email.com', 'sarah@company.com', 'info@business.org'],
                phones: ['(734) 555-0123', '(248) 555-0456', '(313) 555-0789'],
                dates: ['01/15/2026', '02/20/2026', '06/15/2025', '09/30/2026'],
                numbers: ['1500', '2000', '750', '3000', '500', '1200'],
                zipcodes: ['48201', '48202', '48226', '48235', '48301'],
                states: ['MI', 'OH', 'IL', 'IN', 'WI', 'CA', 'NY', 'TX'],
                // Currency values (will be formatted with $ and decimals)
                currencyValues: [1500.00, 2000.50, 750.25, 3000.00, 500.75, 1200.00, 850.00, 425.50],
                // Plain number values for numeric fields (integers)
                integerValues: [12, 25, 8, 45, 100, 15, 30, 50, 75, 200],
                // Decimal number values for measurements
                decimalValues: [12.5, 25.75, 8.25, 45.0, 100.5, 15.25, 30.0, 50.5]
            };
            
            const getRandomItem = (arr) => arr[Math.floor(Math.random() * arr.length)];
            const formatWithCommas = (num, decimals = 2) => {
                // Handle undefined/null/NaN values
                if (num === undefined || num === null || isNaN(num)) {
                    return '0.00';
                }
                return parseFloat(num).toLocaleString('en-US', {
                    minimumFractionDigits: decimals,
                    maximumFractionDigits: decimals
                });
            };
            
            // Currency keywords - fields that need $ symbol and decimal formatting
            const currencyKeywords = [
                'amount', 'rebate', 'cost', 'price', 'fee', 'rate',
                'invoice', 'payment', 'balance', 'subtotal', 'discount',
                'savings', 'incentive', 'credit', 'deduction',
                'reward', 'dollar', 'money'
                // NOTE: 'charge' removed - conflicts with 'refrigerant charge' which is numeric
                // NOTE: 'total' removed - conflicts with 'Total Number in Household' etc
                // NOTE: 'funding' removed - can be date field ("Date: funding")
            ];
            
            // Keywords that should EXCLUDE currency detection (measurements/technical terms)
            const currencyExcludeKeywords = [
                'btuh', 'btu', 'refrigerant', 'cooling', 'heating', 'nominal',
                'kwh', 'watt', 'cfm', 'psi', 'temp', 'pressure',
                'sq ft', 'sqft', 'square', 'linear', 'feet', 'foot',
                'linear_feet', 'installed_linear',
                'household', 'number_in', 'proof', 'qualifying', 'assistance',
                'measure', 'details', 'provide', 'requested', 'needed', 'why',
                // Fields with these patterns should be plain text, not currency
                'funding_amount', 'scope_of_funding', 'associated_eea', 'eea_measure',
                'scope', 'associated', 'reason', 'approved', 'denied'
            ];
            
            // Number keywords - fields that need numeric values (no $ symbol)
            const numberKeywords = [
                'quantity', 'qty', 'count', 'score', 'point',
                'kwh', 'watt', 'btuh', 'btu', 'cfm', 'psi', 'amp', 'volt',
                'hour', 'minute', 'second', 'day', 'week', 'month',
                'feet', 'inch', 'meter', 'sqft', 'square', 'cubic',
                'gallon', 'liter', 'pound', 'ounce', 'ton',
                'percent', 'ratio', 'factor', 'coefficient',
                'sir', 'seer', 'eer', 'hspf', 'afue', 'uef',
                'r-value', 'u-value', 'shgc',
                // Test/measurement fields - these are next to unit symbols
                'tesp', 'tons', 'coil', 'capacity',
                // Additional measurement keywords from test results tables
                'db', 'wb', 'temp', 'temperature', 'airflow', 'air_flow',
                'efficiency', 'charge', 'pressure', 'suction', 'liquid',
                'superheat', 'subcooling', 'approach', 'entering', 'leaving',
                'reading', 'measured', 'verified', 'actual', 'specified',
                'nominal', 'rated', 'condenser', 'evaporator', 'refrigerant',
                'comments', 'linear_feet', 'linear_foot', 'installed_linear', 'household'
            ];
            
            // Keywords that should EXCLUDE number detection (text/detail fields)
            const numberExcludeKeywords = [
                'relationship', 'account_holder', 'customer_relationship',
                'applicant_relationship', 'applicant', 'differen', 
                'eea_measure', 'associated', 'details', 'provide', 
                'requested', 'needed', 'why', 'proof', 'qualifying',
                'scope', 'funding_amount'
            ];
            
            // Field name patterns that indicate measurement/numeric fields
            // These are typically positioned next to unit symbols like @, ˚F, %, Btuh, etc.
            const measurementFieldPatterns = [
                /^before_/i,      // Before_P37, Before_P37_2, etc.
                /^after_/i,       // After_P37, After_P37_2, etc.
                /^test_results/i, // Test_Results_P37, etc.
                /^field_p\d+/i,   // Generic Field_P## fields (often numeric)
                /^\d+_p\d+$/i,    // Fields starting with numbers like 1_P15, 1000_P15
                /^value_/i,       // Value fields
                /^reading_/i,     // Reading fields
                /^measurement_/i  // Measurement fields
            ];
            
            // Helper to check if field matches any keyword
            const matchesKeyword = (fieldName, keywords) => {
                const lower = fieldName.toLowerCase();
                return keywords.some(kw => lower.includes(kw));
            };
            
            // Helper to check if field matches measurement patterns
            const isMeasurementField = (fieldName) => {
                return measurementFieldPatterns.some(pattern => pattern.test(fieldName));
            };
            
            let filledCount = 0;
            const totalFields = fields.length;
            
            for (let i = 0; i < fields.length; i++) {
                const field = fields[i];
                const fieldName = field.getName();
                const fieldLower = fieldName.toLowerCase();
                
                // Update progress every 10 fields
                if (i % 10 === 0) {
                    const percent = Math.round((i / totalFields) * 100);
                    mainWindow.webContents.send('fill-progress', percent);
                }
                
                try {
                    if (field instanceof PDFTextField) {
                        let sampleValue;
                        
                        // Priority order matters! More specific matches first
                        if (fieldLower.includes('zip')) {
                            sampleValue = getRandomItem(sampleData.zipcodes);
                        } else if (fieldLower.includes('state') && !fieldLower.includes('estate') && !fieldLower.includes('statement')) {
                            sampleValue = getRandomItem(sampleData.states);
                        } else if (fieldLower.includes('phone') || fieldLower.includes('tel') || fieldLower.includes('fax')) {
                            sampleValue = getRandomItem(sampleData.phones);
                        } else if (fieldLower.includes('email') || fieldLower.includes('e-mail')) {
                            sampleValue = getRandomItem(sampleData.emails);
                        } else if (fieldLower.includes('date') || fieldLower.includes('installed_date') || fieldLower.includes('date_installed')) {
                            // Note: 'funding' removed - causes false positives on 'Funding_Amount_Requested'
                            sampleValue = getRandomItem(sampleData.dates);
                        } else if (fieldLower.includes('year')) {
                            sampleValue = '2026';
                        } else if (fieldLower.includes('r-value') || fieldLower.includes('rvalue') || fieldLower.includes('r_value')) {
                            // R-Value fields - format like R-19, R-30, R-38
                            const rValues = ['R-13', 'R-19', 'R-30', 'R-38', 'R-49'];
                            sampleValue = getRandomItem(rValues);
                        } else if (matchesKeyword(fieldName, currencyKeywords) && !matchesKeyword(fieldName, currencyExcludeKeywords)) {
                            // Currency fields - format with $ and decimals (e.g., "$1,500.00")
                            // But exclude if field contains measurement terms like btuh, refrigerant, etc.
                            const currencyValue = getRandomItem(sampleData.currencyValues);
                            sampleValue = '$' + formatWithCommas(currencyValue, 2);
                        } else if ((matchesKeyword(fieldName, numberKeywords) || isMeasurementField(fieldName)) && !matchesKeyword(fieldName, numberExcludeKeywords)) {
                            // Number/measurement fields - just numeric value, no $ (e.g., "25" or "12.5")
                            // Use integers for quantity-like fields, decimals for measurements
                            if (fieldLower.includes('quantity') || fieldLower.includes('qty') || 
                                fieldLower.includes('count') || fieldLower.includes('score') ||
                                fieldLower.includes('point')) {
                                sampleValue = String(getRandomItem(sampleData.integerValues));
                            } else {
                                // Measurements may have decimals (Before/After fields, Test Results, etc.)
                                sampleValue = String(getRandomItem(sampleData.decimalValues));
                            }
                        } else if (fieldLower.includes('name') || fieldLower.includes('contact')) {
                            sampleValue = getRandomItem(sampleData.names);
                        } else if (fieldLower.includes('company') || fieldLower.includes('business') || fieldLower.includes('contractor')) {
                            sampleValue = getRandomItem(sampleData.companies);
                        } else if (fieldLower.includes('address') || fieldLower.includes('street') || fieldLower.includes('city')) {
                            sampleValue = getRandomItem(sampleData.addresses);
                        } else {
                            // Default fallback
                            sampleValue = 'Sample';
                        }
                        
                        field.setText(sampleValue);
                        filledCount++;
                        
                    } else if (field instanceof PDFCheckBox) {
                        if (Math.random() > 0.3) {
                            field.check();
                        } else {
                            field.uncheck();
                        }
                        filledCount++;
                        
                    } else if (field instanceof PDFDropdown) {
                        const options = field.getOptions();
                        const validOptions = options.filter(opt => opt.trim() !== '');
                        if (validOptions.length > 0) {
                            field.select(getRandomItem(validOptions));
                            filledCount++;
                        }
                        
                    } else if (field instanceof PDFRadioGroup) {
                        const options = field.getOptions();
                        if (options.length > 0) {
                            field.select(getRandomItem(options));
                            filledCount++;
                        }
                    }
                } catch (err) {
                    // Skip fields that can't be filled
                }
            }
            
            mainWindow.webContents.send('fill-progress', 100);
            mainWindow.webContents.send('progress-update', `✅ Filled ${filledCount} fields\n`);
            mainWindow.webContents.send('progress-update', '💾 Saving PDF...\n');

            let pdfBytes;
            try {
                pdfBytes = await pdfDoc.save();
            } catch (saveErr) {
                // Fields with JS format actions can cause appearance rendering to fail
                // Retry without appearance updates - Acrobat will render them correctly
                mainWindow.webContents.send('progress-update', '⚠️  Retrying save (skipping appearance streams)...\n');
                pdfBytes = await pdfDoc.save({ updateFieldAppearances: false });
            }
            fs.writeFileSync(outputFilePath, pdfBytes);
            
            mainWindow.webContents.send('progress-update', `✅ Saved: ${outputFilePath}\n`);
            
            // Fix checkbox appearances for Acrobat compatibility
            // pdf-lib doesn't create proper ZapfDingbats appearance streams
            mainWindow.webContents.send('progress-update', '☑️  Fixing checkbox appearances for Acrobat...\n');
            
            const fixCheckboxPath = path.join(__dirname, 'fix_checkbox_appearances.py');
            
            // Check for bundled binary first
            const arch = getArchFolder();
            const binDir = app.isPackaged 
                ? path.join(process.resourcesPath, 'python_dist', arch)
                : path.join(__dirname, 'python_dist', arch);
            const fixCheckboxBin = path.join(binDir, exeName('fix_checkbox_appearances'));
            
            let fixProcess;
            
            try {
                await new Promise((resolveFix, rejectFix) => {
                    if (fs.existsSync(fixCheckboxBin)) {
                        console.log('[Fix Checkbox] Using binary:', fixCheckboxBin);
                        fixProcess = spawn(fixCheckboxBin, [outputFilePath]);
                    } else {
                        // Fallback to Python script
                        console.log('[Fix Checkbox] Binary not found, falling back to script');
                        const pythonPath = getPythonPath();
                        const fixScript = app.isPackaged
                            ? path.join(process.resourcesPath, 'fix_checkbox_appearances.py')
                            : fixCheckboxPath;
                        fixProcess = spawn(pythonPath, [fixScript, outputFilePath]);
                    }
                    setActiveProcess(fixProcess);
                    
                    fixProcess.stdout.on('data', (data) => {
                        const output = data.toString();
                        if (output.trim()) {
                            mainWindow.webContents.send('progress-update', `   ${output}`);
                        }
                    });
                    
                    fixProcess.stderr.on('data', (data) => {
                        console.error('Checkbox fix stderr:', data.toString());
                    });
                    
                    fixProcess.on('close', (code) => {
                        if (code === 0) {
                            resolveFix();
                        } else {
                            rejectFix(new Error(`Checkbox fix exited with code ${code}`));
                        }
                    });
                    
                    fixProcess.on('error', (err) => {
                        rejectFix(err);
                    });
                });
                
                mainWindow.webContents.send('progress-update', '   ✅ Checkbox appearances fixed\n');
            } catch (fixError) {
                mainWindow.webContents.send('progress-update', `   ⚠️ Checkbox fix warning: ${fixError.message}\n`);
            }
            
            mainWindow.webContents.send('progress-update', '✅ Test fill complete - ready for Acrobat!\n');
            
            resolve({ success: true, outputPath: outputFilePath, filledCount });
            
        } catch (error) {
            reject(new Error(`Fill failed: ${error.message}`));
        }
    });
});

// ===== SETTINGS IPC HANDLERS =====

// Get current settings
ipcMain.handle('get-settings', async () => {
    console.log('[IPC] get-settings requested');
    try {
        const settings = settingsStore.store;
        console.log('[IPC] get-settings returning:', settings ? 'Valid Data' : 'NULL/Undefined');
        return settings || {};
    } catch (e) {
        console.error('[IPC ERROR] get-settings failed:', e);
        throw e;
    }
});

// Save settings (partial or full update)
ipcMain.handle('save-settings', async (event, newSettings) => {
    try {
        // Send debug to app progress window
        if (mainWindow) {
            mainWindow.webContents.send('progress-update', '\\n=== SETTINGS SAVE DEBUG ===\\n');
            mainWindow.webContents.send('progress-update', `Received ${Object.keys(newSettings).length} settings from UI\\n`);
        }
        
        // Merge with existing settings
        let savedCount = 0;
        for (const [key, value] of Object.entries(newSettings)) {
            if (key in DEFAULT_SETTINGS) {
                settingsStore.set(key, value);
                savedCount++;
                
                // Show key settings being saved (expanded list)
                if (['output_suffix', 'testfill_suffix', 'date_format', 'phone_format', 'min_empty_box_width', 'detection_sensitivity'].includes(key)) {
                    if (mainWindow) {
                        mainWindow.webContents.send('progress-update', `   ✅ Saved '${key}' = '${value}'\\n`);
                    }
                }
            } else {
                // Log rejected keys
                if (mainWindow) {
                    mainWindow.webContents.send('progress-update', `   ⚠️ Ignored unknown setting '${key}'\\n`);
                }
                console.warn(`Attempted to save unknown setting: ${key}`);
            }
        }
        if (mainWindow) {
            mainWindow.webContents.send('progress-update', `✅ Saved ${savedCount} settings\\n`);
            mainWindow.webContents.send('progress-update', '=== END DEBUG ===\\n\\n');
        }
        
        return { success: true, settings: settingsStore.store };
    } catch (error) {
        console.error('Save settings error:', error);
        return { success: false, error: error.message };
    }
});

// Reset settings to defaults
ipcMain.handle('reset-settings', async () => {
    try {
        settingsStore.clear();
        // Re-set all defaults
        for (const [key, value] of Object.entries(DEFAULT_SETTINGS)) {
            settingsStore.set(key, value);
        }
        return { success: true, settings: settingsStore.store };
    } catch (error) {
        return { success: false, error: error.message };
    }
});

// Export settings to file
ipcMain.handle('export-settings', async () => {
    try {
        const { filePath } = await dialog.showSaveDialog(mainWindow, {
            title: 'Export Settings',
            defaultPath: 'fillthatpdf_settings.json',
            filters: [{ name: 'JSON Files', extensions: ['json'] }]
        });
        
        if (filePath) {
            const settings = settingsStore.store;
            fs.writeFileSync(filePath, JSON.stringify(settings, null, 2));
            return { success: true, filePath };
        }
        return { success: false, cancelled: true };
    } catch (error) {
        return { success: false, error: error.message };
    }
});

// Import settings from file
ipcMain.handle('import-settings', async () => {
    try {
        const result = await dialog.showOpenDialog(mainWindow, {
            title: 'Import Settings',
            filters: [{ name: 'JSON Files', extensions: ['json'] }],
            properties: ['openFile']
        });
        
        if (!result.canceled && result.filePaths.length > 0) {
            const filePath = result.filePaths[0];
            const content = fs.readFileSync(filePath, 'utf8');
            const newSettings = JSON.parse(content);
            
            // Validate: check if it looks like our settings (simple check)
            if (newSettings && (newSettings.testFillConfig || newSettings.checkbox_size_min)) {
                // Merge with existing logic (like save-settings)
                // We'll fully replace settings to be safe, but keep stats?
                // Actually usually import means "restore this configuration"
                
                // Preserve stats if imported settings don't have them or we want to keep current
                // Let's keep current machine stats unless user explicitly wants to overwrite (rare)
                const currentStats = settingsStore.get('stats');
                if (currentStats && !newSettings.stats) {
                    newSettings.stats = currentStats;
                } else if (currentStats && newSettings.stats) {
                    // If file has stats, ask user? For now, let's keep LOCAL stats to avoid messing up counts
                    // unless we are doing a full migration. 
                    // Improved decision: Keep local stats always.
                    newSettings.stats = currentStats;
                }
                
                settingsStore.store = newSettings;
                return { success: true, settings: newSettings };
            } else {
                return { success: false, error: "Invalid settings file format" };
            }
        }
        return { success: false, cancelled: true };
    } catch (error) {
        return { success: false, error: error.message };
    }
});

// ============================================================
// VISUAL FIELD EDITOR
// ============================================================

let editorWindow = null;
let editorData = null;

// Open the field editor window
ipcMain.handle('open-editor', async (event, pdfPath, outputPath) => {
    // PRO feature gate
    if (!appConfig.features.visualEditor) {
        dialog.showMessageBox({
            type: 'info',
            title: 'PRO Feature',
            message: 'Visual Field Editor is a PRO feature',
            detail: 'Upgrade to Fill That PDF! PRO to access the Visual Field Editor, AI Assistant, and unlimited bulk processing.',
            buttons: ['OK']
        });
        return { success: false, error: 'PRO feature' };
    }
    
    if (editorWindow) {
        editorWindow.focus();
        return { success: true };
    }
    
    // Get current global field settings from the settings store
    const globalFieldSettings = {
        fillColor: settingsStore.get('field_background_color', '#EDF4FF'),
        fillOpacity: settingsStore.get('field_background_opacity', 100),
        borderColor: settingsStore.get('field_border_color', '#000000'),
        borderVisible: settingsStore.get('field_border_visible', false),
        borderThickness: settingsStore.get('field_border_thickness', 1),
        borderStyle: settingsStore.get('field_border_style', 'solid'),
        fontSize: settingsStore.get('field_font_size', 9),
        fontFamily: settingsStore.get('field_font_family', 'Helvetica'),
        fontColor: settingsStore.get('field_font_color', '#000000'),
        checkboxStyle: settingsStore.get('checkbox_style', 'check'),
        checkboxBgColor: settingsStore.get('checkbox_bg_color', '#FFFFFF'),
        checkboxBorderColor: settingsStore.get('checkbox_border_color', '#000000'),
        displayTooltips: settingsStore.get('display_tooltips', true)
    };
    
    editorData = { pdfPath, outputPath, globalFieldSettings };
    
    editorWindow = new BrowserWindow({
        width: 1600,
        height: 900,
        minWidth: 1200,
        minHeight: 700,
        titleBarStyle: 'hiddenInset',
        backgroundColor: '#0a192f',
        webPreferences: {
            nodeIntegration: true,
            contextIsolation: false
        }
    });
    
    editorWindow.loadFile('editor_v5.html');
    editorWindow.maximize();
    
    editorWindow.on('closed', () => {
        // Close any floating panel windows that belong to this editor session
        for (const [, win] of panelWindows) {
            if (!win.isDestroyed()) win.close();
        }
        panelWindows.clear();

        // Notify main window that editor was closed
        if (mainWindow && !mainWindow.isDestroyed()) {
            mainWindow.webContents.send('editor-closed');
        }
        editorWindow = null;
        editorData = null;
    });

    return { success: true };
});

// ── Floating panel windows ──────────────────────────────────────────────────
// Map of  panelType ('global-styles' | 'properties')  →  BrowserWindow
const panelWindows = new Map();

const PANEL_CONFIGS = {
    'global-styles': { title: '🎨 Global Styles',       width: 360, height: 640, minWidth: 320, minHeight: 500 },
    'properties':    { title: '📋 Field Properties',    width: 680, height: 720, minWidth: 560, minHeight: 640 },
    'history':       { title: '🕐 Version History',     width: 520, height: 560, minWidth: 400, minHeight: 440 },
    'calc':          { title: '🧮 Calculations Manager', width: 760, height: 640, minWidth: 600, minHeight: 500 },
    'hyperlinks':    { title: '🔗 Hyperlinks Manager',  width: 780, height: 680, minWidth: 620, minHeight: 520 },
};

function createPanelWindow(type, data) {
    // Re-focus existing window and push fresh data instead of opening a second one
    if (panelWindows.has(type)) {
        const existing = panelWindows.get(type);
        if (!existing.isDestroyed()) {
            existing.focus();
            existing.webContents.send('panel-update', { type, data });
            return existing;
        }
        panelWindows.delete(type);
    }

    const cfg = PANEL_CONFIGS[type] || { title: 'Panel', width: 340, height: 600, minWidth: 300, minHeight: 440 };

    const win = new BrowserWindow({
        width:           cfg.width,
        height:          cfg.height,
        minWidth:        cfg.minWidth,
        minHeight:       cfg.minHeight,
        title:           cfg.title,
        titleBarStyle:   'hiddenInset',
        backgroundColor: '#0a192f',
        webPreferences: {
            nodeIntegration:  true,
            contextIsolation: false,
        },
        show: false,
        // Keep panels/modals above the editor window.  Using alwaysOnTop
        // with the 'floating' level floats above normal windows but still
        // lets OS dialogs (notarizer prompts, screensavers) take priority.
        // No parent set intentionally — lets the panel move to a second monitor.
        alwaysOnTop: true,
    });
    // 'floating' keeps us above the editor but below system UI.
    try { win.setAlwaysOnTop(true, 'floating'); } catch (_) {}

    win.loadFile('panel.html');

    win.once('ready-to-show', () => {
        win.show();
        // Send init data once renderer is ready
        win.webContents.send('panel-init', type, data);
    });

    win.on('closed', () => {
        panelWindows.delete(type);
        // Tell the editor the panel was closed so it can update its UI state
        if (editorWindow && !editorWindow.isDestroyed()) {
            editorWindow.webContents.send('panel-closed', type);
        }
    });

    panelWindows.set(type, win);
    return win;
}

// Editor → main: open (or focus) a panel window
ipcMain.handle('open-panel', async (event, type, data) => {
    createPanelWindow(type, data);
    return { success: true };
});

// Panel → main → editor  (panel sends actions / edits)
ipcMain.on('panel-to-editor', (event, payload) => {
    if (editorWindow && !editorWindow.isDestroyed()) {
        editorWindow.webContents.send('from-panel', payload);
    }
});

// Editor → main → panel  (editor pushes live data updates to the panel)
// A few sub-types are routed to an "owning" panel window when there is no
// window of that exact type (e.g. 'calc-suggestions' → 'calc').
const PANEL_SUBTYPE_ROUTES = {
    'calc-suggestions': 'calc',
};
ipcMain.on('editor-to-panel', (event, type, data) => {
    const hostType = PANEL_SUBTYPE_ROUTES[type] || type;
    const win = panelWindows.get(hostType);
    if (win && !win.isDestroyed()) {
        win.webContents.send('panel-update', { type, data });
    }
});

// Get editor data (called by editor window on load)
ipcMain.handle('get-editor-data', () => {
    return editorData;
});

// Extract fields from PDF and generate page images
ipcMain.handle('extract-fields', async (event, pdfPath) => {
    // ── Fast path: use the persistent Python server if available ──
    if (isServerAvailable()) {
        try {
            console.log('extract-fields: using persistent server');
            const result = await serverExtractFields(pdfPath);
            if (result.success && result.data) {
                return result.data;
            }
            // Server returned an error — fall through to spawn fallback
            console.warn('extract-fields: server returned error, falling back to spawn:', result.error);
        } catch (err) {
            console.warn('extract-fields: server request failed, falling back to spawn:', err.message);
        }
    }

    // ── Fallback: spawn a one-off process ──
    return new Promise((resolve, reject) => {
        // Check for architecture-specific bundled executable
        const archFolder = getArchFolder();
        const bundledExe = app.isPackaged
            ? path.join(process.resourcesPath, 'python_dist', archFolder, exeName('extract_fields'))
            : path.join(__dirname, 'python_dist', archFolder, exeName('extract_fields'));

        let pythonProcess;

        if (app.isPackaged && fs.existsSync(bundledExe)) {
            console.log('Running bundled extract_fields:', bundledExe);
            pythonProcess = spawn(bundledExe, [pdfPath]);
        } else {
            const pythonExec = getPythonPath();
            const scriptPath = app.isPackaged
                ? path.join(process.resourcesPath, 'extract_fields.py')
                : path.join(__dirname, 'python_dist', 'extract_fields.py');
            console.log('Running Python extract_fields:', scriptPath);
            pythonProcess = spawn(pythonExec, [scriptPath, pdfPath]);
        }
        setActiveProcess(pythonProcess);

        let stdout = '';
        let stderr = '';
        
        pythonProcess.stdout.on('data', (data) => {
            stdout += data.toString();
        });
        
        pythonProcess.stderr.on('data', (data) => {
            stderr += data.toString();
        });
        
        pythonProcess.on('close', (code) => {
            if (code === 0) {
                try {
                    // Parse the last complete JSON line (the final result)
                    const lines = stdout.trim().split('\n');
                    const lastLine = lines[lines.length - 1];
                    const result = JSON.parse(lastLine);
                    resolve(result);
                } catch (e) {
                    resolve({ error: `Failed to parse result: ${e.message}` });
                }
            } else {
                resolve({ error: `Extract failed with code ${code}: ${stderr}` });
            }
        });
        
        pythonProcess.on('error', (err) => {
            resolve({ error: `Failed to start extract: ${err.message}` });
        });
    });
});

// Apply field changes to PDF
ipcMain.handle('apply-field-changes', async (event, pdfPath, outputPath, changes) => {
    return new Promise((resolve, reject) => {
        // Check for architecture-specific bundled executable
        const archFolder = getArchFolder();
        const bundledExe = app.isPackaged
            ? path.join(process.resourcesPath, 'python_dist', archFolder, exeName('modify_fields'))
            : path.join(__dirname, 'python_dist', archFolder, exeName('modify_fields'));

        // Write changes to temp file (to avoid command line length issues)
        const changesFile = path.join(require('os').tmpdir(), `pdf_editor_changes_${Date.now()}_${Math.random().toString(36).slice(2, 8)}.json`);
        fs.writeFileSync(changesFile, JSON.stringify(changes));
        // DEBUG: Save a persistent copy of changes for debugging (timestamped to avoid overwrites)
        const debugTs = new Date().toISOString().replace(/[:.]/g, '-');
        const debugFile = path.join(require('os').tmpdir(), `DEBUG_changes_${debugTs}.json`);
        fs.writeFileSync(debugFile, JSON.stringify(changes, null, 2));
        // Also keep the latest pointer for quick access
        const debugLatest = path.join(require('os').tmpdir(), 'DEBUG_last_changes.json');
        fs.writeFileSync(debugLatest, JSON.stringify(changes, null, 2));
        console.log('[DEBUG] Changes saved to:', debugFile);

        let pythonProcess;

        if (app.isPackaged && fs.existsSync(bundledExe)) {
            console.log('Running bundled modify_fields:', bundledExe);
            const allSettings = settingsStore.store;
            const settingsJson = JSON.stringify(allSettings);
            pythonProcess = spawn(bundledExe, [pdfPath, outputPath, '--changes-file', changesFile, '--settings', settingsJson]);
        } else {
            const pythonExec = getPythonPath();
            const scriptPath = app.isPackaged
                ? path.join(process.resourcesPath, 'modify_fields.py')
                : path.join(__dirname, 'python_dist', 'modify_fields.py');
            console.log('Running Python modify_fields:', scriptPath);

            const allSettings = settingsStore.store;
            const settingsJson = JSON.stringify(allSettings);

            pythonProcess = spawn(pythonExec, [scriptPath, pdfPath, outputPath, '--changes-file', changesFile, '--settings', settingsJson]);
        }
        setActiveProcess(pythonProcess);

        let stdout = '';
        let stderr = '';
        
        pythonProcess.stdout.on('data', (data) => {
            stdout += data.toString();
        });
        
        pythonProcess.stderr.on('data', (data) => {
            stderr += data.toString();
        });
        
        pythonProcess.on('close', (code) => {
            // Clean up temp file
            try { fs.unlinkSync(changesFile); } catch (e) {}
            // DEBUG: Copy output PDF for inspection
            try { fs.copyFileSync(outputPath, path.join(require('os').tmpdir(), 'DEBUG_last_output.pdf')); } catch(e) {}
            
            if (code === 0) {
                try {
                    const result = JSON.parse(stdout.trim());
                    resolve(result);
                } catch (e) {
                    resolve({ error: `Failed to parse result: ${e.message}` });
                }
            } else {
                resolve({ error: `Modify failed with code ${code}: ${stderr}` });
            }
        });
        
        pythonProcess.on('error', (err) => {
            resolve({ error: `Failed to start modify: ${err.message}` });
        });
    });
});

// Handle editor save notification
ipcMain.on('editor-saved', (event, data) => {
    if (mainWindow) {
        mainWindow.webContents.send('editor-saved', data);
    }
});

// Handle app exit request (e.g. from trial expiry)
ipcMain.on('close-app', () => {
    app.quit();
});

// AI/Smart Assistant - Heuristic Naming
ipcMain.handle('auto-name-field', async (event, pdfPath, pageNum, rect) => {
    return new Promise((resolve, reject) => {
        try {
            // Check for bundled executable first
            // Determine correct architecture folder
            const archFolder = getArchFolder();
            const bundledExe = app.isPackaged
                ? path.join(process.resourcesPath, 'python_dist', archFolder, exeName('auto_name_field'))
                : path.join(__dirname, 'python_dist', archFolder, exeName('auto_name_field'));

            // rect is [x0, y0, x1, y1]
            const rectArgs = [
                pdfPath,
                pageNum.toString(),
                rect[0].toString(),
                rect[1].toString(),
                rect[2].toString(),
                rect[3].toString()
            ];

            let childProcess;
            if (fs.existsSync(bundledExe)) {
                console.log('Running bundled auto_name_field:', bundledExe);
                if (process.platform === 'darwin') {
                    try {
                        fs.chmodSync(bundledExe, 0o755);
                        try {
                            execSync(`xattr -d com.apple.quarantine "${bundledExe}"`);
                        } catch (e) {
                            // Ignore
                        }
                    } catch (e) {
                        console.error('Failed to set permissions:', e);
                    }
                }
                childProcess = spawn(bundledExe, rectArgs);
            } else {
                const pythonExec = getPythonPath();
                let scriptDir;
                if (app.isPackaged) {
                    scriptDir = process.resourcesPath;
                } else {
                    scriptDir = path.join(__dirname, 'python_dist');
                }
                const scriptPath = path.join(scriptDir, 'auto_name_field.py');
                console.log('Running Python auto_name_field:', scriptPath);
                childProcess = spawn(pythonExec, [scriptPath, ...rectArgs]);
            }
            setActiveProcess(childProcess);

            const proc = childProcess;
            let output = '';
            
            proc.stdout.on('data', (data) => {
                output += data.toString();
            });
            
            proc.on('close', (code) => {
                if (code === 0) {
                    try {
                        const result = JSON.parse(output.trim());
                        resolve(result.name || '');
                    } catch (e) {
                        console.error('JSON parse error from auto-name:', output);
                        resolve('');
                    }
                } else {
                    console.error('Auto-name process exited with code', code);
                    resolve('');
                }
            });
            
            proc.on('error', (err) => {
                console.error('Failed to spawn auto-name:', err);
                resolve('');
            });
            
        } catch (e) {
            console.error('Auto-name error:', e);
            resolve('');
        }
    });
});

// Batch Auto-Name
ipcMain.handle('auto-name-fields', async (event, pdfPath, fields) => {
    return new Promise((resolve, reject) => {
        try {
            // Check for bundled executable first
            // Determine correct architecture folder
            const archFolder = getArchFolder();
            const bundledExe = app.isPackaged
                ? path.join(process.resourcesPath, 'python_dist', archFolder, exeName('auto_name_field'))
                : path.join(__dirname, 'python_dist', archFolder, exeName('auto_name_field'));

            let childProcess;
            if (fs.existsSync(bundledExe)) {
                console.log('Running bundled auto_name_field (batch):', bundledExe);
                if (process.platform === 'darwin') {
                    try {
                        fs.chmodSync(bundledExe, 0o755);
                        try {
                            execSync(`xattr -d com.apple.quarantine "${bundledExe}"`);
                        } catch (e) {
                            // Ignore
                        }
                    } catch (e) {
                        console.error('Failed to set permissions:', e);
                    }
                }
                childProcess = spawn(bundledExe, []);
            } else {
                const pythonExec = getPythonPath();
                let scriptDir;
                if (app.isPackaged) {
                    scriptDir = process.resourcesPath;
                } else {
                    scriptDir = path.join(__dirname, 'python_dist');
                }
                const scriptPath = path.join(scriptDir, 'auto_name_field.py');
                console.log('Running Python auto_name_field (batch):', scriptPath);
                childProcess = spawn(pythonExec, [scriptPath]);
            }
            setActiveProcess(childProcess);

            const proc = childProcess;

            let output = '';
            let errorOutput = '';

            proc.stdout.on('data', (data) => {
                console.log('Auto-Name STDOUT:', data.toString());
                output += data.toString();
            });
            
            proc.stderr.on('data', (data) => {
                console.error('Auto-Name STDERR:', data.toString());
                errorOutput += data.toString();
            });
            
            proc.on('close', (code) => {
                console.log('Auto-Name Process Closed with code:', code);
                if (code === 0) {
                    try {
                        // find first brace to ignore any spurious output
                        const jsonStart = output.indexOf('{');
                        if (jsonStart >= 0) {
                            const result = JSON.parse(output.slice(jsonStart));
                            resolve(result);
                        } else {
                            resolve({});
                        }
                    } catch (e) {
                        console.error('JSON parse error from auto-name batch:', output);
                        resolve({});
                    }
                } else {
                    console.error('Auto-name batch process exited with code', code, errorOutput);
                    resolve({});
                }
            });
            
            // Send input JSON
            const inputData = JSON.stringify({
                pdfPath: pdfPath,
                fields: fields
            });
            proc.stdin.write(inputData);
            proc.stdin.end();
            
        } catch (e) {
            console.error('Auto-name batch error:', e);
            resolve({});
        }
    });
});

// ============ Gemini AI Chat Handlers ============
const { GeminiClient } = require('./gemini-client');

// Store for API key (separate from settings)
const geminiStore = new Store({
    name: 'gemini-settings',
    encryptionKey: 'fillpdf-gemini-secure-key'
});

// Initialize Gemini client
let geminiClient = null;

// Check if API key is configured
ipcMain.handle('gemini-has-api-key', async () => {
    return geminiStore.has('apiKey');
});

// Set API key (tests it first)
ipcMain.handle('gemini-set-api-key', async (event, apiKey) => {
    try {
        // Create a temporary client to test the key
        const testClient = new GeminiClient(apiKey);
        
        // Test with a simple message using the dedicated test method
        await testClient.testApiKey();
        
        // If successful, save the key and create the main client
        geminiStore.set('apiKey', apiKey);
        geminiClient = new GeminiClient(apiKey);
        
        return { success: true };
    } catch (error) {
        return { success: false, error: error.message };
    }
});

// Send chat message
ipcMain.handle('gemini-chat', async (event, message, context) => {
    try {
        // Get or create client
        if (!geminiClient) {
            const apiKey = geminiStore.get('apiKey');
            if (!apiKey) {
                return { error: 'No API key configured' };
            }
            geminiClient = new GeminiClient(apiKey);
        }
        
        // Send message with context - the client now handles function calling
        const response = await geminiClient.sendMessage(message, context);
        
        // Response can be { type: 'text', message: '...' } or { type: 'action', action: '...', params: {...}, message: '...' }
        return response;
        
    } catch (error) {
        console.error('Gemini chat error:', error);
        return { error: error.message };
    }
});

// Confirm an AI action was executed
ipcMain.handle('gemini-confirm-action', async (event, action, params, success, resultMessage) => {
    try {
        if (!geminiClient) {
            return null;
        }
        
        const response = await geminiClient.confirmAction(action, params, success, resultMessage);
        return response;
        
    } catch (error) {
        console.error('Gemini confirm action error:', error);
        return null;
    }
});

// Clear API key
ipcMain.handle('gemini-clear-api-key', async () => {
    geminiStore.delete('apiKey');
    geminiClient = null;
    return { success: true };
});

// Sign Out / Deactivate License
ipcMain.handle('sign-out', async () => {
    try {
        // Clear Gemini API Key (do NOT touch app license keys)
        geminiStore.delete('apiKey');
        geminiClient = null;

        return { success: true };
    } catch (e) {
        console.error('Sign out error:', e);
        return { success: false, error: e.message };
    }
});

// Reset chat session
ipcMain.handle('gemini-reset-chat', async () => {
    if (geminiClient) {
        geminiClient.resetChat();
    }
    return { success: true };
});
