import React, { useEffect, useRef, useState } from 'react';
import { message } from 'antd';
import { attachFileDrag, fileBasename } from '../utils/fileDrag';

interface FileDragChipProps {
    /** Project-relative path named by a file_read / file_write block. */
    path: string;
    isDarkMode: boolean;
}

/**
 * A small draggable chip in a file-operation block's header. Dragging it
 * out of the browser delivers the file itself (fetched from the server on
 * press) to Finder, Slack, Docs or an editor. It is a separate element so
 * the header text beside it stays selectable; a draggable ancestor would
 * swallow every press (see tableDrag.ts).
 */
export const FileDragChip: React.FC<FileDragChipProps> = ({ path, isDarkMode }) => {
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

/** The path a file-operation header names, or null for any other block. */
export function filePathFromHeader(header: string | undefined | null): string | null {
    const m = /^file (?:read|write):\s+(.+?)\s*$/.exec(header || '');
    return m ? m[1] : null;
}

export default FileDragChip;
