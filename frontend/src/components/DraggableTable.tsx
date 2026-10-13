import React, { useEffect, useRef } from 'react';
import { attachTableDrag } from '../utils/tableDrag';

interface DraggableTableProps {
    /** Markdown source of the table, carried as the text/plain payload. */
    markdown?: string;
    isDarkMode: boolean;
    children: React.ReactNode;
}

/**
 * Wraps a rendered markdown table so it can be dragged out from any press
 * that lands beside its text (padding, gridlines, empty cells). Pressing on
 * words still selects text. The drop target receives an image (native apps)
 * or a real table (rich-text editors). No handle: the gesture is the
 * affordance, and a corner button sat over content.
 */
export const DraggableTable: React.FC<DraggableTableProps> = ({ markdown, isDarkMode, children }) => {
    const wrapRef = useRef<HTMLDivElement>(null);
    // Read live at dragstart; the table re-renders while streaming.
    const markdownRef = useRef(markdown);
    markdownRef.current = markdown;

    useEffect(() => {
        const wrap = wrapRef.current;
        if (!wrap) return;
        return attachTableDrag(wrap, {
            getMarkdown: () => markdownRef.current,
        });
    }, []);

    return (
        <div
            ref={wrapRef}
            className="draggable-table"
            // Shrink-wrap the table: `.message-content table` is display:block,
            // so without this the wrapper would span the whole column while
            // the gridlines stop at the table.
            style={{ position: 'relative', marginBottom: '1em', width: 'fit-content', maxWidth: '100%' }}
        >
            {children}
        </div>
    );
};

export default DraggableTable;
