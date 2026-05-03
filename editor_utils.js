function openPropertiesModal(field) {
    // Auto-save previous field if panel was already open with a different field
    const panel = document.getElementById('propertiesModal');
    if (panel.style.display === 'flex' && modalField && modalField !== field && !isMultiFieldEdit) {
        savePropertiesModalSilent();
    }

    modalField = field;
    modalOriginalRect = [...field.rect];
    isMultiFieldEdit = false;
    // Show field name in title for quick reference
    const displayName = field.name.length > 30 ? field.name.substring(0, 27) + '...' : field.name;
    document.getElementById('modalTitle').textContent = `📋 ${displayName}`;

    // Show all tabs
    document.querySelectorAll('.modal-tab').forEach(tab => tab.style.display = 'block');
    // Only switch tab if panel is first opening (keep current tab on field switch)
    if (panel.style.display !== 'flex') {
        switchPropertiesTab('general');
    }

    panel.style.display = 'flex';
    panel.classList.remove('minimized');
    
    // Populate form
    // General tab
    document.getElementById('modalPropName').value = field.name;
    document.getElementById('modalPropTooltip').value = field.tooltip || '';
    document.getElementById('modalPropType').value = field.type;
    document.getElementById('modalPropPage').value = field.page + 1;

    // v1.1.0: Radio group name row in General tab
    const modalGroupRow = document.getElementById('modalPropRadioGroupRow');
    const modalGroupInput = document.getElementById('modalPropRadioGroup');
    if (modalGroupRow && modalGroupInput) {
        if (field.type === 'radio') {
            modalGroupRow.style.display = 'block';
            modalGroupInput.value = field.radio_group || '';
        } else {
            modalGroupRow.style.display = 'none';
            modalGroupInput.value = '';
        }
    }
    document.getElementById('modalPropVisibility').value = field.visibility || 'visible';
    document.getElementById('modalPropOrientation').value = field.orientation || 0;
    document.getElementById('modalPropReadOnly').checked = field.readOnly || false;
    document.getElementById('modalPropRequired').checked = field.required || false;
    document.getElementById('modalPropLocked').checked = field.locked || false;
    
    // Appearance tab - check edited style, then extracted values, then global defaults
    // Border color: check for empty string (transparent) before falling through to defaults
    const modalExtractedBorder = field.border_color ?? field.borderColor;
    const modalStyledBorder = field.style?.borderColor;
    let modalBorderValue;
    if (modalStyledBorder !== undefined) {
        modalBorderValue = modalStyledBorder;
    } else if (modalExtractedBorder === '' || modalExtractedBorder === 'transparent') {
        modalBorderValue = 'transparent';
    } else if (modalExtractedBorder) {
        modalBorderValue = modalExtractedBorder;
    } else {
        modalBorderValue = window.globalFieldSettings?.borderColor || '#000000';
    }
    updateColorSwatch('modalPropBorderColor', modalBorderValue);
    // Fill color: check for empty string (transparent) before falling through to defaults
    const modalExtractedFill = field.fill_color ?? field.fillColor;
    const modalStyledFill = field.style?.fillColor;
    let modalFillValue;
    if (modalStyledFill !== undefined) {
        modalFillValue = modalStyledFill;
    } else if (modalExtractedFill === '' || modalExtractedFill === 'transparent') {
        modalFillValue = 'transparent';
    } else if (modalExtractedFill) {
        modalFillValue = modalExtractedFill;
    } else {
        modalFillValue = window.globalFieldSettings?.fillColor || '#FFFFFF';
    }
    updateColorSwatch('modalPropFillColor', modalFillValue);
    document.getElementById('modalPropLineThickness').value = field.style?.lineThickness ?? field.lineThickness ?? window.globalFieldSettings?.borderThickness ?? '1';
    document.getElementById('modalPropLineStyle').value = field.style?.lineStyle || field.lineStyle || window.globalFieldSettings?.borderStyle || 'solid';
    document.getElementById('modalPropFontSize').value = field.style?.fontSize || field.font_size || field.fontSize || window.globalFieldSettings?.fontSize || 9;
    updateColorSwatch('modalPropTextColor', field.style?.fontColor || field.text_color || field.fontColor || window.globalFieldSettings?.fontColor || '#000000');
    document.getElementById('modalPropFont').value = field.style?.fontFamily || field.font_family || field.fontFamily || window.globalFieldSettings?.fontFamily || 'Helvetica';
    
    // Options tab
    const checkboxStyleGroup = document.getElementById('modalPropCheckboxStyleGroup');
    if (checkboxStyleGroup) {
        if (field.type === 'checkbox' || field.type === 'radio') {
            checkboxStyleGroup.style.display = 'block';
            const cbStyleInput = document.getElementById('modalPropCheckboxStyle');
            if (cbStyleInput) {
                const globalDefault = window.globalFieldSettings?.checkboxStyle || 'check';
                cbStyleInput.value = field.style?.checkboxStyle || field.checkboxStyle || globalDefault;
            }
        } else {
            checkboxStyleGroup.style.display = 'none';
        }
    }
    
    // Position tab - set to points initially
    document.getElementById('modalPropUnits').value = 'pt';
    updatePositionDisplay();
    
    // Options tab - check edited style, then extracted values
    document.getElementById('modalPropAlignment').value = field.style?.textAlign || field.text_align || field.textAlign || 'left';
    
    // Dropdown options group
    const dropdownOptionsGroup = document.getElementById('modalDropdownOptionsGroup');
    const textOptionsGroup = document.getElementById('modalPropTextOptionsGroup');
    if (field.type === 'combobox' || field.type === 'listbox') {
        if (dropdownOptionsGroup) dropdownOptionsGroup.style.display = 'block';
        if (textOptionsGroup) textOptionsGroup.style.display = 'none';
        
        // Populate flags
        document.getElementById('modalPropDropdownSort').checked = !!(field.style?.sort ?? field.sort);
        document.getElementById('modalPropDropdownEditable').checked = !!(field.style?.editable ?? field.editable);
        document.getElementById('modalPropDropdownSpellCheck').checked = !!(field.style?.spellCheck ?? field.spellCheck ?? true);
        document.getElementById('modalPropDropdownCommit').checked = !!(field.style?.commitImmediately ?? field.commitImmediately);
        
        // Render options list
        field._selectedOptionIdx = -1; // Reset selection
        renderChoiceList(field, 'modalPropDropdownList', 'modalProp');
    } else {
        if (dropdownOptionsGroup) dropdownOptionsGroup.style.display = 'none';
        if (textOptionsGroup) textOptionsGroup.style.display = 'block';
    }

    // Validation tab
    const styled = changes.styled[field.name] || {};
    const validation = styled.validation || field.validation || {};
    
    // Default value
    document.getElementById('modalPropDefaultValue').value = validation.defaultValue || '';
    
    // Text options
    document.getElementById('modalPropMultiline').checked = field.style?.multiline ?? field.multiline ?? false;
    document.getElementById('modalPropScrollText').checked = field.style?.scrollText ?? field.scrollable ?? true;
    document.getElementById('modalPropPassword').checked = field.style?.password ?? field.password ?? false;
    document.getElementById('modalPropMaxLen').value = field.style?.maxLen ?? field.maxLen ?? '';
    document.getElementById('modalPropComb').checked = field.style?.comb ?? field.comb ?? false;
    
    // Format tab - use formatType if available, else detect from specific properties
    let formatCategory = 'none';
    if (field.formatType || field.style?.formatType) {
        formatCategory = field.formatType || field.style?.formatType;
    } else if (field.dateFormat || field.style?.dateFormat) {
        formatCategory = 'date';
    } else if (field.currencySymbol !== undefined || field.style?.currencySymbol !== undefined) {
        formatCategory = 'currency';
    } else if (field.phoneFormat || field.style?.phoneFormat) {
        formatCategory = 'phone';
    } else if (field.numberDecimals !== undefined || field.style?.numberDecimals !== undefined) {
        formatCategory = 'number';
    } else if (field.zipFormat || field.style?.zipFormat) {
        formatCategory = 'zip';
    }
    
    document.getElementById('modalFormatCategory').value = formatCategory;
    updateFormatOptions();
    
    // Set format-specific values
    if (formatCategory === 'date') {
        document.getElementById('modalDateFormat').value = field.style?.dateFormat || field.dateFormat || 'MM/DD/YYYY';
    } else if (formatCategory === 'currency') {
        document.getElementById('modalCurrencySymbol').value = field.style?.currencySymbol ?? field.currencySymbol ?? '$';
        document.getElementById('modalCurrencyDecimals').value = field.style?.currencyDecimals ?? field.currencyDecimals ?? 2;
        document.getElementById('modalSymbolLocation').value = field.style?.symbolLocation || field.symbolLocation || 'before-space';
    } else if (formatCategory === 'phone') {
        document.getElementById('modalPhoneFormat').value = field.style?.phoneFormat || field.phoneFormat || '(XXX) XXX-XXXX';
    } else if (formatCategory === 'number') {
        document.getElementById('modalNumberDecimals').value = field.style?.numberDecimals ?? field.numberDecimals ?? 2;
        document.getElementById('modalNumberSeparator').value = field.style?.thousandSep ?? field.thousandSep ?? ',';
    } else if (formatCategory === 'zip') {
        document.getElementById('modalZipFormat').value = field.style?.zipFormat || field.zipFormat || 'XXXXX';
    }
    
    // Reset dirty flags after populating — user hasn't changed anything yet
    formatDirty = false;
    fontDirty = false;

    // Only switch to General tab if panel is first opening (keep current tab on field switch)
    if (panel.style.display !== 'flex') {
        switchPropertiesTab('general');
    }
}


function openMultiFieldPropertiesModal(fields) {
    isMultiFieldEdit = true;
    multiFieldSelection = fields;
    
    // Use first field as proxy for form
    modalField = fields[0];
    modalOriginalRect = [...modalField.rect];
    
    const panel = document.getElementById('propertiesModal');
    panel.style.display = 'flex';
    panel.classList.remove('minimized');
    document.getElementById('modalTitle').textContent = `Properties (${fields.length} selected)`;

    // Hide tabs not applicable to multi-select (keep Appearance, Options, Format, Calculate)
    document.querySelectorAll('.modal-tab').forEach(tab => {
        const tabName = tab.dataset.tab;
        if (tabName === 'appearance' || tabName === 'options' || tabName === 'format' || tabName === 'calculate') {
            tab.style.display = 'block';
        } else {
            tab.style.display = 'none';
        }
    });

    // Switch to Appearance tab
    switchPropertiesTab('appearance');
    
    // Appearance tab - check edited style, then extracted values, then global defaults
    // Border color: check for empty string (transparent) before falling through to defaults
    const batchExtractedBorder = modalField.border_color ?? modalField.borderColor;
    const batchStyledBorder = modalField.style?.borderColor;
    let batchBorderValue;
    if (batchStyledBorder !== undefined) {
        batchBorderValue = batchStyledBorder;
    } else if (batchExtractedBorder === '' || batchExtractedBorder === 'transparent') {
        batchBorderValue = 'transparent';
    } else if (batchExtractedBorder) {
        batchBorderValue = batchExtractedBorder;
    } else {
        batchBorderValue = window.globalFieldSettings?.borderColor || '#000000';
    }
    updateColorSwatch('modalPropBorderColor', batchBorderValue);
    // Fill color: check for empty string (transparent) before falling through to defaults
    const batchExtractedFill = modalField.fill_color ?? modalField.fillColor;
    const batchStyledFill = modalField.style?.fillColor;
    let batchFillValue;
    if (batchStyledFill !== undefined) {
        batchFillValue = batchStyledFill;
    } else if (batchExtractedFill === '' || batchExtractedFill === 'transparent') {
        batchFillValue = 'transparent';
    } else if (batchExtractedFill) {
        batchFillValue = batchExtractedFill;
    } else {
        batchFillValue = window.globalFieldSettings?.fillColor || '#FFFFFF';
    }
    updateColorSwatch('modalPropFillColor', batchFillValue);
    document.getElementById('modalPropLineThickness').value = modalField.style?.lineThickness ?? modalField.lineThickness ?? window.globalFieldSettings?.borderThickness ?? '1';
    document.getElementById('modalPropLineStyle').value = modalField.style?.lineStyle || modalField.lineStyle || window.globalFieldSettings?.borderStyle || 'solid';
    document.getElementById('modalPropFontSize').value = modalField.style?.fontSize || modalField.font_size || modalField.fontSize || window.globalFieldSettings?.fontSize || 9;
    updateColorSwatch('modalPropTextColor', modalField.style?.fontColor || modalField.text_color || modalField.fontColor || window.globalFieldSettings?.fontColor || '#000000');
    document.getElementById('modalPropFont').value = modalField.style?.fontFamily || modalField.font_family || modalField.fontFamily || window.globalFieldSettings?.fontFamily || 'Helvetica';
    
    // Options tab (Checkbox style for multi-selection)
    const checkboxStyleGroup = document.getElementById('modalPropCheckboxStyleGroup');
    if (checkboxStyleGroup) {
        const hasCheckboxes = fields.some(f => f.type === 'checkbox' || f.type === 'radio');
        checkboxStyleGroup.style.display = hasCheckboxes ? 'block' : 'none';
        if (hasCheckboxes) {
            const globalDefault = window.globalFieldSettings?.checkboxStyle || 'check';
            document.getElementById('modalPropCheckboxStyle').value = modalField.style?.checkboxStyle || modalField.checkboxStyle || globalDefault;
        }
    }
    
    // Options tab - check edited style, then extracted values
    document.getElementById('modalPropAlignment').value = modalField.style?.textAlign || modalField.text_align || modalField.textAlign || 'left';
    document.getElementById('modalPropMultiline').checked = modalField.multiline || false;
    document.getElementById('modalPropScrollText').checked = modalField.scrollable || true;
    document.getElementById('modalPropPassword').checked = modalField.password || false;
    document.getElementById('modalPropMaxLen').value = modalField.maxLen || '';
    document.getElementById('modalPropComb').checked = modalField.comb || false;
    
    // Format tab - use formatType if available, else detect from first field
    let formatCategory = 'none';
    if (modalField.formatType || modalField.style?.formatType) {
        formatCategory = modalField.formatType || modalField.style?.formatType;
    } else if (modalField.dateFormat || modalField.style?.dateFormat) {
        formatCategory = 'date';
    } else if (modalField.currencySymbol !== undefined || modalField.style?.currencySymbol !== undefined) {
        formatCategory = 'currency';
    } else if (modalField.phoneFormat || modalField.style?.phoneFormat) {
        formatCategory = 'phone';
    } else if (modalField.numberDecimals !== undefined || modalField.style?.numberDecimals !== undefined) {
        formatCategory = 'number';
    } else if (modalField.zipFormat || modalField.style?.zipFormat) {
        formatCategory = 'zip';
    }
    
    document.getElementById('modalFormatCategory').value = formatCategory;
    updateFormatOptions();
    
    // Set format-specific values
    if (formatCategory === 'date') {
        document.getElementById('modalDateFormat').value = modalField.style?.dateFormat || modalField.dateFormat || 'MM/DD/YYYY';
    } else if (formatCategory === 'currency') {
        document.getElementById('modalCurrencySymbol').value = modalField.style?.currencySymbol ?? modalField.currencySymbol ?? '$';
        document.getElementById('modalCurrencyDecimals').value = modalField.style?.currencyDecimals ?? modalField.currencyDecimals ?? 2;
        document.getElementById('modalSymbolLocation').value = modalField.style?.symbolLocation || modalField.symbolLocation || 'before-space';
    } else if (formatCategory === 'phone') {
        document.getElementById('modalPhoneFormat').value = modalField.style?.phoneFormat || modalField.phoneFormat || '(XXX) XXX-XXXX';
    } else if (formatCategory === 'number') {
        document.getElementById('modalNumberDecimals').value = modalField.style?.numberDecimals ?? modalField.numberDecimals ?? 2;
        document.getElementById('modalNumberSeparator').value = modalField.style?.thousandSep ?? modalField.thousandSep ?? ',';
    } else if (formatCategory === 'zip') {
        document.getElementById('modalZipFormat').value = modalField.style?.zipFormat || modalField.zipFormat || 'XXXXX';
    }
    
    // Reset dirty flags after populating — user hasn't changed anything yet
    formatDirty = false;
    fontDirty = false;
}


function switchPropertiesTab(tabName) {
    // Turn off pick mode when switching tabs
    disablePickMode();

    // Update tab buttons
    document.querySelectorAll('.modal-tab').forEach(tab => {
        tab.classList.toggle('active', tab.dataset.tab === tabName);
    });

    // Update tab content
    document.querySelectorAll('.modal-tab-content').forEach(content => {
        content.classList.toggle('active', content.id === `tab-${tabName}`);
    });
}


function updateFormatOptions() {
    const category = document.getElementById('modalFormatCategory').value;
    
    // Hide all format options
    document.querySelectorAll('.format-options').forEach(el => el.style.display = 'none');
    
    // Show the selected one
    const optionId = `modalFormat${category.charAt(0).toUpperCase() + category.slice(1)}`;
    const optionEl = document.getElementById(optionId);
    if (optionEl) optionEl.style.display = 'block';
}


function updatePositionDisplay() {
    if (!modalField) return;
    
    const units = document.getElementById('modalPropUnits').value;
    const rect = modalField.rect;
    
    // Conversion factors from points
    const factor = units === 'in' ? 72 : units === 'mm' ? 72/25.4 : 1;
    
    const left = rect[0] / factor;
    const top = rect[1] / factor;
    const width = (rect[2] - rect[0]) / factor;
    const height = (rect[3] - rect[1]) / factor;
    const right = rect[2] / factor;  // Left + Width
    const bottom = rect[3] / factor; // Top + Height
    
    document.getElementById('modalPropLeft').value = left.toFixed(2);
    document.getElementById('modalPropTop').value = top.toFixed(2);
    document.getElementById('modalPropWidth').value = width.toFixed(2);
    document.getElementById('modalPropHeight').value = height.toFixed(2);
    document.getElementById('modalPropRight').value = right.toFixed(2);
    document.getElementById('modalPropBottom').value = bottom.toFixed(2);
    
    // Load "No resize" and "Locked" checkbox states if stored
    const noResize = document.getElementById('modalPropNoResize');
    const posLocked = document.getElementById('modalPropPositionLocked');
    if (noResize) noResize.checked = modalField.noResizeOnMove || false;
    if (posLocked) posLocked.checked = modalField.positionLocked || false;
}


function updatePositionUnits() {
    updatePositionDisplay();
}


function _applyPropertiesFromForm() {
    if (!modalField) return;

    if (!isMultiFieldEdit) {
        // Get name and check if renamed
        const newName = document.getElementById('modalPropName').value.trim();
        const oldName = modalField.name;

        if (newName && newName !== oldName) {
            // Track rename
            changes.renamed[oldName] = newName;
            modalField.name = newName;
        }

        // v1.1.0: Save radio group name changes
        const modalGroupInput = document.getElementById('modalPropRadioGroup');
        if (modalGroupInput && modalField.type === 'radio') {
            const newGroup = modalGroupInput.value.trim();
            const oldGroup = modalField.radio_group;
            if (newGroup && newGroup !== oldGroup) {
                // Rename the group for ALL fields that share it
                fields.forEach(f => {
                    if (f.type === 'radio' && f.radio_group === oldGroup) {
                        f.radio_group = newGroup;
                    }
                });
                // Update changes tracking
                if (oldGroup && changes.new_radio_groups[oldGroup]) {
                    changes.new_radio_groups[newGroup] = changes.new_radio_groups[oldGroup];
                    delete changes.new_radio_groups[oldGroup];
                }
                // Propagate in collapsedGroups
                if (collapsedGroups.has(oldGroup)) {
                    collapsedGroups.delete(oldGroup);
                    collapsedGroups.add(newGroup);
                }
            }
        }

        // Get position in points
        const units = document.getElementById('modalPropUnits').value;
        const factor = units === 'in' ? 72 : units === 'mm' ? 72/25.4 : 1;

        const left = parseFloat(document.getElementById('modalPropLeft').value) * factor;
        const top = parseFloat(document.getElementById('modalPropTop').value) * factor;
        const width = parseFloat(document.getElementById('modalPropWidth').value) * factor;
        const height = parseFloat(document.getElementById('modalPropHeight').value) * factor;

        const newRect = [left, top, left + width, top + height];

        // Check if moved/resized
        if (JSON.stringify(newRect.map(v => Math.round(v))) !== JSON.stringify(modalOriginalRect.map(v => Math.round(v)))) {
            modalField.rect = newRect;
            recordFieldMove(modalField, newRect);
        }
    }

    // Collect all properties for styling
    const props = {
        tooltip: document.getElementById('modalPropTooltip').value,
        visibility: document.getElementById('modalPropVisibility').value,
        orientation: parseInt(document.getElementById('modalPropOrientation').value),
        readOnly: document.getElementById('modalPropReadOnly').checked,
        required: document.getElementById('modalPropRequired').checked,
        locked: document.getElementById('modalPropLocked').checked,

        borderColor: document.getElementById('modalPropBorderColor').value,
        fillColor: document.getElementById('modalPropFillColor').value,
        lineThickness: parseInt(document.getElementById('modalPropLineThickness').value) || 0,
        lineStyle: document.getElementById('modalPropLineStyle').value,
        checkboxStyle: document.getElementById('modalPropCheckboxStyle')?.value || 'check',

        textAlign: document.getElementById('modalPropAlignment').value,
        defaultValue: document.getElementById('modalPropDefaultValue').value,
        multiline: document.getElementById('modalPropMultiline').checked,
        scrollText: document.getElementById('modalPropScrollText').checked,
        password: document.getElementById('modalPropPassword').checked,
        maxLen: parseInt(document.getElementById('modalPropMaxLen').value) || 0,
        comb: document.getElementById('modalPropComb').checked,

        // Dropdown options and flags
        sort: document.getElementById('modalPropDropdownSort').checked,
        editable: document.getElementById('modalPropDropdownEditable').checked,
        spellCheck: document.getElementById('modalPropDropdownSpellCheck').checked,
        commitImmediately: document.getElementById('modalPropDropdownCommit').checked,
        options: modalField.type === 'combobox' || modalField.type === 'listbox' ? [...(modalField.options || [])] : undefined,

        // Position tab options
        noResizeOnMove: document.getElementById('modalPropNoResize')?.checked || false,
        positionLocked: document.getElementById('modalPropPositionLocked')?.checked || false
    };

    // Font properties — only include if user actually changed them
    if (fontDirty) {
        props.fontSize = parseFloat(document.getElementById('modalPropFontSize').value);
        props.fontColor = document.getElementById('modalPropTextColor').value;
        props.fontFamily = document.getElementById('modalPropFont').value;
    }

    // Format options — only include if user actually changed them
    if (formatDirty) {
        const formatCategory = document.getElementById('modalFormatCategory').value;
        if (formatCategory && formatCategory !== 'none') {
            props.formatType = formatCategory;  // Save format type for detection
        } else {
            props.formatType = '';  // Clear format type when set to none
        }
        if (formatCategory === 'date') {
            props.dateFormat = document.getElementById('modalDateFormat').value;
        } else if (formatCategory === 'currency') {
            props.currencySymbol = document.getElementById('modalCurrencySymbol').value;
            props.currencyDecimals = parseInt(document.getElementById('modalCurrencyDecimals').value);
            props.symbolLocation = document.getElementById('modalSymbolLocation').value;
        } else if (formatCategory === 'phone') {
            props.phoneFormat = document.getElementById('modalPhoneFormat').value;
        } else if (formatCategory === 'number') {
            props.numberDecimals = parseInt(document.getElementById('modalNumberDecimals').value);
            props.thousandSep = document.getElementById('modalNumberSeparator').value;
        } else if (formatCategory === 'zip') {
            props.zipFormat = document.getElementById('modalZipFormat').value;
        }
    }

    // Include actions if defined
    if (modalFieldActions && Object.keys(modalFieldActions).length > 0) {
        props.actions = modalFieldActions;
    }

    // Save calculation data from Calculate tab
    if (!isMultiFieldEdit && modalField) {
        saveCalcFromModal(modalField.name);
    }

    if (isMultiFieldEdit && multiFieldSelection.length > 0) {
        // Multi-field save
        multiFieldSelection.forEach(field => {
            // Apply to field object and its style container
            if (!field.style) field.style = {};
            Object.assign(field, props);
            Object.assign(field.style, props);
            // Track in styled changes
            changes.styled[field.name] = { ...props };
        });
        // NOTE: Do NOT save calculations in multi-field mode.
        // Calculations are per-field (each field has its own formula/sources).
        // Applying the modal's calc state to every selected field would
        // overwrite their individual existing calculations.
    } else {
        // Single field save
        if (!modalField.style) modalField.style = {};
        Object.assign(modalField, props);
        Object.assign(modalField.style, props);
        changes.styled[modalField.name] = props;
    }

    markUnsaved();
}


function savePropertiesModalSilent() {
    if (!modalField) return;
    _applyPropertiesFromForm();
    renderCanvas();
}


function savePropertiesModal() {
    if (!modalField) return;
    _applyPropertiesFromForm();
    updatePropertiesPanel();
    updateFieldList();
    renderCanvas();
}


function addActionToField() {
    const trigger = document.getElementById('modalActionTrigger').value;
    const script = document.getElementById('modalActionScript').value.trim();
    
    if (!script) {
        return; // Don't add empty scripts
    }
    
    modalFieldActions[trigger] = script;

    // Clear the script textarea
    document.getElementById('modalActionScript').value = '';

    updateActionsListDisplay();
    markUnsaved();
}


function removeActionFromField(trigger) {
    delete modalFieldActions[trigger];
    updateActionsListDisplay();
}


function updateActionsListDisplay() {
    const listEl = document.getElementById('modalActionsList');
    
    if (Object.keys(modalFieldActions).length === 0) {
        listEl.innerHTML = '<p style="font-size: 11px; color: var(--text-secondary);">No actions defined</p>';
        return;
    }
    
    const triggerLabels = {
        'Mouse Up': 'Mouse Up',
        'Mouse Down': 'Mouse Down',
        'Mouse Enter': 'Mouse Enter',
        'Mouse Exit': 'Mouse Exit',
        'On Focus': 'On Focus',
        'On Blur': 'On Blur',
        'Keystroke': 'Keystroke',
        'Format': 'Format',
        'Validate': 'Validate',
        'Calculate': 'Calculate',
        mouseUp: 'Mouse Up',
        mouseDown: 'Mouse Down',
        mouseEnter: 'Mouse Enter',
        mouseExit: 'Mouse Exit',
        onFocus: 'On Focus',
        onBlur: 'On Blur',
        keystroke: 'Keystroke',
        format: 'Format',
        validate: 'Validate',
        calculate: 'Calculate'
    };
    
    let html = '';
    for (const [trigger, script] of Object.entries(modalFieldActions)) {
        const label = triggerLabels[trigger] || trigger;
        const previewScript = script.length > 40 ? script.substring(0, 40) + '...' : script;
        const escapedTrigger = trigger.replace(/'/g, "\\'");
        html += `
            <div style="display: flex; align-items: center; justify-content: space-between; padding: 6px 8px; background: var(--bg-secondary); border-radius: 4px; margin-bottom: 4px;">
                <div style="flex: 1; min-width: 0;">
                    <span style="font-size: 11px; color: var(--accent); font-weight: 600;">${label}</span>
                    <br><span style="font-size: 10px; color: var(--text-secondary); font-family: monospace; word-break: break-all;">${previewScript}</span>
                </div>
                <div style="display: flex; gap: 4px; margin-left: 8px;">
                    <button onclick="editActionFromField('${escapedTrigger}')" style="background: var(--accent); color: white; border: none; border-radius: 4px; padding: 4px 8px; cursor: pointer; font-size: 10px;">✎ Edit</button>
                    <button onclick="removeActionFromField('${escapedTrigger}')" style="background: var(--danger); color: white; border: none; border-radius: 4px; padding: 4px 8px; cursor: pointer; font-size: 10px;">✕</button>
                </div>
            </div>
        `;
    }
    listEl.innerHTML = html;
}


function editActionFromField(trigger) {
    const script = modalFieldActions[trigger];
    if (script) {
        // Set the trigger dropdown to match
        const triggerSelect = document.getElementById('modalActionTrigger');
        // Find matching option (case-insensitive)
        for (const option of triggerSelect.options) {
            if (option.value === trigger || option.text === trigger) {
                triggerSelect.value = option.value;
                break;
            }
        }
        
        // Load script into textarea
        document.getElementById('modalActionScript').value = script;
        
        // Remove from list (will be re-added when user clicks Add)
        delete modalFieldActions[trigger];
        updateActionsListDisplay();
    }
}


function jsLibUpdateDropdowns() {
    const search  = (document.getElementById('jsLibSearch')?.value || '').toLowerCase().trim();
    const catSel  = document.getElementById('jsLibCategory');
    const presSel = document.getElementById('jsLibPreset');
    if (!catSel || !presSel) return;

    const selCat = catSel.value;

    // Filter presets by search + category
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

    // Rebuild category dropdown to only show categories that have matches when searching
    if (search) {
        const matchingCats = [...new Set(matches.map(m => m.category))];
        const allCats = [...new Set(JS_PRESETS.map(p => p.category))];
        catSel.innerHTML = '<option value="">All Categories</option>' +
            allCats.map(c => `<option value="${c}"${c === selCat ? ' selected' : ''}${!matchingCats.includes(c) ? ' style="color:var(--text-secondary)"' : ''}>${c}${!matchingCats.includes(c) ? ' (0)' : ` (${matches.filter(m => m.category === c).length})`}</option>`).join('');
    }

    // Rebuild preset dropdown
    const prevPreset = presSel.value;
    presSel.innerHTML = `<option value="">-- Select a preset${search ? ` (${matches.length} match${matches.length !== 1 ? 'es' : ''})` : ''} --</option>` +
        matches.map(p => `<option value="${p.id}"${p.id === prevPreset ? ' selected' : ''}>${p.name}</option>`).join('');

    // Re-select previously chosen preset if still valid
    const stillValid = matches.find(p => p.id === prevPreset);
    if (!stillValid) {
        _jsLibCurrentPreset = null;
        jsLibClearPanel();
    } else {
        jsLibOnPresetChange();
    }
}


function jsLibOnCategoryChange() {
    jsLibUpdateDropdowns();
}


function jsLibOnPresetChange() {
    const presSel = document.getElementById('jsLibPreset');
    const preset  = JS_PRESETS.find(p => p.id === presSel?.value);
    _jsLibCurrentPreset = preset || null;

    const descEl    = document.getElementById('jsLibDesc');
    const paramsEl  = document.getElementById('jsLibParams');
    const previewEl = document.getElementById('jsLibPreview');
    const insertBtn = document.getElementById('jsLibInsertBtn');

    if (!preset) {
        jsLibClearPanel();
        return;
    }

    if (descEl)   descEl.textContent = preset.description;
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
    if (insertBtn) {
        insertBtn.disabled = false;
        insertBtn.style.opacity = '1';
    }
}


function jsLibInsert() {
    if (!_jsLibCurrentPreset) return;

    const params = jsLibGetParams();
    // Validate required params (non-empty)
    for (const param of _jsLibCurrentPreset.params) {
        if (!params[param.id] || !params[param.id].trim()) {
            showAlert(`Please fill in the "${param.label}" field before inserting.`, 'Missing Parameter');
            document.getElementById(`jsLibParam_${param.id}`)?.focus();
            return;
        }
    }

    let code;
    try {
        code = _jsLibCurrentPreset.template(params);
    } catch (e) {
        showAlert('Failed to generate code snippet. Check parameters.', 'Error');
        return;
    }

    const textarea = document.getElementById('modalActionScript');
    if (!textarea) return;

    const existing = textarea.value.trim();
    if (existing) {
        // Append with a blank line separator
        textarea.value = existing + '\n\n' + code;
    } else {
        textarea.value = code;
    }

    // Scroll to end of textarea so user can see inserted code
    textarea.scrollTop = textarea.scrollHeight;

    // Visual feedback — briefly highlight the textarea
    textarea.style.outline = '2px solid var(--accent)';
    setTimeout(() => { textarea.style.outline = ''; }, 600);
}


