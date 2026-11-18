/**
 * One lane of the Bench board in "by package" mode: collapsible package rows,
 * component rows nested inside, cards at the leaves.  A renderer like the
 * board: the cards are drawn by the board's own ``renderCard`` and a package
 * move is reported up through ``onMove``; nothing is placed here.
 *
 * A package row moves as one, by drag (GROUP_DRAG_MIME) or by its "move…"
 * menu, which is the keyboard-reachable equivalent.
 */
import React from 'react';
import type { BenchItemView } from '../../hooks/useBench';
import type { PlacementState } from '../../utils/activation';
import { LANES } from './laneDnd';
import type { Lane } from './laneDnd';
import { buildPackageTree, countText, nodeItems, otherPackageCount } from './packageTree';
import type { GroupNode } from './packageTree';

export const GROUP_DRAG_MIME = 'application/x-ziya-bench-keys';

export interface PackageLaneProps {
    lane: Lane;
    cards: BenchItemView[];
    /** Open rows, keyed ``<lane>|<path>``; rows start collapsed. */
    open: Record<string, boolean>;
    onToggle: (id: string) => void;
    renderCard: (item: BenchItemView, reactKey: string) => React.ReactNode;
    onMove: (keys: string[], to: PlacementState, label: string) => void;
    /** Per top-level package: how many things it needs that are not installed here. */
    missingByRoot?: Record<string, number>;
}

const border = '1px solid var(--mockup-border, rgba(127,127,127,0.35))';

const GroupRow: React.FC<PackageLaneProps & { node: GroupNode; root: string; depth: number }> = props => {
    const { node, root, depth, lane, open, onToggle, renderCard, onMove, missingByRoot } = props;
    const id = `${lane.state}|${node.path}`;
    const isOpen = !!open[id];
    const all = nodeItems(node);
    const keys = all.map(i => i.key);
    return (
        <div data-testid={`group-${id}`} style={{ marginLeft: depth ? 12 : 0, display: 'flex', flexDirection: 'column', gap: 4 }}>
            <div
                data-testid={`group-row-${id}`}
                draggable
                onDragStart={e => {
                    e.dataTransfer.effectAllowed = 'move';
                    try {
                        e.dataTransfer.setData(GROUP_DRAG_MIME, JSON.stringify({ keys, label: node.label }));
                    } catch { /* some browsers restrict custom types */ }
                }}
                style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, padding: '4px 6px',
                         borderRadius: 6, border, cursor: 'grab' }}
            >
                <button type="button" data-testid={`group-toggle-${id}`} aria-expanded={isOpen}
                        onClick={() => onToggle(id)}
                        style={{ flex: 1, minWidth: 0, textAlign: 'left', border: 'none', background: 'none',
                                 color: 'inherit', cursor: 'pointer', padding: 0, fontSize: 12 }}>
                    {isOpen ? '▾' : '▸'} <b>{node.label}</b>
                    <span style={{ opacity: 0.6 }}> · {countText(all)}</span>
                    {depth === 0 && (missingByRoot?.[node.label] ?? 0) > 0 && (
                        <span data-testid={`group-missing-${id}`} style={{ color: '#d46b08' }}>
                            {' '}· {missingByRoot![node.label]} not installed
                        </span>
                    )}
                </button>
                <select data-testid={`group-move-${id}`} aria-label={`Move everything in ${node.label}`} value=""
                        onChange={e => {
                            const to = e.target.value as PlacementState | '';
                            if (to) onMove(keys, to, node.label);
                        }}
                        style={{ fontSize: 11, maxWidth: 76 }}>
                    <option value="">move…</option>
                    {LANES.filter(l => l.state !== lane.state).map(l => (
                        <option key={l.state} value={l.state}>{l.label}</option>
                    ))}
                </select>
            </div>
            {isOpen && (
                <>
                    {node.children.map(child => (
                        <GroupRow key={child.path} {...props} node={child} depth={depth + 1} />
                    ))}
                    {node.items.map(item => {
                        const others = otherPackageCount(item, root);
                        return (
                            <div key={`${id}|${item.key}`} style={{ marginLeft: 12 }}>
                                {renderCard(item, `${id}|${item.key}`)}
                                {others > 0 && (
                                    <div data-testid={`also-in-${id}|${item.key}`}
                                         style={{ fontSize: 10, opacity: 0.6, padding: '1px 8px' }}>
                                        also in {others} other package{others === 1 ? '' : 's'}
                                    </div>
                                )}
                            </div>
                        );
                    })}
                </>
            )}
        </div>
    );
};

export const PackageLane: React.FC<PackageLaneProps> = props => (
    <>
        {buildPackageTree(props.cards).map(node => (
            <GroupRow key={node.path} {...props} node={node} root={node.label} depth={0} />
        ))}
    </>
);
