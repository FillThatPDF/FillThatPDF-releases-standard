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
            if (data?.multiFields && data.multiFields.length > 1) {
                propPopulateMulti(data.multiFields, data.globalSettings);
            } else {
                propPopulate(data?.field, data?.globalSettings);
                // Use requestTab if the editor explicitly asked for a tab (e.g. edit from Calc Manager),
                // otherwise restore whichever tab the user was already on
                if (data?.field) propSwitchTab(data?.requestTab || _currentPropTab || 'general');
            }
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
        showTooltips:      { enabled: document.getElementById('enableShowTooltips').checked,      value: document.getElementById('gsShowTooltips').value },
        useNameAsTooltip:  { enabled: document.getElementById('enableUseNameAsTooltip').checked },
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
let _propMultiFields     = null;   // array of fields when multi-selected
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
    if (data?.multiFields && data.multiFields.length > 1) {
        propPopulateMulti(data.multiFields, _propGlobalSettings);
    } else {
        propPopulate(data?.field, _propGlobalSettings);
        // Land on the requested tab, or General if none specified
        propSwitchTab(data?.requestTab || 'general');
    }
}

/** Called when multiple fields are selected */
function propPopulateMulti(multiFields, globalSettings) {
    _propMultiFields    = multiFields || [];
    _propCurrentField   = null;
    _propGlobalSettings = globalSettings || {};

    const nameEl = document.getElementById('modalFieldName');
    const count  = _propMultiFields.length;
    if (nameEl) nameEl.textContent = `${count} Fields Selected`;

    // Dim tabs that don't support batch editing; enable General, Appearance & Options
    document.querySelectorAll('.modal-tab').forEach(t => {
        const batchable = ['general', 'appearance', 'options'].includes(t.dataset.tab);
        t.style.opacity = batchable ? '' : '0.4';
        t.style.pointerEvents = batchable ? '' : 'none';
    });

    // Pre-populate Appearance with first field's values as a reference
    if (_propMultiFields.length > 0) {
        const ref = _propMultiFields[0];
        // Same transparent-aware swatch as propPopulate — empty string,
        // null, undefined, or 'transparent' render as checker pattern.
        const swatch = (base, hex) => {
            const prev   = document.getElementById(`preview_${base}`);
            const text   = document.getElementById(`text_${base}`);
            const hidden = document.getElementById(base);
            const isTransparent = (hex === '' || hex == null || hex === 'transparent');
            const value = isTransparent ? 'transparent' : hex;
            if (prev) {
                if (isTransparent) {
                    prev.style.background = 'transparent';
                    prev.style.backgroundImage = 'linear-gradient(45deg, #ccc 25%, transparent 25%), linear-gradient(-45deg, #ccc 25%, transparent 25%), linear-gradient(45deg, transparent 75%, #ccc 75%), linear-gradient(-45deg, transparent 75%, #ccc 75%)';
                    prev.style.backgroundSize = '8px 8px';
                } else {
                    prev.style.background = value;
                    prev.style.backgroundImage = 'none';
                }
            }
            if (text) text.textContent = isTransparent ? 'None' : String(value).toUpperCase();
            if (hidden) hidden.value = value;
        };
        const pickColor = (styled, extracted, dflt, transparentFallback) => {
            if (styled !== undefined) return styled;
            if (extracted === '' || extracted === 'transparent' || extracted == null) {
                return transparentFallback ? 'transparent' : dflt;
            }
            return extracted;
        };
        const set = (id, val) => { const el = document.getElementById(id); if (el) el.value = (val ?? ''); };
        swatch('modalPropBorderColor', pickColor(ref.style?.borderColor, ref.borderColor, '#000000', true));
        swatch('modalPropFillColor',   pickColor(ref.style?.fillColor,   ref.fillColor,   'transparent', true));
        set('modalPropLineThickness',  String(ref.style?.lineThickness ?? ref.lineThickness ?? 1));
        set('modalPropLineStyle',      ref.style?.lineStyle   || ref.lineStyle   || 'solid');
        set('modalPropFontSize',       ref.style?.fontSize    || ref.fontSize    || 9);
        swatch('modalPropTextColor',   pickColor(ref.style?.fontColor, ref.fontColor, '#000000', false));
        set('modalPropFont',           ref.style?.fontFamily  || ref.fontFamily  || 'Helvetica');
        // General — tooltip visibility (based on first field's state)
        set('modalPropShowTooltip', ref.tooltipDisabled ? 'no' : 'yes');
        // Options
        const set2 = (id, bool) => { const el = document.getElementById(id); if (el) el.checked = !!bool; };
        set2('modalPropReadOnly', ref.readOnly);
        set2('modalPropRequired', ref.required);
        set2('modalPropLocked',   ref.locked);
    }

    // Switch to General tab so the tooltip toggle is immediately visible
    propSwitchTab('general');
}

/** Called by panel-update when editor selection changes */
function propPopulate(field, globalSettings) {
    _propMultiFields    = null;   // clear any multi state
    _propCurrentField   = field;
    _propGlobalSettings = globalSettings || {};

    // Restore all tab interactivity (may have been limited by multi-select)
    document.querySelectorAll('.modal-tab').forEach(t => {
        t.style.opacity = '';
        t.style.pointerEvents = '';
    });

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
    // Fill / border / font colour swatch. Handles the "transparent" sentinel
    // (empty string from extract_fields.py, null, undefined, or literal
    // "transparent") by rendering a checker pattern and storing 'transparent'
    // in the hidden input, rather than silently coercing to '#000000' or
    // '#ffffff' like an earlier version did. Without this, transparent text
    // fields showed up in the picker as #FFFFFF.
    const swatch = (base, hex) => {
        const prev = document.getElementById(`preview_${base}`);
        const text = document.getElementById(`text_${base}`);
        const hidden = document.getElementById(base);
        const isTransparent = (hex === '' || hex == null || hex === 'transparent');
        const value = isTransparent ? 'transparent' : hex;
        if (prev) {
            if (isTransparent) {
                prev.style.background = 'transparent';
                prev.style.backgroundImage = 'linear-gradient(45deg, #ccc 25%, transparent 25%), linear-gradient(-45deg, #ccc 25%, transparent 25%), linear-gradient(45deg, transparent 75%, #ccc 75%), linear-gradient(-45deg, transparent 75%, #ccc 75%)';
                prev.style.backgroundSize = '8px 8px';
            } else {
                prev.style.background = value;
                prev.style.backgroundImage = 'none';
            }
        }
        if (text) text.textContent = isTransparent ? 'None' : String(value).toUpperCase();
        if (hidden) hidden.value = value;
    };

    // ── General tab ─────────────────────────────────────────────────────────
    set('modalPropName',        field.name);
    set('modalPropType',        field.typeLabel || field.type || '');
    set('modalPropPage',        field.page !== undefined ? field.page + 1 : '');
    set('modalPropShowTooltip', field.tooltipDisabled ? 'no' : 'yes');
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
    //
    // Fill / border colour resolution:
    //   1. user-edited style wins
    //   2. literal 'transparent' or empty string from extract_fields.py →
    //      render as transparent (checker pattern)
    //   3. any other extracted hex → use it
    //   4. no extraction at all → use global default, or fall back to 'transparent'
    //      rather than inventing a white fill that the field never actually had.
    const pickColor = (styled, extracted, globalDefault, transparentFallback) => {
        if (styled !== undefined) return styled;
        if (extracted === '' || extracted === 'transparent' || extracted == null) {
            return transparentFallback ? 'transparent' : globalDefault;
        }
        return extracted;
    };
    const gs = globalSettings || {};
    swatch('modalPropBorderColor',
        pickColor(field.style?.borderColor, field.borderColor, gs.borderColor || '#000000', true));
    swatch('modalPropFillColor',
        pickColor(field.style?.fillColor,   field.fillColor,   gs.fillColor   || 'transparent', true));
    set('modalPropLineThickness',  String(field.style?.lineThickness ?? field.lineThickness ?? 1));
    set('modalPropLineStyle',      field.style?.lineStyle   || field.lineStyle   || 'solid');
    set('modalPropFontSize',       field.style?.fontSize    || field.fontSize    || 9);
    swatch('modalPropTextColor',
        pickColor(field.style?.fontColor,   field.fontColor,   gs.fontColor   || '#000000', false));
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
    chk('modalPropMultiline',   field.style?.multiline  ?? field.multiline  ?? field.multiLine  ?? false);
    chk('modalPropScrollText',  field.style?.scrollText ?? field.scrollText ?? field.scrollable ?? (field.doNotScroll === false) ?? true);
    chk('modalPropPassword',    field.password);
    // Character limit & comb — always reset so switching fields doesn't leave stale values
    const rawMaxLen = field.maxLen ?? field.charLimit ?? '';
    set('modalPropMaxLen', rawMaxLen === 0 ? '' : rawMaxLen);
    chk('modalPropComb', field.comb || false);

    // Show/hide field-type-specific sections
    const isText    = field.type === 'text' || field.type === 'date';
    const isChoice  = field.type === 'combobox' || field.type === 'listbox';
    const isCheck   = field.type === 'checkbox' || field.type === 'radio';

    const textGroup = document.getElementById('modalPropTextOptionsGroup');
    if (textGroup) textGroup.style.display = isText ? '' : 'none';

    const checkGroup = document.getElementById('modalPropCheckboxStyleGroup');
    if (checkGroup) checkGroup.style.display = isCheck ? '' : 'none';

    const choiceGroup = document.getElementById('modalDropdownOptionsGroup');
    if (choiceGroup) choiceGroup.style.display = isChoice ? '' : 'none';

    if (isCheck) {
        set('modalPropCheckboxStyle', field.style?.checkboxStyle || field.checkboxStyle || 'check');
    }

    if (isChoice) {
        propRenderOptions(field.options || []);
    }

    // ── Format tab ──────────────────────────────────────────────────────────
    let formatType = field.formatType || field.style?.formatType || '';
    if (!formatType) {
        if      (field.dateFormat     || field.style?.dateFormat)                                   formatType = 'date';
        else if (field.currencySymbol !== undefined || field.style?.currencySymbol !== undefined)   formatType = 'currency';
        else if (field.phoneFormat    || field.style?.phoneFormat)                                  formatType = 'phone';
        else if (field.numberDecimals !== undefined || field.style?.numberDecimals !== undefined)   formatType = 'number';
        else if (field.zipFormat      || field.style?.zipFormat)                                    formatType = 'zip';
    }
    set('modalFormatCategory', formatType || 'none');
    // Number
    set('modalNumberDecimals',  String(field.style?.numberDecimals  ?? field.numberDecimals  ?? 2));
    set('modalNumberSeparator', field.style?.numberSeparator ?? field.numberSeparator ?? ',');
    // Currency
    set('modalCurrencySymbol',   field.style?.currencySymbol   ?? field.currencySymbol   ?? '$');
    set('modalCurrencyDecimals', String(field.style?.currencyDecimals ?? field.currencyDecimals ?? 2));
    set('modalSymbolLocation',   field.style?.symbolLocation   || field.symbolLocation   || 'before-space');
    // Date
    set('modalDateFormat', field.style?.dateFormat || field.dateFormat || 'MM/DD/YYYY');
    // Phone
    set('modalPhoneFormat', field.style?.phoneFormat || field.phoneFormat || '(XXX) XXX-XXXX');
    // ZIP
    set('modalZipFormat', field.style?.zipFormat || field.zipFormat || 'XXXXX');
    propUpdateFormatOptions();   // show/hide the right sub-section

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
    set('calcDecimals',     calc?.decimals ?? '');
    chk('calcReadOnly',     calc?.readOnly !== false);  // default true
    renderPanelCalcSources();
    propUpdateCalcSections();

    // ── Actions tab ──────────────────────────────────────────────────────────
    set('modalActionTrigger', field.actionTrigger || 'mouseUp');
    set('modalActionScript',  field.actionScript  || '');
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
    // Refresh the Live Test preview (source list just changed)
    if (typeof panelUpdateCalcPreview === 'function') panelUpdateCalcPreview();
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

/** Copy the current field name into the tooltip input and activate tooltip visibility */
function propUseNameAsTooltip() {
    const nameEl    = document.getElementById('modalPropName');
    const tooltipEl = document.getElementById('modalPropTooltip');
    const showEl    = document.getElementById('modalPropShowTooltip');
    if (!nameEl || !tooltipEl) return;
    tooltipEl.value = nameEl.value;
    if (showEl) showEl.value = 'yes';
    propSendUpdate();
}

/** Collect current form values and push to the editor */
function propSendUpdate() {
    // ── Multi-field batch update ──────────────────────────────────────────────
    if (_propMultiFields && _propMultiFields.length > 0) {
        const get  = id => { const el = document.getElementById(id); return el ? el.value : ''; };
        const chkd = id => { const el = document.getElementById(id); return el ? el.checked : false; };
        const col  = id => { const el = document.getElementById(id); return el ? el.value : '#000000'; };
        const batchProps = {
            tooltipDisabled: get('modalPropShowTooltip') === 'no',
            borderColor:     col('modalPropBorderColor'),
            fillColor:       col('modalPropFillColor'),
            lineThickness:   parseInt(get('modalPropLineThickness')) || 1,
            lineStyle:       get('modalPropLineStyle'),
            fontSize:        parseFloat(get('modalPropFontSize')) || 9,
            fontColor:       col('modalPropTextColor'),
            fontFamily:      get('modalPropFont'),
            readOnly:        chkd('modalPropReadOnly'),
            required:        chkd('modalPropRequired'),
            locked:          chkd('modalPropLocked'),
        };
        ipcRenderer.send('panel-to-editor', {
            action:      'apply-properties',
            isMulti:     true,
            multiFields: _propMultiFields,
            updates:     { props: batchProps },
        });
        return;
    }

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
            tooltip:         get('modalPropTooltip'),
            tooltipDisabled: get('modalPropShowTooltip') === 'no',
            visibility:      get('modalPropVisibility'),
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
            multiline:    chkd('modalPropMultiline'),
            scrollText:   chkd('modalPropScrollText'),
            password:     chkd('modalPropPassword'),
            maxLen:       parseInt(get('modalPropMaxLen')) || 0,
            comb:         chkd('modalPropComb'),
            checkboxStyle: get('modalPropCheckboxStyle'),
        }
    };

    if (_propCurrentField.type === 'radio') {
        updates.radio_group = get('modalPropRadioGroup');
    }

    // ── Format data ──────────────────────────────────────────────────────────
    const fmtCat = get('modalFormatCategory');
    if (fmtCat && fmtCat !== 'none') {
        updates.props.formatType = fmtCat;
        if (fmtCat === 'number') {
            updates.props.numberDecimals  = parseInt(get('modalNumberDecimals'))  || 0;
            updates.props.numberSeparator = get('modalNumberSeparator');
        } else if (fmtCat === 'currency') {
            updates.props.currencySymbol   = get('modalCurrencySymbol');
            updates.props.currencyDecimals = parseInt(get('modalCurrencyDecimals')) || 2;
            updates.props.symbolLocation   = get('modalSymbolLocation');
        } else if (fmtCat === 'date') {
            updates.props.dateFormat = get('modalDateFormat');
        } else if (fmtCat === 'phone') {
            updates.props.phoneFormat = get('modalPhoneFormat');
        } else if (fmtCat === 'zip') {
            updates.props.zipFormat = get('modalZipFormat');
        }
    } else {
        updates.props.formatType = '';
    }

    // ── Calculation data ─────────────────────────────────────────────────────
    const calcTypeVal = get('modalCalcType');
    updates.calculation = calcTypeVal
        ? {
            type:        calcTypeVal,
            sources:     _panelCalcSources.map(s => s.name),
            decimals:    parseInt(get('calcDecimals')) || 2,
            roundResult: chkd('calcRoundResult'),
            readOnly:    chkd('calcReadOnly'),
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

    // ── Calculate tab: Live Test refresh on formula / constant / round / decimals ──
    ['calcConstantValue', 'calcCustomFormula', 'calcDecimals'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('input', panelUpdateCalcPreview);
    });
    const calcRound = document.getElementById('calcRoundResult');
    if (calcRound) calcRound.addEventListener('change', panelUpdateCalcPreview);

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

    // Refresh the Live Test preview whenever sections change
    panelUpdateCalcPreview();
}

/** Render Live Test formula preview + test-value inputs */
function panelUpdateCalcPreview() {
    const calcType = document.getElementById('modalCalcType');
    const previewSection = document.getElementById('calcPreviewSection');
    if (!calcType || !previewSection) return;

    const type = calcType.value;
    if (!type || _panelCalcSources.length === 0) {
        previewSection.style.display = 'none';
        return;
    }
    previewSection.style.display = '';

    const formulaPreview = document.getElementById('calcFormulaPreview');
    const testInputs     = document.getElementById('calcTestInputs');
    if (!formulaPreview || !testInputs) return;

    const names = _panelCalcSources.map(s => s.name);
    const opSymbol = {
        SUM: '+', SUBTRACT: '-', MULTIPLY: '×', DIVIDE: '÷',
        PRODUCT: '×', AVERAGE: 'avg', MIN: 'min', MAX: 'max',
        CONSTANT_MULTIPLY: '×',
    };

    let formulaText = '';
    if (type === 'CUSTOM') {
        const formula = document.getElementById('calcCustomFormula')?.value || '...';
        formulaText = `Custom: ${formula}`;
    } else if (type === 'CUSTOM_SCRIPT') {
        formulaText = 'Custom Script (Live Test N/A)';
    } else if (type === 'CONSTANT_MULTIPLY') {
        const constVal = document.getElementById('calcConstantValue')?.value || '?';
        formulaText = `${constVal} × ${names.length === 1 ? names[0] : '(' + names.join(' × ') + ')'}`;
    } else if (['SUM', 'AVERAGE', 'MIN', 'MAX'].includes(type)) {
        formulaText = `${type}(${names.join(', ')})`;
    } else {
        formulaText = names.join(` ${opSymbol[type] || '?'} `);
    }
    formulaPreview.textContent = formulaText;

    const op = opSymbol[type] || '?';
    testInputs.innerHTML = _panelCalcSources.map((s, i) => {
        const letter = String.fromCharCode(65 + i);
        const sep = i < _panelCalcSources.length - 1
            ? `<span class="test-operator">${['SUM','AVERAGE','MIN','MAX','CUSTOM'].includes(type) ? ',' : op}</span>`
            : '';
        return `<div class="test-input-group">
            <span class="test-label">${letter}</span>
            <input type="number" class="test-input" id="calcTest_${i}" value="1" oninput="panelRunCalcTest()">
            ${sep}
        </div>`;
    }).join('');

    panelRunCalcTest();
}

/** Compute and render the Live Test numeric result */
function panelRunCalcTest() {
    const calcType   = document.getElementById('modalCalcType');
    const testResult = document.getElementById('calcTestResult');
    if (!calcType || !testResult) return;

    const type = calcType.value;
    const values = _panelCalcSources.map((_, i) => {
        const input = document.getElementById(`calcTest_${i}`);
        return input ? parseFloat(input.value) || 0 : 0;
    });

    let result = 0;
    try {
        switch (type) {
            case 'SUM':      result = values.reduce((a, b) => a + b, 0); break;
            case 'SUBTRACT': result = values.length ? values[0] - values.slice(1).reduce((a, b) => a + b, 0) : 0; break;
            case 'MULTIPLY':
            case 'PRODUCT':  result = values.reduce((a, b) => a * b, 1); break;
            case 'DIVIDE': {
                const divisor = values.slice(1).reduce((a, b) => a * b, 1);
                result = divisor !== 0 ? values[0] / divisor : 0;
                break;
            }
            case 'AVERAGE':  result = values.length ? values.reduce((a, b) => a + b, 0) / values.length : 0; break;
            case 'MIN':      result = values.length ? Math.min(...values) : 0; break;
            case 'MAX':      result = values.length ? Math.max(...values) : 0; break;
            case 'CONSTANT_MULTIPLY': {
                const constVal = parseFloat(document.getElementById('calcConstantValue')?.value) || 1;
                result = constVal * values.reduce((a, b) => a * b, 1);
                break;
            }
            case 'CUSTOM': {
                const formula = document.getElementById('calcCustomFormula')?.value;
                if (formula) {
                    let expr = formula;
                    _panelCalcSources.forEach((_, i) => {
                        const letter = String.fromCharCode(65 + i);
                        expr = expr.replace(new RegExp(`\\b${letter}\\b`, 'g'), values[i]);
                    });
                    result = Function('"use strict"; return (' + expr + ')')();
                }
                break;
            }
            case 'CUSTOM_SCRIPT': result = 'N/A'; break;
        }
    } catch (e) {
        testResult.textContent = 'Error';
        testResult.style.color = '#e94560';
        return;
    }

    const roundCheck    = document.getElementById('calcRoundResult');
    const decimalsInput = document.getElementById('calcDecimals');
    if (roundCheck && roundCheck.checked && decimalsInput && typeof result === 'number') {
        const d = parseInt(decimalsInput.value) || 2;
        result = parseFloat(result.toFixed(d));
    }

    const isNum = typeof result === 'number' && isFinite(result);
    testResult.textContent = isNum ? result : (result === 'N/A' ? 'N/A' : 'Error');
    testResult.style.color = isNum ? 'var(--accent)' : '#e94560';
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
                // Pre-seed with current source list so already-picked fields
                // stay green on the canvas the moment pick mode activates.
                const _names = Array.isArray(calcSources) ? calcSources.map(s => s.name) : [];
                ipcRenderer.send('panel-to-editor', { action: 'calc-pick-start', payload: { names: _names } });
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
    // Color swatch buttons open our custom draggable color picker
    // (see openCustomColorPicker below). Replaces the native color dialog
    // so we can: include a "Transparent" checkbox, persist the selection
    // reliably on close, and let the user drag the picker window anywhere.
    document.querySelectorAll('.color-swatch-btn').forEach(btn => {
        btn.addEventListener('click', () => openCustomColorPicker(btn));
    });
});

// ════════════════════════════════════════════════════════════════════════════
//  CUSTOM COLOR PICKER
//  A freely-draggable HSV picker with Hex/RGB fields and a Transparent
//  checkbox. Replaces the native <input type="color"> which (a) cannot
//  represent transparent, (b) anchors itself to the clicked element, and
//  (c) sometimes drops the selected color when dismissed without firing
//  "change". Live updates are written to the hidden input on every tweak
//  so the value is never lost, regardless of how the popup is closed.
// ════════════════════════════════════════════════════════════════════════════

let _ccpRoot = null;   // currently-open picker DOM root (only one at a time)

function openCustomColorPicker(swatchBtn) {
    // Close any previously-open picker first
    closeCustomColorPicker();

    const targetId = swatchBtn.dataset.target;
    const hidden   = document.getElementById(targetId);
    if (!hidden) return;
    const label    = swatchBtn.dataset.label || 'Color';

    // Seed state from the current hidden value
    const rawVal       = hidden.value;
    const isTransparent = (rawVal === 'transparent' || !rawVal);
    const seedHex      = (rawVal && /^#[0-9a-fA-F]{6}$/.test(rawVal)) ? rawVal : '#000000';

    const { h: seedH, s: seedS, v: seedV } = _ccpHexToHsv(seedHex);

    // ── Build DOM ──────────────────────────────────────────────────────
    const root = document.createElement('div');
    root.className = 'ccp-root';
    root.innerHTML = `
        <div class="ccp-header">
            <span class="ccp-title">${_ccpEscape(label)}</span>
            <button type="button" class="ccp-close" title="Close">✕</button>
        </div>
        <div class="ccp-body">
            <div class="ccp-sv" tabindex="0">
                <div class="ccp-sv-white"></div>
                <div class="ccp-sv-black"></div>
                <div class="ccp-sv-cursor"></div>
            </div>
            <div class="ccp-hue">
                <div class="ccp-hue-cursor"></div>
            </div>
            <div class="ccp-preview-row">
                <div class="ccp-preview"></div>
                <input type="text" class="ccp-hex" maxlength="7" spellcheck="false">
            </div>
            <div class="ccp-rgb-row">
                <label>R<input type="number" class="ccp-r" min="0" max="255"></label>
                <label>G<input type="number" class="ccp-g" min="0" max="255"></label>
                <label>B<input type="number" class="ccp-b" min="0" max="255"></label>
            </div>
            <label class="ccp-transparent">
                <input type="checkbox" class="ccp-transparent-cb">
                <span>Transparent (no color)</span>
            </label>
        </div>
    `;
    document.body.appendChild(root);
    _ccpRoot = root;

    // ── Position: near the clicked swatch, but draggable afterwards ───
    const r = swatchBtn.getBoundingClientRect();
    const pw = 240;  // matches CSS width
    let left = r.left;
    let top  = r.bottom + 6;
    // Clamp into viewport so it never opens offscreen
    const maxLeft = window.innerWidth - pw - 8;
    if (left > maxLeft) left = maxLeft;
    if (left < 8)       left = 8;
    if (top + 320 > window.innerHeight) top = Math.max(8, r.top - 320 - 6);
    root.style.left = left + 'px';
    root.style.top  = top  + 'px';

    // ── Internal state ────────────────────────────────────────────────
    const state = { h: seedH, s: seedS, v: seedV, transparent: isTransparent };

    const $sv       = root.querySelector('.ccp-sv');
    const $svCursor = root.querySelector('.ccp-sv-cursor');
    const $hue      = root.querySelector('.ccp-hue');
    const $hueCur   = root.querySelector('.ccp-hue-cursor');
    const $preview  = root.querySelector('.ccp-preview');
    const $hex      = root.querySelector('.ccp-hex');
    const $r        = root.querySelector('.ccp-r');
    const $g        = root.querySelector('.ccp-g');
    const $b        = root.querySelector('.ccp-b');
    const $trans    = root.querySelector('.ccp-transparent-cb');

    function render() {
        // SV plane background hue
        const pureHue = _ccpHsvToHex(state.h, 1, 1);
        $sv.style.background = pureHue;
        // Cursor positions
        $svCursor.style.left = (state.s * 100) + '%';
        $svCursor.style.top  = ((1 - state.v) * 100) + '%';
        $hueCur.style.left   = ((state.h / 360) * 100) + '%';

        // Inputs / preview
        const hex = _ccpHsvToHex(state.h, state.s, state.v);
        $hex.value = hex;
        const [rr, gg, bb] = _ccpHexToRgb(hex);
        $r.value = rr; $g.value = gg; $b.value = bb;

        if (state.transparent) {
            $preview.style.background = '';
            $preview.classList.add('ccp-checker');
        } else {
            $preview.style.background = hex;
            $preview.classList.remove('ccp-checker');
        }
        $trans.checked = state.transparent;
        $sv.style.opacity = state.transparent ? '0.35' : '1';
        $hue.style.opacity = state.transparent ? '0.35' : '1';

        // Push live update to hidden input + preview swatch + parent panel
        const valueOut = state.transparent ? 'transparent' : hex;
        hidden.value = valueOut;
        if (typeof updateColorSwatch === 'function') {
            updateColorSwatch(targetId, valueOut);
        }
        // Debounce propSendUpdate a little so RGB sliding isn't chatty
        _ccpScheduleUpdate();
    }

    let _updateTimer = null;
    function _ccpScheduleUpdate() {
        if (_updateTimer) clearTimeout(_updateTimer);
        _updateTimer = setTimeout(() => {
            if (typeof propSendUpdate === 'function') propSendUpdate();
        }, 80);
    }

    // ── Saturation/Value plane interaction ────────────────────────────
    function pickFromSV(e) {
        const rect = $sv.getBoundingClientRect();
        const x = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
        const y = Math.max(0, Math.min(1, (e.clientY - rect.top)  / rect.height));
        state.s = x;
        state.v = 1 - y;
        state.transparent = false;
        render();
    }
    $sv.addEventListener('mousedown', (e) => {
        pickFromSV(e);
        const move = (ev) => pickFromSV(ev);
        const up   = () => {
            document.removeEventListener('mousemove', move);
            document.removeEventListener('mouseup',   up);
        };
        document.addEventListener('mousemove', move);
        document.addEventListener('mouseup',   up);
    });

    // ── Hue slider interaction ────────────────────────────────────────
    function pickFromHue(e) {
        const rect = $hue.getBoundingClientRect();
        const x = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
        state.h = x * 360;
        state.transparent = false;
        render();
    }
    $hue.addEventListener('mousedown', (e) => {
        pickFromHue(e);
        const move = (ev) => pickFromHue(ev);
        const up   = () => {
            document.removeEventListener('mousemove', move);
            document.removeEventListener('mouseup',   up);
        };
        document.addEventListener('mousemove', move);
        document.addEventListener('mouseup',   up);
    });

    // ── Hex / RGB input ───────────────────────────────────────────────
    $hex.addEventListener('input', () => {
        let v = $hex.value.trim();
        if (!v.startsWith('#')) v = '#' + v;
        if (/^#[0-9a-fA-F]{6}$/.test(v)) {
            const { h, s, v: val } = _ccpHexToHsv(v);
            state.h = h; state.s = s; state.v = val;
            state.transparent = false;
            render();
        }
    });
    [$r, $g, $b].forEach((input, idx) => {
        input.addEventListener('input', () => {
            const rv = Math.max(0, Math.min(255, parseInt($r.value, 10) || 0));
            const gv = Math.max(0, Math.min(255, parseInt($g.value, 10) || 0));
            const bv = Math.max(0, Math.min(255, parseInt($b.value, 10) || 0));
            const hex = '#' + [rv, gv, bv].map(n => n.toString(16).padStart(2, '0')).join('');
            const { h, s, v: val } = _ccpHexToHsv(hex);
            state.h = h; state.s = s; state.v = val;
            state.transparent = false;
            render();
        });
    });

    // ── Transparent checkbox ──────────────────────────────────────────
    $trans.addEventListener('change', () => {
        state.transparent = $trans.checked;
        render();
    });

    // ── Drag by header ────────────────────────────────────────────────
    const $header = root.querySelector('.ccp-header');
    $header.addEventListener('mousedown', (e) => {
        if (e.target.classList.contains('ccp-close')) return;
        const startX = e.clientX, startY = e.clientY;
        const rRoot  = root.getBoundingClientRect();
        const origL  = rRoot.left, origT = rRoot.top;
        const move = (ev) => {
            const nl = Math.max(0, Math.min(window.innerWidth  - 40, origL + (ev.clientX - startX)));
            const nt = Math.max(0, Math.min(window.innerHeight - 30, origT + (ev.clientY - startY)));
            root.style.left = nl + 'px';
            root.style.top  = nt + 'px';
        };
        const up = () => {
            document.removeEventListener('mousemove', move);
            document.removeEventListener('mouseup',   up);
        };
        document.addEventListener('mousemove', move);
        document.addEventListener('mouseup',   up);
        e.preventDefault();
    });

    // ── Close handlers ────────────────────────────────────────────────
    root.querySelector('.ccp-close').addEventListener('click', closeCustomColorPicker);
    // Click outside: close (value is already live-persisted to hidden input)
    function onOutside(e) {
        if (!root.contains(e.target) && e.target !== swatchBtn && !swatchBtn.contains(e.target)) {
            closeCustomColorPicker();
        }
    }
    // Defer so the opening click doesn't immediately close us
    setTimeout(() => document.addEventListener('mousedown', onOutside), 0);
    root._outsideHandler = onOutside;

    render();
}

function closeCustomColorPicker() {
    if (!_ccpRoot) return;
    try {
        document.removeEventListener('mousedown', _ccpRoot._outsideHandler);
    } catch (_) {}
    // Final flush — make sure the last value reaches propSendUpdate even if
    // the debounce timer hadn't fired yet
    try { if (typeof propSendUpdate === 'function') propSendUpdate(); } catch (_) {}
    _ccpRoot.remove();
    _ccpRoot = null;
}

// ── Color conversion helpers ─────────────────────────────────────────────
function _ccpHexToRgb(hex) {
    hex = hex.replace('#', '');
    return [
        parseInt(hex.slice(0, 2), 16),
        parseInt(hex.slice(2, 4), 16),
        parseInt(hex.slice(4, 6), 16),
    ];
}
function _ccpRgbToHsv(r, g, b) {
    r /= 255; g /= 255; b /= 255;
    const max = Math.max(r, g, b), min = Math.min(r, g, b);
    const d = max - min;
    let h = 0;
    if (d !== 0) {
        if      (max === r) h = ((g - b) / d) % 6;
        else if (max === g) h = (b - r) / d + 2;
        else                h = (r - g) / d + 4;
    }
    h = (h * 60 + 360) % 360;
    const s = max === 0 ? 0 : d / max;
    return { h, s, v: max };
}
function _ccpHexToHsv(hex) {
    const [r, g, b] = _ccpHexToRgb(hex);
    return _ccpRgbToHsv(r, g, b);
}
function _ccpHsvToHex(h, s, v) {
    const c = v * s;
    const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
    const m = v - c;
    let r = 0, g = 0, b = 0;
    if      (h < 60)  { r = c; g = x; }
    else if (h < 120) { r = x; g = c; }
    else if (h < 180) { g = c; b = x; }
    else if (h < 240) { g = x; b = c; }
    else if (h < 300) { r = x; b = c; }
    else              { r = c; b = x; }
    const hex = [r, g, b].map(n => Math.round((n + m) * 255).toString(16).padStart(2, '0')).join('');
    return '#' + hex;
}
function _ccpEscape(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c]));
}





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
        // Actions column is sticky-right with a solid background so it stays visible
        // when long target-field/source names force horizontal scroll.
        list.innerHTML = `
            <table style="width:100%;border-collapse:separate;border-spacing:0;font-size:11px;">
                <thead>
                    <tr>
                        <th style="text-align:left;padding:7px 10px;color:var(--text-secondary);font-weight:500;background:var(--bg-tertiary);">Target Field</th>
                        <th style="text-align:left;padding:7px 10px;color:var(--text-secondary);font-weight:500;width:90px;background:var(--bg-tertiary);">Type</th>
                        <th style="text-align:left;padding:7px 10px;color:var(--text-secondary);font-weight:500;background:var(--bg-tertiary);">Sources</th>
                        <th style="text-align:center;padding:7px 10px;color:var(--text-secondary);font-weight:500;width:70px;position:sticky;right:0;background:var(--bg-tertiary);border-left:1px solid var(--border);box-shadow:-4px 0 6px -2px rgba(0,0,0,0.35);">Actions</th>
                    </tr>
                </thead>
                <tbody>` + entries.map(c => {
                    const srcText = (c.sources || []).length <= 3
                        ? (c.sources || []).join(', ')
                        : `${c.sources.length} fields`;
                    const typeLabel = c.type === 'CONSTANT_MULTIPLY' ? `×${c.constant || 1}` : c.type;
                    const safeName  = c.name.replace(/'/g, "\\'");
                    return `<tr>
                        <td style="padding:6px 10px;color:var(--text-primary);white-space:nowrap;border-top:1px solid var(--border);">${escapeHtml(c.name)}</td>
                        <td style="padding:6px 10px;color:var(--accent);font-weight:500;white-space:nowrap;border-top:1px solid var(--border);">${escapeHtml(typeLabel)}</td>
                        <td style="padding:6px 10px;color:var(--text-secondary);font-size:10px;border-top:1px solid var(--border);">${escapeHtml(srcText)}</td>
                        <td style="padding:6px 10px;text-align:center;white-space:nowrap;position:sticky;right:0;background:var(--bg-primary);border-top:1px solid var(--border);border-left:1px solid var(--border);box-shadow:-4px 0 6px -2px rgba(0,0,0,0.35);">
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
