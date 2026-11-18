/**
 * Bench board layout (design/capabilities-hub.md, slice 6a).
 *
 * Three lanes — on hand / within reach / shelved — for placeable items at the
 * current lens.  A renderer of the useBench() view-model: it takes the model
 * as a prop, never imports the API client, and never derives placement state
 * (every card's lane, ghost and pin come from ``item.atLens``).  Environment
 * items are not lanes and are not rendered here (EnvironmentRow, slice 6c).
 *
 * A pinned item also leaves a faint ghost outline in the lane it would
 * inherit; dropping it there clears the pin (laneDnd.dropIntent).
 *
 * Folding (laneFold.ts): built-in skills at their default always fold into
 * one row; everything else folds to the lane's measured height.  Either fold
 * row expands the lane, which then scrolls.
 */
import React, { useEffect, useRef, useState } from 'react';
import type { BenchItemView, TokenFlash } from '../../hooks/useBench';
import type { Layer, PlacementState } from '../../utils/activation';
import { CapabilityCard } from './CapabilityCard';
import { DRAG_MIME, DropIntent, LANES, dropIntent, rejectionText } from './laneDnd';
import { foldLane, foldSummary, sumTokens } from './laneFold';
import type { FoldedLane } from './laneFold';
import { GROUP_DRAG_MIME, PackageLane } from './PackageLane';
import type { GroupBy } from './packageTree';

export interface BenchBoardModel {
    items: BenchItemView[];
    /** ``layer`` defaults to the current lens; the detail drawer clears other layers. */
    place: (key: string, state: PlacementState | null, layer?: Layer) => Promise<void>;
    /** ✦ (slice 5): on-demand items the model loaded this turn. */
    isPickedUp?: (key: string) => boolean;
    /** Transient token movement since the last view (useBench.flash). */
    flash?: TokenFlash | null;
}

export interface BenchBoardProps {
    bench: BenchBoardModel;
    /** The card whose detail the panel's drawer shows; null = none. */
    selectedKey?: string | null;
    onSelect?: (key: string | null) => void;
    /** 'package' files each lane's cards under where they came from. */
    groupBy?: GroupBy;
    /** Shown on package rows: what each package needs that is not installed here. */
    missingByRoot?: Record<string, number>;
}

const CARD_ESTIMATE_PX = 52;
const LANE_HEADER_PX = 48;

/** Cards that fit the lane's current height; null until measured. */
function useLaneCapacity(ref: React.RefObject<HTMLElement>): number | null {
    const [capacity, setCapacity] = useState<number | null>(null);
    useEffect(() => {
        const el = ref.current;
        if (!el || typeof ResizeObserver === 'undefined') return;
        const measure = () => {
            const card = el.querySelector<HTMLElement>('[data-testid^="card-"]');
            const cardPx = (card?.getBoundingClientRect().height || CARD_ESTIMATE_PX) + 6; // + gap
            setCapacity(Math.max(1, Math.floor((el.clientHeight - LANE_HEADER_PX) / cardPx)));
        };
        measure();
        const ro = new ResizeObserver(measure);
        ro.observe(el);
        return () => ro.disconnect();
    }, [ref]);
    return capacity;
}

const foldRowStyle: React.CSSProperties = {
    textAlign: 'left', fontSize: 11, padding: '6px 8px', borderRadius: 6, opacity: 0.75,
    cursor: 'pointer', color: 'inherit', background: 'none',
    border: '1px dashed var(--mockup-border, rgba(127,127,127,0.5))',
};

const fmtK = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}K` : `${n}t`);

export const BenchBoard: React.FC<BenchBoardProps> = ({ bench, selectedKey = null, onSelect, groupBy = 'none', missingByRoot }) => {
    const [notice, setNotice] = useState<string | null>(null);
    const [expanded, setExpanded] = useState<Record<string, boolean>>({});
    const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({});
    const [pendingGroup, setPendingGroup] = useState<
        { keys: string[]; lane: PlacementState; label: string; count: number } | null>(null);
    const grouped = groupBy === 'package';
    const placeable = bench.items.filter(i => i.tier !== 'environment');
    const laneRefs = {
        always: useRef<HTMLElement>(null),
        ondemand: useRef<HTMLElement>(null),
        off: useRef<HTMLElement>(null),
    } as const;
    const capacities: Record<PlacementState, number | null> = {
        always: useLaneCapacity(laneRefs.always),
        ondemand: useLaneCapacity(laneRefs.ondemand),
        off: useLaneCapacity(laneRefs.off),
    };

    const apply = async (item: BenchItemView, intent: DropIntent) => {
        if (intent.kind === 'noop') return;
        if (intent.kind === 'rejected') { setNotice(rejectionText(intent.reason, item.name)); return; }
        setNotice(null);
        try {
            await bench.place(intent.key, intent.toState);
        } catch (e) {
            const reason = (e as { reason?: unknown })?.reason;
            setNotice(typeof reason === 'string' ? rejectionText(reason, item.name) : `Could not move ${item.name}`);
        }
    };

    /** A package row moved as one: every item under it that can take the lane. */
    const moveGroup = async (keys: string[], lane: PlacementState, label: string, confirmed = false) => {
        const moves = keys
            .map(k => placeable.find(i => i.key === k))
            .filter((i): i is BenchItemView => !!i)
            .map(item => ({ item, intent: dropIntent(item, lane) }))
            .filter(m => m.intent.kind === 'place');
        if (moves.length === 0) return;
        // On hand is sent with every turn; a whole package there is asked about first.
        if (lane === 'always' && moves.length > 1 && !confirmed) {
            setPendingGroup({ keys, lane, label, count: moves.length });
            return;
        }
        setPendingGroup(null);
        for (const m of moves) await apply(m.item, m.intent);
    };

    const onDrop = (lane: PlacementState) => (e: React.DragEvent) => {
        e.preventDefault();
        const group = e.dataTransfer.getData(GROUP_DRAG_MIME);
        if (group) {
            try {
                const g = JSON.parse(group) as { keys: string[]; label: string };
                void moveGroup(g.keys, lane, g.label);
            } catch { /* not ours */ }
            return;
        }
        const key = e.dataTransfer.getData(DRAG_MIME) || e.dataTransfer.getData('text/plain');
        const item = placeable.find(i => i.key === key);
        if (item) void apply(item, dropIntent(item, lane));
    };

    const card = (item: BenchItemView, reactKey: string = item.key) => (
        <CapabilityCard key={reactKey} item={item} onIntent={intent => void apply(item, intent)}
                        selected={item.key === selectedKey} onSelect={onSelect}
                        pickedUp={bench.isPickedUp?.(item.key) ?? false}
                        delta={bench.flash?.byKey[item.key]} />
    );

    return (
        <div data-testid="bench-board" style={{ display: 'flex', flexDirection: 'column', minHeight: 0, flex: 1 }}>
            {notice && (
                <div role="status" style={{ fontSize: 12, marginBottom: 8, color: '#d46b08' }}>{notice}</div>
            )}
            {pendingGroup && (
                <div role="alertdialog" data-testid="group-confirm"
                     style={{ fontSize: 12, marginBottom: 8, display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span>Put all {pendingGroup.count} items from <b>{pendingGroup.label}</b> on hand?
                        {' '}They are sent with every turn.</span>
                    <button type="button" data-testid="group-confirm-yes"
                            onClick={() => void moveGroup(pendingGroup.keys, pendingGroup.lane, pendingGroup.label, true)}>
                        Move {pendingGroup.count}</button>
                    <button type="button" data-testid="group-confirm-no" onClick={() => setPendingGroup(null)}>Cancel</button>
                </div>
            )}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 10, flex: 1, minHeight: 0 }}>
                {LANES.map(lane => {
                    const cards = placeable.filter(i => i.atLens.state === lane.state);
                    const ghosts = placeable.filter(i =>
                        !i.atLens.ghost && i.atLens.inherited === lane.state && i.atLens.state !== lane.state);
                    // Grouped lanes fold by package row instead of by height, and scroll.
                    const open = !grouped && !!expanded[lane.state];
                    const { shown, spaceFolded, builtinFolded }: FoldedLane = grouped
                        ? { shown: [], spaceFolded: [], builtinFolded: [] }
                        : foldLane(cards, capacities[lane.state], open, lane.state);
                    const expand = () => setExpanded(e => ({ ...e, [lane.state]: true }));
                    const anyFold = spaceFolded.length + builtinFolded.length > 0;
                    return (
                        <section
                            key={lane.state}
                            ref={laneRefs[lane.state]}
                            data-testid={`lane-${lane.state}`}
                            aria-label={lane.label}
                            onDragOver={e => { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; }}
                            onDrop={onDrop(lane.state)}
                            style={{
                                minHeight: 120, padding: 8, borderRadius: 8,
                                border: '1px solid var(--mockup-border, rgba(127,127,127,0.25))',
                                display: 'flex', flexDirection: 'column', gap: 6,
                                overflowY: open || grouped ? 'auto' : 'hidden',
                            }}
                        >
                            <header style={{ fontSize: 12 }}>
                                <div style={{ fontWeight: 600 }}>{lane.label}
                                    <span data-testid={`lane-tokens-${lane.state}`} style={{ fontWeight: 400, opacity: 0.6, marginLeft: 6 }}>
                                        {fmtK(sumTokens(cards))}{lane.state === 'ondemand' ? ' if loaded' : ''}</span></div>
                                <div style={{ opacity: 0.6, fontSize: 11 }}>
                                    {lane.hint}
                                </div>
                            </header>
                            {grouped && (
                                <PackageLane lane={lane} cards={cards} open={openGroups}
                                             onToggle={id => setOpenGroups(o => ({ ...o, [id]: !o[id] }))}
                                             renderCard={card} missingByRoot={missingByRoot}
                                             onMove={(keys, to, label) => void moveGroup(keys, to, label)} />
                            )}
                            {shown.map(item => card(item))}
                            {spaceFolded.length > 0 && (
                                <button type="button" data-testid={`fold-${lane.state}`} onClick={expand}
                                        title={spaceFolded.map(i => i.name).join('\n')} style={foldRowStyle}>
                                    ▸ <b>{spaceFolded.length} more</b>
                                    <div style={{ opacity: 0.7 }}>{foldSummary(spaceFolded)}</div>
                                </button>
                            )}
                            {builtinFolded.length > 0 && (
                                <button type="button" data-testid={`fold-builtin-${lane.state}`} onClick={expand}
                                        title={builtinFolded.map(i => i.name).join('\n')} style={foldRowStyle}>
                                    ▸ <b>{builtinFolded.length} built-in skill{builtinFolded.length === 1 ? '' : 's'}</b> at their default
                                    <div style={{ opacity: 0.7 }}>
                                        {builtinFolded.slice(0, 3).map(i => i.name).join(', ')}{builtinFolded.length > 3 ? ', …' : ''}
                                        {sumTokens(builtinFolded) > 0 && ` · ${fmtK(sumTokens(builtinFolded))} if loaded`}
                                    </div>
                                </button>
                            )}
                            {open && !anyFold && (
                                <button type="button" data-testid={`unfold-${lane.state}`}
                                        onClick={() => setExpanded(e => ({ ...e, [lane.state]: false }))}
                                        style={{ fontSize: 11, border: 'none', background: 'none', color: 'inherit',
                                                 cursor: 'pointer', opacity: 0.6, textAlign: 'left', padding: '2px 8px' }}>
                                    ▴ fold
                                </button>
                            )}
                            {ghosts.map(item => (
                                <div
                                    key={`ghost-${item.key}`}
                                    data-testid={`ghost-outline-${item.key}`}
                                    title={`Drop ${item.name} here to inherit`}
                                    style={{ fontSize: 11, padding: '4px 8px', borderRadius: 6, opacity: 0.4,
                                             border: '1px dashed var(--mockup-border, rgba(127,127,127,0.5))' }}
                                >{item.name}</div>
                            ))}
                        </section>
                    );
                })}
            </div>
        </div>
    );
};
