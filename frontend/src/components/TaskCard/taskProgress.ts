/**
 * taskProgress — a compact progress reading per task binding, published by
 * the inline tile and read by the task rail.
 *
 * The rail renders from TaskBinding records, which carry status but no
 * progress.  The tile already holds the live run (REST + WS), so rather
 * than a second stream subscription per run, the tile publishes what the
 * chip needs.  A module-level store backs the event so a rail that mounts
 * after the tile still gets the last reading.
 */

import type { TaskRun } from '../../types/task_run';

export const TASK_PROGRESS_EVENT = 'task-run-progress';

export interface ChipProgress {
    /** Iterations completed (against ``planned`` when it is known). */
    done: number;
    /** Roster size of the run's planned loop, when one declares it. */
    planned?: number | null;
    /** Latest progress line; shown in the chip tooltip. */
    note?: string | null;
}

export interface TaskProgressDetail {
    bindingId: string;
    progress: ChipProgress | null;
}

/**
 * Progress for a chip.  A loop that declares ``planned_iterations`` (a
 * for-each roster) reads as done/planned over ALL its recorded iterations,
 * replayed ones included, matching the run map's dot count.  Otherwise the
 * count is the iterations this attempt executed.
 */
export function deriveChipProgress(
    run: TaskRun | null | undefined, note: string | null | undefined,
): ChipProgress | null {
    if (!run) return null;
    let best: { done: number; planned: number } | null = null;
    let executed = 0;
    for (const s of Object.values(run.block_states ?? {}) as any[]) {
        const sums: any[] = s?.iteration_summaries ?? [];
        for (const it of sums) if (!it?.replayed) executed++;
        const p = s?.planned_iterations;
        if (typeof p === 'number' && p > 0 && (!best || p > best.planned)) {
            best = { done: sums.length, planned: p };
        }
    }
    const n = note ?? null;
    if (best) return { done: best.done, planned: best.planned, note: n };
    if (executed > 0) return { done: executed, planned: null, note: n };
    return n ? { done: 0, planned: null, note: n } : null;
}

/** Chip text: "14/20", "7 iter", or null when there is nothing to count. */
export function formatChipProgress(p: ChipProgress | null | undefined): string | null {
    if (!p) return null;
    if (p.planned) return `${Math.min(p.done, p.planned)}/${p.planned}`;
    if (p.done > 0) return `${p.done} iter`;
    return null;
}

const latest = new Map<string, ChipProgress>();

const same = (a?: ChipProgress, b?: ChipProgress | null) =>
    (!a && !b) || (!!a && !!b && a.done === b.done
        && (a.planned ?? null) === (b.planned ?? null) && (a.note ?? null) === (b.note ?? null));

/** Record and announce a binding's progress; null clears it.  Returns
 *  whether anything changed (an unchanged reading dispatches nothing). */
export function publishProgress(bindingId: string, progress: ChipProgress | null): boolean {
    if (!bindingId || same(latest.get(bindingId), progress)) return false;
    if (progress) latest.set(bindingId, progress); else latest.delete(bindingId);
    if (typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent<TaskProgressDetail>(
            TASK_PROGRESS_EVENT, { detail: { bindingId, progress } }));
    }
    return true;
}

export function latestProgress(bindingId: string): ChipProgress | undefined {
    return latest.get(bindingId);
}

/** Test hook. */
export function resetProgressForTests(): void {
    latest.clear();
}
