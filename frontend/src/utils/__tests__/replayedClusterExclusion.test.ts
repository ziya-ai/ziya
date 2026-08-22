/**
 * Failure clustering must ignore a resumed run's replayed prefix.
 *
 * A mid-loop resume seeds the iterations it inherited onto its own record
 * (``IterationSummary.replayed``) so the run map can show that the banked
 * work was preserved.  Those records carry the SOURCE attempt's status
 * and signature, so without an exclusion here a failure the user is
 * explicitly resuming past is re-counted as a fresh occurrence —
 * inflating the very pattern count they are using to decide what to fix,
 * and inflating it more with every chained resume.
 */

import { analyzeFailures } from '../iterationClusters';
import type { TaskRunBlockState } from '../../types/task_run';

function states(
  summaries: Array<Record<string, unknown>>,
): Record<string, TaskRunBlockState> {
  return {
    loop: {
      block_id: 'loop', block_type: 'repeat', status: 'done',
      iteration_summaries: summaries,
    } as unknown as TaskRunBlockState,
  };
}

const fail = (index: number, signature: string, replayed = false) => ({
  index, status: 'failed', signature,
  duration_ms: 1, tokens: 0, has_artifact: true, replayed,
});

describe('analyzeFailures excludes replayed iterations', () => {
  it('counts only the failure this attempt produced', () => {
    const a = analyzeFailures(states([
      fail(0, 'sig-a', true),
      fail(1, 'sig-a', true),
      fail(2, 'sig-a'),
    ]));
    expect(a.totalFailures).toBe(1);
  });

  it('does not let replayed failures reach the cluster threshold', () => {
    // Three replayed failures sharing a signature would otherwise satisfy
    // both clustering conditions on their own, so a resumed run would
    // open the cluster UI over work it did not do.
    const a = analyzeFailures(states([
      fail(0, 'sig-a', true),
      fail(1, 'sig-a', true),
      fail(2, 'sig-a', true),
      fail(3, 'sig-b'),
    ]));
    expect(a.shouldCluster).toBe(false);
    expect(a.totalFailures).toBe(1);
  });

  it('still clusters genuinely repeated executed failures', () => {
    const a = analyzeFailures(states([
      fail(0, 'sig-a', true),
      fail(1, 'sig-b'), fail(2, 'sig-b'), fail(3, 'sig-b'),
    ]));
    expect(a.totalFailures).toBe(3);
    expect(a.shouldCluster).toBe(true);
    expect(a.clusters[0].signature).toBe('sig-b');
    expect(a.clusters[0].count).toBe(3);
  });

  it('excludes an unsigned replayed failure too', () => {
    // The unsigned path is a separate branch and would otherwise leak.
    const a = analyzeFailures(states([
      { index: 0, status: 'failed', duration_ms: 1, tokens: 0,
        has_artifact: true, replayed: true },
    ]));
    expect(a.unsignedFailures).toEqual([]);
    expect(a.totalFailures).toBe(0);
  });

  it('leaves an ordinary run\'s analysis unchanged', () => {
    // Regression guard: no run written before the field exists has it.
    const a = analyzeFailures(states([
      { index: 0, status: 'failed', signature: 'x',
        duration_ms: 1, tokens: 0, has_artifact: true },
      { index: 1, status: 'passed',
        duration_ms: 1, tokens: 0, has_artifact: true },
    ]));
    expect(a.totalFailures).toBe(1);
  });
});
