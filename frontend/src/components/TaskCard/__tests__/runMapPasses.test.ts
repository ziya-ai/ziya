/**
 * A nested loop's iteration record is grouped by the enclosing pass and
 * rendered as one strip per pass; long histories collapse to the first
 * pass, an ellipsis row, the previous pass and the current one.
 *
 * Regression for GFX Stage 2 run 5d0b198c: a 20-engine Repeat inside an
 * Until showed "30/20" and two lit dots per index, because both passes
 * were flattened into one strip keyed by index alone.
 */

import {
  buildDotPasses, collapsePasses, PASS_WINDOW, buildDots,
} from '../runMapModel';
import type { IterationSummary } from '../../../types/task_run';

const s = (index: number, pass_key: string | null, status: 'passed' | 'failed' = 'passed'):
  IterationSummary => ({
    index, status, duration_ms: 1, tokens: 1, has_artifact: true,
    ...(pass_key == null ? {} : { pass_key }),
  });

describe('buildDotPasses', () => {
  it('a top-level loop is one pass with a null key and the buildDots model', () => {
    const sums = [s(0, null), s(1, null, 'failed')];
    const passes = buildDotPasses(sums, true, [2]);
    expect(passes).toHaveLength(1);
    expect(passes[0].passKey).toBeNull();
    expect(passes[0].dots).toEqual(buildDots(sums, true, [2]));
  });

  it('groups a nested loop by pass_key in first-appearance order', () => {
    const sums = [s(0, '0'), s(1, '0', 'failed'), s(0, '1'), s(1, '1')];
    const passes = buildDotPasses(sums, false);
    expect(passes.map(p => p.passKey)).toEqual(['0', '1']);
    expect(passes[0].dots.dots.map(d => [d.index, d.status]))
      .toEqual([[0, 'passed'], [1, 'failed']]);
    expect(passes[1].dots.dots.map(d => d.index)).toEqual([0, 1]);
    // Each pass counts its own iterations -- "2/20", never "30/20".
    expect(passes.every(p => p.dots.total === 2)).toBe(true);
  });

  it('attaches the running indicator to the current (last) pass only', () => {
    const passes = buildDotPasses([s(0, '0'), s(0, '1')], true, [1]);
    expect(passes[0].dots.running).toBe(false);
    expect(passes[0].dots.runningIndices).toEqual([]);
    expect(passes[1].dots.running).toBe(true);
    expect(passes[1].dots.runningIndices).toEqual([1]);
  });

  it('with no summaries yields one empty pass carrying the live state', () => {
    const passes = buildDotPasses([], true, [0]);
    expect(passes).toHaveLength(1);
    expect(passes[0].dots.total).toBe(0);
    expect(passes[0].dots.runningIndices).toEqual([0]);
  });
});

describe('collapsePasses', () => {
  const mk = (n: number) => buildDotPasses(
    Array.from({ length: n }, (_, k) => s(0, String(k))), false);

  it('shows up to PASS_WINDOW passes in full', () => {
    expect(PASS_WINDOW).toBe(4);
    const four = mk(4);
    expect(collapsePasses(four)).toBe(four);
  });

  it('collapses the middle to first, ellipsis, previous, current', () => {
    const rows = collapsePasses(mk(7));
    expect(rows).toHaveLength(4);
    expect((rows[0] as any).passKey).toBe('0');
    expect(rows[1]).toEqual({ hidden: 4 });
    expect((rows[2] as any).passKey).toBe('5');
    expect((rows[3] as any).passKey).toBe('6');
  });

  it('five passes hide exactly two', () => {
    const rows = collapsePasses(mk(5));
    expect(rows[1]).toEqual({ hidden: 2 });
    expect(rows.map(r => ('passKey' in r ? r.passKey : '…')))
      .toEqual(['0', '…', '3', '4']);
  });
});
