/**
 * Rail chip progress (taskProgress.ts).
 *
 * The rail renders from bindings, which carry no progress.  The tile owns
 * the live run, so it publishes a compact progress reading per binding and
 * the rail reads it.  Covered here: the derivation, the chip text, the
 * publish/latest store (a rail that mounts after the tile must still see
 * the last reading), and the wiring at both ends.
 */
import * as fs from 'fs';
import * as path from 'path';
import {
    TASK_PROGRESS_EVENT, deriveChipProgress, formatChipProgress,
    latestProgress, publishProgress, resetProgressForTests,
} from '../taskProgress';

const it_ = (index: number, replayed = false) =>
    ({ index, status: 'passed', has_artifact: true, duration_ms: 1, replayed } as any);
const run = (states: Record<string, any>) => ({ id: 'r', block_states: states } as any);

describe('deriveChipProgress', () => {
    it('reads the planned loop as done/planned, counting replayed iterations', () => {
        const p = deriveChipProgress(run({
            loop: { iteration_summaries: [it_(0, true), it_(1), it_(2)], planned_iterations: 20 },
            other: { iteration_summaries: [] },
        }), 'Lane C');
        expect(p).toEqual({ done: 3, planned: 20, note: 'Lane C' });
    });

    it('without a roster size, counts iterations this attempt executed', () => {
        const p = deriveChipProgress(run({
            a: { iteration_summaries: [it_(0, true), it_(1)] },
            b: { iteration_summaries: [it_(0)] },
        }), null);
        expect(p).toEqual({ done: 2, planned: null, note: null });
    });

    it('a run with neither iterations nor a note has no progress', () => {
        expect(deriveChipProgress(run({ a: { iteration_summaries: [] } }), null)).toBeNull();
        expect(deriveChipProgress(null, 'x')).toBeNull();
    });
});

describe('formatChipProgress', () => {
    it('formats the three cases', () => {
        expect(formatChipProgress({ done: 14, planned: 20 })).toBe('14/20');
        expect(formatChipProgress({ done: 25, planned: 20 })).toBe('20/20');
        expect(formatChipProgress({ done: 7, planned: null })).toBe('7 iter');
        expect(formatChipProgress({ done: 0, planned: null, note: 'x' })).toBeNull();
        expect(formatChipProgress(null)).toBeNull();
    });
});

describe('publishProgress', () => {
    beforeEach(() => resetProgressForTests());

    it('stores the latest reading and dispatches only on change', () => {
        const seen: any[] = [];
        const h = (e: Event) => seen.push((e as CustomEvent).detail);
        window.addEventListener(TASK_PROGRESS_EVENT, h);
        expect(publishProgress('b1', { done: 1, planned: 4 })).toBe(true);
        expect(publishProgress('b1', { done: 1, planned: 4 })).toBe(false);
        expect(latestProgress('b1')).toEqual({ done: 1, planned: 4 });
        expect(publishProgress('b1', null)).toBe(true);
        expect(latestProgress('b1')).toBeUndefined();
        window.removeEventListener(TASK_PROGRESS_EVENT, h);
        expect(seen).toEqual([
            { bindingId: 'b1', progress: { done: 1, planned: 4 } },
            { bindingId: 'b1', progress: null },
        ]);
    });
});

describe('wiring', () => {
    const read = (f: string) => fs.readFileSync(path.join(__dirname, '..', f), 'utf8');

    it('the tile publishes for its own run only, clearing on terminal', () => {
        const tile = read('TaskCardInlineTile.tsx');
        expect(tile).toMatch(/import \{ deriveChipProgress, publishProgress \} from '\.\/taskProgress'/);
        expect(tile).toMatch(/run\.id !== binding\.run_id/);
        expect(tile).toMatch(/publishProgress\(binding\.id, isTerminal \? null : deriveChipProgress\(run, progressNote\)\)/);
    });

    it('the rail renders the chip progress from the event or the store', () => {
        const rail = read('TaskRail.tsx');
        expect(rail).toMatch(/addEventListener\(TASK_PROGRESS_EVENT/);
        expect(rail).toMatch(/progress\[binding\.id\] \?\? latestProgress\(binding\.id\)/);
        expect(rail).toMatch(/className="tc-rail-chip__progress"/);
    });
});
