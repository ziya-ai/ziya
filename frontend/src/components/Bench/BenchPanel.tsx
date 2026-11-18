/**
 * The hub's body: lens strip, weight, board, environment.  A renderer of the
 * useBench() view-model handed in as a prop -- it imports no API client and
 * derives no placement state.  BenchModal (a shell) owns the hook; the rail
 * and palette shells (slices 7, 10) will reuse this panel unchanged.
 */
import React, { useState } from 'react';
import type { BenchItemDetail, BenchItemView, FindResponse, ReauthorizeResult, TokenFlash } from '../../hooks/useBench';
import type { Layer, PlacementState } from '../../utils/activation';
import type { Skill, SkillCreate, SkillUpdate } from '../../types/skill';
import type { Attention } from '../../utils/headerAttention';
import { attentionButtonStyle, attentionTooltip } from '../../utils/headerAttention';
import { useBenchActions } from './BenchActions';
import { BenchBoard } from './BenchBoard';
import { BenchFind } from './BenchFind';
import { CapabilityDetail } from './CapabilityDetail';
import { hasPackages } from './packageTree';
import type { GroupBy } from './packageTree';
import { LensStrip } from './LensStrip';
import { SkillEditor } from './SkillEditor';
import { WeightGauge } from './WeightGauge';
import { WithinReachNotice } from './WithinReachNotice';
import { NotInstalledHere, missingCountByRoot } from './NotInstalledHere';
import type { MissingCapability } from './NotInstalledHere';
import { PackagedAgents } from './PackagedAgents';
import type { PackagedAgentsModel } from './PackagedAgents';
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
    /** What a later definition change does: ask, or follow (per server or per registry provider). */
    setUpdatePolicy?: (server: string, policy: 'ask' | 'follow', scope?: 'server' | 'publisher') => Promise<BenchItemDetail>;
    flash?: TokenFlash | null;
    /** Find view (slice 8b); absent = no arrange/find toggle. */
    find?: (q: string, n?: number, registriesOff?: string[]) => Promise<FindResponse>;
    install?: (source: string, id: string, state: PlacementState | null) => Promise<unknown>;
    /** Install an MCP server from its registry (Find view). */
    installServer?: (serviceId: string, providerId?: string | null) => Promise<{ server_name?: string; warnings?: string[] }>;
    /** Skill editing (slice 6 edit mode).  Custom skills only; the detail says which. */
    createSkill?: (data: SkillCreate) => Promise<Skill>;
    updateSkill?: (skillId: string, key: string, data: SkillUpdate) => Promise<void>;
    deleteSkill?: (skillId: string, key: string) => Promise<void>;
    /** What installed packages need that is not configured here. */
    missing?: MissingCapability[];
}

export interface BenchPanelProps {
    bench: BenchPanelModel;
    shellAttention?: Attention;
    onOpenShellConfig?: () => void;
    /** MCP config / quarantine / connection attention from useHeaderAttention;
     *  drawn on the "MCP servers" button, the way in to the panel that explains it. */
    mcpAttention?: Attention;
    /** Shows a "⌘K" affordance beside the lens strip. */
    onOpenPalette?: () => void;
    /** Agents installed with packages; absent = no agent list. */
    agents?: PackagedAgentsModel;
}

/** localStorage key for the board's grouping choice ('none' | 'package'). */
export const GROUP_PREF_KEY = 'ZIYA_BENCH_GROUP';

export const BenchPanel: React.FC<BenchPanelProps> = ({ bench, shellAttention, onOpenShellConfig, mcpAttention, onOpenPalette, agents }) => {
    const { openRegistry, openMcpServers } = useBenchActions();
    const [selectedKey, setSelectedKey] = useState<string | null>(null);
    // arrange = the board; find = a query to the sources (slice 8b).
    const [mode, setMode] = useState<'arrange' | 'find'>('arrange');
    // "＋ New skill": the editor takes the drawer slot until saved or cancelled.
    const [creating, setCreating] = useState(false);
    // Set when Find is opened for one name (a server a package is missing).
    const [findQuery, setFindQuery] = useState('');
    const [groupBy, setGroupBy] = useState<GroupBy>(() => {
        try { return localStorage.getItem(GROUP_PREF_KEY) === 'package' ? 'package' : 'none'; } catch { return 'none'; }
    });
    const chooseGroup = (g: GroupBy) => {
        setGroupBy(g);
        try { localStorage.setItem(GROUP_PREF_KEY, g); } catch { /* private mode */ }
    };
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
    // Offered only when something on this bench came from a package.
    const packaged = hasPackages(bench.items);
    return (
        // Scrolls when the window is too short for the board's floor plus the strips under it.
        <div data-testid="bench-panel" style={{ display: 'flex', flexDirection: 'column', gap: 12, flex: 1, minHeight: 0,
                                               overflowY: 'auto' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <LensStrip lenses={bench.lenses} lens={bench.lens} deltas={bench.deltas} onChange={bench.setLens}
                           items={bench.items} />
                <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
                    {packaged && !finding && (
                        <div role="tablist" aria-label="Group"
                             style={{ display: 'inline-flex', borderRadius: 6, overflow: 'hidden',
                                      border: '1px solid var(--mockup-border, rgba(127,127,127,0.35))' }}>
                            {([['none', 'flat'], ['package', 'by package']] as const).map(([g, label]) => (
                                <button key={g} type="button" role="tab" aria-selected={groupBy === g}
                                        data-testid={`groupby-${g}`} onClick={() => chooseGroup(g)}
                                        style={{ border: 'none', padding: '2px 10px', fontSize: 11, cursor: 'pointer',
                                                 color: groupBy === g ? '#0d9488' : 'inherit', fontWeight: groupBy === g ? 600 : 400,
                                                 background: groupBy === g ? 'rgba(20,184,166,0.15)' : 'transparent' }}>
                                    {label}
                                </button>
                            ))}
                        </div>
                    )}
                    {bench.createSkill && !finding && (
                        <button type="button" data-testid="bench-new-skill" title="Write a new custom skill for this project"
                                onClick={() => { setSelectedKey(null); setCreating(true); }}
                                style={{ fontSize: 11, padding: '2px 8px', borderRadius: 6, cursor: 'pointer',
                                         color: '#0d9488', background: 'none', border: '1px solid #0d9488' }}>
                            ＋ New skill
                        </button>
                    )}
                    {bench.find && (
                        <div role="tablist" aria-label="Mode"
                             style={{ display: 'inline-flex', borderRadius: 6, overflow: 'hidden',
                                      border: '1px solid var(--mockup-border, rgba(127,127,127,0.35))' }}>
                            {(['arrange', 'find'] as const).map(m => (
                                <button key={m} type="button" role="tab" aria-selected={mode === m} data-testid={`mode-${m}`}
                                        onClick={() => { setFindQuery(''); setMode(m); }}
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
            {!finding && <WithinReachNotice items={bench.items} place={bench.place} />}
            {finding ? (
                <BenchFind key={findQuery} initialQuery={findQuery}
                           model={{ find: bench.find!, install: bench.install, installServer: bench.installServer,
                                    lens: bench.lens }} />
            ) : (
                // A floor: at minHeight 0 the row collapsed and the lanes
                // (minHeight 120) painted under whatever follows the board.
                <div data-testid="bench-board-row" style={{ display: 'flex', gap: 12, flex: 1, minHeight: 180 }}>
                    <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
                        <BenchBoard bench={bench} selectedKey={selectedKey} onSelect={setSelectedKey}
                                    groupBy={packaged ? groupBy : 'none'}
                                    missingByRoot={missingCountByRoot(bench.missing ?? [])} />
                    </div>
                    {creating && bench.createSkill ? (
                        <aside data-testid="bench-detail" aria-label="New skill"
                               style={{ width: 420, flexShrink: 0, minHeight: 0, overflowY: 'auto', paddingLeft: 12,
                                        borderLeft: '1px solid var(--mockup-border, rgba(127,127,127,0.35))' }}>
                            <SkillEditor title="New skill" saveLabel="Create" allowImport
                                         onSave={async draft => {
                                             const s = await bench.createSkill!(draft);
                                             setCreating(false);
                                             setSelectedKey(`skill:${s.name}`);
                                         }}
                                         onCancel={() => setCreating(false)} />
                        </aside>
                    ) : selected && (
                        <CapabilityDetail item={selected} onClose={() => setSelectedKey(null)} detail={bench.detail}
                                          place={bench.place} onToolToggle={bench.setTool} onReauthorize={bench.reauthorize}
                                          onUpdatePolicy={bench.setUpdatePolicy}
                                          onUpdateSkill={bench.updateSkill ? (id, draft) => bench.updateSkill!(id, selected.key, draft) : undefined}
                                          onDeleteSkill={bench.deleteSkill}
                                          delta={bench.flash?.byKey[selected.key]} />
                    )}
                </div>
            )}
            {!finding && (openRegistry || openMcpServers) && (
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                    {openRegistry && (
                        <button type="button" data-testid="bench-find-more" onClick={openRegistry}
                                style={{ fontSize: 11, padding: '4px 10px', borderRadius: 6,
                                         cursor: 'pointer', color: 'inherit', background: 'none',
                                         border: '1px dashed var(--mockup-border, rgba(127,127,127,0.5))' }}>
                            ＋ <b>Find more</b> <span style={{ opacity: 0.6 }}>· browse MCP servers in the registry</span>
                        </button>
                    )}
                    {/* The MCP Servers panel is where Reload Config lives.  A card's
                        drawer reaches it for THAT server; a server just added to
                        mcp_config.json has no card yet, so the board needs its own
                        way in. */}
                    {openMcpServers && (
                        <button type="button" data-testid="bench-mcp-servers" onClick={() => openMcpServers()}
                                data-attention={mcpAttention?.level ?? 'none'}
                                title={attentionTooltip('Status, startup logs and permissions for every configured server; Reload Config re-reads mcp_config.json and reconnects', mcpAttention ?? { level: 'none', reasons: [] })}
                                style={{ fontSize: 11, padding: '4px 10px', borderRadius: 6,
                                         cursor: 'pointer', color: 'inherit', background: 'none',
                                         border: '1px solid var(--mockup-border, rgba(127,127,127,0.35))',
                                         ...attentionButtonStyle(mcpAttention?.level ?? 'none') }}>
                            ⚙ <b>MCP servers</b> <span style={{ opacity: 0.6 }}>· status, logs, reload config</span>
                        </button>
                    )}
                </div>
            )}
            {!finding && (bench.missing?.length ?? 0) > 0 && (
                <NotInstalledHere missing={bench.missing!}
                                  onFind={bench.find ? name => { setFindQuery(name); setMode('find'); } : undefined} />
            )}
            {notHere.length > 0 && (
                <div data-testid="bench-not-installed" style={{ fontSize: 11, opacity: 0.7 }}>
                    Placed but not installed here: {notHere.map(k => k.slice(k.indexOf(':') + 1)).join(', ')}
                </div>
            )}
            {!finding && agents && <PackagedAgents model={agents} />}
            <EnvironmentRow items={env} warning={bench.environmentWarning}
                            onToggle={bench.setEnvironment ? (c, e) => void bench.setEnvironment!(c, e) : undefined}
                            shellAttention={shellAttention} onOpenShellConfig={onOpenShellConfig} />
        </div>
    );
};
