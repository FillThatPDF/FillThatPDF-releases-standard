/**
 * panel.js — Renderer for floating Electron panel windows
 * Handles both "global-styles" and "properties" panel types.
 * Communicates with the editor via main.js IPC relay:
 *   editor  → 'open-panel' invoke  → main creates window, sends 'panel-init'
 *   panel   → ipcRenderer.send('panel-to-editor', payload)
 *   main    → editorWindow.webContents.send('from-panel', payload)
 *   editor  → ipcRenderer.send('editor-to-panel', type, data)
 *   main    → panelWindow.webContents.send('panel-update', { type, data })
 */

const { ipcRenderer } = require('electron');

// ── Module state ────────────────────────────────────────────────────────────
let panelType       = null;      // 'global-styles' | 'properties'
let fieldsByType    = {};        // { text: 5, checkbox: 3, … }  — for affected count
let currentField    = null;      // The field currently shown in Properties panel
let _currentPropTab = 'general'; // Last active Properties tab — preserved across field switches

// ── Boot ─────────────────────────────────────────────────────────────────────
ipcRenderer.on('panel-init', (evt, type, data) => {
    panelType = type;
    if (type === 'global-styles') {
        initGlobalStyles(data);
    } else if (type === 'properties') {
        initProperties(data);
    } else if (type === 'history') {
        initHistory(data);
    } else if (type === 'calc') {
        initCalc(data);
    } else if (type === 'hyperlinks') {
        initHyp(data);
    }
});

// Live updates pushed from the editor (selection changes, field count changes, etc.)
ipcRenderer.on('panel-update', (evt, { type, data }) => {
    if (type === 'global-styles') {
        gsRefreshCount(data);
    } else if (type === 'properties') {
        if (data?.pickResult) {
            // A field was picked from canvas — add to the local calc sources list
            propAddPickedCalcSource(data.pickResult);
        } else if (data?.pickStopped) {
            // Editor signals that pick mode ended (e.g. user deactivated it)
            _panelPickActive = false;
            const btn = document.getElementById('btnPickFromCanvas');
            if (btn) {
                btn.classList.remove('active');
                btn.textContent = '🎯 Click Fields on Canvas to Add';
            }
        } else if (data?.autoNameResult) {
            // Editor finished auto-naming — update the name input and restore button
            const { newName, oldName, error } = data.autoNameResult;
            const btn = document.getElementById('btnAutoNameModal');
            if (btn) { btn.innerHTML = '✨ Auto Name'; btn.disabled = false; }
            if (!error && newName) {
                const nameInput = document.getElementById('modalPropName');
                if (nameInput) {
                    nameInput.value = newName;
                    // Brief green flash to signal success
                    nameInput.style.backgroundColor = 'rgba(100,255,218,0.15)';
                    setTimeout(() => { nameInput.style.backgroundColor = ''; }, 600);
                }
                if (_propCurrentField) _propCurrentField.name = newName;
            }
        } else {
            // Don't let a full field refresh clobber the UI while pick mode is active
            if (_panelPickActive) return;
            propPopulate(data?.field, data?.globalSettings);
            // Restore the tab the user was on — don't jump back to General every time
            if (data?.field) propSwitchTab(_currentPropTab || 'general');
        }
    } else if (type === 'history') {
        histRefresh(data);
    } else if (type === 'calc') {
        calcRefresh(data);
    } else if (type === 'calc-suggestions') {
        calcRenderSuggestions(data?.suggestions || []);
    } else if (type === 'hyperlinks') {
        hypRefresh(data);
    }
});

// ════════════════════════════════════════════════════════════════════════════
//  GLOBAL STYLES PANEL
// ════════════════════════════════════════════════════════════════════════════

function initGlobalStyles(data) {
    document.getElementById('panelTitle').textContent = '🎨 Global Styles';

    const panel = document.getElementById('globalStylesPanel');
    panel.style.display       = 'flex';
    panel.style.flex          = '1';
    panel.style.flexDirection = 'column';
    panel.style.overflow      = 'hidden';

    // Seed field-count data
    fieldsByType = data?.fieldsByType || {};

    // ── Enable-checkbox → enable/disable the row ──────────────────────────
    document.querySelectorAll('.enable-cb').forEach(cb => {
        const row = cb.closest('.toggle-row');
        cb.addEventListener('change', () => {
            row.classList.toggle('disabled', !cb.checked);
        });
    });

    // ── Chip checkboxes → update affected count ───────────────────────────
    document.querySelectorAll('#gsApplyTo .chip input[type="checkbox"]').forEach(cb => {
        cb.addEventListener('change', () => {
            cb.closest('.chip').classList.toggle('active', cb.checked);
            gsUpdateAffectedCount();
        });
    });

    // ── Transparent toggle → disable/fade fill-color picker ──────────────
    const gsTransparent = document.getElementById('gsTransparent');
    const gsFillColor   = document.getElementById('gsFillColor');
    gsTransparent.addEventListener('change', () => {
        gsFillColor.disabled     = gsTransparent.checked;
        gsFillColor.style.opacity = gsTransparent.checked ? '0.4' : '1';
    });

    gsUpdateAffectedCount();
    gsLoadPresetsToDropdown();
}

/** Rebuild the affected-field count badge from the chip selection + fieldsByType map */
function gsUpdateAffectedCount() {
    const selected = Array.from(
        document.querySelectorAll('#gsApplyTo .chip input[type="checkbox"]:checked')
    ).map(c => c.value);

    let count = 0;
    for (const [type, n] of Object.entries(fieldsByType)) {
        if (selected.includes(type)) count += n;
    }

    const totalPages = (typeof window._gsTotalPages !== 'undefined') ? window._gsTotalPages : '—';

    document.getElementById('gsAffectedCount').textContent = count;
    document.getElementById('gsAffectedPages').textContent  = totalPages;
}

/** Called via panel-update when editor field list changes */
function gsRefreshCount(data) {
    fieldsByType          = data?.fieldsByType || {};
    window._gsTotalPages  = data?.totalPages   || '—';
    gsUpdateAffectedCount();
}

// ── gsApply ──────────────────────────────────────────────────────────────────
function gsApply() {
    const applyTo = Array.from(
        document.querySelectorAll('#gsApplyTo .chip input[type="checkbox"]:checked')
    ).map(c => c.value);

    const fillEnabled = document.getElementById('enableFillColor').checked;

    const payload = {
        applyTo,
        fontFamily:    { enabled: document.getElementById('enableFontFamily').checked,    value: document.getElementById('gsFontFamily').value },
        fontSize:      { enabled: document.getElementById('enableFontSize').checked,      value: parseFloat(document.getElementById('gsFontSize').value) },
        fontColor:     { enabled: document.getElementById('enableFontColor').checked,     value: document.getElementById('gsFontColor').value },
        fillColor:     { enabled: fillEnabled, value: document.getElementById('gsFillColor').value },
        transparent:   { enabled: fillEnabled, value: document.getElementById('gsTransparent').checked },
        textAlign:     { enabled: document.getElementById('enableTextAlign').checked,     value: document.getElementById('gsTextAlign').value },
        checkboxStyle: { enabled: document.getElementById('enableCheckboxStyle').checked, value: document.getElementById('gsCheckboxStyle').value },
    };

    ipcRenderer.send('panel-to-editor', { action: 'apply-global-styles', payload });

    // Brief visual feedback on the Apply button
    const btn = document.querySelector('.panel-footer .btn-primary');
    if (btn) {
        const orig = btn.textContent;
        btn.textContent = '✅ Applied!';
        btn.disabled    = true;
        setTimeout(() => { btn.textContent = orig; btn.disabled = false; }, 1600);
    }
}

// ── Presets ──────────────────────────────────────────────────────────────────
const PRESET_STORAGE_KEY = 'ftpGlobalStylePresets';

function gsGetAllPresets() {
    try { return JSON.parse(localStorage.getItem(PRESET_STORAGE_KEY) || '{}'); }
    catch { return {}; }
}

function gsLoadPresetsToDropdown() {
    const presets = gsGetAllPresets();
    const sel     = document.getElementById('gsPresetSelect');
    // Clear non-placeholder options
    while (sel.options.length > 1) sel.remove(1);
    for (const name of Object.keys(presets)) {
        const opt       = document.createElement('option');
        opt.value       = name;
        opt.textContent = name;
        sel.appendChild(opt);
    }
}

function gsLoadPreset() {
    const name = document.getElementById('gsPresetSelect').value;
    if (!name) return;
    const p = (gsGetAllPresets())[name];
    if (!p) return;

    const set = (enableId, trId, inputId, value) => {
        if (value === undefined) return;
        document.getElementById(enableId).checked = true;
        document.getElementById(trId).classList.remove('disabled');
        document.getElementById(inputId).value = value;
    };

    set('enableFontFamily',    'trFontFamily',    'gsFontFamily',    p.fontFamily);
    set('enableFontSize',      'trFontSize',      'gsFontSize',      p.fontSize);
    set('enableFontColor',     'trFontColor',     'gsFontColor',     p.fontColor);
    set('enableTextAlign',     'trTextAlign',     'gsTextAlign',     p.textAlign);
    set('enableCheckboxStyle', 'trCheckboxStyle', 'gsCheckboxStyle', p.checkboxStyle);

    if (p.fillColor !== undefined || p.transparent !== undefined) {
        document.getElementById('enableFillColor').checked = true;
        document.getElementById('trFillColor').classList.remove('disabled');
        if (p.fillColor   !== undefined) document.getElementById('gsFillColor').value  = p.fillColor;
        if (p.transparent !== undefined) document.getElementById('gsTransparent').checked = p.transparent;
        const t = document.getElementById('gsTransparent').checked;
        document.getElementById('gsFillColor').disabled     = t;
        document.getElementById('gsFillColor').style.opacity = t ? '0.4' : '1';
    }
}

function gsSavePreset() {
    const nameEl = document.getElementById('gsPresetName');
    const name   = nameEl.value.trim();
    if (!name) { nameEl.focus(); return; }

    const presets = gsGetAllPresets();
    const snap    = {};

    const maybe = (enableId, key, getter) => {
        if (document.getElementById(enableId).checked) snap[key] = getter();
    };
    maybe('enableFontFamily',    'fontFamily',    () => document.getElementById('gsFontFamily').value);
    maybe('enableFontSize',      'fontSize',      () => parseFloat(document.getElementById('gsFontSize').value));
    maybe('enableFontColor',     'fontColor',     () => document.getElementById('gsFontColor').value);
    maybe('enableTextAlign',     'textAlign',     () => document.getElementById('gsTextAlign').value);
    maybe('enableCheckboxStyle', 'checkboxStyle', () => document.getElementById('gsCheckboxStyle').value);
    if (document.getElementById('enableFillColor').checked) {
        snap.fillColor   = document.getElementById('gsFillColor').value;
        snap.transparent = document.getElementById('gsTransparent').checked;
    }

    presets[name] = snap;
    localStorage.setItem(PRESET_STORAGE_KEY, JSON.stringify(presets));
    gsLoadPresetsToDropdown();
    document.getElementById('gsPresetSelect').value = name;
    nameEl.value = '';
}

function gsDeletePreset() {
    const sel  = document.getElementById('gsPresetSelect');
    const name = sel.value;
    if (!name) return;
    const presets = gsGetAllPresets();
    delete presets[name];
    localStorage.setItem(PRESET_STORAGE_KEY, JSON.stringify(presets));
    gsLoadPresetsToDropdown();
}


// ════════════════════════════════════════════════════════════════════════════
//  PROPERTIES PANEL
// ════════════════════════════════════════════════════════════════════════════

let _propCurrentField    = null;   // last field data received from editor
let _propGlobalSettings  = {};
let _panelCalcSources    = [];     // { name, page } list for Properties Calculate tab
let _panelPickActive     = false;  // whether canvas pick mode is live for Properties panel

function initProperties(data) {
    document.getElementById('panelTitle').textContent = '📋 Field Properties';

    const panel = document.getElementById('propertiesPanelFloat');
    if (!panel) return;

    panel.style.display       = 'flex';
    panel.style.flex          = '1';
    panel.style.flexDirection = 'column';
    panel.style.overflow      = 'hidden';

    // Wire tab buttons (once — guard with a flag)
    if (!panel._tabsWired) {
        panel.querySelectorAll('.modal-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                propSwitchTab(tab.dataset.tab);
                // Initialise JS Snippet Library when Actions tab becomes visible
                if (tab.dataset.tab === 'actions') {
                    setTimeout(jsLibInit, 10);
                }
            });
        });
        panel._tabsWired = true;
    }

    _propGlobalSettings = data?.globalSettings || {};
    propPopulate(data?.field, _propGlobalSettings);
    // Always land on the General tab so the panel is never blank
    propSwitchTab('general');
}

/** Called by panel-update when editor selection changes */
function propPopulate(field, globalSettings) {
    _propCurrentField   = field;
    _propGlobalSettings = globalSettings || {};

    const nameEl = document.getElementById('modalFieldName');
    if (!field) {
        if (nameEl) nameEl.textContent = 'No Field Selected';
        document.querySelectorAll('.modal-tab').forEach(t => t.style.opacity = '0.4');
        return;
    }
    document.querySelectorAll('.modal-tab').forEach(t => t.style.opacity = '');

    // ── Title ───────────────────────────────────────────────────────────────
    if (nameEl) nameEl.textContent = field.name || '';

    const set  = (id, val)  => { const el = document.getElementById(id); if (el) el.value   = (val ?? ''); };
    const chk  = (id, bool) => { const el = document.getElementById(id); if (el) el.checked  = !!bool; };
    const swatch = (base, hex) => {
        const prev = document.getElementById(`preview_${base}`);
        const text = document.getElementById(`text_${base}`);
        const hidden = document.getElementById(base);
        const color = hex || '#000000';
        if (prev)   prev.style.background = color;
        if (text)   text.textContent       = color;
        if (hidden) hidden.value           = color;
    };

    // ── General tab ─────────────────────────────────────────────────────────
    set('modalPropName',        field.name);
    set('modalPropType',        field.typeLabel || field.type || '');
    set('modalPropPage',        field.page !== undefined ? field.page + 1 : '');
    set('modalPropTooltip',     field.tooltip || '');
    set('modalPropVisibility',  field.visibility || 'visible');
    set('modalPropOrientation', String(field.orientation || 0));
    chk('modalPropReadOnly',    field.readOnly);
    chk('modalPropRequired',    field.required);
    chk('modalPropLocked',      field.locked);

    const radioGroupRow = document.getElementById('modalPropRadioGroupRow');
    if (radioGroupRow) {
        radioGroupRow.style.display = field.type === 'radio' ? 'block' : 'none';
        set('modalPropRadioGroup', field.radio_group || '');
    }

    // ── Appearance tab ──────────────────────────────────────────────────────
    swatch('modalPropBorderColor', field.style?.borderColor || field.borderColor || '#000000');
    swatch('modalPropFillColor',   field.style?.fillColor   || field.fillColor   || '#ffffff');
    set('modalPropLineThickness',  String(field.style?.lineThickness ?? field.lineThickness ?? 1));
    set('modalPropLineStyle',      field.style?.lineStyle   || field.lineStyle   || 'solid');
    set('modalPropFontSize',       field.style?.fontSize    || field.fontSize    || 9);
    swatch('modalPropTextColor',   field.style?.fontColor   || field.fontColor   || '#000000');
    set('modalPropFont',           field.style?.fontFamily  || field.fontFamily  || 'Helvetica');

    // ── Position tab ────────────────────────────────────────────────────────
    const rect = field.rect || [0, 0, 0, 0];
    set('modalPropLeft',   Math.round(rect[0] * 100) / 100);
    set('modalPropTop',    Math.round(rect[1] * 100) / 100);
    set('modalPropRight',  Math.round(rect[2] * 100) / 100);
    set('modalPropBottom', Math.round(rect[3] * 100) / 100);
    set('modalPropWidth',  Math.round((rect[2] - rect[0]) * 100) / 100);
    set('modalPropHeight', Math.round((rect[3] - rect[1]) * 100) / 100);
    chk('modalPropNoResize',      field.noResize);
    chk('modalPropPositionLocked', field.positionLocked);

    // ── Options tab ─────────────────────────────────────────────────────────
    set('modalPropAlignment',   field.style?.textAlign || field.textAlign || 'left');
    set('modalPropDefaultValue', field.defaultValue || '');
    chk('modalPropMultiLine',   field.multiLine);
    chk('modalPropScroll',      field.doNotScroll === false || field.scroll);
    chk('modalPropPassword',    field.password);

    // Show/hide field-type-specific sections
    const isText    = field.type === 'text' || field.type === 'date';
    const isChoice  = field.type === 'combobox' || field.type === 'listbox';
    const isCheck   = field.type === 'checkbox' || field.type === 'radio';

    const textOnlyRows = document.querySelectorAll('.text-only-row');
    textOnlyRows.forEach(r => r.style.display = isText ? '' : 'none');

    const checkOnlyRows = document.querySelectorAll('.check-only-row');
    checkOnlyRows.forEach(r => r.style.display = isCheck ? '' : 'none');

    const choiceSection = document.getElementById('propOptionsChoiceSection');
    if (choiceSection) choiceSection.style.display = isChoice ? '' : 'none';

    if (isCheck) {
        set('modalPropCheckStyle', field.style?.checkboxStyle || field.checkboxStyle || 'check');
    }

    if (isChoice) {
        propRenderOptions(field.options || []);
    }

    // ── Calculate tab: restore calc type, sources, and options ──────────────
    const calc = field.calculation;
    set('modalCalcType', calc?.type || '');
    _panelCalcSources = [];
    if (calc && Array.isArray(calc.sources)) {
        calc.sources.forEach(name => _panelCalcSources.push({ name, page: 0 }));
    }
    if (calc?.type === 'CONSTANT_MULTIPLY') set('calcConstantValue', calc.constant ?? '');
    if (calc?.type === 'CUSTOM')            set('calcCustomFormula', calc.formula || '');
    if (calc?.type === 'CUSTOM_SCRIPT')     set('calcCustomScriptCode', calc.script || '');
    chk('calcRoundResult',  calc?.roundResult || false);
    set('calcDecimalPlaces', calc?.decimals ?? '');
    renderPanelCalcSources();
    propUpdateCalcSections();
}

/** Switch the active Properties tab and remember it for the next field switch */
function propSwitchTab(tabName) {
    _currentPropTab = tabName;
    document.querySelectorAll('#propertiesPanelFloat .modal-tab').forEach(t => {
        t.classList.toggle('active', t.dataset.tab === tabName);
    });
    document.querySelectorAll('#propertiesPanelFloat .modal-tab-content').forEach(c => {
        c.classList.toggle('active', c.id === `tab-${tabName}`);
    });
}

/** Render dropdown/listbox options list */
function propRenderOptions(options) {
    const container = document.getElementById('propOptionsList');
    if (!container) return;
    if (!options.length) {
        container.innerHTML = '<div style="padding:8px;color:var(--text-secondary);font-size:11px;">No options yet</div>';
        return;
    }
    container.innerHTML = options.map((o, i) => {
        const label = typeof o === 'string' ? o : (o.label || o.value || '');
        const value = typeof o === 'string' ? o : (o.value || o.label || '');
        return `<div class="option-item" onclick="propSelectOption(${i})">
            <span class="option-label">${escapeHtml(label)}</span>
            <span class="option-value">${escapeHtml(value)}</span>
        </div>`;
    }).join('');
}
function propSelectOption(idx) {
    document.querySelectorAll('#propOptionsList .option-item').forEach((el, i) => {
        el.classList.toggle('selected', i === idx);
    });
}

/** Render the Properties > Calculate tab's source-fields list */
function renderPanelCalcSources() {
    const list = document.getElementById('calcSourceList');
    if (!list) return;
    if (_panelCalcSources.length === 0) {
        list.innerHTML = '<div class="calc-empty-msg" style="padding:6px;font-size:11px;color:var(--text-secondary);">No source fields added yet</div>';
    } else {
        list.innerHTML = _panelCalcSources.map((s, i) => {
            const letter = String.fromCharCode(65 + i);
            const safeName = s.name.replace(/'/g, "\\'");
            return `<div class="calc-source-item">
                <span class="source-letter">${letter}</span>
                <span class="source-name">${escapeHtml(s.name)}</span>
                <span class="source-page">P${(s.page || 0) + 1}</span>
                <span class="source-remove" onclick="propRemoveCalcSource('${safeName}')">&times;</span>
            </div>`;
        }).join('');
    }
    // Update formula legend
    const legend = document.getElementById('calcFormulaLegend');
    if (legend) {
        legend.innerHTML = _panelCalcSources.map((s, i) => {
            const letter = String.fromCharCode(65 + i);
            return `<span class="legend-item"><span class="legend-letter">${letter}</span> = ${escapeHtml(s.name)}</span>`;
        }).join('<br>');
    }
}

function propRemoveCalcSource(name) {
    _panelCalcSources = _panelCalcSources.filter(s => s.name !== name);
    renderPanelCalcSources();
    propSendUpdate();
}

/** Called when the editor picks a field and sends it back via IPC */
function propAddPickedCalcSource(fieldName) {
    if (!fieldName || _panelCalcSources.find(s => s.name === fieldName)) return;
    _panelCalcSources.push({ name: fieldName, page: 0 });
    renderPanelCalcSources();
    propSendUpdate();
    // Flash the list container briefly to show the new addition
    const list = document.getElementById('calcSourceList');
    if (list) {
        list.style.outline = '2px solid var(--accent)';
        setTimeout(() => { list.style.outline = ''; }, 600);
    }
}

/** Footer "Save" button — applies changes and stays open */
function savePropertiesModal() {
    propSendUpdate();
    // Brief visual feedback
    const btn = document.querySelector('#propertiesPanelFloat .modal-btn-primary');
    if (btn) {
        const orig = btn.textContent;
        btn.textContent = '✅ Saved';
        btn.disabled = true;
        setTimeout(() => { btn.textContent = orig; btn.disabled = false; }, 1000);
    }
}

/** Footer "Cancel" button — closes the floating window */
function closePropertiesModal() {
    window.close();
}

/** Collect current form values and push to the editor */
function propSendUpdate() {
    if (!_propCurrentField) return;
    const get  = id => { const el = document.getElementById(id); return el ? el.value : ''; };
    const chkd = id => { const el = document.getElementById(id); return el ? el.checked : false; };
    const col  = id => { const el = document.getElementById(id); return el ? el.value : '#000000'; };

    const rect = _propCurrentField.rect || [0, 0, 0, 0];
    const left   = parseFloat(get('modalPropLeft'))   || rect[0];
    const top    = parseFloat(get('modalPropTop'))    || rect[1];
    const right  = parseFloat(get('modalPropRight'))  || rect[2];
    const bottom = parseFloat(get('modalPropBottom')) || rect[3];

    const updates = {
        name: get('modalPropName'),
        rect: [left, top, right, bottom],
        props: {
            tooltip:      get('modalPropTooltip'),
            visibility:   get('modalPropVisibility'),
            orientation:  parseInt(get('modalPropOrientation')) || 0,
            readOnly:     chkd('modalPropReadOnly'),
            required:     chkd('modalPropRequired'),
            locked:       chkd('modalPropLocked'),
            // Appearance
            borderColor:  col('modalPropBorderColor'),
            fillColor:    col('modalPropFillColor'),
            lineThickness: parseInt(get('modalPropLineThickness')) || 1,
            lineStyle:    get('modalPropLineStyle'),
            fontSize:     parseFloat(get('modalPropFontSize')) || 9,
            fontColor:    col('modalPropTextColor'),
            fontFamily:   get('modalPropFont'),
            // Options
            textAlign:    get('modalPropAlignment'),
            defaultValue: get('modalPropDefaultValue'),
            multiLine:    chkd('modalPropMultiLine'),
            password:     chkd('modalPropPassword'),
        }
    };

    if (_propCurrentField.type === 'radio') {
        updates.radio_group = get('modalPropRadioGroup');
    }

    // ── Calculation data ─────────────────────────────────────────────────────
    const calcTypeVal = get('modalCalcType');
    updates.calculation = calcTypeVal
        ? {
            type:        calcTypeVal,
            sources:     _panelCalcSources.map(s => s.name),
            decimals:    parseInt(get('calcDecimalPlaces')) || 2,
            roundResult: chkd('calcRoundResult'),
            readOnly:    chkd('calcMakeReadOnly'),
            ...(calcTypeVal === 'CONSTANT_MULTIPLY' && { constant: parseFloat(get('calcConstantValue')) || 1 }),
            ...(calcTypeVal === 'CUSTOM'             && { formula:  get('calcCustomFormula') }),
            ...(calcTypeVal === 'CUSTOM_SCRIPT'      && { script:   get('calcCustomScriptCode') }),
          }
        : null;   // null = clear calculation

    // ── Action script data ───────────────────────────────────────────────────
    const trigger = get('modalActionTrigger');
    const script  = get('modalActionScript');
    if (trigger || script) {
        updates.actionTrigger = trigger;
        updates.actionScript  = script;
    }

    ipcRenderer.send('panel-to-editor', {
        action:   'apply-properties',
        fieldObj: _propCurrentField,
        updates,
    });
}

/** Wire all input/select/checkbox changes in the Properties panel to propSendUpdate */
function propWireChangeListeners() {
    const panel = document.getElementById('propertiesPanelFloat');
    if (!panel || panel._changeListenersWired) return;
    panel._changeListenersWired = true;

    const inputs = panel.querySelectorAll('input:not([type="radio"]), select, textarea');
    inputs.forEach(el => {
        el.addEventListener('change', propSendUpdate);
        if (el.tagName === 'INPUT' && (el.type === 'text' || el.type === 'number')) {
            el.addEventListener('blur', propSendUpdate);
        }
    });

    // ── Format tab: show/hide sub-option sections ────────────────────────
    const formatCat = document.getElementById('modalFormatCategory');
    if (formatCat) {
        formatCat.addEventListener('change', propUpdateFormatOptions);
        propUpdateFormatOptions();   // set initial state
    }

    // ── Calculate tab: show/hide sections based on type ──────────────────
    const calcType = document.getElementById('modalCalcType');
    if (calcType) {
        calcType.addEventListener('change', propUpdateCalcSections);
        propUpdateCalcSections();    // set initial state
    }

    // ── Actions tab: trigger-based textarea switching ─────────────────────
    const actionTrigger = document.getElementById('modalActionTrigger');
    const actionCode    = document.getElementById('modalActionScript');
    if (actionTrigger && actionCode) {
        actionTrigger.addEventListener('change', () => {
            // Save current textarea before switching (per-trigger storage)
            const prev = actionTrigger._lastTrigger || 'mouseUp';
            actionTrigger._scripts = actionTrigger._scripts || {};
            actionTrigger._scripts[prev] = actionCode.value;
            const next = actionTrigger.value;
            actionCode.value = actionTrigger._scripts[next] || '';
            actionTrigger._lastTrigger = next;
        });
    }
}

/** Show/hide Format tab sub-sections based on selected category */
function propUpdateFormatOptions() {
    const cat = document.getElementById('modalFormatCategory')?.value || 'none';
    const map = {
        number:   'modalFormatNumber',
        currency: 'modalFormatCurrency',
        date:     'modalFormatDate',
        phone:    'modalFormatPhone',
        zip:      'modalFormatZip',
    };
    Object.entries(map).forEach(([key, id]) => {
        const el = document.getElementById(id);
        if (el) el.style.display = (cat === key) ? '' : 'none';
    });
}

/** Show/hide Calculate tab sub-sections based on selected type */
function propUpdateCalcSections() {
    const type = document.getElementById('modalCalcType')?.value || '';

    const hasType    = type !== '';
    const needSource = hasType && type !== 'CUSTOM_SCRIPT';  // CONSTANT_MULTIPLY also needs source fields
    const needConst  = type === 'CONSTANT_MULTIPLY';
    const needCustom = type === 'CUSTOM';
    const needScript = type === 'CUSTOM_SCRIPT';

    const show = (id, visible) => {
        const el = document.getElementById(id);
        if (el) el.style.display = visible ? '' : 'none';
    };

    show('calcSourceSection',      needSource);
    show('calcConstantSection',    needConst);
    show('calcCustomSection',      needCustom);
    show('calcCustomScriptSection', needScript);
    show('calcOptionsSection',     hasType && !needScript);
    show('calcPreviewSection',     hasType);
    show('calcRemoveSection',      hasType);
}

// Wire listeners as soon as the panel is shown (called at end of initProperties)
document.addEventListener('DOMContentLoaded', () => {
    propWireChangeListeners();

    // ── Properties > General tab: Auto-Name button ──────────────────────────
    const btnAutoName = document.getElementById('btnAutoNameModal');
    if (btnAutoName) {
        btnAutoName.addEventListener('click', () => {
            if (!_propCurrentField) return;
            btnAutoName.innerHTML = '✨...';
            btnAutoName.disabled = true;
            ipcRenderer.send('panel-to-editor', {
                action: 'prop-auto-name',
                payload: { fieldName: _propCurrentField.name },
            });
            // Safety timeout — re-enable if editor never responds
            setTimeout(() => {
                btnAutoName.innerHTML = '✨ Auto Name';
                btnAutoName.disabled = false;
            }, 10000);
        });
    }

    // ── Properties > Calculate tab: Pick from Canvas button ─────────────────
    const btnPropPick = document.getElementById('btnPickFromCanvas');
    if (btnPropPick) {
        btnPropPick.addEventListener('click', () => {
            if (_panelPickActive) {
                // Stop pick mode
                _panelPickActive = false;
                btnPropPick.classList.remove('active');
                btnPropPick.textContent = '🎯 Click Fields on Canvas to Add';
                ipcRenderer.send('panel-to-editor', { action: 'calc-pick-stop', payload: {} });
            } else {
                // Start pick mode
                _panelPickActive = true;
                btnPropPick.classList.add('active');
                btnPropPick.textContent = '🎯 Picking… (click to stop)';
                ipcRenderer.send('panel-to-editor', { action: 'calc-pick-start', payload: {} });
            }
        });
    }

    // ── Properties > Calculate tab: Add Selected Fields from Canvas ──────────
    const btnAddSel = document.getElementById('btnAddCanvasSelection');
    if (btnAddSel) {
        btnAddSel.addEventListener('click', () => {
            ipcRenderer.send('panel-to-editor', { action: 'calc-add-selection', payload: {} });
        });
    }
    // Also hook color swatch buttons to open a native color picker
    document.querySelectorAll('.color-swatch-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const targetId = btn.dataset.target;
            const hidden   = document.getElementById(targetId);
            if (!hidden) return;
            const picker = document.createElement('input');
            picker.type  = 'color';
            picker.value = hidden.value || '#000000';
            picker.style.cssText = 'position:absolute;opacity:0;width:0;height:0;';
            document.body.appendChild(picker);
            picker.addEventListener('input', () => {
                const preview = document.getElementById(`preview_${targetId}`);
                const text    = document.getElementById(`text_${targetId}`);
                hidden.value           = picker.value;
                if (preview) preview.style.background = picker.value;
                if (text)    text.textContent          = picker.value;
            });
            picker.addEventListener('change', () => {
                propSendUpdate();
                picker.remove();
            });
            picker.click();
        });
    });
});





// ════════════════════════════════════════════════════════════════════════════
//  VERSION HISTORY PANEL
// ════════════════════════════════════════════════════════════════════════════

let histSnapshots = [];

function initHistory(data) {
    document.getElementById('panelTitle').textContent = '🕐 Version History';
    const panel = document.getElementById('historyPanel');
    panel.style.display       = 'flex';
    panel.style.flex          = '1';
    panel.style.flexDirection = 'column';
    panel.style.overflow      = 'hidden';

    histSnapshots = data?.snapshots || [];
    histRender();
}

function histRefresh(data) {
    histSnapshots = data?.snapshots || [];
    histRender();
}

function histRender() {
    const container = document.getElementById('histSnapshotsList');
    if (!container) return;

    if (histSnapshots.length === 0) {
        container.innerHTML = `
            <div style="text-align:center;padding:40px 20px;color:var(--text-secondary);">
                <div style="font-size:32px;margin-bottom:10px;">📷</div>
                <div>No snapshots yet</div>
                <div style="margin-top:5px;font-size:11px;">Create a snapshot to save the current state</div>
            </div>`;
        return;
    }

    container.innerHTML = histSnapshots.map((s, idx) => `
        <div style="display:flex;align-items:center;padding:10px;background:var(--bg-tertiary);border-radius:6px;margin-bottom:6px;border:1px solid var(--border);">
            <div style="flex:1;min-width:0;">
                <div style="font-weight:600;color:var(--text-primary);font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${escapeHtml(s.name)}</div>
                <div style="font-size:10px;color:var(--text-secondary);margin-top:2px;">${escapeHtml(s.timestamp)} · ${s.fieldsCount} fields</div>
            </div>
            <div style="display:flex;gap:4px;flex-shrink:0;margin-left:8px;">
                <button class="btn btn-secondary" style="padding:5px 9px;font-size:10px;" onclick="histCompare(${idx})" title="Compare with current">🔍</button>
                <button class="btn btn-primary"   style="padding:5px 9px;font-size:10px;" onclick="histRestore(${idx})" title="Restore this snapshot">⏪</button>
                <button class="btn btn-danger"    style="padding:5px 9px;font-size:10px;" onclick="histDelete(${idx})" title="Delete">🗑</button>
            </div>
        </div>`).join('');
}

async function histCreateSnapshot() {
    const name = await panelPrompt('Enter snapshot name:', `Snapshot ${histSnapshots.length + 1}`);
    if (!name) return;

    ipcRenderer.send('panel-to-editor', {
        action: 'hist-create',
        payload: { name }
    });

    // Visual feedback on the button
    const btn = document.getElementById('histCreateBtn');
    if (btn) {
        const orig = btn.textContent;
        btn.textContent = '✅ Saved!';
        btn.disabled    = true;
        setTimeout(() => { btn.textContent = orig; btn.disabled = false; }, 1400);
    }
}

function histRestore(idx) {
    ipcRenderer.send('panel-to-editor', { action: 'hist-restore', payload: { idx } });
}
function histDelete(idx) {
    ipcRenderer.send('panel-to-editor', { action: 'hist-delete',  payload: { idx } });
}
function histCompare(idx) {
    ipcRenderer.send('panel-to-editor', { action: 'hist-compare', payload: { idx } });
}


// ════════════════════════════════════════════════════════════════════════════
//  CALCULATIONS MANAGER PANEL
// ════════════════════════════════════════════════════════════════════════════

let calcData = { calcs: [], order: [] };

function initCalc(data) {
    document.getElementById('panelTitle').textContent = '🧮 Calculations Manager';
    const panel = document.getElementById('calcPanel');
    panel.style.display       = 'flex';
    panel.style.flex          = '1';
    panel.style.flexDirection = 'column';
    panel.style.overflow      = 'hidden';

    calcData = data || { calcs: [], order: [] };
    calcRender();
}

function calcRefresh(data) {
    calcData = data || { calcs: [], order: [] };
    calcRender();
}

function calcRender() {
    const filter = (document.getElementById('calcPanelFilter')?.value || '').toLowerCase();
    const entries = (calcData.calcs || []).filter(c =>
        !filter || c.name.toLowerCase().includes(filter)
    );

    const list = document.getElementById('calcPanelList');
    if (!list) return;

    if (entries.length === 0) {
        list.innerHTML = '<div style="padding:14px;text-align:center;color:var(--text-secondary);font-size:11px;">No calculations configured yet</div>';
    } else {
        list.innerHTML = `
            <table style="width:100%;border-collapse:collapse;font-size:11px;">
                <thead>
                    <tr style="background:var(--bg-tertiary);">
                        <th style="text-align:left;padding:7px 10px;color:var(--text-secondary);font-weight:500;">Target Field</th>
                        <th style="text-align:left;padding:7px 10px;color:var(--text-secondary);font-weight:500;width:90px;">Type</th>
                        <th style="text-align:left;padding:7px 10px;color:var(--text-secondary);font-weight:500;">Sources</th>
                        <th style="text-align:center;padding:7px 10px;color:var(--text-secondary);font-weight:500;width:70px;">Actions</th>
                    </tr>
                </thead>
                <tbody>` + entries.map(c => {
                    const srcText = (c.sources || []).length <= 3
                        ? (c.sources || []).join(', ')
                        : `${c.sources.length} fields`;
                    const typeLabel = c.type === 'CONSTANT_MULTIPLY' ? `×${c.constant || 1}` : c.type;
                    const safeName  = c.name.replace(/'/g, "\\'");
                    return `<tr style="border-top:1px solid var(--border);">
                        <td style="padding:6px 10px;color:var(--text-primary);white-space:nowrap;">${escapeHtml(c.name)}</td>
                        <td style="padding:6px 10px;color:var(--accent);font-weight:500;white-space:nowrap;">${escapeHtml(typeLabel)}</td>
                        <td style="padding:6px 10px;color:var(--text-secondary);font-size:10px;">${escapeHtml(srcText)}</td>
                        <td style="padding:6px 10px;text-align:center;white-space:nowrap;">
                            <span style="cursor:pointer;color:var(--accent);font-size:13px;margin-right:6px;" onclick="calcEdit('${safeName}')" title="Edit">🔧</span>
                            <span style="cursor:pointer;color:var(--danger);font-size:13px;" onclick="calcDelete('${safeName}')" title="Remove">🗑</span>
                        </td>
                    </tr>`;
                }).join('') + `</tbody></table>`;
    }

    // Order list
    const orderEl = document.getElementById('calcPanelOrder');
    if (orderEl) {
        const order = calcData.order || [];
        if (order.length === 0) {
            orderEl.innerHTML = '<div style="padding:12px;text-align:center;color:var(--text-secondary);font-size:11px;">No calculations to order</div>';
        } else {
            orderEl.innerHTML = order.map((o, i) => `
                <div style="display:flex;align-items:center;padding:6px 10px;border-bottom:1px solid var(--border);gap:8px;">
                    <span style="color:var(--text-secondary);font-size:10px;min-width:20px;">${i + 1}.</span>
                    <span style="flex:1;color:var(--text-primary);font-size:11px;">${escapeHtml(o.name)}</span>
                    <span style="font-size:10px;color:var(--accent);">${escapeHtml(o.type)}</span>
                </div>`).join('');
        }
    }
}

function calcEdit(name) {
    ipcRenderer.send('panel-to-editor', { action: 'calc-edit',   payload: { name } });
}
function calcDelete(name) {
    ipcRenderer.send('panel-to-editor', { action: 'calc-delete', payload: { name } });
}
function calcAutoDetect() {
    const btn = document.getElementById('calcAutoDetectBtn');
    if (btn) { btn.textContent = 'Scanning…'; btn.disabled = true; }
    ipcRenderer.send('panel-to-editor', { action: 'calc-auto-detect', payload: {} });
}
function calcRenderSuggestions(suggestions) {
    const btn = document.getElementById('calcAutoDetectBtn');
    if (btn) { btn.textContent = 'Scan Now'; btn.disabled = false; }

    const host = document.getElementById('calcAutoDetectResults');
    if (!host) return;

    if (!suggestions.length) {
        host.style.display = 'block';
        host.innerHTML = '<div style="padding:8px;font-size:11px;color:var(--text-secondary);">No calculation patterns detected.</div>';
        setTimeout(() => { if (host) host.style.display = 'none'; }, 3000);
        return;
    }

    host.style.display = 'block';
    host.innerHTML = `
        <div style="font-size:11px;color:var(--text-secondary);margin-bottom:6px;">Found ${suggestions.length} suggestion(s):</div>
        <div style="max-height:180px;overflow-y:auto;display:flex;flex-direction:column;gap:6px;">` +
        suggestions.map((s, i) => `
            <div style="background:var(--bg-tertiary);border:1px solid var(--border);border-radius:4px;padding:8px;display:flex;align-items:center;gap:8px;">
                <div style="flex:1;min-width:0;">
                    <div style="font-size:11px;color:var(--text-primary);font-weight:600;">${escapeHtml(s.target)}</div>
                    <div style="font-size:10px;color:var(--text-secondary);margin-top:2px;">${escapeHtml(s.type)} · ${escapeHtml(s.reason || '')}</div>
                </div>
                <button class="btn btn-primary" style="padding:4px 10px;font-size:10px;" onclick="calcApplySuggestion(${i})">Apply</button>
            </div>`).join('') + `</div>
        <div style="margin-top:8px;display:flex;gap:6px;">
            <button class="btn btn-secondary" style="padding:5px 10px;font-size:10px;" onclick="calcApplyAll()">Apply All</button>
            <button class="btn btn-secondary" style="padding:5px 10px;font-size:10px;" onclick="document.getElementById('calcAutoDetectResults').style.display='none';">Dismiss</button>
        </div>`;

    // Cache suggestions locally for apply calls
    calcData._lastSuggestions = suggestions;
}
function calcApplySuggestion(idx) {
    const sugg = (calcData._lastSuggestions || [])[idx];
    if (!sugg) return;
    ipcRenderer.send('panel-to-editor', { action: 'calc-apply-suggestion', payload: { suggestion: sugg } });
}
function calcApplyAll() {
    const all = calcData._lastSuggestions || [];
    if (!all.length) return;
    ipcRenderer.send('panel-to-editor', { action: 'calc-apply-suggestions', payload: { suggestions: all } });
}


// ════════════════════════════════════════════════════════════════════════════
//  HYPERLINKS MANAGER PANEL
// ════════════════════════════════════════════════════════════════════════════

let hypData = { links: [] };

function initHyp(data) {
    document.getElementById('panelTitle').textContent = '🔗 Hyperlinks Manager';
    const panel = document.getElementById('hypPanel');
    panel.style.display       = 'flex';
    panel.style.flex          = '1';
    panel.style.flexDirection = 'column';
    panel.style.overflow      = 'hidden';

    hypData = data || { links: [] };
    hypRender();
}

function hypRefresh(data) {
    hypData = data || { links: [] };
    hypRender();
}

function hypRender() {
    const filter     = (document.getElementById('hypPanelFilter')?.value || '').toLowerCase();
    const activeName = hypData.activeFieldName || null;
    const entries    = (hypData.links || []).filter(l =>
        !filter ||
        (l.fieldName || '').toLowerCase().includes(filter) ||
        (l.destination || '').toLowerCase().includes(filter)
    );

    const list = document.getElementById('hypPanelList');
    if (!list) return;

    if (entries.length === 0) {
        list.innerHTML = '<div style="padding:14px;text-align:center;color:var(--text-secondary);font-size:11px;">No hyperlinks configured</div>';
        return;
    }

    list.innerHTML = `
        <table style="width:100%;border-collapse:collapse;font-size:11px;">
            <thead>
                <tr style="background:var(--bg-tertiary);">
                    <th style="text-align:left;padding:7px 10px;color:var(--text-secondary);font-weight:500;">Trigger Field</th>
                    <th style="text-align:left;padding:7px 10px;color:var(--text-secondary);font-weight:500;width:70px;">Type</th>
                    <th style="text-align:left;padding:7px 10px;color:var(--text-secondary);font-weight:500;">Destination</th>
                    <th style="text-align:center;padding:7px 10px;color:var(--text-secondary);font-weight:500;width:70px;">Actions</th>
                </tr>
            </thead>
            <tbody>` + entries.map(l => {
                const safeName  = (l.fieldName || '').replace(/'/g, "\\'");
                const isActive  = l.fieldName === activeName;
                const rowStyle  = isActive
                    ? 'border-top:1px solid var(--border);background:rgba(100,181,246,0.12);box-shadow:inset 3px 0 0 #64b5f6;'
                    : 'border-top:1px solid var(--border);';
                const destShort = (l.destination || '').length > 40
                    ? l.destination.slice(0, 37) + '…'
                    : (l.destination || '');
                return `<tr data-hyp-name="${escapeHtml(l.fieldName || '')}" style="${rowStyle}">
                    <td style="padding:6px 10px;color:var(--text-primary);white-space:nowrap;">${escapeHtml(l.fieldName || '')}</td>
                    <td style="padding:6px 10px;color:var(--accent);font-weight:500;">${escapeHtml(l.destType || '')}</td>
                    <td style="padding:6px 10px;color:var(--text-secondary);font-size:10px;word-break:break-all;">${escapeHtml(destShort)}</td>
                    <td style="padding:6px 10px;text-align:center;white-space:nowrap;">
                        <span style="cursor:pointer;color:var(--accent);font-size:13px;margin-right:6px;" onclick="hypEdit('${safeName}')" title="Edit">🔧</span>
                        <span style="cursor:pointer;color:var(--danger);font-size:13px;" onclick="hypDelete('${safeName}')" title="Remove">🗑</span>
                    </td>
                </tr>`;
            }).join('') + `</tbody></table>`;

    // Scroll the highlighted row into the centre of the visible area
    if (activeName) {
        requestAnimationFrame(() => {
            const activeRow = list.querySelector(`tr[data-hyp-name="${CSS.escape(activeName)}"]`);
            if (activeRow) activeRow.scrollIntoView({ block: 'center', behavior: 'smooth' });
        });
    }
}

function hypEdit(fieldName) {
    const link = (hypData.links || []).find(l => l.fieldName === fieldName);
    if (!link) return;
    
    // Notify editor to select the field on the canvas
    ipcRenderer.send('panel-to-editor', { action: 'hyp-edit',   payload: { fieldName } });
    
    document.getElementById('hypPanelEditName').textContent = fieldName;
    document.getElementById('hypPanelEditName').dataset.fieldName = fieldName;
    
    // Set dest type
    const destType = link.destTypeRaw || 'url';
    const radios = document.getElementsByName('hypPanelDestType');
    radios.forEach(r => r.checked = (r.value === destType));
    
    // Set url
    document.getElementById('hypPanelEditTargetInput').value = link.editValue || '';
    
    // Set appearance
    document.getElementById('hypPanelLinkType').value = link.linkType || 'invisible';
    document.getElementById('hypPanelHighlight').value = link.linkHighlight || 'I';
    document.getElementById('hypPanelBorderW').value = (link.linkBorderWidth || 1).toString();
    document.getElementById('hypPanelBorderS').value = link.linkBorderStyle || 'S';
    document.getElementById('hypPanelBorderColor').value = link.linkBorderColor || '#0066cc';
    
    hypEditHint();
    hypUpdateAppUI();
    
    document.getElementById('hypPanelEditPane').style.display = 'block';
    
    // Scroll it into view
    document.getElementById('hypPanelEditPane').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function hypCloseEdit() {
    document.getElementById('hypPanelEditPane').style.display = 'none';
    delete document.getElementById('hypPanelEditName').dataset.fieldName;
}

function hypEditHint() {
    const destTypeOptions = document.getElementsByName('hypPanelDestType');
    let destType = 'url';
    destTypeOptions.forEach(r => { if (r.checked) destType = r.value; });
    
    const label = document.getElementById('hypPanelEditTargetLabel');
    const input = document.getElementById('hypPanelEditTargetInput');
    const hint = document.getElementById('hypPanelEditHint');
    
    if (destType === 'goto') {
        label.textContent = 'Page Number';
        input.placeholder = 'e.g. 5';
        hint.textContent  = 'Enter the page number to jump to (first page is 1).';
    } else if (destType === 'url') {
        label.textContent = 'URL';
        input.placeholder = 'https://example.com';
        hint.textContent  = 'Will be opened in the default web browser.';
    } else if (destType === 'goto_remote') {
        label.textContent = 'File Path / URL';
        input.placeholder = 'document.pdf';
        hint.textContent  = 'Path or URL to another PDF document.';
    } else if (destType === 'dest') {
        label.textContent = 'Named Destination';
        input.placeholder = 'Chapter1';
        hint.textContent  = 'Name of a destination defined in the PDF.';
    }
}

function hypUpdateAppUI() {
    const linkType = document.getElementById('hypPanelLinkType').value;
    const highlight = document.getElementById('hypPanelHighlight').value;
    const borderWrap = document.getElementById('hypPanelBorderWrap');
    
    if (linkType === 'invisible') {
        borderWrap.style.display = 'none';
    } else {
        borderWrap.style.display = 'block';
    }
    
    // Update summary text
    let summary = linkType === 'invisible' ? 'Invisible Rectangle' : 'Visible Rectangle';
    const highlightMap = { N: 'None', I: 'Invert', O: 'Outline', P: 'Inset' };
    summary += ' · ' + (highlightMap[highlight] || 'None');
    
    document.getElementById('hypPanelAppSummary').textContent = summary;
}

function hypSave() {
    const fieldName = document.getElementById('hypPanelEditName').dataset.fieldName;
    if (!fieldName) return;
    
    let destType = 'url';
    document.getElementsByName('hypPanelDestType').forEach(r => { if (r.checked) destType = r.value; });
    
    const url = document.getElementById('hypPanelEditTargetInput').value;
    const linkType = document.getElementById('hypPanelLinkType').value;
    const linkHighlight = document.getElementById('hypPanelHighlight').value;
    const linkBorderWidth = parseInt(document.getElementById('hypPanelBorderW').value, 10);
    const linkBorderStyle = document.getElementById('hypPanelBorderS').value;
    const linkBorderColor = document.getElementById('hypPanelBorderColor').value;
    
    ipcRenderer.send('panel-to-editor', {
        action: 'hyp-save',
        payload: {
            fieldName,
            destType,
            url,
            linkType,
            linkHighlight,
            linkBorderWidth,
            linkBorderStyle,
            linkBorderColor
        }
    });
    
    // Brief visual feedback on save
    const btn = document.querySelector('#hypPanelEditPane .btn-primary');
    if (btn) {
        const orig = btn.textContent;
        btn.textContent = '✅ Saved';
        btn.disabled = true;
        setTimeout(() => {
            if (btn) {
                btn.textContent = orig;
                btn.disabled = false;
            }
        }, 1200);
    }
}

function hypDelete(fieldName) {
    ipcRenderer.send('panel-to-editor', { action: 'hyp-delete', payload: { fieldName } });
}
function hypAddNew() {
    ipcRenderer.send('panel-to-editor', { action: 'hyp-new',    payload: {} });
}


// ════════════════════════════════════════════════════════════════════════════
//  SHARED HELPERS
// ════════════════════════════════════════════════════════════════════════════

function escapeHtml(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// ════════════════════════════════════════════════════════════════════════════
//  JAVASCRIPT SNIPPET LIBRARY  (JS_PRESETS and _jsLibCurrentPreset are
//  declared in panel_properties.js which loads first — no re-declaration here)
// ════════════════════════════════════════════════════════════════════════════
//  JS_PRESETS = [ ... ]  defined in panel_properties.js (loads before panel.js)


// ── JS Library helpers ────────────────────────────────────────────────────────
// (JS_PRESETS and _jsLibCurrentPreset are defined in panel_properties.js which
//  is loaded before this file — using those shared globals directly)

function jsLibInit() {
    const catSel = document.getElementById('jsLibCategory');
    if (!catSel) return;
    const cats = [...new Set(JS_PRESETS.map(p => p.category))];
    catSel.innerHTML = '<option value="">All Categories</option>' +
        cats.map(c => `<option value="${c}">${c}</option>`).join('');
    jsLibUpdateDropdowns();
}

function jsLibUpdateDropdowns() {
    const search  = (document.getElementById('jsLibSearch')?.value || '').toLowerCase().trim();
    const catSel  = document.getElementById('jsLibCategory');
    const presSel = document.getElementById('jsLibPreset');
    if (!catSel || !presSel) return;
    const selCat = catSel.value;
    const matches = JS_PRESETS.filter(p => {
        const catMatch = !selCat || p.category === selCat;
        if (!search) return catMatch;
        return catMatch && (
            p.name.toLowerCase().includes(search) ||
            p.description.toLowerCase().includes(search) ||
            (p.tags || []).some(t => t.includes(search)) ||
            p.category.toLowerCase().includes(search)
        );
    });
    if (search) {
        const matchingCats = [...new Set(matches.map(m => m.category))];
        const allCats = [...new Set(JS_PRESETS.map(p => p.category))];
        catSel.innerHTML = '<option value="">All Categories</option>' +
            allCats.map(c => `<option value="${c}"${c === selCat ? ' selected' : ''}${!matchingCats.includes(c) ? ' style="color:var(--text-secondary)"' : ''}>${c}${!matchingCats.includes(c) ? ' (0)' : ` (${matches.filter(m => m.category === c).length})`}</option>`).join('');
    }
    const prevPreset = presSel.value;
    presSel.innerHTML = `<option value="">-- Select a preset${search ? ` (${matches.length} match${matches.length !== 1 ? 'es' : ''})` : ''} --</option>` +
        matches.map(p => `<option value="${p.id}"${p.id === prevPreset ? ' selected' : ''}>${p.name}</option>`).join('');
    const stillValid = matches.find(p => p.id === prevPreset);
    if (!stillValid) {
        _jsLibCurrentPreset = null;
        jsLibClearPanel();
    } else {
        jsLibOnPresetChange();
    }
}

function jsLibOnCategoryChange() { jsLibUpdateDropdowns(); }

function jsLibOnPresetChange() {
    const presSel = document.getElementById('jsLibPreset');
    const preset  = JS_PRESETS.find(p => p.id === presSel?.value);
    _jsLibCurrentPreset = preset || null;
    const descEl    = document.getElementById('jsLibDesc');
    const paramsEl  = document.getElementById('jsLibParams');
    const insertBtn = document.getElementById('jsLibInsertBtn');
    if (!preset) { jsLibClearPanel(); return; }
    if (descEl) descEl.textContent = preset.description;
    if (paramsEl) {
        if (preset.params.length > 0) {
            paramsEl.style.display = 'block';
            paramsEl.innerHTML = preset.params.map(param => `
                <div style="margin-bottom: 8px; display: flex; align-items: center; gap: 8px;">
                    <label style="font-size: 11px; color: var(--text-secondary); width: 130px; flex-shrink: 0;">${param.label}:</label>
                    <input type="${param.type || 'text'}" id="jsLibParam_${param.id}"
                           class="modal-input"
                           placeholder="${(param.placeholder || '').replace(/"/g, '&quot;')}"
                           ${param.min !== undefined ? `min="${param.min}"` : ''}
                           style="flex: 1; font-size: 12px; padding: 4px 8px;"
                           oninput="jsLibRefreshPreview()">
                </div>
            `).join('');
        } else {
            paramsEl.style.display = 'none';
            paramsEl.innerHTML = '';
        }
    }
    jsLibRefreshPreview();
    if (insertBtn) { insertBtn.disabled = false; insertBtn.style.opacity = '1'; }
}

function jsLibClearPanel() {
    const descEl    = document.getElementById('jsLibDesc');
    const paramsEl  = document.getElementById('jsLibParams');
    const previewEl = document.getElementById('jsLibPreview');
    const insertBtn = document.getElementById('jsLibInsertBtn');
    if (descEl)    descEl.textContent = '';
    if (paramsEl)  { paramsEl.style.display = 'none'; paramsEl.innerHTML = ''; }
    if (previewEl) previewEl.textContent = '';
    if (insertBtn) { insertBtn.disabled = true; insertBtn.style.opacity = '0.5'; }
}

function jsLibGetParams() {
    const preset = _jsLibCurrentPreset;
    if (!preset) return {};
    const vals = {};
    for (const param of preset.params) {
        const el = document.getElementById(`jsLibParam_${param.id}`);
        vals[param.id] = el ? el.value : (param.placeholder || '');
    }
    return vals;
}

function jsLibRefreshPreview() {
    const previewEl = document.getElementById('jsLibPreview');
    if (!previewEl || !_jsLibCurrentPreset) return;
    try {
        const params = jsLibGetParams();
        const previewParams = {};
        for (const param of _jsLibCurrentPreset.params) {
            previewParams[param.id] = params[param.id] || param.placeholder || `<${param.label}>`;
        }
        previewEl.textContent = _jsLibCurrentPreset.template(previewParams);
    } catch (e) {
        previewEl.textContent = '// (preview error)';
    }
}

function jsLibInsert() {
    if (!_jsLibCurrentPreset) return;
    const params = jsLibGetParams();
    for (const param of _jsLibCurrentPreset.params) {
        if (!params[param.id] || !params[param.id].trim()) {
            panelPrompt(`Please fill in the "${param.label}" field before inserting.`).then(() => {
                document.getElementById(`jsLibParam_${param.id}`)?.focus();
            });
            return;
        }
    }
    let code;
    try {
        code = _jsLibCurrentPreset.template(params);
    } catch (e) {
        return;
    }
    const textarea = document.getElementById('modalActionScript');
    if (!textarea) return;
    const existing = textarea.value.trim();
    textarea.value = existing ? existing + '\n\n' + code : code;
    textarea.scrollTop = textarea.scrollHeight;
    textarea.style.outline = '2px solid var(--accent)';
    setTimeout(() => { textarea.style.outline = ''; }, 600);
}

/** Promise-based inline prompt (custom replacement for window.prompt, which is disabled in Electron) */
function panelPrompt(titleText, defaultValue = '') {
    return new Promise((resolve) => {
        const overlay = document.getElementById('pnlPromptOverlay');
        const input   = document.getElementById('pnlPromptInput');
        const title   = document.getElementById('pnlPromptTitle');
        const btnOk   = document.getElementById('pnlPromptOk');
        const btnCan  = document.getElementById('pnlPromptCancel');

        title.textContent = titleText;
        input.value       = defaultValue;

        overlay.style.display = 'flex';
        setTimeout(() => { input.focus(); input.select(); }, 50);

        const cleanup = (val) => {
            overlay.style.display = 'none';
            btnOk.onclick   = null;
            btnCan.onclick  = null;
            input.onkeydown = null;
            resolve(val);
        };
        btnOk.onclick  = () => cleanup(input.value.trim());
        btnCan.onclick = () => cleanup(null);
        input.onkeydown = (e) => {
            if (e.key === 'Enter')  cleanup(input.value.trim());
            if (e.key === 'Escape') cleanup(null);
        };
    });
}
