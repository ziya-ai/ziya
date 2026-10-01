/**
 * The hub's body: lens strip, weight, board, environment.  A renderer of the
 * useBench() view-model handed in as a prop -- it imports no API client and
 * derives no placement state.  BenchModal (a shell) owns the hook; the rail
 * and palette shells (slices 7, 10) will reuse this panel unchanged.
 */
import React, { useState } from 'react';
import type { BenchItemDetail, BenchItemView, FindResponse, ReauthorizeResult, TokenFlash } from '../../hooks/useBench';
import type { Layer, PlacementState } from '../../utils/activation';
import type { Attention } from '../../utils/headerAttention';
import { useBenchActions } from './BenchActions';
import { BenchBoard } from './BenchBoard';
import { BenchFind } from './BenchFind';
import { CapabilityDetail } from './CapabilityDetail';
import { LensStrip } from './LensStrip';
import { WeightGauge } from './WeightGauge';
import { EnvironmentRow } from './EnvironmentRow';

export interface BenchPanelModel {
    ready: boolean;
    error: unknown;
    items: BenchItemView[];
    dropped: Array<{ key: string; layer: string; reason: string }>;
    weight: { always: number; ondemand: number; environment: number };
    deltas: Record<Layer, number>;
    lenses: Layer[];
    lens: Layer;
    setLens: (l: Layer) => void;
    place: (key: string, state: PlacementState | null, layer?: Layer) => Promise<void>;
    detail?: (key: string) => Promise<BenchItemDetail>;
    setEnvironment?: (category: string, enabled: boolean) => Promise<void>;
    environmentWarning?: string | null;
    isPickedUp?: (key: string) => boolean;
    setTool?: (server: string, tool: string, enabled: boolean) => Promise<BenchItemDetail>;
    /** Lift an MCP server's rug-pull quarantine (authorization gate). */
    reauthorize?: (server: string, force?: boolean) => Promise<ReauthorizeResult>;
    flash?: TokenFlash | null;
    /** Find view (slice 8b); absent = no arrange/find toggle. */
    find?: (q: string, n?: number) => Promise<FindResponse>;
    install?: (source: string, id: string, state: PlacementState | null) => Promise<unknown>;
}

export interface BenchPanelProps {
    bench: BenchPanelModel;
    shellAttention?: Attention;
    onOpenShellConfig?: () => void;
    /** Shows a "⌘K" affordance beside the lens strip. */
    onOpenPalette?: () => void;
}

export const BenchPanel: React.FC<BenchPanelProps> = ({ bench, shellAttention, onOpenShellConfig, onOpenPalette }) => {
    const { openRegistry } = useBenchActions();
    const [selectedKey, setSelectedKey] = useState<string | null>(null);
    // arrange = the board; find = a query to the sources (slice 8b).
    const [mode, setMode] = useState<'arrange' | 'find'>('arrange');
    if (!bench.ready) {
        return bench.error
            ? <div role="alert" style={{ color: '#d46b08' }}>Could not load the bench.</div>
            : <div data-testid="bench-loading" style={{ opacity: 0.6 }}>Loading…</div>;
    }
    const env = bench.items.filter(i => i.tier === 'environment');
    // A placement for something this project does not have (a made-global
    // chat, an uninstalled skill) must be named, not silently lost.
    const notHere = Array.from(new Set(
        bench.dropped.filter(d => d.reason === 'unknown_key').map(d => d.key)));
    // The drawer follows the selected card; a key that left the bench closes it.
    const selected = selectedKey ? bench.items.find(i => i.key === selectedKey) ?? null : null;
    const finding = mode === 'find' && !!bench.find;
    return (
        <div data-testid="bench-panel" style={{ display: 'flex', flexDirection: 'column', gap: 12, flex: 1, minHeight: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <LensStrip lenses={bench.lenses} lens={bench.lens} deltas={bench.deltas} onChange={bench.setLens}
                           items={bench.items} />
                <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
                    {bench.find && (
                        <div role="tablist" aria-label="Mode"
                             style={{ display: 'inline-flex', borderRadius: 6, overflow: 'hidden',
                                      border: '1px solid var(--mockup-border, rgba(127,127,127,0.35))' }}>
                            {(['arrange', 'find'] as const).map(m => (
                                <button key={m} type="button" role="tab" aria-selected={mode === m} data-testid={`mode-${m}`}
                                        onClick={() => setMode(m)}
                                        style={{ border: 'none', padding: '2px 10px', fontSize: 11, cursor: 'pointer',
                                                 color: mode === m ? '#0d9488' : 'inherit', fontWeight: mode === m ? 600 : 400,
                                                 background: mode === m ? 'rgba(20,184,166,0.15)' : 'transparent' }}>
                                    {m}
                                </button>
                            ))}
                        </div>
                    )}
                    {onOpenPalette && (
                        <button type="button" data-testid="bench-open-palette" onClick={onOpenPalette} title="Search and move by keyboard"
                                style={{ fontSize: 11, padding: '2px 8px', borderRadius: 6, cursor: 'pointer',
                                         color: 'inherit', background: 'none',
                                         border: '1px solid var(--mockup-border, rgba(127,127,127,0.35))' }}>
                            › move <kbd style={{ fontSize: 10, opacity: 0.7 }}>⌘K</kbd>
                        </button>
                    )}
                </div>
            </div>
            <WeightGauge items={bench.items} weight={bench.weight} delta={bench.flash?.always} />
            {finding ? (
                <BenchFind model={{ find: bench.find!, install: bench.install, lens: bench.lens }} />
            ) : (
                <div style={{ display: 'flex', gap: 12, flex: 1, minHeight: 0 }}>
                    <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
                        <BenchBoard bench={bench} selectedKey={selectedKey} onSelect={setSelectedKey} />
                    </div>
                    {selected && (
                        <CapabilityDetail item={selected} onClose={() => setSelectedKey(null)} detail={bench.detail}
                                          place={bench.place} onToolToggle={bench.setTool} onReauthorize={bench.reauthorize}
                                          delta={bench.flash?.byKey[selected.key]} />
                    )}
                </div>
            )}
            {openRegistry && !finding && (
                <button type="button" data-testid="bench-find-more" onClick={openRegistry}
                        style={{ alignSelf: 'flex-start', fontSize: 11, padding: '4px 10px', borderRadius: 6,
                                 cursor: 'pointer', color: 'inherit', background: 'none',
                                 border: '1px dashed var(--mockup-border, rgba(127,127,127,0.5))' }}>
                    ＋ <b>Find more</b> <span style={{ opacity: 0.6 }}>· browse MCP servers in the registry</span>
                </button>
            )}
            {notHere.length > 0 && (
                <div data-testid="bench-not-installed" style={{ fontSize: 11, opacity: 0.7 }}>
                    Placed but not installed here: {notHere.map(k => k.slice(k.indexOf(':') + 1)).join(', ')}
                </div>
            )}
            <EnvironmentRow items={env} warning={bench.environmentWarning}
                            onToggle={bench.setEnvironment ? (c, e) => void bench.setEnvironment!(c, e) : undefined}
                            shellAttention={shellAttention} onOpenShellConfig={onOpenShellConfig} />
        </div>
    );
};
