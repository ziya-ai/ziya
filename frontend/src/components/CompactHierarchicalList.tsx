/**
 * CompactHierarchicalList — dense rendering of hierarchical tool results
 * (layout: 'compact').  One line per row, rows grouped under a header (e.g.
 * repository) with a hit count, secondary detail right-aligned, directory
 * dimmed so the file name stands out.  Clicking a row expands its body,
 * which the caller renders exactly as the default layout does.
 */
import React, { useCallback, useState } from 'react';
import { groupHierarchicalRows, splitRowTitle, HierarchicalRow } from '../utils/hierarchicalGroups';

interface Props {
    rows: HierarchicalRow[];
    renderBody: (row: HierarchicalRow) => React.ReactNode;
    colors: { border: string; contentText: string };
}

const ellipsis: React.CSSProperties = { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' };

export const CompactHierarchicalList: React.FC<Props> = ({ rows, renderBody, colors }) => {
    const [open, setOpen] = useState<Set<number>>(() => new Set());
    const toggle = useCallback((index: number) => {
        setOpen(prev => {
            const next = new Set(prev);
            if (next.has(index)) next.delete(index); else next.add(index);
            return next;
        });
    }, []);

    return (
        <div data-testid="compact-hierarchical-list"
            style={{ padding: '4px 0', fontSize: '12px', lineHeight: 1.4, color: colors.contentText }}>
            {groupHierarchicalRows(rows).map((group, gi) => {
                const indent = group.name !== null ? 22 : 10;
                return (
                    <div key={group.name ?? `_ungrouped_${gi}`} data-group={group.name ?? undefined}
                        style={{ borderTop: gi > 0 ? `1px solid ${colors.border}` : 'none', padding: '2px 0' }}>
                        {group.name !== null && (
                            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', padding: '2px 10px', fontWeight: 600 }}>
                                <span style={ellipsis}>{group.name}</span>
                                <span style={{ opacity: 0.6, fontWeight: 'normal', flexShrink: 0 }}>
                                    {group.items.length} {group.items.length === 1 ? 'hit' : 'hits'}
                                </span>
                            </div>
                        )}
                        {group.items.map(({ row, index }) => {
                            const isOpen = open.has(index);
                            const { dir, base } = splitRowTitle(row.title, group.name);
                            return (
                                <div key={index} data-row-index={index}>
                                    <div role="button" tabIndex={0} aria-expanded={isOpen} title={row.title}
                                        onClick={() => toggle(index)}
                                        onKeyDown={e => {
                                            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(index); }
                                        }}
                                        style={{
                                            display: 'flex', alignItems: 'baseline', gap: '8px',
                                            padding: `1px 10px 1px ${indent}px`, cursor: 'pointer',
                                            opacity: row.dim ? 0.5 : 1,
                                        }}>
                                        <span aria-hidden="true" style={{ width: '10px', flexShrink: 0, opacity: 0.6 }}>{isOpen ? '▾' : '▸'}</span>
                                        <span style={{ display: 'flex', flex: 1, minWidth: 0 }}>
                                            {dir && <span style={{ ...ellipsis, opacity: 0.6, minWidth: 0 }}>{dir}</span>}
                                            <span style={{ ...ellipsis, flexShrink: 0, maxWidth: '100%' }}>{base}</span>
                                        </span>
                                        {row.detail && (
                                            <span style={{ opacity: 0.6, flexShrink: 0, fontVariantNumeric: 'tabular-nums' }}>{row.detail}</span>
                                        )}
                                    </div>
                                    {isOpen && (
                                        <div data-testid="compact-row-body" style={{ margin: `2px 10px 6px ${indent}px` }}>
                                            {renderBody(row)}
                                        </div>
                                    )}
                                </div>
                            );
                        })}
                    </div>
                );
            })}
        </div>
    );
};

export default CompactHierarchicalList;
