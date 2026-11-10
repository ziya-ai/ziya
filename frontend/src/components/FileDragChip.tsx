import React, { useEffect, useRef, useState } from 'react';
import { message } from 'antd';
import { attachFileDrag, fileBasename } from '../utils/fileDrag';
import { fileUri, openProjectFile } from '../utils/fileLinks';

interface FileDragChipProps {
    /** Project-relative path named by a file_read / file_write block. */
    path: string;
    isDarkMode: boolean;
}

/**
 * Wire `ref`'s element as a drag source for `path` via attachFileDrag.
 * Returns the fetch error, if any, so the element can say why it is inert.
 */
function useFileDrag<T extends HTMLElement = HTMLSpanElement>(path: string) {
    const ref = useRef<T>(null);
    const [failed, setFailed] = useState<string | null>(null);
    useEffect(() => {
        const el = ref.current;
        if (!el) return;
        return attachFileDrag(el, {
            path,
            onError: (m) => {
                setFailed(m);
                message.warning({ content: `Cannot drag ${fileBasename(path)}: ${m}`, key: `file-drag-${path}` });
            },
        });
    }, [path]);
    return { ref, failed };
}

/**
 * A small draggable chip in a file-operation block's header. Dragging it
 * out of the browser delivers the file itself (fetched from the server on
 * press) to Finder, Slack, Docs or an editor. It is a separate element so
 * the header text beside it stays selectable; a draggable ancestor would
 * swallow every press (see tableDrag.ts).
 */
export const FileDragChip: React.FC<FileDragChipProps> = ({ path, isDarkMode }) => {
    const { ref, failed } = useFileDrag(path);

    return (
        <span
            ref={ref}
            className="file-drag-chip"
            title={failed ? `Not draggable: ${failed}` : `Drag ${fileBasename(path)} out to another app`}
            style={{
                display: 'inline-flex', alignItems: 'center', gap: 4,
                marginLeft: 8, padding: '0 6px', borderRadius: 4,
                fontSize: '11px', fontWeight: 'normal', letterSpacing: 0,
                cursor: failed ? 'not-allowed' : 'grab', userSelect: 'none',
                opacity: failed ? 0.5 : 0.85,
                border: `1px dashed ${isDarkMode ? 'rgba(255,255,255,0.35)' : 'rgba(0,0,0,0.3)'}`,
            }}
        >
            ⠿ {fileBasename(path)}
        </span>
    );
};

/**
 * The 📄 glyph in front of a shown document's path, as a drag source for
 * that file.  Only the glyph is draggable, so the path text beside it stays
 * selectable (same reason the chip is a separate element).
 */
export const FileDragIcon: React.FC<{ path: string }> = ({ path }) => {
    const { ref, failed } = useFileDrag(path);
    return (
        <span
            ref={ref}
            className="file-drag-icon"
            role="img"
            aria-label={`Drag ${fileBasename(path)}`}
            title={failed ? `Not draggable: ${failed}` : `Drag ${fileBasename(path)} out to another app`}
            style={{
                cursor: failed ? 'not-allowed' : 'grab', userSelect: 'none',
                opacity: failed ? 0.5 : 1, marginRight: 4,
            }}
        >📄</span>
    );
};

/** The path a file-operation header names, or null for any other block. */
export function filePathFromHeader(header: string | undefined | null): string | null {
    const m = /^file (?:read|write):\s+(.+?)\s*$/.exec(header || '');
    return m ? m[1] : null;
}

interface FilePathLinkProps {
    /** Project-relative or absolute path the link's href named. */
    path: string;
    title?: string;
    children?: React.ReactNode;
}

/**
 * A markdown link in prose whose href names a file
 * (`[report](.ziya/documents/report.md)`).  The anchor's href is the
 * file:// URI, so Copy Link yields a real address; a click asks the server
 * to open the file in its default app, Alt-click or the ⤴ glyph reveals it
 * in Finder/Explorer, and dragging it out carries the file itself
 * (attachFileDrag).  When the server is not on this machine the click
 * copies the URI instead of opening.
 */
export const FilePathLink: React.FC<FilePathLinkProps> = ({ path, title, children }) => {
    const { ref, failed } = useFileDrag<HTMLAnchorElement>(path);
    const [busy, setBusy] = useState(false);
    const name = fileBasename(path);
    const href = fileUri(path, (window as any).__ZIYA_CURRENT_PROJECT_PATH__) ?? path;

    const open = async (reveal: boolean): Promise<void> => {
        if (busy) return;
        setBusy(true);
        try {
            const r = await openProjectFile(path, reveal);
            if (!r.opened) {
                const uri = r.uri ?? href;
                try { await navigator.clipboard?.writeText(uri); } catch { /* no clipboard here */ }
                message.info({
                    content: `${r.reason || 'Cannot open from here'}; path copied: ${r.path ?? path}`,
                    key: `file-open-${path}`,
                });
            }
        } catch (err) {
            message.warning({
                content: `Cannot open ${name}: ${err instanceof Error ? err.message : String(err)}`,
                key: `file-open-${path}`,
            });
        } finally {
            setBusy(false);
        }
    };

    const dragNote = failed ? `not draggable: ${failed}` : 'drag to copy the file out';
    return (
        <a
            ref={ref}
            href={href}
            className="file-path-link"
            data-file-path={path}
            title={title ?? `Open ${name} · Alt-click to reveal in file browser · ${dragNote}`}
            onClick={(e) => { e.preventDefault(); e.stopPropagation(); void open(e.altKey); }}
        >
            {children}
            <span
                className="file-path-link-reveal"
                role="button"
                aria-label={`Reveal ${name} in file browser`}
                title="Reveal in file browser"
                onClick={(e) => { e.preventDefault(); e.stopPropagation(); void open(true); }}
                style={{ marginLeft: 3, fontSize: '0.85em', opacity: 0.7, cursor: 'pointer' }}
            >⤴</span>
        </a>
    );
};

export default FileDragChip;
