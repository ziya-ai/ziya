/**
 * File links in chat prose: `[report](.ziya/documents/report.md)`.
 *
 * Only an explicit markdown link counts -- never a bare path, a codespan, or
 * a block/diff header (those have their own chips).  A link whose href has
 * no scheme, or the file: scheme, names a file; MarkdownRenderer mounts
 * FilePathLink for it (frontend/src/components/FileDragChip.tsx), which
 * opens the file through POST /api/v1/drag/open (app/api/drag_helper.py)
 * and drags it out through the existing attachFileDrag.
 */

export const FILE_OPEN_PATH = '/api/v1/drag/open';

const SCHEME_RE = /^[a-z][a-z0-9+.-]*:/i;
const WINDOWS_ABS_RE = /^[a-z]:[\\/]/i;

export function isAbsolutePath(p: string): boolean {
    return p.startsWith('/') || WINDOWS_ABS_RE.test(p);
}

/**
 * The file path a link href names, or null when the href is anything else:
 * a web URL, mailto:, ziya://, a fragment, or protocol-relative.  A relative
 * href is returned as written (minus a `#fragment`); a file:// href as the
 * absolute path it encodes.
 */
export function fileLinkPath(href: string | null | undefined): string | null {
    if (typeof href !== 'string') return null;
    let s = href.trim();
    if (!s || s.startsWith('#') || s.startsWith('//')) return null;
    if (/^file:/i.test(s)) {
        // file:///x, file://localhost/x, file:/x
        s = s.replace(/^file:(\/\/[^/]*)?/i, '');
        try { s = decodeURIComponent(s); } catch { /* keep as written */ }
        return isAbsolutePath(s) ? s : null;
    }
    if (!WINDOWS_ABS_RE.test(s) && SCHEME_RE.test(s)) return null;
    s = s.split('#')[0];
    try { s = decodeURIComponent(s); } catch { /* keep as written */ }
    return s || null;
}

/**
 * `file://` URI for `path`, resolved against `projectRoot` when relative.
 * Null when relative and no root is known (the anchor then keeps the raw
 * path, which still drags; the server reports the real URI on open).
 */
export function fileUri(path: string, projectRoot?: string | null): string | null {
    const abs = isAbsolutePath(path)
        ? path
        : projectRoot ? `${projectRoot.replace(/[\\/]+$/, '')}/${path}` : null;
    if (!abs) return null;
    const posix = abs.replace(/\\/g, '/');
    // encodeURI leaves `#` and `?` alone; in a path they are characters.
    const enc = encodeURI(posix).replace(/#/g, '%23').replace(/\?/g, '%3F');
    return `file://${enc.startsWith('/') ? '' : '/'}${enc}`;
}

export interface OpenFileResult {
    opened: boolean;
    /** False when the server cannot act: browser on another machine, no opener. */
    supported: boolean;
    path?: string;
    uri?: string;
    reason?: string;
    reveal?: boolean;
}

/**
 * Ask the server to open `path` in its default app, or with `reveal` to
 * select it in the file browser.  Rejects with the server's detail on a
 * 4xx/5xx (outside the readable set, not a file, ...).
 */
export async function openProjectFile(
    path: string,
    reveal: boolean,
    fetchImpl: typeof fetch = fetch,
): Promise<OpenFileResult> {
    const projectPath = (window as any).__ZIYA_CURRENT_PROJECT_PATH__;
    const res = await fetchImpl(FILE_OPEN_PATH, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            ...(projectPath ? { 'X-Project-Root': projectPath } : {}),
        },
        body: JSON.stringify({ path, reveal }),
    });
    if (!res.ok) {
        let detail = `${res.status}`;
        try { detail = (await res.json()).detail || detail; } catch { /* keep status */ }
        throw new Error(detail);
    }
    return res.json();
}
