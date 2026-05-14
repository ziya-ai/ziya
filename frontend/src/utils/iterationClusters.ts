/**
 * Group failed iteration summaries by signature for the cluster view.
 *
 * Each iteration summary carries an optional `signature` (a 12-hex
 * hash derived server-side from error_type + error_location — see
 * app/agents/block_executor._derive_signature).  Identical signatures
 * mean "same failure pattern" and can be collapsed in the UI.
 *
 * This is the "10,000 runs, 4 error patterns" primitive from
 * design/task-cards.md §Runtime semantics.
 */

import type { IterationSummary, TaskRunBlockState } from '../types/task_run';

export interface IterationRef {
  blockId: string;
  index: number;
}

export interface FailureCluster {
  /** 12-hex signature shared by all iterations in this cluster. */
  signature: string;
  /** Count of failing iterations with this signature. */
  count: number;
  /** Iterations in this cluster, ordered by (blockId, index). */
  iterations: IterationRef[];
}

export interface ClusterAnalysis {
  totalFailures: number;
  /** Failures whose signature is null — can't be clustered. */
  unsignedFailures: IterationRef[];
  clusters: FailureCluster[];
  /** True when the UI should render clusters instead of a flat list. */
  shouldCluster: boolean;
}

/** Threshold below which clustering isn't worth the UI complexity. */
const MIN_FAILURES_FOR_CLUSTERING = 3;

/**
 * Walk a run's block_states and bucket failing iterations by
 * signature.  Pure; does no I/O.
 */
export function analyzeFailures(
  blockStates: Record<string, TaskRunBlockState>,
): ClusterAnalysis {
  const bySig = new Map<string, IterationRef[]>();
  const unsigned: IterationRef[] = [];

  for (const [blockId, state] of Object.entries(blockStates)) {
    for (const s of state.iteration_summaries) {
      if (s.status !== 'failed') continue;
      const ref: IterationRef = { blockId, index: s.index };
      if (s.signature) {
        const arr = bySig.get(s.signature);
        if (arr) arr.push(ref); else bySig.set(s.signature, [ref]);
      } else {
        unsigned.push(ref);
      }
    }
  }

  const clusters: FailureCluster[] = [];
  for (const [signature, iterations] of bySig) {
    iterations.sort((a, b) =>
      a.blockId < b.blockId ? -1 :
      a.blockId > b.blockId ? 1 :
      a.index - b.index,
    );
    clusters.push({ signature, count: iterations.length, iterations });
  }
  // Most-common first so the biggest problem leads the UI.
  clusters.sort((a, b) => b.count - a.count);

  const totalFailures = unsigned.length +
    clusters.reduce((n, c) => n + c.count, 0);

  // Clustering is worth showing only when there's meaningful
  // collapse — otherwise the flat list is friendlier.  The rule:
  //   - at least MIN_FAILURES_FOR_CLUSTERING total failures, AND
  //   - some cluster has at least 2 iterations (i.e. signatures
  //     actually repeat).
  const anyRepeat = clusters.some(c => c.count >= 2);
  const shouldCluster =
    totalFailures >= MIN_FAILURES_FOR_CLUSTERING && anyRepeat;

  return { totalFailures, unsignedFailures: unsigned, clusters, shouldCluster };
}
