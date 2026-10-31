/**
 * Whole-file access for a show_document block (MarkdownRenderer.DocumentBlock).
 *
 * show_document may show only a section or line range (and caps a call at
 * 512 KB), but the user's Download and "Show whole file" are about the FILE.
 * Both read it through GET /api/v1/drag/file, which is confined exactly like
 * file_read (app/api/drag_helper.py), so no new server surface is involved.
 *
 * The caption's sha256 is of the file at the time it was shown; the fetched
 * bytes are hashed the same way so a file edited since then is reported,
 * not silently substituted.
 */
import { FILE_DRAG_PATH } from './fileDrag';

/** Caption fields from parseDocumentCaption (app/utils/show_document.py). */
export type CaptionFields = Record<string, string | undefined>;

/** "Show whole file" renders at most this much inline.  The text never
 *  reaches the model, but several MB of markdown in one block stalls the
 *  page; larger files are download-only. */
export const INLINE_FULL_MAX_BYTES = 2 * 1024 * 1024;

/** True when the block shows a strict sub-range of the file. */
export function isPartialDocument(fields: CaptionFields): boolean {
    if (!fields.lines || !fields.total_lines) return false;
    return fields.lines !== `1-${fields.total_lines}`;
}

function basename(path: string | undefined): string {
    return (path || '').replace(/\\/g, '/').split('/').filter(Boolean).pop() || 'document.md';
}

/** Name for a download of the SHOWN text: a partial range says so, so it is
 *  not mistaken for the whole file. */
export function documentDownloadName(fields: CaptionFields): string {
    const base = basename(fields.path);
    if (!isPartialDocument(fields)) return base;
    const tag = `.lines-${fields.lines}`;
    const dot = base.lastIndexOf('.');
    return dot > 0 ? base.slice(0, dot) + tag + base.slice(dot) : base + tag;
}

/** The language of a body show_document wrapped as code (a tilde fence that
 *  opens the body and closes it), or null for a markdown body. */
function shownCodeLang(shownBody: string): string | null {
    const m = shownBody.match(/^(~{3,})([\w+.-]*)\n/);
    if (!m) return null;
    return new RegExp(`\\n${m[1]}\\s*$`).test(shownBody) ? (m[2] || 'text') : null;
}

/** Markdown for the whole file, rendered the way the block was (markdown,
 *  or a code block in the same language). */
export function fullDocumentMarkdown(text: string, shownBody: string): string {
    const t = text.replace(/\n+$/, '');
    const lang = shownCodeLang(shownBody);
    if (lang === null) return t;
    let longest = 0;
    for (const line of t.split('\n')) {
        const m = line.match(/^ {0,3}(~+)/);
        if (m) longest = Math.max(longest, m[1].length);
    }
    const fence = '~'.repeat(Math.max(3, longest + 1));
    return `${fence}${lang}\n${t}\n${fence}`;
}

export interface WholeDocument {
    bytes: Uint8Array<ArrayBuffer>;
    text: string;
    mime: string;
    /** Hex SHA-256 of the bytes, or null where WebCrypto is unavailable. */
    sha256: string | null;
}

async function sha256Hex(bytes: Uint8Array): Promise<string | null> {
    const subtle = (globalThis as any).crypto?.subtle;
    if (!subtle) return null;
    try {
        const d = new Uint8Array(await subtle.digest('SHA-256', bytes));
        return Array.from(d, b => b.toString(16).padStart(2, '0')).join('');
    } catch {
        return null;
    }
}

export async function fetchWholeDocument(path: string, fetchFn: typeof fetch = fetch): Promise<WholeDocument> {
    const projectPath = (window as any).__ZIYA_CURRENT_PROJECT_PATH__;
    const res = await fetchFn(`${FILE_DRAG_PATH}?path=${encodeURIComponent(path)}`, {
        headers: projectPath ? { 'X-Project-Root': projectPath } : {},
    });
    if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try { detail = (await res.json()).detail || detail; } catch { /* keep status */ }
        throw new Error(detail);
    }
    // Bytes, not res.text(): text() drops a BOM, which would break the hash.
    const bytes = new Uint8Array(await res.arrayBuffer());
    return {
        bytes,
        text: new TextDecoder('utf-8').decode(bytes),
        mime: (res.headers.get('content-type') || 'text/plain').trim(),
        sha256: await sha256Hex(bytes),
    };
}

/** The fetched file differs from the one the block was built from. */
export function changedSinceShown(doc: WholeDocument, fields: CaptionFields): boolean {
    return !!(fields.sha256 && doc.sha256 && fields.sha256 !== doc.sha256);
}

export function countLines(text: string): number {
    return text === '' ? 0 : text.replace(/(\r\n|\r|\n)$/, '').split(/\r\n|\r|\n/).length;
}

export function saveBlob(blob: Blob, name: string): void {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    try {
        a.click();
    } finally {
        document.body.removeChild(a);
        // Deferred: revoking synchronously can cancel the download in some
        // browsers before it has read the blob.
        setTimeout(() => URL.revokeObjectURL(url), 0);
    }
}

export type DownloadOutcome =
    | { kind: 'whole' }
    | { kind: 'changed' }
    /** The file could not be read; the shown text was saved instead. */
    | { kind: 'shown'; reason?: string };

/** Save the whole file; if it cannot be read, save the shown text. */
export async function downloadDocument(
    shownBody: string, fields: CaptionFields, fetchFn?: typeof fetch,
): Promise<DownloadOutcome> {
    let reason: string | undefined;
    if (fields.path) {
        try {
            const doc = await fetchWholeDocument(fields.path, fetchFn);
            saveBlob(new Blob([doc.bytes], { type: doc.mime }), basename(fields.path));
            return changedSinceShown(doc, fields) ? { kind: 'changed' } : { kind: 'whole' };
        } catch (e) {
            reason = e instanceof Error ? e.message : String(e);
        }
    }
    saveBlob(new Blob([shownBody], { type: 'text/markdown;charset=utf-8' }), documentDownloadName(fields));
    return { kind: 'shown', reason };
}
