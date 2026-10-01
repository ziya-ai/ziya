/**
 * useBench() — the client core (design/capabilities-hub.md, slice 4).
 * Owns the fetch, the one-shot legacy migration per project, and the
 * derived per-lens view. Components fetch nothing and derive nothing.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
    fetchBench, fetchBenchDetail, fetchBenchFind, installBenchSkill, placeBench, reauthorizeBenchServer,
    setBenchEnvironment, setBenchTool,
    BenchView, BenchViewItem, BenchItemDetail, ToolDetail, FindResponse, InstallResult, ReauthorizeResult,
} from '../apis/benchApi';
import type { Layer, PlacementState } from '../utils/activation';
import { useLens, viewAtLens, LensView } from '../context/LensContext';
import {
    runBenchMigration, clearLegacySkillIds, markBenchLive, isBenchLive, MigratableSkill,
} from '../utils/benchMigration';
import { useCapabilityPickups } from '../utils/capabilityPickup';

const migrationStarted = new Set<string>();

// Every useBench() instance holds its own copy of the view-model (the hub
// and ActiveContextBar mount separate ones).  A placement made through one
// announces itself here so the others re-fetch instead of showing a stale
// bench until the next remount.
export const BENCH_CHANGED_EVENT = 'ziya:bench-changed';

export interface UseBenchArgs {
    projectId: string | null | undefined;
    conversationId?: string | null;
    /** Legacy inputs, for the one-shot migration only. */
    legacy?: { skillIds: string[]; skills: MigratableSkill[]; skillsLoadedForProject: boolean };
}

export interface BenchItemView extends BenchViewItem { atLens: LensView; }
export type { BenchItemDetail, ToolDetail, InstallResult };
export type { ReauthorizeResult, ReauthorizeVerdict } from '../apis/benchApi';
export type { FindHome, FindResults, FindColumn, FindHit, FindResponse } from '../apis/benchApi';
// Renderers never import the API module (benchWiring guard); the two find
// helpers they need are re-exported from the hook instead.
export { isFindResults, MCP_FIND_COLUMN } from '../apis/benchApi';

/** Token movement since the previous view; shown briefly, then dropped. */
export interface TokenFlash { byKey: Record<string, number>; always: number; at: number; }
export const FLASH_MS = 1500;

export function useBench({ projectId, conversationId, legacy }: UseBenchArgs) {
    const { lens, setLens } = useLens();
    const [view, setView] = useState<BenchView | null>(null);
    const [error, setError] = useState<unknown>(null);
    const [loading, setLoading] = useState(false);
    const pickups = useCapabilityPickups(conversationId);   // ✦ slice 5

    const refresh = useCallback(async () => {
        if (!projectId) { setView(null); return; }
        setLoading(true);
        try { setView(await fetchBench(projectId, conversationId)); setError(null); }
        catch (e) { setError(e); }
        finally { setLoading(false); }
    }, [projectId, conversationId]);

    useEffect(() => { refresh(); }, [refresh]);

    const instanceId = useRef(Symbol('bench'));
    const announce = useCallback(() => {
        if (typeof window === 'undefined' || !projectId) return;
        window.dispatchEvent(new CustomEvent(BENCH_CHANGED_EVENT,
            { detail: { projectId, source: instanceId.current } }));
    }, [projectId]);

    useEffect(() => {
        const onChanged = (e: Event) => {
            const d = (e as CustomEvent).detail || {};
            if (d.source === instanceId.current || d.projectId !== projectId) return;
            refresh();
        };
        window.addEventListener(BENCH_CHANGED_EVENT, onChanged);
        return () => window.removeEventListener(BENCH_CHANGED_EVENT, onChanged);
    }, [projectId, refresh]);

    // One-shot migration, once the skills for THIS project are loaded (a
    // stale list from the previous project must not be converted).
    useEffect(() => {
        if (!projectId || !legacy || !legacy.skillsLoadedForProject) return;
        if (migrationStarted.has(projectId) || isBenchLive(projectId)) return;
        migrationStarted.add(projectId);
        runBenchMigration(projectId, legacy.skillIds, legacy.skills, {
            fetchBench: (pid) => fetchBench(pid),
            place: (pid, key, state) => placeBench(pid, key, 'project', state, null, 'migration'),
            clearLegacy: clearLegacySkillIds,
        }).then(outcome => {
            if (outcome.status !== 'failed') markBenchLive(projectId);
            else migrationStarted.delete(projectId);   // allow a retry next mount
            if (outcome.status === 'migrated') { refresh(); announce(); }
        }).catch(() => migrationStarted.delete(projectId));
    }, [projectId, legacy?.skillsLoadedForProject, legacy?.skillIds, legacy?.skills, refresh, announce]);

    const place = useCallback(async (key: string, state: PlacementState | null, layer: Layer = lens) => {
        if (!projectId) return;
        setView(await placeBench(projectId, key, layer, state, conversationId));
        announce();
    }, [projectId, conversationId, lens, announce]);

    // Environment toggles are user-wide, not placements: no lens, no layer.
    // The server's warning (e.g. skill discovery off) rides on the view and
    // stays until the next environment change replaces or clears it.
    const [environmentWarning, setEnvironmentWarning] = useState<string | null>(null);
    const setEnvironment = useCallback(async (category: string, enabled: boolean) => {
        if (!projectId) return;
        const v = await setBenchEnvironment(projectId, category, enabled, conversationId);
        setView(v);
        setEnvironmentWarning(v.warning ?? null);
        announce();
    }, [projectId, conversationId, announce]);

    // Card-back detail (body, path, tools): loaded when a card turns, cached
    // for the life of this project because bodies are heavy and do not change
    // with placement.  Components never call the API; they call this.
    const detailCache = useRef(new Map<string, Promise<BenchItemDetail>>());
    useEffect(() => { detailCache.current.clear(); }, [projectId]);
    const detail = useCallback((key: string): Promise<BenchItemDetail> => {
        if (!projectId) return Promise.reject(new Error('no project'));
        let p = detailCache.current.get(key);
        if (!p) {
            p = fetchBenchDetail(projectId, key).catch(e => { detailCache.current.delete(key); throw e; });
            detailCache.current.set(key, p);
        }
        return p;
    }, [projectId]);

    // Per-tool switch: one response carries the new detail (drawer) and the
    // re-costed view (card, gauge, chip bar), so every counter moves together.
    const setTool = useCallback(async (server: string, tool: string, enabled: boolean): Promise<BenchItemDetail> => {
        if (!projectId) throw new Error('no project');
        const { detail: d, view: v } = await setBenchTool(projectId, server, tool, enabled, conversationId);
        detailCache.current.set(`mcp:${server}`, Promise.resolve(d));
        setView(v);
        announce();
        return d;
    }, [projectId, conversationId, announce]);

    // Lift a rug-pull quarantine from the drawer.  A refusal (blocking
    // injection pattern) comes back as a verdict, not a throw, so the drawer
    // can show the flagged tools and offer the override.
    const reauthorize = useCallback(async (server: string, force = false): Promise<ReauthorizeResult> => {
        if (!projectId) throw new Error('no project');
        const r = await reauthorizeBenchServer(projectId, server, force, conversationId);
        detailCache.current.set(`mcp:${server}`, Promise.resolve(r.detail));
        setView(r.view);
        announce();
        return r;
    }, [projectId, conversationId, announce]);

    // Find (slice 8b): a query to the sources, never a catalog.  Install lands
    // files at the lens's layer -- the conversation layer has no files, so it
    // installs to the project -- and places there; the returned view replaces
    // ours like any other write.
    const find = useCallback((q: string, n = 8): Promise<FindResponse> => {
        if (!projectId) return Promise.reject(new Error('no project'));
        return fetchBenchFind(projectId, q, n);
    }, [projectId]);
    const install = useCallback(async (source: string, id: string, state: PlacementState | null): Promise<InstallResult> => {
        if (!projectId) throw new Error('no project');
        const layer: 'user' | 'project' = lens === 'user' ? 'user' : 'project';
        const r = await installBenchSkill(projectId, source, id, layer, state, conversationId);
        setView(r.view);
        announce();
        return r;
    }, [projectId, conversationId, lens, announce]);

    // Transient token deltas: what changed in the last view update, per key
    // and for the on-hand total.  Read by the gauge / card / drawer to flash a
    // "−1,872t" chip; cleared after FLASH_MS.  Client-only.
    const [flash, setFlash] = useState<TokenFlash | null>(null);
    const prevTokens = useRef<{ byKey: Record<string, number>; always: number } | null>(null);
    useEffect(() => {
        if (!view) return;
        const byKey: Record<string, number> = {};
        for (const i of view.items) byKey[i.key] = i.tokens;
        const prev = prevTokens.current;
        prevTokens.current = { byKey, always: view.weight.always };
        if (!prev) return;
        const deltas: Record<string, number> = {};
        for (const k of Object.keys(byKey)) {
            const d = byKey[k] - (prev.byKey[k] ?? byKey[k]);
            if (d !== 0) deltas[k] = d;
        }
        const always = view.weight.always - prev.always;
        if (always === 0 && Object.keys(deltas).length === 0) return;
        setFlash({ byKey: deltas, always, at: Date.now() });
        const t = setTimeout(() => setFlash(null), FLASH_MS);
        return () => clearTimeout(t);
    }, [view]);

    const items: BenchItemView[] = useMemo(
        () => (view?.items ?? []).map(i => ({ ...i, atLens: viewAtLens(i, lens) })),
        [view, lens]);

    const alwaysItems = useMemo(() => items.filter(i => i.effective === 'always'), [items]);
    // ✦: on-demand items the model loaded this turn.  An item placed
    // ``always`` is already shown as active and is not repeated here.
    const pickedUpItems = useMemo(
        () => items.filter(i => i.effective === 'ondemand' && pickups.has(i.key)),
        [items, pickups]);

    return {
        ready: view !== null,
        loading,
        error,
        items,
        alwaysItems,
        pickedUpItems,
        dropped: view?.dropped ?? [],
        weight: view?.weight ?? { always: 0, ondemand: 0, environment: 0 },
        deltas: view?.deltas ?? { conversation: 0, project: 0, user: 0 },
        lenses: view?.lenses ?? (['project', 'user'] as Layer[]),
        lens, setLens,
        place,
        setEnvironment,
        environmentWarning,
        detail,
        setTool,
        reauthorize,
        flash,
        find,
        install,
        refresh,
        isPickedUp: (key: string) => pickups.has(key),
    };
}
