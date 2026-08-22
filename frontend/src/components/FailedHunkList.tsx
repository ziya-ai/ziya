import React from 'react';
import { CloseCircleOutlined } from '@ant-design/icons';
import {
    parseHunkHeaders,
    formatHunkRange,
    formatStage,
    formatHunkError,
} from '../utils/hunkErrorFormat';

interface FailedHunkListProps {
    /** Target file the diff was applied to. */
    filePath: string;
    /** The diff text, used to recover each hunk's line range and section. */
    diff?: string;
    /** Hunk ids the backend reported as failed. */
    failed: Array<number | string>;
    /** Per-hunk status map from the backend response. */
    hunkStatuses?: Record<string, any>;
}

// Neutral grey that reads acceptably against both the light and dark
// notification surfaces, avoiding a theme dependency in a transient popup.
const SUBTLE = 'rgba(127,127,127,0.16)';

/**
 * Failure breakdown for a diff that could not be applied cleanly.
 *
 * Shows which file, which hunks, where in the file they were aimed, and why
 * each one failed — replacing the raw `JSON.stringify(error_details)` dump
 * that previously carried no information beyond the hunk number.
 */
export const FailedHunkList: React.FC<FailedHunkListProps> = ({
    filePath,
    diff,
    failed,
    hunkStatuses,
}) => {
    const headers = React.useMemo(() => parseHunkHeaders(diff || ''), [diff]);
    const headerByNumber = React.useMemo(() => {
        const map = new Map<number, ReturnType<typeof parseHunkHeaders>[number]>();
        headers.forEach(h => map.set(h.number, h));
        return map;
    }, [headers]);

    const total = headers.length;

    return (
        <div>
            <div
                style={{
                    fontFamily: 'monospace',
                    fontSize: '12px',
                    wordBreak: 'break-all',
                    padding: '4px 6px',
                    borderRadius: '3px',
                    background: SUBTLE,
                    marginBottom: '8px',
                }}
            >
                {filePath}
            </div>
            {failed.length > 0 && (
                <div style={{ fontSize: '12px', opacity: 0.75, marginBottom: '6px' }}>
                    {total > 0
                        ? `${failed.length} of ${total} hunk${total === 1 ? '' : 's'} failed`
                        : `${failed.length} hunk${failed.length === 1 ? '' : 's'} failed`}
                </div>
            )}
            <ul style={{ margin: 0, padding: 0, listStyle: 'none' }}>
                {failed.map((hunkId, index) => {
                    const status = hunkStatuses?.[String(hunkId)];
                    const header = headerByNumber.get(Number(hunkId));
                    const range = formatHunkRange(header);
                    const formatted = formatHunkError(status?.error_details);
                    return (
                        <li key={index} style={{ marginBottom: '10px', lineHeight: 1.5 }}>
                            <div>
                                <CloseCircleOutlined
                                    style={{ color: '#ff4d4f', marginRight: '6px' }}
                                />
                                <strong>Hunk #{hunkId}</strong>
                                {range && (
                                    <span style={{ opacity: 0.75 }}> &middot; {range}</span>
                                )}
                                {header?.context && (
                                    <span
                                        style={{
                                            opacity: 0.75,
                                            fontFamily: 'monospace',
                                            fontSize: '11px',
                                        }}
                                    >
                                        {' '}
                                        &middot; {header.context}
                                    </span>
                                )}
                            </div>
                            <div style={{ marginLeft: '22px', fontSize: '12px' }}>
                                <span
                                    style={{
                                        display: 'inline-block',
                                        padding: '0 5px',
                                        marginRight: '6px',
                                        borderRadius: '2px',
                                        background: SUBTLE,
                                        fontSize: '11px',
                                    }}
                                >
                                    {formatStage(status?.stage)}
                                </span>
                                {formatted?.summary || 'Failed for an unreported reason.'}
                            </div>
                            {formatted?.detail && (
                                <pre
                                    style={{
                                        marginLeft: '22px',
                                        marginTop: '4px',
                                        marginBottom: 0,
                                        padding: '4px 6px',
                                        borderRadius: '3px',
                                        background: SUBTLE,
                                        fontSize: '11px',
                                        whiteSpace: 'pre-wrap',
                                        maxHeight: '140px',
                                        overflow: 'auto',
                                    }}
                                >
                                    {formatted.detail}
                                </pre>
                            )}
                        </li>
                    );
                })}
            </ul>
        </div>
    );
};
