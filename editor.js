/**
 * Visual Field Editor - JavaScript
 * Handles canvas rendering, field selection, resize, and interaction
 */

const { ipcRenderer } = require('electron');

// ============ State ============
let pdfPath = null;
let outputPath = null;
let pages = [];
let fields = [];
let radioGroups = {};
let currentPage = 0;
let zoom = 1.0;
let selectedFields = [];
let changes = {
    deleted: [],
    renamed: {},
    moved: {},
    resized: {},
    styled: {},
    new_radio_groups: {},
    ungroup_radios: [],
    tab_order: {},
    calculations: {}
};
let hasUnsavedChanges = false;

// Helper: record a field move in both name-based and objgen-based change maps.
// objgen (PDF object number) is stable across saves — unlike sequential indices
// which shift when other fields are deleted.
function recordFieldMove(field, rect) {
    changes.moved[field.name] = { rect: [...rect], page: field.page };
    if (field.objgen) {
        if (!changes.moved_by_objgen) changes.moved_by_objgen = {};
        changes.moved_by_objgen[field.objgen.join(',')] = { rect: [...rect], page: field.page, name: field.name };
    }
}
function recordFieldResize(field, rect) {
    changes.resized[field.name] = { rect: [...rect], page: field.page };
    if (field.objgen) {
        if (!changes.resized_by_objgen) changes.resized_by_objgen = {};
        changes.resized_by_objgen[field.objgen.join(',')] = { rect: [...rect], page: field.page, name: field.name };
    }
}

// Generic pick-from-canvas mode. null = off, or { onPick: fn, onDisable: fn, btnId: string }
let pickMode = null;
function isPickModeActive() { return pickMode !== null; }
// Track whether user modified the Calculate tab (prevents accidental overwrites)
let calcDirty = false;
// Track whether user explicitly changed Format or Font properties in the modal
// (prevents overwriting original PDF formatting when user didn't touch these controls)
let formatDirty = false;
let fontDirty = false;

// Canvas state
let canvas, ctx;
let pageImage = null;
const DPI_SCALE = 150 / 72;  // PDF points to image pixels

// Drag/Resize state
let isDragging = false;
let isResizing = false;
let dragStart = { x: 0, y: 0 };
let dragField = null;
let resizeHandle = null;  // 'tl', 'tr', 'bl', 'br'
const HANDLE_SIZE = 8;
// Drag origin snapshot for snap / shift-constrain (canvas-px origin + per-field rect)
let dragOrigin = { x: 0, y: 0 };
let dragOrigRects = new Map();   // field.name → [...rect] at drag start
let resizeOrigRect = null;       // copy of dragField.rect at resize start
const SNAP_THRESHOLD_PX = 8;     // same visual threshold guides use

// Marquee selection (drag to select multiple)
let isMarqueeSelecting = false;
let marqueeStart = { x: 0, y: 0 };
let marqueeEnd = { x: 0, y: 0 };

// Undo/Redo stacks
let undoStack = [];
let redoStack = [];
const MAX_UNDO = 50;

// Preview mode
let isPreviewMode = false;

// Input modal callback
let inputModalResolve = null;

// Clipboard for copy/paste
let clipboard = [];
let clipboardSourcePage = null;
let lastPastePosition = null;  // Track for successive paste-in-place operations

// ============ Render Throttling ============
let renderPending = false;
function requestRender() {
    if (!renderPending) {
        renderPending = true;
        requestAnimationFrame(() => {
            renderPending = false;
            renderCanvas();
        });
    }
}

// ============ Tool State ============
let currentTool = 'select';  // 'select', 'text', 'checkbox', 'radio', 'dropdown', 'signature', 'date'
let isCreatingField = false;
let creationStart = { x: 0, y: 0 };
let creationEnd = { x: 0, y: 0 };
let newFieldCounter = 1;  // For generating unique field names

// Show custom input modal (replaces window.prompt which doesn't work in Electron)
function showInputModal(title, defaultValue = '') {
    return new Promise((resolve) => {
        inputModalResolve = resolve;
        document.getElementById('inputModalTitle').textContent = title;
        document.getElementById('inputModalValue').value = defaultValue;
        document.getElementById('inputModal').style.display = 'flex';
        document.getElementById('inputModalValue').focus();
        document.getElementById('inputModalValue').select();
        
        // Add one-time listener for Enter key
        const handleEnter = (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                document.getElementById('inputModalValue').removeEventListener('keydown', handleEnter);
                resolveInputModal(document.getElementById('inputModalValue').value);
            } else if (e.key === 'Escape') {
                e.preventDefault();
                document.getElementById('inputModalValue').removeEventListener('keydown', handleEnter);
                resolveInputModal(null);
            }
        };
        document.getElementById('inputModalValue').addEventListener('keydown', handleEnter);
    });
}

// Resolve the input modal (called by OK/Cancel buttons)
function resolveInputModal(value) {
    document.getElementById('inputModal').style.display = 'none';
    if (inputModalResolve) {
        inputModalResolve(value);
        inputModalResolve = null;
    }
}

// ============ Initialization ============
document.addEventListener('DOMContentLoaded', async () => {
    canvas = document.getElementById('editorCanvas');
    ctx = canvas.getContext('2d');
    
    const data = await ipcRenderer.invoke('get-editor-data');
    if (data) {
        pdfPath = data.pdfPath;
        outputPath = data.outputPath;
        // Store global field settings for use as defaults
        if (data.globalFieldSettings) {
            window.globalFieldSettings = data.globalFieldSettings;
        }
        await loadPdfData();
    } else {
        showError('No PDF data received');
    }
    
    setupEventListeners();
});

// Release resources when editor window closes
window.addEventListener('beforeunload', () => {
    // Release page images
    pages = [];
    pageImage = null;
    // Release field data
    fields = [];
    selectedFields = [];
    // Release undo/redo stacks
    undoStack = [];
    redoStack = [];
    // Release clipboard
    clipboard = [];
});

async function loadPdfData() {
    showLoading('Extracting fields and converting pages...');
    
    try {
        const result = await ipcRenderer.invoke('extract-fields', pdfPath);
        
        if (result.error) {
            throw new Error(result.error);
        }
        
        pages = result.pages || [];
        fields = result.fields || [];
        radioGroups = result.radio_groups || {};
        
        updateFieldCount();
        document.getElementById('totalPages').textContent = pages.length;
        
        // Build page thumbnails
        buildThumbnails();
        
        if (pages.length > 0) {
            await loadPage(0);
        }
        
        hideLoading();
    } catch (error) {
        showError(`Failed to load PDF: ${error.message}`);
    }
}

// Build page thumbnails sidebar
function buildThumbnails() {
    const list = document.getElementById('thumbnailsList');
    if (!list) return;
    
    list.innerHTML = '';
    
    pages.forEach((page, index) => {
        const item = document.createElement('div');
        item.className = 'thumbnail-item' + (index === 0 ? ' active' : '');
        item.dataset.page = index;
        item.innerHTML = `
            <img class="thumbnail-img" src="${page.image_path}" alt="Page ${index + 1}">
            <div class="thumbnail-label">Page ${index + 1}</div>
        `;
        item.addEventListener('click', () => {
            if (viewMode === 'scroll') {
                // Scroll the scroll-view container exactly so the target page-wrap
                // is at the top. Use explicit math — scrollIntoView is unreliable
                // with nested scroll containers.
                const container = document.getElementById('scrollViewContainer');
                const pageWrap = container?.querySelector(`.sv-page-wrap[data-page-idx="${index}"]`);
                if (container && pageWrap) {
                    const cRect = container.getBoundingClientRect();
                    const wRect = pageWrap.getBoundingClientRect();
                    const target = container.scrollTop + (wRect.top - cRect.top) - 8;
                    container.scrollTo({ top: target, behavior: 'smooth' });
                }
                currentPage = index;
                updateActiveThumbnail();
            } else {
                loadPage(index);
            }
        });
        list.appendChild(item);
    });
}

// Update active thumbnail
function updateActiveThumbnail() {
    const items = document.querySelectorAll('.thumbnail-item');
    items.forEach((item, index) => {
        item.classList.toggle('active', index === currentPage);
    });

    // Scroll the thumbnail LIST so the active item stays visible.
    // Use getBoundingClientRect — offsetTop is unreliable without a positioned ancestor.
    const activeItem    = document.querySelector('.thumbnail-item.active');
    const thumbnailList = document.getElementById('thumbnailsList');
    if (activeItem && thumbnailList) {
        const listRect = thumbnailList.getBoundingClientRect();
        const itemRect = activeItem.getBoundingClientRect();
        const itemTopInList = itemRect.top - listRect.top + thumbnailList.scrollTop;
        const itemH    = itemRect.height;
        const listH    = thumbnailList.clientHeight;
        const scroll   = thumbnailList.scrollTop;
        if (itemTopInList < scroll || itemTopInList + itemH > scroll + listH) {
            thumbnailList.scrollTop = itemTopInList - listH / 2 + itemH / 2;
        }
    }
}

async function loadPage(pageNum, resetZoom = false) {
    if (pageNum < 0 || pageNum >= pages.length) return;
    
    // Store current zoom to preserve it if not first load
    const previousZoom = zoom;
    const isFirstLoad = !pageImage;
    
    currentPage = pageNum;
    document.getElementById('pageInput').value = pageNum + 1;
    updateActiveThumbnail();
    
    const page = pages[pageNum];
    
    pageImage = new Image();
    pageImage.onload = () => {
        // Only fit to page on initial load or if explicitly requested
        if (isFirstLoad || resetZoom) {
            fitToPage();
        } else {
            // Preserve the user's current zoom level
            document.getElementById('zoomLevel').textContent = Math.round(previousZoom * 100) + '%';
        }
        renderCanvas();
        updateFieldList();  // Update field list for new page
        
        // Re-render preview inputs for new page if in preview mode
        if (isPreviewMode) {
            renderPreviewInputs();
        }
    };
    pageImage.src = page.image_path;
}

function fitToPage() {
    if (!pageImage) return;
    
    const wrapper = document.getElementById('canvasWrapper');
    const wrapperWidth = wrapper.clientWidth - 40;
    const wrapperHeight = wrapper.clientHeight - 40;
    
    const scaleX = wrapperWidth / pageImage.width;
    const scaleY = wrapperHeight / pageImage.height;
    
    zoom = Math.min(scaleX, scaleY, 1.0);
    zoom = Math.max(0.25, zoom);
    
    document.getElementById('zoomLevel').textContent = Math.round(zoom * 100) + '%';
}

/** Apply the global zoom variable to scroll-view page widths.
 *  zoom is expressed relative to NATURAL image size (same convention as single view).
 *  So zoom=1.0 means 150 DPI (full natural). Overlays use % coords → auto-scale. */
function applyScrollZoom() {
    const container = document.getElementById('scrollViewContainer');
    if (!container) return;
    const firstWrap = container.querySelector('.sv-page-wrap');
    if (!firstWrap) return;
    // Use deterministic page dims (works even before any image decodes)
    const pageW = parseFloat(firstWrap.dataset.pageW || 612);
    const iw    = pageW * DPI_SCALE;
    const targetW = Math.round(iw * zoom);
    container.querySelectorAll('.sv-page-wrap').forEach(wrap => {
        wrap.style.width       = targetW + 'px';
        wrap.style.marginLeft  = 'auto';
        wrap.style.marginRight = 'auto';
    });
    // Broadcast zoom to CSS so icon / badge sizes track it (same scaling law as single view)
    container.style.setProperty('--sv-zoom', zoom);
    const zl = document.getElementById('zoomLevel');
    if (zl) zl.textContent = Math.round(zoom * 100) + '%';
}

/** Compute "fit whole first page in viewport" zoom and apply it.
 *  If images aren't loaded yet, retries once the first image fires load. */
function fitScrollToPage() {
    const container = document.getElementById('scrollViewContainer');
    if (!container) return;
    const firstWrap = container.querySelector('.sv-page-wrap');
    if (!firstWrap) return;
    // Use deterministic page dimensions (works even before images decode)
    const pageW = parseFloat(firstWrap.dataset.pageW || 612);
    const pageH = parseFloat(firstWrap.dataset.pageH || 792);
    const iw    = pageW * DPI_SCALE;
    const ih    = pageH * DPI_SCALE;
    const availW = container.clientWidth  - 32;   // 16 px padding each side
    const availH = container.clientHeight - 32;
    zoom = Math.max(0.1, Math.min(availW / iw, availH / ih, 2.0));
    const zl = document.getElementById('zoomLevel');
    if (zl) zl.textContent = Math.round(zoom * 100) + '%';

    // Capture the page we want to stay on BEFORE applyScrollZoom resizes wraps.
    // Otherwise the scroll-listener fires on the reflow and overwrites currentPage
    // to whatever page now lies under the old scrollTop.
    const targetPage = currentPage;
    applyScrollZoom();
    requestAnimationFrame(() => {
        scrollToPageInScrollView(targetPage);
        currentPage = targetPage;
        updateActiveThumbnail();
    });
}

// ============ v1.1.0: View Mode (Single / Scroll) ============
function setViewMode(mode) {
    if (mode === viewMode) return;

    // Save / restore per-view zoom
    if (mode === 'scroll') {
        singleViewZoom = zoom;   // remember single-view zoom
        zoom = 1.0;              // scroll view starts at fit-to-width (100 %)
    } else {
        zoom = singleViewZoom;   // restore single-view zoom
    }

    viewMode = mode;

    // Update toolbar button states
    document.getElementById('btnViewSingle')?.classList.toggle('active', mode === 'single');
    document.getElementById('btnViewScroll')?.classList.toggle('active', mode === 'scroll');

    // Show/hide page nav controls (dimmed in scroll — all pages visible at once)
    const pageNav = document.querySelector('.page-nav');
    if (pageNav) pageNav.style.opacity = mode === 'scroll' ? '0.4' : '1';

    const editorCanvas  = document.getElementById('editorCanvas');
    const scrollViewEl  = document.getElementById('scrollViewContainer');

    if (mode === 'scroll') {
        editorCanvas.style.display = 'none';
        scrollViewEl.style.display = 'block';
        buildScrollView();
        // Fit whole first page in viewport once images are ready,
        // then scroll to the page we were on in single view
        const targetPage = currentPage;
        requestAnimationFrame(() => {
            fitScrollToPage();
            // fitScrollToPage resizes wraps — wait a frame then scroll to the target page
            requestAnimationFrame(() => scrollToPageInScrollView(targetPage));
        });
    } else {
        scrollViewEl.style.display = 'none';
        editorCanvas.style.display = 'block';
        loadPage(currentPage);
    }
    updateFieldList();
}

/**
 * DOM-based scroll view — all pages stacked as <img> elements with
 * absolutely-positioned field overlay <div>s (% coords → auto-scale).
 * Supports: click/shift-click select, double-click modal, drag to move,
 * drag-handle to resize, and thumbnail sync on scroll.
 */
function buildScrollView() {
    const container = document.getElementById('scrollViewContainer');
    if (!container) return;
    container.innerHTML = '';
    container.style.setProperty('--sv-zoom', zoom);  // initial value for icon/badge scaling

    // IntersectionObserver to lazy-load page images as they enter the viewport.
    // Native loading="lazy" won't help here because the scroll container itself
    // is in the document viewport — we need to observe within the scroll container.
    const lazyObserver = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (!entry.isIntersecting) return;
            const img = entry.target;
            const realSrc = img.dataset.src;
            if (realSrc && img.src !== realSrc) {
                img.src = realSrc;
            }
            lazyObserver.unobserve(img);
        });
    }, { root: container, rootMargin: '600px 0px' });   // preload ~600px ahead

    pages.forEach((page, pageIdx) => {
        // ---- page wrapper ----
        const wrap = document.createElement('div');
        wrap.className    = 'sv-page-wrap';
        wrap.dataset.pageIdx = pageIdx;
        wrap.dataset.pageW   = page.width  || 612;     // PDF points
        wrap.dataset.pageH   = page.height || 792;

        // Reserve correct height up-front via CSS aspect-ratio so layout doesn't
        // reflow as lazy-loaded images decode. width / height = page.width / page.height.
        if (page.width && page.height) {
            wrap.style.aspectRatio = `${page.width} / ${page.height}`;
        }

        // ---- page image (lazy) ----
        const img = document.createElement('img');
        img.decoding   = 'async';
        img.dataset.src = page.image_path;       // real source (loaded on demand)
        img.className  = 'sv-page-img';
        img.alt        = `Page ${pageIdx + 1}`;
        // First page loads eagerly so user sees something immediately
        if (pageIdx === 0) img.src = page.image_path;
        else                lazyObserver.observe(img);
        wrap.appendChild(img);

        // ---- page label ----
        const lbl = document.createElement('div');
        lbl.className   = 'sv-page-label';
        lbl.textContent = `Page ${pageIdx + 1}`;
        wrap.appendChild(lbl);

        // ---- add overlays immediately (positions are %; don't need decoded image) ----
        // Use deterministic page dimensions × DPI so overlays work even when
        // the underlying <img> hasn't loaded yet (lazy pages).
        const iw = (page.width  || 612) * DPI_SCALE;
        const ih = (page.height || 792) * DPI_SCALE;
        fields.filter(f => f.page === pageIdx).forEach(field => {
            svBuildOverlay(field, wrap, iw, ih);
        });

        container.appendChild(wrap);
    });
    updateScrollFieldHighlights();

    // Auto-track current page + thumbnail as the user scrolls
    container.addEventListener('scroll', () => {
        const pageWraps     = container.querySelectorAll('.sv-page-wrap');
        const containerRect = container.getBoundingClientRect();
        let bestPage    = currentPage;
        let bestVisible = -1;
        pageWraps.forEach((w, idx) => {
            const r       = w.getBoundingClientRect();
            const visible = Math.max(0, Math.min(r.bottom, containerRect.bottom) - Math.max(r.top, containerRect.top));
            if (visible > bestVisible) { bestVisible = visible; bestPage = idx; }
        });
        if (bestPage !== currentPage) {
            currentPage = bestPage;
            updateActiveThumbnail();
        }
    }, { passive: true });

    // Marquee drag-to-select on empty container area (matches single-view behavior)
    setupScrollViewMarquee(container);
}

/** Scroll the scroll-view container so the target page top is visible. */
function scrollToPageInScrollView(pageIdx) {
    const container = document.getElementById('scrollViewContainer');
    if (!container) return;
    const pageWrap = container.querySelector(`.sv-page-wrap[data-page-idx="${pageIdx}"]`);
    if (!pageWrap) return;
    const cRect = container.getBoundingClientRect();
    const wRect = pageWrap.getBoundingClientRect();
    container.scrollTop = container.scrollTop + (wRect.top - cRect.top) - 8;
}

// ============ Scroll-view marquee selection ============
let svMarqueeActive  = false;
let svMarqueeStart   = null;   // {x, y} — viewport (clientX/Y) pixels
let svMarqueeEl      = null;   // floating <div> (position:fixed)
let svMarqueeShift   = false;

function setupScrollViewMarquee(container) {
    container.addEventListener('mousedown', (e) => {
        // Only start marquee on left-button clicks on blank area
        // (overlays/handles stop propagation on their own mousedown handlers)
        if (e.button !== 0) return;
        if (e.target.closest('.sv-field-overlay')) return;
        if (e.target.closest('.sv-handle')) return;

        svMarqueeActive = true;
        svMarqueeShift  = e.shiftKey;
        svMarqueeStart  = { x: e.clientX, y: e.clientY };

        // Clear selection unless shift held (matches single view)
        if (!e.shiftKey) {
            selectedFields = [];
            updateScrollFieldHighlights();
            updatePropertiesPanel();
            updateFieldList();
        }

        // Create floating marquee rectangle (viewport-fixed)
        svMarqueeEl = document.createElement('div');
        svMarqueeEl.className = 'sv-marquee';
        svMarqueeEl.style.cssText = 'position:fixed;border:1px dashed #64ffda;' +
            'background:rgba(100,255,218,0.1);pointer-events:none;z-index:99999;';
        svMarqueeEl.style.left   = e.clientX + 'px';
        svMarqueeEl.style.top    = e.clientY + 'px';
        svMarqueeEl.style.width  = '0px';
        svMarqueeEl.style.height = '0px';
        document.body.appendChild(svMarqueeEl);

        e.preventDefault();
    });

    document.addEventListener('mousemove', (e) => {
        if (!svMarqueeActive || !svMarqueeEl) return;
        const x = Math.min(svMarqueeStart.x, e.clientX);
        const y = Math.min(svMarqueeStart.y, e.clientY);
        const w = Math.abs(e.clientX - svMarqueeStart.x);
        const h = Math.abs(e.clientY - svMarqueeStart.y);
        svMarqueeEl.style.left   = x + 'px';
        svMarqueeEl.style.top    = y + 'px';
        svMarqueeEl.style.width  = w + 'px';
        svMarqueeEl.style.height = h + 'px';
    });

    document.addEventListener('mouseup', (e) => {
        if (!svMarqueeActive) return;
        svMarqueeActive = false;

        if (!svMarqueeEl) return;
        const mLeft   = parseFloat(svMarqueeEl.style.left);
        const mTop    = parseFloat(svMarqueeEl.style.top);
        const mRight  = mLeft + parseFloat(svMarqueeEl.style.width);
        const mBottom = mTop  + parseFloat(svMarqueeEl.style.height);

        // Only perform hit-testing if marquee has meaningful size
        if ((mRight - mLeft) > 2 && (mBottom - mTop) > 2) {
            const hits = [];
            container.querySelectorAll('.sv-field-overlay').forEach(ov => {
                const r = ov.getBoundingClientRect();
                const intersects = !(r.right  < mLeft || r.left > mRight ||
                                     r.bottom < mTop  || r.top  > mBottom);
                if (intersects) {
                    const uid = ov.dataset.fieldUid;
                    const field = fields.find(f => fieldUID(f) === uid);
                    if (field) hits.push(field);
                }
            });
            if (hits.length) {
                hits.forEach(f => {
                    if (!selectedFields.includes(f)) selectedFields.push(f);
                });
                updateScrollFieldHighlights();
                updatePropertiesPanel();
                updateFieldList();
            }
        }

        svMarqueeEl.remove();
        svMarqueeEl   = null;
        svMarqueeStart = null;
    });
}

/** Build a single field overlay <div> and attach all interaction handlers. */
/** Stable per-field identifier. Radio group members share `.name` in the PDF
 *  spec (distinguished only by their "on" value / groupIndex), so we include
 *  groupIndex + page to guarantee overlay uniqueness in the scroll view DOM. */
function fieldUID(field) {
    const gi = (field.groupIndex != null) ? field.groupIndex : 0;
    return `${field.page}::${field.name}::${gi}`;
}

function svBuildOverlay(field, wrap, iw, ih) {
    const [x1, y1, x2, y2] = field.rect;
    const px1 = x1 * DPI_SCALE, py1 = y1 * DPI_SCALE;
    const pw  = (x2 - x1) * DPI_SCALE;
    const ph  = (y2 - y1) * DPI_SCALE;

    const ov = document.createElement('div');
    ov.className         = 'sv-field-overlay';
    ov.dataset.fieldName = field.name;
    ov.dataset.fieldUid  = fieldUID(field);
    svPositionOverlay(ov, field, iw, ih);

    // Type colour
    const fieldHasCalc = !!(changes.calculations[field.name] || field.calculation)?.type;
    if      (field.type === 'checkbox') ov.classList.add('sv-checkbox');
    else if (field.type === 'radio')    ov.classList.add('sv-radio');
    else if (field.type === 'link')     ov.classList.add('sv-link');
    else if (fieldHasCalc)              ov.classList.add('sv-calc');

    // ---- Type icon (top-left) — matches single view drawField() ----
    let typeIcon = '📝';
    if (field.type === 'link') {
        typeIcon = '🔗';
    } else if (field.type === 'checkbox' || field.type === 'radio') {
        const style = field.style?.checkboxStyle || field.checkboxStyle || 'check';
        const iconMap = { 'check': '✓', 'circle': '●', 'cross': '✖',
                          'square': '■', 'diamond': '◆', 'star': '★' };
        typeIcon = field.type === 'radio' ? '🔘' : (iconMap[style] || '☐');
    }
    const iconEl = document.createElement('div');
    iconEl.className   = 'sv-field-icon';
    iconEl.textContent = typeIcon;
    ov.appendChild(iconEl);

    // ---- Σ calc badge (top-right) ----
    if (fieldHasCalc) {
        const badge = document.createElement('div');
        badge.className   = 'sv-calc-badge';
        badge.textContent = 'Σ';
        ov.appendChild(badge);
    }

    // Resolve the live field object from the overlay's UID — fields[] may be
    // replaced (undo/redo/paste) after the overlay was built, so the closure
    // reference can become stale. Always look up by UID at event time.
    const liveField = () => {
        const uid = ov.dataset.fieldUid;
        return fields.find(f => fieldUID(f) === uid) || field;
    };

    // ---- click: select ----
    ov.addEventListener('click', (e) => {
        e.stopPropagation();
        const f = liveField();
        if (e.shiftKey || e.metaKey) {
            const idx = selectedFields.indexOf(f);
            idx >= 0 ? selectedFields.splice(idx, 1) : selectedFields.push(f);
        } else {
            selectedFields = [f];
        }
        currentPage = f.page;
        updatePropertiesPanel();
        updateFieldList();
        updateScrollFieldHighlights();
    });

    // ---- double-click: open properties modal ----
    ov.addEventListener('dblclick', (e) => {
        e.stopPropagation();
        const f = liveField();
        selectedFields = [f];
        currentPage    = f.page;
        updatePropertiesPanel();
        updateFieldList();
        updateScrollFieldHighlights();
        try { openPropertiesModal(f); } catch(err) { console.error(err); }
    });

    // ---- mousedown: start drag (move) ----
    ov.addEventListener('mousedown', (e) => {
        if (e.button !== 0 || e.target.classList.contains('sv-handle')) return;
        e.stopPropagation();
        e.preventDefault();
        const f = liveField();
        if (!e.shiftKey) {
            // If already part of the current selection, keep the multi-selection
            // so the user can drag the whole group. Otherwise replace selection.
            if (!selectedFields.includes(f)) selectedFields = [f];
        } else {
            const idx = selectedFields.indexOf(f);
            idx >= 0 ? selectedFields.splice(idx, 1) : selectedFields.push(f);
        }
        currentPage = f.page;
        saveState();
        updatePropertiesPanel();
        updateFieldList();
        updateScrollFieldHighlights();

        svDragField    = f;
        svDragPageWrap = wrap;
        // Snapshot rects for all selected fields so multi-select moves cleanly
        const groupOrig = {};
        selectedFields.forEach(sf => { groupOrig[sf.name] = [...sf.rect]; });
        f._svGroupOrig = groupOrig;
        svDragStart    = { clientX: e.clientX, clientY: e.clientY, rect: [...f.rect] };
        document.addEventListener('mousemove', svOnMouseMove);
        document.addEventListener('mouseup',   svOnMouseUp);
    });

    wrap.appendChild(ov);
}

/** Update an overlay's CSS position/size from the field's current rect. */
function svPositionOverlay(ov, field, iw, ih) {
    const [x1, y1, x2, y2] = field.rect;
    const px1 = x1 * DPI_SCALE, py1 = y1 * DPI_SCALE;
    const px2 = x2 * DPI_SCALE, py2 = y2 * DPI_SCALE;
    const pw  = px2 - px1;
    const ph  = py2 - py1;

    // Hide overlays whose rect is fully outside the page bounds — malformed PDF
    // annotations occasionally have off-page coordinates that would otherwise
    // appear as stray green dots scattered around the scroll-view canvas.
    const offPage = (px2 < 0 || py2 < 0 || px1 > iw || py1 > ih);
    if (offPage) {
        ov.style.display = 'none';
        return;
    }
    ov.style.display = '';

    ov.style.left   = `${(px1 / iw * 100).toFixed(4)}%`;
    ov.style.top    = `${(py1 / ih * 100).toFixed(4)}%`;
    ov.style.width  = `${(pw  / iw * 100).toFixed(4)}%`;
    ov.style.height = `${(ph  / ih * 100).toFixed(4)}%`;
}

/** Return effective image-pixel dimensions for a page wrap, falling back to
 *  deterministic page.width × DPI when the underlying <img> hasn't loaded. */
function svGetWrapImgDims(wrap) {
    const img = wrap?.querySelector('.sv-page-img');
    let iw = img?.naturalWidth || 0;
    let ih = img?.naturalHeight || 0;
    if (!iw || !ih) {
        iw = parseFloat(wrap.dataset.pageW || 612) * DPI_SCALE;
        ih = parseFloat(wrap.dataset.pageH || 792) * DPI_SCALE;
    }
    return { iw, ih };
}

// ---- Scroll-view drag (move) handlers ----
function svOnMouseMove(e) {
    if (!svDragField || !svDragPageWrap) return;
    const { iw, ih } = svGetWrapImgDims(svDragPageWrap);
    const wrapW = svDragPageWrap.clientWidth;
    if (!iw || !wrapW) return;

    // Convert screen-pixel delta → PDF-point delta
    const scale  = wrapW / iw * DPI_SCALE;   // screen-px per PDF-pt
    let dxPdf   = (e.clientX - svDragStart.clientX) / scale;
    let dyPdf   = (e.clientY - svDragStart.clientY) / scale;

    // Shift held → lock to dominant axis
    if (e.shiftKey) {
        if (Math.abs(dxPdf) > Math.abs(dyPdf)) dyPdf = 0; else dxPdf = 0;
    }

    const orig = svDragStart.rect;
    const w    = orig[2] - orig[0];
    const h    = orig[3] - orig[1];

    // Snap primary field — compute offset via shared helper, apply to whole group
    if (!e.shiftKey) {
        const proposed = [orig[0] + dxPdf, orig[1] + dyPdf,
                          orig[0] + dxPdf + w, orig[1] + dyPdf + h];
        const snap = computeMoveSnap(proposed, svDragField);
        dxPdf += snap.sx;
        dyPdf += snap.sy;
    }

    // Move all selected fields by the same delta
    for (const f of selectedFields) {
        if (f === svDragField) {
            f.rect[0] = orig[0] + dxPdf;
            f.rect[1] = orig[1] + dyPdf;
            f.rect[2] = orig[0] + dxPdf + w;
            f.rect[3] = orig[1] + dyPdf + h;
        } else {
            const fo = svDragField._svGroupOrig?.[f.name];
            if (fo) {
                const fw = fo[2] - fo[0], fh = fo[3] - fo[1];
                f.rect[0] = fo[0] + dxPdf; f.rect[1] = fo[1] + dyPdf;
                f.rect[2] = fo[0] + dxPdf + fw; f.rect[3] = fo[1] + dyPdf + fh;
            }
        }
        recordFieldMove(f, f.rect);
        // Reposition the overlay
        const ov = document.querySelector(`#scrollViewContainer .sv-field-overlay[data-field-uid="${CSS.escape(fieldUID(f))}"]`);
        if (ov) svPositionOverlay(ov, f, iw, ih);
    }
    markUnsaved();
    updatePropertiesPanel();

    // Draw alignment guides while dragging (parity with single view)
    if (selectedFields.length === 1) {
        drawScrollViewGuides(selectedFields[0], svDragPageWrap, iw, ih);
    }
}

function svOnMouseUp() {
    svDragField    = null;
    svDragPageWrap = null;
    svDragStart    = null;
    document.removeEventListener('mousemove', svOnMouseMove);
    document.removeEventListener('mouseup',   svOnMouseUp);
    clearScrollViewGuides();
}

// ---- Scroll-view resize handlers (per handle) ----
/**
 * Clamp a rect to a minimum size, moving only the edge controlled by the
 * active resize handle. This prevents tiny square fields (e.g. 10×10pt
 * checkboxes) from being forced into a rectangle the instant resize begins.
 *   tl / bl / ml  → left edge moves  (right edge anchored)
 *   tr / br / mr  → right edge moves (left edge anchored)
 *   tl / tr / mt  → top edge moves   (bottom edge anchored)
 *   bl / br / mb  → bottom edge moves (top edge anchored)
 */
function clampMinRectSize(r, handleDir, minW = 4, minH = 4) {
    if (r[2] - r[0] < minW) {
        if (handleDir === 'tl' || handleDir === 'bl' || handleDir === 'ml') {
            r[0] = r[2] - minW;   // left edge moving → pin right, shift left back
        } else {
            r[2] = r[0] + minW;   // right edge moving (or no-op for mt/mb)
        }
    }
    if (r[3] - r[1] < minH) {
        if (handleDir === 'tl' || handleDir === 'tr' || handleDir === 'mt') {
            r[1] = r[3] - minH;   // top edge moving → pin bottom, shift top back
        } else {
            r[3] = r[1] + minH;   // bottom edge moving (or no-op for ml/mr)
        }
    }
}

function svOnResizeMove(e, field, wrap, handleDir, resizeStart) {
    const { iw, ih } = svGetWrapImgDims(wrap);
    const wrapW = wrap.clientWidth;
    if (!iw || !wrapW) return;

    const scale = wrapW / iw * DPI_SCALE;
    let dx    = (e.clientX - resizeStart.clientX) / scale;
    let dy    = (e.clientY - resizeStart.clientY) / scale;
    const orig  = resizeStart.rect;
    const origW = orig[2] - orig[0];
    const origH = orig[3] - orig[1];

    // Shift on a corner = maintain aspect ratio (works growing AND shrinking).
    // Treat each axis' drag as a signed "growth" (positive = bigger, negative =
    // smaller), pick the axis the user moved more in relative terms, and apply
    // the same signed scale to both axes so the rect stays proportional even
    // when dragged past the original size.
    if (e.shiftKey && ['tl','tr','bl','br'].includes(handleDir) && origW > 0 && origH > 0) {
        const sx = (handleDir === 'tl' || handleDir === 'bl') ? -1 : 1;
        const sy = (handleDir === 'tl' || handleDir === 'tr') ? -1 : 1;
        const rx = (dx * sx) / origW;   // signed growth fraction on X
        const ry = (dy * sy) / origH;   // signed growth fraction on Y
        const r  = Math.abs(rx) > Math.abs(ry) ? rx : ry;
        dx = r * origW * sx;
        dy = r * origH * sy;
    }

    const r = [...orig];
    if      (handleDir === 'tl') { r[0] = orig[0]+dx; r[1] = orig[1]+dy; }
    else if (handleDir === 'tr') { r[2] = orig[2]+dx; r[1] = orig[1]+dy; }
    else if (handleDir === 'bl') { r[0] = orig[0]+dx; r[3] = orig[3]+dy; }
    else if (handleDir === 'br') { r[2] = orig[2]+dx; r[3] = orig[3]+dy; }
    else if (handleDir === 'ml') { r[0] = orig[0]+dx; }
    else if (handleDir === 'mr') { r[2] = orig[2]+dx; }
    else if (handleDir === 'mt') { r[1] = orig[1]+dy; }
    else if (handleDir === 'mb') { r[3] = orig[3]+dy; }

    if (!e.shiftKey) applyEdgeSnap(r, field, handleDir);

    clampMinRectSize(r, handleDir);

    field.rect[0] = r[0]; field.rect[1] = r[1];
    field.rect[2] = r[2]; field.rect[3] = r[3];
    recordFieldResize(field, field.rect);
    markUnsaved();

    const ov = wrap.querySelector(`.sv-field-overlay[data-field-uid="${CSS.escape(fieldUID(field))}"]`);
    if (ov) svPositionOverlay(ov, field, iw, ih);
    updatePropertiesPanel();

    // Draw alignment guides while resizing (parity with single view)
    drawScrollViewGuides(field, wrap, iw, ih);
}

/**
 * Sync scroll-view DOM with the fields[] model.
 * - Repositions overlays whose field rect changed (nudge, drag, align, etc.)
 * - Creates overlays for newly-added fields (paste, duplicate, new fields)
 * - Removes overlays for fields no longer in the model (delete, cut)
 */
function syncScrollViewDOM() {
    const container = document.getElementById('scrollViewContainer');
    if (!container) return;
    const presentUIDs = new Set();

    fields.forEach(field => {
        const uid = fieldUID(field);
        presentUIDs.add(uid);
        const wrap = container.querySelector(`.sv-page-wrap[data-page-idx="${field.page}"]`);
        if (!wrap) return;   // page wrap not built yet
        const { iw, ih } = svGetWrapImgDims(wrap);

        let ov = wrap.querySelector(`.sv-field-overlay[data-field-uid="${CSS.escape(uid)}"]`);
        if (!ov) {
            // New field — build overlay
            svBuildOverlay(field, wrap, iw, ih);
        } else {
            svPositionOverlay(ov, field, iw, ih);
        }
    });

    // Remove overlays whose field no longer exists
    container.querySelectorAll('.sv-field-overlay').forEach(el => {
        if (!presentUIDs.has(el.dataset.fieldUid)) el.remove();
    });
}

/** Refresh selected-state CSS + resize handles on all scroll-view overlays. */
function updateScrollFieldHighlights() {
    document.querySelectorAll('.sv-field-overlay').forEach(el => {
        const uid        = el.dataset.fieldUid;
        const field      = fields.find(f => fieldUID(f) === uid);
        const isSelected = !!(field && selectedFields.includes(field));
        el.classList.toggle('selected', isSelected);

        // Remove stale handles
        el.querySelectorAll('.sv-handle').forEach(h => h.remove());

        // Add 8 resize handles when this field is the sole selection (matches single view)
        if (isSelected && selectedFields.length === 1 && field) {
            const wrap = el.closest('.sv-page-wrap');
            const img  = wrap?.querySelector('.sv-page-img');
            ['tl','tr','bl','br','mt','mb','ml','mr'].forEach(dir => {
                const h = document.createElement('div');
                h.className       = `sv-handle sv-handle-${dir}`;
                h.dataset.dir     = dir;
                h.addEventListener('mousedown', (e) => {
                    if (e.button !== 0) return;
                    e.stopPropagation();
                    e.preventDefault();
                    // Re-resolve field live to avoid stale closure after fields[] replacement
                    const liveF = fields.find(ff => fieldUID(ff) === uid) || field;
                    saveState();
                    const resizeStart = { clientX: e.clientX, clientY: e.clientY, rect: [...liveF.rect] };
                    const onMove = (ev) => svOnResizeMove(ev, liveF, wrap, dir, resizeStart);
                    const onUp   = () => {
                        document.removeEventListener('mousemove', onMove);
                        document.removeEventListener('mouseup',   onUp);
                        clearScrollViewGuides();
                    };
                    document.addEventListener('mousemove', onMove);
                    document.addEventListener('mouseup',   onUp);
                });
                el.appendChild(h);
            });
        }
    });
}

/**
 * Compute snap offset (in PDF points) for a proposed moved rect.
 * Returns { sx, sy } — add these to the move delta to snap left/right/center-X,
 * top/bottom/center-Y to any other field on the same page.
 * Threshold is expressed in canvas pixels (SNAP_THRESHOLD_PX) converted to PDF pts
 * at current zoom so snap feels consistent regardless of zoom level.
 */
function computeMoveSnap(proposed, dragField) {
    const showGuides = document.getElementById('showAlignmentGuides')?.checked ?? true;
    if (!showGuides) return { sx: 0, sy: 0 };
    const pageFields = fields.filter(f => f.page === dragField.page && f !== dragField);
    const thresholdPts = SNAP_THRESHOLD_PX / (DPI_SCALE * Math.max(zoom, 0.01));

    const [pL, pT, pR, pB] = proposed;
    const pcx = (pL + pR) / 2, pcy = (pT + pB) / 2;
    let sx = 0, sy = 0;
    let bestXD = thresholdPts, bestYD = thresholdPts;

    for (const f of pageFields) {
        const [fL, fT, fR, fB] = f.rect;
        const fcx = (fL + fR) / 2, fcy = (fT + fB) / 2;
        const xCandidates = [
            [fL - pL, 'L'],  [fR - pR, 'R'],  [fcx - pcx, 'C'],
            [fL - pR, 'L'],  [fR - pL, 'R'],     // edge-to-edge (right→left, left→right)
        ];
        const yCandidates = [
            [fT - pT, 'T'],  [fB - pB, 'B'],  [fcy - pcy, 'C'],
            [fT - pB, 'T'],  [fB - pT, 'B'],
        ];
        xCandidates.forEach(([d]) => { if (Math.abs(d) < bestXD) { bestXD = Math.abs(d); sx = d; } });
        yCandidates.forEach(([d]) => { if (Math.abs(d) < bestYD) { bestYD = Math.abs(d); sy = d; } });
    }
    return { sx, sy };
}

/**
 * Snap the edge(s) being resized to nearby fields. Mutates rect r in place.
 */
function applyEdgeSnap(r, dragField, handleDir) {
    const showGuides = document.getElementById('showAlignmentGuides')?.checked ?? true;
    if (!showGuides) return;
    const pageFields = fields.filter(f => f.page === dragField.page && f !== dragField);
    const thresholdPts = SNAP_THRESHOLD_PX / (DPI_SCALE * Math.max(zoom, 0.01));

    const movesLeft   = ['tl','bl','ml'].includes(handleDir);
    const movesRight  = ['tr','br','mr'].includes(handleDir);
    const movesTop    = ['tl','tr','mt'].includes(handleDir);
    const movesBottom = ['bl','br','mb'].includes(handleDir);

    let bestXD = thresholdPts, bestXv = null, bestXedge = null;
    let bestYD = thresholdPts, bestYv = null, bestYedge = null;

    for (const f of pageFields) {
        const [fL, fT, fR, fB] = f.rect;
        if (movesLeft) {
            [fL, fR].forEach(v => { const d = Math.abs(r[0] - v); if (d < bestXD) { bestXD = d; bestXv = v; bestXedge = 'L'; } });
        } else if (movesRight) {
            [fL, fR].forEach(v => { const d = Math.abs(r[2] - v); if (d < bestXD) { bestXD = d; bestXv = v; bestXedge = 'R'; } });
        }
        if (movesTop) {
            [fT, fB].forEach(v => { const d = Math.abs(r[1] - v); if (d < bestYD) { bestYD = d; bestYv = v; bestYedge = 'T'; } });
        } else if (movesBottom) {
            [fT, fB].forEach(v => { const d = Math.abs(r[3] - v); if (d < bestYD) { bestYD = d; bestYv = v; bestYedge = 'B'; } });
        }
    }
    if (bestXv !== null) { if (bestXedge === 'L') r[0] = bestXv; else r[2] = bestXv; }
    if (bestYv !== null) { if (bestYedge === 'T') r[1] = bestYv; else r[3] = bestYv; }
}

/**
 * Draw red dashed alignment guides in scroll view as DOM lines inside a single
 * page wrap. Mirrors the canvas-based drawAlignmentGuides() in single view.
 */
function drawScrollViewGuides(draggedField, wrap, iw, ih) {
    const showGuides = document.getElementById('showAlignmentGuides')?.checked ?? true;
    if (!showGuides) { clearScrollViewGuides(); return; }
    if (!wrap || !iw || !ih) return;

    const SNAP_PX = 8;
    const wrapW   = wrap.clientWidth;
    const wrapH   = wrap.clientHeight || (wrapW * ih / iw);
    const sX      = wrapW / iw * DPI_SCALE;   // PDF-pt → screen-px on this wrap
    const sY      = wrapH / ih * DPI_SCALE;

    const dx       = draggedField.rect[0] * sX;
    const dy       = draggedField.rect[1] * sY;
    const dRight   = draggedField.rect[2] * sX;
    const dBottom  = draggedField.rect[3] * sY;
    const dCenterX = (dx + dRight) / 2;
    const dCenterY = (dy + dBottom) / 2;

    const lines = [];
    fields.forEach(f => {
        if (f === draggedField || f.page !== draggedField.page) return;
        const fx = f.rect[0] * sX, fy = f.rect[1] * sY;
        const fr = f.rect[2] * sX, fb = f.rect[3] * sY;
        const fcx = (fx + fr) / 2, fcy = (fy + fb) / 2;

        if (Math.abs(dx - fx)         < SNAP_PX) lines.push(['v', fx,  Math.min(dy, fy), Math.max(dBottom, fb)]);
        if (Math.abs(dRight - fr)     < SNAP_PX) lines.push(['v', fr,  Math.min(dy, fy), Math.max(dBottom, fb)]);
        if (Math.abs(dCenterX - fcx)  < SNAP_PX) lines.push(['v', fcx, Math.min(dy, fy), Math.max(dBottom, fb)]);
        if (Math.abs(dy - fy)         < SNAP_PX) lines.push(['h', fy,  Math.min(dx, fx), Math.max(dRight, fr)]);
        if (Math.abs(dBottom - fb)    < SNAP_PX) lines.push(['h', fb,  Math.min(dx, fx), Math.max(dRight, fr)]);
        if (Math.abs(dCenterY - fcy)  < SNAP_PX) lines.push(['h', fcy, Math.min(dx, fx), Math.max(dRight, fr)]);
    });

    let layer = wrap.querySelector('.sv-guides-layer');
    if (!layer) {
        layer = document.createElement('div');
        layer.className = 'sv-guides-layer';
        layer.style.cssText = 'position:absolute;inset:0;pointer-events:none;z-index:50;';
        wrap.appendChild(layer);
    }
    layer.innerHTML = '';
    lines.forEach(([type, pos, a, b]) => {
        const ln = document.createElement('div');
        if (type === 'v') {
            ln.style.cssText = `position:absolute;left:${pos}px;top:${a}px;width:0;` +
                `height:${b - a}px;border-left:1px dashed #ff6b6b;`;
        } else {
            ln.style.cssText = `position:absolute;top:${pos}px;left:${a}px;height:0;` +
                `width:${b - a}px;border-top:1px dashed #ff6b6b;`;
        }
        layer.appendChild(ln);
    });
}

function clearScrollViewGuides() {
    document.querySelectorAll('.sv-guides-layer').forEach(l => l.remove());
}

/** Refresh tab-number badges on all scroll-view overlays — mirrors the
 *  showTabNumbers rendering in single view (pageFields.forEach index+1). */
function updateScrollTabNumbers() {
    // Clear any existing badges
    document.querySelectorAll('.sv-field-overlay .sv-tab-number').forEach(el => el.remove());
    if (!showTabNumbers) return;
    const container = document.getElementById('scrollViewContainer');
    if (!container) return;

    // Per-page counter so numbering matches single view's pageFields order
    const pageCounter = new Map();
    fields.forEach(field => {
        const n = (pageCounter.get(field.page) || 0) + 1;
        pageCounter.set(field.page, n);
        const ov = container.querySelector(
            `.sv-field-overlay[data-field-uid="${CSS.escape(fieldUID(field))}"]`
        );
        if (!ov) return;
        const badge = document.createElement('div');
        badge.className   = 'sv-tab-number';
        badge.textContent = String(n);
        ov.appendChild(badge);
    });
}

// ============ Canvas Rendering ============
function renderCanvas() {
    // v1.1.0: in scroll view, fully sync DOM overlays with the fields[] model
    if (viewMode === 'scroll') {
        syncScrollViewDOM();
        applyScrollZoom();
        // In preview mode, hide all field overlays so only the interactive
        // preview inputs show, and refresh those inputs (zoom may have changed).
        if (isPreviewMode) {
            document.querySelectorAll('.sv-field-overlay').forEach(el => {
                el.style.pointerEvents = 'none';
                el.style.opacity = '0';
            });
            renderScrollPreviewInputs();
        } else {
            updateScrollFieldHighlights();
        }
        updateScrollTabNumbers();
        return;
    }
    if (!pageImage) return;
    
    const width  = pageImage.width  * zoom;
    const height = pageImage.height * zoom;
    // Match scroll view resolution on Retina: backing store = CSS px × devicePixelRatio.
    // All draw code continues to use CSS-pixel coords thanks to ctx.setTransform(dpr,...).
    const dpr = window.devicePixelRatio || 1;
    canvas.style.width  = width  + 'px';
    canvas.style.height = height + 'px';
    canvas.width  = Math.round(width  * dpr);
    canvas.height = Math.round(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    ctx.clearRect(0, 0, width, height);
    ctx.drawImage(pageImage, 0, 0, width, height);
    
    const pageFields = fields.filter(f => f.page === currentPage);
    
    // Only draw field overlays if highlighting is enabled
    if (highlightFieldsEnabled) {
        for (const field of pageFields) {
            drawField(field);
        }
    } else {
        // Still draw selected fields even if highlighting is off
        for (const field of selectedFields) {
            if (field.page === currentPage) {
                drawField(field);
            }
        }
    }
    
    // Draw tab numbers on fields if enabled
    if (showTabNumbers) {
        pageFields.forEach((field, index) => {
            if (!field.rect) return;
            
            // Use same coordinate calculation as drawField
            const x = field.rect[0] * DPI_SCALE * zoom;
            const y = field.rect[1] * DPI_SCALE * zoom;
            const w = (field.rect[2] - field.rect[0]) * DPI_SCALE * zoom;
            const h = (field.rect[3] - field.rect[1]) * DPI_SCALE * zoom;
            
            // Draw tab number badge in top-left corner of the field
            const num = index + 1;
            const text = String(num);
            ctx.font = 'bold 10px Arial';
            const textWidth = ctx.measureText(text).width;
            const badgeWidth = Math.max(textWidth + 6, 16);
            const badgeHeight = 14;
            
            // Position badge at top-left corner of the field
            ctx.fillStyle = '#64ffda';
            ctx.fillRect(x, y, badgeWidth, badgeHeight);
            
            ctx.fillStyle = '#0a192f';
            ctx.textBaseline = 'middle';
            ctx.fillText(text, x + 3, y + badgeHeight / 2);
        });
    }
    
    // Draw dependency lines for selected calculated fields
    if (selectedFields.length === 1 && !isDragging) {
        const selField = selectedFields[0];
        const calc = changes.calculations[selField.name] || selField.calculation;
        if (calc && calc.type && calc.sources && calc.sources.length > 0) {
            const tx = selField.rect[0] * DPI_SCALE * zoom;
            const ty = selField.rect[1] * DPI_SCALE * zoom;
            const tw = (selField.rect[2] - selField.rect[0]) * DPI_SCALE * zoom;
            const th = (selField.rect[3] - selField.rect[1]) * DPI_SCALE * zoom;
            const targetCX = tx + tw / 2;
            const targetCY = ty + th / 2;

            for (const srcName of calc.sources) {
                const srcField = pageFields.find(f => f.name === srcName);
                if (!srcField) continue;
                const sx = srcField.rect[0] * DPI_SCALE * zoom;
                const sy = srcField.rect[1] * DPI_SCALE * zoom;
                const sw = (srcField.rect[2] - srcField.rect[0]) * DPI_SCALE * zoom;
                const sh = (srcField.rect[3] - srcField.rect[1]) * DPI_SCALE * zoom;
                const srcCX = sx + sw / 2;
                const srcCY = sy + sh / 2;

                // Draw dashed line from source to target
                ctx.strokeStyle = 'rgba(76, 175, 80, 0.6)';
                ctx.lineWidth = 1.5;
                ctx.setLineDash([4, 4]);
                ctx.beginPath();
                ctx.moveTo(srcCX, srcCY);
                ctx.lineTo(targetCX, targetCY);
                ctx.stroke();
                ctx.setLineDash([]);

                // Draw small arrow at target end
                const angle = Math.atan2(targetCY - srcCY, targetCX - srcCX);
                const arrowLen = 8 * zoom;
                ctx.fillStyle = 'rgba(76, 175, 80, 0.8)';
                ctx.beginPath();
                ctx.moveTo(targetCX, targetCY);
                ctx.lineTo(targetCX - arrowLen * Math.cos(angle - 0.4), targetCY - arrowLen * Math.sin(angle - 0.4));
                ctx.lineTo(targetCX - arrowLen * Math.cos(angle + 0.4), targetCY - arrowLen * Math.sin(angle + 0.4));
                ctx.closePath();
                ctx.fill();

                // Highlight source field with green border
                ctx.strokeStyle = 'rgba(76, 175, 80, 0.7)';
                ctx.lineWidth = 2;
                ctx.strokeRect(sx, sy, sw, sh);
            }
        }
    }

    // Draw alignment guides when dragging (if toggle is on)
    const showGuides = document.getElementById('showAlignmentGuides')?.checked ?? true;
    if (isDragging && selectedFields.length === 1 && showGuides) {
        drawAlignmentGuides(selectedFields[0], pageFields);
    }
    
    // Draw marquee selection rectangle
    if (isMarqueeSelecting) {
        const mx = Math.min(marqueeStart.x, marqueeEnd.x);
        const my = Math.min(marqueeStart.y, marqueeEnd.y);
        const mw = Math.abs(marqueeEnd.x - marqueeStart.x);
        const mh = Math.abs(marqueeEnd.y - marqueeStart.y);
        
        ctx.strokeStyle = '#64ffda';
        ctx.lineWidth = 2;
        ctx.setLineDash([5, 5]);
        ctx.strokeRect(mx, my, mw, mh);
        ctx.setLineDash([]);
        
        ctx.fillStyle = 'rgba(100, 255, 218, 0.1)';
        ctx.fillRect(mx, my, mw, mh);
    }
    
    // Draw field creation preview
    if (isCreatingField) {
        const cx = Math.min(creationStart.x, creationEnd.x);
        const cy = Math.min(creationStart.y, creationEnd.y);
        let cw = Math.abs(creationEnd.x - creationStart.x);
        let ch = Math.abs(creationEnd.y - creationStart.y);
        
        // For checkbox/radio, use fixed size
        if (currentTool === 'checkbox' || currentTool === 'radio') {
            cw = 12 * zoom;
            ch = 12 * zoom;
        }
        
        // Use different color for creation preview
        ctx.strokeStyle = '#ff6b6b';
        ctx.lineWidth = 2;
        ctx.setLineDash([5, 5]);
        ctx.strokeRect(cx, cy, cw, ch);
        ctx.setLineDash([]);
        
        ctx.fillStyle = 'rgba(255, 107, 107, 0.2)';
        ctx.fillRect(cx, cy, cw, ch);
        
        // Show tool type icon
        ctx.fillStyle = '#ff6b6b';
        ctx.font = `${14 * zoom}px Arial`;
        const toolIcons = { text: '📝', checkbox: '☑', radio: '🔘', dropdown: '📋', signature: '✍', date: '📅' };
        ctx.fillText(toolIcons[currentTool] || '📄', cx + 2, cy + 14 * zoom);
    }
}

// Draw alignment guides
function drawAlignmentGuides(draggedField, allFields) {
    const SNAP_THRESHOLD = 8; // pixels
    const guides = [];
    
    const dx = draggedField.rect[0] * DPI_SCALE * zoom;
    const dy = draggedField.rect[1] * DPI_SCALE * zoom;
    const dw = (draggedField.rect[2] - draggedField.rect[0]) * DPI_SCALE * zoom;
    const dh = (draggedField.rect[3] - draggedField.rect[1]) * DPI_SCALE * zoom;
    const dCenterX = dx + dw / 2;
    const dCenterY = dy + dh / 2;
    const dRight = dx + dw;
    const dBottom = dy + dh;
    
    for (const field of allFields) {
        if (field.name === draggedField.name) continue;
        
        const fx = field.rect[0] * DPI_SCALE * zoom;
        const fy = field.rect[1] * DPI_SCALE * zoom;
        const fw = (field.rect[2] - field.rect[0]) * DPI_SCALE * zoom;
        const fh = (field.rect[3] - field.rect[1]) * DPI_SCALE * zoom;
        const fCenterX = fx + fw / 2;
        const fCenterY = fy + fh / 2;
        const fRight = fx + fw;
        const fBottom = fy + fh;
        
        // Left alignment
        if (Math.abs(dx - fx) < SNAP_THRESHOLD) {
            guides.push({ type: 'vertical', x: fx, y1: Math.min(dy, fy), y2: Math.max(dBottom, fBottom) });
        }
        // Right alignment
        if (Math.abs(dRight - fRight) < SNAP_THRESHOLD) {
            guides.push({ type: 'vertical', x: fRight, y1: Math.min(dy, fy), y2: Math.max(dBottom, fBottom) });
        }
        // Center X alignment
        if (Math.abs(dCenterX - fCenterX) < SNAP_THRESHOLD) {
            guides.push({ type: 'vertical', x: fCenterX, y1: Math.min(dy, fy), y2: Math.max(dBottom, fBottom) });
        }
        // Top alignment
        if (Math.abs(dy - fy) < SNAP_THRESHOLD) {
            guides.push({ type: 'horizontal', y: fy, x1: Math.min(dx, fx), x2: Math.max(dRight, fRight) });
        }
        // Bottom alignment
        if (Math.abs(dBottom - fBottom) < SNAP_THRESHOLD) {
            guides.push({ type: 'horizontal', y: fBottom, x1: Math.min(dx, fx), x2: Math.max(dRight, fRight) });
        }
        // Center Y alignment
        if (Math.abs(dCenterY - fCenterY) < SNAP_THRESHOLD) {
            guides.push({ type: 'horizontal', y: fCenterY, x1: Math.min(dx, fx), x2: Math.max(dRight, fRight) });
        }
    }
    
    // Draw guides
    ctx.strokeStyle = '#ff6b6b';
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    
    for (const guide of guides) {
        ctx.beginPath();
        if (guide.type === 'vertical') {
            ctx.moveTo(guide.x, guide.y1 - 10);
            ctx.lineTo(guide.x, guide.y2 + 10);
        } else {
            ctx.moveTo(guide.x1 - 10, guide.y);
            ctx.lineTo(guide.x2 + 10, guide.y);
        }
        ctx.stroke();
    }
    
    ctx.setLineDash([]);
}

function drawField(field) {
    const isSelected = selectedFields.includes(field);
    
    const x = field.rect[0] * DPI_SCALE * zoom;
    const y = field.rect[1] * DPI_SCALE * zoom;
    const w = (field.rect[2] - field.rect[0]) * DPI_SCALE * zoom;
    const h = (field.rect[3] - field.rect[1]) * DPI_SCALE * zoom;
    
    const fieldHasCalc = !!(changes.calculations[field.name] || field.calculation)?.type;
    // Check if this field is a calc source in pick mode
    const isCalcSource = isPickModeActive() && calcSources.find(s => s.name === field.name);
    const isCalcTarget = isPickModeActive() && modalField && field.name === modalField.name;
    let fillColor, strokeColor;
    if (isCalcTarget) {
        fillColor = 'rgba(233, 69, 96, 0.4)';
        strokeColor = '#e94560';
    } else if (isCalcSource) {
        fillColor = 'rgba(76, 175, 80, 0.4)';
        strokeColor = '#4CAF50';
    } else if (isSelected) {
        fillColor = 'rgba(100, 255, 218, 0.4)';
        strokeColor = '#64ffda';
    } else if (field.type === 'link') {
        fillColor = 'rgba(0, 0, 0, 0.03)';
        strokeColor = '#333333';
    } else if (field.type === 'checkbox') {
        fillColor = 'rgba(255, 193, 7, 0.2)';
        strokeColor = '#ffc107';
    } else if (field.type === 'radio') {
        fillColor = 'rgba(233, 69, 96, 0.2)';
        strokeColor = '#e94560';
    } else if (fieldHasCalc) {
        fillColor = 'rgba(76, 175, 80, 0.18)';
        strokeColor = 'rgba(76, 175, 80, 0.7)';
    } else {
        fillColor = 'rgba(100, 255, 218, 0.15)';
        strokeColor = 'rgba(100, 255, 218, 0.6)';
    }
    
    ctx.fillStyle = fillColor;
    ctx.fillRect(x, y, w, h);
    
    ctx.strokeStyle = strokeColor;
    ctx.lineWidth = isSelected ? 2 : 1;
    ctx.strokeRect(x, y, w, h);
    
    if (isSelected) {
        drawResizeHandles(x, y, w, h);
    }
    
    ctx.fillStyle = strokeColor;
    ctx.font = `${10 * zoom}px Arial`;
    
    // Determine type icon
    let typeIcon = '📝';
    if (field.type === 'link') {
        typeIcon = '🔗';
    } else if (field.type === 'checkbox' || field.type === 'radio') {
        const style = field.style?.checkboxStyle || field.checkboxStyle || 'check';
        const iconMap = {
            'check': '✓',
            'circle': '●',
            'cross': '✖',
            'square': '■',
            'diamond': '◆',
            'star': '★'
        };
        typeIcon = field.type === 'radio' ? '🔘' : (iconMap[style] || '☐');
    }
    
    ctx.fillText(typeIcon, x + 2, y + 12 * zoom);

    // Draw calculation badge (Σ) for fields with calculations
    const hasCalc = changes.calculations[field.name] || field.calculation;
    if (hasCalc && hasCalc.type) {
        const badgeSize = 14 * zoom;
        const bx = x + w - badgeSize - 1;
        const by = y + 1;
        // Green circle background
        ctx.fillStyle = 'rgba(76, 175, 80, 0.9)';
        ctx.beginPath();
        ctx.arc(bx + badgeSize / 2, by + badgeSize / 2, badgeSize / 2, 0, Math.PI * 2);
        ctx.fill();
        // Σ text
        ctx.fillStyle = '#ffffff';
        ctx.font = `bold ${10 * zoom}px Arial`;
        ctx.textBaseline = 'middle';
        ctx.textAlign = 'center';
        ctx.fillText('Σ', bx + badgeSize / 2, by + badgeSize / 2 + 0.5);
        ctx.textAlign = 'start';
        ctx.textBaseline = 'alphabetic';
    }
}

function drawResizeHandles(x, y, w, h) {
    const hs = HANDLE_SIZE;
    // Match scroll view style: teal fill + 1px dark navy outline
    const positions = [
        [x - hs/2, y - hs/2],                 // TL
        [x + w - hs/2, y - hs/2],             // TR
        [x - hs/2, y + h - hs/2],             // BL
        [x + w - hs/2, y + h - hs/2],         // BR
        [x - hs/2, y + h/2 - hs/2],           // ML
        [x + w - hs/2, y + h/2 - hs/2],       // MR
        [x + w/2 - hs/2, y - hs/2],           // MT
        [x + w/2 - hs/2, y + h - hs/2],       // MB
    ];
    ctx.fillStyle = '#64ffda';
    positions.forEach(([hx, hy]) => ctx.fillRect(hx, hy, hs, hs));
    ctx.strokeStyle = '#0a192f';
    ctx.lineWidth = 1;
    positions.forEach(([hx, hy]) => ctx.strokeRect(hx + 0.5, hy + 0.5, hs - 1, hs - 1));
}

// ============ Event Handlers ============
function setupEventListeners() {
    // Set up canvas interaction
    canvas.addEventListener('mousedown', handleMouseDown);
    canvas.addEventListener('mousemove', handleMouseMove);
    canvas.addEventListener('mouseup', handleMouseUp);
    canvas.addEventListener('mouseleave', handleMouseUp); // Treat leave as up
    
    // Double-click to open properties
    canvas.addEventListener('dblclick', handleCanvasDblClick);

    // Context menu (right-click)
    canvas.addEventListener('contextmenu', handleContextMenu);
    
    // Page navigation
    document.getElementById('btnPrevPage').addEventListener('click', () => loadPage(currentPage - 1));
    document.getElementById('btnNextPage').addEventListener('click', () => loadPage(currentPage + 1));
    
    // Page input
    const pageInput = document.getElementById('pageInput');
    if (pageInput) {
        pageInput.addEventListener('change', () => {
            const val = parseInt(pageInput.value);
            if (val >= 1 && val <= pages.length) {
                loadPage(val - 1);
            } else {
                pageInput.value = currentPage + 1;
            }
        });
        pageInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') pageInput.blur();
        });
    }
    
    // Zoom
    document.getElementById('btnZoomIn').addEventListener('click', () => setZoom(zoom + 0.1));
    document.getElementById('btnZoomOut').addEventListener('click', () => setZoom(zoom - 0.1));

    // Fit Page
    const btnFitPage = document.getElementById('btnFitPage');
    if (btnFitPage) {
        btnFitPage.addEventListener('click', () => {
            if (viewMode === 'scroll') {
                fitScrollToPage();
            } else {
                fitToPage();
                renderCanvas();
            }
        });
    }

    // v1.1.0: View mode toggle (Single / Scroll)
    document.getElementById('btnViewSingle')?.addEventListener('click', () => setViewMode('single'));
    document.getElementById('btnViewScroll')?.addEventListener('click', () => setViewMode('scroll'));

    // v1.1.0: Apply radio group rename from side panel
    document.getElementById('btnApplyRadioGroup')?.addEventListener('click', () => {
        if (selectedFields.length !== 1 || selectedFields[0].type !== 'radio') return;
        const field = selectedFields[0];
        const input = document.getElementById('propRadioGroupName');
        if (!input) return;
        const newGroup = input.value.trim();
        const oldGroup = field.radio_group;
        if (!newGroup || newGroup === oldGroup) return;
        fields.forEach(f => {
            if (f.type === 'radio' && f.radio_group === oldGroup) f.radio_group = newGroup;
        });
        if (oldGroup && changes.new_radio_groups[oldGroup]) {
            changes.new_radio_groups[newGroup] = changes.new_radio_groups[oldGroup];
            delete changes.new_radio_groups[oldGroup];
        }
        if (collapsedGroups.has(oldGroup)) {
            collapsedGroups.delete(oldGroup);
            collapsedGroups.add(newGroup);
        }
        markUnsaved();
        updateFieldList();
        updatePropertiesPanel();
    });
    
    // Property editing
    document.getElementById('propName').addEventListener('change', handleNameChange);
    document.getElementById('propTooltip').addEventListener('change', handleTooltipChange);
    
    // Apply buttons for Name and Tooltip
    const btnApplyName = document.getElementById('btnApplyName');
    if (btnApplyName) {
        btnApplyName.addEventListener('click', (e) => {
            e.preventDefault();
            handleNameChange();
        });
    }
    
    const btnApplyTooltip = document.getElementById('btnApplyTooltip');
    if (btnApplyTooltip) {
        btnApplyTooltip.addEventListener('click', (e) => {
            e.preventDefault();
            handleTooltipChange();
        });
    }
    
    // Style change listeners
    setupStyleListeners();
    
    // Actions
    document.getElementById('btnDeleteField').addEventListener('click', handleDelete);
    document.getElementById('btnGroupRadio').addEventListener('click', handleGroupRadio);
    document.getElementById('btnUngroupRadio').addEventListener('click', handleUngroupRadio);
    
    // Properties button
    const btnProperties = document.getElementById('btnProperties');
    if (btnProperties) {
        btnProperties.addEventListener('click', () => {
            try {
                if (selectedFields.length === 1) {
                    openPropertiesModal(selectedFields[0]);
                } else if (selectedFields.length > 1) {
                    openMultiFieldPropertiesModal(selectedFields);
                }
            } catch (e) {
                showAlert('Properties Error: ' + e.message, '⚠️ Error');
                console.error(e);
            }
        });
    }
    
    // Duplicate button
    const btnDuplicate = document.getElementById('btnDuplicate');
    if (btnDuplicate) {
        btnDuplicate.addEventListener('click', duplicateSelectedFields);
    }
    
    // Modal tab switching
    document.querySelectorAll('.modal-tab').forEach(tab => {
        tab.addEventListener('click', () => switchPropertiesTab(tab.dataset.tab));
    });
    
    // Format category change shows/hides format options
    const formatCategory = document.getElementById('modalFormatCategory');
    if (formatCategory) {
        formatCategory.addEventListener('change', () => { formatDirty = true; updateFormatOptions(); });
    }
    // Mark format dirty when any format sub-option changes
    ['modalDateFormat','modalCurrencySymbol','modalCurrencyDecimals','modalSymbolLocation',
     'modalPhoneFormat','modalNumberDecimals','modalNumberSeparator','modalZipFormat'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('change', () => { formatDirty = true; });
    });
    // Mark font dirty when modal font controls change
    ['modalPropFont','modalPropFontSize','modalPropTextColor'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('change', () => { fontDirty = true; });
        if (el) el.addEventListener('input', () => { fontDirty = true; });
    });
    
    // Position units conversion
    const modalUnits = document.getElementById('modalPropUnits');
    if (modalUnits) {
        modalUnits.addEventListener('change', updatePositionUnits);
    }
    
    // Batch editing
    const btnApplyBatch = document.getElementById('btnApplyBatch');
    if (btnApplyBatch) {
        btnApplyBatch.addEventListener('click', handleBatchApply);
    }
    
    // Batch format dropdown - toggle sub-options
    const batchFormatDropdown = document.getElementById('batchFormat');
    if (batchFormatDropdown) {
        batchFormatDropdown.addEventListener('change', () => {
            showBatchSubFormatOptions(batchFormatDropdown.value);
        });
    }
    
    // Arrange buttons
    document.getElementById('btnAlignLeft')?.addEventListener('click', () => alignFields('left'));
    document.getElementById('btnAlignCenterH')?.addEventListener('click', () => alignFields('centerH'));
    document.getElementById('btnAlignRight')?.addEventListener('click', () => alignFields('right'));
    document.getElementById('btnAlignTop')?.addEventListener('click', () => alignFields('top'));
    document.getElementById('btnAlignCenterV')?.addEventListener('click', () => alignFields('centerV'));
    document.getElementById('btnAlignBottom')?.addEventListener('click', () => alignFields('bottom'));
    
    document.getElementById('btnMatchWidth')?.addEventListener('click', () => matchSize('width'));
    document.getElementById('btnMatchHeight')?.addEventListener('click', () => matchSize('height'));
    document.getElementById('btnMatchBoth')?.addEventListener('click', () => matchSize('both'));
    
    document.getElementById('btnDistributeH')?.addEventListener('click', () => distributeFields('horizontal'));
    document.getElementById('btnDistributeV')?.addEventListener('click', () => distributeFields('vertical'));
    
    // Save/Cancel
    document.getElementById('btnSave').addEventListener('click', handleSave);
    document.getElementById('btnExit').addEventListener('click', handleExit);
    
    // Keyboard shortcuts
    document.addEventListener('keydown', handleKeyDown);
    
    // Tool buttons
    setupToolButtons();
    
    // Field list sidebar
    setupFieldListListeners();
}

// ============ Tool Selection ============
function setupToolButtons() {
    const toolButtons = document.querySelectorAll('.tool-btn[data-tool]');
    toolButtons.forEach(btn => {
        btn.addEventListener('click', () => {
            setTool(btn.dataset.tool);
        });
    });
}

function setTool(toolName) {
    currentTool = toolName;
    
    // Update button active states
    document.querySelectorAll('.tool-btn[data-tool]').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.tool === toolName);
    });
    
    // Update cursor based on tool
    if (toolName === 'select') {
        canvas.style.cursor = 'crosshair';
    } else {
        canvas.style.cursor = 'crosshair';  // Could use tool-specific cursors later
    }
    
    // Clear selection when switching to creation tools
    if (toolName !== 'select') {
        selectedFields = [];
        updatePropertiesPanel();
        renderCanvas();
    }
}

// ============ Field Creation ============
function generateUniqueName(baseName) {
    // Find all existing names that match this pattern
    const existingNames = new Set(fields.map(f => f.name));
    let counter = 1;
    let candidateName = `${baseName}_${counter}`;
    while (existingNames.has(candidateName)) {
        counter++;
        candidateName = `${baseName}_${counter}`;
    }
    return candidateName;
}

function createNewField(toolType, x1, y1, x2, y2) {
    // Convert canvas coordinates to PDF coordinates
    const pdfX1 = x1 / (DPI_SCALE * zoom);
    const pdfY1 = y1 / (DPI_SCALE * zoom);
    let pdfX2 = x2 / (DPI_SCALE * zoom);
    let pdfY2 = y2 / (DPI_SCALE * zoom);
    
    // Enforce minimum sizes
    const minW = (toolType === 'checkbox' || toolType === 'radio') ? 12 : 50;
    const minH = (toolType === 'checkbox' || toolType === 'radio') ? 12 : 14;
    if (pdfX2 - pdfX1 < minW) pdfX2 = pdfX1 + minW;
    if (pdfY2 - pdfY1 < minH) pdfY2 = pdfY1 + minH;
    
    // Generate unique name based on type
    const typeLabels = { text: 'TextField', checkbox: 'Checkbox', radio: 'RadioButton', 
                         dropdown: 'Dropdown', signature: 'Signature', date: 'DateField' };
    const baseName = typeLabels[toolType] || 'Field';
    const fieldName = generateUniqueName(baseName);
    
    // Map tool type to field type
    const fieldTypes = { text: 'text', checkbox: 'checkbox', radio: 'checkbox',  // radio starts as checkbox, group later
                         dropdown: 'combobox', signature: 'signature', date: 'text' };
    
    // Create the new field object
    const newField = {
        name: fieldName,
        type: fieldTypes[toolType] || 'text',
        page: currentPage,
        rect: [pdfX1, pdfY1, pdfX2, pdfY2],
        is_new: true,  // Flag for backend to know this is a new field
        created_tool: toolType  // Store original tool type for special handling
    };
    
    // Add default style properties
    if (toolType === 'text' || toolType === 'signature' || toolType === 'date') {
        newField.style = {
            fontFamily: 'Helvetica',
            fontSize: 9,
            fontColor: '#000000',
            fillColor: '#EDF4FF',
            textAlign: 'left'
        };
    }
    
    // Date fields get special formatting
    if (toolType === 'date') {
        newField.style = newField.style || {};
        newField.style.dateFormat = 'MM/DD/YYYY';
        newField.is_date = true;
    }
    
    // Signature fields get taller default
    if (toolType === 'signature') {
        if (pdfY2 - pdfY1 < 25) newField.rect[3] = pdfY1 + 25;
        newField.is_signature = true;
    }
    
    // Dropdown fields start with empty options
    if (toolType === 'dropdown') {
        newField.options = [];
    }
    
    // Checkbox and radio fields get style from global settings
    if (toolType === 'checkbox' || toolType === 'radio') {
        newField.style = {
            checkboxStyle: window.globalFieldSettings?.checkboxStyle || 'check',
            exportValue: fieldName.replace(/\s+/g, '_')
        };
    }
    
    // Save state for undo
    saveState();
    
    // Add to fields array
    fields.push(newField);
    
    // Track as a new field for saving
    if (!changes.created) changes.created = [];
    changes.created.push(newField);
    
    // Select the new field
    selectedFields = [newField];
    
    // Switch back to select tool
    setTool('select');
    
    markUnsaved();
    updateFieldCount();
    updatePropertiesPanel();
    renderCanvas();
}

function handleCanvasDblClick(e) {
    if (currentTool !== 'select') return;
    // Don't open properties modal during pick mode — double-clicks
    // should just pick the field (handled by mousedown), not switch the modal target
    if (isPickModeActive()) return;

    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;

    const field = getFieldAtPoint(x, y);
    if (field) {
        selectedFields = [field];
        openPropertiesModal(field);
        updateFieldList();
        renderCanvas();
    }
}

function handleMouseDown(e) {
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    
    // If using a creation tool, start creating a new field
    if (currentTool !== 'select') {
        isCreatingField = true;
        creationStart = { x, y };
        creationEnd = { x, y };
        return;
    }
    
    // Check if clicking on a resize handle of selected field
    if (selectedFields.length === 1) {
        const handle = getResizeHandleAtPoint(x, y, selectedFields[0]);
        if (handle) {
            saveState();  // Save state before resize
            isResizing = true;
            resizeHandle = handle;
            dragStart = { x, y };
            dragOrigin = { x, y };
            dragField = selectedFields[0];
            resizeOrigRect = [...dragField.rect];
            return;
        }
    }
    
    // Check if clicking on a field
    const clickedField = getFieldAtPoint(x, y);

    // Pick mode — clicking a field triggers the pick callback, not selecting it
    // Dragging on empty space starts a marquee for batch-picking fields
    if (isPickModeActive()) {
        if (clickedField) {
            // Don't pick the target field itself
            const targetName = modalField ? modalField.name : '';
            if (clickedField.name !== targetName) {
                pickMode.onPick(clickedField);
            }
            renderCanvas();
            return;
        }
        // No field clicked — start marquee for batch selection
        selectedFields = [];
        isMarqueeSelecting = true;
        marqueeStart = { x, y };
        marqueeEnd = { x, y };
        return;
    }

    if (clickedField) {
        if (e.shiftKey) {
            const index = selectedFields.indexOf(clickedField);
            if (index >= 0) {
                selectedFields.splice(index, 1);
            } else {
                selectedFields.push(clickedField);
            }
        } else {
            if (!selectedFields.includes(clickedField)) {
                selectedFields = [clickedField];
            }
            saveState();  // Save state before drag
            isDragging = true;
            dragStart = { x, y };
            dragOrigin = { x, y };
            dragField = clickedField;
            // Snapshot origin rects for all selected fields
            dragOrigRects = new Map();
            selectedFields.forEach(f => dragOrigRects.set(f.name, [...f.rect]));
        }
    } else {
        // Clicked on empty space - start marquee selection
        if (!e.shiftKey) {
            selectedFields = [];
        }
        isMarqueeSelecting = true;
        marqueeStart = { x, y };
        marqueeEnd = { x, y };
    }
    
    updatePropertiesPanel();
    updateFieldList();  // Sync field list sidebar with selection
    renderCanvas();
}

function handleMouseMove(e) {
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    
    // Field creation - draw preview rectangle
    if (isCreatingField) {
        creationEnd = { x, y };
        requestRender();
        return;
    }

    // Marquee selection
    if (isMarqueeSelecting) {
        marqueeEnd = { x, y };
        requestRender();
        return;
    }
    
    // Resizing
    if (isResizing && dragField && resizeHandle && resizeOrigRect) {
        // Use total delta from drag origin (not incremental) so shift-constrain works cleanly
        let dx = (x - dragOrigin.x) / (DPI_SCALE * zoom);
        let dy = (y - dragOrigin.y) / (DPI_SCALE * zoom);
        const orig = resizeOrigRect;
        const origW = orig[2] - orig[0];
        const origH = orig[3] - orig[1];

        // Shift: constrain aspect ratio on corner handles (signed so shrinking
        // past the original size stays proportional instead of flipping to a
        // non-square when the derived axis keeps growing).
        if (e.shiftKey && ['tl','tr','bl','br'].includes(resizeHandle) && origW > 0 && origH > 0) {
            const sx = (resizeHandle === 'tl' || resizeHandle === 'bl') ? -1 : 1;
            const sy = (resizeHandle === 'tl' || resizeHandle === 'tr') ? -1 : 1;
            const rx = (dx * sx) / origW;
            const ry = (dy * sy) / origH;
            const r  = Math.abs(rx) > Math.abs(ry) ? rx : ry;
            dx = r * origW * sx;
            dy = r * origH * sy;
        }

        // Build rect from original + delta
        const r = [...orig];
        if      (resizeHandle === 'tl') { r[0] = orig[0]+dx; r[1] = orig[1]+dy; }
        else if (resizeHandle === 'tr') { r[2] = orig[2]+dx; r[1] = orig[1]+dy; }
        else if (resizeHandle === 'bl') { r[0] = orig[0]+dx; r[3] = orig[3]+dy; }
        else if (resizeHandle === 'br') { r[2] = orig[2]+dx; r[3] = orig[3]+dy; }
        else if (resizeHandle === 'ml') { r[0] = orig[0]+dx; }
        else if (resizeHandle === 'mr') { r[2] = orig[2]+dx; }
        else if (resizeHandle === 'mt') { r[1] = orig[1]+dy; }
        else if (resizeHandle === 'mb') { r[3] = orig[3]+dy; }

        // Snap the moving edge(s) to nearby fields (unless shift: keep precise size)
        if (!e.shiftKey) {
            applyEdgeSnap(r, dragField, resizeHandle);
        }

        // Ensure min size (respects which edge is anchored per handle direction,
        // and uses an equal, small minimum so tiny square fields like checkboxes
        // don't get forced into rectangles by the first pixel of resize).
        clampMinRectSize(r, resizeHandle);

        dragField.rect[0] = r[0]; dragField.rect[1] = r[1];
        dragField.rect[2] = r[2]; dragField.rect[3] = r[3];
        recordFieldResize(dragField, dragField.rect);
        markUnsaved();
        requestRender();
        updatePropertiesPanel();
        return;
    }

    // Dragging
    if (isDragging && dragField) {
        // Total delta from the drag origin (lets shift-constrain work cleanly)
        let dx = (x - dragOrigin.x) / (DPI_SCALE * zoom);
        let dy = (y - dragOrigin.y) / (DPI_SCALE * zoom);

        // Shift held → lock to dominant axis
        if (e.shiftKey) {
            if (Math.abs(dx) > Math.abs(dy)) dy = 0; else dx = 0;
        }

        // Snap the primary field to nearby fields; apply same offset to rest of group
        const primaryOrig = dragOrigRects.get(dragField.name) || dragField.rect;
        const proposed = [
            primaryOrig[0] + dx, primaryOrig[1] + dy,
            primaryOrig[2] + dx, primaryOrig[3] + dy
        ];
        if (!e.shiftKey) {
            const snap = computeMoveSnap(proposed, dragField);
            dx += snap.sx;
            dy += snap.sy;
        }

        for (const field of selectedFields) {
            const o = dragOrigRects.get(field.name);
            if (!o) continue;
            field.rect[0] = o[0] + dx;
            field.rect[1] = o[1] + dy;
            field.rect[2] = o[2] + dx;
            field.rect[3] = o[3] + dy;
            recordFieldMove(field, field.rect);
        }

        markUnsaved();
        requestRender();
        updatePropertiesPanel();
        return;
    }
    
    // Cursor & tooltip
    const hoverField = getFieldAtPoint(x, y);
    if (selectedFields.length === 1) {
        const handle = getResizeHandleAtPoint(x, y, selectedFields[0]);
        if (handle) {
            // Set cursor based on handle type
            if (handle === 'ml' || handle === 'mr') {
                canvas.style.cursor = 'ew-resize';  // Horizontal resize
            } else if (handle === 'mt' || handle === 'mb') {
                canvas.style.cursor = 'ns-resize';  // Vertical resize
            } else if (handle === 'tl' || handle === 'br') {
                canvas.style.cursor = 'nwse-resize';  // Diagonal NW-SE
            } else {
                canvas.style.cursor = 'nesw-resize';  // Diagonal NE-SW
            }
            hideTooltip();
            return;
        }
    }
    
    if (hoverField) {
        // v51.1c: Respect display_tooltips setting for canvas hover tooltips
        const showTooltips = window.globalFieldSettings ? window.globalFieldSettings.displayTooltips !== false : true;
        if (showTooltips) {
            // Include calculation info in tooltip if field has a calculation
            const hoverCalc = changes.calculations[hoverField.name] || hoverField.calculation;
            let tooltipText = hoverField.name;
            if (hoverCalc && hoverCalc.type) {
                const srcList = (hoverCalc.sources || []).length <= 3
                    ? (hoverCalc.sources || []).join(', ')
                    : `${hoverCalc.sources.length} fields`;
                tooltipText += `\n🧮 ${hoverCalc.type}(${srcList})`;
            }
            showTooltip(e.clientX, e.clientY, tooltipText);
        } else {
            hideTooltip();
        }
        canvas.style.cursor = 'move';
    } else {
        hideTooltip();
        canvas.style.cursor = 'crosshair';
    }
}

function handleMouseUp() {
    // Finalize field creation
    if (isCreatingField) {
        const x1 = Math.min(creationStart.x, creationEnd.x);
        const y1 = Math.min(creationStart.y, creationEnd.y);
        const x2 = Math.max(creationStart.x, creationEnd.x);
        const y2 = Math.max(creationStart.y, creationEnd.y);
        
        // Only create if dragged more than 10 pixels (or for checkbox/radio, any click)
        const minDrag = (currentTool === 'checkbox' || currentTool === 'radio') ? 0 : 10;
        if (x2 - x1 > minDrag || y2 - y1 > minDrag || currentTool === 'checkbox' || currentTool === 'radio') {
            createNewField(currentTool, x1, y1, x2, y2);
        }
        
        isCreatingField = false;
        renderCanvas();
        return;
    }
    
    // Finalize marquee selection
    if (isMarqueeSelecting) {
        // Get marquee bounds in canvas coordinates
        const mx1 = Math.min(marqueeStart.x, marqueeEnd.x);
        const my1 = Math.min(marqueeStart.y, marqueeEnd.y);
        const mx2 = Math.max(marqueeStart.x, marqueeEnd.x);
        const my2 = Math.max(marqueeStart.y, marqueeEnd.y);
        
        // Only select if dragged more than 5 pixels
        if (mx2 - mx1 > 5 || my2 - my1 > 5) {
            // Find all fields within the marquee
            const pageFields = fields.filter(f => f.page === currentPage);
            const newlySelected = [];
            
            for (const field of pageFields) {
                // Convert field rect to canvas coordinates
                const fx1 = field.rect[0] * DPI_SCALE * zoom;
                const fy1 = field.rect[1] * DPI_SCALE * zoom;
                const fx2 = field.rect[2] * DPI_SCALE * zoom;
                const fy2 = field.rect[3] * DPI_SCALE * zoom;
                
                // Check if field overlaps with marquee
                if (fx1 < mx2 && fx2 > mx1 && fy1 < my2 && fy2 > my1) {
                    newlySelected.push(field);
                }
            }
            
            // If in pick mode, pass all lassoed fields to the pick callback
            // but do NOT change selectedFields (we're picking sources, not selecting).
            if (isPickModeActive()) {
                // Don't set selectedFields in pick mode
            } else {
                selectedFields = newlySelected;
            }

            if (isPickModeActive()) {
                const targetName = modalField ? modalField.name : '';
                for (const f of newlySelected) {
                    if (f.name !== targetName) {
                        pickMode.onPick(f);
                    }
                }
                isMarqueeSelecting = false;
                renderCanvas();
                return;
            }

            updatePropertiesPanel();
            updateFieldList();  // Sync field list with marquee selection
        }
        
        isMarqueeSelecting = false;
        renderCanvas();
    }
    
    isDragging = false;
    isResizing = false;
    dragField = null;
    resizeHandle = null;
    resizeOrigRect = null;
    dragOrigRects = new Map();
}

function getFieldAtPoint(canvasX, canvasY) {
    const pageFields = fields.filter(f => f.page === currentPage);
    
    for (const field of pageFields.slice().reverse()) {
        const x = field.rect[0] * DPI_SCALE * zoom;
        const y = field.rect[1] * DPI_SCALE * zoom;
        const w = (field.rect[2] - field.rect[0]) * DPI_SCALE * zoom;
        const h = (field.rect[3] - field.rect[1]) * DPI_SCALE * zoom;
        
        if (canvasX >= x && canvasX <= x + w && canvasY >= y && canvasY <= y + h) {
            return field;
        }
    }
    return null;
}

function getResizeHandleAtPoint(canvasX, canvasY, field) {
    const x = field.rect[0] * DPI_SCALE * zoom;
    const y = field.rect[1] * DPI_SCALE * zoom;
    const w = (field.rect[2] - field.rect[0]) * DPI_SCALE * zoom;
    const h = (field.rect[3] - field.rect[1]) * DPI_SCALE * zoom;
    const hs = HANDLE_SIZE;
    
    // Check each corner
    if (canvasX >= x - hs && canvasX <= x + hs && canvasY >= y - hs && canvasY <= y + hs) return 'tl';
    if (canvasX >= x + w - hs && canvasX <= x + w + hs && canvasY >= y - hs && canvasY <= y + hs) return 'tr';
    if (canvasX >= x - hs && canvasX <= x + hs && canvasY >= y + h - hs && canvasY <= y + h + hs) return 'bl';
    if (canvasX >= x + w - hs && canvasX <= x + w + hs && canvasY >= y + h - hs && canvasY <= y + h + hs) return 'br';
    
    // Check middle handles (left, right, top, bottom)
    if (canvasX >= x - hs && canvasX <= x + hs && canvasY >= y + h/2 - hs && canvasY <= y + h/2 + hs) return 'ml';
    if (canvasX >= x + w - hs && canvasX <= x + w + hs && canvasY >= y + h/2 - hs && canvasY <= y + h/2 + hs) return 'mr';
    if (canvasX >= x + w/2 - hs && canvasX <= x + w/2 + hs && canvasY >= y - hs && canvasY <= y + hs) return 'mt';
    if (canvasX >= x + w/2 - hs && canvasX <= x + w/2 + hs && canvasY >= y + h - hs && canvasY <= y + h + hs) return 'mb';
    
    return null;
}

function handleKeyDown(e) {
    const isInputActive = document.activeElement.tagName === 'INPUT' || 
                          document.activeElement.tagName === 'TEXTAREA';
    
    // Save: Cmd+S (Mac) or Ctrl+S (Windows)
    if ((e.metaKey || e.ctrlKey) && e.key === 's') {
        e.preventDefault();
        handleSave();
        return;
    }

    // Undo: Cmd+Z (Mac) or Ctrl+Z (Windows)
    if ((e.metaKey || e.ctrlKey) && e.key === 'z' && !e.shiftKey) {
        e.preventDefault();
        undo();
        return;
    }
    
    // Redo: Cmd+Shift+Z (Mac) or Ctrl+Y (Windows)
    if ((e.metaKey || e.ctrlKey) && ((e.key === 'z' && e.shiftKey) || e.key === 'y')) {
        e.preventDefault();
        redo();
        return;
    }
    
    // Copy: Cmd+C
    if ((e.metaKey || e.ctrlKey) && e.key === 'c' && !isInputActive) {
        e.preventDefault();
        copySelectedFields();
        return;
    }
    
    // Paste: Cmd+V (with offset) or Shift+Cmd+V (in place)
    if ((e.metaKey || e.ctrlKey) && e.key === 'v' && !isInputActive) {
        e.preventDefault();
        pasteFields(e.shiftKey);  // shiftKey = paste in place
        return;
    }
    
    // Cut: Cmd+X
    if ((e.metaKey || e.ctrlKey) && e.key === 'x' && !isInputActive) {
        e.preventDefault();
        cutSelectedFields();
        return;
    }
    
    // Duplicate: Ctrl/Cmd+D
    if ((e.metaKey || e.ctrlKey) && e.key === 'd') {
        e.preventDefault();
        if (selectedFields.length > 0) {
            duplicateSelectedFields();
        }
        return;
    }
    
    // Select All: Ctrl/Cmd+A
    if ((e.metaKey || e.ctrlKey) && e.key === 'a' && !isInputActive) {
        e.preventDefault();
        const pageFields = fields.filter(f => f.page === currentPage);
        selectedFields = [...pageFields];
        updatePropertiesPanel();
        updateFieldList();
        renderCanvas();
        return;
    }
    
    // Delete/Backspace
    if (e.key === 'Delete' || e.key === 'Backspace') {
        if (selectedFields.length > 0 && !isInputActive) {
            handleDelete();
        }
    }
    
    // Escape - close properties panel if open, otherwise deselect
    if (e.key === 'Escape') {
        hideContextMenu();
        const propPanel = document.getElementById('propertiesModal');
        if (propPanel && propPanel.style.display === 'flex') {
            closePropertiesModal();
            return;
        }
        selectedFields = [];
        updatePropertiesPanel();
        updateFieldList();
        renderCanvas();
    }
    
    // Arrow keys for nudge (1px normal, 10px with Shift)
    if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(e.key) && !isInputActive) {
        if (selectedFields.length > 0) {
            e.preventDefault();
            const nudgeAmount = e.shiftKey ? 10 : 1;
            nudgeFields(e.key, nudgeAmount);
        }
    }

    // Zoom shortcuts: Cmd + / Cmd - / Cmd 0
    if (e.metaKey || e.ctrlKey) {
        if (e.key === '=' || e.key === '+') {
            e.preventDefault();
            setZoom(zoom + 0.1);
            return;
        }
        if (e.key === '-') {
            e.preventDefault();
            setZoom(zoom - 0.1);
            return;
        }
        if (e.key === '0') {
            e.preventDefault();
            if (viewMode === 'scroll') {
                fitScrollToPage();
            } else {
                fitToPage();
                renderCanvas();
            }
            return;
        }
    }

    // Enter key for modals — only save if focus is inside the properties panel
    if (e.key === 'Enter') {
        const propertiesModal = document.getElementById('propertiesModal');
        if (propertiesModal && propertiesModal.style.display === 'flex' && propertiesModal.contains(document.activeElement)) {
            // Only trigger if not in a textarea (where Enter is used for newlines)
            if (document.activeElement.tagName !== 'TEXTAREA') {
                e.preventDefault();
                savePropertiesModal();
                return;
            }
        }
    }
}

function setZoom(newZoom) {
    // ---- Scroll-view branch: scroll #scrollViewContainer instead of canvasWrapper ----
    if (viewMode === 'scroll') {
        const container = document.getElementById('scrollViewContainer');
        const oldZoom = zoom;

        // Capture current center (content-space) BEFORE resize
        let prevCenterX = 0, prevCenterY = 0;
        if (container) {
            prevCenterX = container.scrollLeft + container.clientWidth  / 2;
            prevCenterY = container.scrollTop  + container.clientHeight / 2;
        }

        zoom = Math.max(0.1, Math.min(3.0, newZoom));
        document.getElementById('zoomLevel').textContent = Math.round(zoom * 100) + '%';
        applyScrollZoom();

        if (!container) return;

        // Center on selection (first selected field) if any, else preserve centre
        if (selectedFields.length > 0) {
            const f = selectedFields[0];
            const pageWrap = container.querySelector(`.sv-page-wrap[data-page-idx="${f.page}"]`);
            if (pageWrap) {
                const { iw, ih } = svGetWrapImgDims(pageWrap);
                if (iw && ih) {
                    // Centre of field in image-pixel coords within the wrap
                    const wrapW = pageWrap.clientWidth;
                    const wrapH = pageWrap.clientHeight || (wrapW * ih / iw);
                    const fcx = (f.rect[0] + f.rect[2]) / 2 * DPI_SCALE * (wrapW / iw);
                    const fcy = (f.rect[1] + f.rect[3]) / 2 * DPI_SCALE * (wrapH / ih);
                    // Wrap offset inside scroll container
                    const cRect = container.getBoundingClientRect();
                    const wRect = pageWrap.getBoundingClientRect();
                    const wrapLeft = container.scrollLeft + (wRect.left - cRect.left);
                    const wrapTop  = container.scrollTop  + (wRect.top  - cRect.top);
                    container.scrollTo({
                        left: Math.max(0, wrapLeft + fcx - container.clientWidth  / 2),
                        top:  Math.max(0, wrapTop  + fcy - container.clientHeight / 2),
                    });
                }
            }
        } else if (oldZoom > 0) {
            // No selection: preserve centre point by scaling it
            const ratio = zoom / oldZoom;
            container.scrollTo({
                left: Math.max(0, prevCenterX * ratio - container.clientWidth  / 2),
                top:  Math.max(0, prevCenterY * ratio - container.clientHeight / 2),
            });
        }
        // Preview refresh is now handled inside renderCanvas() scroll branch
        return;
    }

    // ---- Single-view branch (original behaviour) ----
    const wrapper = document.getElementById('canvasWrapper');
    const oldZoom = zoom;

    // Capture scroll center BEFORE renderCanvas changes the DOM
    let prevScrollCenterX = 0, prevScrollCenterY = 0;
    if (wrapper) {
        prevScrollCenterX = wrapper.scrollLeft + wrapper.clientWidth / 2;
        prevScrollCenterY = wrapper.scrollTop + wrapper.clientHeight / 2;
    }

    zoom = Math.max(0.25, Math.min(3.0, newZoom));
    document.getElementById('zoomLevel').textContent = Math.round(zoom * 100) + '%';
    renderCanvas();

    // After render, center on selection or preserve scroll center
    if (wrapper) {
        if (selectedFields.length > 0) {
            // Center on the selection's midpoint
            let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
            for (const f of selectedFields) {
                minX = Math.min(minX, f.rect[0]);
                minY = Math.min(minY, f.rect[1]);
                maxX = Math.max(maxX, f.rect[2]);
                maxY = Math.max(maxY, f.rect[3]);
            }
            const centerX = (minX + maxX) / 2 * zoom * DPI_SCALE;
            const centerY = (minY + maxY) / 2 * zoom * DPI_SCALE;
            wrapper.scrollTo({
                left: Math.max(0, centerX - wrapper.clientWidth / 2),
                top: Math.max(0, centerY - wrapper.clientHeight / 2),
            });
        } else {
            // No selection: keep the current center point stable
            const ratio = zoom / oldZoom;
            wrapper.scrollTo({
                left: Math.max(0, prevScrollCenterX * ratio - wrapper.clientWidth / 2),
                top: Math.max(0, prevScrollCenterY * ratio - wrapper.clientHeight / 2),
            });
        }
    }

    // Refresh preview inputs if in preview mode
    if (isPreviewMode) {
        renderPreviewInputs();
    }
}

// ============ Property Panel ============
// Update a color swatch button and its hidden input
function updateColorSwatch(targetId, hex) {
    const input = document.getElementById(targetId);
    if (input) input.value = hex;
    
    const preview = document.getElementById('preview_' + targetId);
    if (preview) {
        if (hex === 'transparent' || !hex) {
            preview.style.background = 'transparent';
            preview.style.backgroundImage = 'linear-gradient(45deg, #ccc 25%, transparent 25%), linear-gradient(-45deg, #ccc 25%, transparent 25%), linear-gradient(45deg, transparent 75%, #ccc 75%), linear-gradient(-45deg, transparent 75%, #ccc 75%)';
            preview.style.backgroundSize = '8px 8px';
        } else {
            preview.style.background = hex;
            preview.style.backgroundImage = 'none';
        }
    }
    
    const label = document.getElementById('text_' + targetId);
    if (label) {
        label.textContent = (hex && hex !== 'transparent') ? hex.toUpperCase() : 'None';
    }
}

let _updatingPropPanel = false;
function updatePropertiesPanel() {
    // Auto-update floating properties panel when selection changes (with recursion guard)
    // Skip auto-update when in pick mode (user is clicking fields to pick)
    if (!_updatingPropPanel && !isPickModeActive()) {
        const propPanel = document.getElementById('propertiesModal');
        if (propPanel && propPanel.style.display === 'flex') {
            _updatingPropPanel = true;
            try {
                if (selectedFields.length === 1 && selectedFields[0] !== modalField) {
                    // Different single field selected — auto-save current, reload new
                    openPropertiesModal(selectedFields[0]);
                } else if (selectedFields.length > 1) {
                    // Multiple fields selected — auto-save current, switch to multi-field mode
                    if (modalField && !isMultiFieldEdit) {
                        savePropertiesModalSilent();
                    }
                    openMultiFieldPropertiesModal(selectedFields);
                } else if (selectedFields.length === 0) {
                    // No selection — auto-save current field and update title
                    if (modalField) {
                        savePropertiesModalSilent();
                        modalField = null;
                    }
                    document.getElementById('modalTitle').textContent = 'No Field Selected';
                }
            } finally {
                _updatingPropPanel = false;
            }
        }
    }

    // Sync calc manager highlight with current selection
    highlightCalcManagerRow();

    const noSelection = document.getElementById('noSelection');
    const singleSelection = document.getElementById('singleSelection');
    const multiSelection = document.getElementById('multiSelection');
    const panelActions = document.getElementById('panelActions');
    const btnGroupRadio = document.getElementById('btnGroupRadio');
    const btnUngroupRadio = document.getElementById('btnUngroupRadio');
    const textFieldStyling = document.getElementById('textFieldStyling');
    const checkboxStyling = document.getElementById('checkboxStyling');
    const dropdownStyling = document.getElementById('dropdownStyling');
    const formattingStyling = document.getElementById('formattingStyling');
    
    if (selectedFields.length === 0) {
        noSelection.style.display = 'block';
        singleSelection.style.display = 'none';
        multiSelection.style.display = 'none';
        panelActions.style.display = 'none';
    } else if (selectedFields.length === 1) {
        noSelection.style.display = 'none';
        singleSelection.style.display = 'block';
        multiSelection.style.display = 'none';
        panelActions.style.display = 'flex';
        
        const field = selectedFields[0];
        document.getElementById('propName').value = field.name;
        document.getElementById('propTooltip').value = field.tooltip || '';
        document.getElementById('propType').textContent = getTypeLabel(field.type);

        const propPage = document.getElementById('propPage');
        if (propPage) propPage.textContent = (field.page + 1).toString();

        // v1.1.0: Show/edit radio group name in the side panel
        const groupRow = document.getElementById('propRadioGroupRow');
        const groupInput = document.getElementById('propRadioGroupName');
        if (groupRow && groupInput) {
            if (field.type === 'radio' && field.radio_group) {
                groupRow.style.display = 'block';
                groupInput.value = field.radio_group;
            } else {
                groupRow.style.display = 'none';
                groupInput.value = '';
            }
        }
        
        document.getElementById('propPosition').textContent = 
            `${Math.round(field.rect[0])}, ${Math.round(field.rect[1])}`;
        document.getElementById('propSize').textContent = 
            `${Math.round(field.rect[2] - field.rect[0])}, ${Math.round(field.rect[3] - field.rect[1])}`;
        
        // Show/hide styling sections based on field type
        if (textFieldStyling && checkboxStyling && dropdownStyling) {
            if (field.type === 'text') {
                textFieldStyling.style.display = 'block';
                checkboxStyling.style.display = 'none';
                dropdownStyling.style.display = 'none';
            } else if (field.type === 'checkbox' || field.type === 'radio') {
                textFieldStyling.style.display = 'none';
                checkboxStyling.style.display = 'block';
                dropdownStyling.style.display = 'none';
                
                // Show batch checkbox style row if multi-selected
                const batchCheckboxRow = document.getElementById('batchCheckboxStyleRow');
                if (batchCheckboxRow) {
                    batchCheckboxRow.style.display = selectedFields.length > 1 ? 'flex' : 'none';
                }
            } else if (field.type === 'combobox' || field.type === 'listbox') {
                textFieldStyling.style.display = 'none';
                checkboxStyling.style.display = 'none';
                dropdownStyling.style.display = 'block';
                
                renderChoiceList(field, 'propDropdownList', 'prop');
                
                // Populate flags
                document.getElementById('propDropdownSort').checked = !!(field.style?.sort ?? field.sort);
                document.getElementById('propDropdownEditable').checked = !!(field.style?.editable ?? field.editable);
                document.getElementById('propDropdownSpellCheck').checked = !!(field.style?.spellCheck ?? field.spellCheck ?? true);
                document.getElementById('propDropdownCommit').checked = !!(field.style?.commitImmediately ?? field.commitImmediately);
            } else {
                textFieldStyling.style.display = (field.type === 'text') ? 'block' : 'none';
                checkboxStyling.style.display = (field.type === 'checkbox' || field.type === 'radio') ? 'block' : 'none';
                dropdownStyling.style.display = (field.type === 'combobox' || field.type === 'listbox') ? 'block' : 'none';
            }
        }
        
        // Populate styling inputs with field's current values
        // Populate styling inputs with field's current values
        
        // Font Family
        const fontInput = document.getElementById('propFontFamily');
        if (fontInput) {
             const globalDefault = window.globalFieldSettings?.fontFamily || 'Helvetica';
             fontInput.value = field.style?.fontFamily || field.font_family || field.fontFamily || globalDefault;
        }

        // Font Size
        const fontSizeInput = document.getElementById('propFontSize');
        if (fontSizeInput) {
            const globalDefault = window.globalFieldSettings?.fontSize || 9;
            const sizeValue = field.style?.fontSize || field.font_size || field.fontSize || globalDefault;
            fontSizeInput.value = isNaN(parseFloat(sizeValue)) ? globalDefault : sizeValue;
        }
        
        // Text Align
        const textAlignInput = document.getElementById('propTextAlign'); 
        if (textAlignInput) {
            textAlignInput.value = field.style?.textAlign || field.text_align || field.textAlign || 'left';
        }

        // Font Color
        const fontColorInput = document.getElementById('propFontColor');
        if (fontColorInput) {
            const globalDefault = window.globalFieldSettings?.fontColor || '#000000';
            // Check edited style first, then extracted value, then global default
            const colorValue = field.style?.fontColor || field.text_color || field.fontColor || globalDefault;
            updateColorSwatch('propFontColor', colorValue);
        }

        // Fill Color
        const fillColorInput = document.getElementById('propFillColor');
        if (fillColorInput) {
             const globalDefault = window.globalFieldSettings?.fillColor || '#EDF4FF';
             // Check edited style first, then extracted value
             // Empty string from extract_fields means transparent (no fill color)
             const extractedFill = field.fill_color ?? field.fillColor;
             const styledFill = field.style?.fillColor;
             
             let colorValue;
             if (styledFill !== undefined) {
                 // User has edited this field's fill color in this session
                 colorValue = styledFill;
             } else if (extractedFill === '' || extractedFill === 'transparent') {
                 // Empty string from extraction means transparent field
                 colorValue = 'transparent';
             } else if (extractedFill) {
                 // Valid color from PDF
                 colorValue = extractedFill;
             } else {
                 // Use global default
                 colorValue = globalDefault;
             }
            updateColorSwatch('propFillColor', colorValue);
        }
        
        // Checkbox Style
        const checkboxStyleInput = document.getElementById('propCheckboxStyle');
        if (checkboxStyleInput) {
            const globalDefault = window.globalFieldSettings?.checkboxStyle || 'check';
            checkboxStyleInput.value = field.style?.checkboxStyle || field.checkboxStyle || field.checkbox_style || globalDefault;
        }
        
        // Export Value (for checkbox-as-radio behavior)
        const exportValueInput = document.getElementById('propExportValue');
        if (exportValueInput) {
            exportValueInput.value = field.style?.exportValue || field.exportValue || field.export_value || 'Yes';
        }
        
        // Show formatting options for text fields
        if (formattingStyling) {
            const dateOptions = document.getElementById('dateFormatOptions');
            const currencyOptions = document.getElementById('currencyFormatOptions');
            const phoneOptions = document.getElementById('phoneFormatOptions');
            const numberOptions = document.getElementById('numberFormatOptions');
            const formatTypeSelect = document.getElementById('propFormatType');
            
            // Reset all
            dateOptions.style.display = 'none';
            currencyOptions.style.display = 'none';
            phoneOptions.style.display = 'none';
            numberOptions.style.display = 'none';
            const zipOptions = document.getElementById('zipFormatOptions');
            if (zipOptions) zipOptions.style.display = 'none';
            formattingStyling.style.display = 'none';
            
            if (field.type === 'text' || field.type === 'combobox') {
                formattingStyling.style.display = 'block';
                
                // Load format type from field data (extracted from PDF)
                // Use same fallback detection chain as the properties modal
                let formatType = field.formatType || field.style?.formatType || '';
                if (!formatType) {
                    if (field.dateFormat || field.style?.dateFormat) formatType = 'date';
                    else if (field.currencySymbol !== undefined || field.style?.currencySymbol !== undefined) formatType = 'currency';
                    else if (field.phoneFormat || field.style?.phoneFormat) formatType = 'phone';
                    else if (field.numberDecimals !== undefined || field.style?.numberDecimals !== undefined) formatType = 'number';
                    else if (field.zipFormat || field.style?.zipFormat) formatType = 'zip';
                }
                if (formatTypeSelect) {
                    formatTypeSelect.value = formatType;
                }
                
                // Show sub-options based on format type
                showFormatSubOptions(formatType);
                
                // Pre-populate options if available
                if (formatType === 'date') {
                    const dateFormatSelect = document.getElementById('propDateFormat');
                    if (dateFormatSelect) {
                        dateFormatSelect.value = field.style?.dateFormat || field.dateFormat || '';
                    }
                } else if (formatType === 'currency') {
                    const curSym = document.getElementById('propCurrencySymbol');
                    const curDec = document.getElementById('propCurrencyDecimals');
                    const curLoc = document.getElementById('propSymbolLocation');
                    if (curSym) curSym.value = field.style?.currencySymbol ?? field.currencySymbol ?? '$';
                    if (curDec) curDec.value = field.style?.currencyDecimals || field.currencyDecimals || 2;
                    if (curLoc) curLoc.value = field.style?.symbolLocation || field.symbolLocation || 'before-space';
                } else if (formatType === 'phone') {
                    const phoneFmt = document.getElementById('propPhoneFormat');
                    if (phoneFmt) phoneFmt.value = field.style?.phoneFormat || field.phoneFormat || '';
                } else if (formatType === 'number') {
                    const numDec = document.getElementById('propNumberDecimals');
                    const numSep = document.getElementById('propThousandSep');
                    const vDec = field.style?.numberDecimals ?? field.numberDecimals;
                    if (numDec) numDec.value = (vDec !== undefined && vDec !== null) ? vDec : 0;
                    const vSep = field.style?.thousandSep ?? field.thousandSep;
                    if (numSep) numSep.value = (vSep !== undefined && vSep !== null) ? vSep : ',';
                } else if (formatType === 'zip') {
                    const zipFmt = document.getElementById('propZipFormat');
                    if (zipFmt) zipFmt.value = field.style?.zipFormat || field.zipFormat || 'XXXXX';
                }
            }
        }
        
        btnGroupRadio.style.display = 'none';
        btnUngroupRadio.style.display = field.type === 'radio' ? 'block' : 'none';
    } else {
        noSelection.style.display = 'none';
        singleSelection.style.display = 'none';
        multiSelection.style.display = 'block';
        panelActions.style.display = 'flex';
        
        document.getElementById('selectedCount').textContent = `${selectedFields.length} fields`;
        
        const types = [...new Set(selectedFields.map(f => f.type))];
        document.getElementById('selectedTypes').textContent = types.map(getTypeLabel).join(', ');
        
        const list = document.getElementById('selectedFieldList');
        list.innerHTML = selectedFields.map(f => `
            <div class="field-item selected">
                <span class="field-type-icon">${getTypeIcon(f.type)}</span>
                <span class="field-name">${f.name}</span>
            </div>
        `).join('');
        
        const allCheckboxes = selectedFields.every(f => f.type === 'checkbox');
        const allRadios = selectedFields.every(f => f.type === 'radio');
        
        // Show batch format row only if selection includes text/dropdown fields
        const textFields = selectedFields.filter(f => f.type === 'text' || f.type === 'combobox');
        const hasTextFields = textFields.length > 0;
        const batchFormatRow = document.getElementById('batchFormatRow');
        const batchFormat = document.getElementById('batchFormat');
        const batchSubFormatRow = document.getElementById('batchSubFormatRow');
        
        if (batchFormatRow) {
            batchFormatRow.style.display = hasTextFields ? 'flex' : 'none';
        }
        
        if (hasTextFields && batchFormat) {
            const formatTypes = textFields.map(f => {
                let ft = f.formatType || f.style?.formatType || '';
                if (!ft) {
                    if (f.dateFormat || f.style?.dateFormat) ft = 'date';
                    else if (f.currencySymbol !== undefined || f.style?.currencySymbol !== undefined) ft = 'currency';
                    else if (f.phoneFormat || f.style?.phoneFormat) ft = 'phone';
                    else if (f.numberDecimals !== undefined || f.style?.numberDecimals !== undefined) ft = 'number';
                    else if (f.zipFormat || f.style?.zipFormat) ft = 'zip';
                }
                return ft;
            });
            const uniqueFormats = [...new Set(formatTypes)];

            if (uniqueFormats.length === 1 && uniqueFormats[0]) {
                batchFormat.value = uniqueFormats[0];
                showBatchSubFormatOptions(uniqueFormats[0]);
            } else {
                batchFormat.value = '';
                showBatchSubFormatOptions('');
            }
        }

        // Show batch calculation row when text fields are selected
        const batchCalcRow = document.getElementById('batchCalcRow');
        if (batchCalcRow) {
            batchCalcRow.style.display = hasTextFields ? 'flex' : 'none';
            // Reset when selection changes
            const batchCalcType = document.getElementById('batchCalcType');
            if (batchCalcType) batchCalcType.value = '';
            const batchCalcSourceRow = document.getElementById('batchCalcSourceRow');
            if (batchCalcSourceRow) batchCalcSourceRow.style.display = 'none';
            const batchCalcConstantRow = document.getElementById('batchCalcConstantRow');
            if (batchCalcConstantRow) batchCalcConstantRow.style.display = 'none';
            const batchColPicker = document.getElementById('batchCalcColumnPicker');
            if (batchColPicker) batchColPicker.style.display = 'none';
        }

        // Detect common styling among all selected fields
        const getCommonValue = (propMap) => {
            const values = selectedFields.map(propMap);
            const unique = [...new Set(values)];
            return unique.length === 1 ? unique[0] : '';
        };

        const commonFont = getCommonValue(f => f.style?.fontFamily || f.font_family || f.fontFamily || '');
        const commonSize = getCommonValue(f => f.style?.fontSize || f.font_size || f.fontSize || '');
        const commonAlign = getCommonValue(f => f.style?.textAlign || f.text_align || f.textAlign || '');
        const commonColor = getCommonValue(f => f.style?.fontColor || f.text_color || f.fontColor || '');
        // Fill color: resolve per-field using same transparent-aware logic as single-select
        const commonFill = getCommonValue(f => {
            const styled = f.style?.fillColor;
            if (styled !== undefined) return styled;
            const extracted = f.fill_color ?? f.fillColor;
            if (extracted === '' || extracted === 'transparent') return 'transparent';
            if (extracted) return extracted;
            return '';
        });

        document.getElementById('batchFontFamily').value = commonFont;
        document.getElementById('batchFontSize').value = commonSize;
        document.getElementById('batchTextAlign').value = commonAlign;

        updateColorSwatch('batchFontColor', commonColor || '#000000');
        updateColorSwatch('batchFillColor', commonFill || (window.globalFieldSettings?.fillColor || '#EDF4FF'));

        btnGroupRadio.style.display = allCheckboxes && selectedFields.length >= 2 ? 'block' : 'none';
        btnUngroupRadio.style.display = allRadios ? 'block' : 'none';
    }
}

function getTypeLabel(type) {
    const labels = { 'text': 'Text Field', 'checkbox': 'Checkbox', 'radio': 'Radio Button',
                     'button': 'Button', 'combobox': 'Dropdown', 'listbox': 'List Box',
                     'link': 'Hyperlink' };
    return labels[type] || type;
}

function getTypeIcon(type) {
    const icons = { 'text': '📝', 'checkbox': '☐', 'radio': '🔘', 'button': '🔲', 'combobox': '📋', 'listbox': '📃', 'link': '🔗' };
    return icons[type] || '📄';
}

// Show/hide format-specific options based on format type
function showFormatSubOptions(formatType) {
    const dateOptions = document.getElementById('dateFormatOptions');
    const currencyOptions = document.getElementById('currencyFormatOptions');
    const phoneOptions = document.getElementById('phoneFormatOptions');
    const numberOptions = document.getElementById('numberFormatOptions');
    
    // Hide all first
    if (dateOptions) dateOptions.style.display = 'none';
    if (currencyOptions) currencyOptions.style.display = 'none';
    if (phoneOptions) phoneOptions.style.display = 'none';
    if (numberOptions) numberOptions.style.display = 'none';
    if (document.getElementById('zipFormatOptions')) document.getElementById('zipFormatOptions').style.display = 'none';
    
    // Show specific options
    if (formatType === 'date' && dateOptions) {
        dateOptions.style.display = 'block';
    } else if (formatType === 'currency' && currencyOptions) {
        currencyOptions.style.display = 'block';
    } else if (formatType === 'phone' && phoneOptions) {
        phoneOptions.style.display = 'block';
    } else if (formatType === 'number' && numberOptions) {
        numberOptions.style.display = 'block';
    } else if (formatType === 'zip' && document.getElementById('zipFormatOptions')) {
        document.getElementById('zipFormatOptions').style.display = 'block';
    }
}

    // Batch checkbox style
    const batchCheckboxRow = document.getElementById('batchCheckboxStyleRow');
    if (batchCheckboxRow) {
        const hasCheckboxes = selectedFields.some(f => f.type === 'checkbox' || f.type === 'radio');
        batchCheckboxRow.style.display = hasCheckboxes ? 'flex' : 'none';
    }

// Show/hide batch sub-format options (for multi-selection)
function showBatchSubFormatOptions(formatType) {
    const subFormatRow = document.getElementById('batchSubFormatRow');
    const dateRow = document.getElementById('batchDateFormatRow');
    const currencyRow = document.getElementById('batchCurrencyRow');
    const phoneRow = document.getElementById('batchPhoneRow');
    
    const numberRow = document.getElementById('batchNumberRow');
    const symLocRow = document.getElementById('batchSymbolLocationRow');
    
    // Hide all first
    if (dateRow) dateRow.style.display = 'none';
    if (currencyRow) currencyRow.style.display = 'none';
    if (phoneRow) phoneRow.style.display = 'none';
    if (numberRow) numberRow.style.display = 'none';
    if (symLocRow) symLocRow.style.display = 'none';
    if (document.getElementById('batchZipRow')) document.getElementById('batchZipRow').style.display = 'none';
    
    // Show container and specific row
    if (subFormatRow) {
        subFormatRow.style.display = formatType ? 'block' : 'none';
    }
    
    if (formatType === 'date' && dateRow) {
        dateRow.style.display = 'flex';
    } else if (formatType === 'currency' && currencyRow) {
        currencyRow.style.display = 'flex';
        if (symLocRow) symLocRow.style.display = 'flex';
    } else if (formatType === 'phone' && phoneRow) {
        phoneRow.style.display = 'flex';
    } else if (formatType === 'number' && numberRow) {
        numberRow.style.display = 'flex';
    } else if (formatType === 'zip' && document.getElementById('batchZipRow')) {
        document.getElementById('batchZipRow').style.display = 'flex';
    }
}

// ============ Style Change Tracking ============
function setupStyleListeners() {
    // Text field styling
    const styleInputs = [
        { id: 'propFontFamily', prop: 'fontFamily' },
        { id: 'propFontSize', prop: 'fontSize' },
        { id: 'propFontColor', prop: 'fontColor' },
        { id: 'propTextAlign', prop: 'textAlign' },
        { id: 'propFillColor', prop: 'fillColor' },
        { id: 'propCheckboxStyle', prop: 'checkboxStyle' },
        { id: 'propExportValue', prop: 'exportValue' },
        // Formatting options
        { id: 'propFormatType', prop: 'formatType' },
        { id: 'propDateFormat', prop: 'dateFormat' },
        { id: 'propCurrencySymbol', prop: 'currencySymbol' },
        { id: 'propCurrencyDecimals', prop: 'currencyDecimals' },
        { id: 'propSymbolLocation', prop: 'symbolLocation' },
        { id: 'propPhoneFormat', prop: 'phoneFormat' },
        { id: 'propZipFormat', prop: 'zipFormat' },
        { id: 'propNumberDecimals', prop: 'numberDecimals' },
        { id: 'propThousandSep', prop: 'thousandSep' }
    ];
    
    for (const { id, prop } of styleInputs) {
        const el = document.getElementById(id);
        if (el) {
            el.addEventListener('change', () => handleStyleChange(prop, el.value));
        }
    }

    // Dropdown Action Listeners (Sidebar)
    const btnAddOpt = document.getElementById('btnAddDropdownOption');
    if (btnAddOpt) btnAddOpt.addEventListener('click', () => handleChoiceOptionAction('add', 'prop'));
    const btnDelOpt = document.getElementById('btnDeleteDropdownOption');
    if (btnDelOpt) btnDelOpt.addEventListener('click', () => handleChoiceOptionAction('delete', 'prop'));
    const btnUpOpt = document.getElementById('btnMoveUpDropdownOption');
    if (btnUpOpt) btnUpOpt.addEventListener('click', () => handleChoiceOptionAction('up', 'prop'));
    const btnDownOpt = document.getElementById('btnMoveDownDropdownOption');
    if (btnDownOpt) btnDownOpt.addEventListener('click', () => handleChoiceOptionAction('down', 'prop'));

    // Dropdown flag listeners (Sidebar)
    ['Sort', 'Editable', 'SpellCheck', 'Commit'].forEach(flag => {
        const el = document.getElementById(`propDropdown${flag}`);
        if (el) {
            el.addEventListener('change', (e) => {
                if (selectedFields.length === 1) {
                    const field = selectedFields[0];
                    let prop = flag.charAt(0).toLowerCase() + flag.slice(1);
                    if (prop === 'commit') prop = 'commitImmediately';
                    
                    if (!field.style) field.style = {};
                    field.style[prop] = e.target.checked;
                    field[prop] = e.target.checked;
                    if (!changes.styled[field.name]) changes.styled[field.name] = {};
                    changes.styled[field.name][prop] = e.target.checked;
                    markUnsaved();
                }
            });
        }
    });

    // Dropdown Action Listeners (Modal)
    const modalBtnAddOpt = document.getElementById('modalBtnAddDropdownOption');
    if (modalBtnAddOpt) modalBtnAddOpt.addEventListener('click', () => handleChoiceOptionAction('add', 'modalProp'));
    const modalBtnDelOpt = document.getElementById('modalBtnDeleteDropdownOption');
    if (modalBtnDelOpt) modalBtnDelOpt.addEventListener('click', () => handleChoiceOptionAction('delete', 'modalProp'));
    const modalBtnUpOpt = document.getElementById('modalBtnMoveUpDropdownOption');
    if (modalBtnUpOpt) modalBtnUpOpt.addEventListener('click', () => handleChoiceOptionAction('up', 'modalProp'));
    const modalBtnDownOpt = document.getElementById('modalBtnMoveDownDropdownOption');
    if (modalBtnDownOpt) modalBtnDownOpt.addEventListener('click', () => handleChoiceOptionAction('down', 'modalProp'));
    
    // Special handler for format type - also toggle sub-options
    const formatTypeSelect = document.getElementById('propFormatType');
    if (formatTypeSelect) {
        formatTypeSelect.addEventListener('change', () => {
            const val = formatTypeSelect.value;
            showFormatSubOptions(val);

            // Save the formatType itself (not just sub-options)
            const finalVal = (val === 'none' || val === '') ? '' : val;
            handleStyleChange('formatType', finalVal);
            // Also set directly on field object so it takes priority on reload
            for (const field of selectedFields) {
                field.formatType = finalVal;
            }

            // Force update sub-options to ensure defaults are saved
            if (val === 'zip') {
                const el = document.getElementById('propZipFormat');
                if (el) handleStyleChange('zipFormat', el.value || 'XXXXX');
            } else if (val === 'phone') {
                const el = document.getElementById('propPhoneFormat');
                if (el) handleStyleChange('phoneFormat', el.value || '(XXX) XXX-XXXX');
            } else if (val === 'currency') {
                const elSym = document.getElementById('propCurrencySymbol');
                if (elSym) handleStyleChange('currencySymbol', elSym.value !== undefined ? elSym.value : '$');
                const elDec = document.getElementById('propCurrencyDecimals');
                if (elDec) handleStyleChange('currencyDecimals', elDec.value || '2');
                const elLoc = document.getElementById('propSymbolLocation');
                if (elLoc) handleStyleChange('symbolLocation', elLoc.value || 'before-space');
            } else if (val === 'date') {
                const el = document.getElementById('propDateFormat');
                if (el) handleStyleChange('dateFormat', el.value || 'MM/DD/YYYY');
            } else if (val === 'number') {
                const elDec = document.getElementById('propNumberDecimals');
                if (elDec) handleStyleChange('numberDecimals', (elDec.value !== undefined && elDec.value !== null) ? elDec.value : '0');
                const elSep = document.getElementById('propThousandSep');
                if (elSep) handleStyleChange('thousandSep', (elSep.value !== undefined && elSep.value !== null) ? elSep.value : ',');
            }
        });
    }
    
    // Apply Format button - saves format type + sub-options for the selected field
    const btnApplyFormat = document.getElementById('btnApplyFormat');
    if (btnApplyFormat) {
        btnApplyFormat.addEventListener('click', () => {
            if (selectedFields.length === 0) return;
            const formatVal = document.getElementById('propFormatType')?.value || '';
            const finalFormat = (formatVal === '' || formatVal === 'none') ? '' : formatVal;

            // Save the format type
            handleStyleChange('formatType', finalFormat);
            // Also set directly on field object so it takes priority on reload
            for (const field of selectedFields) {
                field.formatType = finalFormat;
            }

            // Save sub-options based on selected format
            if (formatVal === 'date') {
                const el = document.getElementById('propDateFormat');
                if (el) handleStyleChange('dateFormat', el.value || 'MM/DD/YYYY');
            } else if (formatVal === 'currency') {
                const elSym = document.getElementById('propCurrencySymbol');
                if (elSym) handleStyleChange('currencySymbol', elSym.value !== undefined ? elSym.value : '$');
                const elDec = document.getElementById('propCurrencyDecimals');
                if (elDec) handleStyleChange('currencyDecimals', elDec.value || '2');
                const elLoc = document.getElementById('propSymbolLocation');
                if (elLoc) handleStyleChange('symbolLocation', elLoc.value || 'before-space');
            } else if (formatVal === 'phone') {
                const el = document.getElementById('propPhoneFormat');
                if (el) handleStyleChange('phoneFormat', el.value || '(XXX) XXX-XXXX');
            } else if (formatVal === 'number') {
                const elDec = document.getElementById('propNumberDecimals');
                if (elDec) handleStyleChange('numberDecimals', (elDec.value !== undefined && elDec.value !== null) ? elDec.value : '0');
                const elSep = document.getElementById('propThousandSep');
                if (elSep) handleStyleChange('thousandSep', (elSep.value !== undefined && elSep.value !== null) ? elSep.value : ',');
            } else if (formatVal === 'zip') {
                const el = document.getElementById('propZipFormat');
                if (el) handleStyleChange('zipFormat', el.value || 'XXXXX');
            }

            // Visual feedback
            btnApplyFormat.textContent = '✓ Format Applied!';
            btnApplyFormat.style.borderColor = 'rgba(76,175,80,0.8)';
            btnApplyFormat.style.color = '#4caf50';
            setTimeout(() => {
                btnApplyFormat.textContent = '✓ Apply to Selected Field';
                btnApplyFormat.style.borderColor = 'rgba(33,150,243,0.5)';
                btnApplyFormat.style.color = '#2196f3';
            }, 1500);
        });
    }

    // Dropdown options button - saves the current options to changes for PDF output
    const btnApplyDropdownOptions = document.getElementById('btnApplyDropdownOptions');
    if (btnApplyDropdownOptions) {
        btnApplyDropdownOptions.addEventListener('click', () => {
            if (selectedFields.length !== 1) return;
            const field = selectedFields[0];
            if (field.type !== 'combobox') return;
            
            // Get the current options from the field (added via ADD ITEM button)
            const currentOptions = field.options || [];
            
            if (currentOptions.length === 0) {
                showAlert('Please add at least one option using the "+ ADD ITEM" button first.', '⚠️ No Options');
                return;
            }
            
            saveState();
            
            // Normalize options to proper object format
            const normalizedOptions = currentOptions.map(opt => {
                if (typeof opt === 'string') {
                    return { label: opt, value: opt };
                }
                return { label: opt.label || '', value: opt.value || opt.label || '' };
            });
            
            // Store normalized options back on the field
            field.options = normalizedOptions;
            
            // Track in changes for save
            if (!changes.styled[field.name]) {
                changes.styled[field.name] = {};
            }
            changes.styled[field.name].options = normalizedOptions;
            
            // Also save the dropdown flags
            const sort = document.getElementById('propDropdownSort')?.checked || false;
            const editable = document.getElementById('propDropdownEditable')?.checked || false;
            const spellCheck = document.getElementById('propDropdownSpellCheck')?.checked || false;
            const commitImmediately = document.getElementById('propDropdownCommit')?.checked || false;
            
            changes.styled[field.name].sort = sort;
            changes.styled[field.name].editable = editable;
            changes.styled[field.name].spellCheck = spellCheck;
            changes.styled[field.name].commitImmediately = commitImmediately;
            
            // Update field style
            if (!field.style) field.style = {};
            field.style.sort = sort;
            field.style.editable = editable;
            field.style.spellCheck = spellCheck;
            field.style.commitImmediately = commitImmediately;
            
            // Refresh the dropdown options list display
            renderChoiceList(field, 'propDropdownList', 'prop');
            
            markUnsaved();
            showAlert(`Saved ${normalizedOptions.length} options to dropdown.`, '✅ Options Saved');
        });
    }
    
    // Export Value Apply button
    const btnApplyExportValue = document.getElementById('btnApplyExportValue');
    if (btnApplyExportValue) {
        btnApplyExportValue.addEventListener('click', () => {
            if (selectedFields.length !== 1) return;
            const field = selectedFields[0];
            
            const exportValueInput = document.getElementById('propExportValue');
            if (!exportValueInput) return;
            
            const value = exportValueInput.value.trim() || 'Yes';
            
            saveState();
            
            // Store on field object
            if (!field.style) field.style = {};
            field.style.exportValue = value;
            
            // Track in changes for save
            if (!changes.styled[field.name]) {
                changes.styled[field.name] = {};
            }
            changes.styled[field.name].exportValue = value;
            
            markUnsaved();
        });
    }
}

function handleStyleChange(prop, value) {
    if (selectedFields.length === 0) return;
    
    saveState();  // Save state before style change
    
    // Apply style change to ALL selected fields (supports multi-selection)
    for (const field of selectedFields) {
        // Initialize style object for this field if not exists
        // Fix for duplicate names: track by PDF object number (stable across saves)
        if (field.objgen) {
            const objKey = field.objgen.join(',');
            if (!changes.styled_by_objgen) changes.styled_by_objgen = {};
            if (!changes.styled_by_objgen[objKey]) changes.styled_by_objgen[objKey] = {};
            changes.styled_by_objgen[objKey][prop] = value;
            changes.styled_by_objgen[objKey]._name = field.name;
        }

        if (!changes.styled[field.name]) {
            changes.styled[field.name] = {};
        }
        
        // Store the style change
        changes.styled[field.name][prop] = value;
        
        // Also store on the field object for consistency
        if (!field.style) field.style = {};
        field.style[prop] = value;
    }
    markUnsaved();
}

// ============ Actions ============
function handleNameChange() {
    if (selectedFields.length === 1) {
        const field = selectedFields[0];
        const oldName = field.name;
        const newName = document.getElementById('propName').value.trim();
        
        if (newName && newName !== oldName) {
            saveState();  // Save state before rename
            
            // Check if this is a newly created field (in changes.created)
            const createdIdx = changes.created ? changes.created.findIndex(f => f.name === oldName) : -1;
            
            if (createdIdx >= 0) {
                // Field was created in this session - just update its name in the created array
                changes.created[createdIdx].name = newName;
                // Don't add to renamed since it's a new field
            } else {
                // Existing field - add to renamed
                changes.renamed[oldName] = newName;
            }
            
            // Migrate any unsaved style changes to the new name
            if (changes.styled[oldName]) {
                changes.styled[newName] = changes.styled[oldName];
                delete changes.styled[oldName];
            }
            
            field.name = newName;
            markUnsaved();
            updateFieldList();  // Update the field list to show new name
            renderCanvas();
        }
    }
}

function handleTooltipChange() {
    if (selectedFields.length === 1) {
        const field = selectedFields[0];
        const newTooltip = document.getElementById('propTooltip').value.trim();
        
        if (newTooltip !== (field.tooltip || '')) {
            saveState();
            field.tooltip = newTooltip;
            
            // Track tooltip changes for saving
            if (!changes.styled[field.name]) {
                changes.styled[field.name] = {};
            }
            changes.styled[field.name].tooltip = newTooltip;
            
            markUnsaved();
        }
    }
}

function handleDelete() {
    if (selectedFields.length === 0) return;
    
    saveState();  // Save state before delete
    
    for (const field of selectedFields) {
        // Check if this is a newly created field (exists in changes.created)
        const createdIdx = changes.created ? changes.created.findIndex(f => f.name === field.name) : -1;
        
        if (createdIdx >= 0) {
            // Field was created in this session - just remove from created array, don't add to deleted
            changes.created.splice(createdIdx, 1);
        } else {
            // Existing field - add to deleted array (avoid duplicates)
            if (!changes.deleted.includes(field.name)) {
                changes.deleted.push(field.name);
            }
        }
        
        // Remove from fields array
        const index = fields.indexOf(field);
        if (index >= 0) fields.splice(index, 1);
        
        // Also clean up from styled if present
        if (changes.styled[field.name]) {
            delete changes.styled[field.name];
        }
    }
    
    selectedFields = [];
    updatePropertiesPanel();
    updateFieldCount();
    markUnsaved();
    renderCanvas();
}

// Handle batch apply to multiple fields
function handleBatchApply() {
    if (selectedFields.length < 2) return;
    
    saveState();  // Save state before batch edit
    
    // Get values from batch inputs
    const batchFontFamily = document.getElementById('batchFontFamily').value;
    const batchFontSize = document.getElementById('batchFontSize').value;
    const batchTextAlign = document.getElementById('batchTextAlign').value;
    const batchFontColor = document.getElementById('batchFontColor').value;
    const batchFillColor = document.getElementById('batchFillColor').value;
    const batchCheckboxStyle = document.getElementById('batchCheckboxStyle')?.value;
    
    let changesMade = 0;
    
    for (const field of selectedFields) {
        // Initialize style object if not exists
        if (!changes.styled[field.name]) {
            changes.styled[field.name] = {};
        }
        if (!field.style) field.style = {};
        
        // Apply font size if set
        if (batchFontSize) {
            changes.styled[field.name].fontSize = parseFloat(batchFontSize);
            field.style.fontSize = parseFloat(batchFontSize);
            changesMade++;
        }

        // Apply font family if set
        if (batchFontFamily) {
             changes.styled[field.name].fontFamily = batchFontFamily;
             field.style.fontFamily = batchFontFamily;
             changesMade++;
        }
        
        // Apply text alignment if set
        if (batchTextAlign) {
            changes.styled[field.name].textAlign = batchTextAlign;
            field.style.textAlign = batchTextAlign;
            changesMade++;
        }
        
        // Apply font color (if color picker was interacts with, it will have a value)
        // We assume if it's not the initial value or if the user clicked it, they want to apply it
        // To be safe, we check if it's different from current OR if we want to force it
        // The most reliable way for batch is to just apply if it's present and not empty
        if (batchFontColor) {
            changes.styled[field.name].fontColor = batchFontColor;
            field.style.fontColor = batchFontColor;
            changesMade++;
        }
        
        // Apply fill color
        if (batchFillColor) {
            changes.styled[field.name].fillColor = batchFillColor;
            field.style.fillColor = batchFillColor;
            changesMade++;
        }
        
        // Apply checkbox style
        if (batchCheckboxStyle && (field.type === 'checkbox' || field.type === 'radio')) {
            changes.styled[field.name].checkboxStyle = batchCheckboxStyle;
            field.style.checkboxStyle = batchCheckboxStyle;
            changesMade++;
        }
        
        // Apply format type (only to text/dropdown fields)
        const batchFormat = document.getElementById('batchFormat')?.value;
        if (batchFormat && (field.type === 'text' || field.type === 'combobox')) {
            const finalFormat = (batchFormat === 'none') ? '' : batchFormat;
            changes.styled[field.name].formatType = finalFormat;
            field.style.formatType = finalFormat;
            field.formatType = finalFormat;  // Also set on field for detection
            
            // Only apply sub-options if it's a real format
            if (finalFormat) {
                // Apply sub-format options based on format type
                if (finalFormat === 'date') {
                const el = document.getElementById('batchDateFormat');
                const dateFormat = (el && el.value !== undefined) ? el.value : 'MM/DD/YYYY';
                changes.styled[field.name].dateFormat = dateFormat;
                field.style.dateFormat = dateFormat;
            } else if (batchFormat === 'currency') {
                const elSym = document.getElementById('batchCurrencySymbol');
                const currencySymbol = (elSym && elSym.value !== undefined) ? elSym.value : '$';
                const elDec = document.getElementById('batchCurrencyDecimals');
                const currencyDecimals = (elDec && elDec.value !== "") ? elDec.value : '2';
                const elLoc = document.getElementById('batchSymbolLocation');
                const symbolLocation = (elLoc && elLoc.value) ? elLoc.value : 'before-space';
                
                changes.styled[field.name].currencySymbol = currencySymbol;
                field.style.currencySymbol = currencySymbol;
                changes.styled[field.name].currencyDecimals = currencyDecimals;
                field.style.currencyDecimals = currencyDecimals;
                changes.styled[field.name].symbolLocation = symbolLocation;
                field.style.symbolLocation = symbolLocation;
            } else if (batchFormat === 'phone') {
                const el = document.getElementById('batchPhoneFormat');
                const phoneFormat = (el && el.value !== undefined) ? el.value : '(XXX) XXX-XXXX';
                changes.styled[field.name].phoneFormat = phoneFormat;
                field.style.phoneFormat = phoneFormat;
            } else if (batchFormat === 'number') {
                const elDec = document.getElementById('batchNumberDecimals');
                const numberDecimals = (elDec && elDec.value !== "") ? elDec.value : '0';
                const elSep = document.getElementById('batchThousandSep');
                const thousandSep = (elSep && elSep.value !== undefined) ? elSep.value : ',';
                
                changes.styled[field.name].numberDecimals = numberDecimals;
                changes.styled[field.name].thousandSep = thousandSep;
                field.style.numberDecimals = numberDecimals;
                field.style.thousandSep = thousandSep;
            } else if (batchFormat === 'zip') {
                const el = document.getElementById('batchZipFormat');
                const zipFormat = (el && el.value !== undefined) ? el.value : 'XXXXX';
                changes.styled[field.name].zipFormat = zipFormat;
                field.style.zipFormat = zipFormat;
            }
        }
            
        changesMade++;
    }
}
    
    if (changesMade > 0) {
        markUnsaved();
        
        // Flash the button to confirm
        const btn = document.getElementById('btnApplyBatch');
        btn.textContent = '✓ Applied!';
        btn.style.background = '#48c6a2';
        setTimeout(() => {
            btn.textContent = '✓ Apply to All Selected';
            btn.style.background = '';
        }, 1500);
    }
}

async function handleGroupRadio() {
    if (selectedFields.length < 2) {
        showError('Please select 2 or more checkboxes to group into radio buttons.');
        return;
    }
    
    // Check that all selected are checkboxes
    const allCheckboxes = selectedFields.every(f => f.type === 'checkbox');
    if (!allCheckboxes) {
        showError('Please select only checkboxes to group into radio buttons.');
        return;
    }
    
    // Use custom modal dialog (window.prompt doesn't work in Electron)
    const groupName = await showInputModal('Enter radio group name:', 'Radio Group');
    if (!groupName) return;
    
    saveState();  // Save state for undo
    
    changes.new_radio_groups[groupName] = selectedFields.map(f => f.name);
    
    for (let i = 0; i < selectedFields.length; i++) {
        const field = selectedFields[i];
        const oldName = field.name;
        field.type = 'radio';
        field.radio_group = groupName;
        
        // Assign unique export value (matching backend logic)
        const ev = oldName.trim().replace(/\s+/g, '_') || `Choice${i+1}`;
        if (!field.style) field.style = {};
        field.style.exportValue = ev;
        
        // Track in changes so user can skip manual edits
        if (!changes.styled[oldName]) {
            changes.styled[oldName] = {};
        }
        changes.styled[oldName].exportValue = ev;
        
        // Also track by PDF object number for safety (stable across saves)
        if (field.objgen) {
            const objKey = field.objgen.join(',');
            if (!changes.styled_by_objgen) changes.styled_by_objgen = {};
            if (!changes.styled_by_objgen[objKey]) changes.styled_by_objgen[objKey] = {};
            changes.styled_by_objgen[objKey].exportValue = ev;
            changes.styled_by_objgen[objKey]._name = field.name;
        }
    }
    
    selectedFields = [];
    updatePropertiesPanel();
    markUnsaved();
    renderCanvas();
}

function handleUngroupRadio() {
    if (selectedFields.length === 0) return;
    
    // Collect unique radio groups from selected fields
    const groupsToUngroup = new Set();
    for (const field of selectedFields) {
        if (field.type === 'radio' && field.radio_group) {
            groupsToUngroup.add(field.radio_group);
        }
    }
    
    if (groupsToUngroup.size === 0) return;
    
    saveState();
    
    // Add groups to changes for Python processing
    for (const group of groupsToUngroup) {
        if (!changes.ungroup_radios.includes(group)) {
            changes.ungroup_radios.push(group);
        }
    }
    
    // Update local field state - only fields in selected groups
    for (const f of fields) {
        if (f.radio_group && groupsToUngroup.has(f.radio_group)) {
            f.type = 'checkbox';
            delete f.radio_group;
        }
    }
    
    selectedFields = [];
    updatePropertiesPanel();
    markUnsaved();
    renderCanvas();
}

async function handleSave() {
    if (!hasUnsavedChanges) return;

    showLoading('Saving changes...');

    try {
        const result = await ipcRenderer.invoke('apply-field-changes', pdfPath, outputPath, changes);

        if (result.error) throw new Error(result.error);

        hideLoading();
        ipcRenderer.send('editor-saved', { outputPath, changes: result });

        // Reset the changes object — all changes are now committed to the PDF
        // so re-applying them on the next save would double-move / re-delete fields.
        changes = {
            deleted: [],
            renamed: {},
            moved: {},
            resized: {},
            styled: {},
            new_radio_groups: {},
            ungroup_radios: [],
            tab_order: {},
            calculations: {}
        };

        markSaved();
    } catch (error) {
        console.error('Save error:', error);
        hideLoading();
        showError(`Failed to save: ${error.message}`);
    }
}

async function handleExit() {
    if (!hasUnsavedChanges) {
        window.close();
        return;
    }
    const action = await showExitConfirm();
    if (action === 'save') {
        await handleSave();
        window.close();
    } else if (action === 'exit') {
        window.close();
    }
    // 'cancel' — stay in editor
}

// ============ Helpers ============
function markUnsaved() {
    hasUnsavedChanges = true;
    document.getElementById('changesMade').style.display = 'inline';
    document.getElementById('btnSave').disabled = false;
}

function markSaved() {
    hasUnsavedChanges = false;
    document.getElementById('changesMade').style.display = 'none';
    document.getElementById('btnSave').disabled = true;
}

function updateFieldCount() {
    document.getElementById('fieldCount').textContent = `${fields.length} fields`;
    // Also update the field list sidebar count
    const listCount = document.getElementById('fieldListCount');
    if (listCount) {
        listCount.textContent = fields.length;
    }
}

// ============ Duplicate & Nudge ============
function duplicateSelectedFields() {
    if (selectedFields.length === 0) return;
    
    const newFields = [];
    const offset = 15; // Offset for duplicated field position
    
    for (const field of selectedFields) {
        // Create a duplicate with offset position
        const newField = {
            ...field,
            rect: [
                field.rect[0] + offset,
                field.rect[1] + offset,
                field.rect[2] + offset,
                field.rect[3] + offset
            ],
            name: generateUniqueName(field.name),
            objgen: null  // Clear — original's objgen refers to a different widget
        };
        
        // Copy any complex properties
        if (field.actions) {
            newField.actions = { ...field.actions };
        }
        
        fields.push(newField);
        newFields.push(newField);
        
        // Track as a new field for saving
        if (!changes.created) changes.created = [];
        changes.created.push(newField);
        
        // Track in styled for styles (redundant but consistent)
        changes.styled[newField.name] = {
            ...changes.styled[field.name] || {},
            isNew: true,
            originalName: field.name
        };
    }
    
    // Select the new fields
    selectedFields = newFields;
    
    markUnsaved();
    updateFieldCount();
    updatePropertiesPanel();
    updateFieldList();
    renderCanvas();
}

function generateUniqueName(baseName) {
    // Remove any existing _copy suffix
    let cleanName = baseName.replace(/_copy\d*$/, '');
    
    // Find a unique name
    let counter = 1;
    let newName = `${cleanName}_copy`;
    
    while (fields.some(f => f.name === newName)) {
        counter++;
        newName = `${cleanName}_copy${counter}`;
    }
    
    return newName;
}

// ============ Copy/Paste/Cut ============
function copySelectedFields() {
    if (selectedFields.length === 0) return;
    
    // Deep copy selected fields to clipboard
    clipboard = selectedFields.map(field => ({
        ...field,
        rect: [...field.rect],
        actions: field.actions ? { ...field.actions } : undefined,
        style: field.style ? { ...field.style } : undefined
    }));
    clipboardSourcePage = currentPage;
    lastPastePosition = null;  // Reset paste position tracking
    
    console.log(`Copied ${clipboard.length} field(s) to clipboard`);
}

function cutSelectedFields() {
    if (selectedFields.length === 0) return;
    
    // Copy first, then delete
    copySelectedFields();
    handleDelete();
}

function pasteFields(inPlace = false) {
    if (clipboard.length === 0) return;
    
    const newFields = [];
    const PASTE_OFFSET = 15;  // Offset for regular paste
    
    // Calculate offset based on paste mode
    let offsetX = 0, offsetY = 0;
    
    if (inPlace) {
        // Paste in place - same position (or stacked if pasting multiple times)
        if (lastPastePosition && currentPage === clipboardSourcePage) {
            // If we've already pasted in place, stack with offset
            offsetX = lastPastePosition.count * PASTE_OFFSET;
            offsetY = lastPastePosition.count * PASTE_OFFSET;
            lastPastePosition.count++;
        } else {
            // First paste in place - exact same position
            offsetX = 0;
            offsetY = 0;
            lastPastePosition = { count: 1 };
        }
    } else {
        // Regular paste - offset from original
        offsetX = PASTE_OFFSET;
        offsetY = PASTE_OFFSET;
        lastPastePosition = null;
    }
    
    for (const clipField of clipboard) {
        // Create new field from clipboard with offset
        const newField = {
            ...clipField,
            page: currentPage,  // Paste to current page
            rect: [
                clipField.rect[0] + offsetX,
                clipField.rect[1] + offsetY,
                clipField.rect[2] + offsetX,
                clipField.rect[3] + offsetY
            ],
            name: generateUniqueName(clipField.name),
            objgen: null  // Clear — original's objgen refers to a different widget
        };
        
        // Deep copy complex properties
        if (clipField.actions) {
            newField.actions = { ...clipField.actions };
        }
        if (clipField.style) {
            newField.style = { ...clipField.style };
        }
        
        fields.push(newField);
        newFields.push(newField);
        
        // Track as a new field for saving
        if (!changes.created) changes.created = [];
        changes.created.push(newField);
        
        changes.styled[newField.name] = {
            ...changes.styled[clipField.name] || {},
            isNew: true,
            originalName: clipField.name
        };
    }
    
    // Select the pasted fields
    selectedFields = newFields;
    
    markUnsaved();
    updateFieldCount();
    updatePropertiesPanel();
    updateFieldList();
    renderCanvas();
    
    console.log(`Pasted ${newFields.length} field(s)${inPlace ? ' in place' : ''}`);
}

// ============ Context Menu ============
let contextMenu = null;

function createContextMenu() {
    // Create context menu if it doesn't exist
    if (contextMenu) return;
    
    contextMenu = document.createElement('div');
    contextMenu.className = 'context-menu';
    contextMenu.innerHTML = `
        <div class="context-menu-item" data-action="properties">Properties...</div>
        <div class="context-menu-separator"></div>
        <div class="context-menu-item" data-action="rename">Rename Field...</div>
        <div class="context-menu-item" data-action="required">Set as Required Field</div>
        <div class="context-menu-separator"></div>
        <div class="context-menu-item" data-action="cut"><span>Cut</span><span class="shortcut">⌘X</span></div>
        <div class="context-menu-item" data-action="copy"><span>Copy</span><span class="shortcut">⌘C</span></div>
        <div class="context-menu-item" data-action="paste"><span>Paste</span><span class="shortcut">⌘V</span></div>
        <div class="context-menu-item" data-action="paste-in-place"><span>Paste in Place</span><span class="shortcut">⇧⌘V</span></div>
        <div class="context-menu-item" data-action="delete"><span>Delete</span><span class="shortcut">⌫</span></div>
        <div class="context-menu-item" data-action="select-all"><span>Select All</span><span class="shortcut">⌘A</span></div>
        <div class="context-menu-separator"></div>
        <div class="context-menu-item has-submenu" data-action="align">
            Align, Distribute or Center
            <div class="context-submenu">
                <div class="context-menu-item" data-action="align-left">Align Left</div>
                <div class="context-menu-item" data-action="align-center-h">Align Center Horizontally</div>
                <div class="context-menu-item" data-action="align-right">Align Right</div>
                <div class="context-menu-separator"></div>
                <div class="context-menu-item" data-action="align-top">Align Top</div>
                <div class="context-menu-item" data-action="align-center-v">Align Center Vertically</div>
                <div class="context-menu-item" data-action="align-bottom">Align Bottom</div>
                <div class="context-menu-separator"></div>
                <div class="context-menu-item" data-action="distribute-h">Distribute Horizontally</div>
                <div class="context-menu-item" data-action="distribute-v">Distribute Vertically</div>
            </div>
        </div>
        <div class="context-menu-item has-submenu" data-action="size">
            Set Fields to Same Size
            <div class="context-submenu">
                <div class="context-menu-item" data-action="match-width">Same Width</div>
                <div class="context-menu-item" data-action="match-height">Same Height</div>
                <div class="context-menu-item" data-action="match-both">Same Width and Height</div>
            </div>
        </div>
        <div class="context-menu-separator"></div>
        <div class="context-menu-item" data-action="duplicate">Create Duplicate</div>
        <div class="context-menu-item" data-action="duplicate-to-pages">Duplicate to Other Pages...</div>
    `;
    
    document.body.appendChild(contextMenu);
    
    // Add click handlers for menu items
    contextMenu.querySelectorAll('.context-menu-item[data-action]').forEach(item => {
        item.addEventListener('click', (e) => {
            e.stopPropagation();
            const action = item.dataset.action;
            handleContextMenuAction(action);
        });
    });
    

    // Submenu boundary detection
    contextMenu.querySelectorAll('.context-menu-item.has-submenu').forEach(item => {
        item.addEventListener('mouseenter', () => {
            const submenu = item.querySelector('.context-submenu');
            if (!submenu) return;
            
            // Reset classes
            submenu.classList.remove('submenu-flip-x', 'submenu-flip-y');
            
            // Measure
            const parentRect = item.getBoundingClientRect();
            
            // Force display to measure dimensions
            submenu.style.display = 'block';
            submenu.style.visibility = 'hidden';
            const subRect = submenu.getBoundingClientRect();
            submenu.style.display = '';
            submenu.style.visibility = '';
            
            // Check Horizontal Overflow
            if (parentRect.right + subRect.width > window.innerWidth) {
                submenu.classList.add('submenu-flip-x');
            }
            
            // Check Vertical Overflow
            // If defaulting to top alignment (top: -6px), check if bottom goes off screen
            // effective top is parentRect.top - 6
            const effectiveTop = parentRect.top - 6;
            if (effectiveTop + subRect.height > window.innerHeight) {
                submenu.classList.add('submenu-flip-y');
            }
        });
    });
    
    // Close menu when clicking elsewhere
    document.addEventListener('click', hideContextMenu);
    document.addEventListener('contextmenu', (e) => {
        // Only hide if clicking outside the canvas
        if (e.target !== canvas) {
            hideContextMenu();
        }
    });
}

function showContextMenu(x, y) {
    createContextMenu();
    
    // Update menu state based on selection
    const hasSelection = selectedFields.length > 0;
    const hasMultiple = selectedFields.length >= 2;
    const hasClipboard = clipboard.length > 0;
    
    // Enable/disable items based on context
    contextMenu.querySelectorAll('.context-menu-item').forEach(item => {
        const action = item.dataset.action;
        let enabled = true;
        
        switch (action) {
            case 'properties':
            case 'rename':
            case 'required':
            case 'cut':
            case 'copy':
            case 'delete':
            case 'duplicate':
            case 'duplicate-to-pages':
                enabled = hasSelection;
                break;
            case 'paste':
            case 'paste-in-place':
                enabled = hasClipboard;
                break;
            case 'align':
            case 'size':
                enabled = hasMultiple;
                break;
            case 'align-left':
            case 'align-center-h':
            case 'align-right':
            case 'align-top':
            case 'align-center-v':
            case 'align-bottom':
            case 'distribute-h':
            case 'distribute-v':
            case 'match-width':
            case 'match-height':
            case 'match-both':
                enabled = hasMultiple;
                break;
        }
        
        item.classList.toggle('disabled', !enabled);
    });
    
    // Position the menu
    contextMenu.style.display = 'block';
    
    // Adjust position if menu would go off screen
    const menuRect = contextMenu.getBoundingClientRect();
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;
    
    let menuX = x;
    let menuY = y;
    
    // Check horizontal overflow - flip to left if needed
    if (x + menuRect.width > viewportWidth) {
        menuX = x - menuRect.width;
    }
    
    // Check vertical overflow - flip upwards if needed
    if (y + menuRect.height > viewportHeight) {
        menuY = y - menuRect.height;
    }
    
    // Final safety check to ensure it doesn't go off top/left edges
    if (menuX < 10) menuX = 10;
    if (menuY < 10) menuY = 10;
    
    contextMenu.style.left = menuX + 'px';
    contextMenu.style.top = menuY + 'px';
}

function hideContextMenu() {
    if (contextMenu) {
        contextMenu.style.display = 'none';
    }
}

function handleContextMenu(e) {
    e.preventDefault();
    
    const rect = canvas.getBoundingClientRect();
    const canvasX = e.clientX - rect.left;
    const canvasY = e.clientY - rect.top;
    
    // Check if right-clicked on a field
    const clickedField = getFieldAtPoint(canvasX, canvasY);
    
    if (clickedField) {
        // If clicked field is not in selection, select only it
        if (!selectedFields.includes(clickedField)) {
            selectedFields = [clickedField];
            updatePropertiesPanel();
            updateFieldList();
            renderCanvas();
        }
    }
    // If clicked on empty area but have selection, keep selection for paste
    // If no selection and clicked empty, that's fine too (for paste)
    
    showContextMenu(e.clientX, e.clientY);
}

function handleContextMenuAction(action) {
    hideContextMenu();
    
    switch (action) {
        case 'properties':
            if (selectedFields.length === 1) {
                openPropertiesModal(selectedFields[0]);
            } else if (selectedFields.length > 1) {
                openMultiFieldPropertiesModal(selectedFields);
            }
            break;
        case 'rename':
            if (selectedFields.length === 1) {
                renameFieldPrompt(selectedFields[0]);
            }
            break;
        case 'required':
            toggleRequiredField();
            break;
        case 'cut':
            cutSelectedFields();
            break;
        case 'copy':
            copySelectedFields();
            break;
        case 'paste':
            pasteFields(false);
            break;
        case 'paste-in-place':
            pasteFields(true);
            break;
        case 'delete':
            handleDelete();
            break;
        case 'select-all':
            const pageFields = fields.filter(f => f.page === currentPage);
            selectedFields = [...pageFields];
            updatePropertiesPanel();
            updateFieldList();
            renderCanvas();
            break;
        case 'align-left':
            alignFields('left');
            break;
        case 'align-center-h':
            alignFields('centerH');
            break;
        case 'align-right':
            alignFields('right');
            break;
        case 'align-top':
            alignFields('top');
            break;
        case 'align-center-v':
            alignFields('centerV');
            break;
        case 'align-bottom':
            alignFields('bottom');
            break;
        case 'distribute-h':
            distributeFields('horizontal');
            break;
        case 'distribute-v':
            distributeFields('vertical');
            break;
        case 'match-width':
            matchSize('width');
            break;
        case 'match-height':
            matchSize('height');
            break;
        case 'match-both':
            matchSize('both');
            break;
        case 'duplicate':
            duplicateSelectedFields();
            break;
        case 'duplicate-to-pages':
            showDuplicateToPagesDialog();
            break;
    }
}

async function renameFieldPrompt(field) {
    const newName = await showInputModal('Rename Field', field.name);
    if (newName && newName !== field.name) {
        // Check for duplicate names
        if (fields.some(f => f !== field && f.name === newName)) {
            showAlert('A field with that name already exists.', '⚠️ Duplicate Name');
            return;
        }
        
        const oldName = field.name;
        field.name = newName;
        changes.renamed[oldName] = newName;
        markUnsaved();
        updatePropertiesPanel();
        updateFieldList();
    }
}

function toggleRequiredField() {
    if (selectedFields.length === 0) return;
    
    for (const field of selectedFields) {
        field.required = !field.required;
        if (!changes.styled[field.name]) {
            changes.styled[field.name] = {};
        }
        changes.styled[field.name].required = field.required;
    }
    
    markUnsaved();
    updatePropertiesPanel();
    renderCanvas();
}

function showDuplicateToPagesDialog() {
    if (selectedFields.length === 0) return;
    
    // Simple prompt for page numbers
    const pageCount = pages.length;
    const currentPageNum = currentPage + 1;
    const promptText = `Enter page numbers to duplicate to (e.g., "2,3,4" or "2-5").\nCurrent page: ${currentPageNum}, Total pages: ${pageCount}`;
    
    showInputModal('Duplicate to Pages', '').then(input => {
        if (!input) return;
        
        // Parse page numbers
        const targetPages = parsePageNumbers(input, pageCount);
        if (targetPages.length === 0) {
            showAlert('No valid page numbers entered.', '⚠️ Invalid Input');
            return;
        }
        
        // Remove current page from targets
        const filteredTargets = targetPages.filter(p => p !== currentPage);
        if (filteredTargets.length === 0) {
            showAlert('Please enter pages other than the current page.', '⚠️ Invalid Pages');
            return;
        }
        
        // Duplicate to each target page
        let totalCreated = 0;
        for (const targetPage of filteredTargets) {
            for (const field of selectedFields) {
                const newField = {
                    ...field,
                    page: targetPage,
                    rect: [...field.rect],
                    name: generateUniqueName(field.name)
                };
                
                if (field.actions) newField.actions = { ...field.actions };
                if (field.style) newField.style = { ...field.style };
                
                fields.push(newField);
                if (!changes.created) changes.created = [];
                changes.created.push(newField);
                totalCreated++;
            }
        }
        
        markUnsaved();
        updateFieldCount();
        updateFieldList();
        showAlert(`Created ${totalCreated} field(s) across ${filteredTargets.length} page(s).`, '✅ Fields Duplicated');
    });
}

function parsePageNumbers(input, maxPage) {
    const pages = new Set();
    const parts = input.split(',');
    
    for (const part of parts) {
        const trimmed = part.trim();
        if (trimmed.includes('-')) {
            // Range like "2-5"
            const [start, end] = trimmed.split('-').map(s => parseInt(s.trim()));
            if (!isNaN(start) && !isNaN(end)) {
                for (let i = Math.max(1, start); i <= Math.min(maxPage, end); i++) {
                    pages.add(i - 1);  // Convert to 0-indexed
                }
            }
        } else {
            // Single page
            const num = parseInt(trimmed);
            if (!isNaN(num) && num >= 1 && num <= maxPage) {
                pages.add(num - 1);  // Convert to 0-indexed
            }
        }
    }
    
    return Array.from(pages).sort((a, b) => a - b);
}

function nudgeFields(direction, amount) {
    if (selectedFields.length === 0) return;
    
    // Convert amount from pixels to PDF points (accounting for zoom and DPI)
    const pdfAmount = amount / (DPI_SCALE * zoom);
    
    for (const field of selectedFields) {
        const rect = [...field.rect];
        
        switch (direction) {
            case 'ArrowUp':
                rect[1] -= pdfAmount;
                rect[3] -= pdfAmount;
                break;
            case 'ArrowDown':
                rect[1] += pdfAmount;
                rect[3] += pdfAmount;
                break;
            case 'ArrowLeft':
                rect[0] -= pdfAmount;
                rect[2] -= pdfAmount;
                break;
            case 'ArrowRight':
                rect[0] += pdfAmount;
                rect[2] += pdfAmount;
                break;
        }
        
        field.rect = rect;
        recordFieldMove(field, rect);
    }

    markUnsaved();
    renderCanvas();
}

// ============ Arrange Operations ============
function alignFields(direction) {
    if (selectedFields.length < 2) return;
    
    // Get bounds of first selected field as reference
    const ref = selectedFields[0];
    const refLeft = ref.rect[0];
    const refRight = ref.rect[2];
    const refTop = ref.rect[1];
    const refBottom = ref.rect[3];
    const refCenterX = (refLeft + refRight) / 2;
    const refCenterY = (refTop + refBottom) / 2;
    
    for (let i = 1; i < selectedFields.length; i++) {
        const field = selectedFields[i];
        const width = field.rect[2] - field.rect[0];
        const height = field.rect[3] - field.rect[1];
        
        switch (direction) {
            case 'left':
                field.rect[0] = refLeft;
                field.rect[2] = refLeft + width;
                break;
            case 'right':
                field.rect[2] = refRight;
                field.rect[0] = refRight - width;
                break;
            case 'centerH':
                const fieldCenterX = (field.rect[0] + field.rect[2]) / 2;
                const offsetX = refCenterX - fieldCenterX;
                field.rect[0] += offsetX;
                field.rect[2] += offsetX;
                break;
            case 'top':
                field.rect[1] = refTop;
                field.rect[3] = refTop + height;
                break;
            case 'bottom':
                field.rect[3] = refBottom;
                field.rect[1] = refBottom - height;
                break;
            case 'centerV':
                const fieldCenterY = (field.rect[1] + field.rect[3]) / 2;
                const offsetY = refCenterY - fieldCenterY;
                field.rect[1] += offsetY;
                field.rect[3] += offsetY;
                break;
        }
        
        recordFieldMove(field, field.rect);
    }

    markUnsaved();
    renderCanvas();
}

function matchSize(dimension) {
    if (selectedFields.length < 2) return;
    
    // Get size of first selected field as reference
    const ref = selectedFields[0];
    const refWidth = ref.rect[2] - ref.rect[0];
    const refHeight = ref.rect[3] - ref.rect[1];
    
    for (let i = 1; i < selectedFields.length; i++) {
        const field = selectedFields[i];
        
        if (dimension === 'width' || dimension === 'both') {
            field.rect[2] = field.rect[0] + refWidth;
        }
        if (dimension === 'height' || dimension === 'both') {
            field.rect[3] = field.rect[1] + refHeight;
        }
        
        recordFieldResize(field, field.rect);
    }

    markUnsaved();
    renderCanvas();
}

function distributeFields(direction) {
    if (selectedFields.length < 3) return;
    
    // Sort fields by position
    const sorted = [...selectedFields].sort((a, b) => {
        return direction === 'horizontal' 
            ? a.rect[0] - b.rect[0] 
            : a.rect[1] - b.rect[1];
    });
    
    // Get bounding box of first and last
    const first = sorted[0];
    const last = sorted[sorted.length - 1];
    
    if (direction === 'horizontal') {
        const startX = first.rect[0];
        const endX = last.rect[0];
        const totalGap = endX - startX;
        const step = totalGap / (sorted.length - 1);
        
        for (let i = 1; i < sorted.length - 1; i++) {
            const field = sorted[i];
            const width = field.rect[2] - field.rect[0];
            field.rect[0] = startX + step * i;
            field.rect[2] = field.rect[0] + width;
            recordFieldMove(field, field.rect);
        }
    } else {
        const startY = first.rect[1];
        const endY = last.rect[1];
        const totalGap = endY - startY;
        const step = totalGap / (sorted.length - 1);

        for (let i = 1; i < sorted.length - 1; i++) {
            const field = sorted[i];
            const height = field.rect[3] - field.rect[1];
            field.rect[1] = startY + step * i;
            field.rect[3] = field.rect[1] + height;
            recordFieldMove(field, field.rect);
        }
    }
    
    markUnsaved();
    renderCanvas();
}

// ============ Field List Sidebar ============
let fieldListSearchTerm = '';
let fieldSortOrder = 'default';  // 'default', 'alphabetic', 'reverse-alpha', 'tab'
let highlightFieldsEnabled = true;
let showTabNumbers = false;
/// v1.1.0: View mode + radio group state
let viewMode = 'single';           // 'single' | 'scroll'
let collapsedGroups = new Set();   // radio group names that are collapsed in the field list
let scrollPageImages = {};         // (legacy, unused)
let singleViewZoom  = 1.0;         // zoom saved when switching to scroll so it can be restored
// Scroll view drag state
let svDragField    = null;
let svDragPageWrap = null;
let svDragStart    = null;          // { clientX, clientY, rect: [...] }

function updateFieldList() {
    const container = document.getElementById('fieldListContainer');
    if (!container) return;

    // In scroll mode show all fields; otherwise filter to current page
    let pageFields = viewMode === 'scroll'
        ? [...fields]
        : fields.filter(f => f.page === currentPage);

    // Apply sorting
    if (fieldSortOrder === 'alphabetic') {
        pageFields.sort((a, b) => a.name.localeCompare(b.name));
    } else if (fieldSortOrder === 'reverse-alpha') {
        pageFields.sort((a, b) => b.name.localeCompare(a.name));
    } else if (fieldSortOrder === 'tab') {
        pageFields.sort((a, b) => (a.tabindex || 9999) - (b.tabindex || 9999));
    }

    // Apply search filter
    const searchTerm = fieldListSearchTerm.toLowerCase();
    const filteredFields = searchTerm
        ? pageFields.filter(f => f.name.toLowerCase().includes(searchTerm))
        : pageFields;

    // ---- Build HTML, grouping radio buttons under collapsible headers ----
    let html = '';
    if (filteredFields.length === 0) {
        html = `<div style="padding:20px;text-align:center;color:var(--text-secondary);font-size:11px;">
            ${searchTerm ? 'No fields match your search' : 'No fields on this page'}
        </div>`;
    } else {
        // Partition into radio groups and standalone (non-radio) fields,
        // preserving original order within each category.
        const renderedGroups = new Set();
        let listIndex = 0;

        filteredFields.forEach((field) => {
            if (field.type === 'radio' && field.radio_group) {
                const grp = field.radio_group;
                if (renderedGroups.has(grp)) return; // already emitted this group
                renderedGroups.add(grp);

                // All members of this group that are visible after filtering
                const members = filteredFields.filter(
                    f => f.type === 'radio' && f.radio_group === grp
                );
                const isCollapsed = collapsedGroups.has(grp);
                const anySelected = members.some(f => selectedFields.includes(f));
                const safeGrp = grp.replace(/"/g, '&quot;');

                html += `
                <div class="radio-group-header ${anySelected ? 'group-has-selected' : ''}"
                     data-group-name="${safeGrp}">
                    <span class="group-chevron">${isCollapsed ? '▶' : '▼'}</span>
                    <span class="group-radio-icon">🔘</span>
                    <div class="group-label-wrap">
                        <div class="group-label-name">${grp}</div>
                        <div class="group-label-meta">Radio Group · ${members.length} option${members.length !== 1 ? 's' : ''}</div>
                    </div>
                </div>`;

                if (!isCollapsed) {
                    members.forEach(m => {
                        const isSelected = selectedFields.includes(m);
                        const tabNum = showTabNumbers ? `<span class="tab-number">${++listIndex}</span>` : '';
                        const pageTag = `<span class="field-page-badge">p${m.page + 1}</span>`;
                        html += `
                        <div class="field-list-item radio-group-child ${isSelected ? 'selected' : ''}"
                             data-field-name="${m.name.replace(/"/g, '&quot;')}"
                             data-field-uid="${fieldUID(m).replace(/"/g, '&quot;')}"
                             title="${m.name}"
                             draggable="true">
                            ${tabNum}
                            <span class="field-icon">🔘</span>
                            <div class="field-info">
                                <div class="field-name">${m.name}</div>
                                <div class="field-meta">Radio${pageTag}</div>
                            </div>
                        </div>`;
                    });
                }
            } else {
                // Regular (non-radio) field
                const isSelected = selectedFields.includes(field);
                const icon = getFieldIcon(field.type);
                const typeName = getTypeLabel(field.type);
                const tabNum = showTabNumbers ? `<span class="tab-number">${++listIndex}</span>` : '';
                const pageTag = `<span class="field-page-badge">p${field.page + 1}</span>`;
                html += `
                <div class="field-list-item ${isSelected ? 'selected' : ''}"
                     data-field-name="${field.name.replace(/"/g, '&quot;')}"
                     data-field-uid="${fieldUID(field).replace(/"/g, '&quot;')}"
                     title="${field.name}"
                     draggable="true">
                    ${tabNum}
                    <span class="field-icon">${icon}</span>
                    <div class="field-info">
                        <div class="field-name">${field.name}</div>
                        <div class="field-meta">${typeName}${pageTag}</div>
                    </div>
                </div>`;
            }
        });
    }

    container.innerHTML = html;

    // Update count
    const listCount = document.getElementById('fieldListCount');
    if (listCount) listCount.textContent = pageFields.length;

    // Auto-scroll the field list so the (first) selected field-list-item is visible.
    // In scroll view especially, the list contains all pages — the highlighted item
    // may otherwise sit off-screen making the selection look like it "didn't happen".
    if (selectedFields.length > 0) {
        const firstUid = fieldUID(selectedFields[0]);
        const el = container.querySelector(
            `.field-list-item[data-field-uid="${CSS.escape(firstUid)}"]`
        );
        if (el) {
            const cRect = container.getBoundingClientRect();
            const eRect = el.getBoundingClientRect();
            // Only scroll if the item isn't fully visible in the list viewport
            if (eRect.top < cRect.top || eRect.bottom > cRect.bottom) {
                const offset = eRect.top - cRect.top + container.scrollTop
                               - container.clientHeight / 2 + el.offsetHeight / 2;
                container.scrollTo({ top: Math.max(0, offset), behavior: 'smooth' });
            }
        }
    }

    // ---- Radio group header click → toggle collapse ----
    container.querySelectorAll('.radio-group-header').forEach(header => {
        header.addEventListener('click', () => {
            const grp = header.dataset.groupName;
            if (collapsedGroups.has(grp)) {
                collapsedGroups.delete(grp);
            } else {
                collapsedGroups.add(grp);
            }
            updateFieldList();
        });
    });

    // ---- Field item click handlers ----
    container.querySelectorAll('.field-list-item').forEach(item => {
        item.addEventListener('click', (e) => {
            const uid = item.dataset.fieldUid;
            const field = uid
                ? fields.find(f => fieldUID(f) === uid)
                : fields.find(f => f.name === item.dataset.fieldName);
            if (!field) return;

            // Navigate to field's page if different
            if (field.page !== currentPage) {
                if (viewMode === 'scroll') {
                    // Don't rebuild the canvas — just track page + scroll the scroll-view
                    currentPage = field.page;
                    scrollToPageInScrollView(field.page);
                    updateActiveThumbnail();
                } else {
                    loadPage(field.page);
                }
            }

            // Handle multi-select with shift/cmd
            if (e.shiftKey || e.metaKey) {
                const index = selectedFields.indexOf(field);
                if (index >= 0) {
                    selectedFields.splice(index, 1);
                } else {
                    selectedFields.push(field);
                }
            } else {
                selectedFields = [field];
            }

            updatePropertiesPanel();
            updateFieldList();
            renderCanvas();

            // Scroll canvas to show selected field (center it if possible)
            scrollToField(field);
        });
        
        // Drag and drop handlers for reordering.
        // Uses field UIDs (not names) so radio group members — which share a
        // name — can be individually reordered.
        item.addEventListener('dragstart', (e) => {
            const uid = item.dataset.fieldUid;
            const field = fields.find(f => fieldUID(f) === uid);

            // Check if we are dragging a selected item as part of a multi-selection
            if (field && selectedFields.includes(field) && selectedFields.length > 1) {
                const uids = selectedFields.map(f => fieldUID(f)).join('\u0001');
                e.dataTransfer.setData('text/plain', 'MULTI_UID:' + uids);

                selectedFields.forEach(f => {
                    const el = container.querySelector(
                        `.field-list-item[data-field-uid="${CSS.escape(fieldUID(f))}"]`
                    );
                    if (el) el.classList.add('dragging');
                });
            } else {
                e.dataTransfer.setData('text/plain', 'UID:' + uid);
                item.classList.add('dragging');
            }
        });
        
        item.addEventListener('dragend', () => {
            // Remove dragging + drop-indicator classes from all items
            container.querySelectorAll('.field-list-item').forEach(el => {
                el.classList.remove('dragging', 'drop-before', 'drop-after');
            });
        });

        item.addEventListener('dragover', (e) => {
            e.preventDefault();
            // Determine whether cursor is in the top or bottom half of the item
            // and show a green insertion line on that edge instead of a blanket
            // highlight — makes the drop target unambiguous.
            const rect = item.getBoundingClientRect();
            const before = (e.clientY - rect.top) < rect.height / 2;
            // Clear indicators on siblings so only this item shows the line
            container.querySelectorAll('.field-list-item.drop-before, .field-list-item.drop-after')
                .forEach(el => { if (el !== item) el.classList.remove('drop-before', 'drop-after'); });
            item.classList.toggle('drop-before',  before);
            item.classList.toggle('drop-after',  !before);
        });

        item.addEventListener('dragleave', (e) => {
            // Only clear when the cursor actually leaves the item (not when it
            // moves between child elements, which also fires dragleave).
            if (!item.contains(e.relatedTarget)) {
                item.classList.remove('drop-before', 'drop-after');
            }
        });
        
        item.addEventListener('drop', (e) => {
            e.preventDefault();
            // Insert BEFORE or AFTER this item based on which green line was showing
            const dropAfter = item.classList.contains('drop-after');
            item.classList.remove('drop-before', 'drop-after');

            const data = e.dataTransfer.getData('text/plain');
            const targetUid = item.dataset.fieldUid;

            if (data.startsWith('MULTI_UID:')) {
                const draggedUids = data.substring(10).split('\u0001');
                if (draggedUids.includes(targetUid)) return;

                const uidSet  = new Set(draggedUids);
                const moveObjects = fields
                    .map((f, i) => ({ f, i, uid: fieldUID(f) }))
                    .filter(x => uidSet.has(x.uid))
                    .sort((a, b) => a.i - b.i)
                    .map(x => x.f);

                const newFields = fields.filter(f => !uidSet.has(fieldUID(f)));
                const tgtIdx = newFields.findIndex(f => fieldUID(f) === targetUid);

                if (tgtIdx !== -1) {
                    const insertAt = dropAfter ? tgtIdx + 1 : tgtIdx;
                    newFields.splice(insertAt, 0, ...moveObjects);
                    fields.length = 0;
                    fields.push(...newFields);
                    markUnsaved();
                    updateFieldList();
                    renderCanvas();
                }

            } else if (data.startsWith('UID:')) {
                const draggedUid = data.substring(4);
                if (draggedUid === targetUid) return;

                const draggedIdx = fields.findIndex(f => fieldUID(f) === draggedUid);
                if (draggedIdx === -1) return;

                const [draggedField] = fields.splice(draggedIdx, 1);
                // Re-locate target after splice, then apply before/after offset
                const tgtIdx = fields.findIndex(f => fieldUID(f) === targetUid);
                if (tgtIdx === -1) {
                    // Target vanished (shouldn't happen) — put it back where it was
                    fields.splice(draggedIdx, 0, draggedField);
                    return;
                }
                const insertAt = dropAfter ? tgtIdx + 1 : tgtIdx;
                fields.splice(insertAt, 0, draggedField);
                markUnsaved();
                updateFieldList();
                renderCanvas();
            }
        });
    });
}

function getFieldIcon(type) {
    const icons = {
        'text': '📝',
        'checkbox': '☑️',
        'radio': '🔘',
        'dropdown': '📋',
        'combobox': '📋',
        'signature': '✍️',
        'button': '🔲',
        'listbox': '📜',
        'link': '🔗'
    };
    return icons[type] || '📄';
}

function setupFieldListListeners() {
    // Search input filter
    const searchInput = document.getElementById('fieldListSearch');
    if (searchInput) {
        searchInput.addEventListener('input', (e) => {
            fieldListSearchTerm = e.target.value;
            updateFieldList();
        });
    }
    
    // Sort order dropdown
    const sortSelect = document.getElementById('fieldSortOrder');
    if (sortSelect) {
        sortSelect.addEventListener('change', (e) => {
            const mode = e.target.value;
            if (mode === 'manual' || mode === 'unspecified') {
                fieldSortOrder = 'default'; // Keep manual/default view
                 // Typically 'manual' implies we just let the user drag. 
                 // We don't reorder the array.
                 updateFieldList();
            } else {
                applyTabOrder(mode);
            }
        });
    }
    
    // Highlight fields toggle
    const highlightToggle = document.getElementById('highlightFields');
    if (highlightToggle) {
        highlightToggle.addEventListener('change', (e) => {
            highlightFieldsEnabled = e.target.checked;
            renderCanvas();
        });
    }
    
    // Show tab numbers toggle
    const tabNumbersToggle = document.getElementById('showTabNumbers');
    if (tabNumbersToggle) {
        tabNumbersToggle.addEventListener('change', (e) => {
            showTabNumbers = e.target.checked;
            updateFieldList();
            renderCanvas();  // Also updates canvas to show tab numbers
        });
    }
}

function scrollToField(field) {
    if (!field || !field.rect) return;
    
    const wrapper = document.getElementById('canvasWrapper');
    if (!wrapper) return;
    
    // Calculate field center position in canvas coordinates
    const fieldCenterX = (field.rect[0] + field.rect[2]) / 2 * zoom * DPI_SCALE;
    const fieldCenterY = (field.rect[1] + field.rect[3]) / 2 * zoom * DPI_SCALE;
    
    // Calculate scroll position to center the field in the viewport
    const scrollLeft = fieldCenterX - wrapper.clientWidth / 2;
    const scrollTop = fieldCenterY - wrapper.clientHeight / 2;
    
    // Smooth scroll to the field
    wrapper.scrollTo({
        left: Math.max(0, scrollLeft),
        top: Math.max(0, scrollTop),
        behavior: 'smooth'
    });
}

function showLoading(text) {
    document.getElementById('loadingText').textContent = text;
    document.getElementById('loadingOverlay').style.display = 'flex';
}

function hideLoading() {
    document.getElementById('loadingOverlay').style.display = 'none';
}

function showError(message) {
    hideLoading();
    showAlert(message, '❌ Error');
}

function showTooltip(x, y, text) {
    const tooltip = document.getElementById('tooltip');
    tooltip.textContent = text;
    tooltip.style.left = (x + 15) + 'px';
    tooltip.style.top = (y + 15) + 'px';
    tooltip.style.display = 'block';
}

function hideTooltip() {
    document.getElementById('tooltip').style.display = 'none';
}

// ============ Undo/Redo System ============

// Save current state before making changes
function saveState() {
    // Deep copy fields array and their rects
    const state = {
        fields: fields.map(f => ({
            ...f,
            rect: [...f.rect]
        })),
        changes: JSON.parse(JSON.stringify(changes)),
        selectedFieldNames: selectedFields.map(f => f.name)
    };
    
    undoStack.push(state);
    if (undoStack.length > MAX_UNDO) {
        undoStack.shift();
    }
    
    // Clear redo stack when new action is performed
    redoStack = [];
}

// Undo last action
function undo() {
    if (undoStack.length === 0) return;
    
    // Save current state to redo stack
    const currentState = {
        fields: fields.map(f => ({ ...f, rect: [...f.rect] })),
        changes: JSON.parse(JSON.stringify(changes)),
        selectedFieldNames: selectedFields.map(f => f.name)
    };
    redoStack.push(currentState);
    
    // Restore previous state
    const prevState = undoStack.pop();
    fields = prevState.fields;
    changes = prevState.changes;
    
    // Restore selection
    selectedFields = fields.filter(f => prevState.selectedFieldNames.includes(f.name));
    
    // Update UI
    updateFieldCount();
    updatePropertiesPanel();
    renderCanvas();
    
    // Update unsaved indicator
    if (undoStack.length === 0) {
        hasUnsavedChanges = false;
        document.getElementById('changesMade').style.display = 'none';
    }
}

// Redo last undone action
function redo() {
    if (redoStack.length === 0) return;
    
    // Save current state to undo stack
    const currentState = {
        fields: fields.map(f => ({ ...f, rect: [...f.rect] })),
        changes: JSON.parse(JSON.stringify(changes)),
        selectedFieldNames: selectedFields.map(f => f.name)
    };
    undoStack.push(currentState);
    
    // Restore next state
    const nextState = redoStack.pop();
    fields = nextState.fields;
    changes = nextState.changes;
    
    // Restore selection
    selectedFields = fields.filter(f => nextState.selectedFieldNames.includes(f.name));
    
    // Update UI
    hasUnsavedChanges = true;
    document.getElementById('changesMade').style.display = 'inline';
    updateFieldCount();
    updatePropertiesPanel();
    renderCanvas();
}

// ============ Properties Modal ============
let modalField = null;
let modalOriginalRect = null;
let isMultiFieldEdit = false;
let multiFieldSelection = [];

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

function closePropertiesModal() {
    const panel = document.getElementById('propertiesModal');
    panel.style.display = 'none';
    panel.classList.remove('minimized');
    modalField = null;
    isMultiFieldEdit = false;
    multiFieldSelection = [];

    // Turn off pick mode
    disablePickMode();

    // Reset tabs visibility
    document.querySelectorAll('.modal-tab').forEach(tab => tab.style.display = 'block');
}

function disablePickMode() {
    if (!pickMode) return;
    const btn = document.getElementById(pickMode.btnId);
    if (btn) {
        btn.classList.remove('active');
        btn.textContent = btn.dataset.defaultText || '🎯 Pick from Canvas';
    }
    if (pickMode.onDisable) pickMode.onDisable();
    pickMode = null;
    if (canvas) canvas.classList.remove('canvas-pick-mode');
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

// Core save logic — applies all form values to the field(s)
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

// Silent save — used when switching fields while panel is open
// Note: Does NOT call updatePropertiesPanel() to avoid recursion
function savePropertiesModalSilent() {
    if (!modalField) return;
    _applyPropertiesFromForm();
    renderCanvas();
}

// Explicit Save button — saves and keeps panel open
function savePropertiesModal() {
    if (!modalField) return;
    _applyPropertiesFromForm();
    updatePropertiesPanel();
    updateFieldList();
    renderCanvas();
}

// ============ Actions Tab Functions ============
let modalFieldActions = {};

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

// Edit an existing action - loads it into the editor
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

// Update openPropertiesModal to load existing actions and calculations
const originalOpenPropertiesModal = openPropertiesModal;
openPropertiesModal = function(field) {
    // Store pre-modal selection for "Add Selected Fields from Canvas" button
    window._preModalSelection = selectedFields ? [...selectedFields] : [];

    // Load existing actions
    modalFieldActions = field.actions ? { ...field.actions } : {};

    // Call original
    originalOpenPropertiesModal(field);

    // Update Actions tab display
    updateActionsListDisplay();

    // Load calculation data into Calculate tab
    loadCalcToModal(field);

};

// ============ Preview Mode ============
function togglePreviewMode() {
    isPreviewMode = !isPreviewMode;
    const btn = document.getElementById('btnPreview');
    const overlay = document.getElementById('previewOverlay');

    if (isPreviewMode) {
        btn.classList.add('active');
        btn.innerHTML = '✋ Exit Preview';

        selectedFields = [];
        updatePropertiesPanel();

        if (viewMode === 'scroll') {
            // Scroll view: hide field overlays, inject per-page preview layers
            document.querySelectorAll('.sv-field-overlay').forEach(el => {
                el.style.pointerEvents = 'none';
                el.style.opacity = '0';
            });
            renderScrollPreviewInputs();
        } else {
            canvas.style.pointerEvents = 'none';
            renderPreviewInputs();
            overlay.style.display = 'block';
        }
        renderCanvas();
    } else {
        btn.classList.remove('active');
        btn.innerHTML = '👁️ Preview';

        if (viewMode === 'scroll') {
            // Remove all scroll preview layers and restore overlays
            document.querySelectorAll('.sv-preview-layer').forEach(el => el.remove());
            document.querySelectorAll('.sv-field-overlay').forEach(el => {
                el.style.pointerEvents = '';
                el.style.opacity = '';
            });
        } else {
            canvas.style.pointerEvents = 'auto';
            overlay.innerHTML = '';
            overlay.style.display = 'none';
        }
        renderCanvas();
    }
}

function renderPreviewInputs() {
    const overlay = document.getElementById('previewOverlay');
    overlay.innerHTML = '';
    
    // Size and position overlay to match canvas
    overlay.style.width = canvas.width + 'px';
    overlay.style.height = canvas.height + 'px';
    overlay.style.left = canvas.offsetLeft + 'px';
    overlay.style.top = canvas.offsetTop + 'px';
    
    const pageFields = fields.filter(f => f.page === currentPage);
    
    for (const field of pageFields) {
        const x = field.rect[0] * DPI_SCALE * zoom;
        const y = field.rect[1] * DPI_SCALE * zoom;
        const w = (field.rect[2] - field.rect[0]) * DPI_SCALE * zoom;
        const h = (field.rect[3] - field.rect[1]) * DPI_SCALE * zoom;
        
        let el;
        
        // Get font styling properties - check in order: style (user set), font_size (PDF), fontSize, default
        const rawFontSize = field.style?.fontSize || field.font_size || field.fontSize || 9;
        const fontSize = rawFontSize * zoom;
        const fontColor = field.style?.fontColor || field.text_color || field.fontColor || '#000000';
        const fontFamily = field.style?.fontFamily || field.fontFamily || 'Helvetica, Arial, sans-serif';
        const fillColor = field.style?.fillColor || field.fillColor || 'transparent';
        const fillOpacity = (field.style?.fillOpacity !== undefined ? field.style.fillOpacity : 100) / 100;
        
        if (field.type === 'checkbox' || field.type === 'Checkbox') {
            el = document.createElement('input');
            el.type = 'checkbox';
            el.className = 'preview-checkbox';
            // Use appropriate checkbox size (square, max 16px scaled by zoom)
            const cbSize = Math.min(w, h, 16 * zoom);
            el.style.left = (x + (w - cbSize) / 2) + 'px';  // Center horizontally
            el.style.top = (y + (h - cbSize) / 2) + 'px';   // Center vertically
            el.style.width = cbSize + 'px';
            el.style.height = cbSize + 'px';
        } else if (field.type === 'radio' || field.type === 'Radio') {
            el = document.createElement('input');
            el.type = 'radio';
            el.name = field.radioGroup || field.name;
            el.className = 'preview-checkbox';
            // Use appropriate radio size (square, max 16px scaled by zoom)
            const rbSize = Math.min(w, h, 16 * zoom);
            el.style.left = (x + (w - rbSize) / 2) + 'px';  // Center horizontally
            el.style.top = (y + (h - rbSize) / 2) + 'px';   // Center vertically
            el.style.width = rbSize + 'px';
            el.style.height = rbSize + 'px';
        } else if (field.type === 'dropdown' || field.type === 'Dropdown') {
            el = document.createElement('select');
            el.className = 'preview-input';
            el.style.left = x + 'px';
            el.style.top = y + 'px';
            el.style.width = w + 'px';
            el.style.height = h + 'px';
            el.style.fontSize = fontSize + 'px';
            el.style.fontFamily = fontFamily;
            el.style.color = fontColor;
            el.innerHTML = '<option>Option 1</option><option>Option 2</option><option>Option 3</option>';
        } else if (field.formatType === 'date') {
            // Date field - use HTML5 date picker
            el = document.createElement('input');
            el.type = 'date';
            el.className = 'preview-input preview-date';
            el.style.left = x + 'px';
            el.style.top = y + 'px';
            el.style.width = w + 'px';
            el.style.height = h + 'px';
            el.style.fontSize = fontSize + 'px';
            el.style.fontFamily = fontFamily;
            el.style.color = fontColor;
            // Set today's date as default
            el.valueAsDate = new Date();
        } else if (field.formatType === 'phone') {
            // Phone field - add placeholder with format hint
            el = document.createElement('input');
            el.type = 'tel';
            el.className = 'preview-input preview-phone';
            el.placeholder = field.phoneFormat === 'intl' ? '+1 (___) ___-____' : '(___) ___-____';
            el.style.left = x + 'px';
            el.style.top = y + 'px';
            el.style.width = w + 'px';
            el.style.height = h + 'px';
            el.style.fontSize = fontSize + 'px';
            el.style.fontFamily = fontFamily;
            el.style.color = fontColor;
            // Add input mask behavior
            el.addEventListener('input', (e) => {
                let val = e.target.value.replace(/\D/g, '');
                if (val.length >= 10) {
                    e.target.value = `(${val.slice(0,3)}) ${val.slice(3,6)}-${val.slice(6,10)}`;
                } else if (val.length >= 6) {
                    e.target.value = `(${val.slice(0,3)}) ${val.slice(3,6)}-${val.slice(6)}`;
                } else if (val.length >= 3) {
                    e.target.value = `(${val.slice(0,3)}) ${val.slice(3)}`;
                }
            });
        } else if (field.formatType === 'currency') {
            // Currency field - add symbol and formatting
            el = document.createElement('input');
            el.type = 'text';
            el.className = 'preview-input preview-currency';
            const symbol = field.currencySymbol !== undefined ? field.currencySymbol : '$';
            el.placeholder = `${symbol}0.00`;
            el.style.left = x + 'px';
            el.style.top = y + 'px';
            el.style.width = w + 'px';
            el.style.height = h + 'px';
            el.style.fontSize = fontSize + 'px';
            el.style.fontFamily = fontFamily;
            el.style.color = fontColor;
            el.style.textAlign = 'right';
            // Format on blur
            el.addEventListener('blur', (e) => {
                const val = parseFloat(e.target.value.replace(/[^0-9.-]/g, ''));
                if (!isNaN(val)) {
                    const decimals = field.currencyDecimals !== undefined ? field.currencyDecimals : 2;
                    e.target.value = symbol + val.toFixed(decimals);
                }
            });
        } else if (field.formatType === 'number') {
            // Number field
            el = document.createElement('input');
            el.type = 'number';
            el.className = 'preview-input preview-number';
            el.step = field.numberDecimals > 0 ? Math.pow(10, -field.numberDecimals) : 1;
            el.style.left = x + 'px';
            el.style.top = y + 'px';
            el.style.width = w + 'px';
            el.style.height = h + 'px';
            el.style.fontSize = fontSize + 'px';
            el.style.fontFamily = fontFamily;
            el.style.color = fontColor;
        } else if (field.formatType === 'zip') {
            // Zip code field
            el = document.createElement('input');
            el.type = 'text';
            el.className = 'preview-input preview-zip';
            el.placeholder = field.zipFormat === 'zip4' ? '_____-____' : '_____';
            el.maxLength = field.zipFormat === 'zip4' ? 10 : 5;
            el.style.left = x + 'px';
            el.style.top = y + 'px';
            el.style.width = w + 'px';
            el.style.height = h + 'px';
            el.style.fontSize = fontSize + 'px';
            el.style.fontFamily = fontFamily;
            el.style.color = fontColor;
        } else {
            // Regular text field
            el = document.createElement('input');
            el.type = field.password ? 'password' : 'text';
            el.className = 'preview-input';
            el.placeholder = field.tooltip || field.name;
            el.style.left = x + 'px';
            el.style.top = y + 'px';
            el.style.width = w + 'px';
            el.style.height = h + 'px';
            el.style.fontSize = fontSize + 'px';
            el.style.fontFamily = fontFamily;
            el.style.color = fontColor;
            
            if (field.textAlign) {
                el.style.textAlign = field.textAlign;
            }
            if (field.maxLen && field.maxLen > 0) {
                el.maxLength = field.maxLen;
            }
            if (field.readOnly) {
                el.readOnly = true;
                el.style.background = '#f0f0f0';
            }
            if (field.defaultValue) {
                el.value = field.defaultValue;
            }
        }
        
        // Apply fill color with opacity to all inputs (not checkboxes/radios)
        if (el && field.type !== 'checkbox' && field.type !== 'Checkbox' && 
            field.type !== 'radio' && field.type !== 'Radio') {
            if (fillColor && fillColor !== 'transparent') {
                // Convert hex to rgba with opacity
                const r = parseInt(fillColor.slice(1,3), 16);
                const g = parseInt(fillColor.slice(3,5), 16);
                const b = parseInt(fillColor.slice(5,7), 16);
                el.style.backgroundColor = `rgba(${r}, ${g}, ${b}, ${fillOpacity})`;
            }
        }
        
        // v51.1d: Respect displayTooltips setting for preview mode HTML title tooltips
        const previewTooltips = window.globalFieldSettings ? window.globalFieldSettings.displayTooltips !== false : true;
        if (previewTooltips) {
            el.title = field.name + (field.formatType ? ` (${field.formatType})` : '');
        }
        overlay.appendChild(el);
    }
}

/** Scroll-view preview: inject interactive inputs into each page wrap.
 *  Uses the same percentage-based positioning as svPositionOverlay so inputs
 *  scale correctly at any zoom level, matching the overlay system exactly. */
function renderScrollPreviewInputs() {
    // Remove any stale layers first
    document.querySelectorAll('.sv-preview-layer').forEach(el => el.remove());

    const container = document.getElementById('scrollViewContainer');
    if (!container) return;

    // Group fields by page
    const byPage = new Map();
    fields.forEach(f => {
        if (!byPage.has(f.page)) byPage.set(f.page, []);
        byPage.get(f.page).push(f);
    });

    container.querySelectorAll('.sv-page-wrap').forEach(wrap => {
        const pageIdx = parseInt(wrap.dataset.pageIdx ?? -1);
        const pageFields = byPage.get(pageIdx) || [];
        if (!pageFields.length) return;

        const { iw, ih } = svGetWrapImgDims(wrap);
        if (!iw || !ih) return;

        const layer = document.createElement('div');
        layer.className = 'sv-preview-layer';
        wrap.appendChild(layer);

        const wrapW = wrap.clientWidth;
        const scale = wrapW / iw;   // px per image-pixel → used for font sizing

        pageFields.forEach(field => {
            const [x1, y1, x2, y2] = field.rect;
            const px1 = x1 * DPI_SCALE, py1 = y1 * DPI_SCALE;
            const pw  = (x2 - x1) * DPI_SCALE, ph = (y2 - y1) * DPI_SCALE;

            // Percentage coords relative to image size
            const leftPct   = (px1 / iw * 100).toFixed(4) + '%';
            const topPct    = (py1 / ih * 100).toFixed(4) + '%';
            const widthPct  = (pw  / iw * 100).toFixed(4) + '%';
            const heightPct = (ph  / ih * 100).toFixed(4) + '%';

            const rawFontSize = field.style?.fontSize || field.font_size || field.fontSize || 9;
            const fontSize    = rawFontSize * scale;
            const fontColor   = field.style?.fontColor  || field.text_color  || '#000000';
            const fontFamily  = field.style?.fontFamily || field.fontFamily  || 'Helvetica, Arial, sans-serif';
            const fillColor   = field.style?.fillColor  || field.fillColor   || 'transparent';
            const fillOpacity = (field.style?.fillOpacity !== undefined ? field.style.fillOpacity : 100) / 100;

            let el;
            const pos = () => {
                el.style.left = leftPct; el.style.top = topPct;
                el.style.width = widthPct; el.style.height = heightPct;
            };

            if (field.type === 'checkbox' || field.type === 'Checkbox') {
                el = document.createElement('input');
                el.type = 'checkbox';
                el.className = 'preview-checkbox';
                const cbSize = Math.min(pw * scale, ph * scale, 16 * scale);
                el.style.left   = ((px1 + (pw - cbSize / scale) / 2) / iw * 100).toFixed(4) + '%';
                el.style.top    = ((py1 + (ph - cbSize / scale) / 2) / ih * 100).toFixed(4) + '%';
                el.style.width  = (cbSize / wrapW * 100).toFixed(4) + '%';
                el.style.height = (cbSize / (wrap.clientHeight || (wrapW * ih / iw)) * 100).toFixed(4) + '%';
            } else if (field.type === 'radio' || field.type === 'Radio') {
                el = document.createElement('input');
                el.type = 'radio';
                el.name = field.radio_group || field.name;
                el.className = 'preview-checkbox';
                const rbSize = Math.min(pw * scale, ph * scale, 16 * scale);
                el.style.left   = ((px1 + (pw - rbSize / scale) / 2) / iw * 100).toFixed(4) + '%';
                el.style.top    = ((py1 + (ph - rbSize / scale) / 2) / ih * 100).toFixed(4) + '%';
                el.style.width  = (rbSize / wrapW * 100).toFixed(4) + '%';
                el.style.height = (rbSize / (wrap.clientHeight || (wrapW * ih / iw)) * 100).toFixed(4) + '%';
            } else if (field.type === 'dropdown' || field.type === 'Dropdown') {
                el = document.createElement('select');
                el.className = 'preview-input';
                pos();
                el.style.fontSize = fontSize + 'px';
                el.style.fontFamily = fontFamily;
                el.style.color = fontColor;
                const opts = field.options?.length
                    ? field.options.map(o => `<option>${typeof o === 'object' ? o.label : o}</option>`).join('')
                    : '<option>Option 1</option><option>Option 2</option>';
                el.innerHTML = opts;
            } else {
                // Text / date / phone / number / etc.
                el = document.createElement('input');
                el.className = 'preview-input';
                pos();
                el.style.fontSize = fontSize + 'px';
                el.style.fontFamily = fontFamily;
                el.style.color = fontColor;
                if (field.formatType === 'date') {
                    el.type = 'date'; el.valueAsDate = new Date();
                } else if (field.formatType === 'number') {
                    el.type = 'number';
                } else {
                    el.type = field.password ? 'password' : 'text';
                    el.placeholder = field.tooltip || field.name;
                }
                if (field.textAlign) el.style.textAlign = field.textAlign;
                if (field.maxLen > 0) el.maxLength = field.maxLen;
                if (field.readOnly) { el.readOnly = true; el.style.background = '#f0f0f0'; }
                if (field.defaultValue) el.value = field.defaultValue;
            }

            // Fill colour (non checkbox/radio)
            if (el && field.type !== 'checkbox' && field.type !== 'Checkbox' &&
                field.type !== 'radio' && field.type !== 'Radio') {
                if (fillColor && fillColor !== 'transparent') {
                    const r = parseInt(fillColor.slice(1,3), 16);
                    const g = parseInt(fillColor.slice(3,5), 16);
                    const b = parseInt(fillColor.slice(5,7), 16);
                    el.style.backgroundColor = `rgba(${r},${g},${b},${fillOpacity})`;
                }
            }

            el.title = field.name;
            layer.appendChild(el);
        });
    });
}

// Add preview button event listener
document.addEventListener('DOMContentLoaded', () => {
    const btnPreview = document.getElementById('btnPreview');
    if (btnPreview) {
        btnPreview.addEventListener('click', togglePreviewMode);
    }
});

// Exit preview mode on Escape
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && isPreviewMode) {
        togglePreviewMode();
    }
});

// ============ Tab Order & Auto Sort ============
document.addEventListener('DOMContentLoaded', () => {
    // Button removed, logic moved to dropdown change event
});

function applyTabOrder(mode) {
    // 1. Get fields for current page
    const pageFields = fields.filter(f => f.page === currentPage);
    const otherFields = fields.filter(f => f.page !== currentPage);
    
    // 2. Sort page fields based on mode
    pageFields.sort((a, b) => {
        if (mode === 'structure' || mode === 'row') {
            // Sort by Y (row) first with tolerance, then X
            const yDiff = Math.abs(a.rect[1] - b.rect[1]);
            if (yDiff > 5) return a.rect[1] - b.rect[1];
            return a.rect[0] - b.rect[0];
        } else if (mode === 'column') {
            // Sort by X (col) first with tolerance, then Y
            const xDiff = Math.abs(a.rect[0] - b.rect[0]);
            if (xDiff > 5) return a.rect[0] - b.rect[0];
            return a.rect[1] - b.rect[1];
        } else if (mode === 'alphabetic') {
            return a.name.localeCompare(b.name);
        } else if (mode === 'reverse-alpha') {
            return b.name.localeCompare(a.name);
        }
        return 0;
    });
    
    // 2. Reconstruct fields array
    const sortedPageFields = [...pageFields];
    
    // IMPORTANT: We need to keep the global fields array consistent with the page sort
    // But we can't just replace fields, we need to respect other pages
    // So we'll put the current page's sorted fields back into the main array
    // To do this simply, we'll just use the sortedPageFields for the current page in the UI
    // and when saving, we construct the whole list.
    
    // Actually, let's just reorder the global list for the current page
    // Filter out current page fields, then insert sorted ones? 
    // No, that messes up page order.
    // Better: Just update the `changes.tab_order` with the new list of names
    
    const newOrderNames = sortedPageFields.map(f => f.name);
    changes.tab_order[currentPage] = newOrderNames;
    
    // Also update the UI to reflect this order if "Tab Order" sort is active or if we force it
    document.getElementById('fieldSortOrder').value = 'tab';
    fieldSortOrder = 'tab';
    
    // We also need to actually reorder the `fields` array so that the "Tab Order" sort works correctly
    // The "Tab Order" sort currently relies on the array index.
    
    // Correct approach to reorder global fields:
    // Group all by page
    const fieldsByPage = {};
    const oldFields = [...fields]; // copy
    oldFields.forEach(f => {
        if (!fieldsByPage[f.page]) fieldsByPage[f.page] = [];
        fieldsByPage[f.page].push(f);
    });
    
    // Replace current page with sorted fields
    fieldsByPage[currentPage] = sortedPageFields;
    
    // Flatten back
    fields = [];
    for (let i = 0; i < pages.length; i++) {
        if (fieldsByPage[i]) {
            fields.push(...fieldsByPage[i]);
        }
    }
    
    // 5. Update UI
    // Update state to match selected mode so valid options (like 'row') persist in dropdown
    fieldSortOrder = mode; 
    const sortSelect = document.getElementById('fieldSortOrder');
    if (sortSelect) sortSelect.value = mode;

    markUnsaved();
    updateFieldList();
    renderCanvas();
    
    console.log(`Applied tab order: ${mode}`);
    const statusMsg = document.getElementById('statusMessage');
    if (statusMsg) {
        statusMsg.textContent = `Applied order: ${mode}`;
        statusMsg.style.opacity = '1';
        setTimeout(() => { statusMsg.style.opacity = '0'; }, 2000);
    }
}

// ============ Drag and Drop for Field List ============
let draggedItem = null;

function handleDragStart(e) {
    draggedItem = this;
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/html', this.innerHTML);
    this.classList.add('dragging');
}

function handleDragOver(e) {
    if (e.preventDefault) e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    return false;
}

function handleDragEnter(e) {
    this.classList.add('drag-over');
}

function handleDragLeave(e) {
    this.classList.remove('drag-over');
}

function handleDrop(e) {
    if (e.stopPropagation) e.stopPropagation();
    
    if (draggedItem !== this) {
        // Swap items in UI
        const list = this.parentNode;
        const allItems = [...list.querySelectorAll('.field-item')];
        const fromIndex = allItems.indexOf(draggedItem);
        const toIndex = allItems.indexOf(this);
        
        if (fromIndex < toIndex) {
            list.insertBefore(draggedItem, this.nextSibling);
        } else {
            list.insertBefore(draggedItem, this);
        }
        
        // Update data model
        // We are strictly reordering within the filtered list (current page)
        // Get all fields on current page
        const pageFields = fields.filter(f => f.page === currentPage);
        // Move item in array
        const movedField = pageFields.splice(fromIndex, 1)[0];
        pageFields.splice(toIndex, 0, movedField);
        
        // Update global fields array
        const otherFields = fields.filter(f => f.page !== currentPage);
        
        // To keep global order sane, we need to find where the current page block starts
        // Or simpler: just replace the current page's fields with the new order
        
        // Rebuild global fields
        const fieldsByPage = {};
        fields.forEach(f => {
            if (!fieldsByPage[f.page]) fieldsByPage[f.page] = [];
            fieldsByPage[f.page].push(f);
        });
        fieldsByPage[currentPage] = pageFields;
        
        fields = [];
        for (let i = 0; i < pages.length; i++) {
            if (fieldsByPage[i]) fields.push(...fieldsByPage[i]);
        }
        
        // Record change
        changes.tab_order[currentPage] = pageFields.map(f => f.name);
        markUnsaved();
        
        // Force sort to Tab Order
        document.getElementById('fieldSortOrder').value = 'tab';
        fieldSortOrder = 'tab';
        
        renderCanvas(); // Redraw to update tab numbers
    }
    
    this.classList.remove('drag-over');
    return false;
}

function handleDragEnd(e) {
    this.classList.remove('dragging');
    document.querySelectorAll('.field-item').forEach(item => {
        item.classList.remove('drag-over');
    });
}
// Initialize on load
document.addEventListener('DOMContentLoaded', () => {
    // Auto-Name Buttons
    const btnAutoSidebar = document.getElementById('btnAutoNameSidebar');
    if (btnAutoSidebar) {
        btnAutoSidebar.addEventListener('click', (e) => {
            e.stopPropagation(); // prevent modal close or list select issues
            handleAutoName(btnAutoSidebar, false);
        });
    }

    const btnAutoModal = document.getElementById('btnAutoNameModal');
    if (btnAutoModal) {
        btnAutoModal.addEventListener('click', (e) => {
            e.preventDefault();
            handleAutoName(btnAutoModal, true);
        });
    }
    
    // Batch Auto-Name Button (for multi-selection)
    const btnAutoNameBatch = document.getElementById('btnAutoNameBatch');
    if (btnAutoNameBatch) {
        btnAutoNameBatch.addEventListener('click', (e) => {
            e.preventDefault();
            handleAutoName(btnAutoNameBatch, false); // false = not modal, uses selectedFields
        });
    }
});

// ============ AI Helpers ============
async function handleAutoName(btn, isModal) {
    // 1. Identify target fields
    let targets = [];
    if (isModal && modalField) {
        // Validation: Modal only edits one field
        targets = [modalField];
    } else if (!isModal && selectedFields.length > 0) {
        targets = [...selectedFields];
    }
    
    if (targets.length === 0) return;
    
    // Button element is now passed directly
    if (!btn) return;
    
    const originalText = btn.innerHTML;
    btn.innerHTML = '✨...';
    btn.disabled = true;
    
    try {
        // 2. Prepare payload
        // We use the field name as ID to map back results
        const fieldsPayload = targets.map(f => ({
            id: f.name, 
            page: (f.page || 0) + 1, // Convert 0-indexed to 1-indexed for Python
            rect: f.rect
        }));
        
        // 3. Call backend batch API
        // pdfPath is global
        const results = await ipcRenderer.invoke('auto-name-fields', pdfPath, fieldsPayload);
        
        // 4. Process results & Handle Collisions
        // Group fields by their suggested name to apply numbering
        const bySuggested = {};
        
        targets.forEach(f => {
            const suggested = results[f.name]; // Look up by old name (ID)
            if (suggested) {
                if (!bySuggested[suggested]) bySuggested[suggested] = [];
                bySuggested[suggested].push(f);
            }
        });
        
        // Helper: Find highest existing number for a base name across ALL fields
        function getHighestExistingNumber(baseName) {
            let highest = 0;
            const targetFieldNames = new Set(targets.map(t => t.name));
            
            for (const f of fields) {
                // Skip fields we're about to rename (they're in our targets)
                if (targetFieldNames.has(f.name)) continue;
                
                // Check for exact match (base name without number)
                if (f.name === baseName) {
                    highest = Math.max(highest, 1); // Treat exact match as _1
                }
                
                // Check for numbered variants like "Friday_1", "Friday_2", etc.
                const match = f.name.match(new RegExp(`^${baseName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}_(\\d+)$`));
                if (match) {
                    highest = Math.max(highest, parseInt(match[1], 10));
                }
            }
            return highest;
        }
        
        // Assign new names
        const renames = {}; // Map<OldName, NewName>
        
        for (const [baseName, fieldsInGroup] of Object.entries(bySuggested)) {
            // Find the highest existing number for this base name across all pages
            const startingNumber = getHighestExistingNumber(baseName);
            
            // Sort by page first, then by Y (top to bottom), then X (left to right)
            fieldsInGroup.sort((a, b) => {
                // Sort by page first for consistent cross-page ordering
                if (a.page !== b.page) return a.page - b.page;
                // Tolerance of 5pt for Y to handle slight misalignment in rows
                if (Math.abs(a.rect[1] - b.rect[1]) > 5) return a.rect[1] - b.rect[1];
                return a.rect[0] - b.rect[0];
            });
            
            // If only one field AND no existing fields with this name, use exact name
            if (fieldsInGroup.length === 1 && startingNumber === 0) {
                renames[fieldsInGroup[0].name] = baseName;
            } else {
                // Multiple fields or existing numbered fields - use sequential numbering
                fieldsInGroup.forEach((f, idx) => {
                    renames[f.name] = `${baseName}_${startingNumber + idx + 1}`;
                });
            }
        }
        
        // 5. Apply Changes
        let changedCount = 0;
        let lastNewName = "";
        
        for (const f of targets) {
            const newName = renames[f.name];
            if (newName && newName !== f.name) {
                // Track rename
                if (changes.renamed[f.name]) {
                    // Already renamed in this session? complex. 
                    // Just overwrite mapping if logic allows, but here we map Old->New.
                    // If A->B, and we rename B->C, we need to update A->C.
                    // But here f.name IS the current working name (updated in RAM).
                    // So we just add new entry.
                    // Wait, changes.renamed maps OriginalPDFName -> NewName.
                    // If we renamed "Text1" -> "Foo", f.name is "Foo". 
                    // We need the key to be "Text1".
                    // We don't easily track "OriginalPDFName on field object".
                    // Actually we DO NOT. We relying on f.name being the key.
                    // If f.name was already changed, we might have issues tracking back to original.
                    // Ideally we should update the existing rename entry.
                    
                    // Simple approach: Update f.name and store in changes.renamed using *current* f.name as key?
                    // No, modify_fields.py needs ORIGINAL name to find it.
                    // If we lose track of original name, we can't rename it.
                    // ISSUE: Editor doesn't track `originalName` on field object explicitly?
                    // Let's check `parseField`.
                    
                    // Workaround: We blindly add to `renamed`. The backend applies them in sequence?
                    // No, `renamed` is a dict.
                    // If we rename "A"->"B", then "B"->"C".
                    // The backend sees "A"->"B" and "B"->"C".
                    // If "B" didn't exist in PDF, "B"->"C" fails.
                    // We need to resolve chain or store original name.
                    
                    // For now, let's assume one rename per session or just update the value if key exists?
                    // Finding the key for value `f.name` in `changes.renamed`?
                    let originalKey = f.name;
                    for (const [k, v] of Object.entries(changes.renamed)) {
                        if (v === f.name) {
                            originalKey = k;
                            break;
                        }
                    }
                    
                    changes.renamed[originalKey] = newName;
                } else {
                    changes.renamed[f.name] = newName;
                }
                
                f.name = newName;
                
                // Also set tooltip to match new name
                f.tooltip = newName;
                if (!changes.styled[newName]) {
                    changes.styled[newName] = {};
                }
                changes.styled[newName].tooltip = newName;
                
                changedCount++;
                lastNewName = newName;
            }
        }
        
        // 6. Update UI
        if (changedCount > 0) {
            hasUnsavedChanges = true;
            document.getElementById('changesMade').style.display = 'inline';
            updateFieldCount();
            updateFieldList();
            renderCanvas();
            
            // Flash success
            btn.style.backgroundColor = '#e6fffa'; // Greenish
            setTimeout(() => btn.style.backgroundColor = '', 500);
            
            // Update inputs
            if (isModal) {
                 document.getElementById('modalPropName').value = modalField.name;
                 // Flash input
                 const input = document.getElementById('modalPropName');
                 input.style.backgroundColor = '#e6fffa';
                 setTimeout(() => input.style.backgroundColor = '', 500);
            } else {
                if (targets.length === 1) {
                    document.getElementById('propName').value = targets[0].name;
                } else {
                    // Update property panel if showing distinct values
                    updatePropertiesPanel(); // This might show "Multiple Values" or blank
                }
            }
        } else {
            // No changes found
            const originalColor = btn.style.backgroundColor;
            btn.style.backgroundColor = '#fff0f0'; // Reddish
            setTimeout(() => btn.style.backgroundColor = originalColor, 500);
        }

    } catch (e) {
        console.error("Auto name failed", e);
        btn.style.backgroundColor = '#ffcccc';
    } finally {
         setTimeout(() => {
            btn.innerHTML = originalText;
            btn.disabled = false;
        }, 500);
    }
}

// ============ Draggable Modal Logic ============
function makeElementDraggable(headerEl, modalEl) {
    let isDragging = false;
    let startX, startY;
    let initialTranslateX = 0, initialTranslateY = 0;

    headerEl.addEventListener('mousedown', dragStart);

    function dragStart(e) {
        if (e.target.closest('.modal-close')) return; // Don't drag if clicking close button
        
        isDragging = true;
        startX = e.clientX;
        startY = e.clientY;
        
        // Get current transform values
        const style = window.getComputedStyle(modalEl);
        // WebKitCSSMatrix handles 'none' and matrix() strings gracefully
        const matrix = new WebKitCSSMatrix(style.transform);
        initialTranslateX = matrix.m41;
        initialTranslateY = matrix.m42;
        
        document.addEventListener('mousemove', drag);
        document.addEventListener('mouseup', dragEnd);
        
        // Optional: reduce opacity while dragging
        modalEl.style.opacity = '0.9';
    }

    function drag(e) {
        if (!isDragging) return;
        e.preventDefault();
        
        const currentX = e.clientX - startX;
        const currentY = e.clientY - startY;
        
        let x = initialTranslateX + currentX;
        let y = initialTranslateY + currentY;
        
        // Clamp to viewport so the modal can't escape the window
        const rect = modalEl.getBoundingClientRect();
        const curMatrix = new WebKitCSSMatrix(window.getComputedStyle(modalEl).transform);
        const dx = x - curMatrix.m41;
        const dy = y - curMatrix.m42;
        
        const newTop = rect.top + dy;
        const newLeft = rect.left + dx;
        const newRight = rect.right + dx;
        const newBottom = rect.bottom + dy;
        
        if (newTop < 0) y -= newTop;
        if (newBottom > window.innerHeight) y -= (newBottom - window.innerHeight);
        if (newLeft < 0) x -= newLeft;
        if (newRight > window.innerWidth) x -= (newRight - window.innerWidth);
        
        modalEl.style.transform = `translate(${x}px, ${y}px)`;
    }

    function dragEnd() {
        isDragging = false;
        modalEl.style.opacity = '1';
        document.removeEventListener('mousemove', drag);
        document.removeEventListener('mouseup', dragEnd);
    }
}

// Initialize draggable properties panel
document.addEventListener('DOMContentLoaded', () => {
    const propPanel = document.getElementById('propertiesModal');
    if (propPanel) {
        const propHeader = document.getElementById('propPanelHeader');
        if (propHeader) {
            makeElementDraggable(propHeader, propPanel);
        }
    }

    // Initialize draggable Calculations Manager panel
    const calcPanel = document.getElementById('calcManagerPanel');
    if (calcPanel) {
        const calcHeader = document.getElementById('calcPanelHeader');
        if (calcHeader) {
            makeElementDraggable(calcHeader, calcPanel);
        }
    }

    // Initialize draggable column resizers for Calculations Manager table
    (function initCalcColumnResizers() {
        const table = document.getElementById('calcManagerTableEl');
        if (!table) return;
        const resizers = table.querySelectorAll('.calc-col-resizer');
        resizers.forEach(resizer => {
            let startX, startWidth, th;
            resizer.addEventListener('mousedown', (e) => {
                e.preventDefault();
                e.stopPropagation();
                th = resizer.parentElement;
                startX = e.pageX;
                startWidth = th.offsetWidth;
                resizer.classList.add('dragging');
                document.addEventListener('mousemove', onMouseMove);
                document.addEventListener('mouseup', onMouseUp);
            });
            function onMouseMove(e) {
                const newWidth = Math.max(40, startWidth + (e.pageX - startX));
                th.style.width = newWidth + 'px';
            }
            function onMouseUp() {
                resizer.classList.remove('dragging');
                document.removeEventListener('mousemove', onMouseMove);
                document.removeEventListener('mouseup', onMouseUp);
            }
        });
    })();

    // Initialize draggable History panel
    const historyPanel = document.getElementById('historyModal');
    if (historyPanel) {
        const histHeader = document.getElementById('historyPanelHeader');
        if (histHeader) makeElementDraggable(histHeader, historyPanel);
    }
});

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Event listener for History button
document.addEventListener('DOMContentLoaded', () => {
    const btnHistory = document.getElementById('btnVersionHistory');
    if (btnHistory) {
        btnHistory.addEventListener('click', openHistoryModal);
    }
    
    const btnSnapshot = document.getElementById('btnCreateSnapshot');
    if (btnSnapshot) {
        btnSnapshot.addEventListener('click', createSnapshot);
    }
});

// ============ Version History / Snapshots ============

// Store snapshots in memory (session-based)
let snapshots = [];

/**
 * Custom in-app prompt (Electron doesn't support window.prompt).
 * Returns a Promise that resolves with the entered string, or null if cancelled.
 */
function showInputPrompt(title, defaultValue = '') {
    return new Promise((resolve) => {
        // Create overlay
        const overlay = document.createElement('div');
        overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:100000;';

        const box = document.createElement('div');
        box.style.cssText = 'background:var(--bg-secondary, #1e293b);border:1px solid var(--border-color, #334155);border-radius:12px;padding:24px;width:380px;box-shadow:0 20px 60px rgba(0,0,0,0.5);';

        const label = document.createElement('div');
        label.textContent = title;
        label.style.cssText = 'color:var(--text-primary, #e2e8f0);font-size:14px;font-weight:600;margin-bottom:12px;';

        const input = document.createElement('input');
        input.type = 'text';
        input.value = defaultValue;
        input.style.cssText = 'width:100%;padding:10px 12px;border-radius:8px;border:1px solid var(--border-color, #334155);background:var(--bg-primary, #0f172a);color:var(--text-primary, #e2e8f0);font-size:14px;outline:none;box-sizing:border-box;';

        const btnRow = document.createElement('div');
        btnRow.style.cssText = 'display:flex;gap:10px;justify-content:flex-end;margin-top:16px;';

        const btnCancel = document.createElement('button');
        btnCancel.textContent = 'Cancel';
        btnCancel.className = 'action-btn btn-secondary';
        btnCancel.style.cssText = 'padding:8px 20px;font-size:13px;';

        const btnOk = document.createElement('button');
        btnOk.textContent = 'OK';
        btnOk.className = 'action-btn btn-primary';
        btnOk.style.cssText = 'padding:8px 20px;font-size:13px;';

        function cleanup(value) {
            overlay.remove();
            resolve(value);
        }

        btnCancel.addEventListener('click', () => cleanup(null));
        btnOk.addEventListener('click', () => cleanup(input.value.trim() || null));
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') cleanup(input.value.trim() || null);
            if (e.key === 'Escape') cleanup(null);
        });

        btnRow.appendChild(btnCancel);
        btnRow.appendChild(btnOk);
        box.appendChild(label);
        box.appendChild(input);
        box.appendChild(btnRow);
        overlay.appendChild(box);
        document.body.appendChild(overlay);

        // Auto-focus and select
        setTimeout(() => { input.focus(); input.select(); }, 50);
    });
}

/**
 * Custom in-app alert dialog (replaces Electron's native alert with its icon).
 * Returns a Promise that resolves when dismissed.
 */
function showAlert(message, title = '') {
    return new Promise((resolve) => {
        const overlay = document.createElement('div');
        overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:100000;';

        const box = document.createElement('div');
        box.style.cssText = 'background:var(--bg-secondary, #1e293b);border:1px solid var(--border-color, #334155);border-radius:12px;padding:24px;width:420px;box-shadow:0 20px 60px rgba(0,0,0,0.5);';

        if (title) {
            const titleEl = document.createElement('div');
            titleEl.textContent = title;
            titleEl.style.cssText = 'color:var(--text-primary, #e2e8f0);font-size:15px;font-weight:700;margin-bottom:14px;';
            box.appendChild(titleEl);
        }

        const msg = document.createElement('div');
        msg.style.cssText = 'color:var(--text-secondary, #94a3b8);font-size:13px;line-height:1.6;white-space:pre-wrap;margin-bottom:18px;';
        msg.textContent = message;
        box.appendChild(msg);

        const btnRow = document.createElement('div');
        btnRow.style.cssText = 'display:flex;justify-content:flex-end;';
        const btnOk = document.createElement('button');
        btnOk.textContent = 'OK';
        btnOk.className = 'action-btn btn-primary';
        btnOk.style.cssText = 'padding:8px 28px;font-size:13px;';
        btnOk.addEventListener('click', () => { overlay.remove(); resolve(); });

        btnRow.appendChild(btnOk);
        box.appendChild(btnRow);
        overlay.appendChild(box);
        document.body.appendChild(overlay);
        setTimeout(() => btnOk.focus(), 50);

        // Allow Enter/Escape to dismiss
        overlay.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === 'Escape') { overlay.remove(); resolve(); }
        });
    });
}

/**
 * Custom in-app confirm dialog (replaces Electron's native confirm).
 * Returns a Promise that resolves with true (OK) or false (Cancel).
 */
function showConfirm(message, title = '') {
    return new Promise((resolve) => {
        const overlay = document.createElement('div');
        overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:100000;';

        const box = document.createElement('div');
        box.style.cssText = 'background:var(--bg-secondary, #1e293b);border:1px solid var(--border-color, #334155);border-radius:12px;padding:24px;width:420px;box-shadow:0 20px 60px rgba(0,0,0,0.5);';

        if (title) {
            const titleEl = document.createElement('div');
            titleEl.textContent = title;
            titleEl.style.cssText = 'color:var(--text-primary, #e2e8f0);font-size:15px;font-weight:700;margin-bottom:14px;';
            box.appendChild(titleEl);
        }

        const msg = document.createElement('div');
        msg.style.cssText = 'color:var(--text-secondary, #94a3b8);font-size:13px;line-height:1.6;white-space:pre-wrap;margin-bottom:18px;';
        msg.textContent = message;
        box.appendChild(msg);

        const btnRow = document.createElement('div');
        btnRow.style.cssText = 'display:flex;gap:10px;justify-content:flex-end;';

        const btnCancel = document.createElement('button');
        btnCancel.textContent = 'Cancel';
        btnCancel.className = 'action-btn btn-secondary';
        btnCancel.style.cssText = 'padding:8px 20px;font-size:13px;';
        btnCancel.addEventListener('click', () => { overlay.remove(); resolve(false); });

        const btnOk = document.createElement('button');
        btnOk.textContent = 'OK';
        btnOk.className = 'action-btn btn-primary';
        btnOk.style.cssText = 'padding:8px 20px;font-size:13px;';
        btnOk.addEventListener('click', () => { overlay.remove(); resolve(true); });

        btnRow.appendChild(btnCancel);
        btnRow.appendChild(btnOk);
        box.appendChild(btnRow);
        overlay.appendChild(box);
        document.body.appendChild(overlay);
        setTimeout(() => btnOk.focus(), 50);

        overlay.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') { overlay.remove(); resolve(true); }
            if (e.key === 'Escape') { overlay.remove(); resolve(false); }
        });
    });
}

/**
 * Custom exit confirmation dialog with Save & Exit / Exit Without Saving.
 * Returns a Promise that resolves with 'save', 'exit', or 'cancel'.
 */
function showExitConfirm() {
    return new Promise((resolve) => {
        const overlay = document.createElement('div');
        overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:100000;';

        const box = document.createElement('div');
        box.style.cssText = 'background:var(--bg-secondary, #1e293b);border:1px solid var(--border-color, #334155);border-radius:12px;padding:24px;width:440px;box-shadow:0 20px 60px rgba(0,0,0,0.5);';

        const titleEl = document.createElement('div');
        titleEl.textContent = '\u26A0\uFE0F Unsaved Changes';
        titleEl.style.cssText = 'color:var(--text-primary, #e2e8f0);font-size:15px;font-weight:700;margin-bottom:14px;';
        box.appendChild(titleEl);

        const msg = document.createElement('div');
        msg.textContent = 'You have unsaved changes. What would you like to do?';
        msg.style.cssText = 'color:var(--text-secondary, #94a3b8);font-size:13px;line-height:1.6;margin-bottom:18px;';
        box.appendChild(msg);

        const btnRow = document.createElement('div');
        btnRow.style.cssText = 'display:flex;gap:10px;justify-content:flex-end;';

        const btnExitOnly = document.createElement('button');
        btnExitOnly.textContent = 'Exit Without Saving';
        btnExitOnly.className = 'action-btn';
        btnExitOnly.style.cssText = 'padding:8px 18px;font-size:13px;font-weight:600;background:var(--danger, #e94560);color:#fff;border:none;border-radius:6px;cursor:pointer;';
        btnExitOnly.addEventListener('click', () => { overlay.remove(); resolve('exit'); });

        const btnSaveExit = document.createElement('button');
        btnSaveExit.textContent = '\uD83D\uDCBE Save & Exit';
        btnSaveExit.className = 'action-btn btn-primary';
        btnSaveExit.style.cssText = 'padding:8px 18px;font-size:13px;font-weight:600;background:var(--accent, #64ffda);color:var(--bg-primary, #0a192f);border:none;border-radius:6px;cursor:pointer;';
        btnSaveExit.addEventListener('click', () => { overlay.remove(); resolve('save'); });

        btnRow.appendChild(btnExitOnly);
        btnRow.appendChild(btnSaveExit);
        box.appendChild(btnRow);
        overlay.appendChild(box);
        document.body.appendChild(overlay);
        setTimeout(() => btnSaveExit.focus(), 50);

        overlay.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') { overlay.remove(); resolve('cancel'); }
        });
    });
}

function openHistoryModal() {
    const panel = document.getElementById('historyModal');
    if (panel) {
        panel.style.display = 'flex';
        panel.classList.remove('minimized');
        renderSnapshotsList();
    }
}

function closeHistoryModal() {
    const panel = document.getElementById('historyModal');
    if (panel) {
        panel.style.display = 'none';
        panel.classList.remove('minimized');
    }
}

async function createSnapshot() {
    const name = await showInputPrompt('Enter a name for this snapshot:', `Snapshot ${snapshots.length + 1}`);
    if (!name) return;

    // Deep clone current state
    const snapshot = {
        id: Date.now(),
        name: name,
        timestamp: new Date().toLocaleString(),
        fieldsCount: fields.length,
        data: {
            fields: JSON.parse(JSON.stringify(fields)),
            changes: JSON.parse(JSON.stringify(changes))
        }
    };

    snapshots.unshift(snapshot); // Add to beginning
    renderSnapshotsList();

    // Flash feedback
    const btn = document.getElementById('btnCreateSnapshot');
    if (btn) {
        btn.textContent = '✅ Saved!';
        btn.style.background = '#10b981';
        setTimeout(() => {
            btn.textContent = '📸 Create Snapshot';
            btn.style.background = '';
        }, 1500);
    }
}

function renderSnapshotsList() {
    const container = document.getElementById('snapshotsList');
    if (!container) return;
    
    if (snapshots.length === 0) {
        container.innerHTML = `
            <div style="text-align: center; padding: 40px; color: var(--text-secondary);">
                <div style="font-size: 36px; margin-bottom: 10px;">📷</div>
                <div>No snapshots yet</div>
                <div style="font-size: 12px; margin-top: 5px;">Create a snapshot to save the current state</div>
            </div>
        `;
        return;
    }
    
    container.innerHTML = snapshots.map((snap, idx) => `
        <div class="snapshot-item" style="display: flex; align-items: center; padding: 12px; background: var(--bg-tertiary); border-radius: 8px; margin-bottom: 8px; border: 1px solid var(--border-color);">
            <div style="flex: 1;">
                <div style="font-weight: 600; color: var(--text-primary);">${escapeHtml(snap.name)}</div>
                <div style="font-size: 11px; color: var(--text-secondary); margin-top: 3px;">
                    ${snap.timestamp} • ${snap.fieldsCount} fields
                </div>
            </div>
            <div style="display: flex; gap: 8px;">
                <button class="action-btn btn-secondary" onclick="compareSnapshot(${idx})" style="padding: 6px 12px; font-size: 11px;" title="Compare with current">
                    🔍 Compare
                </button>
                <button class="action-btn btn-primary" onclick="restoreSnapshot(${idx})" style="padding: 6px 12px; font-size: 11px;" title="Restore this snapshot">
                    ⏪ Restore
                </button>
                <button onclick="deleteSnapshot(${idx})" style="background: none; border: none; color: var(--danger); cursor: pointer; font-size: 16px;" title="Delete">
                    🗑️
                </button>
            </div>
        </div>
    `).join('');
}

async function restoreSnapshot(index) {
    const snap = snapshots[index];
    if (!snap) return;

    const ok = await showConfirm(`Restore to "${snap.name}"?\n\nThis will replace all current field data and unsaved changes.`, '⏪ Restore Snapshot');
    if (!ok) return;

    // Restore state
    fields.length = 0;
    fields.push(...JSON.parse(JSON.stringify(snap.data.fields)));

    Object.keys(changes).forEach(k => {
        if (typeof changes[k] === 'object') {
            changes[k] = {};
        }
    });
    Object.assign(changes, JSON.parse(JSON.stringify(snap.data.changes)));

    // Update UI
    renderCanvas();
    renderFieldList();
    updateFieldCount();
    hasUnsavedChanges = true;
    document.getElementById('changesMade').style.display = 'inline';

    closeHistoryModal();
    showAlert(`Restored to "${snap.name}"`, '✅ Snapshot Restored');
}

/**
 * Describe a property change in human-readable form
 */
function _describeChange(key, oldVal, newVal) {
    // Property display name mapping
    const labels = {
        type: 'Type', tooltip: 'Tooltip', readOnly: 'Read Only', required: 'Required',
        locked: 'Locked', multiline: 'Multiline', password: 'Password', comb: 'Comb',
        scrollText: 'Scroll Text', visibility: 'Visibility', orientation: 'Rotation',
        maxLen: 'Character Limit', defaultValue: 'Default Value', exportValue: 'Export Value',
        fontSize: 'Font Size', fontFamily: 'Font', fontColor: 'Font Color',
        fillColor: 'Fill Color', borderColor: 'Border Color', textAlign: 'Alignment',
        lineThickness: 'Border Width', lineStyle: 'Border Style', checkboxStyle: 'Checkbox Style',
        formatType: 'Format', dateFormat: 'Date Format', currencySymbol: 'Currency',
        currencyDecimals: 'Currency Decimals', phoneFormat: 'Phone Format',
        numberDecimals: 'Number Decimals', thousandSep: 'Thousands Separator',
        zipFormat: 'ZIP Format', sort: 'Sort Options', editable: 'Editable',
        spellCheck: 'Spell Check', commitImmediately: 'Commit Immediately',
        name: 'Name', page: 'Page',
    };

    const label = labels[key] || key;

    // Format values nicely
    const fmt = (v) => {
        if (v === undefined || v === null || v === '') return 'none';
        if (typeof v === 'boolean') return v ? 'Yes' : 'No';
        if (Array.isArray(v)) return `[${v.length} items]`;
        if (typeof v === 'object') return JSON.stringify(v).substring(0, 40);
        return String(v);
    };

    return `${label}: ${fmt(oldVal)} → ${fmt(newVal)}`;
}

/**
 * Deep-diff two field objects and return array of change descriptions
 */
function _diffFields(oldField, newField) {
    const diffs = [];
    // Skip internal/volatile keys
    const skip = new Set(['rect', 'style', 'noResizeOnMove', 'positionLocked', 'tabOrder']);

    // Collect all keys from both objects
    const allKeys = new Set([...Object.keys(oldField), ...Object.keys(newField)]);

    for (const key of allKeys) {
        if (skip.has(key)) continue;

        const oldVal = oldField[key];
        const newVal = newField[key];

        // Handle style sub-object specially
        if (key === 'style') continue; // We handle style properties at the top level already

        // Deep equality check
        if (JSON.stringify(oldVal) !== JSON.stringify(newVal)) {
            // For options arrays, summarize
            if (key === 'options') {
                const oldCount = Array.isArray(oldVal) ? oldVal.length : 0;
                const newCount = Array.isArray(newVal) ? newVal.length : 0;
                if (oldCount !== newCount) {
                    diffs.push(`Options: ${oldCount} → ${newCount} items`);
                } else {
                    diffs.push('Options: items changed');
                }
            } else if (key === 'actions') {
                diffs.push('Actions: modified');
            } else if (key === 'validation') {
                const oldRule = oldVal?.rule || 'none';
                const newRule = newVal?.rule || 'none';
                diffs.push(`Validation: ${oldRule} → ${newRule}`);
            } else if (key === 'rules') {
                const oldCount = Array.isArray(oldVal) ? oldVal.length : 0;
                const newCount = Array.isArray(newVal) ? newVal.length : 0;
                diffs.push(`Rules: ${oldCount} → ${newCount}`);
            } else {
                diffs.push(_describeChange(key, oldVal, newVal));
            }
        }
    }

    // Also check rect for position/size changes
    if (JSON.stringify(oldField.rect) !== JSON.stringify(newField.rect)) {
        diffs.push('Position/Size: moved or resized');
    }

    return diffs;
}

function compareSnapshot(index) {
    const snap = snapshots[index];
    if (!snap) return;

    // Build comparison
    const currentFieldNames = new Set(fields.map(f => f.name));
    const snapFieldNames = new Set(snap.data.fields.map(f => f.name));
    const snapFieldMap = {};
    snap.data.fields.forEach(f => { snapFieldMap[f.name] = f; });

    const added = [...currentFieldNames].filter(n => !snapFieldNames.has(n));
    const removed = [...snapFieldNames].filter(n => !currentFieldNames.has(n));
    const modifiedDetails = []; // { name, diffs[] }

    // Deep compare all field properties
    fields.forEach(f => {
        if (snapFieldNames.has(f.name)) {
            const snapField = snapFieldMap[f.name];
            if (snapField && JSON.stringify(f) !== JSON.stringify(snapField)) {
                const diffs = _diffFields(snapField, f);
                if (diffs.length > 0) {
                    modifiedDetails.push({ name: f.name, diffs });
                }
            }
        }
    });

    // Build rich HTML comparison modal
    const overlay = document.createElement('div');
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:100000;';

    const box = document.createElement('div');
    box.style.cssText = 'background:var(--bg-secondary, #1e293b);border:1px solid var(--border-color, #334155);border-radius:12px;padding:24px;width:560px;max-height:80vh;display:flex;flex-direction:column;box-shadow:0 20px 60px rgba(0,0,0,0.5);';

    // Title
    const title = document.createElement('div');
    title.style.cssText = 'color:var(--text-primary, #e2e8f0);font-size:15px;font-weight:700;margin-bottom:16px;';
    title.textContent = `📊 Comparison: "${snap.name}" vs Current`;
    box.appendChild(title);

    // Summary bar
    const summary = document.createElement('div');
    summary.style.cssText = 'display:flex;gap:16px;margin-bottom:16px;padding:10px;background:var(--bg-primary, #0f172a);border-radius:8px;font-size:13px;color:var(--text-secondary, #94a3b8);';
    summary.innerHTML = `
        <span style="color:#10b981;">➕ ${added.length} added</span>
        <span style="color:#ef4444;">➖ ${removed.length} removed</span>
        <span style="color:#f59e0b;">✏️ ${modifiedDetails.length} modified</span>
    `;
    box.appendChild(summary);

    // Scrollable content
    const content = document.createElement('div');
    content.style.cssText = 'overflow-y:auto;flex:1;max-height:50vh;padding-right:6px;';

    // Added fields
    if (added.length > 0) {
        const section = document.createElement('div');
        section.style.cssText = 'margin-bottom:14px;';
        section.innerHTML = `<div style="color:#10b981;font-weight:600;font-size:12px;margin-bottom:6px;text-transform:uppercase;">Added Fields</div>`;
        added.forEach(name => {
            const row = document.createElement('div');
            row.style.cssText = 'padding:6px 10px;background:rgba(16,185,129,0.1);border-left:3px solid #10b981;margin-bottom:4px;border-radius:4px;font-size:12px;color:var(--text-primary);';
            row.textContent = name;
            section.appendChild(row);
        });
        content.appendChild(section);
    }

    // Removed fields
    if (removed.length > 0) {
        const section = document.createElement('div');
        section.style.cssText = 'margin-bottom:14px;';
        section.innerHTML = `<div style="color:#ef4444;font-weight:600;font-size:12px;margin-bottom:6px;text-transform:uppercase;">Removed Fields</div>`;
        removed.forEach(name => {
            const row = document.createElement('div');
            row.style.cssText = 'padding:6px 10px;background:rgba(239,68,68,0.1);border-left:3px solid #ef4444;margin-bottom:4px;border-radius:4px;font-size:12px;color:var(--text-primary);';
            row.textContent = name;
            section.appendChild(row);
        });
        content.appendChild(section);
    }

    // Modified fields with per-property diffs
    if (modifiedDetails.length > 0) {
        const section = document.createElement('div');
        section.style.cssText = 'margin-bottom:14px;';
        section.innerHTML = `<div style="color:#f59e0b;font-weight:600;font-size:12px;margin-bottom:6px;text-transform:uppercase;">Modified Fields (${modifiedDetails.length})</div>`;

        modifiedDetails.forEach(({ name, diffs }) => {
            const fieldBlock = document.createElement('div');
            fieldBlock.style.cssText = 'background:rgba(245,158,11,0.08);border-left:3px solid #f59e0b;margin-bottom:6px;border-radius:4px;overflow:hidden;';

            // Field name header (clickable to expand/collapse)
            const header = document.createElement('div');
            header.style.cssText = 'padding:8px 10px;font-size:12px;font-weight:600;color:var(--text-primary);cursor:pointer;display:flex;justify-content:space-between;align-items:center;';
            header.innerHTML = `<span>${name}</span><span style="color:var(--text-secondary);font-weight:400;font-size:11px;">${diffs.length} change${diffs.length !== 1 ? 's' : ''} ▾</span>`;

            const details = document.createElement('div');
            details.style.cssText = 'padding:0 10px 8px 10px;display:none;';

            diffs.forEach(d => {
                const diffLine = document.createElement('div');
                diffLine.style.cssText = 'font-size:11px;color:var(--text-secondary);padding:2px 0 2px 12px;border-left:1px solid var(--border-color);margin-bottom:2px;';
                diffLine.textContent = d;
                details.appendChild(diffLine);
            });

            header.addEventListener('click', () => {
                const isOpen = details.style.display !== 'none';
                details.style.display = isOpen ? 'none' : 'block';
                header.querySelector('span:last-child').textContent = `${diffs.length} change${diffs.length !== 1 ? 's' : ''} ${isOpen ? '▾' : '▴'}`;
            });

            fieldBlock.appendChild(header);
            fieldBlock.appendChild(details);
            section.appendChild(fieldBlock);
        });
        content.appendChild(section);
    }

    // Empty state
    if (added.length === 0 && removed.length === 0 && modifiedDetails.length === 0) {
        content.innerHTML = '<div style="text-align:center;padding:30px;color:var(--text-secondary);font-size:13px;">No differences found — snapshots are identical.</div>';
    }

    box.appendChild(content);

    // Footer with buttons
    const footer = document.createElement('div');
    footer.style.cssText = 'display:flex;justify-content:flex-end;margin-top:16px;gap:10px;';

    if (modifiedDetails.length > 0) {
        const expandBtn = document.createElement('button');
        expandBtn.textContent = 'Expand All';
        expandBtn.className = 'action-btn btn-secondary';
        expandBtn.style.cssText = 'padding:8px 16px;font-size:12px;';
        let allExpanded = false;
        expandBtn.addEventListener('click', () => {
            allExpanded = !allExpanded;
            content.querySelectorAll('div[style*="display: none"], div[style*="display:none"], div[style*="display: block"], div[style*="display:block"]').forEach(el => {
                // Target only the detail divs (children of fieldBlocks that have padding:0)
            });
            // More reliable: toggle all detail panels
            box.querySelectorAll('.snapshot-detail-panel').forEach(el => {
                el.style.display = allExpanded ? 'block' : 'none';
            });
            expandBtn.textContent = allExpanded ? 'Collapse All' : 'Expand All';
        });
        // Tag detail panels for easier toggling
        content.querySelectorAll('div').forEach(el => {
            if (el.style.padding && el.style.padding.includes('0') && el.style.display === 'none') {
                el.classList.add('snapshot-detail-panel');
            }
        });
        footer.appendChild(expandBtn);
    }

    const closeBtn = document.createElement('button');
    closeBtn.textContent = 'Close';
    closeBtn.className = 'action-btn btn-primary';
    closeBtn.style.cssText = 'padding:8px 24px;font-size:12px;';
    closeBtn.addEventListener('click', () => overlay.remove());
    footer.appendChild(closeBtn);

    box.appendChild(footer);
    overlay.appendChild(box);
    document.body.appendChild(overlay);

    // Escape to close
    overlay.addEventListener('keydown', (e) => { if (e.key === 'Escape') overlay.remove(); });
    setTimeout(() => closeBtn.focus(), 50);
}

async function deleteSnapshot(index) {
    const snap = snapshots[index];
    if (!snap) return;

    const ok = await showConfirm(`Delete snapshot "${snap.name}"?`, '🗑️ Delete Snapshot');
    if (!ok) return;

    snapshots.splice(index, 1);
    renderSnapshotsList();
}

// ============ AI Chat Functionality ============
let chatOpen = false;
let apiKeyConfigured = false;

// Initialize chat when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    setupChatListeners();
    checkApiKey();
});

function setupChatListeners() {
    // Toggle chat panel
    const toggleBtn = document.getElementById('chatToggleBtn');
    if (toggleBtn) {
        toggleBtn.addEventListener('click', toggleChat);
    }
    
    // Save API key
    const saveKeyBtn = document.getElementById('apiKeySaveBtn');
    if (saveKeyBtn) {
        saveKeyBtn.addEventListener('click', saveApiKey);
    }
    
    // Close button in header
    const closeBtn = document.getElementById('chatCloseBtn');
    if (closeBtn) {
        closeBtn.addEventListener('click', toggleChat);
    }
    
    // Send message
    const sendBtn = document.getElementById('chatSendBtn');
    if (sendBtn) {
        sendBtn.addEventListener('click', sendChatMessage);
    }
    
    // Enter to send
    const chatInput = document.getElementById('chatInput');
    if (chatInput) {
        chatInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendChatMessage();
            }
        });
        
        // Auto-resize textarea
        chatInput.addEventListener('input', () => {
            chatInput.style.height = 'auto';
            chatInput.style.height = Math.min(chatInput.scrollHeight, 80) + 'px';
        });
    }
}

function toggleChat() {
    chatOpen = !chatOpen;
    const panel = document.getElementById('chatPanel');
    const btn = document.getElementById('chatToggleBtn');
    
    if (chatOpen) {
        panel.classList.add('open');
        btn.style.display = 'none'; // Hide button when chat is open
    } else {
        panel.classList.remove('open');
        btn.style.display = 'flex'; // Show button when chat is closed
    }
}

async function checkApiKey() {
    const hasKey = await ipcRenderer.invoke('gemini-has-api-key');
    apiKeyConfigured = hasKey;
    updateChatUI();
}

function updateChatUI() {
    const setupDiv = document.getElementById('apiKeySetup');
    const welcomeDiv = document.getElementById('chatWelcome');
    const inputArea = document.getElementById('chatInputArea');
    
    if (apiKeyConfigured) {
        setupDiv.style.display = 'none';
        welcomeDiv.style.display = 'block';
        inputArea.style.display = 'flex';
    } else {
        setupDiv.style.display = 'block';
        welcomeDiv.style.display = 'none';
        inputArea.style.display = 'none';
    }
}

function showApiKeySetup() {
    const setupDiv = document.getElementById('apiKeySetup');
    const welcomeDiv = document.getElementById('chatWelcome');
    const inputArea = document.getElementById('chatInputArea');
    
    setupDiv.style.display = 'block';
    welcomeDiv.style.display = 'none';
    inputArea.style.display = 'none';
}

async function saveApiKey() {
    const keyInput = document.getElementById('apiKeyInput');
    const key = keyInput.value.trim();
    
    if (!key) {
        addChatMessage('Please enter a valid API key.', 'error');
        return;
    }
    
    // Test the API key
    addChatMessage('Testing API key...', 'system');
    
    const result = await ipcRenderer.invoke('gemini-set-api-key', key);
    
    if (result.success) {
        apiKeyConfigured = true;
        keyInput.value = '';
        updateChatUI();
        
        // Clear messages and show welcome
        const messages = document.getElementById('chatMessages');
        messages.innerHTML = '';
        addChatMessage('✅ API key saved! I\'m ready to help you with your PDF fields.', 'system');
    } else {
        addChatMessage(`❌ API key test failed: ${result.error}`, 'error');
    }
}

async function sendChatMessage() {
    const input = document.getElementById('chatInput');
    const message = input.value.trim();
    
    if (!message) return;
    
    // Clear input
    input.value = '';
    input.style.height = 'auto';
    
    // Add user message to chat
    addChatMessage(message, 'user');
    
    // Build context about current PDF
    const context = buildPdfContext();
    
    // Show typing indicator
    const typingId = addChatMessage('Thinking...', 'system');
    
    // Send to Gemini
    const result = await ipcRenderer.invoke('gemini-chat', message, context);
    
    // Remove typing indicator
    removeMessage(typingId);
    
    if (result.error) {
        addChatMessage(`Error: ${result.error}`, 'error');
    } else if (result.type === 'action') {
        // AI wants to perform an action - show confirmation
        addAiActionMessage(result.action, result.params, result.message);
    } else {
        // Regular text response
        addChatMessage(result.response || result.message, 'assistant');
    }
    
    // Scroll to bottom
    const messages = document.getElementById('chatMessages');
    messages.scrollTop = messages.scrollHeight;
}

// Add an AI action message with Apply/Cancel buttons
function addAiActionMessage(action, params, description) {
    const messages = document.getElementById('chatMessages');
    const msgDiv = document.createElement('div');
    const msgId = `msg-${messageCounter++}`;
    msgDiv.id = msgId;
    msgDiv.className = 'chat-message action';
    
    msgDiv.innerHTML = `
        <div class="action-description">
            <span class="action-icon">🔧</span>
            <span>${description}</span>
        </div>
        <div class="action-buttons">
            <button class="action-btn-apply" data-action="${action}" data-params='${JSON.stringify(params)}'>✓ Apply</button>
            <button class="action-btn-cancel" data-msgid="${msgId}">✕ Cancel</button>
        </div>
    `;
    
    messages.appendChild(msgDiv);
    
    // Add event listeners
    msgDiv.querySelector('.action-btn-apply').addEventListener('click', async (e) => {
        const actionName = e.target.dataset.action;
        const actionParams = JSON.parse(e.target.dataset.params);
        await executeAiAction(actionName, actionParams, msgId);
    });
    
    msgDiv.querySelector('.action-btn-cancel').addEventListener('click', (e) => {
        // Replace action message with cancelled notice
        msgDiv.innerHTML = '<div class="action-cancelled">Action cancelled</div>';
        msgDiv.className = 'chat-message system';
    });
    
    messages.scrollTop = messages.scrollHeight;
}

// Execute an AI-suggested action
async function executeAiAction(action, params, msgId) {
    const msgDiv = document.getElementById(msgId);
    let success = false;
    let resultMessage = '';
    
    try {
        switch (action) {
            case 'renameField':
                success = renameFieldByName(params.fieldName, params.newName);
                resultMessage = success 
                    ? `Renamed "${params.fieldName}" to "${params.newName}"`
                    : `Could not find field "${params.fieldName}"`;
                break;
                
            case 'renameSelectedFields':
                if (selectedFields.length === 0) {
                    resultMessage = 'No fields selected';
                } else {
                    const renamed = renameSelectedFieldsWithPattern(params.newName);
                    success = renamed > 0;
                    resultMessage = `Renamed ${renamed} field(s)`;
                }
                break;
                
            case 'setFieldProperty':
                if (selectedFields.length === 0) {
                    resultMessage = 'No fields selected';
                } else {
                    const updated = setPropertyOnSelected(params.property, params.value);
                    success = updated > 0;
                    resultMessage = `Updated ${params.property} on ${updated} field(s)`;
                }
                break;
                
            case 'deleteFields':
                if (selectedFields.length === 0) {
                    resultMessage = 'No fields selected';
                } else if (params.confirm) {
                    const count = selectedFields.length;
                    deleteSelectedFields();
                    success = true;
                    resultMessage = `Deleted ${count} field(s)`;
                } else {
                    resultMessage = 'Deletion not confirmed';
                }
                break;
                
            case 'autoNameSelected':
                if (selectedFields.length === 0) {
                    resultMessage = 'No fields selected';
                } else {
                    // Trigger auto-name on selected
                    document.getElementById('btnAutoNameBatch')?.click();
                    success = true;
                    resultMessage = `Auto-naming ${selectedFields.length} field(s)...`;
                }
                break;
            
            case 'selectFields':
                {
                    let fieldsToSelect = [];
                    
                    switch (params.mode) {
                        case 'all':
                            fieldsToSelect = [...fields];
                            break;
                        case 'pattern':
                            const pattern = params.pattern.toLowerCase().replace(/\*/g, '.*');
                            const regex = new RegExp(`^${pattern}$`, 'i');
                            fieldsToSelect = fields.filter(f => regex.test(f.name));
                            break;
                        case 'type':
                            const targetType = params.fieldType.toLowerCase();
                            fieldsToSelect = fields.filter(f => f.type.toLowerCase() === targetType);
                            break;
                        case 'page':
                            const pageNum = parseInt(params.pageNumber) - 1; // 0-indexed
                            fieldsToSelect = fields.filter(f => f.page === pageNum);
                            break;
                    }
                    
                    // Clear current selection and select new fields
                    selectedFields = fieldsToSelect;
                    success = fieldsToSelect.length > 0;
                    resultMessage = success 
                        ? `Selected ${fieldsToSelect.length} field(s)` 
                        : 'No fields matched the criteria';
                }
                break;
            
            case 'setPropertyOnFilteredFields':
                {
                    let fieldsToUpdate = [];
                    
                    switch (params.filter) {
                        case 'all':
                            fieldsToUpdate = [...fields];
                            break;
                        case 'pattern':
                            const pattern = params.pattern.toLowerCase().replace(/\*/g, '.*');
                            const regex = new RegExp(`^${pattern}$`, 'i');
                            fieldsToUpdate = fields.filter(f => regex.test(f.name));
                            break;
                        case 'type':
                            const targetType = params.fieldType.toLowerCase();
                            fieldsToUpdate = fields.filter(f => f.type.toLowerCase() === targetType);
                            break;
                        case 'selected':
                            fieldsToUpdate = [...selectedFields];
                            break;
                    }
                    
                    if (fieldsToUpdate.length === 0) {
                        resultMessage = 'No fields matched the filter';
                    } else {
                        // Apply property to all filtered fields
                        let count = 0;
                        for (const field of fieldsToUpdate) {
                            // Initialize style and changes.styled if needed
                            if (!field.style) field.style = {};
                            if (!changes.styled[field.name]) changes.styled[field.name] = {};
                            
                            switch (params.property) {
                                case 'fontSize':
                                    const size = parseInt(params.value) || 12;
                                    field.fontSize = size;
                                    field.style.fontSize = size;
                                    changes.styled[field.name].fontSize = size;
                                    break;
                                case 'textAlign':
                                    field.textAlign = params.value;
                                    field.style.textAlign = params.value;
                                    changes.styled[field.name].textAlign = params.value;
                                    break;
                                case 'required':
                                    field.required = params.value === 'true' || params.value === true;
                                    break;
                                case 'readOnly':
                                    field.readOnly = params.value === 'true' || params.value === true;
                                    break;
                                case 'fontColor':
                                    field.fontColor = params.value;
                                    field.style.fontColor = params.value;
                                    changes.styled[field.name].fontColor = params.value;
                                    break;
                                case 'fillColor':
                                    field.fillColor = params.value;
                                    field.style.fillColor = params.value;
                                    changes.styled[field.name].fillColor = params.value;
                                    break;
                                case 'tooltip':
                                    field.tooltip = params.value;
                                    break;
                            }
                            count++;
                        }
                        success = count > 0;
                        resultMessage = `Updated ${params.property} to "${params.value}" on ${count} field(s)`;
                    }
                }
                break;
                
            default:
                resultMessage = `Unknown action: ${action}`;
        }
    } catch (error) {
        resultMessage = `Error: ${error.message}`;
    }
    
    // Update the message with result
    if (msgDiv) {
        msgDiv.innerHTML = `<div class="action-result ${success ? 'success' : 'error'}">
            ${success ? '✓' : '✗'} ${resultMessage}
        </div>`;
        msgDiv.className = 'chat-message system';
    }
    
    // Confirm action with AI to continue conversation
    const aiResponse = await ipcRenderer.invoke('gemini-confirm-action', action, params, success, resultMessage);
    if (aiResponse) {
        addChatMessage(aiResponse, 'assistant');
    }
    
    // Redraw if changes were made
    if (success) {
        hasUnsavedChanges = true;
        updateChangeIndicator();
        redrawCanvas();
        updatePropertiesPanel();
    }
}

// Helper: Rename a field by its name
function renameFieldByName(oldName, newName) {
    const field = fields.find(f => f.name === oldName);
    if (field) {
        field.name = newName;
        return true;
    }
    return false;
}

// Helper: Rename selected fields with pattern (supports {n} for numbering)
function renameSelectedFieldsWithPattern(pattern) {
    let count = 0;
    selectedFields.forEach((field, index) => {
        let newName = pattern.replace('{n}', index + 1);
        field.name = newName;
        count++;
    });
    return count;
}

// Helper: Set a property on all selected fields
function setPropertyOnSelected(property, value) {
    let count = 0;
    for (const field of selectedFields) {
        // Initialize style and changes.styled if needed
        if (!field.style) field.style = {};
        if (!changes.styled[field.name]) changes.styled[field.name] = {};
        
        switch (property) {
            case 'fontSize':
                const size = parseInt(value) || 12;
                field.fontSize = size;
                field.style.fontSize = size;
                changes.styled[field.name].fontSize = size;
                break;
            case 'textAlign':
                field.textAlign = value;
                field.style.textAlign = value;
                changes.styled[field.name].textAlign = value;
                break;
            case 'required':
                field.required = value === 'true' || value === true;
                break;
            case 'readOnly':
                field.readOnly = value === 'true' || value === true;
                break;
            case 'fontColor':
                field.fontColor = value;
                field.style.fontColor = value;
                changes.styled[field.name].fontColor = value;
                break;
            case 'fillColor':
                field.fillColor = value;
                field.style.fillColor = value;
                changes.styled[field.name].fillColor = value;
                break;
            case 'tooltip':
                field.tooltip = value;
                break;
        }
        count++;
    }
    return count;
}

function buildPdfContext() {
    return {
        pdfPath: pdfPath,
        totalFields: fields.length,
        fieldTypes: countFieldTypes(),
        fields: fields.map(f => ({
            name: f.name,
            type: f.type,
            page: f.page + 1,
            rect: f.rect.map(Math.round)
        })),
        selectedFields: selectedFields.map(f => ({
            name: f.name,
            type: f.type,
            page: f.page + 1,
            rect: f.rect.map(Math.round)
        })),
        currentPage: currentPage + 1,
        totalPages: pages.length,
        hasUnsavedChanges: hasUnsavedChanges
    };
}

function countFieldTypes() {
    const counts = {};
    for (const field of fields) {
        counts[field.type] = (counts[field.type] || 0) + 1;
    }
    return counts;
}

let messageCounter = 0;

function addChatMessage(text, type) {
    const messages = document.getElementById('chatMessages');
    const msgDiv = document.createElement('div');
    const msgId = `msg-${messageCounter++}`;
    msgDiv.id = msgId;
    msgDiv.className = `chat-message ${type}`;
    
    // Simple markdown-like formatting for assistant messages
    if (type === 'assistant') {
        text = text
            .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
            .replace(/\*(.*?)\*/g, '<em>$1</em>')
            .replace(/`([^`]+)`/g, '<code style="background: rgba(0,0,0,0.2); padding: 2px 4px; border-radius: 3px;">$1</code>')
            .replace(/\n/g, '<br>');
        msgDiv.innerHTML = text;
    } else {
        msgDiv.textContent = text;
    }
    
    messages.appendChild(msgDiv);
    messages.scrollTop = messages.scrollHeight;
    
    return msgId;
}

function removeMessage(msgId) {
    const msg = document.getElementById(msgId);
    if (msg) msg.remove();
}

// ============ Custom Color Picker ============
(function initColorPicker() {
    let colorPickerTarget = null;
    let currentR = 0, currentG = 0, currentB = 0, currentA = 100;
    
    const popup = document.getElementById('colorPickerPopup');
    if (!popup) return; // No popup in DOM
    
    const tabs = document.querySelectorAll('.color-picker-tab');
    const rgbPane = document.getElementById('colorModeRGB');
    const cmykPane = document.getElementById('colorModeCMYK');
    const grayPane = document.getElementById('colorModeGray');
    
    const sliderR = document.getElementById('cpSliderR');
    const sliderG = document.getElementById('cpSliderG');
    const sliderB = document.getElementById('cpSliderB');
    const sliderA = document.getElementById('cpSliderA');
    const sliderC = document.getElementById('cpSliderC');
    const sliderM = document.getElementById('cpSliderM');
    const sliderY = document.getElementById('cpSliderY');
    const sliderK = document.getElementById('cpSliderK');
    const sliderGray = document.getElementById('cpSliderGray');
    const preview = document.getElementById('cpPreview');
    const hexInput = document.getElementById('cpHexInput');
    const transparentCheckbox = document.getElementById('cpTransparent'); // Added constant
    
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
        return {
            r: Math.round(255 * (1 - c / 100) * (1 - k / 100)),
            g: Math.round(255 * (1 - m / 100) * (1 - k / 100)),
            b: Math.round(255 * (1 - y / 100) * (1 - k / 100))
        };
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
            if (preview) {
                preview.style.background = 'transparent';
                // Add checkerboard pattern to visualize transparency better
                preview.style.backgroundImage = 'linear-gradient(45deg, #ccc 25%, transparent 25%), linear-gradient(-45deg, #ccc 25%, transparent 25%), linear-gradient(45deg, transparent 75%, #ccc 75%), linear-gradient(-45deg, transparent 75%, #ccc 75%)';
                preview.style.backgroundSize = '10px 10px';
                preview.style.backgroundPosition = '0 0, 0 5px, 5px -5px, -5px 0px';
            }
            if (hexInput) hexInput.value = 'transparent';
        } else {
            if (preview) {
                preview.style.background = `rgba(${currentR}, ${currentG}, ${currentB}, ${currentA / 100})`;
                preview.style.backgroundImage = 'none';
            }
            if (hexInput) hexInput.value = rgbToHex(currentR, currentG, currentB);
        }
    }
    
    function updateAllSliders() {
        if (sliderR) sliderR.value = currentR;
        if (sliderG) sliderG.value = currentG;
        if (sliderB) sliderB.value = currentB;
        if (sliderA) sliderA.value = currentA;
        const valR = document.getElementById('cpValueR');
        const valG = document.getElementById('cpValueG');
        const valB = document.getElementById('cpValueB');
        const valA = document.getElementById('cpValueA');
        if (valR) valR.textContent = currentR;
        if (valG) valG.textContent = currentG;
        if (valB) valB.textContent = currentB;
        if (valA) valA.textContent = currentA + '%';
        
        const cmyk = rgbToCmyk(currentR, currentG, currentB);
        if (sliderC) sliderC.value = cmyk.c;
        if (sliderM) sliderM.value = cmyk.m;
        if (sliderY) sliderY.value = cmyk.y;
        if (sliderK) sliderK.value = cmyk.k;
        const valC = document.getElementById('cpValueC');
        const valM = document.getElementById('cpValueM');
        const valY = document.getElementById('cpValueY');
        const valK = document.getElementById('cpValueK');
        if (valC) valC.textContent = cmyk.c + '%';
        if (valM) valM.textContent = cmyk.m + '%';
        if (valY) valY.textContent = cmyk.y + '%';
        if (valK) valK.textContent = cmyk.k + '%';
        
        const gray = rgbToGray(currentR, currentG, currentB);
        if (sliderGray) sliderGray.value = gray;
        const valGray = document.getElementById('cpValueGray');
        if (valGray) valGray.textContent = gray + '%';
        
        updatePreview();
    }
    
    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            tabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            const mode = tab.dataset.mode;
            if (rgbPane) rgbPane.style.display = mode === 'rgb' ? 'block' : 'none';
            if (cmykPane) cmykPane.style.display = mode === 'cmyk' ? 'block' : 'none';
            if (grayPane) grayPane.style.display = mode === 'gray' ? 'block' : 'none';
        });
    });
    
    [sliderR, sliderG, sliderB].forEach((slider, i) => {
        if (slider) slider.addEventListener('input', () => {
            if (i === 0) currentR = parseInt(slider.value);
            if (i === 1) currentG = parseInt(slider.value);
            if (i === 2) currentB = parseInt(slider.value);
            updateAllSliders();
        });
    });
    
    if (sliderA) sliderA.addEventListener('input', () => {
        currentA = parseInt(sliderA.value);
        updateAllSliders();
    });
    
    [sliderC, sliderM, sliderY, sliderK].forEach(slider => {
        if (slider) slider.addEventListener('input', () => {
            const rgb = cmykToRgb(parseInt(sliderC.value), parseInt(sliderM.value), parseInt(sliderY.value), parseInt(sliderK.value));
            currentR = rgb.r; currentG = rgb.g; currentB = rgb.b;
            updateAllSliders();
        });
    });
    
    if (sliderGray) sliderGray.addEventListener('input', () => {
        const rgb = grayToRgb(parseInt(sliderGray.value));
        currentR = rgb.r; currentG = rgb.g; currentB = rgb.b;
        updateAllSliders();
    });
    
    if (hexInput) hexInput.addEventListener('change', () => {
        if (hexInput.value.toLowerCase() === 'transparent') {
            if (transparentCheckbox) {
                transparentCheckbox.checked = true;
                // Dispatch event to update UI
                transparentCheckbox.dispatchEvent(new Event('change'));
            }
            return;
        }
        const rgb = hexToRgb(hexInput.value);
        if (rgb) { 
            currentR = rgb.r; currentG = rgb.g; currentB = rgb.b; 
            if (transparentCheckbox && transparentCheckbox.checked) {
                transparentCheckbox.checked = false;
                transparentCheckbox.dispatchEvent(new Event('change'));
            }
            updateAllSliders(); 
        }
    });

    // Transparent Checkbox Listener
    if (transparentCheckbox) {
        transparentCheckbox.addEventListener('change', () => {
            const isTransparent = transparentCheckbox.checked;
            
            // Disable/Enable inputs based on transparency
            const inputs = [sliderR, sliderG, sliderB, sliderA, sliderC, sliderM, sliderY, sliderK, sliderGray, hexInput];
            inputs.forEach(input => {
                if (input && input !== hexInput) input.disabled = isTransparent;
            });
            
            // opacity slider specific visual feedback
            if (sliderA && sliderA.parentElement) {
                sliderA.parentElement.style.opacity = isTransparent ? '0.5' : '1';
            }

            updatePreview();
        });
    }
    
    document.querySelectorAll('.color-swatch-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const targetId = btn.dataset.target;
            const label = btn.dataset.label || 'Select Color';
            colorPickerTarget = targetId;
            
            const currentVal = document.getElementById(targetId)?.value || '#000000';
            
            // Check for transparent value
            const isTransparent = currentVal === 'transparent' || currentVal === '';
            if (transparentCheckbox) {
                transparentCheckbox.checked = isTransparent;
                // Trigger change to update UI state
                transparentCheckbox.dispatchEvent(new Event('change'));
            }

            const rgb = hexToRgb(isTransparent ? '#000000' : currentVal);
            if (rgb) { currentR = rgb.r; currentG = rgb.g; currentB = rgb.b; }
            
            // Initialize alpha from globalFieldSettings for fill color
            if (targetId === 'propFillColor' && window.globalFieldSettings?.fillOpacity !== undefined) {
                currentA = window.globalFieldSettings.fillOpacity;
            } else {
                currentA = 100; // Default for other color pickers
            }
            // If transparent, we can set alpha to 0 for consistency, or leave it to revert when unchecked
            if (isTransparent) currentA = 0;
            
            const labelEl = document.getElementById('colorPickerLabel');
            if (labelEl) labelEl.textContent = label;
            updateAllSliders();
            
            // Position popup centered on screen
            const popupWidth = 280;
            const popupHeight = 420;
            popup.style.left = `${(window.innerWidth - popupWidth) / 2}px`;
            popup.style.top = `${(window.innerHeight - popupHeight) / 2}px`;
            popup.style.display = 'block';
        });
    });
    
    document.getElementById('colorPickerClose')?.addEventListener('click', () => {
        popup.style.display = 'none';
        colorPickerTarget = null;
    });
    
    document.getElementById('cpApply')?.addEventListener('click', () => {
        if (colorPickerTarget) {
            let hex;
            if (transparentCheckbox && transparentCheckbox.checked) {
                hex = 'transparent'; // Use special keyword
            } else {
                hex = rgbToHex(currentR, currentG, currentB);
            }
            
            const input = document.getElementById(colorPickerTarget);
            if (input) input.value = hex;
            
            const preview = document.getElementById('preview_' + colorPickerTarget);
            if (preview) {
                if (hex === 'transparent') {
                    preview.style.background = 'transparent';
                    // Checkerboard
                    preview.style.backgroundImage = 'linear-gradient(45deg, #ccc 25%, transparent 25%), linear-gradient(-45deg, #ccc 25%, transparent 25%), linear-gradient(45deg, transparent 75%, #ccc 75%), linear-gradient(-45deg, transparent 75%, #ccc 75%)';
                    preview.style.backgroundSize = '10px 10px';
                } else {
                    preview.style.background = hex;
                    preview.style.backgroundImage = 'none';
                }
            }
             
            const text = document.getElementById('text_' + colorPickerTarget);
            if (text) text.textContent = hex;
            
            // Trigger change event to update field data
            if (input) input.dispatchEvent(new Event('change', { bubbles: true }));
        }
        popup.style.display = 'none';
        colorPickerTarget = null;
    });
    
    document.addEventListener('click', (e) => {
        if (popup.style.display === 'block' && !popup.contains(e.target) && !e.target.closest('.color-swatch-btn')) {
            popup.style.display = 'none';
            colorPickerTarget = null;
        }
    });
})();

/**
 * Dropdown Option Management
 */
function renderChoiceList(field, containerId, prefix) {
    const container = document.getElementById(containerId);
    if (!container) return;

    const options = field.options || [];
    container.innerHTML = options.map((opt, idx) => {
        // Handle both string format (old) and object format (new)
        const label = typeof opt === 'string' ? opt : (opt.label || '');
        const value = typeof opt === 'string' ? opt : (opt.value || label);
        return `
            <div class="option-item ${field._selectedOptionIdx === idx ? 'selected' : ''}" 
                 onclick="selectChoiceOption('${field.name}', ${idx}, '${containerId}', '${prefix}')">
                <span class="option-label">${label}</span>
                <span class="option-value">${value !== label ? value : ''}</span>
            </div>
        `;
    }).join('');
}

window.selectChoiceOption = function(fieldName, idx, containerId, prefix) {
    const field = fields.find(f => f.name === fieldName);
    if (!field) return;

    field._selectedOptionIdx = idx;
    
    // Update visual selection without full re-render
    const container = document.getElementById(containerId);
    if (container) {
        const items = container.querySelectorAll('.option-item');
        items.forEach((item, i) => {
            item.classList.toggle('selected', i === idx);
        });
    }

    // Populate inputs - handle both string and object formats
    const opt = field.options ? field.options[idx] : null;
    if (!opt) return;
    
    const label = typeof opt === 'string' ? opt : (opt.label || '');
    const value = typeof opt === 'string' ? opt : (opt.value || label);
    
    const nameInput = document.getElementById(`${prefix}DropdownItemName`);
    const valInput = document.getElementById(`${prefix}DropdownExportValue`);
    if (nameInput) nameInput.value = label;
    if (valInput) valInput.value = value;
};

async function handleChoiceOptionAction(action, prefix) {
    const field = (prefix === 'modalProp') ? modalField : (selectedFields.length === 1 ? selectedFields[0] : null);
    if (!field) return;

    if (!field.options) field.options = [];
    const idx = field._selectedOptionIdx ?? -1;

    if (action === 'add') {
        const nameInput = document.getElementById(`${prefix}DropdownItemName`);
        const valInput = document.getElementById(`${prefix}DropdownExportValue`);
        const label = nameInput.value.trim();
        const value = valInput.value.trim() || label;
        
        if (!label) return;
        
        field.options.push({ label, value });
        nameInput.value = '';
        valInput.value = '';
        field._selectedOptionIdx = field.options.length - 1;
    } else if (action === 'delete') {
        if (idx >= 0) {
            field.options.splice(idx, 1);
            field._selectedOptionIdx = field.options.length > 0 ? Math.min(idx, field.options.length - 1) : -1;
        }
    } else if (action === 'up') {
        if (idx > 0) {
            const temp = field.options[idx];
            field.options[idx] = field.options[idx - 1];
            field.options[idx - 1] = temp;
            field._selectedOptionIdx = idx - 1;
        }
    } else if (action === 'down') {
        if (idx >= 0 && idx < field.options.length - 1) {
            const temp = field.options[idx];
            field.options[idx] = field.options[idx + 1];
            field.options[idx + 1] = temp;
            field._selectedOptionIdx = idx + 1;
        }
    } else if (action === 'save') {
        // Trigger manual sync
        if (prefix === 'prop') {
             if (!changes.styled[field.name]) changes.styled[field.name] = {};
             changes.styled[field.name].options = [...field.options];
             markUnsaved();
        }
    }

    renderChoiceList(field, `${prefix}DropdownList`, prefix);

    // For sidebar, mark unsaved immediately if it's a side-edit
    if (prefix === 'prop' && action !== 'save') {
        if (!changes.styled[field.name]) changes.styled[field.name] = {};
        changes.styled[field.name].options = [...field.options];
        markUnsaved();
    }
}

// ============ Calculations Feature ============

// Track current calculation sources for the Calculate tab
let calcSources = [];

function initCalculateTab() {
    const calcType = document.getElementById('modalCalcType');
    const calcFieldSearch = document.getElementById('calcFieldSearch');
    const btnAddCanvas = document.getElementById('btnAddCanvasSelection');
    const btnRemoveCalc = document.getElementById('btnRemoveCalc');
    const calcFormula = document.getElementById('calcCustomFormula');
    const calcDecimals = document.getElementById('calcDecimals');
    const calcRound = document.getElementById('calcRoundResult');
    const calcReadOnly = document.getElementById('calcReadOnly');

    if (!calcType) return;

    // Type change handler
    calcType.addEventListener('change', () => {
        calcDirty = true;
        const type = calcType.value;
        const sourceSection = document.getElementById('calcSourceSection');
        const customSection = document.getElementById('calcCustomSection');
        const constantSection = document.getElementById('calcConstantSection');
        const optionsSection = document.getElementById('calcOptionsSection');
        const previewSection = document.getElementById('calcPreviewSection');
        const removeSection = document.getElementById('calcRemoveSection');

        if (type) {
            sourceSection.style.display = 'block';
            optionsSection.style.display = 'block';
            previewSection.style.display = 'block';
            removeSection.style.display = 'block';
            customSection.style.display = type === 'CUSTOM' ? 'block' : 'none';
            const scriptSection = document.getElementById('calcCustomScriptSection');
            if (scriptSection) scriptSection.style.display = type === 'CUSTOM_SCRIPT' ? 'block' : 'none';
            constantSection.style.display = type === 'CONSTANT_MULTIPLY' ? 'block' : 'none';
        } else {
            sourceSection.style.display = 'none';
            customSection.style.display = 'none';
            constantSection.style.display = 'none';
            optionsSection.style.display = 'none';
            previewSection.style.display = 'none';
            removeSection.style.display = 'none';
        }
        updateCalcPreview();
    });

    // Field search handler (multi-select with checkboxes)
    calcFieldSearch.addEventListener('input', () => {
        const query = calcFieldSearch.value.toLowerCase().trim();
        const resultsDiv = document.getElementById('calcFieldSearchResults');
        if (query.length < 1) {
            resultsDiv.style.display = 'none';
            return;
        }
        const currentFieldName = selectedFields.length === 1 ? selectedFields[0].name : '';
        const matches = fields.filter(f =>
            f.name.toLowerCase().includes(query) &&
            f.name !== currentFieldName &&
            !calcSources.find(s => s.name === f.name)
        ).slice(0, 30);

        if (matches.length === 0) {
            resultsDiv.style.display = 'none';
            return;
        }

        resultsDiv.innerHTML =
            `<div class="calc-search-actions" style="position:sticky;top:0;z-index:1;display:flex;gap:6px;padding:4px 8px;border-bottom:1px solid #333;align-items:center;background:#1e1e1e;">
                <button id="calcSearchAddSelected" style="padding:3px 10px;font-size:11px;background:#4CAF50;color:#fff;border:none;border-radius:3px;cursor:pointer;">Add Selected</button>
                <button id="calcSearchSelectAll" style="padding:3px 10px;font-size:11px;background:#555;color:#fff;border:none;border-radius:3px;cursor:pointer;">Select All</button>
                <span id="calcSearchSelCount" style="font-size:11px;color:#888;margin-left:auto;">0 selected</span>
            </div>
            <div class="calc-search-list" style="max-height:250px;overflow-y:auto;">` +
            matches.map(f =>
                `<div class="calc-search-item" data-field-name="${f.name}" data-field-page="${f.page}" style="display:flex;align-items:center;gap:6px;">
                    <input type="checkbox" class="calc-search-cb" data-field-name="${f.name}" data-field-page="${f.page}" style="margin:0;cursor:pointer;flex-shrink:0;">
                    <span style="flex:1;overflow:hidden;text-overflow:ellipsis;">${f.name}</span>
                    <span class="page-label">P${(f.page || 0) + 1}</span>
                </div>`
            ).join('') + `</div>`;
        resultsDiv.style.display = 'block';

        const updateSelCount = () => {
            const cnt = resultsDiv.querySelectorAll('.calc-search-cb:checked').length;
            const label = document.getElementById('calcSearchSelCount');
            if (label) label.textContent = cnt + ' selected';
        };

        // Clicking a row toggles its checkbox
        resultsDiv.querySelectorAll('.calc-search-item').forEach(item => {
            item.addEventListener('click', (e) => {
                if (e.target.tagName === 'INPUT') { updateSelCount(); return; }
                const cb = item.querySelector('.calc-search-cb');
                if (cb) { cb.checked = !cb.checked; updateSelCount(); }
            });
        });

        // Add Selected button
        document.getElementById('calcSearchAddSelected')?.addEventListener('click', (e) => {
            e.stopPropagation();
            const checked = resultsDiv.querySelectorAll('.calc-search-cb:checked');
            checked.forEach(cb => {
                addCalcSource(cb.dataset.fieldName, parseInt(cb.dataset.fieldPage) || 0);
            });
            calcFieldSearch.value = '';
            resultsDiv.style.display = 'none';
        });

        // Select All button
        document.getElementById('calcSearchSelectAll')?.addEventListener('click', (e) => {
            e.stopPropagation();
            const cbs = resultsDiv.querySelectorAll('.calc-search-cb');
            const allChecked = Array.from(cbs).every(cb => cb.checked);
            cbs.forEach(cb => cb.checked = !allChecked);
            updateSelCount();
        });
    });

    // Prevent clicks inside the results dropdown from triggering blur on the search input
    document.getElementById('calcFieldSearchResults').addEventListener('mousedown', (e) => {
        e.preventDefault();
    });

    // Close search results on blur (only fires when clicking OUTSIDE the dropdown)
    calcFieldSearch.addEventListener('blur', () => {
        setTimeout(() => {
            document.getElementById('calcFieldSearchResults').style.display = 'none';
        }, 200);
    });

    // Add canvas selection button — uses current canvas selection (panel is non-blocking)
    btnAddCanvas.addEventListener('click', () => {
        const targetName = modalField ? modalField.name : '';
        // First try current canvas selection
        const selectionToUse = selectedFields.length > 0 ? selectedFields : (window._preModalSelection || []);
        for (const f of selectionToUse) {
            if (f.name !== targetName && !calcSources.find(s => s.name === f.name)) {
                addCalcSource(f.name, f.page || 0);
            }
        }
    });

    // Pick-from-canvas toggle button
    const btnPick = document.getElementById('btnPickFromCanvas');
    if (btnPick) {
        btnPick.dataset.defaultText = '🎯 Click Fields on Canvas to Add';
        btnPick.addEventListener('click', () => {
            if (isPickModeActive()) {
                disablePickMode();
            } else {
                pickMode = {
                    btnId: 'btnPickFromCanvas',
                    onPick: (field) => {
                        addCalcSource(field.name, field.page || 0);
                    },
                    onDisable: null,
                };
                btnPick.classList.add('active');
                btnPick.textContent = '🎯 Picking... (click fields, then click here to stop)';
                if (canvas) canvas.classList.add('canvas-pick-mode');
            }
        });
    }

    // Remove calculation button
    btnRemoveCalc.addEventListener('click', () => {
        calcType.value = '';
        calcSources = [];
        renderCalcSources();
        calcType.dispatchEvent(new Event('change'));
        // Remove from changes — use modalField (the field the panel is editing)
        const targetField = modalField || (selectedFields.length === 1 ? selectedFields[0] : null);
        if (targetField) {
            delete changes.calculations[targetField.name];
            if (targetField.calculation) delete targetField.calculation;
            markUnsaved();
            refreshCalcManagerTable();
        }
    });

    // Custom formula change
    if (calcFormula) {
        calcFormula.addEventListener('input', () => { calcDirty = true; updateCalcPreview(); });
    }

    // Constant multiplier change
    const calcConstantValue = document.getElementById('calcConstantValue');
    if (calcConstantValue) {
        calcConstantValue.addEventListener('input', () => { calcDirty = true; updateCalcPreview(); });
    }

    // Decimals / rounding changes
    if (calcDecimals) calcDecimals.addEventListener('input', () => { calcDirty = true; updateCalcPreview(); });
    if (calcRound) calcRound.addEventListener('change', () => { calcDirty = true; updateCalcPreview(); });

    // Read-only toggle
    if (calcReadOnly) calcReadOnly.addEventListener('change', () => { calcDirty = true; });
}

function addCalcSource(name, page) {
    if (calcSources.find(s => s.name === name)) return;
    calcSources.push({ name, page });
    calcDirty = true;
    renderCalcSources();
    updateCalcPreview();
}

function removeCalcSource(name) {
    calcSources = calcSources.filter(s => s.name !== name);
    calcDirty = true;
    renderCalcSources();
    updateCalcPreview();
}

function renderCalcSources() {
    const list = document.getElementById('calcSourceList');
    if (!list) return;

    if (calcSources.length === 0) {
        list.innerHTML = '<div class="calc-empty-msg">No source fields added yet</div>';
    } else {
        list.innerHTML = calcSources.map((s, i) => {
            const letter = String.fromCharCode(65 + i);
            return `<div class="calc-source-item">
                <span class="source-letter">${letter}</span>
                <span class="source-name">${s.name}</span>
                <span class="source-page">P${(s.page || 0) + 1}</span>
                <span class="source-remove" onclick="removeCalcSource('${s.name.replace(/'/g, "\\'")}')">&times;</span>
            </div>`;
        }).join('');
    }

    // Update formula legend
    const legend = document.getElementById('calcFormulaLegend');
    if (legend) {
        legend.innerHTML = calcSources.map((s, i) => {
            const letter = String.fromCharCode(65 + i);
            return `<span class="legend-item"><span class="legend-letter">${letter}</span> = ${s.name}</span>`;
        }).join('<br>');
    }
}

function updateCalcPreview() {
    const calcType = document.getElementById('modalCalcType');
    const previewSection = document.getElementById('calcPreviewSection');
    if (!calcType || !previewSection) return;

    const type = calcType.value;
    if (!type || calcSources.length === 0) {
        previewSection.style.display = 'none';
        return;
    }
    previewSection.style.display = 'block';

    const formulaPreview = document.getElementById('calcFormulaPreview');
    const testInputs = document.getElementById('calcTestInputs');
    const testResult = document.getElementById('calcTestResult');

    // Build human-readable formula
    const names = calcSources.map(s => s.name);
    const opSymbol = { SUM: '+', SUBTRACT: '-', MULTIPLY: '×', DIVIDE: '÷', PRODUCT: '×', AVERAGE: 'avg', MIN: 'min', MAX: 'max', CONSTANT_MULTIPLY: '×' };
    let formulaText = '';
    if (type === 'CUSTOM') {
        const formula = document.getElementById('calcCustomFormula').value || '...';
        formulaText = `Custom: ${formula}`;
    } else if (type === 'CUSTOM_SCRIPT') {
        formulaText = `Custom Script (Live Test N/A)`;
    } else if (type === 'CONSTANT_MULTIPLY') {
        const constVal = document.getElementById('calcConstantValue').value || '?';
        formulaText = `${constVal} × ${names.length === 1 ? names[0] : '(' + names.join(' × ') + ')'}`;
    } else if (['SUM', 'AVERAGE', 'MIN', 'MAX'].includes(type)) {
        formulaText = `${type}(${names.join(', ')})`;
    } else {
        formulaText = names.join(` ${opSymbol[type] || '?'} `);
    }
    formulaPreview.textContent = formulaText;

    // Build test inputs
    const op = opSymbol[type] || '?';
    testInputs.innerHTML = calcSources.map((s, i) => {
        const letter = String.fromCharCode(65 + i);
        const separator = i < calcSources.length - 1 ?
            `<span class="test-operator">${['SUM', 'AVERAGE', 'MIN', 'MAX', 'CUSTOM'].includes(type) ? ',' : op}</span>` : '';
        return `<div class="test-input-group">
            <span class="test-label">${letter}</span>
            <input type="number" class="test-input" id="calcTest_${i}" value="1" oninput="runCalcTest()">
            ${separator}
        </div>`;
    }).join('');

    // Run initial test
    runCalcTest();
}

function runCalcTest() {
    const calcType = document.getElementById('modalCalcType');
    const testResult = document.getElementById('calcTestResult');
    if (!calcType || !testResult) return;

    const type = calcType.value;
    const values = calcSources.map((_, i) => {
        const input = document.getElementById(`calcTest_${i}`);
        return input ? parseFloat(input.value) || 0 : 0;
    });

    let result = 0;
    try {
        switch (type) {
            case 'SUM': result = values.reduce((a, b) => a + b, 0); break;
            case 'SUBTRACT': result = values.length > 0 ? values[0] - values.slice(1).reduce((a, b) => a + b, 0) : 0; break;
            case 'MULTIPLY': case 'PRODUCT': result = values.reduce((a, b) => a * b, 1); break;
            case 'DIVIDE': {
                const divisor = values.slice(1).reduce((a, b) => a * b, 1);
                result = divisor !== 0 ? values[0] / divisor : 0;
                break;
            }
            case 'AVERAGE': result = values.length > 0 ? values.reduce((a, b) => a + b, 0) / values.length : 0; break;
            case 'MIN': result = values.length > 0 ? Math.min(...values) : 0; break;
            case 'MAX': result = values.length > 0 ? Math.max(...values) : 0; break;
            case 'CONSTANT_MULTIPLY': {
                const constVal = parseFloat(document.getElementById('calcConstantValue').value) || 1;
                result = constVal * values.reduce((a, b) => a * b, 1);
                break;
            }
            case 'CUSTOM': {
                const formula = document.getElementById('calcCustomFormula').value;
                if (formula) {
                    // Replace letters with values
                    let expr = formula;
                    calcSources.forEach((_, i) => {
                        const letter = String.fromCharCode(65 + i);
                        expr = expr.replace(new RegExp(`\\b${letter}\\b`, 'g'), values[i]);
                    });
                    result = Function('"use strict"; return (' + expr + ')')();
                }
                break;
            }
            case 'CUSTOM_SCRIPT': {
                result = 'N/A';
                break;
            }
        }
    } catch (e) {
        testResult.textContent = 'Error';
        testResult.style.color = '#e94560';
        return;
    }

    const roundCheck = document.getElementById('calcRoundResult');
    const decimalsInput = document.getElementById('calcDecimals');
    if (roundCheck && roundCheck.checked && decimalsInput) {
        const d = parseInt(decimalsInput.value) || 2;
        result = parseFloat(result.toFixed(d));
    }

    testResult.textContent = isFinite(result) ? result : 'Error';
    testResult.style.color = isFinite(result) ? 'var(--accent)' : '#e94560';
}

// Save calculation from modal to changes
function saveCalcFromModal(fieldName) {
    // Only save/delete if user actually modified the Calculate tab this session.
    // This prevents auto-save from accidentally erasing calculations on other
    // fields when the modal UI shows a different field's (or empty) calc state.
    if (!calcDirty) return;

    const calcType = document.getElementById('modalCalcType');
    if (!calcType || !calcType.value || calcSources.length === 0) {
        // No calculation — remove any existing
        delete changes.calculations[fieldName];
        return;
    }

    const calc = {
        type: calcType.value,
        sources: calcSources.map(s => s.name)
    };

    if (calcType.value === 'CUSTOM') {
        // Ensure formula is simplified letter-reference notation, not raw JS
        const rawFormula = document.getElementById('calcCustomFormula').value || '';
        calc.formula = simplifyCalcFormula(rawFormula, calc.sources, fieldName);
    }

    if (calcType.value === 'CUSTOM_SCRIPT') {
        calc.script = document.getElementById('calcCustomScriptCode').value || '';
    }

    if (calcType.value === 'CONSTANT_MULTIPLY') {
        calc.constant = parseFloat(document.getElementById('calcConstantValue').value) || 1;
    }

    const roundCheck = document.getElementById('calcRoundResult');
    const decimalsInput = document.getElementById('calcDecimals');
    if (roundCheck && roundCheck.checked && decimalsInput) {
        calc.decimals = parseInt(decimalsInput.value) || 2;
    }

    const readOnlyCheck = document.getElementById('calcReadOnly');
    if (readOnlyCheck) {
        calc.readOnly = readOnlyCheck.checked;
    }

    changes.calculations[fieldName] = calc;

    // Also store on the field object for display
    const field = fields.find(f => f.name === fieldName);
    if (field) {
        field.calculation = calc;
    }

    markUnsaved();
}

// Simplify a raw JS calc formula to user-friendly letter-reference notation.
// Handles BVCALC markers, AFMakeNumber/getField wrappers, and Number() wrappers.
function simplifyCalcFormula(formula, sources, fieldName) {
    if (!formula || !sources || sources.length === 0) return formula;

    let simplified = formula;

    // 1. Check for BVCALC markers: /** BVCALC simplified_formula EVCALC **/
    const bvMatch = simplified.match(/\/\*\*\s*BVCALC\s+([\s\S]*?)\s+EVCALC\s*\*\*\//);
    if (bvMatch) {
        simplified = bvMatch[1].trim();
    } else if (simplified.includes('event.value')) {
        // 2. Extract expression after event.value =
        const evMatch = simplified.match(/event\.value\s*=\s*([\s\S]+?)(?:;|\s*$)/);
        if (evMatch) {
            simplified = evMatch[1].trim();
        }
        // Strip AFMakeNumber(getField("name").value)
        simplified = simplified.replace(/AFMakeNumber\s*\(\s*(?:this\.)?getField\s*\(\s*"([^"]+)"\s*\)\s*\.value\s*\)/g, '$1');
        // Strip Number(this.getField("name").value)
        simplified = simplified.replace(/Number\s*\(\s*(?:this\.)?getField\s*\(\s*"([^"]+)"\s*\)\s*\.value\s*\)/g, '$1');
        // Strip bare getField("name").value
        simplified = simplified.replace(/(?:this\.)?getField\s*\(\s*"([^"]+)"\s*\)\s*\.value/g, '$1');
    }

    // 3. Convert field names to letter references (A, B, C...)
    //    Sort by name length descending to avoid partial replacements
    const sorted = sources.map((name, i) => ({ name, letter: String.fromCharCode(65 + i) }))
        .sort((a, b) => b.name.length - a.name.length);
    for (const { name, letter } of sorted) {
        const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        simplified = simplified.replace(new RegExp('\\b' + escaped + '\\b', 'g'), letter);
    }

    // 4. Auto-fix missing * operators (common in source PDFs):
    //    A (B  →  A * (B     and    ) (B  →  ) * (B
    //    )A    →  ) * A      and    2(A   →  2 * (A
    simplified = simplified.replace(/([A-Z0-9)])\s*\(/g, '$1 * (');
    simplified = simplified.replace(/\)\s*([A-Z0-9])/g, ') * $1');

    // 5. Auto-fix unbalanced parentheses (drop extra ')' / append missing ')')
    let balanced = '';
    let depth = 0;
    for (const ch of simplified) {
        if (ch === '(') { depth++; balanced += ch; }
        else if (ch === ')') { if (depth > 0) { depth--; balanced += ch; } }
        else { balanced += ch; }
    }
    balanced += ')'.repeat(depth);
    simplified = balanced;

    // 6. Auto-strip self-references: a field referencing itself always reads
    //    its own stale value, producing wrong results (usually 0).
    if (fieldName && sources.includes(fieldName)) {
        const selfIdx = sources.indexOf(fieldName);
        const selfLetter = String.fromCharCode(65 + selfIdx);
        const nOriginal = sources.length;
        // Remove self from sources (mutate in place so caller sees the fix)
        sources.splice(selfIdx, 1);
        // Replace self-ref letter with multiplicative identity (1)
        simplified = simplified.replace(
            new RegExp('(?<![A-Za-z])' + selfLetter + '(?![A-Za-z])', 'g'), '1');
        // Clean up: (1) → 1, 1 * X → X, X * 1 → X
        for (let pass = 0; pass < 3; pass++) {
            simplified = simplified.replace(/\(1\)/g, '1');
            simplified = simplified.replace(/1\s*\*\s*/g, '');
            simplified = simplified.replace(/\s*\*\s*1(?![0-9])/g, '');
        }
        simplified = simplified.trim();
        // Re-letter remaining sources (shift letters above selfIdx down by 1)
        for (let i = selfIdx + 1; i < nOriginal; i++) {
            const oldL = String.fromCharCode(65 + i);
            const newL = String.fromCharCode(65 + i - 1);
            simplified = simplified.replace(
                new RegExp('(?<![A-Za-z])' + oldL + '(?![A-Za-z])', 'g'), newL);
        }
    }

    return simplified;
}

// Load calculation into modal when opening
function loadCalcToModal(field) {
    const calcType = document.getElementById('modalCalcType');
    if (!calcType) return;

    calcSources = [];

    // Check changes first, then field.calculation from extraction
    const calc = changes.calculations[field.name] || field.calculation || null;

    if (calc && calc.type) {
        calcType.value = calc.type;
        // Load sources
        if (calc.sources) {
            for (const srcName of calc.sources) {
                const srcField = fields.find(f => f.name === srcName);
                addCalcSource(srcName, srcField ? srcField.page : 0);
            }
        }
        // Load custom formula — simplify raw JS to letter-reference notation
        if (calc.type === 'CUSTOM' && calc.formula) {
            const formulaInput = document.getElementById('calcCustomFormula');
            if (formulaInput) {
                formulaInput.value = simplifyCalcFormula(calc.formula, calc.sources || [], field.name);
            }
        }
        // Load custom script logic
        if (calc.type === 'CUSTOM_SCRIPT' && calc.script) {
            const scriptInput = document.getElementById('calcCustomScriptCode');
            if (scriptInput) {
                scriptInput.value = calc.script;
            }
        }
        // Load constant multiplier
        if (calc.type === 'CONSTANT_MULTIPLY' && calc.constant !== undefined) {
            const constInput = document.getElementById('calcConstantValue');
            if (constInput) constInput.value = calc.constant;
        }
        // Load options
        if (calc.decimals !== undefined) {
            const decInput = document.getElementById('calcDecimals');
            if (decInput) decInput.value = calc.decimals;
        }
        if (calc.readOnly !== undefined) {
            const roCheck = document.getElementById('calcReadOnly');
            if (roCheck) roCheck.checked = calc.readOnly;
        }
    } else {
        calcType.value = '';
    }

    renderCalcSources();
    calcType.dispatchEvent(new Event('change'));
    calcDirty = false;  // Reset AFTER loading — user hasn't modified this field's calc yet
}

// ============ Calculations Manager ============

function openCalcManager() {
    const panel = document.getElementById('calcManagerPanel');
    panel.style.display = 'flex';
    panel.classList.remove('minimized');
    refreshCalcManagerTable();
}

function closeCalcManager() {
    document.getElementById('calcManagerPanel').style.display = 'none';
}

function refreshCalcManagerTable() {
    const tbody = document.getElementById('calcManagerBody');
    if (!tbody) return;

    // Merge: calculations from changes + calculations from extracted fields
    const allCalcs = {};
    // From extracted fields
    for (const f of fields) {
        if (f.calculation && f.calculation.type) {
            allCalcs[f.name] = f.calculation;
        }
    }
    // Override with pending changes
    for (const [name, calc] of Object.entries(changes.calculations)) {
        if (calc && calc.type) {
            allCalcs[name] = calc;
        } else {
            delete allCalcs[name];
        }
    }

    const filter = (document.getElementById('calcManagerFilter')?.value || '').toLowerCase();
    const entries = Object.entries(allCalcs).filter(([name]) =>
        !filter || name.toLowerCase().includes(filter)
    );

    const actionsBody = document.getElementById('calcActionsBody');

    if (entries.length === 0) {
        tbody.innerHTML = '<tr><td colspan="3" class="calc-empty-msg">No calculations configured yet</td></tr>';
        if (actionsBody) actionsBody.innerHTML = '<tr><td style="padding: 6px 10px;">&nbsp;</td></tr>';
        return;
    }

    tbody.innerHTML = entries.map(([name, calc]) => {
        const srcText = (calc.sources || []).length <= 3
            ? (calc.sources || []).join(', ')
            : `${calc.sources.length} fields`;
        const typeLabel = calc.type === 'CONSTANT_MULTIPLY' ? `×${calc.constant || 1}` : calc.type;
        return `<tr data-calc-field="${name}" style="border-bottom: 1px solid #1a2a44; transition: background 0.15s;">
            <td style="padding: 6px 10px; color: var(--text-primary); white-space: nowrap;">${name}</td>
            <td style="padding: 6px 10px; color: var(--accent); font-weight: 500; white-space: nowrap;">${typeLabel}</td>
            <td style="padding: 6px 10px; color: var(--text-secondary); font-size: 11px; white-space: nowrap;">${srcText}</td>
        </tr>`;
    }).join('');

    // Render actions in the separate fixed column
    if (actionsBody) {
        actionsBody.innerHTML = entries.map(([name]) => {
            return `<tr data-calc-action="${name}" style="border-bottom: 1px solid #1a2a44;">
                <td style="padding: 6px 10px; text-align: center; white-space: nowrap;">
                    <span style="cursor: pointer; color: var(--accent); font-size: 14px; margin-right: 6px;" onclick="editCalcFromManager('${name.replace(/'/g, "\\'")}')" title="Edit calculation">🔧</span>
                    <span style="cursor: pointer; color: var(--danger); font-size: 14px;" onclick="removeCalcFromManager('${name.replace(/'/g, "\\'")}')" title="Remove calculation">🗑</span>
                </td>
            </tr>`;
        }).join('');

        // Sync row heights between data table and actions table
        requestAnimationFrame(() => {
            const dataRows = tbody.querySelectorAll('tr');
            const actionRows = actionsBody.querySelectorAll('tr');
            dataRows.forEach((row, i) => {
                if (actionRows[i]) {
                    const h = Math.max(row.offsetHeight, actionRows[i].offsetHeight);
                    row.style.height = h + 'px';
                    actionRows[i].style.height = h + 'px';
                }
            });
            // Sync scroll position vertically
            const dataScroll = document.getElementById('calcDataScroll');
            const actionsCol = document.getElementById('calcActionsCol');
            if (dataScroll && actionsCol) {
                dataScroll.onscroll = () => { actionsCol.scrollTop = dataScroll.scrollTop; };
            }
        });
    }

    // Update order list
    updateCalcOrderList(allCalcs);

    // Sync highlight with current selection
    highlightCalcManagerRow();
}

function highlightCalcManagerRow() {
    const tbody = document.getElementById('calcManagerBody');
    if (!tbody) return;

    const activeName = selectedFields.length === 1 ? selectedFields[0].name : null;

    let activeRow = null;
    tbody.querySelectorAll('tr[data-calc-field]').forEach(row => {
        if (row.dataset.calcField === activeName) {
            row.style.background = 'rgba(100, 255, 218, 0.08)';
            row.style.boxShadow = 'inset 3px 0 0 var(--accent)';
            activeRow = row;
        } else {
            row.style.background = '';
            row.style.boxShadow = '';
        }
    });

    // Also highlight the corresponding actions row
    const actionsBody = document.getElementById('calcActionsBody');
    if (actionsBody) {
        actionsBody.querySelectorAll('tr[data-calc-action]').forEach(row => {
            if (row.dataset.calcAction === activeName) {
                row.style.background = 'rgba(100, 255, 218, 0.08)';
            } else {
                row.style.background = '';
            }
        });
    }

    // Scroll the highlighted row into view, centered in the list
    if (activeRow) {
        activeRow.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
}

function removeCalcFromManager(fieldName) {
    delete changes.calculations[fieldName];
    const field = fields.find(f => f.name === fieldName);
    if (field && field.calculation) delete field.calculation;
    markUnsaved();
    refreshCalcManagerTable();
}

async function editCalcFromManager(fieldName) {
    const field = fields.find(f => f.name === fieldName);
    if (!field) return;

    // Navigate to the field's page if needed
    if (field.page !== currentPage) {
        await loadPage(field.page);
    }

    // Select the field
    selectedFields = [field];
    updateFieldList();
    renderCanvas();

    // Open properties modal on the Calculate tab
    openPropertiesModal(field);
    switchPropertiesTab('calculate');

    // Scroll canvas to center on the field
    scrollToField(field);
}

function updateCalcOrderList(allCalcs) {
    const orderList = document.getElementById('calcOrderList');
    if (!orderList) return;

    const names = Object.keys(allCalcs);
    if (names.length === 0) {
        orderList.innerHTML = '<div class="calc-empty-msg">No calculations to order</div>';
        return;
    }

    orderList.innerHTML = names.map((name, i) => {
        const calc = allCalcs[name];
        return `<div class="calc-source-item" style="cursor: default;">
            <span style="color: var(--text-secondary); font-size: 11px; min-width: 20px;">${i + 1}.</span>
            <span class="source-name">${name}</span>
            <span style="font-size: 11px; color: var(--accent); margin-left: auto;">${calc.type}</span>
        </div>`;
    }).join('');
}

// ============ Auto-Detect Calculations ============

function autoDetectCalculations() {
    const suggestions = [];
    const fieldNames = fields.map(f => f.name);
    const fieldsByPage = {};
    for (const f of fields) {
        const p = f.page || 0;
        if (!fieldsByPage[p]) fieldsByPage[p] = [];
        fieldsByPage[p].push(f);
    }

    // Pattern 1: Suffix matching — find "Total X_N" paired with "Y_N" fields
    // Look for fields with numeric suffixes that share the suffix
    const suffixGroups = {};
    for (const name of fieldNames) {
        const match = name.match(/^(.+?)[\s_]*(\d+)$/);
        if (match) {
            const base = match[1].trim().replace(/_$/, '');
            const num = match[2];
            if (!suffixGroups[num]) suffixGroups[num] = [];
            suffixGroups[num].push({ name, base });
        }
    }

    // For each suffix group, look for total/result fields paired with quantity/rate fields
    for (const [num, group] of Object.entries(suffixGroups)) {
        const totalField = group.find(g =>
            /total|rebate|result|amount|sum|subtotal/i.test(g.base)
        );
        const otherFields = group.filter(g => g !== totalField);

        if (totalField && otherFields.length >= 1) {
            // Check if we can find qty/units and rate fields
            const qtyField = otherFields.find(g => /qty|quantity|unit|count|num/i.test(g.base));
            const rateField = otherFields.find(g => /rate|price|cost|rebate|per|each/i.test(g.base) && g !== totalField);

            if (qtyField && rateField) {
                suggestions.push({
                    target: totalField.name,
                    type: 'MULTIPLY',
                    sources: [qtyField.name, rateField.name],
                    confidence: 'high',
                    reason: `${qtyField.base} × ${rateField.base} = ${totalField.base}`
                });
            } else if (otherFields.length >= 2) {
                suggestions.push({
                    target: totalField.name,
                    type: 'MULTIPLY',
                    sources: otherFields.slice(0, 2).map(f => f.name),
                    confidence: 'medium',
                    reason: `${otherFields[0].base} × ${otherFields[1].base} = ${totalField.base}`
                });
            }
        }
    }

    // Pattern 2: Grand total / subtotal fields that should SUM other totals
    const totalFields = fields.filter(f =>
        /grand\s*total|total\s*rebate\s*all|total\s*custom|subtotal/i.test(f.name)
    );
    for (const tf of totalFields) {
        // Find fields on the same page that look like individual totals
        const samePage = (fieldsByPage[tf.page] || []).filter(f =>
            f.name !== tf.name &&
            /total|rebate|amount|sum/i.test(f.name) &&
            !/grand|all|custom|sub/i.test(f.name)
        );
        if (samePage.length >= 2) {
            suggestions.push({
                target: tf.name,
                type: 'SUM',
                sources: samePage.map(f => f.name),
                confidence: 'medium',
                reason: `SUM of ${samePage.length} total fields on page ${(tf.page || 0) + 1}`
            });
        }
    }

    // Pattern 3: Fields with "AxB" or "A*B" in name
    for (const f of fields) {
        const axbMatch = f.name.match(/(\w+)\s*[x×*]\s*(\w+)/i);
        if (axbMatch) {
            const fieldA = fields.find(fl => fl.name.toLowerCase().includes(axbMatch[1].toLowerCase()));
            const fieldB = fields.find(fl => fl.name.toLowerCase().includes(axbMatch[2].toLowerCase()));
            if (fieldA && fieldB && fieldA.name !== f.name && fieldB.name !== f.name) {
                suggestions.push({
                    target: f.name,
                    type: 'MULTIPLY',
                    sources: [fieldA.name, fieldB.name],
                    confidence: 'high',
                    reason: `Name suggests ${fieldA.name} × ${fieldB.name}`
                });
            }
        }
    }

    // Pattern 4: Text-based detection — use nearbyText from PDF extraction
    for (const f of fields) {
        if (!f.nearbyText) continue;
        // Skip if already detected by name patterns
        if (suggestions.find(s => s.target === f.name)) continue;

        const allText = Object.values(f.nearbyText).join(' ').toLowerCase();
        const isTotalField = /\btotal\b|\bsum\b|\bsubtotal\b|\bamount\b/.test(allText);
        if (!isTotalField) continue;

        // Find fields on same page in the same column (X overlap)
        const samePage = (fieldsByPage[f.page] || []).filter(other => {
            if (other.name === f.name) return false;
            // Skip other total/sum fields
            if (other.nearbyText) {
                const otherText = Object.values(other.nearbyText).join(' ').toLowerCase();
                if (/\btotal\b|\bsum\b|\bsubtotal\b/.test(otherText)) return false;
            }
            if (/total|sum|subtotal/i.test(other.name)) return false;
            // Check same column (X overlap within 15pt)
            const xOverlap = Math.min(other.rect[2], f.rect[2]) - Math.max(other.rect[0], f.rect[0]);
            return xOverlap > -15;
        });

        if (samePage.length >= 2) {
            suggestions.push({
                target: f.name,
                type: 'SUM',
                sources: samePage.map(sf => sf.name),
                confidence: 'medium',
                reason: `PDF text "${allText.substring(0, 35).trim()}..." suggests total`
            });
        }
    }

    // Pattern 5: Column header detection — group by X position
    const columnGroups = {};
    for (const f of fields) {
        if (f.type === 'link') continue;
        const colKey = `${f.page}_${Math.round(f.rect[0] / 10) * 10}`;
        if (!columnGroups[colKey]) columnGroups[colKey] = [];
        columnGroups[colKey].push(f);
    }

    for (const [colKey, colFields] of Object.entries(columnGroups)) {
        if (colFields.length < 3) continue;

        // Sort by Y position (top to bottom)
        colFields.sort((a, b) => a.rect[1] - b.rect[1]);

        // Check if any field (especially the last few) has "total" in nearby text or name
        for (let ci = Math.max(0, colFields.length - 3); ci < colFields.length; ci++) {
            const candidate = colFields[ci];
            // Skip if already detected
            if (suggestions.find(s => s.target === candidate.name)) continue;

            let hasTotal = /total|sum|subtotal/i.test(candidate.name);
            if (!hasTotal && candidate.nearbyText) {
                const txt = Object.values(candidate.nearbyText).join(' ').toLowerCase();
                hasTotal = /\btotal\b|\bsum\b|\bsubtotal\b/.test(txt);
            }

            if (hasTotal) {
                const sources = colFields.filter(cf => cf !== candidate).map(cf => cf.name);
                if (sources.length >= 2) {
                    suggestions.push({
                        target: candidate.name,
                        type: 'SUM',
                        sources: sources,
                        confidence: 'high',
                        reason: `Column total: ${sources.length} fields above in same column`
                    });
                }
            }
        }
    }

    // Pattern 6: t-suffix CONSTANT_MULTIPLY — detect "calNt = constant × calN" naming convention
    // Works when column headers above the source field contain a dollar/numeric value
    // e.g. cal1t = 35 * cal1, iuw4t = 7 * iuw4, cag1t = 1300 * cag1
    for (const f of fields) {
        if (suggestions.find(s => s.target === f.name)) continue;

        // Field name ends in 't' (the total/result field)
        const tMatch = f.name.match(/^(.+)t$/i);
        if (!tMatch) continue;
        const baseName = tMatch[1];

        // Find the matching source field (same name without the 't')
        const sourceField = fields.find(sf => sf.name === baseName);
        if (!sourceField) continue;

        // Look for a numeric constant in the column header above the source field
        const constant = _getColumnConstant(sourceField);
        if (constant !== null) {
            suggestions.push({
                target: f.name,
                type: 'CONSTANT_MULTIPLY',
                sources: [sourceField.name],
                constant: constant,
                confidence: 'high',
                reason: `Column header value ${constant} × ${sourceField.name} (from table header)`
            });
            continue;
        }

        // Fallback: check column header above the total field itself
        const constantFromTotal = _getColumnConstant(f);
        if (constantFromTotal !== null) {
            suggestions.push({
                target: f.name,
                type: 'CONSTANT_MULTIPLY',
                sources: [sourceField.name],
                constant: constantFromTotal,
                confidence: 'medium',
                reason: `Column header value ${constantFromTotal} × ${sourceField.name}`
            });
        }
    }

    // Pattern 7: General column header constant detect — any field whose column header
    // has a number, paired with a same-row "total" field to the right or with a name suffix
    for (const f of fields) {
        if (suggestions.find(s => s.target === f.name)) continue;
        if (!f.nearbyText) continue;

        const constant = _getColumnConstant(f);
        if (constant === null) continue;

        // Skip if this field already looks like a total
        if (/total|sum|subtotal|rebate/i.test(f.name)) continue;

        // Look for a "total" partner field on the same page and same row (similar Y)
        const fy_mid = (f.rect[1] + f.rect[3]) / 2;
        const sameRow = (fieldsByPage[f.page] || []).filter(other => {
            if (other.name === f.name) return false;
            const oy_mid = (other.rect[1] + other.rect[3]) / 2;
            // Same row: Y centers within 10pt
            if (Math.abs(oy_mid - fy_mid) > 10) return false;
            // Total field is to the right
            if (other.rect[0] <= f.rect[2]) return false;
            // Partner name or text suggests it's a total
            const isTotal = /total|sum|rebate|result|amount/i.test(other.name) ||
                (other.nearbyText && /\btotal\b|\bsum\b|\brebate\b/i.test(Object.values(other.nearbyText).join(' ')));
            return isTotal;
        });

        if (sameRow.length > 0) {
            // Pick the closest total field to the right
            sameRow.sort((a, b) => a.rect[0] - b.rect[0]);
            const totalField = sameRow[0];
            if (!suggestions.find(s => s.target === totalField.name)) {
                suggestions.push({
                    target: totalField.name,
                    type: 'CONSTANT_MULTIPLY',
                    sources: [f.name],
                    constant: constant,
                    confidence: 'medium',
                    reason: `Column header value ${constant} × ${f.name} (table pattern)`
                });
            }
        }
    }

    return suggestions;
}

// Helper: extract numeric constant from a field's row context
// Checks rowConstant (dollar amount on same row to the left) set by extract_fields.py
function _getColumnConstant(field) {
    if (!field.nearbyText) return null;
    // Primary: row-level dollar amount (e.g. "$35.00" between measure description and qty field)
    if (field.nearbyText.rowConstant !== undefined) return field.nearbyText.rowConstant;
    return null;
}

function displayAutoDetectResults(suggestions) {
    const resultsDiv = document.getElementById('autoDetectResults');
    if (!resultsDiv) return;

    if (suggestions.length === 0) {
        resultsDiv.style.display = 'block';
        resultsDiv.innerHTML = '<div style="color: var(--text-secondary); font-size: 12px; padding: 8px 0;">No calculation patterns detected in field names.</div>';
        return;
    }

    resultsDiv.style.display = 'block';
    resultsDiv.innerHTML = `
        <div style="font-size: 11px; color: var(--text-secondary); margin-bottom: 8px;">Found ${suggestions.length} potential calculation(s):</div>
        ${suggestions.map((s, i) => `
            <div style="display: flex; align-items: center; gap: 8px; padding: 6px 8px; background: var(--bg-primary); border-radius: 4px; margin-bottom: 4px; font-size: 12px;">
                <input type="checkbox" id="autoCalc_${i}" checked style="accent-color: var(--accent);">
                <span style="flex: 1;">
                    <span style="color: var(--accent); font-weight: 600;">${s.target}</span>
                    <span style="color: var(--text-secondary);"> = </span>
                    ${s.type === 'CONSTANT_MULTIPLY' && s.constant !== undefined
                        ? `<span style="color: #f0a500; font-weight:600;">${s.constant}</span><span style="color: var(--text-secondary);"> × </span><span style="color: var(--text-primary);">${s.sources[0]}</span>`
                        : `<span style="color: var(--text-secondary);">${s.type}(</span><span style="color: var(--text-primary);">${s.sources.join(', ')}</span><span style="color: var(--text-secondary);">)</span>`
                    }
                </span>
                <span style="font-size: 10px; padding: 1px 6px; border-radius: 3px; background: ${s.confidence === 'high' ? '#1a3a2a' : '#2a2a1a'}; color: ${s.confidence === 'high' ? '#4caf50' : '#ff9800'};">${s.confidence}</span>
            </div>
        `).join('')}
        <button class="action-btn btn-secondary" onclick="applyAutoDetectedCalcs()" style="margin-top: 8px; padding: 6px 16px; font-size: 12px; width: 100%;">
            ✅ Apply Selected (${suggestions.length})
        </button>
    `;

    // Store suggestions for later application
    window._autoDetectSuggestions = suggestions;
}

function applyAutoDetectedCalcs() {
    const suggestions = window._autoDetectSuggestions || [];
    let applied = 0;

    for (let i = 0; i < suggestions.length; i++) {
        const checkbox = document.getElementById(`autoCalc_${i}`);
        if (checkbox && checkbox.checked) {
            const s = suggestions[i];
            const calcEntry = {
                type: s.type,
                sources: s.sources,
                decimals: 2,
                readOnly: true
            };
            if (s.type === 'CONSTANT_MULTIPLY' && s.constant !== undefined) {
                calcEntry.constant = s.constant;
            }
            changes.calculations[s.target] = calcEntry;
            // Update field object
            const field = fields.find(f => f.name === s.target);
            if (field) {
                field.calculation = changes.calculations[s.target];
            }
            applied++;
        }
    }

    if (applied > 0) {
        markUnsaved();
        refreshCalcManagerTable();
        renderCanvas();
    }

    // Update the results display
    const resultsDiv = document.getElementById('autoDetectResults');
    if (resultsDiv) {
        resultsDiv.innerHTML = `<div style="color: var(--accent); font-size: 12px; padding: 8px 0;">✅ Applied ${applied} calculation(s)</div>`;
    }
}

// ============ Column Detection & Picker ============

function detectColumns(fieldList) {
    // Group fields by page + X-position (bucketed to 10pt)
    const columns = {};
    for (const f of fieldList) {
        if (f.type === 'link') continue;
        const colKey = `${f.page}_${Math.round(f.rect[0] / 10) * 10}`;
        if (!columns[colKey]) columns[colKey] = [];
        columns[colKey].push(f);
    }
    // Filter out single-field columns and sort each column by Y
    const result = {};
    for (const [key, colFields] of Object.entries(columns)) {
        if (colFields.length < 2) continue;
        colFields.sort((a, b) => a.rect[1] - b.rect[1]);
        result[key] = colFields;
    }
    return result;
}

function renderColumnPicker() {
    const picker = document.getElementById('batchCalcColumnPicker');
    if (!picker) return;

    const allColumns = detectColumns(fields);

    // Find which columns contain the selected (target) fields
    const targetColKeys = new Set();
    for (const sf of selectedFields) {
        const key = `${sf.page}_${Math.round(sf.rect[0] / 10) * 10}`;
        targetColKeys.add(key);
    }

    // Build checkboxes for non-target columns
    const entries = Object.entries(allColumns)
        .filter(([key]) => !targetColKeys.has(key));

    if (entries.length === 0) {
        picker.innerHTML = '<div style="font-size: 11px; color: var(--text-secondary);">No other columns detected on this page</div>';
        return;
    }

    picker.innerHTML = entries.map(([key, colFields]) => {
        // Use first field's name or nearby text as column label
        const label = colFields[0].nearbyText
            ? (Object.values(colFields[0].nearbyText).join(' ').trim().substring(0, 25) || colFields[0].name)
            : colFields[0].name;
        const fieldNames = colFields.map(f => f.name).join(',');
        return `<label style="display: flex; align-items: center; gap: 6px; padding: 3px 2px; font-size: 11px; cursor: pointer;">
            <input type="checkbox" class="batch-col-check" data-col-key="${key}" data-fields="${fieldNames}" style="accent-color: var(--accent);">
            <span style="color: var(--text-primary);">${label}</span>
            <span style="color: var(--text-secondary); font-size: 10px; margin-left: auto;">(${colFields.length} fields)</span>
        </label>`;
    }).join('');
}

// ============ Batch Calculation Assignment ============

function applyBatchCalculation() {
    const calcType = document.getElementById('batchCalcType')?.value;
    if (!calcType || selectedFields.length === 0) return;

    const sourceMode = document.getElementById('batchCalcSourceMode')?.value || 'other_selected';
    const decimals = parseInt(document.getElementById('batchCalcDecimals')?.value) || 2;
    const readOnly = document.getElementById('batchCalcReadOnly')?.checked ?? true;
    let applied = 0;

    for (const field of selectedFields) {
        let sources = [];

        if (sourceMode === 'other_selected') {
            // All OTHER selected fields become sources for THIS field
            sources = selectedFields
                .filter(f => f.name !== field.name)
                .map(f => f.name);
        } else if (sourceMode === 'same_row') {
            // Find fields on the same page with similar Y position (within 5pt)
            const pageFields = fields.filter(f =>
                f.page === field.page &&
                f.name !== field.name &&
                !selectedFields.includes(f) &&
                Math.abs(f.rect[1] - field.rect[1]) < 5
            );
            sources = pageFields.map(f => f.name);
        } else if (sourceMode === 'pick_columns') {
            // Get checked source columns
            const checkedCols = document.querySelectorAll('.batch-col-check:checked');
            const sourceColumns = [];
            checkedCols.forEach(cb => {
                const colFields = cb.dataset.fields.split(',')
                    .map(name => fields.find(f => f.name === name))
                    .filter(Boolean);
                sourceColumns.push(colFields);
            });

            if (sourceColumns.length === 0) continue;

            // Match by row: find source fields at same Y-position (±5pt)
            for (const sourceCol of sourceColumns) {
                const match = sourceCol.find(sf =>
                    Math.abs(sf.rect[1] - field.rect[1]) < 5 &&
                    sf.page === field.page
                );
                if (match) sources.push(match.name);
            }
        }

        if (sources.length > 0) {
            const calcObj = {
                type: calcType,
                sources: sources,
                decimals: decimals,
                readOnly: readOnly
            };
            // Include constant for CONSTANT_MULTIPLY
            if (calcType === 'CONSTANT_MULTIPLY') {
                calcObj.constant = parseFloat(document.getElementById('batchCalcConstant')?.value) || 1;
            }
            changes.calculations[field.name] = calcObj;
            field.calculation = calcObj;
            applied++;
        }
    }

    if (applied > 0) {
        markUnsaved();
        refreshCalcManagerTable();
        renderCanvas();
        updatePropertiesPanel();
    }

    // Show feedback
    const btn = document.getElementById('btnApplyBatchCalc');
    if (btn) {
        const origText = btn.textContent;
        btn.textContent = `✅ Applied to ${applied} field(s)`;
        btn.style.color = '#4caf50';
        setTimeout(() => {
            btn.textContent = origText;
            btn.style.color = '#4caf50';
        }, 2000);
    }
}

// ============ Init Calc Event Listeners ============

document.addEventListener('DOMContentLoaded', () => {
    // Init calculate tab
    initCalculateTab();

    // Calc manager button
    const btnCalcMgr = document.getElementById('btnCalcManager');
    if (btnCalcMgr) {
        btnCalcMgr.addEventListener('click', openCalcManager);
    }

    // Calc manager filter
    const calcFilter = document.getElementById('calcManagerFilter');
    if (calcFilter) {
        calcFilter.addEventListener('input', refreshCalcManagerTable);
    }

    // Auto-detect button
    const btnAutoDetect = document.getElementById('btnAutoDetectCalcs');
    if (btnAutoDetect) {
        btnAutoDetect.addEventListener('click', () => {
            const suggestions = autoDetectCalculations();
            displayAutoDetectResults(suggestions);
        });
    }

    // Batch calc type change — show/hide source options and constant row
    const batchCalcType = document.getElementById('batchCalcType');
    if (batchCalcType) {
        batchCalcType.addEventListener('change', () => {
            const row = document.getElementById('batchCalcSourceRow');
            const constRow = document.getElementById('batchCalcConstantRow');
            if (row) row.style.display = batchCalcType.value ? 'block' : 'none';
            if (constRow) constRow.style.display = batchCalcType.value === 'CONSTANT_MULTIPLY' ? 'block' : 'none';
        });
    }

    // Batch calc source mode change — update hint text and column picker
    const batchCalcSourceMode = document.getElementById('batchCalcSourceMode');
    if (batchCalcSourceMode) {
        batchCalcSourceMode.addEventListener('change', () => {
            const hint = document.getElementById('batchCalcSourceHint');
            const picker = document.getElementById('batchCalcColumnPicker');

            if (batchCalcSourceMode.value === 'same_row') {
                if (hint) hint.textContent = 'Each field\'s sources = other fields at same Y position';
                if (picker) picker.style.display = 'none';
            } else if (batchCalcSourceMode.value === 'pick_columns') {
                if (hint) hint.textContent = 'Check the source columns below \u2014 fields matched by row';
                if (picker) { picker.style.display = 'block'; renderColumnPicker(); }
            } else {
                if (hint) hint.textContent = 'Each field\'s sources = all other selected fields';
                if (picker) picker.style.display = 'none';
            }
        });
    }

    // Batch calc apply button
    const btnApplyBatchCalc = document.getElementById('btnApplyBatchCalc');
    if (btnApplyBatchCalc) {
        btnApplyBatchCalc.addEventListener('click', applyBatchCalculation);
    }

    // Collapsible panels
    initCollapsiblePanels();

});

// ── Collapsible panels ────────────────────────────────────────────────────────
// Three panels can be independently collapsed to a 28px strip to free up
// canvas space. State persists in localStorage across sessions.
function initCollapsiblePanels() {
    const panels = [
        {
            panelId:    'pageSidebar',
            btnId:      'btnCollapsePages',
            labelId:    'pagesCollapsedLabel',
            storageKey: 'panelCollapsed_pages',
            side:       'left',   // left panel: ‹ = collapse, › = expand
        },
        {
            panelId:    'fieldListSidebar',
            btnId:      'btnCollapseFields',
            labelId:    'fieldsCollapsedLabel',
            storageKey: 'panelCollapsed_fields',
            side:       'right',  // right panel: › = collapse, ‹ = expand
        },
        {
            panelId:    'propertiesPanel',
            btnId:      'btnCollapseProps',
            labelId:    'propsCollapsedLabel',
            storageKey: 'panelCollapsed_props',
            side:       'right',
        },
    ];

    panels.forEach(({ panelId, btnId, labelId, storageKey, side }) => {
        const panel = document.getElementById(panelId);
        const btn   = document.getElementById(btnId);
        const label = document.getElementById(labelId);
        if (!panel || !btn) return;

        // Double chevrons: when expanded show the "close" direction, collapsed shows "open"
        const collapseChar = side === 'left' ? '«' : '»';
        const expandChar   = side === 'left' ? '»' : '«';

        const applyState = (collapsed) => {
            panel.classList.toggle('collapsed', collapsed);
            btn.textContent = collapsed ? expandChar : collapseChar;
            btn.title       = collapsed ? 'Expand panel' : 'Collapse panel';
        };

        const toggle = () => {
            const nowCollapsed = !panel.classList.contains('collapsed');
            applyState(nowCollapsed);
            localStorage.setItem(storageKey, nowCollapsed);
            // After the CSS width transition, nudge the canvas to re-fit if in
            // single view — avoids the canvas staying stale-sized.
            setTimeout(() => {
                if (viewMode !== 'scroll') requestRender();
                else { syncScrollViewDOM(); applyScrollZoom(); }
            }, 220);
        };

        // Restore saved state on load
        const saved = localStorage.getItem(storageKey) === 'true';
        applyState(saved);

        btn.addEventListener('click', (e) => { e.stopPropagation(); toggle(); });
        // Clicking the vertical label also expands
        if (label) label.addEventListener('click', () => {
            if (panel.classList.contains('collapsed')) toggle();
        });
    });
}
