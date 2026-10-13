/**
 * Native drag-out of a project file named by a file-operation block.
 *
 * A `file write: Docs/x.md` block in the chat names a file but does not hold
 * it (a patch-mode write carries one hunk; history drops tool inputs), so the
 * bytes are fetched from the server when the chip is pressed and published
 * at dragstart:
 *   - `DownloadURL` + a File item: Finder, Explorer, browser drop zones
 *     receive the real file under its own name,
 *   - `text/plain`: the file's text, for editors,
 *   - `text/html`: a `<!--ziya-drag-file:name:data:...-->` carrier the macOS
 *     helper (app/utils/drag_pasteboard_helper.py) turns into a file URL so
 *     Slack desktop accepts the drop.
 *
 * dragstart data must be set synchronously, so the fetch happens on
 * pointerdown (a drag threshold is hundreds of ms away) and is cached per
 * path for a short time; a drag that starts before it lands carries only
 * the path as text.
 */

import { addFileItem, fileStem } from './diagramDrag';
import { armNativeDrag } from './nativeDragArm';

export const FILE_DRAG_PATH = '/api/v1/drag/file';
const CACHE_MS = 15_000;

export interface FetchedFile {
    text: string;
    mime: string;
}

export interface FileDragOptions {
    /** Project-relative path of the file (the one in the block header). */
    path: string;
    /** Injection point for tests; defaults to GET /api/v1/drag/file. */
    fetchFile?: (path: string) => Promise<FetchedFile | null>;
    /** Called when the file could not be fetched (so the UI can say so). */
    onError?: (message: string) => void;
}

export function fileBasename(path: string): string {
    return path.replace(/\\/g, '/').split('/').filter(Boolean).pop() || 'file';
}

/** UTF-8 text as a base64 data: URL. */
export function textToDataUrl(text: string, mime: string): string {
    const bytes = new TextEncoder().encode(text);
    let bin = '';
    for (let i = 0; i < bytes.length; i += 0x8000) {
        bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    }
    return `data:${mime};base64,${btoa(bin)}`;
}

export async function fetchProjectFile(path: string): Promise<FetchedFile | null> {
    const projectPath = (window as any).__ZIYA_CURRENT_PROJECT_PATH__;
    const res = await fetch(`${FILE_DRAG_PATH}?path=${encodeURIComponent(path)}`, {
        headers: projectPath ? { 'X-Project-Root': projectPath } : {},
    });
    if (!res.ok) {
        let detail = `${res.status}`;
        try { detail = (await res.json()).detail || detail; } catch { /* keep status */ }
        throw new Error(detail);
    }
    const mime = (res.headers.get('content-type') || 'text/plain').split(';')[0].trim();
    return { text: await res.text(), mime };
}

/**
 * Make `el` a drag source for the file at `options.path`. Returns a cleanup.
 */
export function attachFileDrag(el: HTMLElement, options: FileDragOptions): () => void {
    const fetchFile = options.fetchFile ?? fetchProjectFile;
    let cached: { at: number; file: FetchedFile } | null = null;
    let inflight: Promise<void> | null = null;
    let disposed = false;

    const prepare = (): void => {
        if (disposed || inflight) return;
        if (cached && Date.now() - cached.at < CACHE_MS) return;
        inflight = fetchFile(options.path).then(
            (file) => { if (file && !disposed) cached = { at: Date.now(), file }; },
            (err) => { options.onError?.(err instanceof Error ? err.message : String(err)); },
        ).finally(() => { inflight = null; });
    };

    const onPointerDown = (e: PointerEvent): void => {
        if (e.button !== 0) return;
        prepare();
        armNativeDrag();
    };

    const onDragStart = (e: DragEvent): void => {
        const dt = e.dataTransfer;
        if (!dt) return;
        prepare();
        dt.effectAllowed = 'copy';
        const name = fileBasename(options.path);
        if (!cached) {
            // Nothing fetched yet: the path is still something to paste.
            dt.setData('text/plain', options.path);
            return;
        }
        const { text, mime } = cached.file;
        const dataUrl = textToDataUrl(text, mime);
        dt.setData('DownloadURL', `${mime}:${name}:${dataUrl}`);
        addFileItem(dt, dataUrl, name, mime);
        dt.setData('text/plain', text);
        // Carrier for the macOS helper, which strips it again; `fileStem`
        // is not used for the name because the dropped file should keep its own.
        dt.setData('text/html',
            `<!--ziya-drag-file:${name}:${dataUrl}--><span>${name}</span>`);
    };

    el.draggable = true;
    el.setAttribute('data-file-drag', 'true');
    el.addEventListener('pointerdown', onPointerDown);
    el.addEventListener('dragstart', onDragStart);
    return () => {
        disposed = true;
        el.removeEventListener('pointerdown', onPointerDown);
        el.removeEventListener('dragstart', onDragStart);
        el.draggable = false;
        el.removeAttribute('data-file-drag');
        cached = null;
    };
}

// Re-exported so a caller can name the file the same way the diagram drag does.
export { fileStem };
