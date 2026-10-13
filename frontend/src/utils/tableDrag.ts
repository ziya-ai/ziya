/**
 * Native drag-out of rendered markdown tables.
 *
 * A table is dragged from any press that lands off its text (cell padding,
 * gridlines, empty cells) or from a small handle; a press on glyphs keeps
 * the browser's text selection. It travels in three forms at once, letting
 * each drop target take the one it understands:
 *   - `DownloadURL`: a PNG of the table as rendered (Chromium's file-promise
 *     path; Slack, Finder and Explorer receive a real .png),
 *   - `text/html`: the <table> itself with its computed styles inlined, so a
 *     rich-text editor (Docs, Word, Confluence, Sheets) pastes a real table,
 *   - `text/plain`: the markdown source.
 *
 * The PNG is made by wrapping the styled table in an SVG <foreignObject> and
 * painting that onto a canvas, which is the only way to rasterize HTML in the
 * browser without a library. It is prepared when the pointer rests on the
 * table and cached against the table's markup; a drag that starts first
 * carries the SVG instead.
 */

import {
    rasterizeSvgToPng,
    resolveBackground,
    svgToDataUrl,
    fileStem,
    addFileItem,
    copyImageToClipboard,
    type SvgRasterizer,
} from './diagramDrag';
import { armNativeDrag } from './nativeDragArm';

export interface TableDragOptions {
    /** Element that becomes the drag source (default: the container). */
    dragSource?: HTMLElement;
    /** Markdown source for the text/plain payload (default: tab-separated cells). */
    getMarkdown?: () => string | undefined;
    /** Colour painted behind the table (default: resolved from the DOM). */
    getBackground?: () => string;
    /** Device-pixel scale of the PNG (default: min(devicePixelRatio, 2)). */
    scale?: number;
    /** Delay after pointer entry before rasterizing. */
    prepareDelayMs?: number;
    /** Injection point for tests; defaults to the canvas rasterizer. */
    rasterize?: SvgRasterizer;
    /**
     * Whether a press at viewport (x, y) landed on rendered text (default:
     * caret hit-test against the text node's line boxes). Injectable for tests.
     */
    isOverText?: (x: number, y: number) => boolean;
    /** Element whose click copies the table PNG to the clipboard (for Slack). */
    copySource?: HTMLElement;
    /** Feedback for a copy attempt. */
    onCopied?: (ok: boolean) => void;
}

export interface SerializedTable {
    /** SVG wrapping the styled table in a <foreignObject>, ready to rasterize. */
    svgString: string;
    /** The styled table alone, for the text/html payload. */
    html: string;
    width: number;
    height: number;
}

interface PreparedTable extends SerializedTable {
    fingerprint: string;
    png: string | null;
    pngPromise: Promise<string> | null;
}

/** Padding (CSS px) painted around the table in the PNG. */
export const TABLE_IMAGE_PADDING = 8;

// Longhands only: jsdom and some browsers return '' for shorthands from
// getComputedStyle, and a shorthand can be reconstructed from these anyway.
const TABLE_INLINED_PROPS = [
    'color', 'background-color',
    'border-top-width', 'border-top-style', 'border-top-color',
    'border-right-width', 'border-right-style', 'border-right-color',
    'border-bottom-width', 'border-bottom-style', 'border-bottom-color',
    'border-left-width', 'border-left-style', 'border-left-color',
    'border-collapse', 'border-spacing',
    'padding-top', 'padding-right', 'padding-bottom', 'padding-left',
    'text-align', 'vertical-align', 'white-space',
    'font-family', 'font-size', 'font-weight', 'font-style', 'line-height',
    'text-decoration', 'width',
];

const XHTML_NS = 'http://www.w3.org/1999/xhtml';

/**
 * Clone a live table with its computed styles inlined, so it looks the same
 * outside the page (in a foreignObject, or pasted into another application
 * that does not have our stylesheets). Returns null when the table has no
 * rendered box.
 */
export function serializeTableForImage(
    table: HTMLTableElement,
    background: string,
    padding: number = TABLE_IMAGE_PADDING,
): SerializedTable | null {
    const box = table.getBoundingClientRect();
    const tableWidth = Math.ceil(box.width);
    const tableHeight = Math.ceil(box.height);
    if (tableWidth < 1 || tableHeight < 1) return null;

    const clone = table.cloneNode(true) as HTMLTableElement;
    const origEls = [table, ...Array.from(table.querySelectorAll('*'))];
    const cloneEls = [clone, ...Array.from(clone.querySelectorAll('*'))];
    origEls.forEach((origEl, i) => {
        const target = cloneEls[i] as HTMLElement | undefined;
        if (!target || !target.style) return;
        const computed = window.getComputedStyle(origEl);
        for (const prop of TABLE_INLINED_PROPS) {
            const v = computed.getPropertyValue(prop);
            if (v && v !== 'normal') target.style.setProperty(prop, v);
        }
    });
    // External images would taint the canvas and abort the PNG; nothing else
    // inside a markdown table needs to travel.
    clone.querySelectorAll('img, video, iframe, script, button').forEach((el) => el.remove());
    // Per-gesture drag arming (attachTableDrag) must not leak into the
    // exported markup.
    clone.removeAttribute('draggable');
    clone.style.margin = '0';
    clone.style.width = `${tableWidth}px`;

    const html = new XMLSerializer().serializeToString(clone);

    const root = window.getComputedStyle(table);
    const wrapper = document.createElementNS(XHTML_NS, 'div');
    wrapper.setAttribute('style',
        `margin:0;padding:${padding}px;box-sizing:border-box;background:${background};` +
        `color:${root.color || 'inherit'};font-family:${root.fontFamily || 'sans-serif'};` +
        `font-size:${root.fontSize || '14px'};`);
    wrapper.appendChild(clone);

    // The frame must fit the CLONE, not the live table: the page renders the
    // table as display:block with half its collapsed outer border outside the
    // box, while the clone (no page stylesheet) is a plain display:table with
    // the border inside, and a cell may wrap differently. Sizing from the live
    // box alone clipped the bottom gridline or the last row. Lay the clone out
    // off-screen and take the larger measurement; jsdom reports 0 here, so
    // the live box remains the floor.
    let width = tableWidth + 2 * padding;
    let height = tableHeight + 2 * padding;
    try {
        wrapper.style.position = 'absolute';
        wrapper.style.left = '-100000px';
        wrapper.style.top = '0';
        wrapper.style.visibility = 'hidden';
        document.body.appendChild(wrapper);
        const laid = wrapper.getBoundingClientRect();
        width = Math.max(width, Math.ceil(laid.width));
        height = Math.max(height, Math.ceil(laid.height));
    } finally {
        wrapper.remove();
        ['position', 'left', 'top', 'visibility'].forEach((p) => wrapper.style.removeProperty(p));
    }
    const svgString =
        `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}">` +
        `<foreignObject width="${width}" height="${height}">` +
        new XMLSerializer().serializeToString(wrapper) +
        `</foreignObject></svg>`;
    return { svgString, html, width, height };
}

/** Tab-separated cell text, one row per line: the text/plain fallback. */
export function tableToTsv(table: HTMLTableElement): string {
    return Array.from(table.querySelectorAll('tr'))
        .map((tr) => Array.from(tr.querySelectorAll('th, td'))
            .map((c) => (c.textContent || '').replace(/\s+/g, ' ').trim())
            .join('\t'))
        .join('\n');
}

/**
 * True when viewport point (x, y) lies on a glyph box of a text node inside
 * `root`. The caret APIs snap to the NEAREST text position, so a press in a
 * cell's padding still returns its text node; the line-box containment test
 * is what separates "on the words" from "beside them". Without either API
 * (no layout engine) every press counts as off-text.
 */
export function isPointOverText(x: number, y: number, root: Element): boolean {
    const doc = root.ownerDocument as any;
    let node: Node | null = null;
    if (typeof doc.caretRangeFromPoint === 'function') {
        node = doc.caretRangeFromPoint(x, y)?.startContainer ?? null;
    } else if (typeof doc.caretPositionFromPoint === 'function') {
        node = doc.caretPositionFromPoint(x, y)?.offsetNode ?? null;
    } else {
        return false;
    }
    if (!node || node.nodeType !== Node.TEXT_NODE || !root.contains(node)) return false;
    if (!(node.textContent || '').trim()) return false;
    const range = (doc as Document).createRange();
    range.selectNodeContents(node);
    for (const rect of Array.from(range.getClientRects())) {
        if (x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom) return true;
    }
    return false;
}

/**
 * Make the table inside `container` draggable from any press that is not on
 * its text, plus `options.dragSource` — when given — as an unconditional
 * drag source (a handle). The container itself is never marked draggable:
 * Chromium starts a drag for any press inside a draggable ancestor, which
 * would defeat the per-press decision below and make the text inside
 * unselectable. Returns a cleanup.
 *
 * `draggable` is decided per gesture at pointerdown, before the browser
 * chooses between a drag and a text selection on the following move: on the
 * words it stays false so selection works; anywhere else on the table it is
 * true until the pointer is released.
 */
export function attachTableDrag(container: HTMLElement, options: TableDragOptions = {}): () => void {
    const getBackground = options.getBackground ?? (() => resolveBackground(container));
    const rasterize = options.rasterize
        ?? ((s, w, h, sc) => rasterizeSvgToPng(s, w, h, sc, getBackground()));
    const scale = options.scale ?? Math.min(Math.max(window.devicePixelRatio || 1, 1), 2);
    const prepareDelay = options.prepareDelayMs ?? 120;
    const dragSource = options.dragSource ?? container;
    const handle = options.dragSource;
    const isOverText = options.isOverText ?? ((x, y) => isPointOverText(x, y, container));

    let prepared: PreparedTable | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let disposed = false;

    const currentTable = (): HTMLTableElement | null => container.querySelector('table');

    // Re-prepares only when the table changed, so it is cheap to call at
    // dragstart as well as on pointer entry.
    const prepare = (): void => {
        if (disposed) return;
        const table = currentTable();
        if (!table) return;
        const background = getBackground();
        const box = table.getBoundingClientRect();
        // innerHTML, not outerHTML: onPointerDown toggles `draggable` on the
        // table itself right before dragstart, and that attribute must not
        // read as a content change (it discarded the finished PNG and shipped
        // the SVG fallback on every whitespace drag).
        const fingerprint = `${background}|${Math.round(box.width)}x${Math.round(box.height)}|${table.innerHTML}`;
        if (prepared && prepared.fingerprint === fingerprint) return;

        const serialized = serializeTableForImage(table, background);
        if (!serialized) return;
        const entry: PreparedTable = { ...serialized, fingerprint, png: null, pngPromise: null };
        prepared = entry;
        const png = rasterize(entry.svgString, entry.width, entry.height, scale);
        entry.pngPromise = png;
        png.then(
            (png) => { if (prepared === entry) entry.png = png; },
            (err) => { console.warn('tableDrag: PNG rasterization failed, SVG fallback stays', err); },
        );
    };

    const onPointerEnter = (): void => {
        if (timer) clearTimeout(timer);
        timer = setTimeout(() => { timer = null; prepare(); }, prepareDelay);
    };
    const onPointerLeave = (): void => {
        if (timer) { clearTimeout(timer); timer = null; }
    };

    const onPointerDown = (e: PointerEvent): void => {
        const table = currentTable();
        if (!table || e.button !== 0) return;
        // Prepare now, with no hover delay, so the PNG is ready by the time
        // the drag threshold is crossed (a drag from a cold table used to
        // ship the SVG fallback); and arm the native helper for this origin.
        prepare();
        armNativeDrag();
        const target = e.target as Element | null;
        if (!target || !table.contains(target)) return;
        // Controls and natively draggable things keep their own behaviour.
        if (target.closest('img, a[href], input, button, textarea, select')) return;
        table.draggable = !isOverText(e.clientX, e.clientY);
    };
    const releasePress = (): void => {
        const table = currentTable();
        if (table) table.draggable = false;
    };

    const onDragStart = (e: DragEvent): void => {
        const dt = e.dataTransfer;
        if (!dt) return;
        const target = e.target as Element | null;
        if (!target) return;
        // A nested <img> or link supplies its own payload; leave it alone.
        if (target !== dragSource && target.closest('img, a[href]')) return;

        prepare();
        const table = currentTable();
        if (!prepared || !table) return;

        dt.effectAllowed = 'copy';
        const stem = fileStem('table');
        if (prepared.png) {
            dt.setData('DownloadURL', `image/png:${stem}.png:${prepared.png}`);
            addFileItem(dt, prepared.png, `${stem}.png`, 'image/png');
        } else {
            dt.setData('DownloadURL', `image/svg+xml:${stem}.svg:${svgToDataUrl(prepared.svgString)}`);
        }
        // PNG carrier for the macOS native drag helper, ahead of the real
        // <table> so rich-text targets still paste a table (see
        // app/utils/drag_pasteboard_helper.py, which strips the comment).
        const carrier = prepared.png ? `<!--ziya-drag-png:${prepared.png}-->` : '';
        dt.setData('text/html', carrier + prepared.html);
        const markdown = options.getMarkdown?.();
        dt.setData('text/plain', markdown && markdown.trim() ? markdown : tableToTsv(table));
    };

    const onCopyClick = (e: MouseEvent): void => {
        e.preventDefault();
        e.stopPropagation();
        prepare();
        const png = prepared?.png ?? prepared?.pngPromise ?? null;
        const done = png ? copyImageToClipboard(png) : Promise.resolve(false);
        void done.then((ok) => options.onCopied?.(ok));
    };

    if (handle) {
        handle.draggable = true;
        handle.setAttribute('data-table-drag', 'true');
    }
    container.addEventListener('pointerenter', onPointerEnter);
    container.addEventListener('pointerleave', onPointerLeave);
    container.addEventListener('pointerdown', onPointerDown);
    container.addEventListener('pointerup', releasePress);
    container.addEventListener('pointercancel', releasePress);
    container.addEventListener('dragend', releasePress);
    // On the container so a drag from the table OR the handle lands here.
    container.addEventListener('dragstart', onDragStart);
    options.copySource?.addEventListener('click', onCopyClick);
    options.copySource?.setAttribute('data-table-copy', 'true');

    return () => {
        disposed = true;
        if (timer) clearTimeout(timer);
        container.removeEventListener('pointerenter', onPointerEnter);
        container.removeEventListener('pointerleave', onPointerLeave);
        container.removeEventListener('pointerdown', onPointerDown);
        container.removeEventListener('pointerup', releasePress);
        container.removeEventListener('pointercancel', releasePress);
        container.removeEventListener('dragend', releasePress);
        container.removeEventListener('dragstart', onDragStart);
        options.copySource?.removeEventListener('click', onCopyClick);
        options.copySource?.removeAttribute('data-table-copy');
        if (handle) {
            handle.draggable = false;
            handle.removeAttribute('data-table-drag');
        }
        releasePress();
        prepared = null;
    };
}
