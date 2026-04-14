const { ipcRenderer } = require('electron');
const fs = require('fs');

// ============ Global Error Reporting (For Built App Debugging) ============
window.onerror = function(message, source, lineno, colno, error) {
    const errorMsg = `🛑 UI Error: ${message}\nAt: ${source}:${lineno}:${colno}\n\nStack: ${error ? error.stack : 'N/A'}`;
    console.error(errorMsg);
    alert(errorMsg); // Visible feedback for user to report
    return false;
};

window.addEventListener('unhandledrejection', (event) => {
    const errorMsg = `🛑 Promise Rejected: ${event.reason}\n\nStack: ${event.reason ? event.reason.stack : 'N/A'}`;
    console.error(errorMsg);
    alert(errorMsg);
});

// DOM Elements
const inputPathEl = document.getElementById('inputPath');
const outputPathEl = document.getElementById('outputPath');
const browseInputBtn = document.getElementById('browseInput');
const browseOutputBtn = document.getElementById('browseOutput');
const btnBoth = document.getElementById('btnBoth');
const btnFillable = document.getElementById('btnFillable');
const btnTestFill = document.getElementById('btnTestFill');
const progressSection = document.getElementById('progressSection');
const progressBar = document.getElementById('progressBar');
const progressText = document.getElementById('progressText');
const logContainer = document.getElementById('logContainer');
const btnCopyLog = document.getElementById('btnCopyLog');
const btnCancelProcessing = document.getElementById('btnCancelProcessing');
const statusMessage = document.getElementById('statusMessage');

// Mode Elements (unified input — auto-detect file vs folder)
const modeSingleBtn = null; // Removed: unified input auto-detects
const modeBatchBtn = null;  // Removed: unified input auto-detects
const fieldDetectionStatus = document.getElementById('fieldDetectionStatus');
const singleInputGroup = document.getElementById('singleInputGroup');
const batchInputGroup = null; // Removed: unified into singleInputGroup
const inputFolderPathEl = null; // Removed: reuse inputPathEl for folders too
const browseInputFolderBtn = null; // Removed: single Browse button handles both
const batchCountEl = document.getElementById('batchCount');
const inputLabel = document.getElementById('inputLabel');

// Input Row Elements for highlighting
const singleInputRow = document.getElementById('singleInputRow');
const batchInputRow = null; // Removed: unified into singleInputRow
const outputInputRow = document.getElementById('outputInputRow');

let inputPath = '';
let outputPath = '';
let lastOutputPath = ''; // Track last generated fillable PDF for editor
let currentFlow = 'fillable-only'; // 'fillable-only' or 'fillable-test'

// ===== CUSTOM INPUT PROMPT (replaces native prompt) =====
function showInputPrompt(title, defaultValue = '') {
    return new Promise((resolve) => {
        const modal = document.getElementById('inputPromptModal');
        const titleEl = document.getElementById('inputPromptTitle');
        const inputEl = document.getElementById('inputPromptValue');
        const okBtn = document.getElementById('inputPromptOK');
        const cancelBtn = document.getElementById('inputPromptCancel');
        
        titleEl.textContent = title;
        inputEl.value = defaultValue;
        modal.style.display = 'flex';
        inputEl.focus();
        inputEl.select();
        
        const cleanup = () => {
            modal.style.display = 'none';
            okBtn.removeEventListener('click', handleOK);
            cancelBtn.removeEventListener('click', handleCancel);
            inputEl.removeEventListener('keydown', handleKeydown);
        };
        
        const handleOK = () => {
            cleanup();
            resolve(inputEl.value.trim() || null);
        };
        
        const handleCancel = () => {
            cleanup();
            resolve(null);
        };
        
        const handleKeydown = (e) => {
            if (e.key === 'Enter') handleOK();
            if (e.key === 'Escape') handleCancel();
        };
        
        okBtn.addEventListener('click', handleOK);
        cancelBtn.addEventListener('click', handleCancel);
        inputEl.addEventListener('keydown', handleKeydown);
    });
}

// Batch State
let currentMode = 'single'; // 'single' or 'batch'
let inputFolderPath = '';
let batchFiles = [];
let batchCategory = 'none'; // 'all-blank', 'all-fillable', 'mixed', 'none'

// Edit Fields button
const btnEditFields = document.getElementById('btnEditFields');

// Update button states
function updateButtons() {
    let hasInput = false;
    
    if (currentMode === 'single') {
        hasInput = inputPath.length > 0;
    } else {
        hasInput = batchFiles.length > 0;
    }
    
    // Update input label and status displays based on mode
    if (currentMode === 'batch') {
        if (inputLabel) inputLabel.textContent = 'Input Folder';
        if (fieldDetectionStatus) fieldDetectionStatus.style.display = 'none';
        if (batchCountEl) batchCountEl.style.display = 'block';
    } else {
        if (inputLabel) inputLabel.textContent = 'Input PDF or Batch Folder';
        if (batchCountEl) batchCountEl.style.display = 'none';
        // fieldDetectionStatus visibility managed by scanForExistingFields
    }

    if (currentMode === 'batch') {
        // Batch Mode Logic
        btnBoth.style.display = 'block';
        btnFillable.style.display = 'block';
        btnTestFill.style.display = 'block';
        // Hide Edit Fields in batch mode (can't edit multiple PDFs)
        if (btnEditFields) btnEditFields.style.display = 'none';

        if (batchCategory === 'all-blank') {
            btnBoth.disabled = !hasInput;
            btnFillable.disabled = !hasInput;
            btnTestFill.disabled = true;
        } else if (batchCategory === 'all-fillable') {
            btnBoth.disabled = !hasInput;
            btnFillable.disabled = true;
            btnTestFill.disabled = !hasInput;
        } else {
            // Mixed or None
            btnBoth.disabled = true;
            btnFillable.disabled = true;
            btnTestFill.disabled = true;
        }
    } else {
        // Single PDF mode — show Edit Fields button
        if (btnEditFields) btnEditFields.style.display = 'block';

        if (isPrefillMode) {
            // PDF has existing fields — show Test Fill + Edit Fields only
            btnBoth.style.display = 'none';
            btnFillable.style.display = 'none';
            btnTestFill.style.display = 'block';
            
            btnTestFill.disabled = !hasInput;
        } else {
            // Single PDF mode (no existing fields) — show Make Fillable buttons
            btnBoth.style.display = 'block';
            btnFillable.style.display = 'block';
            btnTestFill.style.display = 'none';
            
            btnBoth.disabled = !hasInput;
            btnFillable.disabled = !hasInput;
        }
    }

    // Update Input Row Highlights
    if (singleInputRow) {
        if ((currentMode === 'single' && inputPath && inputPath.length > 0) ||
            (currentMode === 'batch' && inputFolderPath && inputFolderPath.length > 0)) {
            singleInputRow.classList.add('drag-over');
        } else {
            singleInputRow.classList.remove('drag-over');
        }
    }

    if (outputInputRow) {
        if (outputPath && outputPath.length > 0) {
            outputInputRow.classList.add('drag-over');
        } else {
            outputInputRow.classList.remove('drag-over');
        }
    }
}

// Mode Switching
const config = require('./config');

// Hide PRO badge for Standard version
if (!config.isPro) {
    const proBadge = document.getElementById('headerProBadge');
    if (proBadge) proBadge.style.display = 'none';
    
    // Disable Auto-improve field names for Standard version
    const autoImproveRow = document.getElementById('autoImproveRow');
    const autoImproveProBadge = document.getElementById('autoImproveProBadge');
    const autoImproveToggle = document.getElementById('setting_auto_improve_names');
    
    if (autoImproveRow) {
        autoImproveRow.classList.add('disabled', 'pro-restricted');
    }
    if (autoImproveProBadge) {
        autoImproveProBadge.style.display = 'inline';
    }
    if (autoImproveToggle) {
        autoImproveToggle.classList.remove('active');
        autoImproveToggle.style.pointerEvents = 'none';
    }
}

// Auto-detect prefill mode (set automatically by scanForExistingFields)
let isPrefillMode = false;
let prefillFieldData = null; // Store scanned fields

// ===== DEMO TRIAL SYSTEM =====
if (config.isDemo) {
    const TRIAL_START_KEY = 'fillthatpdf_trial_start';
    const TRIAL_DAYS = config.demo.trialDays || 7;
    
    // Get or set trial start date
    let trialStart = localStorage.getItem(TRIAL_START_KEY);
    if (!trialStart) {
        trialStart = new Date().toISOString();
        localStorage.setItem(TRIAL_START_KEY, trialStart);
    }
    
    // Calculate days remaining
    const startDate = new Date(trialStart);
    const now = new Date();
    const daysPassed = Math.floor((now - startDate) / (1000 * 60 * 60 * 24));
    const daysRemaining = Math.max(0, TRIAL_DAYS - daysPassed);
    
    if (daysRemaining <= 0) {
        // Trial expired - show blocking popup
        alert(`⏰ Your 7-Day Free Trial Has Expired

Thank you for trying Fill That PDF! PRO.

Your trial period has ended. To continue using all PRO features including:
• Visual Field Editor
• Batch Processing
• AI Assistant & more

Please upgrade to the full version at:
fillthatpdf.com

Click OK to close the application.`);
        
        // Close the app
        const { ipcRenderer } = require('electron');
        ipcRenderer.send('close-app');
    } else {
        // Show daily reminder with days remaining
        const dayWord = daysRemaining === 1 ? 'day' : 'days';
        const urgency = daysRemaining <= 2 ? '⚠️' : '👋';
        
        alert(`${urgency} Fill That PDF! PRO - Free Trial

You have ${daysRemaining} ${dayWord} remaining in your free trial.

Enjoy unlimited access to all PRO features:
✓ Visual Field Editor
✓ Batch Processing  
✓ AI Assistant
✓ Smart Detection

Upgrade anytime at fillthatpdf.com to keep access forever!`);
    }
}

// Copy Log button
if (btnCopyLog) {
    btnCopyLog.addEventListener('click', () => {
        const logText = logContainer.innerText || logContainer.textContent || '';
        if (logText.trim()) {
            navigator.clipboard.writeText(logText).then(() => {
                const orig = btnCopyLog.textContent;
                btnCopyLog.textContent = 'Copied!';
                setTimeout(() => { btnCopyLog.textContent = orig; }, 1500);
            }).catch(() => {
                // Fallback for older Electron
                const ta = document.createElement('textarea');
                ta.value = logText;
                document.body.appendChild(ta);
                ta.select();
                document.execCommand('copy');
                document.body.removeChild(ta);
                const orig = btnCopyLog.textContent;
                btnCopyLog.textContent = 'Copied!';
                setTimeout(() => { btnCopyLog.textContent = orig; }, 1500);
            });
        }
    });
}

if (modeSingleBtn && modeBatchBtn) {
    // Mode tabs removed — auto-detect from input selection
}

function setMode(mode) {
    currentMode = mode === 'batch' ? 'batch' : 'single';
    
    // Clear prefill state when switching to batch
    if (mode === 'batch') {
        isPrefillMode = false;
        prefillFieldData = null;
        const editFieldsBtn = document.getElementById('btnEditFields');
        if (editFieldsBtn) {
            editFieldsBtn.classList.remove('prefill-ready', 'pulse-ready');
        }
        // Hide field detection status
        if (fieldDetectionStatus) fieldDetectionStatus.style.display = 'none';
    }
    
    // Reset batch state when switching to single
    if (mode !== 'batch') {
        batchCategory = 'none';
        batchFiles = [];
        inputFolderPath = '';
    } else {
        // Reset single state when switching to batch
        inputPath = '';
        isPrefillMode = false;
        prefillFieldData = null;
    }
    
    updateButtons();
}

// Scan PDF for existing form fields (auto-detect on every PDF selection)
async function scanForExistingFields(pdfPath) {
    if (!pdfPath) return;

    // Reset state before scanning
    isPrefillMode = false;
    prefillFieldData = null;
    const editFieldsBtn = document.getElementById('btnEditFields');
    const scanProgress = document.getElementById('fieldScanProgress');
    const scanText = document.getElementById('fieldScanText');
    if (editFieldsBtn) {
        editFieldsBtn.disabled = true;
        editFieldsBtn.classList.add('btn-disabled-look');
        editFieldsBtn.classList.remove('prefill-ready', 'pulse-ready');
        editFieldsBtn.style.pointerEvents = 'none';
        editFieldsBtn.style.opacity = '0.5';
    }

    // Update field detection status indicator
    if (fieldDetectionStatus) {
        fieldDetectionStatus.style.display = 'block';
        fieldDetectionStatus.textContent = '🔍 Scanning for existing fields...';
        fieldDetectionStatus.style.color = 'var(--text-secondary)';
    }

    // Show loading progress indicator with steady fill
    const scanBar = document.getElementById('fieldScanBar');
    let scanTimer = null;
    if (scanProgress) {
        scanProgress.style.display = 'block';
        if (scanText) scanText.textContent = 'Scanning for fields...';
        if (scanBar) {
            scanBar.style.width = '0%';
            let progress = 0;
            scanTimer = setInterval(() => {
                // Ease toward 90% — slows down as it approaches
                progress += (90 - progress) * 0.08;
                scanBar.style.width = Math.min(progress, 90) + '%';
            }, 150);
        }
    }

    try {
        addLog('🔍 Scanning PDF for existing form fields...');
        const result = await ipcRenderer.invoke('extract-fields', pdfPath);

        // Complete the bar to 100%
        if (scanTimer) clearInterval(scanTimer);
        if (scanBar) scanBar.style.width = '100%';

        if (result && result.fields && result.fields.length > 0) {
            prefillFieldData = result.fields;  // Keep ALL fields (including links) for the editor

            // Count only real form fields (not hyperlinks) for auto-detect
            const formFields = result.fields.filter(f => f.type !== 'link');
            const linkCount = result.fields.length - formFields.length;

            if (formFields.length > 0) {
                isPrefillMode = true;  // Auto-switch to prefill mode
                const editNote = config.isPro ? ' You can click "Edit Fields" to modify them.' : ' You can run Test Fill on this PDF.';
                addLog(`✅ Found ${formFields.length} existing field(s)!${linkCount > 0 ? ` (plus ${linkCount} hyperlink(s))` : ''}${editNote}`);
                if (scanText) scanText.textContent = `Found ${formFields.length} field(s)!`;

                // Update field detection status
                if (fieldDetectionStatus) {
                    fieldDetectionStatus.textContent = `✅ ${formFields.length} existing field(s) detected — Test Fill mode`;
                    fieldDetectionStatus.style.color = '#64ffda';
                }

                // Enable Edit Fields button (PRO only)
                if (editFieldsBtn && config.isPro) {
                    editFieldsBtn.disabled = false;
                    editFieldsBtn.classList.remove('btn-disabled-look');
                    editFieldsBtn.classList.add('prefill-ready', 'pulse-ready');
                    editFieldsBtn.style.pointerEvents = 'auto';
                    editFieldsBtn.style.opacity = '1';
                    addLog('   > Edit Fields button enabled');
                }
            } else {
                // Only hyperlinks found, no real form fields — stay in Make Fillable mode
                isPrefillMode = false;
                addLog(`ℹ️ Found ${linkCount} hyperlink(s) only — no form fields. Showing Make Fillable options.`);
                if (scanText) scanText.textContent = 'No form fields found';
                if (fieldDetectionStatus) {
                    fieldDetectionStatus.textContent = '○ No existing fields — Make Fillable mode';
                    fieldDetectionStatus.style.color = 'var(--text-secondary)';
                }
                // Still enable Edit Fields for PRO so user can edit the links
                if (editFieldsBtn && config.isPro) {
                    editFieldsBtn.disabled = false;
                    editFieldsBtn.classList.remove('btn-disabled-look');
                    editFieldsBtn.classList.add('prefill-ready');
                    editFieldsBtn.style.pointerEvents = 'auto';
                    editFieldsBtn.style.opacity = '1';
                }
            }
        } else if (result && result.error) {
            addLog(`❌ Error: ${result.error}`);
            if (scanText) scanText.textContent = 'Error scanning PDF';
            isPrefillMode = false;
            prefillFieldData = null;
            if (fieldDetectionStatus) {
                fieldDetectionStatus.textContent = '○ No existing fields — Make Fillable mode';
                fieldDetectionStatus.style.color = 'var(--text-secondary)';
            }
        } else {
            addLog('ℹ️ No existing form fields found — showing Make Fillable options.');
            if (scanText) scanText.textContent = 'No fields found';
            isPrefillMode = false;
            prefillFieldData = null;
            if (fieldDetectionStatus) {
                fieldDetectionStatus.textContent = '○ No existing fields — Make Fillable mode';
                fieldDetectionStatus.style.color = 'var(--text-secondary)';
            }
        }
    } catch (error) {
        if (scanTimer) clearInterval(scanTimer);
        console.error('[Prefill] Error scanning for fields:', error);
        addLog(`❌ Error scanning PDF: ${error.message}`);
        if (scanText) scanText.textContent = 'Error scanning PDF';
        isPrefillMode = false;
        prefillFieldData = null;
        if (fieldDetectionStatus) {
            fieldDetectionStatus.textContent = '○ No existing fields — Make Fillable mode';
            fieldDetectionStatus.style.color = 'var(--text-secondary)';
        }
    }

    // Hide progress after a brief moment so user sees the completed bar
    setTimeout(() => {
        if (scanProgress) scanProgress.style.display = 'none';
        if (scanBar) scanBar.style.width = '0%';
    }, 2000);

    updateButtons();
}

// Add log entry
function addLog(text) {
    const lines = text.split('\n').filter(line => line.trim());
    lines.forEach(line => {
        logContainer.innerHTML += line + '<br>';
    });
    // Use requestAnimationFrame for more reliable scrolling
    requestAnimationFrame(() => {
        logContainer.scrollTop = logContainer.scrollHeight;
    });
}

// Clear log
function clearLog() {
    logContainer.innerHTML = '';
}



// Helper to set selected button (for visual feedback)
function setSelectedButton(selectedBtn) {
    // Remove 'selected' from all main action buttons
    [btnBoth, btnFillable, btnTestFill].forEach(btn => {
        btn.classList.remove('selected');
    });
    // Add 'selected' to the clicked button
    if (selectedBtn) {
        selectedBtn.classList.add('selected');
    }
}

// Show progress section
function showProgress(show = true) {
    progressSection.classList.toggle('active', show);
    if (show) {
        clearLog();
        progressBar.style.width = '0%';
        statusMessage.className = 'status';
        statusMessage.style.display = 'none';
        progressText.style.color = ''; // Reset color
        progressBar.style.backgroundColor = ''; // Reset success color
        detectedPageCount = 0; // Reset page count for new operation
    }

}

// Set progress
function setProgress(percent, text) {
    progressBar.style.width = `${percent}%`;
    if (text) progressText.textContent = text;
}

// Show status message
function showStatus(type, message) {
    if (type === 'success') {
        // For success, update the progress text instead of showing a bubble
        progressText.textContent = message;
        progressText.style.color = 'var(--status-success-text, #64ffda)';
        statusMessage.style.display = 'none';
        
        // Add visual emphasis to progress bar
        progressBar.style.backgroundColor = 'var(--status-success-text, #64ffda)';
    } else {
        // For errors, show the bubble
        statusMessage.className = `status ${type}`;
        statusMessage.textContent = message;
        statusMessage.style.display = 'block';
        
        // Reset progress bar color if needed
        progressBar.style.backgroundColor = '';
    }
}

// Disable all buttons during processing
function setProcessing(isProcessing) {
    btnBoth.disabled = isProcessing;
    btnFillable.disabled = isProcessing;
    btnTestFill.disabled = isProcessing;
    browseInputBtn.disabled = isProcessing;
    browseOutputBtn.disabled = isProcessing;
    if (browseInputFolderBtn) browseInputFolderBtn.disabled = isProcessing;
    if (modeSingleBtn) modeSingleBtn.disabled = isProcessing;
    if (modeBatchBtn) modeBatchBtn.disabled = isProcessing;

    // Show/hide cancel button
    if (btnCancelProcessing) {
        btnCancelProcessing.style.display = isProcessing ? 'inline-block' : 'none';
    }

    if (!isProcessing) {
        if (modeSingleBtn) modeSingleBtn.disabled = false;
        if (modeBatchBtn) modeBatchBtn.disabled = false;
        updateButtons();
    }
}

// Cancel processing button
if (btnCancelProcessing) {
    btnCancelProcessing.addEventListener('click', async () => {
        btnCancelProcessing.disabled = true;
        btnCancelProcessing.textContent = 'Cancelling...';
        addLog('\n⏹️ Cancelling operation...\n');
        try {
            await ipcRenderer.invoke('cancel-processing');
        } catch (e) {
            // Process may already be dead
        }
        // The catch block in the processing flow will handle cleanup
        // Reset cancel button state for next use
        setTimeout(() => {
            btnCancelProcessing.disabled = false;
            btnCancelProcessing.textContent = '✕ Cancel';
        }, 1000);
    });
}

// Helper to process a selected folder (used by browse button and drag & drop)
async function processSelectedFolder(folderPath) {
    inputFolderPath = folderPath;
    inputPathEl.value = folderPath;
    
    // Scan for PDFs
    try {
        batchFiles = await ipcRenderer.invoke('scan-folder-for-pdfs', folderPath);
        
        if (batchFiles.length === 0) {
            if (batchCountEl) batchCountEl.textContent = "0 PDFs found";
            batchCategory = 'none';
            showStatus('error', 'No PDF files found in the selected folder.');
            updateButtons();
            return;
        }

        addLog(`🔍 Analyzing ${batchFiles.length} PDFs in folder...`);
        batchCategory = await ipcRenderer.invoke('analyze-folder-content', batchFiles);
        
        if (batchCategory === 'mixed') {
            alert('⚠️ Mixed Content Detected\n\nThis folder contains both blank PDFs and PDFs that already have form fields.\n\nTo process these files, please organize them into separate folders of "all blank" or "all fillable" PDFs.');
            batchFiles = [];
            inputFolderPath = '';
            inputPathEl.value = '';
            if (batchCountEl) batchCountEl.textContent = '0 PDFs found';
            batchCategory = 'none';
            showStatus('error', 'Mixed folder content - please organize files.');
        } else {
            if (batchCountEl) batchCountEl.textContent = `${batchFiles.length} PDFs found (${batchCategory.replace('all-', '')})`;
            statusMessage.style.display = 'none';
        }
        
        updateButtons();
    } catch (error) {
        showStatus('error', `Error scanning folder: ${error.message}`);
        batchFiles = [];
        batchCategory = 'none';
        updateButtons();
    }
}

// Browse for input file or folder (Unified — auto-detect)
browseInputBtn.addEventListener('click', async () => {
    const result = await ipcRenderer.invoke('select-input-file-or-folder');
    if (result) {
        await handleSelectedInput(result);
    }
});

// Auto-detect whether selection is a file or folder and switch mode
async function handleSelectedInput(selectedPath) {
    try {
        const stats = fs.lstatSync(selectedPath);
        
        if (stats.isDirectory()) {
            // Folder selected — check Pro feature
            if (!config.features.bulkProcessing) {
                alert('🔒 Batch Processing - PRO Feature\n\nBatch (folder) processing is available in Fill That PDF! PRO.\n\nTo process multiple PDFs at once:\n• Upgrade to Fill That PDF! PRO\n• Get unlimited batch processing\n• Plus Visual Field Editor & more!\n\nVisit fillthatpdf.com to upgrade.');
                return;
            }
            setMode('batch');
            inputPathEl.value = selectedPath;
            await processSelectedFolder(selectedPath);
        } else if (selectedPath.toLowerCase().endsWith('.pdf')) {
            // PDF file selected
            setMode('single');
            inputPath = selectedPath;
            inputPathEl.value = selectedPath;
            updateButtons();
            scanForExistingFields(selectedPath);
        }
    } catch (error) {
        console.error('Error detecting input type:', error);
    }
}

// Browse for input folder — kept for backwards compatibility
if (browseInputFolderBtn) {
    browseInputFolderBtn.addEventListener('click', async () => {
        const result = await ipcRenderer.invoke('select-input-folder');
        if (result) {
            await handleSelectedInput(result);
        }
    });
}


// Browse for output folder
browseOutputBtn.addEventListener('click', async () => {
    const result = await ipcRenderer.invoke('select-output-folder');
    if (result) {
        outputPath = result;
        outputPathEl.value = result;
        updateButtons(); // Refresh highlights
    }
});

// ===== DRAG AND DROP HANDLERS =====
// Handle drag-and-drop highlights for all input fields
function setupDragDrop() {
    const rows = [
        { row: singleInputRow, input: inputPathEl },
        { row: outputInputRow, input: outputPathEl }
    ];
    
    // Prevent default drag behaviors on the document
    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
        document.addEventListener(eventName, (e) => {
            e.preventDefault();
            e.stopPropagation();
        }, false);
    });
    
    rows.forEach(item => {
        if (!item.row) return;
        
        // Highlight when dragging over the input row
        ['dragenter', 'dragover'].forEach(eventName => {
            item.row.addEventListener(eventName, () => {
                item.row.classList.add('drag-over');
            }, false);
        });
        
        ['dragleave', 'drop'].forEach(eventName => {
            item.row.addEventListener(eventName, () => {
                // If it has content, updateButtons will maintain the class
                if (item.row === singleInputRow && !inputPath && !inputFolderPath) {
                    item.row.classList.remove('drag-over');
                } else if (item.row === outputInputRow && !outputPath) {
                    item.row.classList.remove('drag-over');
                } else if (!inputPath && !inputFolderPath && !outputPath) {
                    item.row.classList.remove('drag-over');
                }
                
                // Trigger a refresh after a short delay for smooth transitions
                setTimeout(updateButtons, 50);
            }, false);
        });
    });
}

// Initialize drag-drop on load
setupDragDrop();

// Listen for progress updates from main process
let detectedPageCount = 0; // Track total pages for progress display

ipcRenderer.on('progress-update', (event, text) => {
    addLog(text);

    // v41.3: Split by newlines and process each line to ensure the LATEST status is reflected
    // This prevents the UI from getting "stuck" on an early PASS if multiple lines arrive in one chunk.
    const lines = text.split('\n');
    lines.forEach(line => {
        if (!line.trim()) return;

        // Parse page count from Python output
        const pageCountMatch = line.match(/structural data for (\d+) page/);
        if (pageCountMatch) {
            detectedPageCount = parseInt(pageCountMatch[1]);
        }

        // Parse per-page progress during auto-calibration
        const pageAnalysisMatch = line.match(/Analyzing page (\d+)/);
        if (pageAnalysisMatch && detectedPageCount > 0) {
            const currentPage = parseInt(pageAnalysisMatch[1]);
            // Auto-calibration is roughly the first 5% of progress
            const pct = Math.round((currentPage / detectedPageCount) * 5);
            const pageInfo = detectedPageCount > 1 ? ` (page ${currentPage}/${detectedPageCount})` : '';
            setProgress(pct, `Analyzing PDF structure${pageInfo}...`);
            return;
        }

        // Granular PASS progress tracking
        // Detection phase (PASS 0 through 6H) = 5% to 65%
        // Generation phase (PASS 7 through 12) = 65% to 98%
        const pageInfo = detectedPageCount > 1 ? ` (${detectedPageCount} pages)` : '';

        if (line.includes('PASS 0.7')) setProgress(9, `Pre-detecting image regions${pageInfo}...`);
        else if (line.includes('PASS 0.5')) setProgress(8, `Pre-detecting box entry areas${pageInfo}...`);
        else if (line.includes('PASS 0.3')) setProgress(7, `Pre-detecting tally sheets${pageInfo}...`);
        else if (line.includes('PASS 0')) setProgress(6, `Detecting text-only pages${pageInfo}...`);
        else if (line.includes('PASS 1.5')) setProgress(14, `Detecting horizontal-line tables${pageInfo}...`);
        else if (line.includes('PASS 1:') || line.includes('PASS 1 ')) setProgress(10, `Detecting checkboxes${pageInfo}...`);
        else if (line.includes('PASS 2.5')) setProgress(22, `Reconstructing table columns${pageInfo}...`);
        else if (line.includes('PASS 2:') || line.includes('PASS 2 ')) setProgress(18, `Detecting table cells${pageInfo}...`);
        else if (line.includes('PASS 3D')) setProgress(32, `Detecting colon-label fields${pageInfo}...`);
        else if (line.includes('PASS 3C')) setProgress(31, `Detecting underscore patterns${pageInfo}...`);
        else if (line.includes('PASS 3B')) setProgress(30, `Detecting inline labels${pageInfo}...`);
        else if (line.includes('PASS 3A')) setProgress(29, `Detecting special labels${pageInfo}...`);
        else if (line.includes('PASS 3:') || line.includes('PASS 3 ')) setProgress(26, `Detecting signature fields${pageInfo}...`);
        else if (line.includes('PASS 4E')) setProgress(48, `Enriching checkbox labels${pageInfo}...`);
        else if (line.includes('PASS 4C3')) setProgress(47, `Removing pre-filled columns${pageInfo}...`);
        else if (line.includes('PASS 4C2')) setProgress(46, `Detecting colon-label cells${pageInfo}...`);
        else if (line.includes('PASS 4C1')) setProgress(45, `Cleaning up headers${pageInfo}...`);
        else if (line.includes('PASS 4C0')) setProgress(44, `Adjusting header bars${pageInfo}...`);
        else if (line.includes('PASS 4C')) setProgress(43, `Cleaning field boundaries${pageInfo}...`);
        else if (line.includes('PASS 4B')) setProgress(40, `Detecting form lines${pageInfo}...`);
        else if (line.includes('PASS 4A')) setProgress(38, `Detecting image rectangles${pageInfo}...`);
        else if (line.includes('PASS 4F')) setProgress(49, `Enhanced detection${pageInfo}...`);
        else if (line.includes('PASS 4:') || line.includes('PASS 4 ')) setProgress(36, `Detecting empty boxes${pageInfo}...`);
        else if (line.includes('PASS 5C')) setProgress(57, `Splitting fields on separators${pageInfo}...`);
        else if (line.includes('PASS 5B')) setProgress(56, `Assigning checkbox names${pageInfo}...`);
        else if (line.includes('PASS 5A')) setProgress(55, `Refining checkbox labels${pageInfo}...`);
        else if (line.includes('PASS 5:') || line.includes('PASS 5 ')) setProgress(52, `Identifying radio buttons${pageInfo}...`);
        else if (line.includes('PASS 6H')) setProgress(65, `Filling labeled-cell gaps${pageInfo}...`);
        else if (line.includes('PASS 6G')) setProgress(64, `Global audit & self-correction${pageInfo}...`);
        else if (line.includes('PASS 6D')) setProgress(63, `Standardizing field heights${pageInfo}...`);
        else if (line.includes('PASS 6C')) setProgress(62, `Extending to form lines${pageInfo}...`);
        else if (line.includes('PASS 6A0')) setProgress(60, `Enforcing table consistency${pageInfo}...`);
        else if (line.includes('PASS 6A')) setProgress(61, `Overlap cleanup${pageInfo}...`);
        else if (line.includes('PASS 6:') || line.includes('PASS 6 ')) setProgress(58, `Resolving field conflicts${pageInfo}...`);
        else if (line.includes('PASS 7')) setProgress(68, 'Creating fillable PDF...');
        else if (line.includes('PASS 8')) setProgress(75, 'Fixing font encoding...');
        else if (line.includes('PASS 9')) setProgress(80, 'Creating radio groups & overlays...');
        else if (line.includes('PASS 10')) setProgress(88, 'Adding JavaScript formatting...');
        else if (line.includes('PASS 12')) setProgress(95, 'Final AcroForm settings...');
        else if (line.includes('Done!')) setProgress(100, 'Complete!');
        // Per-page garbage cleanup or auto-rename sub-steps
        else if (line.includes('Cleaning up garbage fields')) setProgress(96, 'Cleaning up garbage fields...');
        else if (line.includes('Auto-improving field names')) setProgress(97, 'Improving field names...');
    });
});

// Listen for fill progress
ipcRenderer.on('fill-progress', (event, percent) => {
    setProgress(percent, `Filling fields... ${percent}%`);
});

// Helper for Batch Processing
async function runBatchProcessing(actionType) {
    if (batchFiles.length === 0) return;
    
    // Confirm execution
    const actionName = actionType === 'fillable' ? 'Make Fillable' : 
                       actionType === 'test' ? 'Test Fill' : 'Make Fillable + Test';
    
    if (!confirm(`Ready to ${actionName} for ${batchFiles.length} files?`)) return;
    
    setProcessing(true);
    showProgress(true);
    addLog(`🚀 Starting Batch: ${actionName} (${batchFiles.length} files)\n`);
    
    let successCount = 0;
    let failCount = 0;
    
    // Create specific output subfolder for batch to keep things clean?
    // Optional, for now just use selected output path or source folder
    
    for (let i = 0; i < batchFiles.length; i++) {
        const file = batchFiles[i];
        const fileName = file.split(/[/\\]/).pop();
        const percent = Math.round((i / batchFiles.length) * 100);
        
        setProgress(percent, `Processing ${i+1}/${batchFiles.length}: ${fileName}`);
        addLog(`\n[${i+1}/${batchFiles.length}] Processing: ${fileName}...`);
        
        try {
            if (actionType === 'fillable' || actionType === 'both') {
                // Determine output path for this file
                // If output folder is set, all go there. If not, side-by-side.
                
                // For batch, we skip inspection/validation modal to run uninterrupted
                // So we use 'run-make-fillable' directly
                const result = await ipcRenderer.invoke('run-make-fillable', file, outputPath);
                
                if (actionType === 'both') {
                    addLog(`   > PDF generated. Test filling...`);
                    await ipcRenderer.invoke('run-test-fill', result.outputPath, outputPath);
                }
                
                addLog(`   ✅ Success`);
                successCount++;
                
                // Update stats after each success
                updateStatsDashboard();
                
            } else if (actionType === 'test') {
                // Test Fill Only
                await ipcRenderer.invoke('run-test-fill', file, outputPath);
                addLog(`   ✅ Success`);
                successCount++;
            }
        } catch (error) {
            if (error.message === 'CANCELLED') {
                addLog('\n⏹️ Batch cancelled by user.\n');
                showStatus('error', 'Batch processing cancelled');
                setProgress(0, 'Cancelled');
                setProcessing(false);
                return;
            }
            console.error(`Error processing ${fileName}:`, error);
            addLog(`   ❌ Failed: ${error.message}`);
            failCount++;
        }
    }

    setProgress(100, 'Batch Complete!');
    updateStatsDashboard();
    
    const resultMsg = `Batch Complete! Success: ${successCount}, Failed: ${failCount}`;
    addLog(`\n🏁 ${resultMsg}`);
    
    if (failCount === 0) {
        showStatus('success', `✅ ${resultMsg}`);
    } else if (successCount > 0) {
        showStatus('success', `⚠️ ${resultMsg} (Check logs for details)`);
    } else {
        showStatus('error', `❌ ${resultMsg}`);
    }
    
    setProcessing(false);
}

// Button: Make Fillable Only
btnFillable.addEventListener('click', async () => {
    if (currentMode === 'batch') {
        await runBatchProcessing('fillable');
        return;
    }

    if (!inputPath) return;
    
    currentFlow = 'fillable-only';
    setSelectedButton(btnFillable);
    setProcessing(true);
    showProgress(true);
    setProgress(0, 'Starting analysis...');
    addLog('🚀 Starting: PDF Analysis & Generation\n');
    
    try {
        // Run staged inspection
        const result = await ipcRenderer.invoke('run-inspection', inputPath, outputPath);
        
        if (result.issues && result.issues.length > 0) {
            // Auto-fix all fixable issues without showing dialog
            const fixableIssues = result.issues.filter(i => i.fixable);
            const fixIds = fixableIssues.map(i => i.id);
            
            if (fixIds.length > 0) {
                addLog(`\n🔧 Auto-fixing ${fixIds.length} issues...`);
                setProgress(50, 'Applying fixes & generating PDF...');
                
                const fixResult = await ipcRenderer.invoke('apply-inspection-fixes', inputPath, outputPath, fixIds);
                
                setProgress(100, 'Complete!');
                showStatus('success', `✅ Success! Auto-fixed ${fixIds.length} issues. Saved to: ${fixResult.outputPath}`);
                showEditFieldsButton(fixResult.outputPath, true);
                updateStatsDashboard();
            } else {
                // No fixable issues - just generate PDF normally
                addLog('\n✅ Processing complete. Generating PDF...');
                setProgress(50, 'Generating PDF...');
                
                const genResult = await ipcRenderer.invoke('run-make-fillable', inputPath, outputPath);
                
                setProgress(100, 'Complete!');
                showStatus('success', `✅ Success! Saved to: ${genResult.outputPath}`);
                showEditFieldsButton(genResult.outputPath, true);
                updateStatsDashboard();
            }
        } else {
            // No issues - proceed to generate PDF
            addLog('\n✅ Analysis complete! Generating PDF...');
            setProgress(50, 'Generating PDF...');
            
            const genResult = await ipcRenderer.invoke('run-make-fillable', inputPath, outputPath);
            
            setProgress(100, 'Complete!');
            showStatus('success', `✅ Success! Saved to: ${genResult.outputPath}`);
            showEditFieldsButton(genResult.outputPath, true);
            updateStatsDashboard();
        }
        
    } catch (error) {
        if (error.message === 'CANCELLED') {
            addLog('\n⏹️ Operation cancelled by user.\n');
            showStatus('error', 'Operation cancelled');
            setProgress(0, 'Cancelled');
        } else {
            showStatus('error', `❌ Error: ${error.message}`);
            await ipcRenderer.invoke('show-error', 'Processing Error', error.message);
        }
    } finally {
        setProcessing(false);
    }
});

// Inspection Progress Events
ipcRenderer.on('inspection-stage-start', (event, data) => {
    // Only update progress text in single mode or detailed view, logic handled by setProgress
    if (currentMode === 'single') {
        setProgress(data.progress, `Analyzing: ${data.name}...`);
    }
});

ipcRenderer.on('inspection-stage-complete', (event, data) => {
    if (currentMode === 'single' && data.issue_count > 0) {
        addLog(`   • Found ${data.issue_count} issues in ${data.name}`);
    }
});

ipcRenderer.on('inspection-complete', (event, data) => {
    if (currentMode === 'single') {
        setProgress(100, 'Analysis Complete');
    }
});

// Validation Modal Logic
const modal = document.getElementById('validationModal');
const validationList = document.getElementById('validationList');
const btnFixSelected = document.getElementById('btnFixSelected');
const btnIgnoreAll = document.getElementById('btnIgnoreAll');
const btnCancelValidation = document.getElementById('btnCancelValidation');
const btnSelectAll = document.getElementById('btnSelectAll');
const btnSelectNone = document.getElementById('btnSelectNone');
const fixCountText = document.getElementById('fixCountText');

let currentIssues = [];

function showValidationModal(issues) {
    currentIssues = issues;
    modal.style.display = 'flex';
    renderValidationIssues(issues);
    updateFixCount();
}

function hideValidationModal() {
    modal.style.display = 'none';
}

function renderValidationIssues(issues) {
    validationList.innerHTML = '';
    
    // Group by stage/category for better readability
    const stageNames = {
        1: "Size & Dimensions",
        2: "Cell Coverage",
        3: "Radio Buttons",
        4: "Field Overlaps",
        5: "Naming Conventions"
    };
    
    // Sort issues by page then type
    issues.sort((a, b) => (a.page - b.page) || a.type.localeCompare(b.type));
    
    issues.forEach(issue => {
        const item = document.createElement('div');
        item.style.marginBottom = '10px';
        item.style.padding = '12px';
        item.style.background = '#0a192f';
        item.style.border = '1px solid #233554';
        item.style.borderRadius = '8px';
        item.style.display = 'flex';
        item.style.gap = '12px';
        
        const isFixable = issue.fixable;
        const color = issue.severity === 'error' ? '#ff6b6b' : '#64ffda';
        const icon = issue.severity === 'error' ? '🚫' : '⚠️';
        
        let checkboxHtml = '';
        if (isFixable) {
            checkboxHtml = `<input type="checkbox" class="issue-checkbox" data-id="${issue.id}" checked style="margin-top: 4px; cursor: pointer;">`;
        } else {
            checkboxHtml = `<span style="display:inline-block; width:13px;"></span>`;
        }
        
        const actionText = issue.fix_action === 'expand_width' ? 'Auto-fix: Expand width' :
                           issue.fix_action === 'expand_height' ? 'Auto-fix: Expand height' :
                           issue.fix_action === 'expand_to_cell' ? 'Auto-fix: Expand to cell end' :
                           issue.fix_action === 'remove_smaller' ? 'Auto-fix: Remove smaller overlap' :
                           issue.fix_action === 'rename_unique' ? 'Auto-fix: Rename uniquely' : 
                           'Manual review required';
                           
        item.innerHTML = `
            ${checkboxHtml}
            <div style="flex: 1;">
                <div style="display:flex; justify-content:space-between; margin-bottom:4px;">
                    <span style="color: ${color}; font-weight:600; font-size:13px;">${icon} Page ${issue.page}: ${issue.type.replace(/_/g, ' ')}</span>
                    ${isFixable ? '<span style="color:#64ffda; font-size:11px; background:rgba(100,255,218,0.1); padding:2px 6px; border-radius:4px;">Fixable</span>' : ''}
                </div>
                <div style="color:#ccd6f6; font-size:14px; margin-bottom:4px;">${issue.message}</div>
                <div style="color:#8892b0; font-size:12px;">👉 ${actionText}</div>
            </div>
        `;
        
        validationList.appendChild(item);
    });
    
    // Add listeners to checkboxes
    document.querySelectorAll('.issue-checkbox').forEach(cb => {
        cb.addEventListener('change', updateFixCount);
    });
}

function updateFixCount() {
    const checked = document.querySelectorAll('.issue-checkbox:checked').length;
    fixCountText.textContent = `${checked} issue${checked !== 1 ? 's' : ''} selected`;
    btnFixSelected.textContent = checked > 0 ? `Fix ${checked} Issue${checked !== 1 ? 's' : ''} & Continue` : 'Continue Without Fixing';
    
    // Update button style based on selection
    if (checked === 0) {
        btnFixSelected.classList.remove('btn-primary');
        btnFixSelected.classList.add('btn-secondary');
    } else {
        btnFixSelected.classList.add('btn-primary');
        btnFixSelected.classList.remove('btn-secondary');
    }
}

btnSelectAll.addEventListener('click', () => {
    document.querySelectorAll('.issue-checkbox').forEach(cb => cb.checked = true);
    updateFixCount();
});

btnSelectNone.addEventListener('click', () => {
    document.querySelectorAll('.issue-checkbox').forEach(cb => cb.checked = false);
    updateFixCount();
});

btnCancelValidation.addEventListener('click', () => {
    hideValidationModal();
    setProcessing(false);
    addLog('\n❌ Cancelled by user.');
    showStatus('error', 'Operation cancelled');
    setProgress(0, 'Cancelled');
});

btnIgnoreAll.addEventListener('click', async () => {
    hideValidationModal();
    setProcessing(true); // Should already be true, but ensure
    addLog('\n⚠️ Ignoring validation issues...');
    setProgress(0, 'Generating PDF (ignoring issues)...');
    
    try {
        // Run standard generation (which skips inspection)
        const result = await ipcRenderer.invoke('run-make-fillable', inputPath, outputPath);
        
        if (currentFlow === 'fillable-test') {
            // Continue with test fill
            addLog('\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n');
            addLog('Step 2: Test filling with dummy data...\n');
            setProgress(50, 'Now filling with test data...');
            
            const fillResult = await ipcRenderer.invoke('run-test-fill', result.outputPath, outputPath);
            
            setProgress(100, 'All done!');
            showStatus('success', `✅ Complete! Fillable PDF created and test-filled with ${fillResult.filledCount} fields! (Warnings Ignored)`);
            updateStatsDashboard(); // Update stats
        } else {
            setProgress(100, 'Complete!');
            showStatus('success', `✅ Success (Warnings Ignored)! Saved to: ${result.outputPath}`);
            updateStatsDashboard(); // Update stats
        }
    } catch (error) {
        if (error.message === 'CANCELLED') {
            addLog('\n⏹️ Operation cancelled by user.\n');
            showStatus('error', 'Operation cancelled');
            setProgress(0, 'Cancelled');
        } else {
            showStatus('error', `❌ Error: ${error.message}`);
        }
    } finally {
        setProcessing(false);
    }
});

btnFixSelected.addEventListener('click', async () => {
    const checkedBoxes = document.querySelectorAll('.issue-checkbox:checked');
    const fixIds = Array.from(checkedBoxes).map(cb => cb.dataset.id);
    
    hideValidationModal();
    setProcessing(true);
    
    if (fixIds.length > 0) {
        addLog(`\n🔧 Applying ${fixIds.length} fixes...`);
        setProgress(0, 'Applying fixes & regenerating...');
        
        try {
            const result = await ipcRenderer.invoke('apply-inspection-fixes', inputPath, outputPath, fixIds);
            
            if (currentFlow === 'fillable-test') {
                // Continue with test fill
                addLog('\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n');
                addLog('Step 2: Test filling with dummy data...\n');
                setProgress(50, 'Now filling with test data...');
                
                const fillResult = await ipcRenderer.invoke('run-test-fill', result.outputPath, outputPath);
                
                setProgress(100, 'All done!');
                showStatus('success', `✅ Complete! Fixed ${fixIds.length} issues, then test-filled with ${fillResult.filledCount} fields!`);
                updateStatsDashboard(); // Update stats
            } else {
                setProgress(100, 'Complete!');
                showStatus('success', `✅ Success! Fixed ${fixIds.length} issues. Saved to: ${result.outputPath}`);
                showEditFieldsButton(result.outputPath, true);
                updateStatsDashboard(); // Update stats
            }
        } catch (error) {
            if (error.message === 'CANCELLED') {
                addLog('\n⏹️ Operation cancelled by user.\n');
                showStatus('error', 'Operation cancelled');
                setProgress(0, 'Cancelled');
            } else {
                showStatus('error', `❌ Error applying fixes: ${error.message}`);
            }
        } finally {
            setProcessing(false);
        }
    } else {
        // No fixes selected, treated same as Ignore All
        btnIgnoreAll.click();
    }
});
    


// Button: Test Fill Only
btnTestFill.addEventListener('click', async () => {
    setSelectedButton(btnTestFill);
    if (currentMode === 'batch') {
        await runBatchProcessing('test');
        return;
    }

    if (!inputPath) return;
    
    setProcessing(true);
    showProgress(true);
    setProgress(5, 'Loading PDF...');
    addLog('🚀 Starting: Test Fill\n');
    
    try {
        const result = await ipcRenderer.invoke('run-test-fill', inputPath, outputPath);
        setProgress(100, 'Complete!');
        showStatus('success', `✅ Filled ${result.filledCount} fields! Saved to: ${result.outputPath}`);
        updateStatsDashboard(); // Update stats
    } catch (error) {
        if (error.message === 'CANCELLED') {
            addLog('\n⏹️ Operation cancelled by user.\n');
            showStatus('error', 'Operation cancelled');
            setProgress(0, 'Cancelled');
        } else {
            showStatus('error', `❌ Error: ${error.message}`);
            await ipcRenderer.invoke('show-error', 'Fill Error', error.message);
        }
    } finally {
        setProcessing(false);
    }
});

// Helper function for Make Fillable + Test Fill flow (when no inspection issues)
async function runFillableAndTestFlow() {
    setProgress(30, 'Step 1: Making PDF fillable...');
    addLog('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n');
    addLog('Step 1: Making PDF fillable...\n');
    
    try {
        const fillableResult = await ipcRenderer.invoke('run-make-fillable', inputPath, outputPath);
        
        addLog('\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n');
        addLog('Step 2: Test filling with dummy data...\n');
        setProgress(60, 'Step 2: Test filling...');
        
        const fillResult = await ipcRenderer.invoke('run-test-fill', fillableResult.outputPath, outputPath);
        
        setProgress(100, 'All done!');
        showStatus('success', `✅ Complete! Fillable PDF created and test-filled with ${fillResult.filledCount} fields!`);
        // NOTE: Field editor not enabled for "Make Fillable + Test Fill" flow
        updateStatsDashboard(); // Update stats
    } catch (error) {
        if (error.message === 'CANCELLED') {
            addLog('\n⏹️ Operation cancelled by user.\n');
            showStatus('error', 'Operation cancelled');
            setProgress(0, 'Cancelled');
        } else {
            showStatus('error', `❌ Error: ${error.message}`);
        }
        throw error;
    } finally {
        setProcessing(false);
    }
}

// Button: Make Fillable + Test Fill
btnBoth.addEventListener('click', async () => {
    if (currentMode === 'batch') {
        await runBatchProcessing('both');
        return;
    }

    if (!inputPath) return;
    
    currentFlow = 'fillable-test';
    setSelectedButton(btnBoth);
    setProcessing(true);
    showProgress(true);
    setProgress(0, 'Starting analysis...');
    addLog('🚀 Starting: Make Fillable + Test Fill\n');
    
    try {
        // Run staged inspection first
        const result = await ipcRenderer.invoke('run-inspection', inputPath, outputPath);
        
        if (result.issues && result.issues.length > 0) {
            // Auto-fix all fixable issues without showing dialog
            const fixableIssues = result.issues.filter(i => i.fixable);
            const fixIds = fixableIssues.map(i => i.id);
            
            if (fixIds.length > 0) {
                addLog(`\n🔧 Auto-fixing ${fixIds.length} issues...`);
                setProgress(40, 'Applying fixes & generating PDF...');
                
                const fixResult = await ipcRenderer.invoke('apply-inspection-fixes', inputPath, outputPath, fixIds);
                
                // Continue with test fill
                addLog('\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n');
                addLog('Test filling with sample data...\n');
                setProgress(70, 'Test filling...');
                
                const fillResult = await ipcRenderer.invoke('run-test-fill', fixResult.outputPath, outputPath);
                
                setProgress(100, 'All done!');
                showStatus('success', `✅ Complete! Auto-fixed ${fixIds.length} issues, then test-filled with ${fillResult.filledCount} fields!`);
                updateStatsDashboard();
            } else {
                // No fixable issues - just run normal flow
                addLog('\n✅ Processing complete. Generating PDF...');
                await runFillableAndTestFlow();
            }
        } else {
            // No issues - proceed directly to make-fillable + test-fill
            addLog('\n✅ Analysis complete! Generating PDF...');
            await runFillableAndTestFlow();
        }
        
    } catch (error) {
        if (error.message === 'CANCELLED') {
            addLog('\n⏹️ Operation cancelled by user.\n');
            showStatus('error', 'Operation cancelled');
            setProgress(0, 'Cancelled');
        } else {
            showStatus('error', `❌ Error: ${error.message}`);
            await ipcRenderer.invoke('show-error', 'Processing Error', error.message);
        }
    } finally {
        setProcessing(false);
    }
});

// Allow drag and drop for input file
document.addEventListener('dragover', (e) => {
    e.preventDefault();
    e.stopPropagation();
});

document.addEventListener('drop', async (e) => {
    e.preventDefault();
    e.stopPropagation();
    
    const files = e.dataTransfer.files;
    if (files.length === 0) return;
    
    const droppedPath = files[0].path;
    
    try {
        // Auto-detect file vs folder and handle accordingly
        await handleSelectedInput(droppedPath);
    } catch (error) {
        console.error('Drop error:', error);
    }
});

// ===== EDIT FIELDS BUTTON =====

// Track if a fillable PDF was created (not Make Fillable + Test Fill)
let isFillableOnlyMode = false;

// Enable Edit Fields button after "Make Fillable Only" processing
function showEditFieldsButton(outputPath, fromFillableOnly = false) {
    lastOutputPath = outputPath;
    isFillableOnlyMode = fromFillableOnly;
    if (btnEditFields && fromFillableOnly) {
        btnEditFields.disabled = false;
        btnEditFields.classList.remove('btn-disabled-look');
        btnEditFields.classList.add('pulse-ready'); // Add pulsing glow
        btnEditFields.style.pointerEvents = 'auto';
        btnEditFields.style.opacity = '1';
    }
}

// Disable Edit Fields button (reset)
function hideEditFieldsButton() {
    lastOutputPath = '';
    isFillableOnlyMode = false;
    if (btnEditFields) {
        btnEditFields.disabled = true;
        btnEditFields.classList.add('btn-disabled-look');
        btnEditFields.classList.remove('pulse-ready'); // Remove pulsing glow
        btnEditFields.style.pointerEvents = 'none';
        btnEditFields.style.opacity = '0.5';
    }
}

// Edit Fields button click - opens visual editor or shows explanation popup
if (btnEditFields) {
    btnEditFields.addEventListener('click', async () => {
        // Check if we're in prefill mode with existing fields
        if (isPrefillMode && prefillFieldData && prefillFieldData.length > 0) {
            const inputPath = document.getElementById('inputPath').value;
            try {
                addLog('\n🔧 Opening Field Editor for existing fields...');
                await ipcRenderer.invoke('open-editor', inputPath, inputPath);
            } catch (error) {
                console.error('Failed to open editor:', error);
                alert('Failed to open field editor: ' + error.message);
            }
            return;
        }
        
        // If button looks disabled, show explanation popup
        if (btnEditFields.classList.contains('btn-disabled-look') || !lastOutputPath || !isFillableOnlyMode) {
            const message = `📋 How to Enable the Field Editor:

1. Add a PDF to the input field
2. Click "Make Fillable Only" button
3. After the fillable PDF is generated, this button will be enabled

Note: The Field Editor is not available when using "Make Fillable + Test Fill" - only when using "Make Fillable Only".

This allows you to review and manually adjust fields before test-filling the document.`;
            
            alert(message);
            return;
        }
        
        try {
            addLog('\n🔧 Opening Field Editor...');
            await ipcRenderer.invoke('open-editor', lastOutputPath, lastOutputPath);
        } catch (error) {
            console.error('Failed to open editor:', error);
            alert('Failed to open field editor: ' + error.message);
        }
    });
}

// Listen for editor save completion
ipcRenderer.on('editor-saved', (event, data) => {
    addLog(`\n✅ Editor saved: ${data.changes.deleted} deleted, ${data.changes.moved} moved, ${data.changes.renamed} renamed`);
    showStatus('success', '✅ Field changes saved!');
});

// Listen for editor window close - stop pulsing
ipcRenderer.on('editor-closed', () => {
    if (btnEditFields) {
        btnEditFields.classList.remove('pulse-ready');
    }
});

// ===== SETTINGS MODAL =====

const settingsModal = document.getElementById('settingsModal');
const btnOpenSettings = document.getElementById('btnOpenSettings');
const btnSaveSettings = document.getElementById('btnSaveSettings');
const btnCancelSettings = document.getElementById('btnCancelSettings');
const btnResetSettings = document.getElementById('btnResetSettings');
const btnImportSettings = document.getElementById('btnImportSettings');

// Template Elements
const templateSelect = document.getElementById('templateSelect');
const btnSavePreset = document.getElementById('btnSavePreset');
const btnDeleteTemplate = document.getElementById('btnDeleteTemplate');

// Settings field IDs and their types
const SETTING_FIELDS = {
    // Detection Controls
    'auto_calibrate': 'toggle',
    'detection_sensitivity': 'text',
    'enable_empty_box_detection': 'toggle',
    'enable_underscore_detection': 'toggle',
    'enable_radio_grouping': 'toggle',
    'enable_table_detection': 'toggle',
    'skip_page_edges': 'toggle',
    'enable_garbage_field_cleanup': 'toggle',
    
    // Checkbox Settings
    'checkbox_size_min': 'number',
    'checkbox_size_max': 'number',
    'checkbox_field_size': 'number',
    'checkbox_style': 'text',             // checkmark style (check, circle, cross, etc.)
    'checkbox_bg_color': 'color',
    'checkbox_bg_opacity': 'number',
    'checkbox_border_color': 'color',
    'checkbox_border_thickness': 'number',
    'checkbox_border_opacity': 'number',
    
    // Visual/Styling
    'theme': 'text',
    'field_background_color': 'color',
    'field_background_opacity': 'number',
    'field_border_visible': 'toggle',
    'field_border_color': 'color',
    'field_border_thickness': 'number',
    'field_border_style': 'text',
    'field_font_size': 'number',
    'field_font_family': 'text',
    'field_font_color': 'color',
    'small_field_threshold': 'number',
    'display_tooltips': 'toggle',
    
    // JavaScript Auto-Formatting
    'enable_currency_formatting': 'toggle',
    'enable_date_picker': 'toggle',
    'enable_phone_formatting': 'toggle',
    'enable_number_formatting': 'toggle',
    
    // Currency Options
    'currency_symbol': 'text',
    'currency_symbol_position': 'text',
    'currency_decimal_places': 'number',
    
    // Number Options
    'number_decimal_places': 'number',
    'number_thousands_separator': 'text',
    'number_negative_style': 'text',
    
    // Date Options
    'date_format': 'text',
    
    // Phone Options
    'phone_format': 'text',
    
    // Advanced Layout
    'field_gap': 'float',
    'table_cell_padding': 'float',
    'min_field_width': 'number',
    'min_field_height': 'number',
    'cell_coverage_threshold': 'percent',  // Stored as decimal (0.7) but shown as percent (70)
    'auto_expand_fields': 'toggle',
    'enable_global_single_line_height': 'toggle',
    'global_single_line_height': 'float',
    
    // Detection Thresholds
    'min_underscore_length': 'number',
    'min_empty_box_width': 'number',
    'min_empty_box_height': 'number',
    'page_edge_margin': 'number',
    
    // Debug/Developer
    'check_for_updates': 'toggle',
    'verbose_logging': 'toggle',
    'preserve_existing_widgets': 'toggle',
    'generate_qa_overlays': 'toggle',
    
    // Output
    'output_suffix': 'text',
    'testfill_suffix': 'text',
    'auto_improve_names': 'toggle',

    // Auto-Calibration
    'auto_calibrate': 'toggle'
};

// Apply theme to document
function applyTheme(theme) {
    if (theme === 'light') {
        document.documentElement.setAttribute('data-theme', 'light');
    } else {
        document.documentElement.removeAttribute('data-theme');
    }
}

// Detection Sensitivity Presets
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
        min_empty_box_width: 100,
        min_empty_box_height: 30,
        min_field_width: 20,
        min_field_height: 10,
        page_edge_margin: 30
    },
    aggressive: {
        min_underscore_length: 2,      // Detect shorter underscores
        min_empty_box_width: 60,       // Detect smaller boxes
        min_empty_box_height: 20,
        min_field_width: 15,           // Allow smaller fields
        min_field_height: 8,
        page_edge_margin: 20           // Smaller margin from edges
    }
};

// Apply sensitivity preset to form
function applySensitivityPreset(sensitivity) {
    const preset = SENSITIVITY_PRESETS[sensitivity];
    if (!preset) return;
    
    for (const [key, value] of Object.entries(preset)) {
        const el = document.getElementById(`setting_${key}`);
        if (el) {
            el.value = value;
        }
    }
}

// Logic for Auto-Calibration Toggle
function updateAutoCalibrationState() {
    const autoCalibrateEl = document.getElementById('setting_auto_calibrate');
    const isAuto = autoCalibrateEl && autoCalibrateEl.classList.contains('active');
    
    // List of settings to disable when Auto-Calibration is ON
    // We keep visual settings (colors, fonts) enabled
    // We also keep garbage cleanup independent - it's a post-processing preference, not detection sensitivity
    const controlledSettings = [
        'setting_detection_sensitivity',
        'setting_enable_empty_box_detection', 
        'setting_enable_underscore_detection',
        'setting_enable_radio_grouping',
        'setting_enable_table_detection',
        'setting_skip_page_edges',
        // Garbage cleanup removed - let users control independently even with auto-config ON
        // 'setting_enable_garbage_field_cleanup',
        // 'setting_garbage_cleanup_sensitivity',
        
        // Thresholds
        'setting_min_underscore_length',
        'setting_min_empty_box_width',
        'setting_min_empty_box_height',
        'setting_page_edge_margin',
        'setting_small_field_threshold'
    ];
    
    controlledSettings.forEach(id => {
        const el = document.getElementById(id);
        const row = el ? el.closest('.setting-row') : null;
        
        if (row) {
            if (isAuto) {
                row.style.opacity = '0.5';
                row.style.pointerEvents = 'none';
                // optional: add a title explaining why
                row.title = "Managed by Auto-Calibration";
            } else {
                row.style.opacity = '1';
                row.style.pointerEvents = 'auto';
                row.title = "";
            }
        }
    });
}

// Listen for sensitivity changes (with live update)
document.addEventListener('DOMContentLoaded', () => {
    // Auto-Calibration
    const autoCalibrateToggle = document.getElementById('setting_auto_calibrate');
    if (autoCalibrateToggle) {
        autoCalibrateToggle.addEventListener('click', () => {
            // Generic handler toggles the class, we just need to update UI state
            // Use setTimeout to ensure the class has updated before we check it
            setTimeout(updateAutoCalibrationState, 0);
        });
    }

    const sensitivitySelect = document.getElementById('setting_detection_sensitivity');
    if (sensitivitySelect) {
        sensitivitySelect.addEventListener('change', (e) => {
            applySensitivityPreset(e.target.value);
        });
    }
});

// Load settings into form
async function loadSettingsIntoForm(specificSettings = null) {
    const settings = specificSettings || await ipcRenderer.invoke('get-settings');
    
    // Apply theme immediately
    if (settings.theme) {
        applyTheme(settings.theme);
        // Also update the select if it exists
        const themeSelect = document.getElementById('setting_theme');
        if (themeSelect) themeSelect.value = settings.theme;
    }

    for (const [key, type] of Object.entries(SETTING_FIELDS)) {
        const el = document.getElementById(`setting_${key}`);
        if (!el) continue;
        
        const value = settings[key];
        
        if (value === undefined) continue;
        
        if (type === 'toggle') {
            el.classList.toggle('active', value);
        } else if (type === 'percent') {
            // Convert decimal to percent
            el.value = Math.round((value || 0.7) * 100);
        } else if (type === 'color') {
            const colorValue = value || '#EDF4FF';
            el.value = colorValue;
            
            // Also update the visible color swatch preview and text
            const preview = document.getElementById(`preview_${key}`);
            const text = document.getElementById(`text_${key}`);
            if (preview) {
                if (colorValue === 'transparent' || !colorValue) {
                    preview.style.background = 'transparent';
                    preview.style.backgroundImage = 'linear-gradient(45deg, #ccc 25%, transparent 25%), linear-gradient(-45deg, #ccc 25%, transparent 25%), linear-gradient(45deg, transparent 75%, #ccc 75%), linear-gradient(-45deg, transparent 75%, #ccc 75%)';
                    preview.style.backgroundSize = '8px 8px';
                } else {
                    preview.style.background = colorValue;
                    preview.style.backgroundImage = 'none';
                }
            }
            if (text) {
                text.textContent = (colorValue && colorValue !== 'transparent') ? colorValue.toUpperCase() : 'None';
            }
        } else {
            el.value = value;
        }
    }
    
    // Load Garbage Cleanup Sensitivity
    const sensitivity = settings.garbage_cleanup_sensitivity || 'standard';
    const sensitivityRow = document.getElementById('row_garbage_cleanup_sensitivity');
    const sensitivitySelect = document.getElementById('setting_garbage_cleanup_sensitivity');
    const cleanupToggle = document.getElementById('setting_enable_garbage_field_cleanup');
    
    if (sensitivitySelect) {
        sensitivitySelect.value = sensitivity;
    }
    
    // Set initial visibility of sensitivity dropdown based on cleanup toggle
    if (sensitivityRow && cleanupToggle) {
        const isEnabled = cleanupToggle.classList.contains('active');
        sensitivityRow.style.display = isEnabled ? 'flex' : 'none';
    }
    
    // Update Auto-Calibration UI state
    updateAutoCalibrationState();
}

// Refresh logic for settings modal
async function refreshSettingsModal() {
    await loadSettingsIntoForm();
    await loadTemplates();
}

// === Template Management ===

// Update Preset Button visibility (Save/Delete)
function updatePresetButtons() {
    const isValidPreset = templateSelect.value && templateSelect.value !== '__create__';
    if (btnSavePreset) {
        btnSavePreset.style.opacity = isValidPreset ? '1' : '0.5';
        btnSavePreset.style.pointerEvents = isValidPreset ? 'auto' : 'none';
        btnSavePreset.title = isValidPreset ? 'Save changes to current preset' : 'Select a preset to update';
    }
    if (btnDeleteTemplate) {
        btnDeleteTemplate.style.opacity = isValidPreset ? '1' : '0.5';
        btnDeleteTemplate.style.pointerEvents = isValidPreset ? 'auto' : 'none';
        btnDeleteTemplate.title = isValidPreset ? 'Delete selected preset' : 'Select a preset to delete';
    }
}

async function loadTemplates() {
    try {
        const templates = await ipcRenderer.invoke('get-templates');
        
        // Save current selection if any
        const currentSelection = templateSelect.value;
        
        // Clear options except the first two (Select Preset... and Create New Preset)
        while (templateSelect.options.length > 2) {
            templateSelect.remove(2);
        }
        
        // Add saved presets after the two default options
        Object.keys(templates).sort().forEach(name => {
            const option = document.createElement('option');
            option.value = name;
            option.textContent = `📁 ${name}`;
            templateSelect.appendChild(option);
        });
        
        // Restore selection if it still exists
        if (templates[currentSelection]) {
            templateSelect.value = currentSelection;
        }
        
        updatePresetButtons();
    } catch (e) {
        console.error('Failed to load templates:', e);
    }
}

// Handle preset selection (both creating new and loading existing)
templateSelect.addEventListener('change', async () => {
    const name = templateSelect.value;
    
    // Handle "Create New Preset" option
    if (name === '__create__') {
        const presetName = await showInputPrompt('Enter a name for this preset:', 'My Preset');
        if (!presetName) {
            templateSelect.value = ''; // Reset to default
            return;
        }
        
        try {
            const settings = collectSettingsFromForm();
            await ipcRenderer.invoke('save-template', presetName, settings);
            await loadTemplates();
            templateSelect.value = presetName; // Select the new preset
            updatePresetButtons(); // Activate Save/Delete buttons
            
            // Flash success
            const option = Array.from(templateSelect.options).find(o => o.value === presetName);
            if (option) {
                const originalText = option.text;
                option.text = `${originalText} ✓ Saved!`;
                setTimeout(() => {
                    option.text = originalText;
                }, 1500);
            }
        } catch (e) {
            console.error('Failed to save preset:', e);
            alert('Failed to save preset');
            templateSelect.value = '';
            updatePresetButtons();
        }
        return;
    }
    
    // Handle empty selection (just reset)
    if (!name) return;
    
    // Handle loading an existing preset
    try {
        const templates = await ipcRenderer.invoke('get-templates');
        const settings = templates[name];
        if (settings) {
            await loadSettingsIntoForm(settings);
            // Flash success feedback
            const originalText = templateSelect.options[templateSelect.selectedIndex].text;
            templateSelect.options[templateSelect.selectedIndex].text = `${originalText} ✓`;
            setTimeout(() => {
                templateSelect.options[templateSelect.selectedIndex].text = originalText;
            }, 1500);
        }
    } catch (e) {
        console.error('Failed to load preset:', e);
        alert('Failed to load preset');
    }
    updatePresetButtons();
});

// Save Preset (Update currently selected preset)
if (btnSavePreset) {
    btnSavePreset.addEventListener('click', async () => {
        const name = templateSelect.value;
        if (!name || name === '__create__') {
            alert('Please select a saved preset to update.');
            return;
        }
        
        try {
            const settings = collectSettingsFromForm();
            await ipcRenderer.invoke('save-template', name, settings);
            
            // Flash success feedback
            const originalText = templateSelect.options[templateSelect.selectedIndex].text;
            templateSelect.options[templateSelect.selectedIndex].text = `${originalText} ✓ Updated!`;
            setTimeout(() => {
                templateSelect.options[templateSelect.selectedIndex].text = originalText;
            }, 1500);
        } catch (e) {
            console.error('Failed to update preset:', e);
            alert('Failed to update preset');
        }
    });
}

// Set up Template Buttons
if (templateSelect) {
    templateSelect.addEventListener('change', updatePresetButtons);
}

if (btnDeleteTemplate) {
    btnDeleteTemplate.addEventListener('click', async () => {
        const name = templateSelect.value;
        if (!name || name === '__create__') {
            alert('Please select a saved preset to delete.');
            return;
        }
        
        if (confirm(`Are you sure you want to delete "${name}"?`)) {
            try {
                await ipcRenderer.invoke('delete-template', name);
                await loadTemplates();
                templateSelect.value = '';
                updatePresetButtons();
            } catch (e) {
                console.error('Failed to delete preset:', e);
                alert('Failed to delete preset');
            }
        }
    });
}

// Live preview for theme
const themeSelect = document.getElementById('setting_theme');
if (themeSelect) {
    themeSelect.addEventListener('change', (e) => {
        applyTheme(e.target.value);
    });
}


// Collect settings from form
function collectSettingsFromForm() {
    const settings = {};
    
    for (const [key, type] of Object.entries(SETTING_FIELDS)) {
        const el = document.getElementById(`setting_${key}`);
        if (!el) continue;
        
        if (type === 'toggle') {
            settings[key] = el.classList.contains('active');
        } else if (type === 'percent') {
            // Convert percent to decimal
            settings[key] = parseInt(el.value) / 100;
        } else if (type === 'number') {
            settings[key] = parseInt(el.value);
        } else if (type === 'float') {
            settings[key] = parseFloat(el.value);
        } else {
            settings[key] = el.value;
        }
    }
    
    // FORCE manual collection of date_format (debug fix)
    const dateEl = document.getElementById('setting_date_format');
    if (dateEl) {
        settings['date_format'] = dateEl.value;
    } else {
        console.error('Could not find setting_date_format element');
    }
    

    // Collect Garbage Cleanup Sensitivity
    const sensitivitySelect = document.getElementById('setting_garbage_cleanup_sensitivity');
    if (sensitivitySelect) {
        settings.garbage_cleanup_sensitivity = sensitivitySelect.value;
    }
    
    return settings;
}

// Close settings modal
function closeSettingsModal() {
    settingsModal.style.display = 'none';
    // Clear search when closing
    if (settingsSearchInput) {
        settingsSearchInput.value = '';
        settingsSearchInput.dispatchEvent(new Event('input'));
    }
}

// Handler for the main Settings button (in buttons section)
if (btnOpenSettings) {
    btnOpenSettings.addEventListener('click', async () => {
        console.log('Open Settings button clicked');
        
        // Show modal IMMEDIATELY for responsiveness
        settingsModal.style.display = 'flex';
        
        try {
            console.log('Calling refreshSettingsModal...');
            await refreshSettingsModal();
            console.log('refreshSettingsModal returned');
        } catch (e) {
            console.error('Error loading settings:', e);
            // Non-blocking alert since modal is already open
            alert('⚠️ Warning: Could not load some settings: ' + e.message);
        }
    });
}

btnCancelSettings.addEventListener('click', closeSettingsModal);

// Close on backdrop click
settingsModal.addEventListener('click', (e) => {
    if (e.target === settingsModal) {
        closeSettingsModal();
    }
});

// ===== ABOUT MODAL =====
const aboutModal = document.getElementById('aboutModal');
const btnOpenAbout = document.getElementById('btnOpenAbout');
const btnCloseAbout = document.getElementById('btnCloseAbout');

if (btnOpenAbout) {
    btnOpenAbout.addEventListener('click', () => {
        aboutModal.style.display = 'flex';
    });
}

if (btnCloseAbout) {
    btnCloseAbout.addEventListener('click', () => {
        aboutModal.style.display = 'none';
    });
}

// Close About on backdrop click
if (aboutModal) {
    aboutModal.addEventListener('click', (e) => {
        if (e.target === aboutModal) {
            aboutModal.style.display = 'none';
        }
    });
}

// Toggle switches
document.querySelectorAll('.setting-toggle').forEach(toggle => {
    toggle.addEventListener('click', () => {
        toggle.classList.toggle('active');
        
        // Special handling: show/hide sensitivity dropdown when cleanup toggle is clicked
        if (toggle.id === 'setting_enable_garbage_field_cleanup') {
            const sensitivityRow = document.getElementById('row_garbage_cleanup_sensitivity');
            if (sensitivityRow) {
                const isEnabled = toggle.classList.contains('active');
                sensitivityRow.style.display = isEnabled ? 'flex' : 'none';
            }
        }
    });
});

// Save settings
btnSaveSettings.addEventListener('click', async () => {
    const settings = collectSettingsFromForm();
    
    const result = await ipcRenderer.invoke('save-settings', settings);
    
    if (result.success) {
        closeSettingsModal();
        showStatus('success', '✅ Settings saved!');
    } else {
        showStatus('error', `❌ Failed to save settings: ${result.error}`);
    }
});

// Reset to defaults
btnResetSettings.addEventListener('click', async () => {
    const result = await ipcRenderer.invoke('reset-settings');
    
    if (result.success) {
        await loadSettingsIntoForm();
        showStatus('success', '✅ Settings reset to defaults');
    }
});

// Export Settings
const btnExportSettings = document.getElementById('btnExportSettings');
if (btnExportSettings) {
    btnExportSettings.addEventListener('click', async () => {
        const result = await ipcRenderer.invoke('export-settings');
        if (result.success) {
            showStatus('success', `✅ Settings exported to: ${result.filePath}`);
        } else if (result.error) {
            showStatus('error', `❌ Export failed: ${result.error}`);
        }
    });
}

// Import Settings
// Import Settings
if (btnImportSettings) {
    btnImportSettings.addEventListener('click', async () => {
        if (!confirm('Importing settings will overwrite your current configuration. Continue?')) {
            return;
        }
        
        const result = await ipcRenderer.invoke('import-settings');
        if (result.success) {
            await loadSettingsIntoForm();
            // Also refresh stats if they were imported? (Usually we kept local stats, but refresh display anyway)
            updateStatsDashboard();
            showStatus('success', '✅ Settings imported successfully!');
        } else if (result.error) {
            showStatus('error', `❌ Import failed: ${result.error}`);
        }
    });
}

// Sign Out / Deactivate License
const btnSignOut = document.getElementById('btnSignOut');
if (btnSignOut) {
    btnSignOut.addEventListener('click', async () => {
        if (confirm('Are you sure you want to clear your AI Assistant API Key?')) {
            try {
                const result = await ipcRenderer.invoke('sign-out');
                if (result.success) {
                    alert('AI API Key cleared successfully.');
                    // No need to reload, just update UI if needed (could hide button or reset key status)
                    // For now, reload to be safe and reset state
                    window.location.reload();
                } else {
                    alert('Failed to clear API key: ' + (result.error || 'Unknown error'));
                }
            } catch (e) {
                console.error('Sign out error:', e);
                alert('Sign out error: ' + e.message);
            }
        }
    });
}

// Load settings on startup
(async () => {
    try {
        const settings = await ipcRenderer.invoke('get-settings');
        console.log('Settings loaded successfully');
        
        // Apply theme on startup
        if (settings.theme) {
            applyTheme(settings.theme);
        }

        // Initialize collapsible sections
        initSettingsCollapsible();
        
        updateStatsDashboard(); // Initial load of stats
        
        // Listen for auto-update events from main process
        ipcRenderer.on('update-available', (event, version) => {
            const notification = document.getElementById('updateNotification');
            const versionEl = document.getElementById('updateVersion');
            const link = document.getElementById('updateLink');
            if (notification && versionEl && link) {
                versionEl.textContent = `(v${version})`;
                link.textContent = 'Downloading...';
                link.removeAttribute('href');
                link.style.cursor = 'default';
                notification.style.display = 'flex';
            }
        });
        ipcRenderer.on('update-download-progress', (event, percent) => {
            const link = document.getElementById('updateLink');
            if (link) link.textContent = `Downloading... ${percent}%`;
        });
        ipcRenderer.on('update-downloaded', (event, version) => {
            const link = document.getElementById('updateLink');
            if (link) {
                link.textContent = 'Restart to Update';
                link.style.cursor = 'pointer';
                link.onclick = (e) => {
                    e.preventDefault();
                    ipcRenderer.invoke('install-update');
                };
            }
        });
        
    } catch (e) {
        console.error('Failed to load settings:', e);
    }
})();

// ===== STATS DASHBOARD =====

const statsHeader = document.getElementById('statsHeader');
const statsContent = document.getElementById('statsContent'); // Note: statsContent div added in HTML inside statsDashboard
const statsDashboard = document.getElementById('statsDashboard');
const statsArrow = document.getElementById('statsArrow');

// Toggle stats dashboard
if (statsHeader) {
    statsHeader.addEventListener('click', () => {
        const isCollapsed = statsContent.style.display === 'none';
        statsContent.style.display = isCollapsed ? 'block' : 'none';
        statsArrow.style.transform = isCollapsed ? 'rotate(0deg)' : 'rotate(-90deg)';
    });
}

// Update stats dashboard
async function updateStatsDashboard() {
    try {
        const settings = await ipcRenderer.invoke('get-settings');
        const stats = settings.stats || {
            pdfsProcessed: 0,
            fieldsCreated: 0,
            checkboxesCreated: 0,
            radioGroupsCreated: 0,
            textFieldsCreated: 0
        };
        
        // Show dashboard if we have data
        if (stats.pdfsProcessed > 0 && statsDashboard) {
            statsDashboard.style.display = 'block';
        }

        // Update values
        if (document.getElementById('statPdfs')) 
            document.getElementById('statPdfs').textContent = stats.pdfsProcessed;
        
        if (document.getElementById('statFields')) 
            document.getElementById('statFields').textContent = stats.fieldsCreated;
            
        if (document.getElementById('statText'))
            document.getElementById('statText').textContent = `${stats.textFieldsCreated} text fields`;
            
        if (document.getElementById('statChecks'))
            document.getElementById('statChecks').textContent = `${stats.checkboxesCreated} checkboxes`;
            
        if (document.getElementById('statRadios'))
            document.getElementById('statRadios').textContent = `${stats.radioGroupsCreated} radio groups`;
            
    } catch (e) {
        console.error('Failed to update stats:', e);
    }
}

// Update stats when processing completes
// Hook into existing success handlers by wrapping them or calling updateStatsDashboard()
// We'll update stats whenever the window gets focus as a simple catch-all, 
// and explicitly after processing success.

window.addEventListener('focus', updateStatsDashboard);


// ===== TAB NAVIGATION =====

const settingsTabs = document.querySelectorAll('.settings-tab');
const tabContents = {
    'fillable': document.getElementById('tabFillable'),
    'testfill': document.getElementById('tabTestFill')
};

settingsTabs.forEach(tab => {
    tab.addEventListener('click', () => {
        // Update active tab button
        settingsTabs.forEach(t => t.classList.remove('active'));
        tab.classList.add('active');

        // Show active tab content
        const targetTab = tab.dataset.tab;
        Object.values(tabContents).forEach(content => {
            if (content) content.classList.remove('active');
        });
        if (tabContents[targetTab]) {
            tabContents[targetTab].classList.add('active');
        }

        // Show/hide search box based on tab (only for Fillable tab)
        const searchBox = document.getElementById('settingsSearch');
        if (searchBox) {
            const searchContainer = searchBox.parentElement;
            if (targetTab === 'testfill') {
                searchContainer.style.display = 'none';
                // Clear and reset filter when leaving Fillable tab
                searchBox.value = '';
                searchBox.dispatchEvent(new Event('input'));
            } else {
                searchContainer.style.display = '';
            }
        }
    });
});

// ===== SETTINGS SEARCH =====
const settingsSearchInput = document.getElementById('settingsSearch');
if (settingsSearchInput) {
    settingsSearchInput.addEventListener('input', () => {
        const query = settingsSearchInput.value.toLowerCase().trim();

        // Only search within the Fillable PDF tab
        const activePane = document.getElementById('tabFillable');
        if (!activePane) return;

        const sections = activePane.querySelectorAll('.settings-section');
        const allRows = activePane.querySelectorAll('.setting-row');

        if (!query) {
            // Reset: show everything in the active tab
            sections.forEach(s => s.style.display = '');
            allRows.forEach(r => r.style.display = '');
            return;
        }

        // Filter rows by label text match (only within active tab)
        sections.forEach(s => {
            let hasVisibleRow = false;
            const rows = s.querySelectorAll('.setting-row');
            rows.forEach(row => {
                const label = row.textContent.toLowerCase();
                if (label.includes(query)) {
                    row.style.display = '';
                    hasVisibleRow = true;
                } else {
                    row.style.display = 'none';
                }
            });
            // Also check section header (h3)
            const header = s.querySelector('h3');
            if (header && header.textContent.toLowerCase().includes(query)) {
                // Show entire section if header matches
                rows.forEach(r => r.style.display = '');
                hasVisibleRow = true;
            }
            s.style.display = hasVisibleRow ? '' : 'none';
        });
    });
}

// ===== TEST FILL SPREADSHEET =====

const testFillTableBody = document.getElementById('testFillTableBody');
const numericPatternsInput = document.getElementById('setting_numeric_patterns');

// Load test fill config into spreadsheet
async function loadTestFillConfig(specificSettings = null) {
    const settings = specificSettings || await ipcRenderer.invoke('get-settings');
    const config = settings.testFillConfig;
    
    if (!config || !config.categories) return;
    
    // Clear existing rows
    testFillTableBody.innerHTML = '';
    
    // Populate table with categories
    config.categories.forEach((cat, idx) => {
        const row = document.createElement('tr');
        row.innerHTML = `
            <td class="category-name">${cat.name}</td>
            <td>
                <textarea class="editable-cell keywords-cell" data-category="${idx}">${cat.keywords.join(', ')}</textarea>
            </td>
            <td>
                <textarea class="editable-cell data-cell" data-category="${idx}">${cat.data.join(', ')}</textarea>
            </td>
        `;
        testFillTableBody.appendChild(row);
    });
    
    // Load numeric patterns
    if (numericPatternsInput && config.numericPatterns) {
        numericPatternsInput.value = config.numericPatterns;
    }
}

// Collect test fill config from spreadsheet
function collectTestFillConfig() {
    const categories = [];
    
    testFillTableBody.querySelectorAll('tr').forEach((row, idx) => {
        const categoryName = row.querySelector('.category-name')?.textContent || `category_${idx}`;
        const keywordsCell = row.querySelector('.keywords-cell');
        const dataCell = row.querySelector('.data-cell');
        
        if (keywordsCell && dataCell) {
            const keywords = keywordsCell.value.split(',').map(k => k.trim()).filter(k => k);
            const data = dataCell.value.split(',').map(d => d.trim()).filter(d => d);
            categories.push({ name: categoryName, keywords, data });
        }
    });
    
    return {
        categories,
        numericPatterns: numericPatternsInput?.value || ''
    };
}

// Update loadSettingsIntoForm to also load test fill config
const originalLoadSettingsIntoForm = loadSettingsIntoForm;
loadSettingsIntoForm = async function(specificSettings = null) {
    await originalLoadSettingsIntoForm(specificSettings);
    await loadTestFillConfig(specificSettings);
};

// Update collectSettingsFromForm to also collect test fill config
const originalCollectSettingsFromForm = collectSettingsFromForm;
collectSettingsFromForm = function() {
    const settings = originalCollectSettingsFromForm();
    settings.testFillConfig = collectTestFillConfig();
    return settings;
};

// Add Category function - reads from input field instead of prompt (Electron doesn't support prompt)
function addNewCategory() {
    const inputField = document.getElementById('newCategoryName');
    const newName = inputField ? inputField.value : '';
    if (!newName || !newName.trim()) {
        inputField?.focus();
        return;
    }
    
    const cleanName = newName.trim().toLowerCase().replace(/\s+/g, '_');
    const idx = testFillTableBody.querySelectorAll('tr').length;
    
    const row = document.createElement('tr');
    row.innerHTML = `
        <td class="category-name">${cleanName}</td>
        <td>
            <textarea class="editable-cell keywords-cell" data-category="${idx}">${cleanName}</textarea>
        </td>
        <td>
            <textarea class="editable-cell data-cell" data-category="${idx}">Sample 1, Sample 2, Sample 3</textarea>
        </td>
    `;
    testFillTableBody.appendChild(row);
    
    // Clear input and scroll to new row
    if (inputField) inputField.value = '';
    row.scrollIntoView({ behavior: 'smooth', block: 'center' });
}
// Make function globally accessible for button click
window.addNewCategory = addNewCategory;

// Also add click handler for the Add button (found after DOM ready)
document.addEventListener('DOMContentLoaded', () => {
    const btn = document.getElementById('btnAddCategory');
    if (btn) {
        btn.addEventListener('click', addNewCategory);
    }
    
    // Info icon tooltip handlers
    let activeTooltip = null;
    
    document.querySelectorAll('.info-icon').forEach(icon => {
        icon.addEventListener('click', (e) => {
            e.stopPropagation();
            
            // Remove existing tooltip
            if (activeTooltip) {
                activeTooltip.remove();
                activeTooltip = null;
            }
            
            const infoText = icon.getAttribute('data-info');
            if (!infoText) return;
            
            // Create tooltip
            const tooltip = document.createElement('div');
            tooltip.className = 'info-tooltip';
            tooltip.textContent = infoText;
            document.body.appendChild(tooltip);
            
            // Position tooltip below the icon
            const rect = icon.getBoundingClientRect();
            tooltip.style.left = `${rect.left - 10}px`;
            tooltip.style.top = `${rect.bottom + 8}px`;
            
            activeTooltip = tooltip;
        });
    });
    
    // Close tooltip when clicking elsewhere
    document.addEventListener('click', () => {
        if (activeTooltip) {
            activeTooltip.remove();
            activeTooltip = null;
        }
    });
    
    // Checkbox opacity slider handlers
    const checkboxBgOpacity = document.getElementById('setting_checkbox_bg_opacity');
    const checkboxBgOpacityLabel = document.getElementById('checkbox_bg_opacity_label');
    if (checkboxBgOpacity && checkboxBgOpacityLabel) {
        checkboxBgOpacity.addEventListener('input', (e) => {
            checkboxBgOpacityLabel.textContent = `${e.target.value}%`;
        });
    }
    
    const checkboxBorderOpacity = document.getElementById('setting_checkbox_border_opacity');
    const checkboxBorderOpacityLabel = document.getElementById('checkbox_border_opacity_label');
    if (checkboxBorderOpacity && checkboxBorderOpacityLabel) {
        checkboxBorderOpacity.addEventListener('input', (e) => {
            checkboxBorderOpacityLabel.textContent = `${e.target.value}%`;
        });
    }
    
    // ============ Custom Color Picker ============
    let colorPickerTarget = null;
    let currentColorMode = 'rgb';
    let currentR = 237, currentG = 244, currentB = 255, currentA = 100;
    
    const popup = document.getElementById('colorPickerPopup');
    const tabs = document.querySelectorAll('.color-picker-tab');
    const rgbPane = document.getElementById('colorModeRGB');
    const cmykPane = document.getElementById('colorModeCMYK');
    const grayPane = document.getElementById('colorModeGray');
    
    // RGB sliders
    const sliderR = document.getElementById('cpSliderR');
    const sliderG = document.getElementById('cpSliderG');
    const sliderB = document.getElementById('cpSliderB');
    const sliderA = document.getElementById('cpSliderA');
    
    // CMYK sliders
    const sliderC = document.getElementById('cpSliderC');
    const sliderM = document.getElementById('cpSliderM');
    const sliderY = document.getElementById('cpSliderY');
    const sliderK = document.getElementById('cpSliderK');
    
    // Gray slider
    const sliderGray = document.getElementById('cpSliderGray');
    
    // Preview & hex
    const preview = document.getElementById('cpPreview');
    const hexInput = document.getElementById('cpHexInput');
    const transparentCheckbox = document.getElementById('cpTransparent');
    
    // Color conversion functions
    function rgbToHex(r, g, b) {
        return '#' + [r, g, b].map(x => x.toString(16).padStart(2, '0')).join('').toUpperCase();
    }
    
    function hexToRgb(hex) {
        const match = hex.match(/^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i);
        return match ? { r: parseInt(match[1], 16), g: parseInt(match[2], 16), b: parseInt(match[3], 16) } : null;
    }
    
    function rgbToCmyk(r, g, b) {
        if (r === 0 && g === 0 && b === 0) return { c: 0, m: 0, y: 0, k: 100 };
        const c = 1 - r / 255, m = 1 - g / 255, y = 1 - b / 255;
        const k = Math.min(c, m, y);
        return {
            c: Math.round(((c - k) / (1 - k)) * 100),
            m: Math.round(((m - k) / (1 - k)) * 100),
            y: Math.round(((y - k) / (1 - k)) * 100),
            k: Math.round(k * 100)
        };
    }
    
    function cmykToRgb(c, m, y, k) {
        const r = Math.round(255 * (1 - c / 100) * (1 - k / 100));
        const g = Math.round(255 * (1 - m / 100) * (1 - k / 100));
        const b = Math.round(255 * (1 - y / 100) * (1 - k / 100));
        return { r, g, b };
    }
    
    function rgbToGray(r, g, b) {
        return Math.round((0.299 * r + 0.587 * g + 0.114 * b) / 255 * 100);
    }
    
    function grayToRgb(gray) {
        const val = Math.round(gray / 100 * 255);
        return { r: val, g: val, b: val };
    }
    
    function updatePreview() {
        if (transparentCheckbox && transparentCheckbox.checked) {
            preview.style.background = 'transparent';
            hexInput.value = 'transparent';
        } else {
            const rgba = `rgba(${currentR}, ${currentG}, ${currentB}, ${currentA / 100})`;
            preview.style.background = rgba;
            hexInput.value = rgbToHex(currentR, currentG, currentB);
        }
    }
    
    function updateAllSliders() {
        // RGB
        if (sliderR) sliderR.value = currentR;
        if (sliderG) sliderG.value = currentG;
        if (sliderB) sliderB.value = currentB;
        if (sliderA) sliderA.value = currentA;
        document.getElementById('cpValueR').textContent = currentR;
        document.getElementById('cpValueG').textContent = currentG;
        document.getElementById('cpValueB').textContent = currentB;
        document.getElementById('cpValueA').textContent = currentA + '%';
        
        // CMYK
        const cmyk = rgbToCmyk(currentR, currentG, currentB);
        if (sliderC) sliderC.value = cmyk.c;
        if (sliderM) sliderM.value = cmyk.m;
        if (sliderY) sliderY.value = cmyk.y;
        if (sliderK) sliderK.value = cmyk.k;
        document.getElementById('cpValueC').textContent = cmyk.c + '%';
        document.getElementById('cpValueM').textContent = cmyk.m + '%';
        document.getElementById('cpValueY').textContent = cmyk.y + '%';
        document.getElementById('cpValueK').textContent = cmyk.k + '%';
        
        // Gray
        const gray = rgbToGray(currentR, currentG, currentB);
        if (sliderGray) sliderGray.value = gray;
        document.getElementById('cpValueGray').textContent = gray + '%';
        
        updatePreview();
    }
    
    // Tab switching
    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            tabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            currentColorMode = tab.dataset.mode;
            
            rgbPane.style.display = currentColorMode === 'rgb' ? 'block' : 'none';
            cmykPane.style.display = currentColorMode === 'cmyk' ? 'block' : 'none';
            grayPane.style.display = currentColorMode === 'gray' ? 'block' : 'none';
        });
    });
    
    // RGB slider handlers
    [sliderR, sliderG, sliderB].forEach((slider, i) => {
        if (slider) {
            slider.addEventListener('input', () => {
                if (i === 0) currentR = parseInt(slider.value);
                if (i === 1) currentG = parseInt(slider.value);
                if (i === 2) currentB = parseInt(slider.value);
                updateAllSliders();
            });
        }
    });
    
    // Alpha slider
    if (sliderA) {
        sliderA.addEventListener('input', () => {
            currentA = parseInt(sliderA.value);
            updateAllSliders();
        });
    }
    
    // CMYK slider handlers
    [sliderC, sliderM, sliderY, sliderK].forEach(slider => {
        if (slider) {
            slider.addEventListener('input', () => {
                const rgb = cmykToRgb(
                    parseInt(sliderC.value),
                    parseInt(sliderM.value),
                    parseInt(sliderY.value),
                    parseInt(sliderK.value)
                );
                currentR = rgb.r; currentG = rgb.g; currentB = rgb.b;
                updateAllSliders();
            });
        }
    });
    
    // Gray slider handler
    if (sliderGray) {
        sliderGray.addEventListener('input', () => {
            const rgb = grayToRgb(parseInt(sliderGray.value));
            currentR = rgb.r; currentG = rgb.g; currentB = rgb.b;
            updateAllSliders();
        });
    }
    
    // Hex input handler
    if (hexInput) {
        hexInput.addEventListener('change', () => {
            if (hexInput.value === 'transparent') {
                if (transparentCheckbox) {
                    transparentCheckbox.checked = true;
                    transparentCheckbox.dispatchEvent(new Event('change'));
                }
                return;
            }
            const rgb = hexToRgb(hexInput.value);
            if (rgb) {
                if (transparentCheckbox) {
                    transparentCheckbox.checked = false;
                    transparentCheckbox.dispatchEvent(new Event('change'));
                }
                currentR = rgb.r; currentG = rgb.g; currentB = rgb.b;
                updateAllSliders();
            }
        });
    }

    // Transparent Checkbox Listener
    if (transparentCheckbox) {
        transparentCheckbox.addEventListener('change', () => {
            const isTransparent = transparentCheckbox.checked;
            // Disable/Enable inputs
            const inputs = [sliderR, sliderG, sliderB, sliderA, sliderC, sliderM, sliderY, sliderK, sliderGray];
            inputs.forEach(input => {
                if (input) input.disabled = isTransparent;
            });
            updatePreview();
        });
    }
    
    // Open popup on swatch button click
    document.querySelectorAll('.color-swatch-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const targetId = btn.dataset.target;
            const label = btn.dataset.label || 'Select Color';
            colorPickerTarget = targetId;
            
            // Get current value
            const currentVal = document.getElementById(targetId).value;
            
            // Handle transparency
            if (currentVal === 'transparent' || currentVal === '') {
                if (transparentCheckbox) {
                    transparentCheckbox.checked = true;
                    // Trigger change to update UI state (disable sliders)
                    transparentCheckbox.dispatchEvent(new Event('change'));
                }
            } else {
                if (transparentCheckbox) {
                    transparentCheckbox.checked = false;
                    transparentCheckbox.dispatchEvent(new Event('change'));
                }
                const rgb = hexToRgb(currentVal);
                if (rgb) {
                    currentR = rgb.r; currentG = rgb.g; currentB = rgb.b;
                }
            }
            
            document.getElementById('colorPickerLabel').textContent = label;
            updateAllSliders();
            
            // Position popup centered on screen
            const popupWidth = 280;
            const popupHeight = 420;
            popup.style.left = `${(window.innerWidth - popupWidth) / 2}px`;
            popup.style.top = `${(window.innerHeight - popupHeight) / 2}px`;
            popup.style.display = 'block';
        });
    });
    
    // Close button
    document.getElementById('colorPickerClose')?.addEventListener('click', () => {
        popup.style.display = 'none';
        colorPickerTarget = null;
    });
    
    // Apply button
    document.getElementById('cpApply')?.addEventListener('click', () => {
        if (colorPickerTarget) {
            let hex;
            if (transparentCheckbox && transparentCheckbox.checked) {
                hex = 'transparent';
            } else {
                hex = rgbToHex(currentR, currentG, currentB);
            }
            document.getElementById(colorPickerTarget).value = hex;
            document.getElementById('preview_' + colorPickerTarget.replace('setting_', '')).style.background = hex;
            document.getElementById('text_' + colorPickerTarget.replace('setting_', '')).textContent = hex;
            
            // Also save opacity for field_background_color
            if (colorPickerTarget === 'setting_field_background_color') {
                const opacityInput = document.getElementById('setting_field_background_opacity');
                if (!opacityInput) {
                    // Create hidden input if it doesn't exist
                    const input = document.createElement('input');
                    input.type = 'hidden';
                    input.id = 'setting_field_background_opacity';
                    input.value = currentA;
                    document.body.appendChild(input);
                } else {
                    opacityInput.value = currentA;
                }
            }
        }
        popup.style.display = 'none';
        colorPickerTarget = null;
    });
    

    // Close when clicking outside
    document.addEventListener('click', (e) => {
        if (popup.style.display === 'block' && !popup.contains(e.target) && !e.target.closest('.color-swatch-btn')) {
            popup.style.display = 'none';
            colorPickerTarget = null;
        }
    });
});

// Initialize collapsible settings sections
async function initSettingsCollapsible() {
    try {
        const saved = await ipcRenderer.invoke('get-settings');
        let collapsed = (saved && saved.ui_settings_collapsed) ? saved.ui_settings_collapsed : {};
        const defaultCollapse = !saved || !saved.ui_settings_collapsed || Object.keys(saved.ui_settings_collapsed).length === 0;

        document.querySelectorAll('.settings-section').forEach(section => {
            const h3 = section.querySelector('h3');
            if (!h3) return;

            // Add a chevron indicator if not present
            if (!h3.querySelector('.chev')) {
                const chev = document.createElement('span');
                chev.className = 'chev';
                chev.textContent = '▾';
                h3.appendChild(chev);
            }

            // Use the heading text as a key (sanitized)
            const key = h3.textContent.trim().toLowerCase().replace(/[^a-z0-9_]/g, '_').slice(0,40);

            // If there's no saved state, default to collapsed
            // Also default to collapsed if the specific key is missing (undefined)
            if (defaultCollapse || collapsed[key] !== false) {
                collapsed[key] = true;
                section.classList.add('collapsed');
            }

            h3.addEventListener('click', async () => {
                const isCollapsed = section.classList.toggle('collapsed');
                // Persist the state
                const current = await ipcRenderer.invoke('get-settings') || {};
                const ui = current.ui_settings_collapsed || {};
                ui[key] = isCollapsed;
                await ipcRenderer.invoke('save-settings', { ui_settings_collapsed: ui });
            });
        });
    } catch (e) {
        console.error('Failed to initialize collapsible settings:', e);
    }
}
