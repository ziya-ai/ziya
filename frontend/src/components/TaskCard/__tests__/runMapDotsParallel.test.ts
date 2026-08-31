/**
 * ``buildDots`` running-indicator model for PARALLEL loops.
 *
 * ``DotModel.running`` is one boolean for the whole loop row, so the run
 * map could only ever render a single pulsing dot — a Repeat with 8
 * iterations in flight was visually indistinguishable from a serial one.
 * ``runningIndices`` carries the ordinals actually in flight so the strip
 * can show one dot per concurrent iteration.
 *
 * ``running`` is deliberately unchanged (it still gates ``showDots`` in
 * TaskRunMap), so these tests also pin that the addition is a refinement
 * rather than a replacement.
 */

import { buildDots } from '../runMapModel';
import type { IterationSummary } from '../../../types/task_run';

const summary = (
  index: number,
  status: 'passed' | 'failed' | 'cancelled' = 'passed',
): IterationSummary => ({
  index, status, has_artifact: false,
} as IterationSummary);

describe('buildDots running indicators', () => {
  it('reports every in-flight ordinal, ascending', () => {
    const d = buildDots([summary(0)], true, [3, 1, 2]);
    expect(d.runningIndices).toEqual([1, 2, 3]);
    // One dot per concurrent iteration is the point.
    expect(d.runningIndices).toHaveLength(3);
  });

  it('keeps the running boolean so showDots still gates the strip', () => {
    const d = buildDots([], true, [0, 1]);
    expect(d.running).toBe(true);
    expect(d.total).toBe(0);
  });

  it('claims no in-flight iterations when the block is not running', () => {
    // Guards a stale live bucket painting a pulsing dot under a finished
    // loop — the same class of bug resolveBlockStatus already defends
    // against for row status.
    const d = buildDots([summary(0), summary(1)], false, [1]);
    expect(d.runningIndices).toEqual([]);
    expect(d.running).toBe(false);
  });

  it('falls back to no ordinals when none are supplied', () => {
    // A reloaded run has iteration_summaries but no live buckets; the
    // legacy single dot must still be reachable via ``running``.
    const d = buildDots([summary(0)], true);
    expect(d.runningIndices).toEqual([]);
    expect(d.running).toBe(true);
  });

  it('leaves the completed-iteration dots untouched', () => {
    // Positive control: the existing strip semantics (order, count,
    // overflow) must not shift.
    const d = buildDots([summary(0), summary(1, 'failed')], true, [2]);
    expect(d.dots.map(x => x.index)).toEqual([0, 1]);
    expect(d.dots.map(x => x.status)).toEqual(['passed', 'failed']);
    expect(d.total).toBe(2);
    expect(d.overflow).toBe(0);
  });

  it('does not mutate the caller\'s array while sorting', () => {
    const live = [2, 0, 1];
    buildDots([], true, live);
    expect(live).toEqual([2, 0, 1]);
  });
});
