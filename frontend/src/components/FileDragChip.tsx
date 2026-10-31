import React, { useEffect, useRef, useState } from 'react';
import { message } from 'antd';
import { attachFileDrag, fileBasename } from '../utils/fileDrag';

interface FileDragChipProps {
    /** Project-relative path named by a file_read / file_write block. */
    path: string;
    isDarkMode: boolean;
}

/**
 * Wire `ref`'s element as a drag source for `path` via attachFileDrag.
 * Returns the fetch error, if any, so the element can say why it is inert.
 */
function useFileDrag(path: string) {
    const ref = useRef<HTMLSpanElement>(null);
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

export default FileDragChip;
