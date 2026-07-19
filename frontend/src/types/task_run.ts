/**
 * Task Run types — mirrors app/models/task_run.py.
 *
 * A TaskRun is one execution of a TaskCard's block tree.  Its
 * iteration_summaries carry lightweight per-iteration records; full
 * artifacts are loaded on demand via the /iterations/{block_id}/{index}
 * endpoint (see design/task-cards.md §Queryable runs).
 */

import type { Artifact, Block } from './task_card';

export type RunStatus = 'queued' | 'running' | 'paused' | 'done' | 'failed' | 'cancelled';
/**
 * Per-block lifecycle status — RunStatus plus 'skipped' (a sibling
 * that never ran because of on_failure="stop").  Drives the run map.
 */
export type BlockStatus = RunStatus | 'skipped';
export type IterationStatus = 'passed' | 'failed' | 'cancelled';

export interface IterationSummary {
  index: number;
  status: IterationStatus;
  signature?: string | null;
  duration_ms: number;
  tokens: number;
  has_artifact: boolean;
}

export interface TaskRunBlockState {
  block_id: string;
  block_type: string;
  status: BlockStatus;
  started_at?: number | null;
  completed_at?: number | null;
  artifact?: Artifact | null;
  error?: string | null;
  iteration_summaries: IterationSummary[];
}

export interface TaskRun {
  id: string;
  card_id: string;
  source_conversation_id?: string | null;
  status: RunStatus;
  started_at?: number | null;
  completed_at?: number | null;
  error?: string | null;
  cancel_requested: boolean;
  /** Soft-pause flag; executor holds at the next boundary when set. */
  pause_requested: boolean;
  artifact?: Artifact | null;
  block_states: Record<string, TaskRunBlockState>;
  total_tokens: number;
  total_tool_calls: number;
  /** Heartbeat: wall-clock seconds of most recent executor activity. */
  last_activity_at?: number | null;
  /** Short server-derived line describing the latest activity. */
  progress_note?: string | null;
  /**
   * Snapshot of the card definition (name/description/root) captured at
   * launch, so later edits to the card don't retroactively rewrite what
   * this run is shown to have executed.  Absent on runs created before
   * snapshotting was added — fall back to the live card in that case.
   */
  card_snapshot?: { name: string; description: string; root: Block } | null;
  created_at: number;
  updated_at: number;
}

export interface IterationsQuery {
  block_id?: string;
  status?: IterationStatus;
  signature?: string;
  limit?: number;
  offset?: number;
  include_artifact?: boolean;
}

export interface IterationsResponse {
  total: number;
  limit: number;
  offset: number;
  items: Array<{
    block_id: string;
    summary: IterationSummary;
    artifact?: Artifact | null;
  }>;
}
