/**
 * useBench() — the client core (design/capabilities-hub.md, slice 4).
 * Owns the fetch, the one-shot legacy migration per project, and the
 * derived per-lens view. Components fetch nothing and derive nothing.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { fetchBench, placeBench, BenchView, BenchViewItem } from '../apis/benchApi';
import type { Layer, PlacementState } from '../utils/activation';
import { useLens, viewAtLens, LensView } from '../context/LensContext';
import {
    runBenchMigration, clearLegacySkillIds, markBenchLive, isBenchLive, MigratableSkill,
} from '../utils/benchMigration';

const migrationStarted = new Set<string>();

export interface UseBenchArgs {
    projectId: string | null | undefined;
    conversationId?: string | null;
    /** Legacy inputs, for the one-shot migration only. */
    legacy?: { skillIds: string[]; skills: MigratableSkill[]; skillsLoadedForProject: boolean };
}

export interface BenchItemView extends BenchViewItem { atLens: LensView; }

export function useBench({ projectId, conversationId, legacy }: UseBenchArgs) {
    const { lens, setLens } = useLens();
    const [view, setView] = useState<BenchView | null>(null);
    const [error, setError] = useState<unknown>(null);
    const [loading, setLoading] = useState(false);
    const pickedUp = useRef<Set<string>>(new Set());   // ✦ slice 5 fills this

    const refresh = useCallback(async () => {
        if (!projectId) { setView(null); return; }
        setLoading(true);
        try { setView(await fetchBench(projectId, conversationId)); setError(null); }
        catch (e) { setError(e); }
        finally { setLoading(false); }
    }, [projectId, conversationId]);

    useEffect(() => { refresh(); }, [refresh]);

    // One-shot migration, once the skills for THIS project are loaded (a
    // stale list from the previous project must not be converted).
    useEffect(() => {
        if (!projectId || !legacy || !legacy.skillsLoadedForProject) return;
        if (migrationStarted.has(projectId) || isBenchLive(projectId)) return;
        migrationStarted.add(projectId);
        runBenchMigration(projectId, legacy.skillIds, legacy.skills, {
            fetchBench: (pid) => fetchBench(pid),
            place: (pid, key, state) => placeBench(pid, key, 'project', state),
            clearLegacy: clearLegacySkillIds,
        }).then(outcome => {
            if (outcome.status !== 'failed') markBenchLive(projectId);
            else migrationStarted.delete(projectId);   // allow a retry next mount
            if (outcome.status === 'migrated') refresh();
        }).catch(() => migrationStarted.delete(projectId));
    }, [projectId, legacy?.skillsLoadedForProject, legacy?.skillIds, legacy?.skills, refresh]);

    const place = useCallback(async (key: string, state: PlacementState | null, layer: Layer = lens) => {
        if (!projectId) return;
        setView(await placeBench(projectId, key, layer, state, conversationId));
    }, [projectId, conversationId, lens]);

    const items: BenchItemView[] = useMemo(
        () => (view?.items ?? []).map(i => ({ ...i, atLens: viewAtLens(i, lens) })),
        [view, lens]);

    const alwaysItems = useMemo(() => items.filter(i => i.effective === 'always'), [items]);

    return {
        ready: view !== null,
        loading,
        error,
        items,
        alwaysItems,
        dropped: view?.dropped ?? [],
        weight: view?.weight ?? { always: 0, ondemand: 0, environment: 0 },
        deltas: view?.deltas ?? { conversation: 0, project: 0, user: 0 },
        lenses: view?.lenses ?? (['project', 'user'] as Layer[]),
        lens, setLens,
        place,
        refresh,
        isPickedUp: (key: string) => pickedUp.current.has(key),
    };
}
