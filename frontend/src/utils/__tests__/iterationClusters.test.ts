/**
 * Tests for analyzeFailures — the signature-clustering primitive.
 *
 * The shape we're verifying:
 *   - failing iterations with the same signature are bucketed together
 *   - clusters are sorted by count descending
 *   - passed/cancelled iterations are ignored
 *   - iterations without a signature fall into unsignedFailures
 *   - shouldCluster respects both the minimum-count AND any-repeat rules
 */

import type { IterationSummary, TaskRunBlockState } from '../../types/task_run';
import { analyzeFailures } from '../iterationClusters';

function summary(
  index: number,
  status: IterationSummary['status'],
  signature: string | null = null,
): IterationSummary {
  return {
    index, status, signature,
    duration_ms: 0, tokens: 0, has_artifact: true,
  };
}

function blockState(
  blockId: string, summaries: IterationSummary[],
): TaskRunBlockState {
  return {
    block_id: blockId,
    block_type: 'repeat',
    status: 'done',
    iteration_summaries: summaries,
  };
}

describe('analyzeFailures', () => {
  it('returns empty result for runs with no failures', () => {
    const r = analyzeFailures({
      b1: blockState('b1', [summary(0, 'passed'), summary(1, 'passed')]),
    });
    expect(r.totalFailures).toBe(0);
    expect(r.clusters).toEqual([]);
    expect(r.unsignedFailures).toEqual([]);
    expect(r.shouldCluster).toBe(false);
  });

  it('ignores non-failed statuses', () => {
    const r = analyzeFailures({
      b1: blockState('b1', [
        summary(0, 'passed', 'abc'),
        summary(1, 'cancelled', 'abc'),
      ]),
    });
    expect(r.totalFailures).toBe(0);
  });

  it('groups failures by signature', () => {
    const r = analyzeFailures({
      b1: blockState('b1', [
        summary(0, 'failed', 'sigA'),
        summary(1, 'passed'),
        summary(2, 'failed', 'sigA'),
        summary(3, 'failed', 'sigB'),
        summary(4, 'failed', 'sigA'),
      ]),
    });
    expect(r.totalFailures).toBe(4);
    expect(r.clusters).toHaveLength(2);
    // Sorted by count descending.
    expect(r.clusters[0].signature).toBe('sigA');
    expect(r.clusters[0].count).toBe(3);
    expect(r.clusters[1].signature).toBe('sigB');
    expect(r.clusters[1].count).toBe(1);
  });

  it('sorts iterations within a cluster by (blockId, index)', () => {
    const r = analyzeFailures({
      b2: blockState('b2', [summary(5, 'failed', 'sigA')]),
      b1: blockState('b1', [
        summary(10, 'failed', 'sigA'),
        summary(2, 'failed', 'sigA'),
      ]),
    });
    expect(r.clusters[0].iterations).toEqual([
      { blockId: 'b1', index: 2 },
      { blockId: 'b1', index: 10 },
      { blockId: 'b2', index: 5 },
    ]);
  });

  it('puts null-signature failures in unsignedFailures', () => {
    const r = analyzeFailures({
      b1: blockState('b1', [
        summary(0, 'failed', null),
        summary(1, 'failed', 'sigA'),
      ]),
    });
    expect(r.unsignedFailures).toEqual([{ blockId: 'b1', index: 0 }]);
    expect(r.clusters).toHaveLength(1);
    expect(r.totalFailures).toBe(2);
  });

  it('clusters across multiple blocks', () => {
    const r = analyzeFailures({
      b1: blockState('b1', [summary(0, 'failed', 'sigA')]),
      b2: blockState('b2', [summary(0, 'failed', 'sigA')]),
    });
    expect(r.clusters).toHaveLength(1);
    expect(r.clusters[0].count).toBe(2);
  });

  describe('shouldCluster', () => {
    it('false when below minimum failure count', () => {
      // 2 failures, both with same sig — not enough to justify the UI.
      const r = analyzeFailures({
        b1: blockState('b1', [
          summary(0, 'failed', 'sigA'),
          summary(1, 'failed', 'sigA'),
        ]),
      });
      expect(r.totalFailures).toBe(2);
      expect(r.shouldCluster).toBe(false);
    });

    it('false when all failures have distinct signatures', () => {
      // 3 failures, 3 different signatures — flat list is better.
      const r = analyzeFailures({
        b1: blockState('b1', [
          summary(0, 'failed', 'sigA'),
          summary(1, 'failed', 'sigB'),
          summary(2, 'failed', 'sigC'),
        ]),
      });
      expect(r.shouldCluster).toBe(false);
    });

    it('true when failures cluster meaningfully', () => {
      const r = analyzeFailures({
        b1: blockState('b1', [
          summary(0, 'failed', 'sigA'),
          summary(1, 'failed', 'sigA'),
          summary(2, 'failed', 'sigB'),
        ]),
      });
      expect(r.shouldCluster).toBe(true);
    });
  });
});
