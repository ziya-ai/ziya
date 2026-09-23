/**
 * activeSkillIds -> bench migration (design/capabilities-hub.md, slice 4).
 *
 * The legacy lens stored ONE list with two meanings (skillActivation.ts): a
 * model_discoverable skill in the list is SUPPRESSED, any other is ACTIVE.
 * The bench represents both directly, so the converter is:
 *   discoverable in list  => skill:<name> = off     (was: suppressed)
 *   selectable  in list   => skill:<name> = always  (was: active)
 * and absence => no placement (inherit the default).
 *
 * Guards, both from the design note:
 *   * PUT only if the PROJECT layer is empty — two browsers must not clobber
 *     each other; whoever migrates first wins and the other's local list is
 *     simply retired.
 *   * delete the localStorage skill list only after EVERY PUT succeeded, so
 *     a half-failed migration leaves the legacy path intact for a retry.
 */
import { MODEL_DISCOVERABLE } from './skillActivation';
import type { BenchView } from '../apis/benchApi';
import type { PlacementState } from './activation';

export interface MigratableSkill { id: string; name: string; visibility?: string | null; }
export interface Conversion { key: string; state: PlacementState; }

/** The placement a legacy toggle means for one skill; null when turned off. */
export function placementForLegacySkill(skill: MigratableSkill, on: boolean): PlacementState | null {
    if (!on) return null;
    return skill.visibility === MODEL_DISCOVERABLE ? 'off' : 'always';
}

export function convertActiveSkillIds(skillIds: string[], skills: MigratableSkill[]): Conversion[] {
    const out: Conversion[] = [];
    const seen = new Set<string>();
    for (const id of skillIds) {
        const s = skills.find(x => x.id === id);
        if (!s || seen.has(s.name)) continue;   // stale id: nothing to carry over
        seen.add(s.name);
        const state = placementForLegacySkill(s, true);
        if (state) out.push({ key: `skill:${s.name}`, state });
    }
    return out;
}

export const projectLayerIsEmpty = (view: BenchView): boolean =>
    !view.items.some(i => i.kind === 'skill' && i.placements.project !== null);

export interface MigrationDeps {
    fetchBench: (projectId: string) => Promise<BenchView>;
    place: (projectId: string, key: string, state: PlacementState) => Promise<unknown>;
    clearLegacy: (projectId: string) => void;
}

export type MigrationOutcome =
    | { status: 'migrated'; placed: Conversion[] }
    | { status: 'skipped'; reason: 'project_layer_not_empty' | 'nothing_to_migrate' }
    | { status: 'failed'; placed: Conversion[]; error: unknown };

export async function runBenchMigration(
    projectId: string, skillIds: string[], skills: MigratableSkill[], deps: MigrationDeps,
): Promise<MigrationOutcome> {
    const conversions = convertActiveSkillIds(skillIds, skills);
    const view = await deps.fetchBench(projectId);
    if (!projectLayerIsEmpty(view)) {
        // Another browser already adopted the bench; the local list is retired
        // WITHOUT being written, so it cannot clobber what is there.
        deps.clearLegacy(projectId);
        return { status: 'skipped', reason: 'project_layer_not_empty' };
    }
    if (conversions.length === 0) return { status: 'skipped', reason: 'nothing_to_migrate' };
    const placed: Conversion[] = [];
    for (const c of conversions) {
        try {
            await deps.place(projectId, c.key, c.state);
            placed.push(c);
        } catch (error) {
            return { status: 'failed', placed, error };   // legacy key kept for retry
        }
    }
    deps.clearLegacy(projectId);
    return { status: 'migrated', placed };
}

// -- liveness: has the bench become authoritative for this project? --------
// Set once the migration has run (or been skipped because the server already
// held placements).  ProjectContext's legacy toggles write through to the
// bench only when this is true; before it, a toggle only touches localStorage
// and the server honours the client prompt (bench_prompt.merge_bench_prompt).
const live = new Set<string>();
export const isBenchLive = (projectId: string) => live.has(projectId);
export const markBenchLive = (projectId: string) => { live.add(projectId); };
/** Test hook. */
export const _resetBenchLive = () => live.clear();

/** Remove only the skill list from the legacy lens key; contexts stay. */
export function clearLegacySkillIds(projectId: string): void {
    const k = `ZIYA_LENS_${projectId}`;
    try {
        const raw = localStorage.getItem(k);
        if (!raw) return;
        const parsed = JSON.parse(raw);
        localStorage.setItem(k, JSON.stringify({ ...parsed, skillIds: [] }));
    } catch { /* corrupt or quota — leave as is */ }
}
