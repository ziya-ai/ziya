/**
 * Native drag-out of rendered diagrams.
 *
 * Browsers only place image bytes on the OS drag pasteboard for a native
 * drag of an <img>. An inline <svg> dragged by itself carries nothing, which
 * is why dragging a rendered diagram into Slack, Finder or a document did
 * nothing. This module makes a diagram container draggable and, at dragstart,
 * publishes a PNG rasterization of its SVG (or canvas) through Chromium's
 * `DownloadURL` drag type -- the same file-promise path an <img> drag uses, so
 * the drop target receives a real .png file -- plus a text/html <img> for
 * rich-text drop targets.
 *
 * dragstart data must be set synchronously, so the PNG is prepared ahead of
 * time when the pointer enters the container and cached against a fingerprint
 * of the SVG. If the user drags before rasterization has finished, the SVG
 * itself is offered instead of nothing.
 */

import { armNativeDrag } from './nativeDragArm';

export type SvgRasterizer = (
    svgString: string,
    width: number,
    height: number,
    scale: number,
) => Promise<string>;

export interface DiagramDragOptions {
    /** Short type name for the dropped file (default: nearest data-visualization-type). */
    getType?: () => string;
    /** Colour painted behind the diagram (default: resolved from the DOM). */
    getBackground?: () => string;
    /** Device-pixel scale of the PNG (default: min(devicePixelRatio, 2)). */
    scale?: number;
    /** Delay after pointer entry before rasterizing, so a pass-over costs nothing. */
    prepareDelayMs?: number;
    /** Injection point for tests; defaults to a canvas rasterizer. */
    rasterize?: SvgRasterizer;
    /**
     * Element that becomes the drag source (default: the container). For a
     * plugin that owns pointer gestures, pass a small handle so a drag on
     * the diagram itself still reaches the plugin.
     */
    dragSource?: HTMLElement;
    /**
     * Plugin-supplied PNG export, evaluated lazily when the image is
     * prepared. Used instead of rasterizing the SVG when it returns a
     * promise; needed where the SVG does not hold the whole picture
     * (Plotly's WebGL traces live on a canvas layer).
     */
    exportPng?: () => Promise<string> | null | undefined;
    /**
     * Element whose click copies the PNG to the clipboard. Slack desktop
     * (Electron) ignores Chromium's cross-app image drags but accepts a
     * pasted image, so the handle doubles as a copy button.
     */
    copySource?: HTMLElement;
    /** Feedback for a copy attempt (clipboard writes fail silently otherwise). */
    onCopied?: (ok: boolean) => void;
}

interface PreparedSvg {
    fingerprint: string;
    svgString: string;
    width: number;
    height: number;
    png: string | null;
    pngPromise: Promise<string> | null;
}

const TRANSPARENT = new Set(['', 'transparent', 'rgba(0, 0, 0, 0)']);

/** Nearest opaque ancestor background, so dark-theme diagrams stay readable
 *  when dropped into a light-themed application. */
export function resolveBackground(el: Element): string {
    let node: Element | null = el;
    while (node) {
        const bg = window.getComputedStyle(node).backgroundColor;
        if (!TRANSPARENT.has(bg)) return bg;
        node = node.parentElement;
    }
    return '#ffffff';
}

const INLINED_PROPS = [
    'fill', 'stroke', 'stroke-width', 'stroke-dasharray', 'font-family',
    'font-size', 'font-weight', 'font-style', 'opacity', 'color', 'display',
    'visibility', 'stop-color', 'flood-color', 'text-decoration',
    'dominant-baseline', 'text-anchor', 'background-color',
];

/**
 * Serialize a live SVG with its computed styles inlined and an explicit
 * pixel size, so it renders identically when loaded as a standalone image
 * (page stylesheets and percentage sizing are not available there).
 */
export function serializeSvgForImage(
    svg: SVGSVGElement,
): { svgString: string; width: number; height: number } | null {
    const box = svg.getBoundingClientRect();
    const width = Math.round(box.width);
    const height = Math.round(box.height);
    if (width < 1 || height < 1) return null;

    const clone = svg.cloneNode(true) as SVGSVGElement;
    const origEls = svg.querySelectorAll('*');
    const cloneEls = clone.querySelectorAll('*');
    origEls.forEach((origEl, i) => {
        const target = cloneEls[i] as SVGElement | undefined;
        if (!target || !(target as any).style) return;
        const computed = window.getComputedStyle(origEl);
        for (const prop of INLINED_PROPS) {
            const v = computed.getPropertyValue(prop);
            if (v && v !== 'normal') target.style.setProperty(prop, v);
        }
    });

    if (!clone.getAttribute('viewBox')) {
        const vb = svg.viewBox?.baseVal;
        const w = vb && vb.width > 0 ? vb.width : Number(svg.getAttribute('width')) || width;
        const h = vb && vb.height > 0 ? vb.height : Number(svg.getAttribute('height')) || height;
        clone.setAttribute('viewBox', `${vb?.x || 0} ${vb?.y || 0} ${w} ${h}`);
    }
    clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
    clone.setAttribute('width', String(width));
    clone.setAttribute('height', String(height));
    // Override any responsive sizing (width:100%, max-width) on the root.
    clone.style.width = `${width}px`;
    clone.style.height = `${height}px`;
    clone.style.maxWidth = 'none';

    return { svgString: new XMLSerializer().serializeToString(clone), width, height };
}

/** Default rasterizer: paint the SVG onto a canvas over an opaque background.
 *  The SVG is loaded as a data: URL, not a blob: URL -- the page's CSP
 *  (`img-src 'self' data: https:`) does not allow blob: images. */
export function rasterizeSvgToPng(
    svgString: string,
    width: number,
    height: number,
    scale: number,
    background: string = '#ffffff',
): Promise<string> {
    return new Promise<string>((resolve, reject) => {
        const url = svgToDataUrl(svgString);
        const img = new Image();
        img.onload = () => {
            try {
                const canvas = document.createElement('canvas');
                canvas.width = Math.round(width * scale);
                canvas.height = Math.round(height * scale);
                const ctx = canvas.getContext('2d');
                if (!ctx) throw new Error('2d context unavailable');
                ctx.fillStyle = background;
                ctx.fillRect(0, 0, canvas.width, canvas.height);
                ctx.scale(scale, scale);
                ctx.drawImage(img, 0, 0, width, height);
                resolve(canvas.toDataURL('image/png'));
            } catch (e) {
                reject(e);
            }
        };
        img.onerror = () => reject(new Error('SVG image failed to load'));
        img.src = url;
    });
}

export function svgToDataUrl(svgString: string): string {
    return 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(svgString)));
}

function defaultType(container: HTMLElement): string {
    const t = container.closest('[data-visualization-type]')?.getAttribute('data-visualization-type');
    return t && t !== 'unknown' ? t : 'diagram';
}

/** `type-<base36 time>`, the stem of the file a drop target receives. */
export function fileStem(type: string): string {
    return `${type.replace(/[^a-z0-9_-]+/gi, '-')}-${Date.now().toString(36)}`;
}

/** Decode the payload of a base64 data: URL. */
export function dataUrlToBytes(dataUrl: string): Uint8Array<ArrayBuffer> {
    const bin = atob(dataUrl.slice(dataUrl.indexOf(',') + 1));
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return bytes;
}

/**
 * Write a PNG to the system clipboard. Accepts a pending PNG: handing
 * ClipboardItem a Promise<Blob> keeps the write inside the user gesture
 * that triggered it even while rasterization is still finishing (a write
 * issued after an await outside the gesture is refused). Resolves false
 * when the Clipboard API is unavailable or the write is denied.
 */
export function copyImageToClipboard(png: string | Promise<string>): Promise<boolean> {
    const clip: any = typeof navigator !== 'undefined' ? (navigator as any).clipboard : undefined;
    const Item: any = (globalThis as any).ClipboardItem;
    if (!clip?.write || !Item) return Promise.resolve(false);
    const toBlob = (d: string) => new Blob([dataUrlToBytes(d)], { type: 'image/png' });
    try {
        const value = typeof png === 'string' ? toBlob(png) : png.then(toBlob);
        return Promise.resolve(clip.write([new Item({ 'image/png': value })])).then(
            () => true,
            (err: unknown) => { console.warn('diagramDrag: clipboard write failed', err); return false; },
        );
    } catch (err) {
        console.warn('diagramDrag: clipboard write failed', err);
        return Promise.resolve(false);
    }
}

/**
 * Attach the image as a real File on the drag. `DownloadURL` only yields a
 * file for NATIVE targets (Finder, Explorer); a browser page or an Electron
 * app (Slack desktop) reads `dataTransfer.files`, which only a File item
 * added at dragstart populates. Best-effort: not every browser allows it.
 */
export function addFileItem(dt: DataTransfer, dataUrl: string, filename: string, mime: string): boolean {
    try {
        const file = new File([dataUrlToBytes(dataUrl)], filename, { type: mime });
        return !!dt.items?.add(file);
    } catch (err) {
        console.warn('diagramDrag: could not attach File item to drag', err);
        return false;
    }
}

/** Publish an image on the drag pasteboard. */
function publish(dt: DataTransfer, mime: string, filename: string, dataUrl: string): void {
    dt.effectAllowed = 'copy';
    // Chromium: delivers a real file (via the OS file-promise mechanism) to
    // native drop targets such as Slack, Finder and Explorer.
    dt.setData('DownloadURL', `${mime}:${filename}:${dataUrl}`);
    // Browser pages and Electron apps read dataTransfer.files instead.
    addFileItem(dt, dataUrl, filename, mime);
    // Rich-text web drop targets embed the image.
    // The leading comment is the carrier the macOS native drag helper reads
    // the PNG from (app/utils/drag_pasteboard_helper.py): Chromium hands
    // text/html to the OS as public.html, so no side channel is needed.
    // Invisible to HTML drop targets.
    const carrier = mime === 'image/png' ? `<!--ziya-drag-png:${dataUrl}-->` : '';
    dt.setData('text/html', `${carrier}<img src="${dataUrl}" alt="${filename}">`);
}

/**
 * Make `container` (or `options.dragSource` inside it) a drag source for the
 * diagram the container holds. Returns a cleanup that removes the listeners
 * and the draggable attribute.
 *
 * An explicit `dragSource` (a handle) is draggable at rest. The container
 * itself is armed per gesture at pointerdown, and only when it holds a
 * picture (an <svg> or <canvas>): a plugin's error panel is plain HTML in
 * the same container, and a draggable ancestor pre-empts text selection
 * and scrollbar drags inside it (the "Mermaid Lexical Error" block could
 * neither be scrolled nor selected).
 */
export function attachDiagramDrag(container: HTMLElement, options: DiagramDragOptions = {}): () => void {
    const rasterize = options.rasterize
        ?? ((s, w, h, sc) => rasterizeSvgToPng(s, w, h, sc, (options.getBackground ?? (() => resolveBackground(container)))()));
    const scale = options.scale ?? Math.min(Math.max(window.devicePixelRatio || 1, 1), 2);
    const prepareDelay = options.prepareDelayMs ?? 120;
    const dragSource = options.dragSource ?? container;
    const handle = options.dragSource;

    let prepared: PreparedSvg | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let disposed = false;

    const currentSvg = (): SVGSVGElement | null => container.querySelector('svg');
    const hasPicture = (): boolean => !!container.querySelector('svg, canvas');

    const fingerprintOf = (svg: SVGSVGElement | null, background: string): string => {
        // Without an SVG there is nothing cheap to fingerprint; a plugin
        // export is then prepared once per attach.
        if (!svg) return `${background}|custom`;
        const box = svg.getBoundingClientRect();
        return `${background}|${Math.round(box.width)}x${Math.round(box.height)}|${svg.outerHTML.length}|${svg.outerHTML}`;
    };

    const customExport = (): Promise<string> | null => {
        if (!options.exportPng) return null;
        try {
            return options.exportPng() ?? null;
        } catch (err) {
            console.warn('diagramDrag: plugin PNG export threw', err);
            return null;
        }
    };

    // Re-prepares only when the diagram changed, so it is cheap to call at
    // dragstart as well as on pointer entry.
    const prepare = (): void => {
        if (disposed) return;
        const svg = currentSvg();
        const background = (options.getBackground ?? (() => resolveBackground(container)))();
        const fingerprint = fingerprintOf(svg, background);
        if (prepared && prepared.fingerprint === fingerprint) return;

        const custom = customExport();
        const serialized = svg ? serializeSvgForImage(svg) : null;
        if (!serialized && !custom) return;
        const entry: PreparedSvg = {
            fingerprint,
            svgString: serialized?.svgString ?? '',
            width: serialized?.width ?? 0,
            height: serialized?.height ?? 0,
            png: null,
            pngPromise: null,
        };
        prepared = entry;
        const png = custom ?? rasterize(entry.svgString, entry.width, entry.height, scale);
        entry.pngPromise = png;
        png.then(
            (png) => { if (prepared === entry) entry.png = png; },
            (err) => { console.warn('diagramDrag: PNG rasterization failed, SVG fallback stays', err); },
        );
    };

    const onPointerEnter = (): void => {
        if (timer) clearTimeout(timer);
        timer = setTimeout(() => { timer = null; prepare(); }, prepareDelay);
    };
    const onPointerLeave = (): void => {
        if (timer) { clearTimeout(timer); timer = null; }
    };
    // A press is the strongest signal a drag is coming: prepare NOW, with no
    // hover delay, so the PNG is ready before the drag threshold is crossed
    // instead of the SVG fallback shipping; and arm the native helper.
    const onPointerDown = (e: PointerEvent): void => {
        if (e.button !== 0) return;
        if (!handle) {
            // Nothing to drag (an error panel, a not-yet-rendered plugin):
            // leave the browser's selection and scrolling alone.
            if (!hasPicture()) return;
            const target = e.target as Element | null;
            if (target?.closest('img, a[href], input, button, textarea, select')) return;
            container.draggable = true;
        }
        prepare();
        armNativeDrag();
    };
    const releasePress = (): void => {
        if (!handle) container.draggable = false;
    };

    const onDragStart = (e: DragEvent): void => {
        const dt = e.dataTransfer;
        if (!dt) return;
        // A nested natively-draggable element (an <img>, a link) already
        // supplies its own payload; leave it alone.
        const target = e.target as Element | null;
        if (target && target !== dragSource && target.closest('img, a[href]')) return;

        const type = (options.getType ?? (() => defaultType(container)))();

        // Serialize now if the pointer never rested on the diagram, so at
        // least the SVG travels and the PNG is started for next time; a
        // no-op when the prepared image is current.
        prepare();
        if (prepared?.png) {
            publish(dt, 'image/png', `${fileStem(type)}.png`, prepared.png);
            return;
        }

        const canvas = container.querySelector('canvas');
        if (!currentSvg() && canvas) {
            try {
                publish(dt, 'image/png', `${fileStem(type)}.png`, canvas.toDataURL('image/png'));
            } catch (err) {
                console.warn('diagramDrag: canvas export failed', err);
            }
            return;
        }

        if (prepared?.svgString) {
            publish(dt, 'image/svg+xml', `${fileStem(type)}.svg`, svgToDataUrl(prepared.svgString));
        }
    };

    const onCopyClick = (e: MouseEvent): void => {
        e.preventDefault();
        e.stopPropagation();
        prepare();
        let png: string | Promise<string> | null = prepared?.png ?? prepared?.pngPromise ?? null;
        if (!png) {
            // Canvas-only diagram with no exporter: read the canvas directly.
            const canvas = container.querySelector('canvas');
            try {
                if (canvas && !currentSvg()) png = canvas.toDataURL('image/png');
            } catch (err) {
                console.warn('diagramDrag: canvas export failed', err);
            }
        }
        const done = png ? copyImageToClipboard(png) : Promise.resolve(false);
        void done.then((ok) => options.onCopied?.(ok));
    };

    // The attribute marks the element as wired; `draggable` on the container
    // is decided per press above.
    if (handle) handle.draggable = true;
    dragSource.setAttribute('data-diagram-drag', 'true');
    container.addEventListener('pointerenter', onPointerEnter);
    container.addEventListener('pointerleave', onPointerLeave);
    container.addEventListener('pointerdown', onPointerDown);
    container.addEventListener('pointerup', releasePress);
    container.addEventListener('pointercancel', releasePress);
    container.addEventListener('dragend', releasePress);
    dragSource.addEventListener('dragstart', onDragStart);
    options.copySource?.addEventListener('click', onCopyClick);
    options.copySource?.setAttribute('data-diagram-copy', 'true');

    return () => {
        disposed = true;
        if (timer) clearTimeout(timer);
        container.removeEventListener('pointerenter', onPointerEnter);
        container.removeEventListener('pointerleave', onPointerLeave);
        container.removeEventListener('pointerdown', onPointerDown);
        container.removeEventListener('pointerup', releasePress);
        container.removeEventListener('pointercancel', releasePress);
        container.removeEventListener('dragend', releasePress);
        dragSource.removeEventListener('dragstart', onDragStart);
        options.copySource?.removeEventListener('click', onCopyClick);
        options.copySource?.removeAttribute('data-diagram-copy');
        dragSource.draggable = false;
        dragSource.removeAttribute('data-diagram-drag');
        prepared = null;
    };
}
